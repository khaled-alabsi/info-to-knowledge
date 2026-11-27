"""A crop box found on a check that differs from the one in use, waiting to replace it (`screen_crop_utils.choose_crop_box`)."""

from __future__ import annotations

from typing import NamedTuple

from models.screen_box import Box

__all__ = ["PendingBoxState"]


class PendingBoxState(NamedTuple):
    """`box` the box found, x0, y0, x1, y1; `checks` how many checks in a row found it."""

    box: Box
    checks: int
