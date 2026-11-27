"""The facts a document's YAML front matter is written from. Lives for one call."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["FrontMatterRequest"]


@dataclass(frozen=True)
class FrontMatterRequest:
    topic: str
    event: str
    speakers: list[str]
    meeting_date: str                               # `YYYY-MM-DD`, or empty when nobody established it
    tags: list[str] = field(default_factory=list)   # the run's tags
    part: str = ""                                  # the part of the recording processed ("12:30 to 1:05:00"), empty when the run processed all of it
