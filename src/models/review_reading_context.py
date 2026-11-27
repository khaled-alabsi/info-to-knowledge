"""What phase 5's reading tools read: the document as the review has it now, the transcript, every entry the document
was written from, phase 2's frames and phase 3's notes on them, and the limits a read stays within.
REQUIREMENTS.md R16. Built once per phase; the document is read from its file at each read, since every accepted edit
rewrites it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.illustration_context import IllustrationContext
from models.run_paths import RunPaths

__all__ = ["ReviewReadingContext"]


@dataclass(frozen=True)
class ReviewReadingContext:
    paths: RunPaths
    document_path: Path                             # the working copy of the document, every accepted edit made
    transcript: list[dict]                          # every speech entry of the merge, in clock order
    entries: list[dict]                             # every entry of the merge, in clock order
    frame_items: dict[int, dict]                    # frame number -> phase 2's item, for its frame and its time
    frame_records: dict[int, dict]                  # frame number -> phase 3's notes on it
    read_chars: int                                 # the most characters one chunk of a read returns
    notes_chars: int                                # the most characters all notes together may hold
    read_budget: int                                # the reads one follow-up may make
    # The frame tools a figure todo recuts with: phase 4's own, over its frame store, so a recut is a cut phase 4 knows
    illustration: IllustrationContext | None = None
