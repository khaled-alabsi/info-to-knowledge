"""What the reading tools of phase 4 and 5 keep across calls: the reads spent, the next result id, the notes, the reads
seen in the current review todo, the chapters read and the image store. REQUIREMENTS.md R12. The notes are also on
disk; this is the live copy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["WriteReadingState"]


@dataclass
class WriteReadingState:
    reads_used: int = 0                                   # against the phase's one read budget
    next_result: int = 1                                  # the number the next read result's id gets, `[r7]`
    notes: list[str] = field(default_factory=list)        # decisions later steps must keep
    # Each read of the current review todo, by its tool and arguments -> its result id and its first 300 characters: so a
    # repeated read is answered with a pointer to the result already given
    seen_reads: dict[str, tuple[str, str]] = field(default_factory=dict)
    chapters_read: set[int] = field(default_factory=set)  # the chapters `read_notes` returned, each whole
    # The looks and cuts a chapter's image tools made, in phase 5's store shape; empty until the first look
    image_store: dict[str, Any] = field(default_factory=dict)
