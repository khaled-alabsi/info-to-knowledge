"""Words struck through on a stored frame, found by the line drawn through their letters (DEFECTS 627).

The text readers return a struck word as any other word, with no mark of the line through it, so the worker judged a
strike by eye on a 720-pixel frame: a slide that struck out "gpu.js, onnx, wasm" before "WebGL!" was outlined as
striking only "onnx, wasm", and a reader took gpu.js as still in use. Letters leave gaps along any row; a line drawn
through them does not. So a word is struck when one thin row of ink runs unbroken across its box, in the middle of its
letters, while its other rows leave most of their pixels blank.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy

from services import text_detection_service

__all__ = ["is_struck", "list_struck_words"]

# A word this long in letters, letters this share of its characters: a rule of "=" signs read as a word is no word
_LEAST_LETTERS = 4
_LETTER_SHARE = 0.6
# A pixel this many grey levels off the word box's border is ink
_INK_LEVELS = 60
# The line runs unbroken across this share of the box's width, is at most this share of the letters' height thick,
# and lies this far down them (an underline lies at their foot)
_LINE_WIDTH_SHARE = 0.9
_LINE_THICKNESS_SHARE = 0.25
_LINE_DEPTH = (0.3, 0.7)
# Outside the line, under this share of the letters' rows is ink: a filled badge, a texture or a box is more
_REST_INK_SHARE = 0.5
# Measured over the words of 4 letters or more of 9 public runs (19,493): named only openpv frame 067's struck words and
# Lecture 7's struck todo items (frames 020, 023, 024); measured once, offline


def _list_longest_runs(ink: numpy.ndarray) -> numpy.ndarray:
    """Each row's longest unbroken run of ink, in pixels."""

    longest = []
    for row in ink:
        padded = numpy.concatenate([[0], row.astype(numpy.int8), [0]])
        edges = numpy.flatnonzero(numpy.diff(padded))
        longest.append(int((edges[1::2] - edges[::2]).max()) if edges.size else 0)

    return numpy.array(longest)


def is_struck(gray: numpy.ndarray,
              box: list[int]) -> bool:
    """Whether a line is drawn through the word in `box` (`[x0, y0, x1, y1]` in `gray`'s pixels)."""

    x0, y0, x1, y1 = box
    word = gray[y0:y1, x0:x1].astype(numpy.int16)
    if word.size == 0:
        return False
    border = numpy.concatenate([word[0], word[-1], word[:, 0], word[:, -1]])
    ink = numpy.abs(word - numpy.median(border)) > _INK_LEVELS
    inked = numpy.flatnonzero(ink.any(axis=1))
    if inked.size < 4:
        return False
    top, bottom = int(inked[0]), int(inked[-1]) + 1
    full = numpy.flatnonzero(_list_longest_runs(ink) >= _LINE_WIDTH_SHARE * (x1 - x0))
    if not full.size or full.size > _LINE_THICKNESS_SHARE * (bottom - top):
        return False
    depth = (full.mean() - top) / (bottom - top)
    if not _LINE_DEPTH[0] <= depth <= _LINE_DEPTH[1]:
        return False
    line_rows = set(range(int(full.min()) - 1, int(full.max()) + 2))
    rest = [row for row in range(top, bottom) if row not in line_rows]

    return bool(rest) and float(ink[rest].mean()) < _REST_INK_SHARE


def _is_word(text: str) -> bool:
    letters = sum(character.isalpha() for character in text)

    return letters >= _LEAST_LETTERS and letters >= _LETTER_SHARE * len(text)


def list_struck_words(frame_path: Path) -> list[str]:
    """The words struck through on the frame, as the text reader reads them, in reading order; a struck word is often
    read garbled or at a low confidence, so every word is looked at whose line holds one read at the screen text's."""

    gray = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return []
    sure_lines = {word["line"] for word in text_detection_service.find_words(
        frame_path, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)}

    return [word["text"] for word in text_detection_service.find_words(frame_path, 0)
            if word["line"] in sure_lines and _is_word(word["text"]) and is_struck(gray, word["box"])]
