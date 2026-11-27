"""What phase 2 finds of the shared screen on one frame (`frame_extraction_service._find_shared_screen`)."""

from __future__ import annotations

from typing import NamedTuple

from models.screen_box import Box

__all__ = ["SharedScreenResult"]


class SharedScreenResult(NamedTuple):
    """`box` the shared screen, x0, y0, x1, y1, or NO_SCREEN; `tile` a presenter's camera floating in one of its
    corners, covered before the frame is taken, or None; `is_panel` whether `box` is a broadcast's slide panel."""

    box: Box
    tile: Box | None
    is_panel: bool = False
