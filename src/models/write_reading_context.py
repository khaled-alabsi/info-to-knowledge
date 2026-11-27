"""What phase 4's reading tools read: the meeting as moments, the agenda's points, phase 2's frames and phase 3's
records, the whole transcript, phase 3's batches, the notes file's sections, and the limits a read stays within.
REQUIREMENTS.md R12. Built once per phase; the agenda's starts are filled in once the agenda is accepted, and the
chapter at each chapter's follow-up."""

from __future__ import annotations

from dataclasses import dataclass, field

from models.illustration_context import IllustrationContext
from models.run_paths import RunPaths

__all__ = ["WriteReadingContext"]


@dataclass(frozen=True)
class WriteReadingContext:
    paths: RunPaths
    moments: list[dict]                                                   # the entries grouped by their first time, with their labels
    point_starts: list[float]                                             # each agenda step's start; empty before the agenda
    frame_items: dict[int, dict]                                          # frame number -> phase 2's item, for its frame
    frame_records: dict[int, dict]                                        # frame number -> phase 3's record
    read_chars: int                                                       # the most characters one chunk of a read returns
    notes_chars: int                                                      # the most characters all notes together may hold
    read_budget: int                                                      # the reads the whole phase may make
    transcript: list[dict] = field(default_factory=list)                  # every speech entry of the merge, in clock order
    batches: list[dict] = field(default_factory=list)                     # phase 3's accounts of its batches, in time order (R15)
    note_sections: list[tuple[float, str]] = field(default_factory=list)  # the notes file's sections, (start, text)
    # What a chapter's look, cut, mark and mask tools work in: phase 4's own, so a cut made here is one phase 4 knows
    illustration: IllustrationContext | None = None
