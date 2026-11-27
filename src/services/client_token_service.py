"""The session token the command line's clients of `i2k serve` send: `i2k login` keeps it
in `ui/client-token`, and `I2K_TOKEN` in the environment wins over it. With no token, a client sends none, which a
server with no account never asks for."""

from __future__ import annotations

import os
from pathlib import Path

from services import job_queue_service

__all__ = ["TOKEN_ENV", "TOKEN_FILE", "read_client_token", "write_client_token", "build_auth_headers"]

TOKEN_ENV = "I2K_TOKEN"
TOKEN_FILE = "client-token"


def _build_token_path() -> Path:
    return job_queue_service.QUEUE_DIR / TOKEN_FILE


def read_client_token() -> str:
    """The token to send: `I2K_TOKEN`, else the one `i2k login` kept, else none."""

    path = _build_token_path()

    return os.environ.get(TOKEN_ENV) or (path.read_text(encoding="utf-8").strip() if path.is_file() else "")


def write_client_token(token: str) -> Path:
    """The token kept for this machine's user alone; an empty one removes the file. The file's path is returned."""

    path = _build_token_path()
    if not token:
        path.unlink(missing_ok=True)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)

    return path


def build_auth_headers() -> dict[str, str]:
    """The bearer header for the token, or none without one."""

    token = read_client_token()

    return {"Authorization": f"Bearer {token}"} if token else {}
