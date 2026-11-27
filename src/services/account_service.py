"""The accounts of `i2k serve` and their sessions.

An account is a name, one role (`account_role_vocabulary`) and a scrypt hash of its password with its own salt, kept in
`ui/accounts.json` beside the queue. Signing in makes a session: a random token, its account and when it ends, kept in
`ui/sessions.json`, so a restarted server keeps everyone signed in. With no account at all the server asks nobody to
sign in, as before accounts existed.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets
import threading
from pathlib import Path
from typing import Any

from models import account_role_vocabulary
from models.ui_errors import AccountError
from services import job_queue_service
from utils import json_store_utils

__all__ = ["ACCOUNTS_FILE", "SESSIONS_FILE", "SESSION_DAYS", "has_accounts", "list_accounts", "add_account",
           "remove_account", "set_role", "set_password", "sign_in", "read_session", "sign_out"]

ACCOUNTS_FILE = "accounts.json"
SESSIONS_FILE = "sessions.json"
# How long a session lasts after signing in
SESSION_DAYS = 14
# scrypt's cost: 2**14 rounds of 8-byte blocks, about 16 MB and 50 ms a check, Python's own recommended floor
_SCRYPT_COST = {"n": 2 ** 14, "r": 8, "p": 1}
_lock = threading.Lock()


def _build_path(name: str) -> Path:
    """A file of this service, in the queue's folder `ui/`."""

    return job_queue_service.QUEUE_DIR / name


def _load(name: str) -> dict[str, Any]:
    path = _build_path(name)
    record = json_store_utils.read_json(path) if path.is_file() else {}

    return record if isinstance(record, dict) else {}


def _save(name: str,
          record: dict[str, Any]) -> None:
    path = _build_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store_utils.write_json(path, record)
    # The hashes and the tokens are this machine's user's alone
    path.chmod(0o600)


def _build_password_hash(password: str,
                   salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT_COST).hex()


def _check_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise AccountError("an account needs a name")

    return cleaned


def _check_role(role: str) -> str:
    if role not in account_role_vocabulary.ROLES:
        raise AccountError(f"no role {role!r}: the roles are " + ", ".join(account_role_vocabulary.ROLES))

    return role


def _check_password(password: str) -> str:
    if not password:
        raise AccountError("an account needs a password")

    return password


def has_accounts() -> bool:
    """Whether any account exists, which is whether the server asks anyone to sign in."""

    return bool(_load(ACCOUNTS_FILE))


def list_accounts() -> list[dict[str, str]]:
    """Every account as `{"name", "role", "created"}`, by name; never its hash."""

    return [{"name": name, "role": account["role"], "created": account.get("created", "")}
            for name, account in sorted(_load(ACCOUNTS_FILE).items())]


def add_account(name: str,
                password: str,
                role: str) -> None:
    """A new account; refused by name when one of that name exists, the role is unknown or a field is empty."""

    name, role, password = _check_name(name), _check_role(role), _check_password(password)
    with _lock:
        accounts = _load(ACCOUNTS_FILE)
        if name in accounts:
            raise AccountError(f"an account named {name!r} exists")
        salt = secrets.token_bytes(16)
        accounts[name] = {"role": role, "salt": salt.hex(), "hash": _build_password_hash(password, salt),
                          "created": json_store_utils.format_utc_now()}
        _save(ACCOUNTS_FILE, accounts)


def _update_account(name: str,
                    fields: dict[str, str]) -> None:
    with _lock:
        accounts = _load(ACCOUNTS_FILE)
        if name not in accounts:
            raise AccountError(f"no account named {name!r}")
        accounts[name].update(fields)
        _save(ACCOUNTS_FILE, accounts)


def set_role(name: str,
             role: str) -> None:
    """The account's role changed; its sessions keep going under the new role."""

    _update_account(name, {"role": _check_role(role)})


def set_password(name: str,
                 password: str) -> None:
    """The account's password changed, with a new salt; every session it had is ended."""

    salt = secrets.token_bytes(16)
    _update_account(name, {"salt": salt.hex(), "hash": _build_password_hash(_check_password(password), salt)})
    _end_sessions_of(name)


def remove_account(name: str) -> None:
    """The account and every session it had are gone."""

    with _lock:
        accounts = _load(ACCOUNTS_FILE)
        if accounts.pop(name, None) is None:
            raise AccountError(f"no account named {name!r}")
        _save(ACCOUNTS_FILE, accounts)
    _end_sessions_of(name)


def _end_sessions_of(name: str) -> None:
    with _lock:
        sessions = _load(SESSIONS_FILE)
        kept = {token: session for token, session in sessions.items() if session.get("name") != name}
        if len(kept) != len(sessions):
            _save(SESSIONS_FILE, kept)


def _read_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def sign_in(name: str,
            password: str) -> str | None:
    """A new session's token for the right name and password; None for a wrong one, never saying which."""

    account = _load(ACCOUNTS_FILE).get(name.strip())
    if account is None:
        return None
    attempt = _build_password_hash(password, bytes.fromhex(account["salt"]))
    if not hmac.compare_digest(attempt, account["hash"]):
        return None
    token = secrets.token_urlsafe(32)
    ends = _read_now() + datetime.timedelta(days=SESSION_DAYS)
    with _lock:
        sessions = _load(SESSIONS_FILE)
        sessions[token] = {"name": name.strip(), "ends": ends.isoformat()}
        _save(SESSIONS_FILE, sessions)

    return token


def read_session(token: str) -> dict[str, str] | None:
    """The account a live session's token belongs to, `{"name", "role"}`; None for an unknown, ended or orphaned one."""

    if not token:
        return None
    session = _load(SESSIONS_FILE).get(token)
    if session is None or datetime.datetime.fromisoformat(session["ends"]) <= _read_now():
        return None
    account = _load(ACCOUNTS_FILE).get(session["name"])

    return {"name": session["name"], "role": account["role"]} if account is not None else None


def sign_out(token: str) -> None:
    """The session ended; an unknown token changes nothing."""

    with _lock:
        sessions = _load(SESSIONS_FILE)
        if sessions.pop(token, None) is not None:
            _save(SESSIONS_FILE, sessions)
