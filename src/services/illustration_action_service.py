"""Carry out one frame tool of a phase 4 chapter's follow-up against the frames and the cuts on disk. REQUIREMENTS.md
R10.

The agent decides; this module does what it asked and checks that it CAN be done: a frame that exists, a box it was
shown, a rectangle inside the cut. A call that cannot be done is refused in words the agent can act on, never raised:
a refusal is part of the conversation, and the agent tries again. Nothing here chooses a frame, a region or a place.

The state is one dict, the image store, which the caller saves after every action: the frames looked at, the cuts
made, and the frames left out or replaced by a diagram.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy
from pydantic import BaseModel, ValidationError

import config as app_config
from models import output_contract
from models import worker_feedback_vocabulary
from models.illustration_action_result import IllustrationActionResult
from models.illustration_context import IllustrationContext
from schemas import frame_record
from schemas import illustration_actions
from services import document_file_service
from services import face_detection_service
from services import recording_frame_service
from services import text_detection_service
from utils import document_text_utils
from utils import frame_region_utils
from utils import mermaid_text_utils
from utils import screen_crop_utils
from utils import screenshot_suggestion_utils

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)

__all__ = ["run_action", "build_empty_store", "build_working_dir", "clear_last_call", "RUNNERS"]


# Edges this close to a cut already made of the same frame make that cut again
_SAME_RECT_PIXELS = 4


# Under every refusal, as under every rejection: a refusal the worker believes wrong reaches the people building the
# tools only through a report ("those tools should ask the worker to use the feedback tools")
_REPORT_ROUTE = (f"If you believe this refusal is wrong, report it with `{worker_feedback_vocabulary.FEEDBACK_TOOL}`, kind "
                 f"`wrong_rejection`, or `insufficient_tool` when the tool cannot do what you need; then carry on.")


# How every refusal of a frame tool opens, for the agent; the program reads `is_refused`
REFUSED_PREFIX = "Refused:"


def _refuse(reason: str) -> IllustrationActionResult:
    return IllustrationActionResult(text=f"{REFUSED_PREFIX} {reason}\n{_REPORT_ROUTE}", is_refused=True)


def _refuse_unknown_frame(frame: str,
                          cut_ids: list[str]) -> IllustrationActionResult:
    # A cut's name with no cut under it is refused as that: a writer read a look's result label "[r10]" as a cut and
    # asked for "cut-10", and a refusal naming frames only stopped the strict run (38c3 talk 797, DEFECTS 545)
    if re.fullmatch(document_text_utils.CUT_ID_PATTERN, str(frame)):
        made = f"the cuts made are {', '.join(cut_ids)}" if cut_ids else "no cut was made yet"
        return _refuse(f"no cut is named {frame!r}: {made}. A `look` makes no cut, and its result's [r<n>] label names "
                       f"no cut; `cut` makes one and names it in its result")

    return _refuse(f"there is no frame {frame!r}; frames are named frame-<n> with three digits, frame-007 for frame 7, "
                   f"as the follow-up lists them")


# A frame named by its number alone, as the review's `look_at_frame` takes it: "17", "frame-17"
_FRAME_NUMBER = re.compile(r"^\s*(?:frame[-_ ]?)?0*(\d+)\s*$", re.IGNORECASE)


def _name_frame_by_slug(context: IllustrationContext,
                        arguments: dict[str, Any]) -> dict[str, Any]:
    """`arguments` with a frame named by its number ("17", "frame-17") named by its slug, when exactly one frame has that
    number: the review's `look_at_frame` takes a number, its `cut` a slug, and a reviewer sent "frame-17" and "17" to
    `cut` three times, each refused."""

    frame = arguments.get("frame")
    match = _FRAME_NUMBER.match(str(frame)) if isinstance(frame, (str, int)) and str(frame) not in context.frames else None
    if match is None:
        return arguments
    slugs = [slug for slug, item in context.frames.items() if int(item.get("index", -1)) == int(match.group(1))]

    return {**arguments, "frame": slugs[0]} if len(slugs) == 1 else arguments


def _render_boxes(boxes: dict[str, list[int]],
                  texts: dict[str, str]) -> str:
    return "\n".join(f"  {box_id} {box}" + (f" {texts[box_id]!r}" if texts.get(box_id) else "") for box_id, box in boxes.items())


def _describe_grid(width: int,
                   height: int,
                   lines: int) -> str:
    if not lines:
        return f"a grid line every {frame_region_utils.GRID_STEP}"

    return (f"{lines} grid lines across each side, every {round(width / (lines + 1))} pixels across and "
            f"{round(height / (lines + 1))} down")


def _show_cut(context: IllustrationContext,
              cut_id: str,
              cut: dict[str, Any],
              lines: int) -> IllustrationActionResult:
    """A cut made earlier, as it stands now: its size and the boxes a `mark` or `mask` on it is placed by. A rejection
    asks the worker to look at a cut it made in an earlier attempt, whose size and boxes that attempt's results no longer
    show (told to look at a cut and mask its secret, a worker guessed the rectangle twice and hid nothing)."""

    cut_path = context.paths.output_root / cut["file"]
    image = cv2.imread(str(cut_path))
    if image is None:
        return _refuse(f"the file of {cut_id} cannot be read")
    height, width = image.shape[:2]
    # Each box named by the words in it, so the one holding a value can be found (shown bare boxes, a worker
    # masked the tab labels and called the secret hidden)
    boxes = cut.get("boxes") or {}
    words = text_detection_service.find_words(cut_path)
    texts = {box_id: " ".join(word["text"] for word in words if frame_region_utils.is_centre_inside(word["box"], box))
             for box_id, box in boxes.items()}

    # A grid is drawn on a copy in the looks folder, never on the cut itself
    shown_path, grid = cut_path, ""
    if lines:
        shown_path = build_working_dir(context.paths.output_root) / "looks" / f"{cut_id}-grid.jpg"
        shown_path.parent.mkdir(parents=True, exist_ok=True)
        _write_image(str(shown_path), frame_region_utils.draw_grid(image, lines))
        grid = f", {_describe_grid(width, height, lines)}"

    return IllustrationActionResult(
        text=(f"{cut_id} from {cut['frame']}: {width} x {height} pixels{grid}, {cut.get('masks', 0)} mask(s), "
              f"{cut.get('marks', 0)} mark(s). A `mark` or `mask` on it takes these boxes or a rectangle in these pixels.\n"
              f"Boxes on the cut:\n{_render_boxes(boxes, texts) or '  none'}"),
        frame_path=shown_path)


def _write_image(path: Path,
                 image: numpy.ndarray) -> None:
    """`image` saved at `path`. `cv2.imwrite` answers False on a failed write and raises nothing, so a mask would be
    recorded over a cut whose file still shows the secret."""

    if not cv2.imwrite(str(path), image):
        raise OSError(f"{path} could not be written")


def _build_numbered_boxes(prefix: str,
                          boxes: list) -> dict[str, Any]:
    """`boxes` by id, `<prefix>1` and on."""

    return {f"{prefix}{number}": box for number, box in enumerate(boxes, start=1)}


def _name_text_boxes(text_blocks: list[dict],
                     prefix: str) -> tuple[dict[str, list[int]], dict[str, str]]:
    """The text blocks of a frame or a cut by box id, `<prefix>1` and on: each id's box, and each id's text."""

    blocks_by_id = _build_numbered_boxes(prefix, text_blocks)

    return ({box_id: block["box"] for box_id, block in blocks_by_id.items()},
            {box_id: block["text"] for box_id, block in blocks_by_id.items()})


def _run_look(context: IllustrationContext,
              store: dict[str, Any],
              action: BaseModel) -> IllustrationActionResult:
    lines = int(action.grid_lines or 0)
    if not 0 <= lines <= app_config.settings.budgets.grid_lines_max:
        return _refuse(f"grid_lines is 1 to {app_config.settings.budgets.grid_lines_max} lines across each side, or left "
                       f"out for the default")
    if action.frame in store["cuts"]:
        return _show_cut(context, action.frame, store["cuts"][action.frame], lines)
    item = context.frames.get(action.frame)
    if item is None:
        return _refuse_unknown_frame(action.frame, list(store["cuts"]))
    frame_path = context.paths.output_root / item["path"]
    frame = cv2.imread(str(frame_path))
    if frame is None:
        return _refuse(f"the frame of {action.frame} cannot be read")
    height, width = frame.shape[:2]
    words = text_detection_service.find_words(frame_path)
    text_blocks = text_detection_service.group_text_blocks(words)
    boxes, texts = _name_text_boxes(text_blocks, _TEXT_BOX_PREFIX)
    # A blank panel (a strip of black stage at the frame's edge) is never offered: the worker cut one as the slide, twice
    panels = frame_region_utils.drop_blank_boxes(frame, frame_region_utils.find_panel_boxes(frame))
    boxes.update(_build_numbered_boxes(_PANEL_BOX_PREFIX, panels))
    figures = frame_region_utils.find_figure_boxes(frame, panels, [word["box"] for word in words])
    # A photo or a screen beside the slide's flat margin, which no outline closes, is a box too (DEFECTS 375)
    content = frame_region_utils.find_content_box(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if content is not None and content not in figures and content not in panels:
        figures = [*figures, content]
    boxes.update(_build_numbered_boxes(_FIGURE_BOX_PREFIX, figures))
    shown_path = build_working_dir(context.paths.output_root) / "looks" / f"{action.frame}.jpg"
    shown_path.parent.mkdir(parents=True, exist_ok=True)
    _write_image(str(shown_path), frame_region_utils.draw_labelled_boxes(frame_region_utils.draw_grid(frame, lines), boxes))
    store["looks"][action.frame] = {"width": width, "height": height, "boxes": boxes, "words": words}
    note = "" if text_detection_service.is_available() else " Tesseract is not installed, so no text box was found."
    # A PDF's figure frame is the figure, cut from its page by phase 2: a Venn diagram's look listed only its labels'
    # boxes, and the worker cut its title strip as the figure
    if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM:
        note += (f" This frame is a figure phase 2 cut from the document's page: the whole frame is the figure, "
                 f"[0, 0, {width}, {height}]; `cut` it with no box and no rectangle to cut it whole, unless the text needs only "
                 f"a part of it.")

    return IllustrationActionResult(
        text=(f"{action.frame}: {width} x {height} pixels, {_describe_grid(width, height, lines)}.{note}\n"
              f"Boxes, [x0, y0, x1, y1] in these pixels:\n{_render_boxes(boxes, texts) or '  none'}"),
        frame_path=shown_path)


# A cut asked for by boxes AND a rectangle: handed back as a result, never refused, since a frame tool's refusal stops the run
_BOTH_GIVEN = "both ways were given: "
# How `_select_target` tells that a box id is not in the look, handed back as a result
_NO_BOX = "no box "
# The letter each kind of box id starts with, a contract with the instructions the worker reads: text blocks of a frame,
# its panels, its figures, and the text blocks of a cut
_TEXT_BOX_PREFIX = "T"
_PANEL_BOX_PREFIX = "P"
_FIGURE_BOX_PREFIX = "G"
_CUT_BOX_PREFIX = "C"
# The text reader's box is a pixel or two tight round its words, so a mask is drawn this many pixels wider on each side
_MASK_MARGIN = 2
# How every mask not made opens, and every mark and cut not made
_NO_MASK = "No mask was made"
_NO_MARK = "No mark was made"
_NO_CUT = "No cut was made"
_NOTHING_RECORDED = "Nothing was recorded"
# An outline's quoted text, a part named a title or a logo, and a word, for weighing a diagram against its frame
_QUOTED_TEXT = re.compile(r'"([^"]+)"')
_TITLE_OR_LOGO = re.compile(r"\b(?:title|logo)\b", re.IGNORECASE)
_DIAGRAM_WORD = re.compile(r"[a-z0-9]+")
# The store's list of frames whose look the worker had seen when its current turn's calls started
_LOOKS_SEEN = "looksSeen"
# How a value an earlier mask covers, and a value no word on the cut holds, are told by the words finder
_ALREADY_HIDDEN = " is already hidden by the mask at "
_NO_WORD = "no word on the cut holds "


def _select_target(boxes: dict[str, list[int]],
                   box_ids: list[str],
                   rect: list[int]) -> list[int] | str:
    """The rectangle covering the named boxes, or the rectangle given, or why neither can be used."""

    # Both given is no choice to make for the worker: box ids used to win in silence, and a worker that named boxes
    # and a rectangle reaching further down got the boxes' cut back as one it had made already
    if box_ids and len(rect) == 4:
        return (f"{_BOTH_GIVEN}you named boxes ({', '.join(box_ids)}) and gave the rectangle {list(rect)}; a cut is "
                f"one or the other. Call `cut` again with the box ids alone, or with the rectangle alone")
    if box_ids:
        unknown = [box_id for box_id in box_ids if box_id not in boxes]
        if unknown:
            return f"{_NO_BOX}{', '.join(unknown)}; the boxes are {', '.join(boxes) or 'none'}"
        chosen = [boxes[box_id] for box_id in box_ids]

        return [min(box[0] for box in chosen), min(box[1] for box in chosen), max(box[2] for box in chosen),
                max(box[3] for box in chosen)]
    if len(rect) == 4 and rect[0] < rect[2] and rect[1] < rect[3]:
        return list(rect)

    return "name box ids, or give a rectangle [x0, y0, x1, y1] with x0 < x1 and y0 < y1"


# A pixel darker than this is ink; an earlier mask is pure black (0) and is no ink. A share of ink pixels above
# _TEXT_SHARE means the area held text, even text the text reader could not read (it read no word of a
# client secret plainly visible on a cut, so no mask over that secret could ever be shown to hide it)
_INK_LEVEL = 150
_TEXT_SHARE = 0.03


def _holds_dark_text(image,
                     rect: list[int]) -> bool:
    x0, y0, x1, y1 = rect
    grey = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if grey.size == 0:
        return False
    ink = ((grey > 0) & (grey < _INK_LEVEL)).sum()

    return bool(ink / grey.size >= _TEXT_SHARE)


# A long value typed from the image differs from the reader's reading by a few letters (1 for l, 5 for S, @ for 0): a
# piece this long matches a word, or the value after its "=", this much alike (a token could not be masked)
_LONG_PIECE = 12
_CLOSE_VALUE_RATIO = 0.8
_FRAGMENT_MIN = 8


def _is_close_value(piece: str,
                    text: str) -> bool:
    """Whether a word, or what follows its "=", reads nearly as `piece`: the text reader misreads a letter or two."""

    return any(difflib.SequenceMatcher(None, piece, value).ratio() >= _CLOSE_VALUE_RATIO
               for value in (text, text.split("=", 1)[-1]))


def _holds_piece(text: str,
                 pieces: list[str]) -> bool:
    """Whether a word the reader read holds one of the pieces, or, for a long piece, nearly holds it."""

    # A word inside a piece is a fragment of the value only when long: "token" sits inside "token=<value>" and on every
    # heading about tokens (a mask by '--token=<value>' blacked out a heading's "token")
    if any(piece in text or (len(text) >= _FRAGMENT_MIN and text in piece) for piece in pieces):
        return True

    return any(len(piece) >= _LONG_PIECE and _is_close_value(piece, text) for piece in pieces)


def _holds_long_value(text: str,
                      pieces: list[str]) -> bool:
    """Whether a word holds one of the long pieces of a value, or nearly does, after its "=" too."""

    return any(len(piece) >= _LONG_PIECE and (piece.split("=", 1)[-1] in text or _is_close_value(piece.split("=", 1)[-1], text))
               for piece in pieces)


# How far a mask may stand out of an earlier one and still be inside it (a box read a pixel or two wider)
_INSIDE_SLACK = 3


def _is_rect_inside(rect: list[int],
                    outer: list[int]) -> bool:
    return (rect[0] >= outer[0] - _INSIDE_SLACK and rect[1] >= outer[1] - _INSIDE_SLACK
            and rect[2] <= outer[2] + _INSIDE_SLACK and rect[3] <= outer[3] + _INSIDE_SLACK)


# A word or a piece of a value shorter than this ("a", "5") sits inside many values and is never a match on its own
_MIN_PIECE = 4


def _match_unmasked_words(read: list[dict],
                          masked_rects: list[list[int]],
                          holds: Callable[[str], bool]) -> list[dict]:
    """The words read that no earlier mask hides and that hold the value."""

    return [word for word in read if not any(frame_region_utils.is_centre_inside(word["box"], rect) for rect in masked_rects)
            and len(word["text"].strip()) >= _MIN_PIECE and holds(word["text"])]


def _find_words_rect(cut_path: Path,
                     wanted: str,
                     frame: str,
                     masked_areas: list[dict] | None = None,
                     is_value_only: bool = False) -> list[int] | str:
    """The rectangle around every word on the cut that holds a piece of `wanted` of at least 4 characters, or why none
    does: a worker knows the value it must hide by its text, not by its pixels. A cut whose value earlier masks already
    cover is cut again (ten guessed masks blacked out a cut's rows, and no word was left to name)."""

    # A value is matched by its parts between spaces, dashes and quotes, so one misread character leaves the others
    # (a client secret read with three characters wrong could not be masked by its words)
    pieces = [piece for piece in re.split(r"[\s\-\u2013\u2014\"',;:]+", wanted) if len(piece) >= _MIN_PIECE] or [wanted]
    # A value followed onto other lines is found only in words that hold it: a word inside it ("token" of "--token=…")
    # stands on lines holding no secret (four headings' "token" were masked)
    holds = ((lambda text: _holds_long_value(text, pieces)) if is_value_only else (lambda text: _holds_piece(text, pieces)))
    words = text_detection_service.find_words(cut_path)
    # A word under an earlier mask is hidden already, whatever the reader still makes of the black
    masked_rects = [area["rect"] for area in masked_areas or []]
    read = words
    matched = _match_unmasked_words(words, masked_rects, holds)
    # With no match, the words read at any confidence are tried (an e-mail address read at confidence 22
    # could not be masked by its words)
    if not matched:
        read = text_detection_service.find_words(cut_path, min_confidence=0)
        matched = _match_unmasked_words(read, masked_rects, holds)
    # A phrase is one line: only the block holding most of its pieces is masked ("client" and "secret" of a
    # placeholder matched across the whole page and one mask blacked out the dialog it was meant to show)
    blocks = [word.get("block") for word in matched]
    best = max(set(blocks), key=blocks.count) if blocks else None
    found = [word["box"] for word in matched if word.get("block") == best]
    if not found:
        # A value an earlier mask covers is said to be hidden (told only "no word holds" it, a worker
        # believed a masked key still showed)
        hidden = next((area for area in masked_areas or [] if any(piece in str(area.get("words") or "") for piece in pieces)), None)
        if hidden:
            return f"{wanted!r}{_ALREADY_HIDDEN}{hidden['rect']}: mask the next value, or place the cut"
        shown = ", ".join(repr(word["text"]) for word in read if word["text"].strip()) or "none"

        return (f"{_NO_WORD}{wanted!r}; the words the text reader found are: {shown}. If earlier masks cover "
                f"the value, cut {frame} again and mask the new cut by its words")

    image = cv2.imread(str(cut_path))
    if image is None:
        return f"the file of {cut_path.name} cannot be read"
    height, width = image.shape[:2]

    return frame_region_utils.clip_rect([min(box[0] for box in found) - _MASK_MARGIN, min(box[1] for box in found) - _MASK_MARGIN,
                                         max(box[2] for box in found) + _MASK_MARGIN, max(box[3] for box in found) + _MASK_MARGIN],
                                        [0, 0, width, height])


def read_covered_recording_frame(video_path: Path,
                                 item: dict) -> numpy.ndarray | None:
    """The recording's frame of `item`, with the presenter's camera tile phase 2 covered on the stored frame
    (`cameraTile`) covered the same way: read whole, a cut of a lecture's diff showed the lecturer in its corner
    (DEFECTS 582). None when the recording cannot be read."""

    recording = recording_frame_service.read_recording_frame(video_path, int(item["frameNumber"]))
    if recording is None or not item.get("cameraTile"):
        return recording

    return screen_crop_utils.cover_camera_tile(recording, tuple(int(value) for value in item["cameraTile"]))


def _run_cut(context: IllustrationContext,
             store: dict[str, Any],
             action: BaseModel) -> IllustrationActionResult:
    # STEP 1: the look the cut is named in, and the boxes or rectangle it covers
    look = store["looks"].get(action.frame)
    # A PDF figure frame is the figure, and its look says only "cut it whole": it is taken unseen, so the cut is made
    # at once (a chapter of 9 figures spent a look and a cut on each, 24 minutes, the hard figures PDF)
    is_figure = (context.frames.get(action.frame) or {}).get("medium") == output_contract.PDF_FIGURE_MEDIUM
    if look is None and is_figure:
        _run_look(context, store, illustration_actions.LookAction(frame=action.frame))
        look = store["looks"].get(action.frame)
    if look is None:
        # Box ids and rectangles are named in a look's pixels, so a cut asked before any look gets that
        # look and no cut. It was refused, and under failOnFrameCutFail the refusal stopped the run.
        if action.frame not in context.frames:
            return _refuse_unknown_frame(action.frame, list(store["cuts"]))
        shown = _run_look(context, store, illustration_actions.LookAction(frame=action.frame))

        return IllustrationActionResult(
            text=(f"{_NO_CUT}: you had not looked at {action.frame}, and a cut is named in the pixels and boxes of a "
                  f"look. Here is the look; cut again by its boxes or a rectangle in its pixels.\n{shown.text}"),
            frame_path=shown.frame_path)
    # A look made in this same turn has not reached the worker yet: a writer looked at a frame and cut it in one turn, its
    # rectangle guessed, and the cut kept the slide's margin and cut through its lines (DEFECTS 363). A store with no
    # record of the looks seen (a caller that marks no turns) cuts as before
    if not is_figure and _LOOKS_SEEN in store and action.frame not in store[_LOOKS_SEEN]:
        return IllustrationActionResult(
            text=(f"{_NO_CUT}: you looked at {action.frame} in this same turn, so you have not seen its picture yet. Its "
                  f"look is in this turn's results: cut it in your next turn, by its boxes or a rectangle in its pixels."))
    # and a figure's cut naming no box and no rectangle is the whole figure
    whole =[0, 0, look["width"], look["height"]] if is_figure and not action.box_ids and len(action.rect) != 4 else action.rect
    target = _select_target(look["boxes"], action.box_ids, whole)
    # Box ids of another frame's look are handed back as a result, never refused: refused, a cut of one frame by the ids of the frame
    # looked at last stopped the run under failOnFrameCutFail
    if isinstance(target, str) and target.startswith(_BOTH_GIVEN):
        return IllustrationActionResult(text=f"{_NO_CUT}: {target[len(_BOTH_GIVEN):]}")
    if isinstance(target, str) and target.startswith(_NO_BOX):
        return IllustrationActionResult(text=f"{_NO_CUT}: {action.frame} has {target}. Box ids belong to one frame's "
                                             f"look: cut {action.frame} by its own boxes, or `look` at it again to see them")
    if isinstance(target, str):
        return _refuse(target)

    # STEP 2: the rectangle with its margin, an edge through a line of text moved out to keep the line whole
    rect = frame_region_utils.clip_rect(frame_region_utils.expand_rect(target, max(action.margin, 0)),
                                          [0, 0, look["width"], look["height"]])
    # The margin stops at the border of the part beside it (DEFECTS 629)
    margin_frame = cv2.imread(str(context.paths.output_root / context.frames[action.frame]["path"]), cv2.IMREAD_GRAYSCALE)
    if margin_frame is not None and margin_frame.shape[1] == look["width"]:
        rect = frame_region_utils.stop_margin_at_borders(gray=margin_frame, target=target, rect=rect)
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        return _refuse("the rectangle lies outside the frame")
    # An edge through a line of text loses the words past it (a slide's decision line lost its last words), so
    # the edge is moved out to keep the line whole and the cut says so. It was refused with that rectangle instead, and
    # under failOnFrameCutFail a worker guessing edges from the grid stopped the run twice on one slide
    crossed_words = frame_region_utils.list_crossed_words(rect, look["words"])
    moved = ""
    if crossed_words:
        whole_lines = frame_region_utils.expand_to_whole_blocks(rect, look["words"], look["width"], look["height"])
        moved = (f"its edges were moved from {rect} to {whole_lines} to keep whole the lines they ran through "
                 f"({', '.join(repr(word) for word in crossed_words[:6])}): look at the cut, and `undo_cut` it if the wider part "
                 f"is not what you meant")
        rect = whole_lines

    # STEP 3: a cut made already handed back, the frame's budget of cuts kept
    # A cut already made is handed back, and a frame is cut at most `budgets.cutsPerFrame` times (a chapter cut one
    # frame sixteen times, the same rectangle three times in a row, until its calls ran out)
    cuts_of_frame = [cut_id for cut_id, cut in store["cuts"].items() if cut["frame"] == action.frame]
    # A rectangle a few pixels off a cut already made is that cut again (a cut 2 pixels off an earlier one
    # of the same frame was made anew and took one of the frame's cuts)
    repeated_cut_id = next((cut_id for cut_id in cuts_of_frame
                 if all(abs(a - b) <= _SAME_RECT_PIXELS for a, b in zip(store["cuts"][cut_id]["lookRect"], rect))
                 and not store["cuts"][cut_id].get("discarded")), None)
    if repeated_cut_id:
        return IllustrationActionResult(
            text=f"{repeated_cut_id} from {action.frame}: this rectangle was cut already; place {repeated_cut_id} by its id.",
            frame_path=context.paths.output_root / store["cuts"][repeated_cut_id]["file"])
    live = [cut_id for cut_id in cuts_of_frame if not store["cuts"][cut_id].get("discarded")]
    if len(live) >= app_config.settings.budgets.cuts_per_frame:
        # Handed back as a result, never refused: refused, a fourth cut stopped the run under failOnFrameCutFail
        return IllustrationActionResult(text=f"{_NO_CUT}: {action.frame} has {len(live)} cuts already "
                                             f"({', '.join(live)}): place one of them by its id, `undo_cut` the last one if it "
                                             f"shows the wrong part, or call `leave_out` for the frame with the reason it "
                                             f"cannot be shown")

    # STEP 4: the piece, from the recording at full resolution
    item = context.frames[action.frame]
    whole_frame_reason = _explain_whole_frame_cut(item, rect, look["width"], look["height"])
    if whole_frame_reason:
        # Handed back with no cut made and what to cut instead, as a cut before a look is: refused, it stopped the run under
        # failOnFrameCutFail though the tool worked and said what to do
        return IllustrationActionResult(text=f"{_NO_CUT}: {whole_frame_reason}.")
    # A PDF's figure over a page break is its pages' parts stacked, as its frame is, and that whole image is its box
    parts = item.get("parts")
    recording = (recording_frame_service.read_figure_parts(context.video_path, parts) if parts
                 else read_covered_recording_frame(context.video_path, item))
    # A frame an `interval` or `scene` run took has no screen box: its frame is the whole recording's frame.
    screen_box = (None if parts else item.get("box")) or ([0, 0, recording.shape[1], recording.shape[0]] if recording is not None
                                                          else [0, 0, look["width"], look["height"]])
    recording_rect = frame_region_utils.map_to_recording(rect, screen_box, look["width"])
    warnings = [moved] if moved else []
    if recording is not None:
        height, width = recording.shape[:2]
        x0, y0, x1, y1 = frame_region_utils.clip_rect(recording_rect, [0, 0, width, height])
        piece = recording[y0:y1, x0:x1]
    else:
        stored = cv2.imread(str(context.paths.output_root / item["path"]))
        if stored is None:
            return _refuse(f"the file of {action.frame} cannot be read")
        piece = stored[rect[1]:rect[3], rect[0]:rect[2]]
        warnings.append("cut from the stored frame: the recording could not be read, so small text may be hard to read")
    # An empty area is refused before it is made, so it never counts toward the frame's cuts (a cut of empty
    # editor space was made, counted and placed)
    if float(cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY).std()) < frame_region_utils.BLANK_SPREAD:
        return _refuse(f"{rect} on {action.frame} is an empty area, with no text or drawing on it: `look` at "
                       f"{action.frame} and cut where the part's text or drawing is")
    # A cut of the whole frame is as large as a cut of it can be: told its side was short, a writer that had cut a thin
    # PDF figure whole doubted the cut and looked again (DEFECTS 525)
    is_whole = (rect[0] <= 1 and rect[1] <= 1 and rect[2] >= int(look["width"]) - 1
                and rect[3] >= int(look.get("height") or 0) - 1)
    if min(piece.shape[0], piece.shape[1]) < context.min_cut_pixels and not is_whole:
        warnings.append(f"a side is shorter than {context.min_cut_pixels} pixels: {piece.shape[1]} x {piece.shape[0]}")

    # STEP 5: what the cut leaves out beside it, past its edges and through its panels, as warnings
    # A box is the program's guess at a part, and a table's last column fell outside one: the text left
    # out in line with the cut is named, and the worker judges whether it belongs to the part
    lined_up_blocks = frame_region_utils.list_lined_up_blocks(rect, text_detection_service.group_text_blocks(look["words"]))
    if lined_up_blocks:
        warnings.append("text in line with the cut is left out: "
                        + ", ".join(f"{block['text'][:40]!r} at its {side}" for side, block in lined_up_blocks[:5])
                        + ": cut again with it if it belongs to the part, as a table's last column or its headings do")
    # A cut of a table stopped after its third row, through a cell's second line, and was placed as the table
    # A row in the cut's own columns just past its top or bottom edge is named
    for side, lines in frame_region_utils.list_continued_rows(rect, look["words"]).items():
        edge = "bottom" if side == "below" else "top"
        warnings.append(f"the cut ends inside a table or list: a row in its columns goes on {side} its {edge} edge ("
                        + ", ".join(repr(line["text"][:30]) for line in lines[:4])
                        + "): cut again to take it in if the whole table is meant, its headings included")
    # A cut's left edge ran through a code line and showed "else:" as "e:", and the tool warned of nothing: a line its side
    # edge runs through is named
    side_cut_lines = frame_region_utils.list_lines_cut_at_sides(rect, look["words"])
    if side_cut_lines:
        warnings.append("the cut's side edge runs through text: "
                        + ", ".join(f"{line['text'][:40]!r} at its {side} edge" for side, line in side_cut_lines[:5])
                        + ": cut again with the edge past the line, so no word is cut in half")
    # A schematic's cut ended through its rotated pin labels, which the text reader does not read, and the tool warned
    # of nothing (DEFECTS 223): an edge many marks cross is named
    look_frame = cv2.imread(str(context.paths.output_root / item["path"]), cv2.IMREAD_GRAYSCALE)
    marked_edges = frame_region_utils.list_marked_edges(look_frame, rect) if look_frame is not None else []
    if marked_edges:
        warnings.append("the cut's " + ", ".join(f"{side} edge runs through {count} marks" for side, count in marked_edges)
                        + " (lines, symbols or letters the text reader may not read, such as rotated labels): look at "
                          "the cut, and cut again with that edge past them if they belong to the part, or before them if not")
    # A chart's arrow and label ran out of a cut's top, and a chart was cut at its x axis, its values and title lost,
    # and the tool warned of nothing: the text reader reads neither (DEFECTS 261)
    for chart in frame_region_utils.list_cut_charts(rect, frame_region_utils.find_chart_boxes(look_frame)
                                                    if look_frame is not None else []):
        warnings.append(f"the cut holds a chart's axes but not the whole chart {chart} (its axis values, axis titles, "
                        f"or an arrow and its label run past the cut's edges): cut again to take the chart in whole")
    # A cut's edge moved to keep a line whole ran through a card beside it, and half the card was cut
    panels = {box_id: box for box_id, box in look["boxes"].items() if box_id.startswith(_PANEL_BOX_PREFIX)}
    # Only a panel whose left-out part holds text is named (an editor's empty space beside its margin guide,
    # read as a panel, was named on every cut of two code frames, and the worker cut them again and again)
    for side, panel_id in frame_region_utils.list_cut_panels(rect, panels, int(look.get("width") or 0)):
        if not frame_region_utils.holds_text_outside(panels[panel_id], rect, look["words"]):
            continue
        warnings.append(f"the cut's {side} edge runs through panel {panel_id} {panels[panel_id]}, so only part of it is "
                        f"shown: cut again to take it in whole, or to leave it out, if the part is meant to show it")

    # A cut taken again wider took in the slide's address line and page number below the part, unwarned (DEFECTS 372)
    footer = frame_region_utils.list_footer_lines(rect, look["words"], int(look.get("height") or 0),
                                                  [box for box_id, box in look["boxes"].items() if box_id[0] in "PG"])
    if footer:
        # The rectangle to cut again with is named: told only to cut above the footer, a writer guessed the edge from the
        # grid four times and cut through the two terminals above the address each time (DEFECTS 396)
        above = frame_region_utils.build_rect_above_footer(rect, footer)
        warnings.append("the cut takes in the slide's footer below the part ("
                        + ", ".join(repr(line["text"][:50]) for line in footer[:3])
                        + f"): cut again as {above}, its bottom edge just above it, so the figure holds the part alone")

    # STEP 6: the cut saved, checked for secrets and faces, and recorded in the store
    cut_id = document_text_utils.build_cut_id(store["nextCut"])
    store["nextCut"] += 1
    cut_path = build_working_dir(context.paths.output_root) / "cuts" / f"{cut_id}.png"
    cut_path.parent.mkdir(parents=True, exist_ok=True)
    _write_image(str(cut_path), piece)
    # The check on a placed cut demands a mask here; said when the cut is made (told "Warnings: none", a
    # worker placed a cut of a credential with no mask)
    # The cut's own text counts without phase 3's note (a settings file's passwords were cut with "Warnings:
    # none", its note naming only "the values used")
    cut_words = [word["text"] for word in text_detection_service.find_words(cut_path, min_confidence=0)]
    keys = document_text_utils.find_credential_keys(cut_words)
    if _is_secret_frame(item, is_strong=True) or keys or (
            _is_secret_frame(item) and document_text_utils.find_readable_secrets(cut_words)):
        # Says where the sign came from: the cut's own keys are named, never put on phase 3
        source = (f"the cut shows {', '.join(repr(key) for key in keys[:6])} with a value after it" if keys
                  else "phase 3 notes this frame shows a secret")
        warnings.append(f"{source}: `mask` every secret value on the cut (a key, a code, "
                        "a password, an authorization header, a cookie) by its `words` before you place it; an id in GUID "
                        "form names a record and stays readable")
    # The texts phase 3 names as the part to show, none of them on the cut: a cut by a box id that was the window's title
    # bar held the bookmarks, not the code phase 3 asked for, with "Warnings: none"
    # An account number on a cut is named to mask: no check refused one, and a worker reasoned that an IBAN needed no
    # mask because none had been asked for. A warning, so accepted chapters are not judged again for it
    ibans = _IBAN.findall(" ".join(cut_words))
    missing_texts = _list_needed_texts_missing(item, cut_words)
    # Named on the frame's first cut alone: the text reader never reads light text on a coloured ground, so the warning
    # stands whatever the cut, and a worker that had looked and seen the part cut the same menu three times chasing it
    # Nor on a frame the reader read no word on at all: there the cut's own "none" line says so
    if missing_texts and not live and look.get("words"):
        # Said as what the reader did not read, never as absent: white text on a red pop-up stood whole on a cut the
        # warning called empty of it, and the worker set out to undo a right cut
        warnings.append(f"phase 3 asks this frame for {', '.join(repr(text) for text in missing_texts[:4])}, and the text reader "
                        f"does not read it on the cut (it misses light text on a coloured ground): look at the cut; if the "
                        f"part is not there, cut the frame again")
    # A floating camera inside the shared screen stays in a cut (a presenter's face and name were cut with a
    # credentials page and offered to place unmasked), so each face is named in the cut's own pixels for `mask`.
    # Said as what the detector found, never as a person: a slide's drawn face icon was named "a person's face" with its
    # label to mask
    # A PDF's photograph is the document's published content, not a camera: told to mask each face, a worker would
    # place a page of blanked portraits (the hard figures PDF)
    faces = ([] if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM
             else face_detection_service.detect_faces(cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)))
    for face in faces:
        warnings.append(f"the face detector finds a face on the cut at {list(face)}: look at it; a person (a camera, a "
                        f"photo) is masked with that rectangle and the name beside it, or cut again without the camera; "
                        f"a drawn icon stays as it is")
    cut_blocks = text_detection_service.group_text_blocks(text_detection_service.find_words(cut_path))
    boxes, texts = _name_text_boxes(cut_blocks, _CUT_BOX_PREFIX)
    # Named by box id: masked by its words, an IBAN the reader read otherwise ("DEAT" for "DE47") matched nothing
    if ibans:
        holders = [box_id for box_id, text in texts.items() if _IBAN.search(text)]
        warnings.append(f"an account number is readable on the cut ({ibans[0].strip()[:8]}…): `mask` each IBAN by the "
                        f"box id holding it{' (' + ', '.join(holders) + ')' if holders else ''} before you place the cut")
    store["cuts"][cut_id] = {"frame": action.frame, "lookRect": rect, "recordingRect": recording_rect,
                             "file": str(cut_path.relative_to(context.paths.output_root)), "warnings": warnings,
                             "boxes": boxes, "boxTexts": texts, "marks": 0, "masks": 0,
                             "isFromRecording": recording is not None}

    # A frame the text reader read no word on says nothing about its cut: told "the cut missed it", a worker undid a
    # right cut of a dimmed slide on a 360p recording and cut it again
    no_text = ("  none: the text reader read no word on the whole of " + action.frame + " either (small, dimmed or light "
               "text), so this says nothing about the cut: judge it by looking at it, and keep it if the part is there"
               if not look.get("words") else
               "  none: no text was found on it. A cut of pictures or shapes has none; if the part you meant holds a "
               "label or text, the cut missed it: look at the cut and cut again")

    return IllustrationActionResult(
        text=(f"{cut_id} from {action.frame}: {piece.shape[1]} x {piece.shape[0]} pixels. "
              # "none" alone read as a failed cut to a worker whose cut held only pictures, and a reassurance alone let a
              # cut that missed its part's label pass unnoticed
              f"Warnings: {'; '.join(warnings) or 'none'}.\nBoxes on the cut:\n"
              f"{_render_boxes(boxes, texts) or no_text}"),
        frame_path=cut_path)


# A cut of this share of a frame or more carries the window, its tabs, bookmarks and side panels with the part: a whole
# browser frame was placed for a flowchart that filled a third of it. A slide or a drawn diagram fills its
# frame and is cut whole.
_WHOLE_FRAME_SHARE = 0.9
_FILLS_ITS_FRAME = re.compile(r"\b(?:slide|title|diagram|chart|card)\b", re.IGNORECASE)


def _explain_whole_frame_cut(item: dict[str, Any],
                             rect: list[int],
                             width: int,
                             height: int) -> str:
    """Why a cut of almost the whole frame is not made, naming the part phase 3 named; empty when the cut is a part, or
    the frame shows a slide or a diagram that fills it."""

    share = (rect[2] - rect[0]) * (rect[3] - rect[1]) / max(width * height, 1)
    record = item.get("record") or {}
    # A PDF's figure frame is the figure, already cut from its page: no window is round it (NIST's Figure 1, refused
    # whole twice, was placed without its title box and its last box)
    is_figure_frame = item.get("medium") == output_contract.PDF_FIGURE_MEDIUM
    if share < _WHOLE_FRAME_SHARE or is_figure_frame or _FILLS_ITS_FRAME.search(str(record.get("screen_kind") or "")):
        return ""
    parts = [str(shot.get("part") or "") for shot in record.get("screenshots") or [] if shot.get("part")]
    named = f": phase 3 named {'; '.join(repr(part) for part in parts)}" if parts else ""

    return (f"this rectangle is {share:.0%} of the frame and carries the window around the part a reader needs{named}. "
            f"Cut that part only, by its boxes or a rectangle around it")


def _is_secret_frame(item: dict[str, Any],
                     is_strong: bool = False) -> bool:
    """Do phase 3's notes on this frame say a part of it shows a secret; with `is_strong`, name a secret, a password, a
    credential or an API key rather than only a token?"""

    shots = (item.get("record") or {}).get("screenshots") or []
    note = " ".join(f"{shot.get('part', '')} {shot.get('why', '')}" for shot in shots)

    return document_text_utils.is_secret_named_strongly(note) if is_strong else document_text_utils.is_secret_shown(note)


# An IBAN as the text reader reads it: a country code, two check digits, then at least twelve more digits, grouped by four
# or run together
# The reader often splits the country code off ("1004 0000 0372 4036 00"), so four groups of four digits count too
# An IBAN, with its country code or with the code split off by the reader: then four groups of four and a closing pair, as
# a German IBAN's digits, since any four groups of four digits named a process table's memory columns (DEFECTS 377)
_IBAN = re.compile(r"\b(?:[A-Z]{2}\d{2}(?:\s?\d{4}){3,}|\d{4}(?:\s\d{4}){3}\s\d{2}\b(?!\s?\d))")
# A text phase 3 quotes as part of what a screenshot must show: in backticks or double quotes, four letters or more
_QUOTED_TEXT = re.compile(r"[`\"“]([^`\"”]{4,60})[`\"”]")


def _list_needed_texts_missing(item: dict[str, Any],
                               cut_words: list[str]) -> list[str]:
    """The texts phase 3's `needed` screenshot notes quote for this frame, when not one of them is on the cut; empty when
    the notes quote none or the cut holds one. A text counts as on the cut when its letters, case folded, stand in the
    cut's words run together: the reader splits and misreads a word, so this is a lead for the worker, never a refusal."""

    shots = [shot for shot in (item.get("record") or {}).get("screenshots") or []
             if shot.get("importance") == frame_record.SCREENSHOT_NEEDED]
    texts = list(dict.fromkeys(match.group(1).strip() for shot in shots
                               for match in _QUOTED_TEXT.finditer(f"{shot.get('part', '')} {shot.get('why', '')}")))
    # A PDF figure's caption stands under it on the page, never in its picture: its words are not asked of the cut
    caption = str(item.get("caption") or "").casefold() if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM else ""
    texts = [text for text in texts if not caption or text.casefold() not in caption]
    letters = re.sub(r"[^a-z0-9]", "", "".join(cut_words).casefold())
    words = [word.casefold() for word in cut_words if len(word) >= 4]
    # A word misread by a letter or two counts ("ausgetUnt" for "Ausgeführt"): an exact test sent a worker to undo a
    # cut that held the word
    found = [text for text in texts if re.sub(r"[^a-z0-9]", "", text.casefold())[:12] in letters
             or difflib.get_close_matches(text.casefold().split()[0] if text.split() else "", words, n=1, cutoff=0.6)]

    return texts if texts and not found else []


def _name_readable_secrets(boxes: dict[str, list[int]],
                           words: list[dict],
                           masked_rects: list[list[int]]) -> list[str]:
    """Each word the reader still reads of a secret on a cut, with the cut's box that holds it: masking a code a word at a
    time, a worker never reached its lines the reader could not read."""

    named = []
    texts = [word["text"] for word in words]
    for index, word in enumerate(words):
        # A label whose value a mask right after it hides shows no secret (named as still readable after its
        # token was masked, it hid the one line truly left, and the worker placed the cut with that token showing)
        if document_text_utils.is_credential_label(word["text"]) and frame_region_utils.is_label_value_masked(
                words, index, masked_rects, document_text_utils.is_auth_scheme):
            continue
        for shown in document_text_utils.find_readable_secrets(texts[index:index + 2])[:1]:
            if not shown.startswith(word["text"][:8]):
                continue
            holder = next((box_id for box_id, box in boxes.items() if frame_region_utils.is_centre_inside(word["box"], box)), "")
            named.append(f"{shown!r}" + (f" in {holder}" if holder else ""))

    return named


def _draw_on_cut(context: IllustrationContext,
                 store: dict[str, Any],
                 action: BaseModel,
                 is_hidden: bool) -> IllustrationActionResult:
    cut = store["cuts"].get(action.cut)
    if cut is None:
        # Names where a cut id comes from: a worker masked a cut it read into its request by the pattern of an earlier
        # follow-up's cut list, and no cut of that id was ever made
        made = [f"{cut_id} ({entry['frame']})" for cut_id, entry in document_file_service.list_cuts_in_use(store).items()]

        return _refuse(f"there is no cut {action.cut!r}: a cut id is the one a `cut` call returned; cuts in use: "
                       f"{', '.join(made) or 'none'}. To show a frame no cut is listed for, `cut` it first")
    wanted = getattr(action, "words", "").strip()
    target = (_find_words_rect(context.paths.output_root / cut["file"], wanted, cut["frame"], cut.get("maskedAreas"))
              if wanted else
              _select_target(cut["boxes"], [action.box_id] if action.box_id else [], action.rect))
    if isinstance(target, str):
        # Words the reader read otherwise get a result saying no mask was made, with the boxes to mask by: refused, a mask by
        # "DE47" on a cut whose reader read "DEAT" stopped the run under failOnFrameCutFail
        # A value already hidden is handed back as a result too: refused, a second mask of a masked token stopped the run
        if wanted and _ALREADY_HIDDEN in target:
            return IllustrationActionResult(text=f"{_NO_MASK}: {target}.")
        if wanted and target.startswith(_NO_WORD):
            boxes = "; ".join(f"{box_id} {text!r}" for box_id, text in (cut.get("boxTexts") or {}).items())

            return IllustrationActionResult(text=f"{_NO_MASK}: {target}."
                                                 + (f" Mask by the box id holding it instead: {boxes}" if boxes else ""))
        # A box id the cut does not list, or box ids and a rectangle together, are handed back like `cut` hands them
        # back: refused, the mix-up stopped the run
        if target.startswith((_NO_BOX, _BOTH_GIVEN)):
            return IllustrationActionResult(text=f"{_NO_MASK if is_hidden else _NO_MARK}: {target}.")
        return _refuse(target)
    cut_path = context.paths.output_root / cut["file"]
    frame = cv2.imread(str(cut_path))
    if frame is None:
        return _refuse(f"the file of {action.cut} cannot be read")
    height, width = frame.shape[:2]
    if target[0] < 0 or target[1] < 0 or target[2] > width or target[3] > height:
        # A rectangle in the frame's pixels lands outside the cut and hides nothing (a secret's cut was
        # masked at y 525 on a cut 390 pixels high, and the secret stayed readable)
        return _refuse(f"{target} runs outside {action.cut}, which is {width} x {height} pixels: give the rectangle in the "
                       f"cut's own pixels, not the frame's. Boxes on the cut:\n{_render_boxes(cut['boxes'], {}) or '  none'}")
    # A mask inside an earlier one hides nothing more (a box already blacked out was masked three times, each
    # told nothing showed the mask hid a value)
    covering = next((area for area in cut.get("maskedAreas") or []
                     if is_hidden and getattr(action, "box_id", "") and _is_rect_inside(target, area["rect"])), None)
    if covering:
        return IllustrationActionResult(text=f"{_NO_MASK}: {target} is already hidden by the mask at "
                                             f"{covering['rect']}. Mask the next value, or place the cut.")
    if not is_hidden:
        _write_image(str(cut_path), frame_region_utils.draw_mark(frame, target, action.number or None))
        cut["marks"] += 1

        return IllustrationActionResult(text=f"{action.cut} marked at {target}.", frame_path=cut_path, is_applied=True)
    # The words under a mask are recorded and named, so a mask on the wrong line is seen (a worker masked a
    # cut's description line, called the secret masked, and the secret's value stayed readable)
    covered = " ".join(word["text"] for word in text_detection_service.find_words(cut_path)
                       if frame_region_utils.is_centre_inside(word["box"], target))
    # A credential's value runs past its line's box, to the edge and onto a wrapped line no box holds (a
    # cookie and an authorization header masked by their boxes stayed readable), so its mask runs to the cut's right
    # edge and down to the next box
    # A mask on the rows of an earlier credential mask continues that line: the label under the earlier mask is no
    # longer readable, so the rows are what shows it
    reaches_edge = document_text_utils.has_credential_label(covered.split()) or any(
        area["rect"][1] < target[3] and target[1] < area["rect"][3]
        for area in cut.get("maskedAreas") or [] if document_text_utils.has_credential_label(str(area.get("words") or "").split()))
    if reaches_edge:
        below = [box[1] for box in cut["boxes"].values() if box[1] > target[3] - _MASK_MARGIN and box[1] > target[1]]
        target = [target[0], target[1], width, max(target[3], min(below) - 1) if below else target[3]]
    # A mask over a label alone hides the one word that shows a secret is there, and leaves the secret (a
    # worker masked 'cookie:' and 'authorization:', and both values stayed readable with nothing left to flag them)
    if document_text_utils.is_label_without_value(covered.split()):
        return _refuse(f"{covered!r} is a credential's label: this mask would hide the label, not its value. `mask` the "
                       f"label with the value after it, by the box id holding its line as `look` on {action.cut} lists it")
    share = frame_region_utils.compute_masked_share(width, height,
                                                    [area["rect"] for area in cut.get("maskedAreas") or []] + [target])
    # Hiding a secret is never refused for its size (a token wrapped over two lines of a narrow terminal,
    # its mask widened to the edge covered 23% of the cut, and the refusal stopped the run four times); the limit is
    # for masks over areas that hold none
    hides_credential = reaches_edge or bool(document_text_utils.find_credential_keys(covered.split()))
    if share > frame_region_utils.MOST_MASKED_SHARE and not hides_credential:
        return _refuse(f"this mask would hide {share:.0%} of {action.cut}: a figure that much hidden shows the reader too "
                       f"little. A mask hides a value, never an area: cut the frame again around the part that holds no "
                       f"secret, or `leave_out` the frame and say in words what it showed")
    has_text = _holds_dark_text(frame, target)
    _write_image(str(cut_path), frame_region_utils.hide_region(frame, target))
    cut["masks"] += 1
    cut.setdefault("maskedAreas", []).append({"rect": target, "words": covered, "coveredText": has_text,
                                              **({"reachesEdge": True} if reaches_edge else {})})
    # What the reader still reads of a secret is named, never called hidden (told "no longer readable" after
    # one line of a code was masked, a worker placed a cut whose authorization header and cookie stayed readable)
    # Read with no confidence floor: a token's line read at low confidence was left readable unnamed
    words = text_detection_service.find_words(cut_path, min_confidence=0)
    readable = (_name_readable_secrets(cut["boxes"], words, [area["rect"] for area in cut["maskedAreas"]])
                if _is_secret_frame(context.frames.get(cut["frame"]) or {})
                or document_text_utils.find_credential_keys([word["text"] for word in words]) else [])
    # By the value's words, never the whole box: a box on a settings file holds most of the file, and masking it blacks
    # out the figure
    after = (f"The text reader still reads {', '.join(readable[:6])} on {action.cut}: `mask` each value by its `words` (a "
             f"label such as 'authorization:', 'cookie:' or a password key with the value after it), before you place the cut."
             if readable else
             "Check the cut shown next: the value that must stay hidden is no longer readable.")
    # The box is the program's: a writer read its masked cut back and wrote that the screen hid the password (DEFECTS 391)
    after = f"{after} {_MASK_IS_YOURS}"
    if not covered and has_text:
        # Text nobody read is never called hidden (a mask on the wrong line was told "no longer readable")
        after = after if readable else f"Check the cut shown next: make sure no part of a secret is still readable. {_MASK_IS_YOURS}"
        summary = f"{action.cut} masked at {target}, over text the text reader could not read."

        return IllustrationActionResult(text=f"{summary} {after}", frame_path=cut_path, summary=summary, is_applied=True)
    if not covered:
        # A mask over no word hides nothing anyone can show (told "no longer readable" after each blind mask,
        # a worker guessed rectangles eleven times and the secret stayed readable)
        summary = f"{action.cut} masked at {target}, over no word the text reader could read: nothing shows this mask hides a value."

        return IllustrationActionResult(text=f"{summary} Mask the value by its `words` as `look` on {action.cut} lists them, "
                                             f"or by the box id that holds them. {_MASK_IS_YOURS}", frame_path=cut_path,
                                        summary=summary, is_applied=True)

    summary = f"{action.cut} masked at {target}, over the words {covered!r}."

    return IllustrationActionResult(text=f"{summary} {after}", frame_path=cut_path, summary=summary, is_applied=True)


_MASK_IS_YOURS = ("The black box is your mask, not the screen's: in the text and the figure's description say the value is "
                  "hidden in this document, never that the screen or the program shown hides it.")


def _run_mark(context: IllustrationContext,
              store: dict[str, Any],
              action: BaseModel) -> IllustrationActionResult:
    # A number with no box and no rectangle is a figure's number, which the program writes itself: a writer marked a
    # cut "1" to number its figure, was refused, and the strict run stopped (DEFECTS 511). Nothing is drawn and nothing
    # is refused
    if action.number and not action.box_id and not action.rect:
        return IllustrationActionResult(text=f"Nothing drawn: the program numbers the document's figures itself, so "
                                             f"{action.cut} needs no number. `mark` draws a box, or a numbered marker "
                                             f"for a numbered step of the text, where a box id or a rectangle says.")

    return _draw_on_cut(context, store, action, is_hidden=False)


# A mask by words hides each line that holds them, up to this many (a value is seldom on a page more often)
_MOST_LINES_BY_WORDS = 6


def _run_mask(context: IllustrationContext,
              store: dict[str, Any],
              action: BaseModel) -> IllustrationActionResult:
    """Masks the target; by words, every line on the cut that holds them, as the tool promises (a token in a
    login command and in a header was hidden on one line, and the cut was placed with the other readable)."""

    result = _draw_on_cut(context, store, action, is_hidden=True)
    # Only a value is followed onto other lines: a phrase's common words recur where nothing is secret
    wanted = " ".join(piece for piece in action.words.split() if len(piece) >= _LONG_PIECE)
    if not wanted or not result.is_applied:
        return result
    done = [result.summary]
    for _ in range(_MOST_LINES_BY_WORDS - 1):
        cut = store["cuts"][action.cut]
        target = _find_words_rect(context.paths.output_root / cut["file"], wanted, cut["frame"], cut.get("maskedAreas"),
                                  is_value_only=True)
        if isinstance(target, str):
            break
        following = _draw_on_cut(context, store, illustration_actions.MaskAction(cut=action.cut, rect=target), is_hidden=True)
        if not following.is_applied:
            break
        result = following
        done.append(following.summary)
    if len(done) == 1:
        return result

    return IllustrationActionResult(text=f"The words stood on {len(done)} lines, each masked: {' '.join(done[:-1])} "
                                         f"{result.text}", frame_path=result.frame_path)


def _run_undo_cut(context: IllustrationContext,
                  store: dict[str, Any],
                  action: BaseModel) -> IllustrationActionResult:
    """The newest cut still in use set aside: never placed, and its place freed (undo the last
    cut). The one way a wrong cut is set aside, capped by `budgets.undosPerFrame` per frame."""

    live = list(document_file_service.list_cuts_in_use(store))
    if not live:
        return _refuse("no cut is left to undo")
    # The cut is named where the worker means a particular one: undoing the newest across every frame threw away a
    # right cut of one frame while the worker meant to replace its cut of another
    named = action.cut
    # Handed back as a result, never refused: a refusal from a frame tool stops the run under `failOnFrameCutFail`
    if named and named not in live:
        return IllustrationActionResult(text=f"Nothing was undone: {named} is not a cut still in use. The cuts still "
                                             f"in use are {', '.join(sorted(live))}; name one of them, or call "
                                             f"`undo_cut` with no name for the newest.")
    chosen = named or max(live, key=document_text_utils.read_cut_number)
    frame = store["cuts"][chosen]["frame"]
    undone = sum(1 for cut in store["cuts"].values() if cut["frame"] == frame and cut.get("discarded") == output_contract.UNDO_DISCARD_REASON)
    if undone >= app_config.settings.budgets.undos_per_frame:
        return _refuse(f"{frame}'s cuts were undone {undone} times, the most a frame allows: place {chosen} or another of its "
                       f"cuts, or call `leave_out` for the frame")
    store["cuts"][chosen]["discarded"] = output_contract.UNDO_DISCARD_REASON

    return IllustrationActionResult(text=f"{chosen} undone; it is never placed and no longer counts toward the cuts of "
                                         f"{frame}, which may be cut again.")


def _list_lines_left_out(record: dict[str, Any]) -> list[str]:
    """The quoted lines of two words or more a frame's outline gives, its title and logo left out, of whose words the
    frame's notes' diagram holds under half."""

    diagram_words = set(_DIAGRAM_WORD.findall(str(record.get("diagram") or "").lower()))
    left_out = []
    for line in str(record.get("screen") or "").splitlines():
        if _TITLE_OR_LOGO.search(line):
            continue
        for text in _QUOTED_TEXT.findall(line):
            words = _DIAGRAM_WORD.findall(text.lower())
            if len(words) >= 2 and 2 * sum(word in diagram_words for word in words) < len(words):
                left_out.append(text)

    return left_out


def _record_frame_decision(records: list[dict[str, Any]],
                           frame: str,
                           reason: str,
                           chapter: str | None) -> None:
    """`frame`'s decision in this chapter's follow-up, with its reason, in `records`: an earlier record of the same
    frame and chapter is replaced, so the newer reason is the one kept."""

    records[:] = [record for record in records if record.get("frame") != frame or record.get("chapter") != chapter]
    records.append({"frame": frame, "reason": reason.strip(), "chapter": chapter})


def _run_leave_out(context: IllustrationContext,
                   store: dict[str, Any],
                   action: BaseModel) -> IllustrationActionResult:
    if action.frame not in context.frames or not action.reason.strip():
        return _refuse("name a frame that exists, and say in one sentence why it is left out")
    # A needed figure of a PDF is placed as its cut: left out because the notes hold its diagram, the wiki's Calvin
    # cycle was the replacement this tool refuses by another name. Handed back as a result, never refused
    item = context.frames.get(action.frame) or {}
    is_needed = any(entry["importance"] == frame_record.SCREENSHOT_NEEDED
                    for entry in screenshot_suggestion_utils.read_suggestions(item.get("record") or {}))
    # A figure whose notes hold it as a markdown block (a table, code) is carried whole by that block, which the request
    # asks to be written and its frame never cut: refused, a table went into a chapter three times (DEFECTS 517)
    markdown = str((item.get("record") or {}).get("as_markdown") or "").strip()
    is_written_as_markdown = bool(markdown) and markdown != frame_record.EMPTY_MARK
    if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM and is_needed and not is_written_as_markdown:
        return IllustrationActionResult(
            text=(f"{_NOTHING_RECORDED}: {action.frame} is a figure of the document the reader needs, and it is placed as "
                  f"its cut: `cut` {action.frame} with no box and no rectangle and place the cut; a Mermaid block may "
                  f"stand beside it under `#### Simplified view`."))
    # A frame left out again in the same follow-up keeps one record, the newer reason, as a diagram replacement does
    _record_frame_decision(store["leftOut"], action.frame, action.reason, context.chapter)

    return IllustrationActionResult(text=f"{action.frame} recorded as left out.")


def _run_replace_with_diagram(context: IllustrationContext,
                              store: dict[str, Any],
                              action: BaseModel) -> IllustrationActionResult:
    if action.frame not in context.frames or not action.reason.strip():
        return _refuse("name a frame that exists, and say what the complete diagram preserves")
    # A PDF's figure is the document's own picture, placed as its cut: redrawn, the wiki's Calvin cycle lost its
    # molecules and its cycle its return arrows. Handed back as a result, never refused
    if (context.frames.get(action.frame) or {}).get("medium") == output_contract.PDF_FIGURE_MEDIUM:
        return IllustrationActionResult(
            text=(f"{_NOTHING_RECORDED}: {action.frame} is a figure of the document, and a figure is placed as its cut, "
                  f"never replaced: `cut` {action.frame} with no box and no rectangle and place the cut; a Mermaid block "
                  f"may stand beside it under `#### Simplified view`."))
    if action.frame not in store["looks"]:
        # Handed back with the look and nothing recorded, as a cut before a look is: it was refused, and under
        # failOnFrameCutFail the refusal stopped the run on a chapter's first call
        shown = _run_look(context, store, illustration_actions.LookAction(frame=action.frame))

        return IllustrationActionResult(
            text=(f"{_NOTHING_RECORDED}: you had not looked at {action.frame}, and a diagram replaces a screenshot only "
                  f"when it keeps everything the frame shows. Here is the look; check the diagram against it, then call "
                  f"`replace_with_diagram` again or cut the frame.\n{shown.text}"),
            frame_path=shown.frame_path)
    # A diagram of more than ten arrows never replaces its screenshot: a writer replaced a 20-arrow slide with phase 3's
    # diagram, about half of whose arrows were wrong, though write.md forbade it. Handed back as a result, never refused
    diagram = str(((context.frames.get(action.frame) or {}).get("record") or {}).get("diagram") or "")
    arrows = mermaid_text_utils.count_arrows(diagram)
    if arrows > mermaid_text_utils.MOST_TRUSTED_ARROWS:
        return IllustrationActionResult(
            text=(f"{_NOTHING_RECORDED}: the notes' diagram of {action.frame} has {arrows} arrows, and a diagram of more "
                  f"than {mermaid_text_utils.MOST_TRUSTED_ARROWS} never replaces its screenshot. `cut` {action.frame} and place the cut; "
                  f"a Mermaid block may stand beside it under `#### Simplified view` only for arrows you checked."))
    # Nor does one that leaves out lines the frame shows: a license slide's lists of what each kind of work allows stood
    # under its boxes, and replaced by the notes' diagram of its seven arrows they reached no part of the document (38c3
    # talk 797 chapter 7, DEFECTS 547). Handed back as a result, never refused
    left_out = _list_lines_left_out((context.frames.get(action.frame) or {}).get("record") or {})
    if left_out:
        return IllustrationActionResult(
            text=(f"{_NOTHING_RECORDED}: {action.frame} shows lines its notes' diagram does not hold: "
                  f"{'; '.join(repr(line) for line in left_out)}. `cut` {action.frame} and place the cut; a Mermaid "
                  f"block may stand beside it under `#### Simplified view`."))
    # A store written before diagram replacements were kept has no such list (`leftOut` is older and always there)
    _record_frame_decision(store.setdefault("diagramReplacements", []), action.frame, action.reason, context.chapter)

    return IllustrationActionResult(text=f"{action.frame} recorded as replaced by a complete diagram.")


# tool name -> what carries it out. One row per row of `schemas/illustration_actions.ACTIONS`.
RUNNERS: dict[str, Callable[[IllustrationContext, dict[str, Any], BaseModel], IllustrationActionResult]] = {
    "look": _run_look,
    "cut": _run_cut,
    "mark": _run_mark,
    "mask": _run_mask,
    "leave_out": _run_leave_out,
    "undo_cut": _run_undo_cut,
    "replace_with_diagram": _run_replace_with_diagram,
}


def build_working_dir(output_root: Path) -> Path:
    return output_root / output_contract.IMAGE_WORK_DIR_NAME


def mark_looks_seen(store: dict[str, Any]) -> None:
    """Record in `store`, as a turn's calls start, the frames whose look the worker has seen: a look's picture reaches
    it only with its turn's results, so a cut asked in the same turn is named blind (DEFECTS 363)."""

    store[_LOOKS_SEEN] = sorted(store.get("looks") or {})


def clear_last_call(store: dict[str, Any]) -> None:
    """Remove from `store` the call the same `look` is pointed back to: a new conversation or follow-up never saw its
    result, and told "its boxes are listed in that result" it had no boxes to cut by."""

    store.pop("lastCall", None)


def build_empty_store(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """The frame tools' store before any tool ran: the document's blocks, and no look, cut or decision yet."""

    return {"schemaVersion": 1, "blocks": blocks, "looks": {}, "cuts": {}, "nextCut": 1, "leftOut": [],
            "diagramReplacements": [], "removedUnreferenced": []}


def run_action(context: IllustrationContext,
               store: dict[str, Any],
               name: str,
               arguments: dict[str, Any]) -> IllustrationActionResult:
    """Carry out one tool call. An unknown tool or arguments that do not fit its schema are refused, not raised."""

    schema = illustration_actions.ACTIONS.get(name)
    if schema is None:
        return _refuse(f"there is no tool {name!r}; the tools are {', '.join(illustration_actions.ACTIONS)}")
    arguments = _name_frame_by_slug(context, arguments or {})
    try:
        action = schema.model_validate(arguments)
    except ValidationError as exc:
        return _refuse(f"the arguments do not fit {name}: {exc.errors()[0].get('msg', exc)}")
    # The same look twice in a row shows nothing new: a worker re-planning each turn from its first step looked at one
    # frame five times running, never getting to its cut. It is pointed at the result it has instead.
    call = [name, json.dumps(arguments or {}, sort_keys=True)]
    if name == "look" and store.get("lastCall") == call:
        return IllustrationActionResult(text=f"{action.frame} is what your last call looked at, and its boxes are listed in "
                                             f"that result: go on with the next step, such as `cut` on {action.frame} or a "
                                             f"`look` at the next frame you need")
    try:
        result = RUNNERS[name](context, store, action)
    except Exception as exc:  # noqa: BLE001 (a failing action is a refusal the agent can work around, not a dead run)
        # The agent is told the failure in a sentence; the traceback stays in the log for whoever fixes the program
        _LOG.exception("frame tool %s failed", name)
        result = _refuse(f"the program failed on this call ({type(exc).__name__}: {exc}); try it another way")
    # Only a call carried out is one to point back to: a refused look listed no boxes
    store["lastCall"] = None if result.is_refused else call

    return result
