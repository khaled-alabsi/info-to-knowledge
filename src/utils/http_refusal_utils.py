"""The text of a refusal the `i2k serve` API answers with, read the same way by every client of it."""

from __future__ import annotations

import httpx

__all__ = ["read_refusal_text"]


def read_refusal_text(response: httpx.Response) -> str:
    """`<status>: <reason>` of a refused response: the `detail` of an object body, else the body's text as it came (a
    proxy in between can answer with a list or a bare string)."""

    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail", response.text) if isinstance(body, dict) else response.text

    return f"{response.status_code}: {detail}"
