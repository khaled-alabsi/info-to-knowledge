"""A conference broadcast's layout, and a lecture's camera inset, kept over phase 2's capture loop (`frame_extraction_service._apply_layout`)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy

from models.screen_box import Box

__all__ = ["BroadcastLayoutState"]


@dataclass
class BroadcastLayoutState:
    """The last slide panel found, and a small grey sample of the frame outside it (the backdrop and the camera), or
    None for both before a panel is found; the last camera tile found, or None; and the panel layouts found ahead of the capture loop, each its panel and its outside sample."""

    panel: Box | None = None
    outside: numpy.ndarray | None = None
    tile: Box | None = None
    known: list[tuple[Box, numpy.ndarray]] = field(default_factory=list)
