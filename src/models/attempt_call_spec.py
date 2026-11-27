"""One model call of an attempt: the name its files and console lines go under, the request it answers, and the
messages it was sent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.attempt_request import AttemptRequest

__all__ = ["AttemptCallSpec"]


@dataclass(frozen=True)
class AttemptCallSpec:
    name: str
    request: AttemptRequest
    messages: list[Any] = field(default_factory=list)
