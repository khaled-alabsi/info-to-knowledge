"""What the classifier decided for one frame."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameDecision:
    key: str            # `<dataset>/<frame file>`, or the file name for a folder outside the datasets
    score: float        # 0 to 1: how far the frame lies on the skip side of the classifier's dividing line
    decision: str       # one of its task's two labels
    milliseconds: float = 0.0
