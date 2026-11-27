"""One frame cut into what the text-only task sees on it: the text, the other ink it counts, and what it sets aside."""

from __future__ import annotations

from dataclasses import dataclass

import numpy


@dataclass(frozen=True)
class InkPartsRecord:
    image: numpy.ndarray            # the frame at the measuring width, dark edge bands cropped (BGR)
    distance: numpy.ndarray         # each pixel's distance from the background colour, in grey levels
    ink: numpy.ndarray              # 1 where a pixel is ink
    text: numpy.ndarray             # 1 inside a box read as text
    lines: numpy.ndarray            # 1 on a plain rule nothing touches (of no value)
    small: numpy.ndarray            # 1 on a mark under 40 x 40 pixels (of no value)
    other: numpy.ndarray            # 1 on the other ink the measures count
    other_edges: numpy.ndarray      # 1 on a sharp edge outside the text, the rules and the small marks
    words: list                     # (x, y, width, height, line key) of each word read
    colour_words: list              # the words whose colour is judged: every word but a code keyword (def, return)
    word_height: float              # the median word box height
