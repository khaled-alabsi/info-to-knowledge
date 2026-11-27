"""The shapes phase 2's crop is written in: a screen box or a face, and the bars cut off a crop's edges."""

from __future__ import annotations

__all__ = ["Box", "BarMargins", "NO_SCREEN", "NO_BARS"]

# A screen box or a face, x0, y0, x1, y1, end exclusive
Box = tuple[int, int, int, int]
# What to cut off a crop's edges: top, bottom, left, right
BarMargins = tuple[int, int, int, int]

NO_SCREEN: Box = (0, 0, 0, 0)               # the box of a frame with nothing shared: a gallery of faces
NO_BARS: BarMargins = (0, 0, 0, 0)          # top, bottom, left, right: nothing to cut
