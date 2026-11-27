"""What `i2k reset-chapter N` removes, worked out once for both its listing and the reset itself."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ChapterResetSpec"]


@dataclass(frozen=True)
class ChapterResetSpec:
    files: list[Path]                           # the files removed, under the run folder
    follow_up_slugs: list[str]                  # the follow-ups whose records go, so a resume asks them again
    unplaced_cut_ids: list[str]                 # the cuts in use no kept section places, set aside
    frame_decisions: list[tuple[str, dict]]     # (store key, record) of each leave_out or diagram decision taken back
