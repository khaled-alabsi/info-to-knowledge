"""What one frame tool did: the text the agent is told, the frame it is shown after it, and whether it was refused.
REQUIREMENTS.md R10."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["IllustrationActionResult"]


@dataclass(frozen=True)
class IllustrationActionResult:
    text: str
    frame_path: Path | None = None                # shown to the agent right after the text, then dropped
    is_refused: bool = False                      # the tool could not do what was asked; `failOnFrameCutFail` stops on it
    summary: str = ""                             # a mask's first sentence alone, to sum up several masks of one call
    is_applied: bool = False                      # a mark or a mask was drawn on the cut
