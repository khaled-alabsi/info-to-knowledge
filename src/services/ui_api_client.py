"""The terminal UI's calls to the server's API (`services/web_api_service.py`), one function per verb.

An error the server answers with (a refused upload, an unknown folder) is raised as the server worded
it, so the terminal shows the same reason the page does.
"""

from __future__ import annotations

import httpx

from utils import http_refusal_utils

from services import client_token_service
from models.ui_errors import ServerAnswerError

__all__ = ["is_server_up", "fetch_json", "send_json", "set_transport"]

_TIMEOUT_S = 150.0
# How the calls reach the server: the network by default; a test hands in the API app in process.
_transport: httpx.AsyncBaseTransport | None = None


def _raise_for_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    raise ServerAnswerError(http_refusal_utils.read_refusal_text(response))


def set_transport(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncBaseTransport | None:
    """Swap how `fetch_json` and `send_json` reach the server; returns the transport it replaced."""

    global _transport
    previous, _transport = _transport, transport

    return previous


def is_server_up(base_url: str,
                 timeout_s: float = 2.0) -> bool:
    """Whether a server answers at `base_url` within `timeout_s`, asked over the network: a transport handed in by
    `set_transport` is not used, since the question is whether a server runs there."""

    try:
        # The one route open with accounts on: asked for the queue, a signed-out client saw no server
        return httpx.get(f"{base_url}/api/auth/me", timeout=timeout_s).is_success
    except httpx.HTTPError:
        return False


async def _call(method: str,
                base_url: str,
                path: str,
                **request) -> object:
    """`method /api<path>` with `request` as httpx takes it, as JSON; a refusal raised in the server's words."""

    # A server that is down, slow or answers no JSON is raised in this layer's words, as a refusal is
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=_TIMEOUT_S, transport=_transport,
                                     headers=client_token_service.build_auth_headers()) as client:
            response = await client.request(method, f"/api{path}", **request)
    except httpx.HTTPError as exc:
        raise ServerAnswerError(f"cannot reach {base_url}: {exc}") from exc
    _raise_for_error(response)
    try:
        return response.json()
    except ValueError as exc:
        raise ServerAnswerError(f"{base_url} answered {method} /api{path} with no JSON") from exc


async def fetch_json(base_url: str,
                     path: str,
                     params: dict | None = None):
    """GET `/api<path>`, as JSON."""

    return await _call("GET", base_url, path, params=params)


async def send_json(base_url: str,
                    path: str,
                    body: dict | None = None):
    """POST `body` to `/api<path>`, and the JSON answer."""

    return await _call("POST", base_url, path, json=body or {})
