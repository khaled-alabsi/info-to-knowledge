"""What one chapter's follow-up recorded for its needed frames, read from phase 4's image store."""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["FrameDispositionsResult"]


class FrameDispositionsResult(NamedTuple):
    """The frames `left_out`; of them, the ones `shown_elsewhere` (left out as shown by a placed frame); and the frames
    `replaced` by a diagram."""

    left_out: set[str]
    shown_elsewhere: set[str]
    replaced: set[str]
