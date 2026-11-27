"""What dropping frames from a run did: the frames dropped and, when they came from an accepted Refine proposal, the
classifier's mistakes the person corrected and where they were saved as learning data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FrameDropResult"]


@dataclass(frozen=True)
class FrameDropResult:
    """`false_drops` (FP: proposed, kept by the person) and `missed_drops` (FN: dropped, not proposed) are empty with no
    Refine; `corrections_dir` is None then."""

    dropped: list[str]
    false_drops: list[str] = field(default_factory=list)
    missed_drops: list[str] = field(default_factory=list)
    corrections_dir: Path | None = None
