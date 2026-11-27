"""Regions of a frame, for phase 4's frame tools (look, cut, mark, mask). REQUIREMENTS.md R10. Pure functions over numpy arrays.

The agent sees a frame as phase 2 stored it, at most 1280 pixels wide, with a labelled grid and the boxes the program
found in it as anchor points. It names a region in THAT frame's pixels; the program maps the region to the
recording's own pixels and cuts it from the recording, so a small region stays readable. A rectangle is always
`[x0, y0, x1, y1]`, the far edges excluded.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import cv2
import numpy


__all__ = ["drop_blank_boxes", "draw_grid", "is_label_value_masked", "holds_text_outside", "is_followed_by_mask", "draw_labelled_boxes",
           "find_panel_boxes", "find_figure_boxes", "find_content_box", "build_rect_above_footer", "list_cut_panels", "map_to_recording", "expand_rect", "clip_rect",
           "list_crossed_words", "list_lined_up_blocks", "list_continued_rows", "list_lines_cut_at_sides", "REPEATED_SAME_WORDS_SHARE",
           "expand_to_whole_blocks", "is_centre_inside", "draw_mark", "hide_region", "GRID_STEP",
           "compute_masked_share", "compute_content_difference", "MOST_MASKED_SHARE", "REPEATED_FIGURE_SHARE",
           "PANE_GAP_HEIGHTS", "BLANK_SPREAD"]

# A gap inside a line wider than this many of its heights splits it: two panes stood 10 heights apart and more, while an
# editor's grey hint inside a code line ("selector:") left about 4, and lines cut there lost their ends (2026-10-02)
PANE_GAP_HEIGHTS = 8
# A cut whose grey levels spread less than this shows an empty area: over the 91 cuts of earlier runs, the one empty
# stretch of an editor spread 2.2 and the plainest real figure 13.1
BLANK_SPREAD = 6.0

GRID_STEP = 100
_GRID_COLOUR = (0, 170, 255)
_MARK_COLOUR = (0, 0, 230)
_BOX_COLOUR = (40, 160, 40)
# A panel is a closed rectangle covering between these shares of the frame.
_PANEL_MIN_SHARE = 0.02
_PANEL_MAX_SHARE = 0.95
# An outline whose area is this share of its bounding box is a rectangle whatever its corners (DEFECTS 392): on the public
# runs' looked-at frames it adds boxes on 65 of 118, panes, a diagram's shapes and status bars by looking at a sample
_PANEL_FILLED_SHARE = 0.8
# A panel's outline is found by edges this strong: app cards outlined 20 grey levels off white had no box at 50/150, and a
# cut's edge ran through one unseen (2026-10-04); over one meeting's 96 frames 20/60 found 235 panels against 158, the
# added ones whole tables and cards, and left 21 frames with none against 34
_PANEL_EDGE_THRESHOLDS = (20, 60)
# A cut's edge runs through a panel that starts inside the cut and goes on past that edge by more than this
_PANEL_CROSS_PIXELS = 12
# A tinted panel: grey levels this far off the background (a light fill, never dark ink), filling this much of its
# rectangle; a box this much inside another counts as inside it
_TINT_LEVELS = (6, 60)
_TINT_FILL = 0.85
_INSIDE_SHARE = 0.9
# A figure is marks set off from the background by more than this many grey levels, joined across gaps narrower than
# _JOIN_PIXELS. A bar chart and a cluster of system boxes beside three closed text panels had no box, and a worker cut
# the panels for "the diagram and the bar chart" (2026-10-04).
_INK_LEVELS = 25
_JOIN_PIXELS = 31
# A rule broken into dashes with gaps up to this wide is still a rule
_DASH_GAP_PIXELS = 15
# The ink inside a panel this small is the panel's own, offered as the panel; a whole-slide panel is looked inside
_PANEL_FIGURE_SHARE = 0.30
# A figure with this share of its area in words is a table or a text panel, offered as its text boxes: on two meetings
# the charts and shape groups held at most 3% words, the tables a G box cut short of their last column 13% to 15%
_TEXT_FIGURE_SHARE = 0.08
# A figure is drawn: at least this share of it is solid shapes (bars, filled boxes) at least _SOLID_PIXELS across. Over
# twelve frames of four meetings, the slides' charts and box clusters passed it, and on app screens a card's corner, an
# editor's gutter, a table's column and a whole web page, all thin lines and text, did not.
_SOLID_PIXELS = 5
_SOLID_FIGURE_SHARE = 0.15
# A row of text in a cut's own columns this many line heights or less past its top or bottom edge continues the table or
# list the cut holds; it counts when at least _CONTINUED_MIN_CELLS of its lines start within _COLUMN_START_PIXELS of a
# line inside the cut, so a slide's title or footer, one line, is never named (2026-10-04: a table cut after its third row)
_CONTINUED_GAP_HEIGHTS = 3
_COLUMN_START_PIXELS = 12
_CONTINUED_MIN_CELLS = 2
# A figure past this share of the frame is the screen itself, which the whole frame already offers
_FIGURE_MAX_SHARE = 0.6
# A figure sharing this much of its area with a panel is that panel again
_REPEAT_OVERLAP = 0.9
# Two outlined rectangles whose every edge lies this many pixels or less apart are one panel found twice
_SAME_BOX_PIXELS = 6
# How far, as a share of its outline's length, a contour may stray from the four corners that make it a rectangle
_CORNER_TOLERANCE = 0.02
# A tint's gaps up to this many pixels are closed, then its specks under this many pixels across are taken out
_TINT_CLOSE_PIXELS = 15
_TINT_OPEN_PIXELS = 9
# Ink this many pixels across beside a rule is a solid shape the rule runs into, and stays
_RULE_SOLID_PIXELS = 4
# A rule is a straight run of ink at least this many pixels, or this fraction of the frame's side, long
_RULE_MIN_PIXELS = 40
_RULE_SIDE_FRACTION = 8
# Two images whose sides' ratios differ by more than this share are never compared
_SHAPE_TOLERANCE = 0.1
# Two images are compared at this many pixels wide
_COMPARE_WIDTH = 256
# A pixel this many grey levels off the shared background is content, and two content pixels this many apart differ
_CONTENT_LEVELS = 30
_DIFFERENT_LEVELS = 40


def _find_tinted_boxes(grey: numpy.ndarray) -> list[list[int]]:
    """The regions of a light tint on the background that fill their rectangle, such as a highlighted row: their edge
    is too faint for an outline. A highlighted bar 15 grey levels off white was outlined from its middle on, and a cut
    by that box lost the bar's label (2026-10-04)."""

    height, width = grey.shape[:2]
    gap = numpy.abs(grey.astype(int) - int(numpy.median(grey)))
    tint = ((gap >= _TINT_LEVELS[0]) & (gap <= _TINT_LEVELS[1])).astype(numpy.uint8)
    close = numpy.ones((_TINT_CLOSE_PIXELS, _TINT_CLOSE_PIXELS), numpy.uint8)
    tint = cv2.morphologyEx(cv2.morphologyEx(tint, cv2.MORPH_CLOSE, close), cv2.MORPH_OPEN,
                            numpy.ones((_TINT_OPEN_PIXELS, _TINT_OPEN_PIXELS), numpy.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(tint)

    return [[int(x), int(y), int(x + w), int(y + h)] for x, y, w, h, area in stats[1:count]
            if _PANEL_MIN_SHARE <= (w * h) / float(width * height) <= _PANEL_MAX_SHARE and area >= _TINT_FILL * w * h]


def _compute_shared_area(first: list[int],
                         second: list[int]) -> int:
    """The area two rectangles share."""

    return max(min(first[2], second[2]) - max(first[0], second[0]), 0) \
        * max(min(first[3], second[3]) - max(first[1], second[1]), 0)


def _is_inside(inner: list[int],
               outer: list[int]) -> bool:
    """Whether most of `inner`'s area lies in `outer`."""

    shared = _compute_shared_area(inner, outer)

    return shared >= _INSIDE_SHARE * (inner[2] - inner[0]) * (inner[3] - inner[1])


def _compute_covered_share(box: list[int],
                           others: list[list[int]]) -> float:
    """The share of `box`'s area that `others` cover together."""

    x0, y0, x1, y1 = box

    shifted = [[ox0 - x0, oy0 - y0, ox1 - x0, oy1 - y0] for ox0, oy0, ox1, oy1 in others]

    return compute_masked_share(x1 - x0, y1 - y0, shifted)


def _compute_overlap(first: list[int],
                     second: list[int]) -> float:
    """The area two rectangles share over the area they cover together."""

    shared = _compute_shared_area(first, second)
    together = (first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1]) - shared

    return shared / float(together) if together else 0.0


def _group_lines(words: list[dict]) -> list[dict]:
    """The text lines the words form, `{"box", "text"}`, by Tesseract's block and line."""

    groups: dict[tuple, list[dict]] = {}
    for word in words:
        groups.setdefault((word["block"], word.get("line")), []).append(word)
    lines = []
    for members in groups.values():
        ordered = sorted(members, key=lambda word: word["box"][0])
        lines.append({"box": [min(word["box"][0] for word in members), min(word["box"][1] for word in members),
                              max(word["box"][2] for word in members), max(word["box"][3] for word in members)],
                      "text": " ".join(word["text"] for word in ordered), "words": ordered})

    return lines


def _to_grey(image: numpy.ndarray) -> numpy.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def _is_word_crossed(rect: list[int],
                     word: dict) -> bool:
    """Whether `rect`'s edge cuts through the word: partly inside, partly outside."""

    x0, y0, x1, y1 = word["box"]
    is_overlapping = x0 < rect[2] and x1 > rect[0] and y0 < rect[3] and y1 > rect[1]
    is_inside = x0 >= rect[0] and y0 >= rect[1] and x1 <= rect[2] and y1 <= rect[3]

    return is_overlapping and not is_inside


def _read_line_key(word: dict) -> object:
    """The line a word belongs to: Tesseract's line, its block when no line is known."""

    return word.get("line", word.get("block"))


def _list_neighbours(word: dict,
                     words: list[dict]) -> list[dict]:
    """The words of `word`'s line that run on from it with no gap wider than PANE_GAP_HEIGHTS of its heights: the reader
    reads one row across two panes as one line, and a file list beside an editor was taken into the rectangle
    (2026-10-02)."""

    line = sorted((other for other in words if _read_line_key(other) == _read_line_key(word)),
                  key=lambda other: other["box"][0])
    gap = PANE_GAP_HEIGHTS * max(word["box"][3] - word["box"][1], 1)
    start = end = line.index(word)
    while start > 0 and line[start]["box"][0] - line[start - 1]["box"][2] <= gap:
        start -= 1
    while end < len(line) - 1 and line[end + 1]["box"][0] - line[end]["box"][2] <= gap:
        end += 1

    return line[start:end + 1]


def _list_grid_lines(width: int,
                     height: int,
                     line_count: int = 0) -> tuple[list[int], list[int]]:
    """Where the grid's lines stand across and down: every GRID_STEP pixels, or `line_count` lines spaced evenly across
    each side when it is given."""

    if line_count > 0:
        return ([round(width * number / (line_count + 1)) for number in range(1, line_count + 1)],
                [round(height * number / (line_count + 1)) for number in range(1, line_count + 1)])

    return list(range(GRID_STEP, width, GRID_STEP)), list(range(GRID_STEP, height, GRID_STEP))


def draw_grid(frame: numpy.ndarray,
              line_count: int = 0) -> numpy.ndarray:
    """A copy of `frame` with thin grid lines (`_list_grid_lines`), each labelled with its coordinate at the top and on
    the left edge."""

    drawn = frame.copy()
    height, width = drawn.shape[:2]
    across, down = _list_grid_lines(width, height, line_count)
    for x in across:
        cv2.line(drawn, (x, 0), (x, height - 1), _GRID_COLOUR, 1)
        cv2.putText(drawn, str(x), (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, _GRID_COLOUR, 1)
    for y in down:
        cv2.line(drawn, (0, y), (width - 1, y), _GRID_COLOUR, 1)
        cv2.putText(drawn, str(y), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, _GRID_COLOUR, 1)

    return drawn


def draw_labelled_boxes(frame: numpy.ndarray,
                        boxes: dict[str, list[int]]) -> numpy.ndarray:
    """A copy of `frame` with each box outlined thinly and labelled with its id at its top left corner."""

    drawn = frame.copy()
    for box_id, (x0, y0, x1, y1) in boxes.items():
        cv2.rectangle(drawn, (x0, y0), (x1 - 1, y1 - 1), _BOX_COLOUR, 1)
        cv2.putText(drawn, box_id, (x0 + 2, max(y0 + 12, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, _BOX_COLOUR, 1)

    return drawn


def drop_blank_boxes(frame: numpy.ndarray,
                     boxes: list[list[int]]) -> list[list[int]]:
    """The boxes with something on them: a box whose own pixels spread less than `BLANK_SPREAD` grey levels (a strip of
    the meeting tool's black stage left at a frame's edge) holds nothing to cut, and the worker cut one as the slide."""

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    return [box for box in boxes if grey[box[1]:box[3], box[0]:box[2]].size
            and float(grey[box[1]:box[3], box[0]:box[2]].std()) >= BLANK_SPREAD]


def find_panel_boxes(frame: numpy.ndarray) -> list[list[int]]:
    """The closed rectangles of the frame, such as a window, a table or a dialog, largest first, each once: outlined ones,
    and tinted regions an outline missed. An outline lying inside a tinted region gives way to it."""

    # STEP 1: outlined rectangles, from the frame's edges
    height, width = frame.shape[:2]
    grey = _to_grey(frame)
    edges = cv2.Canny(grey, *_PANEL_EDGE_THRESHOLDS)
    contours, _ = cv2.findContours(cv2.dilate(edges, numpy.ones((3, 3), numpy.uint8)), cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, _CORNER_TOLERANCE * cv2.arcLength(contour, True), True)
        x, y, w, h = cv2.boundingRect(approx)
        share = (w * h) / float(width * height)
        # A closed outline filling its box is a rectangle whatever its corners: a window's two panes, 9 corners each
        # where their borders met the content, were dropped, and the worker cut the title bar, the only box (DEFECTS 392)
        is_filled = cv2.contourArea(contour) >= _PANEL_FILLED_SHARE * w * h
        if (len(approx) == 4 or is_filled) and _PANEL_MIN_SHARE <= share <= _PANEL_MAX_SHARE:
            boxes.append([x, y, x + w, y + h])

    # STEP 2: tinted regions an outline missed; an outline inside one gives way to it
    tinted = [box for box in _find_tinted_boxes(grey) if not any(_is_inside(box, outlined) for outlined in boxes)]
    boxes = [box for box in boxes if not any(_is_inside(box, region) for region in tinted)] + tinted

    # STEP 3: each box once, largest first
    unique: list[list[int]] = []
    for box in sorted(boxes, key=lambda box: -(box[2] - box[0]) * (box[3] - box[1])):
        if not any(all(abs(a - b) <= _SAME_BOX_PIXELS for a, b in zip(box, kept)) for kept in unique):
            unique.append(box)

    return unique


def find_figure_boxes(frame: numpy.ndarray,
                      panels: list[list[int]],
                      word_boxes: list[list[int]]) -> list[list[int]]:
    """The figures of the frame no closed rectangle holds, such as a bar chart or a cluster of small boxes, largest
    first: the frame's marks set off from its background, thin rules dropped, near marks joined. A figure that repeats a
    panel or holds text is left out. The ink inside a panel of at most `_PANEL_FIGURE_SHARE` of the frame is not
    looked at, so a figure beside a panel stays apart from it."""

    # STEP 1: ink, the marks set off from the background
    height, width = frame.shape[:2]
    grey = _to_grey(frame)
    ink = (numpy.abs(grey.astype(int) - int(numpy.median(grey))) > _INK_LEVELS).astype(numpy.uint8)

    # STEP 2: rules, across and down; a dashed rule is closed into a line first, so its dashes go with it
    across = numpy.ones((1, max(_RULE_MIN_PIXELS, width // _RULE_SIDE_FRACTION)), numpy.uint8)
    down = numpy.ones((max(_RULE_MIN_PIXELS, height // _RULE_SIDE_FRACTION), 1), numpy.uint8)
    rules = cv2.morphologyEx(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, numpy.ones((1, _DASH_GAP_PIXELS), numpy.uint8)),
                             cv2.MORPH_OPEN, across) \
        | cv2.morphologyEx(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, numpy.ones((_DASH_GAP_PIXELS, 1), numpy.uint8)),
                           cv2.MORPH_OPEN, down)

    # STEP 3: only a rule outside every panel is a separator, taken out of the ink: a diagram's own lane lines hold it
    # together
    for x0, y0, x1, y1 in panels:
        rules[y0:y1, x0:x1] = 0
    ink &= 1 - (rules & (1 - cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                                  numpy.ones((_RULE_SOLID_PIXELS, _RULE_SOLID_PIXELS), numpy.uint8))))

    # STEP 4: the ink of a small panel is not looked at, so a figure beside it stays apart from it
    for x0, y0, x1, y1 in panels:
        if (x1 - x0) * (y1 - y0) <= _PANEL_FIGURE_SHARE * width * height:
            ink[y0:y1, x0:x1] = 0

    # STEP 5: near marks joined into boxes, kept when sized as a figure, mostly solid, not a panel again, nearly no text
    count, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(ink, numpy.ones((_JOIN_PIXELS, _JOIN_PIXELS),
                                                                                     numpy.uint8)))
    solid = cv2.morphologyEx(ink, cv2.MORPH_OPEN, numpy.ones((_SOLID_PIXELS, _SOLID_PIXELS), numpy.uint8))
    half = _JOIN_PIXELS // 2
    figures = []
    for x, y, w, h, _ in stats[1:count]:
        box = [int(x + half), int(y + half), int(x + w - half), int(y + h - half)]
        share = ((box[2] - box[0]) * (box[3] - box[1])) / float(width * height)
        repeats_panel = any(_compute_overlap(box, panel) >= _REPEAT_OVERLAP for panel in panels)
        if (_PANEL_MIN_SHARE <= share <= _FIGURE_MAX_SHARE and not repeats_panel
                and float(solid[box[1]:box[3], box[0]:box[2]].mean()) >= _SOLID_FIGURE_SHARE
                and _compute_covered_share(box, word_boxes) < _TEXT_FIGURE_SHARE):
            figures.append(box)

    return sorted(figures, key=lambda box: -(box[2] - box[0]) * (box[3] - box[1]))


def list_lined_up_blocks(rect: list[int],
                         blocks: list[dict]) -> list[tuple[str, dict]]:
    """The text blocks (`{"text", "box"}`) wholly beside `rect` on its rows (at least half the block's height inside the
    rect's band), each with its side. A cut of a table's first columns left its last column out, and the worker placed it
    (2026-10-04). Text above and below is not named: a slide's title and footer stand there beside every figure."""

    lined = []
    for block in blocks:
        x0, y0, x1, y1 = block["box"]
        if (min(y1, rect[3]) - max(y0, rect[1])) * 2 >= y1 - y0 and (x1 <= rect[0] or x0 >= rect[2]):
            lined.append(("left" if x1 <= rect[0] else "right", block))

    return lined


# A slide's footer stands in the frame's bottom band: a cut wider than its part took in the slide's address line and page
# number below it (DEFECTS 372); over the public runs' cuts it is the only one holding such a line outside every box
FOOTER_BAND_SHARE = 0.15
_FOOTER_LINE = re.compile(r"^(\d{1,3}|(https?://|www\.)\S+)$")


# How far above a slide's footer the rectangle the footer warning names ends
FOOTER_GAP = 3


def list_footer_lines(rect: list[int],
                      words: list[dict],
                      height: int,
                      boxes: list[list[int]]) -> list[dict]:
    """The text lines wholly inside `rect` in the frame's bottom FOOTER_BAND_SHARE, inside none of `boxes`, that are a
    web address alone or a page number alone: a slide's footer the cut took in below its part."""

    footer = []
    for line in _group_lines(words):
        x0, y0, x1, y1 = line["box"]
        is_inside = x0 >= rect[0] and y0 >= rect[1] and x1 <= rect[2] and y1 <= rect[3]
        in_box = any(x0 >= b[0] and y0 >= b[1] and x1 <= b[2] and y1 <= b[3] for b in boxes)
        if is_inside and not in_box and y0 >= height * (1 - FOOTER_BAND_SHARE) and _FOOTER_LINE.match(line["text"].strip()):
            footer.append(line)

    return footer


def build_rect_above_footer(rect: list[int],
                            footer: list[dict]) -> list[int]:
    """`rect` with its bottom edge `FOOTER_GAP` pixels above the highest of the `footer` lines, which it leaves out."""

    return [rect[0], rect[1], rect[2], max(rect[1] + 1, min(line["box"][1] for line in footer) - FOOTER_GAP)]


def list_lines_cut_at_sides(rect: list[int],
                            words: list[dict]) -> list[tuple[str, dict]]:
    """The text lines `rect`'s left or right edge runs through (part of the line inside, part outside), on the rect's
    rows, each with its side: a cut's left edge ran through a code line and showed "else:" as "e:", unwarned."""

    crossed = []
    for line in _group_lines(words):
        x0, y0, x1, y1 = line["box"]
        if (min(y1, rect[3]) - max(y0, rect[1])) * 2 < y1 - y0:
            continue
        if x0 < rect[0] < x1 and not _is_in_pane_gap(line, rect[0]):
            crossed.append(("left", line))
        elif x0 < rect[2] < x1 and not _is_in_pane_gap(line, rect[2]):
            crossed.append(("right", line))

    return crossed


def _is_in_pane_gap(line: dict,
                    x: int) -> bool:
    """Whether column `x` falls between two words of `line` standing over PANE_GAP_HEIGHTS of its heights apart: the
    reader read a sidebar's entry and the content row beside it as one line, and every left edge between them was
    refused as cutting that line, 20 cuts running (a meeting's frame, gaps of 26 to 80 heights)."""

    gap = PANE_GAP_HEIGHTS * max(line["box"][3] - line["box"][1], 1)
    left = [word["box"][2] for word in line["words"] if word["box"][2] <= x]
    right = [word["box"][0] for word in line["words"] if word["box"][0] >= x]

    return bool(left and right) and min(right) - max(left) > gap


# A chart is found by its axes: a vertical and a horizontal line, each a fifth of the frame long (40 pixels at least),
# meeting at their lower-left corner within 4 pixels, off the frame's border. The chart is the axes widened by each
# piece of ink (marks 5 pixels apart joined) that runs into them, an arrow and the label at its end, and by each piece
# lying wholly within 8% of the axes' size under or left of them, tick labels and axis titles; a piece over 1.5 times the
# axes' size is a panel round them and widens nothing. Over 79 placed cuts of the public runs, 10 on a frame with axes,
# a cut holding half a chart's axes and not the whole chart named exactly froscon 3143's two: a scatter plot's arrow
# and label cut at the top, a benchmark chart cut at its x axis with its values and title (DEFECTS 261)
_CHART_INK_SPREAD = 40
_CHART_AXIS_SHARE = 5
_CHART_AXIS_MIN_PIXELS = 40
_CHART_CORNER_PIXELS = 4
_CHART_JOIN_PIXELS = 5
_CHART_LABEL_SHARE = 0.08
_CHART_PANEL_SHARE = 1.5
_CHART_WIDE_SHARE = 0.95
_CHART_HELD_SHARE = 0.5
_CHART_SLACK_PIXELS = 2
# A chart running past a cut by this much or less is held whole: a flow chart cut whole by its own box ran 1 px past the
# cut's top and bottom, at the frame's edge, and the warning sent the writer to undo a right cut (38c3-fault-injection
# cut-002, DEFECTS 648); over every run's cuts the warning's charts ran past by 3, then 12, 14, 48 and 48 px
_CHART_CUT_SLACK_PIXELS = 6


def _list_long_lines(ink: numpy.ndarray,
                     kernel: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(opened)

    return [tuple(int(value) for value in stats[index][:4]) for index in range(1, count)]


def _build_chart_box(axes: tuple[int, int, int, int],
                pieces: list[tuple[int, int, int, int]]) -> list[int]:
    left, top, right, bottom = axes
    below, beside = int((bottom - top) * _CHART_LABEL_SHARE), int((right - left) * _CHART_LABEL_SHARE)
    chart = list(axes)
    for x, y, width, height in pieces:
        if width > (right - left) * _CHART_PANEL_SHARE or height > (bottom - top) * _CHART_PANEL_SHARE:
            continue
        runs_in = x < right and x + width > left and y < bottom and y + height > top
        labels = (x >= left - beside and x < right and y + height > top and y + height <= bottom + below
                  and (y >= bottom or x + width <= left))
        if runs_in or labels:
            chart = [min(chart[0], x), min(chart[1], y), max(chart[2], x + width), max(chart[3], y + height)]

    return chart


def find_chart_boxes(gray: numpy.ndarray) -> list[tuple[list[int], list[int]]]:
    """Each chart of a frame as its axes' box and its whole box, labels and arrows included, in the frame's pixels."""

    height, width = gray.shape
    ink = (numpy.abs(gray.astype(int) - int(numpy.median(gray))) > _CHART_INK_SPREAD).astype(numpy.uint8)
    across = _list_long_lines(ink, (max(_CHART_AXIS_MIN_PIXELS, width // _CHART_AXIS_SHARE), 1))
    down = _list_long_lines(ink, (1, max(_CHART_AXIS_MIN_PIXELS, height // _CHART_AXIS_SHARE)))
    joined = cv2.dilate(ink, numpy.ones((_CHART_JOIN_PIXELS, _CHART_JOIN_PIXELS), numpy.uint8))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(joined)
    pieces = [tuple(int(value) for value in stats[index][:4]) for index in range(1, count)]
    charts = []
    for vx, vy, _vw, vh in down:
        for hx, hy, hw, hh in across:
            if (abs(vx - hx) <= _CHART_CORNER_PIXELS and abs(vy + vh - (hy + hh)) <= _CHART_CORNER_PIXELS
                    and hw < width * _CHART_WIDE_SHARE and vx > _CHART_SLACK_PIXELS
                    and hy + hh < height - _CHART_SLACK_PIXELS):
                axes = (vx, vy, hx + hw, hy + hh)
                charts.append((list(axes), _draw_back_flat_sides(gray, list(axes), _build_chart_box(axes, pieces))))

    return charts


# A chart's box side at the frame's edge is drawn back over columns or rows this flat (this share within
# _CHART_FLAT_LEVELS of the line's median): frame 104's box ran through a slide's dark margin to the edge, 98% flat in
# its last 40 columns, where the other public boxes at an edge were 7% to 68% (DEFECTS 390)
_CHART_FLAT_SHARE = 0.9
_CHART_FLAT_LEVELS = 8


def _is_flat_line(line: numpy.ndarray) -> bool:
    values = line.astype(numpy.int16)

    return bool(values.size) and float(numpy.mean(numpy.abs(values - numpy.median(values)) <= _CHART_FLAT_LEVELS)) >= _CHART_FLAT_SHARE


# A slide's photo or screen beside a flat margin of another grey (a dark band holding the page number) has no outline and
# no box: the worker cut the whole frame, margin and page number with it (DEFECTS 375). Each side is drawn back over
# flat lines of the grey its edge line has, starting within `_MARGIN_EDGE_SKIP` pixels of the edge (its outermost line
# can be a stripe of the scaling), and kept only when that band is `_MARGIN_BAND_STEP` grey levels off the strip just
# inside it, a slide's own empty background being flat too. Over 118 public looked-at frames it took 4% or more off a
# side of 20, each looked at and right
_MARGIN_EDGE_SKIP = 3
_MARGIN_BAND_LEVELS = 12
_MARGIN_BAND_STEP = 30
_MARGIN_MIN_SHARE = 0.04


def _walk_back(lines: Callable[[int], numpy.ndarray],
               count: int) -> int:
    """How many lines from the edge, `lines(0)` the outermost, form one flat band of the edge's grey; 0 when none."""

    start = next((step for step in range(_MARGIN_EDGE_SKIP + 1) if _is_flat_line(lines(step))), None)
    if start is None:
        return 0
    edge = float(numpy.median(lines(start)))
    step = start
    while (step + 1 < count // 2 and _is_flat_line(lines(step))
           and abs(float(numpy.median(lines(step))) - edge) <= _MARGIN_BAND_LEVELS):
        step += 1

    return step if step > start else 0


def _is_band_off(outer: numpy.ndarray,
                 inner: numpy.ndarray) -> bool:
    """Is the band `outer` another grey than the strip `inner` just inside it?"""

    return (bool(outer.size and inner.size)
            and abs(float(numpy.median(outer)) - float(numpy.median(inner))) >= _MARGIN_BAND_STEP)


def find_content_box(gray: numpy.ndarray) -> list[int] | None:
    """The frame without a flat margin band of another grey on any side, or None when no side loses
    `_MARGIN_MIN_SHARE` of the frame."""

    height, width = gray.shape[:2]
    band = max(4, min(width, height) // 25)
    right = _walk_back(lambda step: gray[:, width - 1 - step], width)
    left = _walk_back(lambda step: gray[:, step], width)
    x0, x1 = left, width - right
    bottom = _walk_back(lambda step: gray[height - 1 - step, x0:x1], height)
    top = _walk_back(lambda step: gray[step, x0:x1], height)
    y0, y1 = top, height - bottom

    box = [x0 if _is_band_off(gray[:, :x0], gray[:, x0:x0 + band]) else 0,
           y0 if _is_band_off(gray[:y0, x0:x1], gray[y0:y0 + band, x0:x1]) else 0,
           x1 if _is_band_off(gray[:, x1:], gray[:, max(x1 - band, 0):x1]) else width,
           y1 if _is_band_off(gray[y1:, x0:x1], gray[max(y1 - band, 0):y1, x0:x1]) else height]
    sides = [box[0] / width, box[1] / height, 1 - box[2] / width, 1 - box[3] / height]

    return box if max(sides) >= _MARGIN_MIN_SHARE else None


def _draw_back_flat_sides(gray: numpy.ndarray,
                          axes: list[int],
                          box: list[int]) -> list[int]:
    """`box` with each side that touches the frame's edge drawn back over flat lines, never past the axes' far side: the
    axis line itself can run on through a dark margin the frame's median reads as ink."""

    height, width = gray.shape[:2]
    x0, y0, x1, y1 = box
    if x1 >= width - _CHART_SLACK_PIXELS:
        while x1 - 1 > axes[0] and _is_flat_line(gray[y0:y1, x1 - 1]):
            x1 -= 1
    if x0 <= _CHART_SLACK_PIXELS:
        while x0 + 1 < axes[2] and _is_flat_line(gray[y0:y1, x0]):
            x0 += 1
    if y1 >= height - _CHART_SLACK_PIXELS:
        while y1 - 1 > axes[1] and _is_flat_line(gray[y1 - 1, x0:x1]):
            y1 -= 1
    if y0 <= _CHART_SLACK_PIXELS:
        while y0 + 1 < axes[3] and _is_flat_line(gray[y0, x0:x1]):
            y0 += 1

    return [x0, y0, x1, y1]


def list_cut_charts(rect: list[int],
                    charts: list[tuple[list[int], list[int]]]) -> list[list[int]]:
    """The whole box of each chart `rect` holds half the axes of but not whole: its arrows, labels or axis values run
    past the cut's edges."""

    named = []
    for (ax0, ay0, ax1, ay1), chart in charts:
        held = max(0, min(rect[2], ax1) - max(rect[0], ax0)) * max(0, min(rect[3], ay1) - max(rect[1], ay0))
        whole = (chart[0] >= rect[0] - _CHART_CUT_SLACK_PIXELS and chart[1] >= rect[1] - _CHART_CUT_SLACK_PIXELS
                 and chart[2] <= rect[2] + _CHART_CUT_SLACK_PIXELS and chart[3] <= rect[3] + _CHART_CUT_SLACK_PIXELS)
        if held >= _CHART_HELD_SHARE * (ax1 - ax0) * (ay1 - ay0) and not whole and chart not in named:
            named.append(chart)

    return named


# An edge crossed by this many marks runs through content: over 139 edges of placed cuts in 15 public runs, 9 crossed
# 8 or more (a code line cut at a cut's top, line numbers at its left, a schematic's rotated labels at its bottom, and
# two photos' texture), every other edge at most 7 and most 0 or 1
EDGE_MARKS_LIMIT = 8
# A pixel this many grey levels off the cut's median is part of a mark
EDGE_MARK_SPREAD = 60
# A cut's margin is padding round its part, never a piece of the part beside it: a drop-down list cut by its box with a
# 12-pixel margin took in the bottom border of the search field above it (38c3-openpv cut-004, DEFECTS 629). The margin
# stops at the first straight edge it meets (a step of MARGIN_BORDER_STEP grey levels along MARGIN_BORDER_SHARE of the
# side), the line inside it kept: on frame 008 the field's border stood 1 px above the list, so no distance tells a
# neighbour's border from the part's own, and a box drawn on its outline keeps the outline
MARGIN_BORDER_STEP = 20
MARGIN_BORDER_SHARE = 0.6


def _find_border_beyond(lines: numpy.ndarray) -> int | None:
    """The number of lines from the part's edge (`lines[0]` the first line outside it, the rest going outward, each line
    the side's pixels) to the first straight edge, or None when there is none."""

    for distance in range(len(lines) - 1):
        step = numpy.abs(lines[distance].astype(numpy.int16) - lines[distance + 1].astype(numpy.int16))
        if (step >= MARGIN_BORDER_STEP).mean() >= MARGIN_BORDER_SHARE:
            return distance + 1

    return None


def stop_margin_at_borders(gray: numpy.ndarray,
                           target: list[int],
                           rect: list[int]) -> list[int]:
    """`rect`, the part `target` with its margin, each side drawn back to just inside the first straight edge its
    margin crosses past the part's own border, so the cut holds no strip of the part beside it."""

    x0, y0, x1, y1 = rect
    tx0, ty0, tx1, ty1 = (max(target[0], x0), max(target[1], y0), min(target[2], x1), min(target[3], y1))
    if tx0 >= tx1 or ty0 >= ty1:
        return rect
    top = _find_border_beyond(gray[y0:ty0, tx0:tx1][::-1])
    bottom = _find_border_beyond(gray[ty1:y1, tx0:tx1])
    left = _find_border_beyond(gray[ty0:ty1, x0:tx0].T[::-1])
    right = _find_border_beyond(gray[ty0:ty1, tx1:x1].T)

    return [x0 if left is None else tx0 - left, y0 if top is None else ty0 - top,
            x1 if right is None else tx1 + right, y1 if bottom is None else ty1 + bottom]


def list_marked_edges(gray: numpy.ndarray,
                      rect: list[int]) -> list[tuple[str, int]]:
    """Each edge of `rect` inside the frame that EDGE_MARKS_LIMIT marks or more cross, with their count: a mark is a run
    of pixels EDGE_MARK_SPREAD off the cut's median along the edge's line. The text reader misses rotated and small
    labels, so an edge through them was never named (a schematic's cut ended through its rotated pin labels)."""

    x0, y0, x1, y1 = rect
    height, width = gray.shape[:2]
    inside = gray[y0:y1, x0:x1]
    if inside.size == 0:
        return []
    background = float(numpy.median(inside))
    lines = (("top", gray[y0, x0:x1] if y0 > 0 else None), ("bottom", gray[y1 - 1, x0:x1] if y1 < height else None),
             ("left", gray[y0:y1, x0] if x0 > 0 else None), ("right", gray[y0:y1, x1 - 1] if x1 < width else None))
    marked = []
    for side, line in lines:
        if line is None or not len(line):
            continue
        # A mark stands off the line's own background too: an edge along a flat dark margin beside a bright screen had
        # its noise counted as 16 marks against the cut's median (DEFECTS 369)
        values = line.astype(numpy.int16)
        is_mark = ((numpy.abs(values - background) > EDGE_MARK_SPREAD)
                   & (numpy.abs(values - float(numpy.median(line))) > EDGE_MARK_SPREAD)).astype(numpy.int8)
        count = int(is_mark[0]) + int((numpy.diff(is_mark) == 1).sum())
        if count >= EDGE_MARKS_LIMIT:
            marked.append((side, count))

    return marked


def list_continued_rows(rect: list[int],
                        words: list[dict]) -> dict[str, list[dict]]:
    """The row of text lines just past `rect`'s bottom or top edge (`"below"`, `"above"`) that lines up with the
    columns inside it: the table or list the cut holds goes on past its edge. Each side present only when found."""

    # STEP 1: the lines wholly inside the cut give its line height and its columns' starts.
    lines = _group_lines(words)
    inside = [line for line in lines if line["box"][0] >= rect[0] and line["box"][1] >= rect[1]
              and line["box"][2] <= rect[2] and line["box"][3] <= rect[3]]
    if not inside:
        return {}
    heights = sorted(line["box"][3] - line["box"][1] for line in inside)
    height = heights[len(heights) // 2]
    starts = [line["box"][0] for line in inside]

    # STEP 2: on each side, the nearest row of lines within reach, and how many of them start where a column inside does.
    continued: dict[str, list[dict]] = {}
    across = [line for line in lines if line["box"][0] < rect[2] and line["box"][2] > rect[0]]
    for side in ("below", "above"):
        if side == "below":
            near = [line for line in across if rect[3] - 1 <= line["box"][1] <= rect[3] + _CONTINUED_GAP_HEIGHTS * height]
            centre = min((line["box"][1] + line["box"][3]) / 2 for line in near) if near else 0
        else:
            near = [line for line in across if rect[1] - _CONTINUED_GAP_HEIGHTS * height <= line["box"][3] <= rect[1] + 1]
            centre = max((line["box"][1] + line["box"][3]) / 2 for line in near) if near else 0
        row = [line for line in near if abs((line["box"][1] + line["box"][3]) / 2 - centre) <= height / 2
               and any(abs(line["box"][0] - start) <= _COLUMN_START_PIXELS for start in starts)]
        if len(row) >= _CONTINUED_MIN_CELLS:
            continued[side] = sorted(row, key=lambda line: line["box"][0])

    return continued


# A panel starting within this share of the frame's width from its left edge and ending as close to its right spans it
_FRAME_WIDE_SHARE_LEFT = 0.01


def list_cut_panels(rect: list[int],
                    panels: dict[str, list[int]],
                    frame_width: int = 0) -> list[tuple[str, str]]:
    """The panels (`{id: box}`) one of `rect`'s edges runs through, each with that edge's side: the panel starts inside
    the cut and goes on past the edge, so the cut shows part of it. A panel holding the whole cut is never named, nor
    one spanning the frame's whole width (`frame_width`), which is a band of the frame no cut takes in whole: a slide's
    upper part read as such a panel was named on three cuts of one diagram, each a right one, and the worker cut again
    (froscon talk 3143, DEFECTS 263)."""

    margin = _PANEL_CROSS_PIXELS
    crossed = []
    for panel_id, (x0, y0, x1, y1) in panels.items():
        if frame_width and x0 <= _FRAME_WIDE_SHARE_LEFT * frame_width and x1 >= (1 - _FRAME_WIDE_SHARE_LEFT) * frame_width:
            continue
        shares_rows = y0 < rect[3] and y1 > rect[1]
        shares_columns = x0 < rect[2] and x1 > rect[0]
        sides = [("right", shares_rows and rect[0] - margin <= x0 < rect[2] - margin and x1 > rect[2] + margin),
                 ("left", shares_rows and rect[0] + margin < x1 <= rect[2] + margin and x0 < rect[0] - margin),
                 ("bottom", shares_columns and rect[1] - margin <= y0 < rect[3] - margin and y1 > rect[3] + margin),
                 ("top", shares_columns and rect[1] + margin < y1 <= rect[3] + margin and y0 < rect[1] - margin)]
        crossed.extend((side, panel_id) for side, is_crossed in sides if is_crossed)

    return crossed


def map_to_recording(rect: list[int],
                     screen_box: list[int],
                     frame_width: int) -> list[int]:
    """`rect` in the stored frame's pixels, as the same region in the recording's pixels. `screen_box` is what the
    stored frame shows, in the recording's pixels."""

    scale = (screen_box[2] - screen_box[0]) / float(frame_width)

    return [screen_box[0] + round(rect[0] * scale), screen_box[1] + round(rect[1] * scale),
            screen_box[0] + round(rect[2] * scale), screen_box[1] + round(rect[3] * scale)]


def expand_rect(rect: list[int],
                margin: int) -> list[int]:
    return [rect[0] - margin, rect[1] - margin, rect[2] + margin, rect[3] + margin]


def clip_rect(rect: list[int],
              bounds: list[int]) -> list[int]:
    """`rect` held inside `bounds`."""

    return [max(rect[0], bounds[0]), max(rect[1], bounds[1]), min(rect[2], bounds[2]), min(rect[3], bounds[3])]


def list_crossed_words(rect: list[int],
                       words: list[dict]) -> list[str]:
    """The words of `words` (`{"text", "box"}`) that `rect`'s edge cuts through: partly inside, partly outside."""

    return [word["text"] for word in words if _is_word_crossed(rect, word)]


# A block of at most this many lines is taken whole when an edge runs through one of its lines: a slide's two-line title
# cut through its first line kept "Holzofen" alone (DEFECTS 239); a longer block is a paragraph, taken a line at a time
SHORT_BLOCK_LINES = 3
# and whose lines stand this many line heights apart at most
STACKED_LINE_HEIGHTS = 1.8


def _list_short_block(line: list[dict],
                      words: list[dict]) -> list[dict]:
    """The words of a crossed line's block, standing over or under the line's own span, when that block holds
    SHORT_BLOCK_LINES lines or fewer, else none: a title's other line, never the other pane of a row read across two."""

    if not line or line[0].get("block") is None:
        return []
    block = [other for other in words if other.get("block") == line[0]["block"]]
    lines = len({_read_line_key(other) for other in block})
    line_height = max(max(word["box"][3] for word in line) - min(word["box"][1] for word in line), 1)
    block_height = max(other["box"][3] for other in block) - min(other["box"][1] for other in block)
    # Its lines stacked as a title's are: three lines spread over an editor are a paragraph's, taken a line at a time
    if lines > SHORT_BLOCK_LINES or block_height > STACKED_LINE_HEIGHTS * line_height * lines:
        return []
    left, right = min(word["box"][0] for word in line), max(word["box"][2] for word in line)

    return [other for other in block if other["box"][0] < right and other["box"][2] > left]


# How far past a kept line a moved side stops: 2 pixels cleared both address lines of froscon 3147 the cut tool kept
_WHOLE_LINE_MARGIN = 3


def expand_to_whole_blocks(rect: list[int],
                           words: list[dict],
                           width: int,
                           height: int) -> list[int]:
    """`rect` grown until no edge cuts a word: each crossed word's whole line of text (`{"line"}`, Tesseract's; its
    `{"block"}` when no line is known) is taken in, since a word fully outside on the same line is lost with no word
    crossed, and its whole block when that holds `SHORT_BLOCK_LINES` lines or fewer (a title). A line, never its paragraph: on an editor or a portal one paragraph spans most of the screen, and the
    rectangle offered grew to the whole frame, which the whole-frame check then refused. Clipped to the frame."""

    grown = list(rect)
    for _ in range(len(words) + 1):
        crossed = [word for word in words if _is_word_crossed(grown, word) and _read_line_key(word) is not None]
        if not crossed:
            break
        for word in [kept for word in crossed for kept in _list_neighbours(word, words)
                     + _list_short_block(_list_neighbours(word, words), words)]:
            x0, y0, x1, y1 = word["box"]
            grown = [min(grown[0], x0), min(grown[1], y0), max(grown[2], x1), max(grown[3], y1)]
        grown = clip_rect(grown, [0, 0, width, height])
    # A side moved out stops a few pixels past the line, not on its box: on the box its own row is the letters' last
    # row, and the placed cut was refused as running through them (froscon 3147's address lines, DEFECTS 395)
    grown = [grown[0] - _WHOLE_LINE_MARGIN if grown[0] < rect[0] else grown[0],
             grown[1] - _WHOLE_LINE_MARGIN if grown[1] < rect[1] else grown[1],
             grown[2] + _WHOLE_LINE_MARGIN if grown[2] > rect[2] else grown[2],
             grown[3] + _WHOLE_LINE_MARGIN if grown[3] > rect[3] else grown[3]]

    return clip_rect(grown, [0, 0, width, height])


def is_centre_inside(box: list[int],
                     rect: list[int]) -> bool:
    """Whether the centre of `box` lies inside `rect`."""

    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def draw_mark(frame: numpy.ndarray,
              rect: list[int],
              number: int | None) -> numpy.ndarray:
    """A copy of `frame` with a box drawn around `rect`, and a numbered disc at its top left when `number` is given."""

    drawn = frame.copy()
    cv2.rectangle(drawn, (rect[0], rect[1]), (rect[2] - 1, rect[3] - 1), _MARK_COLOUR, 3)
    if number is not None:
        centre = (max(rect[0], 14), max(rect[1], 14))
        cv2.circle(drawn, centre, 13, _MARK_COLOUR, -1)
        cv2.putText(drawn, str(number), (centre[0] - 6, centre[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    return drawn


def hide_region(frame: numpy.ndarray,
                rect: list[int]) -> numpy.ndarray:
    """A copy of `frame` with `rect` filled black: a secret or a person's data hidden before the cut is used."""

    drawn = frame.copy()
    drawn[max(rect[1], 0):rect[3], max(rect[0], 0):rect[2]] = 0

    return drawn


# A figure whose masks hide this share of it shows too little to place: the cuts a reader called useless were masked
# 24% to 74%, every useful one 8% or less (2026-10-01)
MOST_MASKED_SHARE = 0.20


def compute_masked_share(width: int,
                         height: int,
                         rects: list[list[int]]) -> float:
    """The share of a `width` x `height` image the rectangles cover together, overlaps counted once."""

    covered = numpy.zeros((max(height, 1), max(width, 1)), dtype=bool)
    for x0, y0, x1, y1 in rects:
        covered[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = True

    return float(covered.mean())


# Two figures whose content differs in less than this share of its pixels show the same thing: two cuts of one view a
# second apart differed in 7%, the closest two different screens in 22% (2026-10-01)
REPEATED_FIGURE_SHARE = 0.12
# The share for two figures that also read alike: a cut moved by 2 pixels differs in 14% to 15% of its content pixels,
# a current and a target diagram with the same labels in 48% to 55% (2026-10-04)
REPEATED_SAME_WORDS_SHARE = 0.3


def compute_content_difference(first: numpy.ndarray,
                               second: numpy.ndarray) -> float | None:
    """The share of the content pixels (those apart from the shared background) that differ between two images, compared
    at 256 pixels wide; None when their shapes differ by more than a tenth, which no repeat does."""

    first_ratio, second_ratio = first.shape[1] / first.shape[0], second.shape[1] / second.shape[0]
    if abs(first_ratio - second_ratio) / max(first_ratio, second_ratio) > _SHAPE_TOLERANCE:
        return None
    size = (_COMPARE_WIDTH, max(1, round(_COMPARE_WIDTH / first_ratio)))
    grey = [cv2.resize(_to_grey(image), size,
                       interpolation=cv2.INTER_AREA).astype(int) for image in (first, second)]
    background = numpy.median(numpy.concatenate([grey[0].ravel(), grey[1].ravel()]))
    content = (numpy.abs(grey[0] - background) > _CONTENT_LEVELS) | (numpy.abs(grey[1] - background) > _CONTENT_LEVELS)
    if not content.any():
        return 0.0

    return float((numpy.abs(grey[0] - grey[1]) > _DIFFERENT_LEVELS)[content].mean())


# A scrolled copy keeps at least this share of the shorter image's rows in view
_SCROLL_OVERLAP = 0.6


# A cut lying inside another, at the same scale, matches it this well: over 295 pairs of placed cuts in 15 public runs,
# the two that were one figure placed twice (a schematic's relay cut beside a cut of the whole frame; a slide's right
# half beside the whole slide) matched 0.986 and 0.993, every other pair at most 0.84
CONTAINED_MATCH = 0.95
# Neither side of the smaller cut under this many pixels: a sliver matches anywhere
CONTAINED_MIN_PIXELS = 20


def is_contained(image: numpy.ndarray,
                 other: numpy.ndarray) -> bool:
    """Whether the smaller of two cuts, by area, is a part of the larger at the same scale: it fits inside it and is
    found there with a normalised match of CONTAINED_MATCH or more. Two cuts of one schematic, one inside the other,
    read too few words for the word test and were placed as two figures (DEFECTS 225)."""

    first = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    second = cv2.cvtColor(other, cv2.COLOR_BGR2GRAY) if other.ndim == 3 else other
    small, large = (first, second) if first.size <= second.size else (second, first)
    if (small.shape[0] > large.shape[0] or small.shape[1] > large.shape[1]
            or min(small.shape[:2]) < CONTAINED_MIN_PIXELS):
        return False
    _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(large, small, cv2.TM_CCOEFF_NORMED))

    return score >= CONTAINED_MATCH


def compute_scrolled_difference(first: numpy.ndarray,
                                second: numpy.ndarray) -> float | None:
    """The least share of content pixels that differ between two images of one width when one is moved up or down
    against the other, over the rows both show (at least _SCROLL_OVERLAP of the shorter), compared at 256 pixels wide;
    None when their widths differ by more than a tenth. One table cut from two frames scrolled a row apart differed in
    71% of its pixels unmoved (2026-10-10)."""

    if abs(first.shape[1] - second.shape[1]) / max(first.shape[1], second.shape[1]) > _SHAPE_TOLERANCE:
        return None
    grey = [cv2.resize(_to_grey(image), (_COMPARE_WIDTH, max(1, round(image.shape[0] * _COMPARE_WIDTH / image.shape[1]))),
                       interpolation=cv2.INTER_AREA).astype(int) for image in (first, second)]
    background = numpy.median(numpy.concatenate([grey[0].ravel(), grey[1].ravel()]))
    least_rows = round(min(grey[0].shape[0], grey[1].shape[0]) * _SCROLL_OVERLAP)
    best = None
    for shift in range(-grey[1].shape[0] + least_rows, grey[0].shape[0] - least_rows + 1):
        # Rows of the first from `shift` on lie over the second's rows from 0 on (a negative shift the other way round)
        top_first, top_second = max(shift, 0), max(-shift, 0)
        rows = min(grey[0].shape[0] - top_first, grey[1].shape[0] - top_second)
        if rows < least_rows:
            continue
        one, two = grey[0][top_first:top_first + rows], grey[1][top_second:top_second + rows]
        content = (numpy.abs(one - background) > _CONTENT_LEVELS) | (numpy.abs(two - background) > _CONTENT_LEVELS)
        if not content.any():
            continue
        difference = float((numpy.abs(one - two) > _DIFFERENT_LEVELS)[content].mean())
        best = difference if best is None else min(best, difference)

    return best


# How far right of a word a mask may start and still hide the value after it on its line (a space or two)
_VALUE_GAP = 30


def is_followed_by_mask(box: list[int],
                        masked_rects: list[list[int]]) -> bool:
    """Whether a mask starts just right of this word on its row: a label's value there is hidden."""

    return any(rect[1] < box[3] and box[1] < rect[3] and box[2] - 2 <= rect[0] <= box[2] + _VALUE_GAP
               for rect in masked_rects)


def holds_text_outside(panel: list[int],
                       rect: list[int],
                       words: list[dict]) -> bool:
    """Whether a word stands in the panel's part that `rect` leaves out: only then does a cut through it lose anything."""

    return any(is_centre_inside(word["box"], panel) and not is_centre_inside(word["box"], rect) for word in words)


def is_label_value_masked(words: list[dict],
                          index: int,
                          masked_rects: list[list[int]],
                          is_scheme: Callable[[str], bool]) -> bool:
    """Whether a mask hides the value after the label at `index`: right after it, or after one scheme word between
    ("Authorization: Bearer <masked>")."""

    if is_followed_by_mask(words[index]["box"], masked_rects):
        return True
    following = words[index + 1] if index + 1 < len(words) else None

    return bool(following and is_scheme(following["text"]) and is_followed_by_mask(following["box"], masked_rects))
