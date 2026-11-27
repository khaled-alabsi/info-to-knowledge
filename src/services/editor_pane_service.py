"""An editor split into panes one above the other, found on a stored frame by each pane's status line (DEFECTS 129).

A terminal editor split in two ends each pane on a status line of its own, which names that pane's file. The worker saw
one pane: an outline named only the bottom pane's file, and a second look read the file on one status line as holding
"both the implementation and the tests". The text reader alone does not find both lines: it read the active pane's line
without its file name, and the other pane's dim line not at all. So a status line is a row the reader reads as one
(a column count `N:N`, a share `N%`, an editor mode) or a band one text line tall across the pane in a grey of its own,
and each is read again alone, enlarged, for the file it names.
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy

from services import text_detection_service

__all__ = ["list_split_panes"]

# What the text reader reads on a status line that a line of code does not: the cursor's line and column at its end,
# the editor's mode, the file's encoding. A share alone ("[ 75%]") is a test runner's progress too
_STATUS_WORDS = re.compile(r"\b\d+:\d+$|\b(?:NORMAL|INSERT|VISUAL|REPLACE)\b|\butf-8\b")
# A status line read alone shows the share of the file above the cursor or the cursor's line and column as a word of
# its own: a linter's "utils.py:27:26" and a band of a code diff do not
_SHARE = re.compile(r"\b\d{1,3}%|(?:^|\s)\d+:\d+(?:\s|$)")
# A band: one text line tall at the stored frame's size (16 to 18 rows of 688 on a lecture), its rows within this many
# grey levels of their own median on this share of the pane, and the rows round it this many levels off
_BAND_HEIGHT = (0.015, 0.045)
_BAND_LEVELS = 12
_BAND_FLAT = 0.6
_RUN_LEVELS = 6
# A pane is at least this share of the frame wide, and the first status line stands below the window's top bars
_PANE_MIN_WIDTH = 0.3
_TOP_SHARE = 0.15
# A terminal's pane border: a line one or two columns wide whose grey stands off the columns this far either side by
# this many levels, on this share of the rows (a tmux border beside an editor stood 10 to 39 levels off on 46% to 51%)
_LINE_SIDE_PX = 3
_LINE_LEVELS = 8
_LINE_ROWS = 0.4
# Two status lines this close are one
_SAME_ROW = 0.03
# Each status line is read alone, a band this share of the frame tall, its contrast stretched and this many times larger,
# as one line, and once more with its greys turned over for a light block of dark letters: read at the band's own
# height, a dim pane's line gave its file name, read taller, misread letters
_READ_HALF = 0.016
_READ_SCALE = 3
_ONE_LINE_MODE = 7
# A file name, its extension letters; the reader reads "test_refunds. py" with a space, which is taken out
_FILE_NAME = re.compile(r"[A-Za-z_][\w-]*(?:\.[\w-]+)*\. ?[A-Za-z]\w{0,5}\b")


def _find_bands(gray: numpy.ndarray,
                x0: int,
                x1: int) -> list[int]:
    """The middle row of each band of `gray` between columns `x0` and `x1`."""

    height = gray.shape[0]
    part = gray[:, x0:x1].astype(numpy.int16)
    median = numpy.median(part, axis=1)
    flat = (numpy.abs(part - median[:, None]) < _BAND_LEVELS).mean(axis=1)
    rows, top = [], 0
    while top < height:
        bottom = top
        while bottom + 1 < height and abs(median[bottom + 1] - median[top]) < _RUN_LEVELS:
            bottom += 1
        size = bottom - top + 1
        middle = median[top + size // 2]
        is_band = (_BAND_HEIGHT[0] * height <= size <= _BAND_HEIGHT[1] * height and top > 2 and bottom < height - 3
                   and abs(middle - median[top - 3]) >= _BAND_LEVELS and abs(middle - median[bottom + 3]) >= _BAND_LEVELS
                   and flat[top:bottom + 1].mean() >= _BAND_FLAT)
        if is_band:
            rows.append(top + size // 2)
        top = bottom + 1

    return rows


def _find_border_columns(gray: numpy.ndarray) -> list[int]:
    """The columns of `gray` that are a thin vertical line: a terminal's border between panes side by side."""

    pixels = gray.astype(numpy.int16)
    side = _LINE_SIDE_PX
    middle = pixels[:, side:-side]
    is_off = ((numpy.abs(middle - pixels[:, :-2 * side]) > _LINE_LEVELS)
              & (numpy.abs(middle - pixels[:, 2 * side:]) > _LINE_LEVELS))

    return [int(column) + side for column in numpy.flatnonzero(is_off.mean(axis=0) >= _LINE_ROWS)]


def _list_status_lines(words: list[dict]) -> list[tuple[int, int, int]]:
    """Each line the text reader reads as a status line: its middle row, its left and its right edge."""

    lines: dict[tuple, list[dict]] = {}
    for word in words:
        lines.setdefault((word.get("block"), word.get("line")), []).append(word)
    found = []
    for line in lines.values():
        text = " ".join(word["text"] for word in sorted(line, key=lambda word: word["box"][0]))
        if _STATUS_WORDS.search(text):
            found.append(((min(word["box"][1] for word in line) + max(word["box"][3] for word in line)) // 2,
                          min(word["box"][0] for word in line), max(word["box"][2] for word in line)))

    return found


def _read_status_line(image: numpy.ndarray,
                      row: int,
                      x0: int,
                      x1: int) -> str:
    half = max(6, round(_READ_HALF * image.shape[0]))
    gray = cv2.cvtColor(image[max(0, row - half):row + half, x0:x1], cv2.COLOR_BGR2GRAY)
    stretched = cv2.resize(cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX), None, fx=_READ_SCALE, fy=_READ_SCALE,
                           interpolation=cv2.INTER_CUBIC)
    readings = [" ".join(word["text"] for word in text_detection_service.find_words_in_image(
        cv2.cvtColor(part, cv2.COLOR_GRAY2BGR), page_mode=_ONE_LINE_MODE)) for part in (stretched, 255 - stretched)]

    return " · ".join(reading for reading in readings if reading)


def list_split_panes(frame_path: Path) -> list[dict]:
    """The panes of an editor split one above the other on the stored frame, top first, each `{"row", "status",
    "file"}`: the row of its status line, that line as read alone, and the file it names ("" when none is read). Empty
    when the frame shows no two status lines in one column span below its top bars, or none of them names a file."""

    image = cv2.imread(str(frame_path))
    if image is None or not text_detection_service.is_available():
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    words = text_detection_service.find_words(frame_path)
    read = [line for line in _list_status_lines(words) if line[0] > _TOP_SHARE * height]
    if not read:
        return []
    # The panes' span is the widest status line the reader read, with every word read on its row: the reader read a
    # status line's share ("19%") as a line of its own; the bands are looked for across it
    on_rows = [word for word in words
               if any(abs((word["box"][1] + word["box"][3]) / 2 - line[0]) <= _SAME_ROW * height for line in read)]
    # ... but never past a border to the pane beside it, a test runner's lines on the same rows
    x0, x1 = min(line[1] for line in read), max(line[2] for line in read)
    borders = _find_border_columns(gray)
    right = min([border for border in borders if border >= x1] + [width])
    left = max([border for border in borders if border <= x0] + [0])
    x0 = min([x0] + [word["box"][0] for word in on_rows if word["box"][0] >= left])
    x1 = max([x1] + [word["box"][2] for word in on_rows if word["box"][2] <= right])
    if x1 - x0 < _PANE_MIN_WIDTH * width:
        return []
    bands = [row for row in _find_bands(gray, x0, x1) if row > _TOP_SHARE * height]
    rows = sorted([line[0] for line in read] + bands)
    merged = [row for index, row in enumerate(rows) if not index or row - rows[index - 1] > _SAME_ROW * height]
    panes = []
    for row in merged:
        status = _read_status_line(image, row, x0, x1)
        # A band whose reading names a file is a dim pane's status line whose share the reader lost
        is_band = any(abs(row - band) <= _SAME_ROW * height for band in bands)
        if not (_SHARE.search(status) or (is_band and _FILE_NAME.search(status))):
            continue
        named = _FILE_NAME.findall(status)
        panes.append({"row": row, "status": status, "file": named[0].replace(" ", "") if named else ""})

    # A split is named only when a pane's file is read: of Lecture 9's 39 frames found split, the 3 with no file read
    # were 2 frames of one pane (a cursor row and a scanner's result line read as status lines) and 1 real split
    return panes if len(panes) >= 2 and any(pane["file"] for pane in panes) else []
