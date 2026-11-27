"""One model server the program calls by URL, and one file a request sends to it. Carriers only: `services/endpoint_client.py`
builds and reads them."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EndpointSpec", "MultipartFileSpec"]


@dataclass(frozen=True)
class EndpointSpec:
    base_url: str                                             # the address up to and including `/v1`
    api_key_env: str                                          # the NAME of the environment variable with the key; "" for none
    timeout_s: int                                            # how long one request may take
    model: str = ""                                           # the server's own model name; "": the program's name for it


@dataclass(frozen=True)
class MultipartFileSpec:
    file_name: str                                            # the name the server sees for the part
    content: bytes                                            # the file's bytes
    media_type: str                                           # `audio/wav`, `image/png`, ...
