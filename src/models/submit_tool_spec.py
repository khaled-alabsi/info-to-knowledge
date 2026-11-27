"""One submit tool of a conversation (phase 4's writing tools, phase 5's review tools): the schema its arguments follow
and the kind of job it judges.
REQUIREMENTS.md R12."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

__all__ = ["SubmitToolSpec"]


@dataclass(frozen=True)
class SubmitToolSpec:
    schema: type[BaseModel]                         # the schema of the tool's arguments, the same one the calls path uses
    job_kind: str                                   # which writer and validator judge it
