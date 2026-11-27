"""How much the shared screen changed between two seconds, as plain code over pixels. The screen is scaled to 320 x 180
and compared in 32 x 18 blocks of 10 pixels; a block changed when a few of its pixels moved a lot, which a changed
character does and compression noise does not.

Measured in `research/frame-change/` on four recordings of slides, code, web pages and PDFs.
"""

from __future__ import annotations

import math

import cv2
import numpy

from models import screen_box
from utils import frame_region_utils

__all__ = ["SAMPLE_WIDTH", "SAMPLE_HEIGHT", "BLOCK_ROWS", "BLOCK_COLUMNS", "SHIFT_PX", "STILL_LIMIT", "REPEAT_LIMIT",
           "NEW_VIEW_CHANGE", "VIEW_END_CHANGE", "BUILD_CONTENT_GONE_LIMIT", "is_scroll_of", "shrink_screen",
           "map_changed_blocks", "shrink_screen_fine", "map_fine_changed_blocks", "compute_change", "find_active_blocks",
           "update_unhidden_view", "compare_behind_person", "map_box_blocks", "choose_shift_tolerance",
           "map_empty_blocks", "compute_content_gone", "is_addition"]

SAMPLE_WIDTH = 320                          # every second's screen is scaled to this size to compare
SAMPLE_HEIGHT = 180
BLOCK_PX = 10                               # 32 x 18 blocks
BLOCK_ROWS = SAMPLE_HEIGHT // BLOCK_PX
BLOCK_COLUMNS = SAMPLE_WIDTH // BLOCK_PX
PIXEL_CHANGE = 40                           # grey levels a pixel must move to count as changed
# A pixel matched this near in the other image has not moved: between samples of two boxes, always between two fine
# samples of a view's last second, and between a removed frame and the frame said to hold it
SHIFT_PX = 1
BLOCK_MIN_PIXELS = 3                        # a block changed when at least this many of its pixels did
STILL_LIMIT = 0.005                         # a second is still when under this share of blocks changed: a moving cursor stays under it
ACTIVE_MIN_SECONDS = 10                     # blocks are judged always-moving only once this many seconds are seen
ACTIVE_RATE = 0.6                           # a block changing in more of the recent seconds than this is ignored:
                                            # a camera floating over the content, a playing video
REPEAT_LIMIT = 0.01                         # a capture this close to an earlier one is that one shown again
FLAT_BLOCK_STD = 6                          # a block whose grey levels spread less than this is empty background
# A frame taken after this share of the blocks changed shows a new view (a room camera zoomed or panned): the screen
# remembered as seen with nobody in front belongs to the old one, and compared with it a lecturer's walk took a frame
NEW_VIEW_CHANGE = 0.5
# A view's last second is compared with its last frame taken at this size, where thin chalk survives: a lecture's few
# new words on a board in close-up changed 0% of the 320 px blocks and 13 of 534 blocks (2.4%) outside the lecturer
# here, the lecturer's motion alone 0 to 2 of them on most seconds
FINE_WIDTH, FINE_HEIGHT = 960, 540
VIEW_END_CHANGE = 0.02                      # the last second of a view is taken when this share of it changed
ADDITION_FLAT_SHARE = 0.95                  # a capture whose changed blocks were this empty before only adds to it
# A capture that empties this share of the blocks holding content before it took the slide away: no build step. Over
# 895 build steps of 15 runs the most was 60.5%; a section title replacing two code panes emptied 90.2%, at a change of
# 14.4% (the panes' fill is 22 grey levels off the background, under PIXEL_CHANGE)
BUILD_CONTENT_GONE_LIMIT = 0.75


# A frame that is the frame before it scrolled: over the longest band of rows that differ between them (a row differs when over
# SCROLL_ROW_CHANGE of its pixels moved PIXEL_CHANGE grey levels), at least SCROLL_MIN_BAND of the height, the two match
# moved up or down against each other but for under SCROLL_STEP_LIMIT of their content. A board's list scrolled by a
# few rows under its still header matched but for 11%, a build step 18%, two different screens 60% to 85%; marked a new
# screen, the scrolled list was written "same as" the frame before and refused
SCROLL_ROW_CHANGE = 0.02
SCROLL_MIN_BAND = 0.2
SCROLL_STEP_LIMIT = 0.3
SCROLL_ROW_GAP = 0.05                       # still rows up to this share of the height inside the band are joined
# ... and moved, they match far better than unmoved: two unlike screens of close grey levels matched about as badly
# either way, and one slide was marked a scroll of another
SCROLL_GAIN = 0.5


def _find_longest_true_run(flags: numpy.ndarray) -> tuple[int, int] | None:
    """The longest run of True as (start, end), end exclusive, or None when there is none."""

    padded = numpy.concatenate([[False], flags, [False]]).astype(numpy.int8)
    starts, ends = numpy.flatnonzero(numpy.diff(padded) == 1), numpy.flatnonzero(numpy.diff(padded) == -1)
    if starts.size == 0:
        return None
    longest = int(numpy.argmax(ends - starts))

    return int(starts[longest]), int(ends[longest])


# A page scrolled beside parts that stay still (a page's menu, a browser's bars, a presenter's camera) is found by strips
# of its middle columns, each found again in the frame after it moved up or down: three strips across the height, from
# SCROLL_STRIP_COLUMNS of the width, each SCROLL_STRIP_HEIGHT high. It is a scroll when at least two match at
# SCROLL_STRIP_MATCH or better, every match that good agrees on the shift within SCROLL_STRIP_AGREE of the height, and
# the shift is at least SCROLL_STRIP_LEAST of it. Compared whole, a documentation page scrolled 40 to 265 pixels beside
# its still menu and camera matched no better moved than unmoved, and four frames of it were blocked as "same as" the
# first (Lecture 9); over 54 pairs the strips alone took, slide changes on one backdrop each gave a strip matching at
# shift 0 and were refused, and every pair taken was a page, console or terminal scrolled (looked at)
SCROLL_STRIP_TOPS = (0.35, 0.55, 0.75)
SCROLL_STRIP_HEIGHT = 0.12
SCROLL_STRIP_COLUMNS = (0.2, 0.68)
SCROLL_STRIP_MATCH = 0.9
SCROLL_STRIP_AGREE = 0.006
SCROLL_STRIP_LEAST = 0.03
SCROLL_STRIP_MIN_SPREAD = 10                # grey levels a strip must spread over to hold content worth matching


def _is_strip_scroll(before: numpy.ndarray,
                     after: numpy.ndarray) -> bool:
    """Whether strips of `before`'s middle columns are found in `after` moved up or down by one shift, per the
    SCROLL_STRIP_ rules above."""

    height, width = before.shape
    left, right = (round(share * width) for share in SCROLL_STRIP_COLUMNS)
    shifts = []
    for top_share in SCROLL_STRIP_TOPS:
        top, bottom = round(top_share * height), round((top_share + SCROLL_STRIP_HEIGHT) * height)
        strip = before[top:bottom, left:right]
        if strip.std() < SCROLL_STRIP_MIN_SPREAD:
            continue
        _, score, _, place = cv2.minMaxLoc(cv2.matchTemplate(after[:, left:right], strip, cv2.TM_CCOEFF_NORMED))
        if score >= SCROLL_STRIP_MATCH:
            shifts.append(top - place[1])
    if len(shifts) < 2 or max(shifts) - min(shifts) > SCROLL_STRIP_AGREE * height:
        return False

    return abs(float(numpy.median(shifts))) >= SCROLL_STRIP_LEAST * height


def is_scroll_of(before: numpy.ndarray,
                 after: numpy.ndarray) -> bool:
    """Whether grey frame `after` is grey frame `before` scrolled: the band of rows that differ matches once moved, or
    strips of its middle columns are found moved by one shift (`_is_strip_scroll`)."""

    if before.shape != after.shape:
        return False
    is_changed = numpy.abs(before.astype(numpy.int16) - after.astype(numpy.int16)) > PIXEL_CHANGE
    is_row_changed = (is_changed.mean(axis=1) > SCROLL_ROW_CHANGE).astype(numpy.uint8)
    # The longest run of changed rows, the blank lines between a list's rows joined: from the first changed row to the
    # last, a browser tab's title near the top took in the whole still header, and moved matched no better than unmoved
    gap = max(1, round(SCROLL_ROW_GAP * before.shape[0]))
    joined = cv2.morphologyEx(is_row_changed[:, None], cv2.MORPH_CLOSE, numpy.ones((gap, 1), numpy.uint8))[:, 0]
    band = _find_longest_true_run(joined.astype(bool))
    if band is None or band[1] - band[0] < SCROLL_MIN_BAND * before.shape[0]:
        return False
    band_before, band_after = before[band[0]:band[1]], after[band[0]:band[1]]
    moved = frame_region_utils.compute_scrolled_difference(band_before, band_after)
    unmoved = frame_region_utils.compute_content_difference(band_before, band_after)
    if (moved is not None and unmoved is not None and moved < SCROLL_STEP_LIMIT
            and moved < SCROLL_GAIN * unmoved):
        return True

    return _is_strip_scroll(before, after)


def shrink_screen(gray: numpy.ndarray,
                  box: screen_box.Box) -> numpy.ndarray:
    """The shared screen cut out of a grey frame and scaled to the size every comparison uses."""

    x0, y0, x1, y1 = box
    sample = cv2.resize(gray[y0:y1, x0:x1], (SAMPLE_WIDTH, SAMPLE_HEIGHT), interpolation=cv2.INTER_AREA)

    return sample


def _map_moved_pixels(first: numpy.ndarray,
                      second: numpy.ndarray,
                      shift_px: int) -> numpy.ndarray:
    """Per pixel, whether it moved by more than PIXEL_CHANGE; with `shift_px`, only when no pixel within that many
    pixels of it in the other sample is that close to it, either way."""

    first, second = first.astype(numpy.int16), second.astype(numpy.int16)
    if not shift_px:
        return numpy.abs(first - second) > PIXEL_CHANGE
    near = numpy.ones((2 * shift_px + 1, 2 * shift_px + 1), numpy.uint8)
    moved = numpy.zeros(first.shape, bool)
    for one, other in ((first, second), (second, first)):
        moved |= one > cv2.dilate(other, near) + PIXEL_CHANGE
        moved |= one < cv2.erode(other, near) - PIXEL_CHANGE

    return moved


def _count_moved_per_block(moved: numpy.ndarray) -> numpy.ndarray:
    """Per block of the BLOCK_ROWS x BLOCK_COLUMNS grid, how many of its pixels moved, the block's size read from the
    map's own size: BLOCK_PX at the sample size, larger at FINE_WIDTH."""

    block_height_px, block_width_px = moved.shape[-2] // BLOCK_ROWS, moved.shape[-1] // BLOCK_COLUMNS
    shape = moved.shape[:-2] + (BLOCK_ROWS, block_height_px, BLOCK_COLUMNS, block_width_px)

    return moved.reshape(shape).sum(axis=(-3, -1))


def map_changed_blocks(first: numpy.ndarray,
                       second: numpy.ndarray,
                       shift_px: int = 0) -> numpy.ndarray:
    """Per block, whether at least BLOCK_MIN_PIXELS of its pixels moved by more than PIXEL_CHANGE. With `shift_px`, a
    pixel moved only when no pixel within that many sample pixels of it in the other sample is that close to it, either
    way: for two samples cut with screen boxes a pixel or two apart (`choose_shift_tolerance`), where one slide compared pixel by
    pixel changed 18% of its blocks, a new slide by that measure; a character drawn or taken away still leaves pixels
    nothing near them matches."""

    return _count_moved_per_block(_map_moved_pixels(first, second, shift_px)) >= BLOCK_MIN_PIXELS


def shrink_screen_fine(gray: numpy.ndarray,
                       box: screen_box.Box | list[int]) -> numpy.ndarray:
    """The shared screen cut out of a grey frame at FINE_WIDTH x FINE_HEIGHT, for comparing a view's last second."""

    x0, y0, x1, y1 = box

    return cv2.resize(gray[y0:y1, x0:x1], (FINE_WIDTH, FINE_HEIGHT), interpolation=cv2.INTER_AREA)


def map_fine_changed_blocks(first: numpy.ndarray,
                            second: numpy.ndarray) -> numpy.ndarray:
    """Per block of the same grid, whether at least BLOCK_MIN_PIXELS of two fine samples' pixels moved by more than
    PIXEL_CHANGE, a pixel matched within SHIFT_PX in the other sample counted as not moved."""

    return _count_moved_per_block(_map_moved_pixels(first, second, SHIFT_PX)) >= BLOCK_MIN_PIXELS


def compute_change(changed: numpy.ndarray,
                   ignored: numpy.ndarray) -> float:
    """The share of the blocks not ignored that changed."""

    counted = ~ignored
    share = float((changed & counted).sum() / max(int(counted.sum()), 1))

    return share


def find_active_blocks(recent_changes: list[numpy.ndarray]) -> numpy.ndarray:
    """Blocks that changed in more than ACTIVE_RATE of the recent seconds, once at least ACTIVE_MIN_SECONDS are known."""

    if len(recent_changes) < ACTIVE_MIN_SECONDS:
        return numpy.zeros((BLOCK_ROWS, BLOCK_COLUMNS), bool)
    active = numpy.mean(recent_changes, axis=0) > ACTIVE_RATE

    return active


def update_unhidden_view(view: numpy.ndarray | None,
                         seen: numpy.ndarray | None,
                         sample: numpy.ndarray,
                         covered: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The screen as last seen with nobody in front of it, block by block, and which blocks have been seen so: `view`
    and `seen` (None for a new screen box) with `sample`'s blocks that no person `covered` copied in."""

    view = sample.copy() if view is None else view.copy()
    seen = numpy.zeros((BLOCK_ROWS, BLOCK_COLUMNS), bool) if seen is None else seen.copy()
    pixels = numpy.kron(~covered, numpy.ones((BLOCK_PX, BLOCK_PX), bool)).astype(bool)
    view[pixels] = sample[pixels]

    return view, seen | ~covered


def compare_behind_person(changed: numpy.ndarray,
                          sample: numpy.ndarray,
                          behind: numpy.ndarray,
                          view: numpy.ndarray | None,
                          seen: numpy.ndarray | None) -> tuple[numpy.ndarray, numpy.ndarray]:
    """`changed` with each block in `behind` (a person stood there on the last frame taken and no longer does) judged
    against the screen as last seen with nobody in front of it, where that is known, and the blocks still left out:
    those of `behind` never seen unhidden. Compared with the last frame taken, such a block changes whenever the person
    leaves it, so new writing done there was left out with the move (DEFECTS 113)."""

    if view is None or seen is None:
        return changed, behind
    known = behind & seen
    judged = (changed & ~known) | (known & map_changed_blocks(sample, view))

    return judged, behind & ~seen


def map_box_blocks(covering_boxes: list[screen_box.Box],
                   box: screen_box.Box | list[int]) -> numpy.ndarray:
    """Per block of a sample of the shared screen in `box`, whether one of `covering_boxes` (x0, y0, x1, y1 in the
    frame's pixels) reaches into it."""

    covered = numpy.zeros((BLOCK_ROWS, BLOCK_COLUMNS), bool)
    x0, y0, x1, y1 = box
    for left, top, right, bottom in covering_boxes:
        columns = (max(left, x0) - x0) * BLOCK_COLUMNS / (x1 - x0), (min(right, x1) - x0) * BLOCK_COLUMNS / (x1 - x0)
        rows = (max(top, y0) - y0) * BLOCK_ROWS / (y1 - y0), (min(bottom, y1) - y0) * BLOCK_ROWS / (y1 - y0)
        if columns[1] <= columns[0] or rows[1] <= rows[0]:
            continue
        covered[int(rows[0]):math.ceil(rows[1]), int(columns[0]):math.ceil(columns[1])] = True

    return covered


def choose_shift_tolerance(box: screen_box.Box | list[int],
                           other_box: screen_box.Box | list[int]) -> int:
    """How far apart two samples may match a pixel: SHIFT_PX when they were cut with different screen boxes, 0 when
    with the same box. Within one box the comparison stays exact: tolerant there, a camera grid beside the share read
    as still sooner and a frame was taken while the box held the whole call window."""

    return SHIFT_PX if list(box) != list(other_box) else 0


def map_empty_blocks(sample: numpy.ndarray) -> numpy.ndarray:
    """Which blocks of a sample are empty background: their grey levels spread less than FLAT_BLOCK_STD."""

    return sample.reshape(BLOCK_ROWS, BLOCK_PX, BLOCK_COLUMNS, BLOCK_PX).std(axis=(1, 3)) < FLAT_BLOCK_STD


def compute_content_gone(before: numpy.ndarray,
                         after: numpy.ndarray) -> float:
    """The share of the blocks holding content on `before` that are empty background on `after`."""

    held = ~map_empty_blocks(before)
    if not held.any():
        return 0.0

    return float((held & map_empty_blocks(after)).sum() / held.sum())


def is_addition(before: numpy.ndarray,
                after: numpy.ndarray,
                shift_px: int = 0) -> bool:
    """Whether `after` only adds to `before`: every block that changed was empty background before (a picture, a box or
    a text placed on a slide's free space), so nothing on it was replaced. Measured on one recording's 109 pairs of
    consecutive frames: a slide gaining a large picture (21.5% of its blocks) and a known build step were all empty
    before; every other pair, demo screens included, at most 83%."""

    changed = map_changed_blocks(before, after, shift_px)
    if not changed.any():
        return False
    was_empty = map_empty_blocks(before)

    return bool(was_empty[changed].mean() >= ADDITION_FLAT_SHARE)
