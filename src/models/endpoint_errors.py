"""Why a call to a model server failed. Carriers only: `services/endpoint_client.py` raises it, and whoever called the
server decides what is next."""

from __future__ import annotations

__all__ = ["EndpointError"]


class EndpointError(RuntimeError):
    """A model server did not give a usable answer. It names the URL called, the HTTP status the server answered (None
    when nobody answered, or the key's variable is not set) and what went wrong."""

    def __init__(self,
                 url: str,
                 status: int | None,
                 detail: str) -> None:
        self.url = url
        self.status = status
        self.detail = detail
        answered = f"HTTP {status}" if status is not None else "no answer"
        super().__init__(f"{url}: {answered}: {detail}")
