"""A client of the running `i2k serve`, for the front ends that are not the page: `i2k mcp`'s tools and `i2k run
--queue` call the same HTTP API the page calls, so every front end shares one queue and one session per run folder.
"""

from __future__ import annotations

import os
import socket
from typing import Any

import httpx

import config
from services import client_token_service
from utils import http_refusal_utils

__all__ = ["DEFAULT_SERVER_URL", "SERVER_URL_ENV", "call_server", "format_url_host", "pick_address_family", "set_http_client",
           "set_server_url"]

SERVER_URL_ENV = "I2K_SERVER_URL"
DEFAULT_SERVER_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"

# How long one call may take: an upload of a long recording through `POST /inbox/upload` is the slowest
_TIMEOUT_S = 120.0

_http_client: httpx.Client | None = None
# Whether `_http_client` is this module's own, closed when it is replaced; a client handed in is its owner's
_is_own_client = False
# The address `i2k mcp --server` named; unset, `SERVER_URL_ENV` or this machine's default
_server_url = ""


def pick_address_family(host: str) -> socket.AddressFamily:
    """IPv6 for an address with a colon in it, else IPv4."""

    return socket.AF_INET6 if ":" in host else socket.AF_INET


def format_url_host(host: str) -> str:
    """A host as a URL writes it: an IPv6 address in brackets."""

    return f"[{host}]" if pick_address_family(host) == socket.AF_INET6 else host


def _close_own_client() -> None:
    """Close the client this module made, when it has one; a client handed in is its owner's to close."""

    if _http_client is not None and _is_own_client:
        _http_client.close()


def set_server_url(url: str) -> None:
    """The `i2k serve` every later call goes to."""

    global _http_client, _server_url, _is_own_client
    _server_url = url
    _close_own_client()
    _http_client, _is_own_client = None, False


def set_http_client(client: httpx.Client | None) -> None:
    """The client every call sends through; a test hands in the server app's own test client."""

    global _http_client, _is_own_client
    _close_own_client()
    _http_client, _is_own_client = client, False


def _get_client() -> httpx.Client:
    global _http_client, _is_own_client
    if _http_client is None:
        base_url = _server_url or os.environ.get(SERVER_URL_ENV, DEFAULT_SERVER_URL)
        _http_client, _is_own_client = httpx.Client(base_url=base_url, timeout=_TIMEOUT_S,
                                                    headers=client_token_service.build_auth_headers()), True

    return _http_client


def call_server(method: str,
                path: str,
                **request: Any) -> Any:
    """The API's JSON answer to `method /api<path>`, or a RuntimeError naming the server's refusal, or saying no server
    answers."""

    try:
        response = _get_client().request(method, f"/api{path}", **request)
    except httpx.ConnectError as error:
        raise RuntimeError(f"no `i2k serve` answers at {_get_client().base_url}: start it first ({error})") from error
    except httpx.HTTPError as error:
        raise RuntimeError(f"`i2k serve` at {_get_client().base_url} did not answer {method} /api{path}: {error}") from error
    if response.status_code >= 400:
        raise RuntimeError(http_refusal_utils.read_refusal_text(response))
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(f"`i2k serve` at {_get_client().base_url} answered {method} /api{path} with no JSON: "
                           f"{response.text[:200]}") from error
