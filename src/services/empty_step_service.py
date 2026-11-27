"""A build step that adds nothing to the frame before it (DEFECTS 150).

A frame is a build step when it changed the frame before it by at most `frames.buildStepChange`, which a drawn hand
moved over the same screen does too, and so does the same slide zoomed, a spotlight moved over a code listing or a
lecturer stepping in front of the board: no person detector sees a drawing, and the model filters kept those frames.
A step that only adds puts its marks on free space, or shows words the frame before lacks; one whose changed blocks
were nearly all content before and which shows no new word replaced what was there with nothing to read. Over the 306
build steps of 14 public runs, 14 are such steps, and every one opened held nothing the frame before lacks; a single
new word of four letters or more (a hex value typed, `int(3.9`) is a real change and keeps the frame.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import cv2
import numpy

from services import apple_text_service
from services import frame_extraction_service
from services import text_detection_service
from utils import change_capture_utils

__all__ = ["is_empty_step", "is_moved_copy", "is_pointer_copy", "is_replacing_step", "is_resized_copy", "is_unchanged_copy", "is_view_copy", "list_empty_steps",
           "list_changed_places", "list_moved_copies", "list_passed_build_starts", "list_replacing_steps"]

# At most this share of the blocks a step changed were empty background on the frame before (frame 49 of a closing
# ceremony's drawn hand: 17%; a step adding to a slide's free space: 70% or more)
_MOST_EMPTY_SHARE = 0.2
# A word the frame before lacks counts from this many letters: a misread fragment ("rur") is shorter
_MIN_NEW_WORD_LETTERS = 4
_PUNCTUATION = ".,:;!?()[]\"'"
# A step that is the frame before moved or scaled (a broadcast switching layouts): at least this many of the 576 blocks
# changed as they stand, and at most `_MOST_LINED_UP_BLOCKS` once the step is lined up on the frame before. Over the 325
# build steps of 15 public runs, the 11 pairs meeting all four tests were each one slide moved (168 to 366 blocks as
# they stand, 0 to 43 lined up); code typed in an editor and a scrolled console changed under 70 either way
_LEAST_MOVED_BLOCKS = 150
# A scroll by one line of a terminal changes fewer blocks: froscon talk 3211's frame 58, frame 57 one line up, 109
# (DEFECTS 215); the band check then judges it
_LEAST_SCROLLED_BLOCKS = 30
_MOST_LINED_UP_BLOCKS = 45
# The step's words of four letters or more the frame before lacks, a misread letter allowed: a slide read at another
# scale misreads one or two
_MOST_UNMATCHED_WORDS = 2
_WORD_LIKENESS = 0.8
_LINE_UP_WIDTH = 640
# The line-up also starts from the shift and scale this many ORB keypoints, matched both ways, give, when at least
# this many match
_KEYPOINTS = 1000
_LEAST_KEYPOINT_MATCHES = 20
# What a frame moved or scaled shows past the frame before's edges is looked at from this share of it: a slide moved a
# few pixels left 2.4% to 2.5% outside, a dark edge line, on 6 copies of 18 public runs
_LEAST_UNCOVERED_SHARE = 0.03
# ... when the view moved straight by this share of the frame or more, its scale off 1 by at most this much
_LEAST_CAMERA_MOVE = 0.1
_MOST_CAMERA_SCALE_OFF = 0.05
# ... and is blank with edges (Canny, these levels) on at most this share of it, its border of this many pixels left out
_MOST_UNCOVERED_EDGES = 0.02
_UNCOVERED_BORDER = 7
_EDGE_LOW = 60
_EDGE_HIGH = 150
# A step whose changed blocks turn from smooth to sharp, this share of them or more, adds a picture or a label and is
# never empty: drawn over a talk's blurred photo, a zoom panel (36 of 42 blocks) and a camera's picture with its label
# (26 of 50) changed what the rule counts as content and added no word it read; over the 43 steps the rule drops in 17
# public runs, every other one turned at most 26% sharp (DEFECTS 193). A block turns sharp when its mean edge strength
# (the Laplacian, at `_SHARP_SIZE`) is at least `_SHARP_RISE` times what it was and at least `_SHARP_LEVEL`
_MOST_SHARPENED_SHARE = 0.4
_SHARP_SIZE = (640, 360)
_SHARP_RISE = 3.0
_SHARP_LEVEL = 8.0
# A terminal scrolled with nothing new (DEFECTS 210): rows of text repeat their pattern, so ECC is also started from the
# row shift that matches best, over shifts keeping this share of the rows on both frames. A line-up moving the frame
# straight up or down by at least this share of its height, at its own scale and moved sideways at most
# `_MOST_SCROLL_SIDEWAYS` of its width, is a scroll (its scrolls moved 2.7% to 5.5%, the broadcast copies of slides moved
# a few pixels 0.15% to 1.45%). Laid onto the frame before, a scroll's new rows fall outside it and no word check sees
# them, so a scroll is a copy only when the band of rows it brings in, read at full size past `_BAND_MARGIN` rows, holds
# ink (pixels `_INK_LEVELS` off its median) on at most `_MOST_BAND_INK` of its pixels: a cursor, never a line. Comparing
# the last text lines instead named a blackboard's camera tilting onto new rows and a schematic scrolled down. Over the 9
# scroll pairs of 17 public runs under 2%, the copies' bands held 0.23% to 0.76% and the bands bringing in new lines
# 1.56% and 1.77% ("bootloader" typed under a terminal's output, froscon talk 3211, DEFECTS 215)
_SCROLL_OVERLAP = 0.6
_LEAST_SCROLL_SHARE = 0.02
_MOST_SCROLL_SCALE_OFF = 0.02
_MOST_SCROLL_SIDEWAYS = 0.01
_INK_LEVELS = 60
_BAND_MARGIN = 2
_MOST_BAND_INK = 0.01
# How many kept frames before a frame it is compared with for a copy, and how many further back for a scroll alone (a
# terminal shown again after five slides, froscon talk 3211 frame 64 of 57, DEFECTS 153)
_COPY_LOOK_BACK = 3
_SCROLL_LOOK_BACK = 8
# A pointer moved over a screen that stays: at most this many blocks changed, nearly all on content, every word read on
# the frame before too, and the ink it gains, summed over the blocks whose ink changed (each block's mean grey distance
# from its own median, 0.5 or more apart), under _MOST_POINTER_INK: a pointer leaves one place and lands in another.
# Over 3,744 pairs of 17 public runs the block and word rules named 24 frames; the 3 that wrote something (chalk in a
# table's cell, twice; a line of code typed) gained 31 to 75, the pointer and cursor moves at most 17 (froscon talk 3211
# frame 23, a magnifier over a schematic: 12 blocks, 15) (DEFECTS 275)
# A slide shown again at another scale (froscon talk 3211: frame 72 of 71, its card drawn larger, 109 blocks; frame 58
# of 57, 7 of its words read otherwise at the two scales): a line-up that scales by _LEAST_RESCALE or more needs only
# _LEAST_SCROLLED_BLOCKS changed, and may leave a tenth of its words unmatched, never half (a section title lined up
# on a code slide matched none of its 2 words) (DEFECTS 277)
_LEAST_RESCALE = 0.02
_MOST_RESCALED_UNMATCHED_SHARE = 0.1
_MOST_POINTER_BLOCKS = 12
_MOST_POINTER_INK = 20.0
_INK_CHANGE = 0.5
_INK_GRID = (18, 32)


def _read_sample(path: Path) -> numpy.ndarray:
    gray = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2GRAY)

    return cv2.resize(gray, (change_capture_utils.SAMPLE_WIDTH, change_capture_utils.SAMPLE_HEIGHT),
                      interpolation=cv2.INTER_AREA)


def _read_words(path: Path) -> set[str]:
    return {word["text"].casefold().strip(_PUNCTUATION)
            for word in text_detection_service.find_words(path, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)}


def _read_gray(path: Path) -> numpy.ndarray:
    gray = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2GRAY)

    return cv2.resize(gray, (_LINE_UP_WIDTH, int(gray.shape[0] * _LINE_UP_WIDTH / gray.shape[1])),
                      interpolation=cv2.INTER_AREA)


def _build_scaled_to_width(image: numpy.ndarray,
                           width: int,
                           height: int) -> numpy.ndarray:
    """`image` scaled to `width` with its own shape kept, its bottom trimmed or padded with its own median colour to
    `height`: two views of one slide cut at other shapes (a broadcast's panel 555 by 503, its full view 720 by 576)
    stretched to one shape needed a height scale 12% off the width's, and never lined up (DEFECTS 617)."""

    scaled_height = max(1, round(image.shape[0] * width / image.shape[1]))
    scaled = cv2.resize(image, (width, scaled_height), interpolation=cv2.INTER_AREA)
    if scaled_height >= height:
        return scaled[:height]
    pad = numpy.empty((height - scaled_height, *scaled.shape[1:]), scaled.dtype)
    pad[:] = numpy.median(scaled.reshape(scaled_height * width, -1), axis=0).reshape(scaled.shape[2:])

    return numpy.vstack([scaled, pad])


def _shrink(gray: numpy.ndarray) -> numpy.ndarray:
    return cv2.resize(gray, (change_capture_utils.SAMPLE_WIDTH, change_capture_utils.SAMPLE_HEIGHT),
                      interpolation=cv2.INTER_AREA)


def _find_scroll_shift(first: numpy.ndarray,
                       second: numpy.ndarray) -> int:
    """The rows `second` shows `first` moved up (positive) or down where the two match best, over at least
    `_SCROLL_OVERLAP` of the rows."""

    height = first.shape[0]
    reach = int(height * (1 - _SCROLL_OVERLAP))
    first, second = first.astype(numpy.int16), second.astype(numpy.int16)
    differences = {shift: float(numpy.abs(first[shift:] - second[:height - shift]).mean()) if shift >= 0
                   else float(numpy.abs(first[:shift] - second[-shift:]).mean()) for shift in range(-reach, reach + 1)}

    return min(differences, key=differences.get)


def _is_scroll(warp: numpy.ndarray,
               height: int) -> bool:
    return (abs(float(warp[1, 2])) >= _LEAST_SCROLL_SHARE * height
            and abs(float(warp[0, 2])) <= _MOST_SCROLL_SIDEWAYS * _LINE_UP_WIDTH
            and abs(float(warp[0, 0]) - 1) <= _MOST_SCROLL_SCALE_OFF and abs(float(warp[1, 1]) - 1) <= _MOST_SCROLL_SCALE_OFF)


def _estimate_keypoint_warp(first: numpy.ndarray,
                            second: numpy.ndarray) -> numpy.ndarray | None:
    """The shift and scale that lay `second` onto `first` estimated from matched ORB keypoints (RANSAC), or None with too
    few matches: ECC from no shift does not reach a slide zoomed by a tenth (froscon talk 3143, frame 8 of 5)."""

    orb = cv2.ORB_create(_KEYPOINTS)
    first_points, first_marks = orb.detectAndCompute(first, None)
    second_points, second_marks = orb.detectAndCompute(second, None)
    if first_marks is None or second_marks is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(first_marks, second_marks)
    if len(matches) < _LEAST_KEYPOINT_MATCHES:
        return None
    warp, _ = cv2.estimateAffinePartial2D(numpy.float32([first_points[match.queryIdx].pt for match in matches]),
                                          numpy.float32([second_points[match.trainIdx].pt for match in matches]),
                                          method=cv2.RANSAC)

    return None if warp is None else warp.astype(numpy.float32)


def _run_ecc(first: numpy.ndarray,
             second: numpy.ndarray,
             shift: int,
             start: numpy.ndarray | None = None) -> tuple[float, numpy.ndarray] | None:
    warp = numpy.eye(2, 3, dtype=numpy.float32) if start is None else start.copy()
    warp[1, 2] -= shift
    try:
        return cv2.findTransformECC(first.astype(numpy.float32), second.astype(numpy.float32), warp, cv2.MOTION_AFFINE,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
    except cv2.error:
        return None


def _find_line_up(before: Path,
                  step: Path,
                  is_shape_kept: bool = False) -> numpy.ndarray | None:
    """The shift and scale that lay `step` onto `before`, found at `_LINE_UP_WIDTH` (OpenCV's ECC) from no shift or from
    the matched keypoints' estimate, whichever fits closer, or
    from the scroll whose rows match best when that fits closer and is a scroll; None when the two do not line up. The
    scroll's line-up is kept for scrolls only: taken for a camera that moved a third of the frame and changed scale, it
    lined a blackboard's new rows up with the old and the word check could not read the chalk. `step` is stretched to
    `before`'s shape, or with `is_shape_kept` scaled to its width with its own shape kept."""

    first, second = _read_gray(before), _read_gray(step)
    if is_shape_kept:
        second = _build_scaled_to_width(image=second, width=first.shape[1], height=first.shape[0])
    else:
        second = cv2.resize(second, (first.shape[1], first.shape[0]))
    found = _run_ecc(first, second, 0)
    keyed_start = _estimate_keypoint_warp(first, second)
    keyed = None if keyed_start is None else _run_ecc(first, second, 0, keyed_start)
    if keyed is not None and (found is None or keyed[0] > found[0]):
        found = keyed
    shift = _find_scroll_shift(first, second)
    scrolled = _run_ecc(first, second, shift) if shift else None
    if scrolled is not None and _is_scroll(scrolled[1], first.shape[0]) and (found is None or scrolled[0] > found[0]):
        return scrolled[1]

    return None if found is None else found[1]


def _is_new_band_blank(step: Path,
                       warp: numpy.ndarray) -> bool:
    """Whether the rows a scroll brings into `step`, which the frame before does not show, hold no more ink than a
    cursor: content moved up (`warp`'s row shift below 0) brings rows in at the bottom, moved down at the top."""

    gray = cv2.cvtColor(cv2.imread(str(step)), cv2.COLOR_BGR2GRAY)
    rows = int(numpy.ceil(abs(float(warp[1, 2])) * gray.shape[1] / _LINE_UP_WIDTH))
    band = gray[-rows:-_BAND_MARGIN] if warp[1, 2] < 0 else gray[_BAND_MARGIN:rows]
    if not band.size:
        return False

    return float((numpy.abs(band.astype(numpy.int16) - numpy.median(gray)) > _INK_LEVELS).mean()) <= _MOST_BAND_INK


def _build_laid_step(before: numpy.ndarray,
              step: numpy.ndarray,
              warp: numpy.ndarray,
              scale: float,
              is_shape_kept: bool = False) -> numpy.ndarray:
    """`step` laid onto `before`'s pixels by `warp`, found at `scale` of `before`'s width, `step` brought to `before`'s
    size the way the line-up brought it (`_find_line_up`)."""

    height, width = before.shape[:2]
    full = warp.copy()
    full[:, 2] /= scale
    if is_shape_kept:
        sized = _build_scaled_to_width(image=step, width=width, height=height)
    else:
        sized = cv2.resize(step, (width, height))

    return cv2.warpAffine(sized, full, (width, height),
                          flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderValue=(255, 255, 255))


def _map_lined_up_change(before: Path,
                         step: Path,
                         warp: numpy.ndarray) -> numpy.ndarray:
    """The blocks `step` changed once laid onto `before` by `warp`."""

    first, second = _read_gray(before), _read_gray(step)
    second = cv2.resize(second, (first.shape[1], first.shape[0]))
    lined_up = cv2.warpAffine(second, warp, (first.shape[1], first.shape[0]),
                              flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderValue=int(numpy.median(first)))

    return change_capture_utils.map_changed_blocks(_shrink(first), _shrink(lined_up))


def _read_image_words(image: numpy.ndarray) -> list[str]:
    return [word["text"].casefold().strip(_PUNCTUATION)
            for word in text_detection_service.find_words_in_image(image, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)]


def _is_rescaled(warp: numpy.ndarray) -> bool:
    return abs(float(warp[0, 0]) - 1) >= _LEAST_RESCALE or abs(float(warp[1, 1]) - 1) >= _LEAST_RESCALE


def _count_unmatched_words(before: Path,
                           step: Path,
                           warp: numpy.ndarray) -> int:
    return _count_word_matches(before, step, warp)[0]


def _count_word_matches(before: Path,
                        step: Path,
                        warp: numpy.ndarray) -> tuple[int, int]:
    """The step's words of four letters or more that `before` lacks, both read at `before`'s scale with the step laid
    onto it, a word read a letter off, or inside a longer word read there, counting as there: read at its own smaller
    scale a copy came back with "similar" and "axis" misread, which its frame before shows."""

    first, second = cv2.imread(str(before)), cv2.imread(str(step))
    earlier = [word for word in _read_image_words(first) if len(word) >= _MIN_NEW_WORD_LETTERS]
    laid = _build_laid_step(first, second, warp, _LINE_UP_WIDTH / first.shape[1])

    words = [word for word in _read_image_words(laid) if len(word) >= _MIN_NEW_WORD_LETTERS]
    unmatched = [word for word in words if not _is_word_among(word, earlier)]

    return len(unmatched), len(words)


def _is_word_among(word: str,
                   others: list[str]) -> bool:
    """Whether `word` is read among `others`: the same, a letter off, or inside a longer word read there."""

    return any(word == other or difflib.SequenceMatcher(None, word, other).ratio() >= _WORD_LIKENESS
               or (len(word) > _MIN_NEW_WORD_LETTERS and (word in other or other in word)) for other in others)


def _is_camera_moved(warp: numpy.ndarray,
                     shape: tuple[int, int]) -> bool:
    """Whether `warp` moves the view straight by `_LEAST_CAMERA_MOVE` of the frame or more at its own scale: a camera
    turned onto new ground (MIT 6.034 recitation 6, frame 119: 30% up). A zoom or a slide drawn smaller beside a
    panel shows its own margins past the frame before's edges, never new ground (froscon talk 3143, frame 47: 0.93)."""

    is_same_scale = abs(float(warp[0, 0]) - 1) <= _MOST_CAMERA_SCALE_OFF and abs(float(warp[1, 1]) - 1) <= _MOST_CAMERA_SCALE_OFF

    return is_same_scale and (abs(float(warp[0, 2])) >= _LEAST_CAMERA_MOVE * shape[1]
                              or abs(float(warp[1, 2])) >= _LEAST_CAMERA_MOVE * shape[0])


def _is_uncovered_blank(step: Path,
                        warp: numpy.ndarray,
                        shape: tuple[int, int]) -> bool:
    """Whether the part of the step the frame before does not show, laid onto it by `warp`, holds nothing: under
    `_LEAST_UNCOVERED_SHARE` of the step, no camera move (`_is_camera_moved`), or edges on at most
    `_MOST_UNCOVERED_EDGES` of it away from its border. What
    lies past the frame before's edges is never compared, and chalk there gives the word check nothing to read (MIT
    6.034 recitation 6, frame 119: the camera tilted up onto new rows under the same table, 9.3% edges); a call's flat
    side panel beside a slide shown smaller held 1.7% (MIT 6.100L lecture 1, frame 67)."""

    covered = cv2.warpAffine(numpy.ones(shape, numpy.uint8), warp, (shape[1], shape[0]), flags=cv2.INTER_NEAREST)
    uncovered = covered == 0
    if uncovered.mean() < _LEAST_UNCOVERED_SHARE or not _is_camera_moved(warp, shape):
        return True
    inner = cv2.erode(uncovered.astype(numpy.uint8), numpy.ones((_UNCOVERED_BORDER, _UNCOVERED_BORDER), numpy.uint8)) > 0
    if not inner.any():
        return True
    edges = cv2.Canny(cv2.resize(_read_gray(step), (shape[1], shape[0])), _EDGE_LOW, _EDGE_HIGH) > 0

    return float(edges[inner].mean()) <= _MOST_UNCOVERED_EDGES


def _is_moved_copy(before: Path,
                   step: Path,
                   changed: numpy.ndarray) -> bool:
    """Whether `step` is `before` moved or scaled: many blocks changed as they stand, nearly none once lined up, those
    few on content, and its words all read on `before`."""

    if int(changed.sum()) < _LEAST_SCROLLED_BLOCKS:
        return False
    warp = _find_line_up(before, step)
    if warp is None:
        return False
    is_scroll = _is_scroll(warp, _read_gray(before).shape[0])
    is_rescaled = _is_rescaled(warp)
    if int(changed.sum()) < _LEAST_MOVED_BLOCKS and not is_scroll and not is_rescaled:
        return False
    if not is_scroll and not _is_uncovered_blank(step, warp, _read_gray(before).shape):
        return False
    lined_up = _map_lined_up_change(before, step, warp)
    if is_scroll:
        # the top and bottom block rows of a scroll are the row it pushed out and the row beside the rows it brought in,
        # which the band check judges: a terminal whose last line stayed put while the rest moved up changed all 32
        # blocks of its bottom row (froscon talk 3211, frame 53 of 50, DEFECTS 212)
        lined_up[[0, -1]] = False
    if int(lined_up.sum()) > _MOST_LINED_UP_BLOCKS:
        return False
    if is_scroll:
        return _is_new_band_blank(step, warp)
    if lined_up.any() and change_capture_utils.map_empty_blocks(_read_sample(before))[lined_up].mean() > _MOST_EMPTY_SHARE:
        return False

    unmatched, words = _count_word_matches(before, step, warp)
    if is_rescaled:
        return unmatched <= max(_MOST_UNMATCHED_WORDS, int(words * _MOST_RESCALED_UNMATCHED_SHARE)) and (unmatched == 0 or 2 * unmatched < words)

    return unmatched <= _MOST_UNMATCHED_WORDS


def is_unchanged_copy(before: Path,
                      frame: Path) -> bool:
    """Whether no block of `frame` changed from `before`: the same screen taken again, which no repeat mark caught
    (froscon talk 3211's closing card, frame 73 of 71, after the card at another scale between) (DEFECTS 277)."""

    return not change_capture_utils.map_changed_blocks(_read_sample(before), _read_sample(frame)).any()


def is_scrolled_copy(before: Path,
                     frame: Path) -> bool:
    """Whether `frame` is `before` scrolled up or down, nothing new in the rows the scroll brings in: the moved-copy test
    for a scroll alone, which reads no words, so it can look further back."""

    changed = change_capture_utils.map_changed_blocks(_read_sample(before), _read_sample(frame))
    if int(changed.sum()) < _LEAST_SCROLLED_BLOCKS:
        return False
    warp = _find_line_up(before, frame)
    if warp is None or not _is_scroll(warp, _read_gray(before).shape[0]):
        return False
    lined_up = _map_lined_up_change(before, frame, warp)
    lined_up[[0, -1]] = False

    return int(lined_up.sum()) <= _MOST_LINED_UP_BLOCKS and _is_new_band_blank(frame, warp)


def is_moved_copy(before: Path,
                  frame: Path) -> bool:
    """Whether the frame `frame` is the frame `before` it moved or scaled, unchanged once lined up, whether or not the
    capture marked it a build step: over the 1,382 pairs of consecutive kept frames of 15 public runs, the 11 it names
    were each one slide shown again a few pixels off, none a change."""

    changed = change_capture_utils.map_changed_blocks(_read_sample(before), _read_sample(frame))

    return _is_moved_copy(before, frame, changed)


def _map_block_ink(path: Path) -> numpy.ndarray:
    """Per block of the stored frame, its pixels' mean grey distance from the block's own median grey."""

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE).astype(numpy.float32)
    rows, columns = _INK_GRID
    height, width = gray.shape[0] // rows, gray.shape[1] // columns
    blocks = gray[:rows * height, :columns * width].reshape(rows, height, columns, width).transpose(0, 2, 1, 3)
    flat = blocks.reshape(rows, columns, -1)

    return numpy.abs(flat - numpy.median(flat, axis=2, keepdims=True)).mean(axis=2)


def is_pointer_copy(before: Path,
                    frame: Path) -> bool:
    """Whether `frame` is `before` with only a pointer or a cursor moved: a few blocks changed, on content, no word read
    there that `before` lacks, and no ink gained beyond what a moved pointer carries (`_MOST_POINTER_INK`)."""

    first = _read_sample(before)
    changed = change_capture_utils.map_changed_blocks(first, _read_sample(frame))
    if not 0 < int(changed.sum()) <= _MOST_POINTER_BLOCKS or _is_drawn_on(before, frame, changed):
        return False
    if change_capture_utils.map_empty_blocks(first)[changed].mean() > _MOST_EMPTY_SHARE:
        return False
    ink_before, ink_after = _map_block_ink(before), _map_block_ink(frame)
    if ink_before.shape != ink_after.shape:
        return False
    gained = (ink_after - ink_before)[numpy.abs(ink_after - ink_before) > _INK_CHANGE]
    if float(gained.sum()) >= _MOST_POINTER_INK:
        return False

    return _count_unmatched_words(before, frame, numpy.eye(2, 3, dtype=numpy.float32)) <= _MOST_UNMATCHED_WORDS


def list_scrolled_in_lines(before: Path,
                           step: Path) -> list[str]:
    """The lines of text `step` shows where it is `before` scrolled, read in the band the scroll brings in and one text
    line past it, where a command typed on the prompt line lands: empty when `step` is no scroll of `before`. A scroll
    step told to outline what it adds outlined the old lines and left the new output out (DEFECTS 207)."""

    if not before.is_file() or not step.is_file() or cv2.imread(str(before)) is None or cv2.imread(str(step)) is None:
        return []
    warp = _find_line_up(before, step)
    if warp is None or not _is_scroll(warp, _read_gray(before).shape[0]):
        return []
    height, width = cv2.imread(str(step)).shape[:2]
    words = text_detection_service.find_words(step, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)
    if not words:
        return []
    reach = abs(float(warp[1, 2])) * width / _LINE_UP_WIDTH + numpy.median([word["box"][3] - word["box"][1] for word in words])
    start, end = (height - reach, height) if warp[1, 2] < 0 else (0, reach)
    lines: dict[int, list[dict]] = {}
    for word in words:
        if start <= (word["box"][1] + word["box"][3]) / 2 <= end:
            lines.setdefault(word["line"], []).append(word)

    return [" ".join(word["text"] for word in sorted(line, key=lambda word: word["box"][0])) for line in lines.values()]


def _map_block_sharpness(path: Path) -> numpy.ndarray:
    """Each capture block's mean edge strength, the frame at `_SHARP_SIZE`."""

    gray = cv2.resize(cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2GRAY), _SHARP_SIZE, interpolation=cv2.INTER_AREA)
    edges = numpy.abs(cv2.Laplacian(gray, cv2.CV_32F))
    rows, columns = change_capture_utils.SAMPLE_HEIGHT // 10, change_capture_utils.SAMPLE_WIDTH // 10

    return edges.reshape(rows, _SHARP_SIZE[1] // rows, columns, _SHARP_SIZE[0] // columns).mean(axis=(1, 3))


def _is_drawn_on(before: Path,
                 step: Path,
                 changed: numpy.ndarray) -> bool:
    """Whether `_MOST_SHARPENED_SHARE` or more of the blocks `step` changed turned from smooth to sharp."""

    sharp_before, sharp_after = _map_block_sharpness(before), _map_block_sharpness(step)
    sharpened = changed & (sharp_after >= _SHARP_RISE * numpy.maximum(sharp_before, 1.0)) & (sharp_after >= _SHARP_LEVEL)

    return int(sharpened.sum()) >= _MOST_SHARPENED_SHARE * int(changed.sum())


def is_empty_step(before: Path,
                  step: Path,
                  build_start: Path | None = None) -> bool:
    """Whether the frame `step` adds nothing to the frame `before` it: the blocks it changed held content there, and
    it shows no word of four letters or more that `before` lacks; or it is `before` moved or scaled, unchanged once
    lined up (a talk's broadcast showed one slide a few pixels off eight times over). With `build_start`, the first
    frame of its build, a changed block that shows that frame's look again is no change: a closed drop-down list
    brought the start page's map back, and the step, its one new field holding an address the frame before lists, was
    kept (38c3-openpv frame 10, DEFECTS 618)."""

    first, second = _read_sample(before), _read_sample(step)
    changed = change_capture_utils.map_changed_blocks(first, second)
    if not changed.any():
        return False
    # Before any other test: a block showing the build's first frame again is neither drawn nor added
    if build_start is not None and build_start != before:
        changed = changed & change_capture_utils.map_changed_blocks(_read_sample(build_start), second)
        if not changed.any():
            return True
    if _is_drawn_on(before, step, changed):
        return False
    if _is_moved_copy(before, step, changed):
        return True
    if change_capture_utils.map_empty_blocks(first)[changed].mean() > _MOST_EMPTY_SHARE:
        return False
    new = _read_words(step) - _read_words(before)

    return not any(len(word) >= _MIN_NEW_WORD_LETTERS for word in new)


# A step that replaced the text of the frame before in place is no step: at least this share of that frame's words
# (two or more, three letters or more) stand where the step changed the pixels under them (a mean grey difference
# over the word's box above `_REPLACED_WORD_SPREAD`) and are read nowhere on the step, at any confidence. A build adds
# and keeps its words; text scrolled or printed again lower keeps its words on the step. Over 234 public build steps
# 9 reach half, each looked at: a quote in place of a slide's question (release test 1, frame 69), a task slide
# replaced by "It's hard.", a cleared terminal, the next slide of a lecture, two schematics panned to another part
# (DEFECTS 326)
_REPLACED_WORD_SHARE = 0.5
_REPLACED_WORD_SPREAD = 25
_REPLACED_LEAST_WORDS = 2
_REPLACED_WORD = re.compile(r"[^\W_]{3,}")


def _list_read_tokens(path: Path) -> set[str]:
    return {token.casefold() for word in text_detection_service.find_words(path, 0)
            for token in _REPLACED_WORD.findall(str(word["text"]))}


def is_replacing_step(before: Path,
                      step: Path) -> bool:
    """Whether `step` replaced the text `before` shows in place: half its words or more changed under them and gone."""

    first = cv2.imread(str(before), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(step), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None or first.shape != second.shape:
        return False
    words = [word for word in text_detection_service.find_words(before, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)
             if _REPLACED_WORD.search(str(word["text"]))]
    # Counted by their pieces: an address read as one word ("https://github.com/acme-org/gateway") is five words a
    # slide replaces, and a slide holding only it was never judged (DEFECTS 341)
    weights = [len(_REPLACED_WORD.findall(str(word["text"]))) for word in words]
    if sum(weights) < _REPLACED_LEAST_WORDS:
        return False
    seen = _list_read_tokens(step)
    replaced = 0
    for word, weight in zip(words, weights):
        x0, y0, x1, y1 = (int(value) for value in word["box"])
        if x1 <= x0 or y1 <= y0 or all(token.casefold() in seen for token in _REPLACED_WORD.findall(str(word["text"]))):
            continue
        spread = numpy.abs(first[y0:y1, x0:x1].astype(int) - second[y0:y1, x0:x1].astype(int)).mean()
        replaced += weight * (spread > _REPLACED_WORD_SPREAD)

    return replaced >= _REPLACED_WORD_SHARE * sum(weights)


# A step's places of change are patches of the capture blocks it changed, touching at a side or a corner, each of at least
# _LEAST_PLACE_BLOCKS, at most _MOST_PLACES of them, the largest first: a pane switched to another item kept its old
# outline and the step's notes summed the new text up in its step line (DEFECTS 332)
_LEAST_PLACE_BLOCKS = 2
_MOST_PLACES = 4


def list_changed_places(before: Path,
                        step: Path) -> list[tuple[float, float, float, float]]:
    """The places where `step`'s pixels changed from `before`, each as the shares of the frame's width and height it
    spans (left, top, right, bottom), the largest first; empty when either frame cannot be read or they differ in
    size."""

    first = cv2.imread(str(before), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(step), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None or first.shape != second.shape:
        return []
    whole = (0, 0, first.shape[1], first.shape[0])
    changed = change_capture_utils.map_changed_blocks(change_capture_utils.shrink_screen(first, whole),
                                                      change_capture_utils.shrink_screen(second, whole),
                                                      change_capture_utils.SHIFT_PX).astype(numpy.uint8)
    count, labels = cv2.connectedComponents(changed, connectivity=8)
    places = []
    for label in range(1, count):
        rows, columns = numpy.nonzero(labels == label)
        if len(rows) >= _LEAST_PLACE_BLOCKS:
            places.append((len(rows), (columns.min() / changed.shape[1], rows.min() / changed.shape[0],
                                       (columns.max() + 1) / changed.shape[1], (rows.max() + 1) / changed.shape[0])))

    return [box for _, box in sorted(places, key=lambda place: -place[0])[:_MOST_PLACES]]


def list_replacing_steps(kept: list[dict],
                         frames_dir: Path) -> list[str]:
    """The slugs of the kept build steps that replaced the text of the kept frame before them; none when the text
    reader is not installed. A scroll step is never one: its lines moved."""

    if not text_detection_service.is_available():
        return []

    return [item["slug"] for before, item in zip(kept, kept[1:])
            if item.get("stepOf") and not list_scrolled_in_lines(frames_dir / Path(before["path"]).name,
                                                                  frames_dir / Path(item["path"]).name)
            and is_replacing_step(frames_dir / Path(before["path"]).name, frames_dir / Path(item["path"]).name)]


def _is_only_added(start: Path,
                   step: Path) -> bool:
    """Whether every block `step` changed was empty background on `start`, but for the capture's own share
    (`ADDITION_FLAT_SHARE`): bullets filled in under a title alone changed blocks 97% and 100% empty, while over the
    public runs the short build starts whose words a low-resolution reading found on the step (an editor scrolled, a
    room camera's projection changed, a camera moved) changed blocks at most 28% empty (DEFECTS 470)."""

    first = _read_sample(start)
    changed = change_capture_utils.map_changed_blocks(first, _read_sample(step))

    return bool(changed.any()) and float(change_capture_utils.map_empty_blocks(first)[changed].mean()) >= (
        change_capture_utils.ADDITION_FLAT_SHARE)


def list_passed_build_starts(kept: list[dict],
                             frames_dir: Path,
                             seconds: float) -> list[dict]:
    """The kept frames that start a build, no repeat, on screen at most `seconds` before its next step (each taken a
    second after its screen held still), every word of `_MIN_NEW_WORD_LETTERS` letters or more they show read on that
    step, each with the step as its `coveredBy`: a
    slide's title alone, its bullets filled in 2 s and 5 s later, reached phase 3 as frames of their own, where a first
    showing with no fact is refused (38c3 talk 797 frames 17 and 21, DEFECTS 470). None when the text reader is not
    installed."""

    if not text_detection_service.is_available():
        return []
    passed = []
    for item, after in zip(kept, kept[1:]):
        if (after.get("stepOf") != item["slug"] or item.get("repeatOf")
                or after["timestamp"] - item["timestamp"] > seconds):
            continue
        start, step = frames_dir / Path(item["path"]).name, frames_dir / Path(after["path"]).name
        if not _is_only_added(start, step):
            continue
        words = [word for word in _read_words(start) if len(word) >= _MIN_NEW_WORD_LETTERS]
        later = list(_read_words(step))
        if all(_is_word_among(word, later) for word in words):
            passed.append({**item, "coveredBy": [after["slug"]]})

    return passed


def list_empty_steps(kept: list[dict],
                     frames_dir: Path) -> list[dict]:
    """The kept frames that are build steps adding nothing to the kept frame before each, each with that frame as its
    `coveredBy`; none when the text reader is not installed, since a step's new words cannot be read."""

    if not text_detection_service.is_available():
        return []

    paths = {item["slug"]: frames_dir / Path(item["path"]).name for item in kept}

    return [{**item, "coveredBy": [before["slug"]]} for before, item in zip(kept, kept[1:])
            if item.get("stepOf") and is_empty_step(paths[before["slug"]], paths[item["slug"]],
                                                    paths.get(str(item["stepOf"])))]


# A frame stored 5% wider or narrower than the kept frame before it (a broadcast's slide beside the speaker, then in
# full) whose words, read by Apple's recognizer about 1,500 pixels wide, are that frame's but for at most
# _MOST_RESIZED_WORDS each way, from _LEAST_RESIZED_WORDS read, with at most _MOST_RESIZED_BLOCKS of the capture blocks
# changed: froscon talk 3147's frame 143 was 142 drawn larger (13 blocks, 3 words read differently by Tesseract at the
# two sizes), and no copy rule named it (DEFECTS 308). Over the 229 pairs of consecutive kept frames at two sizes of
# the public runs, those with 90 blocks or fewer changed numbered 12: 11 the same slide or terminal at another size,
# each looked at, read at most 2 words apart; the twelfth, a terminal that printed two new lines, 8
_RESIZED_WIDTH_SHARE = 0.05
_MOST_RESIZED_BLOCKS = 90
_MOST_RESIZED_WORDS = 2
_LEAST_RESIZED_WORDS = 3
_RESIZED_READ_WIDTH = 1500
# Words of letters alone: a terminal's status bar clock and date read as digit runs that differ on every second
_RESIZED_WORD = re.compile(r"[^\W\d_]{4,}")
# A frame stored 20% or more wider or narrower than the kept frame before it, a broadcast switching between the slide
# in full and the slide as a panel beside the speaker, is that frame in the other view when the two pictures scaled to
# one size match (the capture loop's own test of a slide in another view) and Apple's words on each are on the other
# but for at most _MOST_VIEW_WORD_SHARE of them, from _LEAST_VIEW_WORDS: release test 2's broadcast kept 11 such pairs,
# their two views framing the slide with other margins (100 to 209 blocks changed), 0 to 5 words read differently of
# 25 to 63. Over the public runs' 194 pairs at sizes this far apart it names only those; the pairs of other runs whose
# words match as well, a slide cut short in one view, a title or an overlay added in the other, fail the picture test
_VIEW_WIDTH_SHARE = 0.2
_MOST_VIEW_WORD_SHARE = 0.12
_LEAST_VIEW_WORDS = 8
# ... and the picture held the same when its content pixels differ in under this share, the capture's repeat test's
# 30% widened: release test 2's frame 52, its 51 in the other view 4 s before with its words matching, differed in
# 30.2%, the run's other view copies in 12% to 27%, and frame 30, its 29 in the other view half-blanked by the
# transition out, nothing on it 29 lacks, 34.9% (DEFECTS 416, 415); over the public runs no other pair whose words
# match falls between 30% and 36%, the next at 39.3% an empty step, and a shift of up to 3 pixels changed none
_VIEW_PICTURE_CONTENT_SHARE = 0.36
# ... or held the same once the later view is laid onto the earlier by a line-up that scales each side by at most
# this share and turns neither: 38c3 talk 797's panel view cut the slide's own left bar off, so the two pictures
# scaled whole stood 16 pixels apart at 640 and differed, while lined up (1.024 and 1.027) they matched (DEFECTS 469);
# a slide cut short in one view lines up at 0.76 (38c3 accessibility) and the next slide not at all
_MOST_VIEW_LINE_UP_SCALE_OFF = 0.05
_MOST_VIEW_LINE_UP_TURN = 0.01


def _read_apple_words(path: Path) -> list[str]:
    image = cv2.imread(str(path))
    scale = max(1, -(-_RESIZED_READ_WIDTH // image.shape[1]))
    scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    return [token.lower() for word in apple_text_service.find_words_in_image(scaled)
            for token in _RESIZED_WORD.findall(str(word["text"]))]


def is_resized_copy(before: Path,
                    frame: Path) -> bool:
    """Whether `frame` is `before` stored at another size with nothing new: few blocks changed at the comparison size,
    and the words Apple's recognizer reads on each found on the other, a letter off allowed. False when the recognizer
    cannot run."""

    first, second = cv2.imread(str(before)), cv2.imread(str(frame))
    if first is None or second is None or not apple_text_service.is_available():
        return False
    widths = (first.shape[1], second.shape[1])
    if abs(widths[0] - widths[1]) < _RESIZED_WIDTH_SHARE * max(widths):
        return False
    changed = change_capture_utils.map_changed_blocks(_read_sample(before), _read_sample(frame))
    if int(changed.sum()) > _MOST_RESIZED_BLOCKS:
        return False
    earlier, later = _read_apple_words(before), _read_apple_words(frame)
    if min(len(earlier), len(later)) < _LEAST_RESIZED_WORDS:
        return False

    return (sum(not _is_word_among(word, earlier) for word in later) <= _MOST_RESIZED_WORDS
            and sum(not _is_word_among(word, later) for word in earlier) <= _MOST_RESIZED_WORDS)


def _is_same_picture_lined_up(before: Path,
                              frame: Path,
                              first: numpy.ndarray,
                              second: numpy.ndarray) -> bool:
    """Whether `second` laid onto `first` by their line-up shows the same picture, for a line-up that only shifts and
    scales each side by at most `_MOST_VIEW_LINE_UP_SCALE_OFF`: the same slide framed with other margins. The later
    view keeps its own shape, since the two views are cut at other shapes (DEFECTS 617)."""

    warp = _find_line_up(before=before, step=frame, is_shape_kept=True)
    if warp is None:
        return False
    if (abs(float(warp[0, 0]) - 1) > _MOST_VIEW_LINE_UP_SCALE_OFF or abs(float(warp[1, 1]) - 1) > _MOST_VIEW_LINE_UP_SCALE_OFF
            or max(abs(float(warp[0, 1])), abs(float(warp[1, 0]))) > _MOST_VIEW_LINE_UP_TURN):
        return False
    laid = _build_laid_step(before=first,
                            step=second,
                            warp=warp,
                            scale=_LINE_UP_WIDTH / first.shape[1],
                            is_shape_kept=True)

    return frame_extraction_service.is_same_picture_scaled(first, laid, _VIEW_PICTURE_CONTENT_SHARE)


def is_view_copy(before: Path,
                 frame: Path,
                 view_widths: tuple[int, int] | None = None) -> bool:
    """Whether `frame` is `before` in the broadcast's other view with nothing new: shown at widths
    `_VIEW_WIDTH_SHARE` or more apart, the same picture scaled to one size, and the words Apple's recognizer reads on
    each found on the other, a letter off allowed, but for `_MOST_VIEW_WORD_SHARE` of them each way (DEFECTS 411).
    The widths are `view_widths`, the parts of the recording each was cut from, when given, and the stored widths
    otherwise: both views of a 1080p broadcast are stored 1,280 wide, the slide in full and the panel of 1,417
    (38c3 talk 797, DEFECTS 469). False when the recognizer cannot run."""

    first, second = cv2.imread(str(before)), cv2.imread(str(frame))
    if first is None or second is None or not apple_text_service.is_available():
        return False
    widths = view_widths or (first.shape[1], second.shape[1])
    if abs(widths[0] - widths[1]) < _VIEW_WIDTH_SHARE * max(widths):
        return False
    if not (frame_extraction_service.is_same_picture_scaled(first, second, _VIEW_PICTURE_CONTENT_SHARE)
            or _is_same_picture_lined_up(before, frame, first, second)):
        return False
    earlier, later = _read_apple_words(before), _read_apple_words(frame)
    if min(len(earlier), len(later)) < _LEAST_VIEW_WORDS:
        return False

    return (sum(not _is_word_among(word, earlier) for word in later) <= _MOST_VIEW_WORD_SHARE * len(later)
            and sum(not _is_word_among(word, later) for word in earlier) <= _MOST_VIEW_WORD_SHARE * len(earlier))


def _read_view_widths(before: dict,
                      item: dict) -> tuple[int, int] | None:
    """The widths of the parts of the recording the two frames were cut from (`screenBox`), or None when either
    lacks its box."""

    boxes = (before.get("screenBox"), item.get("screenBox"))
    if not all(boxes):
        return None

    return boxes[0][2] - boxes[0][0], boxes[1][2] - boxes[1][0]


def list_moved_copies(kept: list[dict],
                      frames_dir: Path) -> list[dict]:
    """The kept frames that are no build step and are one of the `_COPY_LOOK_BACK` kept frames before each moved or
    scaled (`is_moved_copy`), each with that frame as its `coveredBy`, compared with frames that stay only; none when the text reader is not installed, since
    the frames' words cannot be compared. A terminal scrolled up to its help and back showed frame 50's screen again as
    frame 53, two frames between them (froscon talk 3211, DEFECTS 212). A frame that is the one right before it with only
    a pointer moved is one too (`is_pointer_copy`, DEFECTS 275), and so is one that is it stored at another size
    (`is_resized_copy`, DEFECTS 308)."""

    if not text_detection_service.is_available():
        return []
    staying: list[dict] = []
    copies: list[dict] = []
    for item in kept:
        frame = frames_dir / Path(item["path"]).name
        holder = None if not staying or item.get("stepOf") else next(
            (before for before in reversed(staying[-_COPY_LOOK_BACK:])
             if is_moved_copy(frames_dir / Path(before["path"]).name, frame)
             # unchanged or with a pointer moved, only against the kept frame right before: one further back is a
             # return the capture marks a repeat, read again with the words said over it (a lecture's slide shown
             # again after the editor, 6100l lecture 3 frames 128, 129, 130)
             or (before is staying[-1] and (is_unchanged_copy(frames_dir / Path(before["path"]).name, frame)
                                            or is_pointer_copy(frames_dir / Path(before["path"]).name, frame)
                                            or is_resized_copy(frames_dir / Path(before["path"]).name, frame)
                                            or is_view_copy(frames_dir / Path(before["path"]).name, frame,
                                                            _read_view_widths(before, item))))),
            None) or next(
            (before for before in reversed(staying[-_SCROLL_LOOK_BACK:-_COPY_LOOK_BACK])
             if is_scrolled_copy(frames_dir / Path(before["path"]).name, frame)), None)
        if holder is not None:
            copies.append({**item, "coveredBy": [holder["slug"]]})
        else:
            staying.append(item)

    return copies