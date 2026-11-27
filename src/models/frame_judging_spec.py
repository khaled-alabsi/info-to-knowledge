"""How one pass of phase 2's model filters asks its model: the redundancy filter over consecutive batches, or the
similarity recheck over groups of frames that look alike."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FrameJudgingSpec"]


@dataclass(frozen=True)
class FrameJudgingSpec:
    """`model`: the configured model's name. `frame_width`: each frame's width as sent. `dropped_by`: the layer a
    removed frame is listed under. `label`: the pass's name in its console line. `report_progress(frames judged)`
    after each call, or None. `max_missing_words`: a removal stands only when its holders show all but at most this
    many of the removed frame's words; None: no word check. `run_dir`: the run's log folder, where each call's tokens and
    time go into the call metrics phase 2's closing line counts; None: not written. `min_words`: under the word check, a
    removal stands only when the removed frame shows at least this many words to compare (a photo, a room shot or a
    title card shows too few, and the check would see nothing missing)."""

    model: str
    frame_width: int
    dropped_by: str
    label: str
    report_progress: Callable[[int], None] | None = None
    max_missing_words: int | None = None
    run_dir: Path | None = None
    min_words: int = 0
