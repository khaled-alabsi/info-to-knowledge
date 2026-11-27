"""What one of phase 4's conversations works in: the run's paths, the recording, the frames phase 2 took, and the
chapter the conversation is about. REQUIREMENTS.md R10. Lives for one phase."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.run_paths import RunPaths

__all__ = ["IllustrationContext"]


@dataclass(frozen=True)
class IllustrationContext:
    paths: RunPaths
    video_path: Path                                # the recording a cut is taken from at full resolution
    frames: dict[str, dict]                         # slug -> the frame's item in 02_frames.json
    min_cut_pixels: int                             # a shorter cut side than this comes back with a warning
    chapter: str                                    # the chapter this conversation is about
