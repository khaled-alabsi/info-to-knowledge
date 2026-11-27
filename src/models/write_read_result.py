"""What one reading tool of phase 4's or phase 5's conversation returns: its text, a frame when it showed one.
REQUIREMENTS.md R12."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["WriteReadResult"]


@dataclass(frozen=True)
class WriteReadResult:
    text: str                            # what the agent reads back, starting with its id when minted
    frame_path: Path | None = None       # the image the read showed: a frame, or a look's or a cut's
    is_frame_tool_refused: bool = False  # a frame tool refused what it was asked
