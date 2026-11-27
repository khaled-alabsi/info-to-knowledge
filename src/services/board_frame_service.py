"""Phase 2's board stage: the frames the frame classifier tags `board` (a task board or a ticket view, Jira and alike)
are handled by the program, with no model call. Each is compared with the kept frame before it,
when that one is a board too, at the frames' own size and counted in the capture loop's grid of blocks:

- under DROP_CHANGE of the blocks changed: nothing new, so it is dropped (`droppedBy: "board"`, covered by that frame);
- under SAME_BOARD_CHANGE: the same board with a part changed (a ticket opened beside it, a panel scrolled), so it is a
  step of the board's first frame (`stepOf`), phase 3 batches the run and notes only what each step adds, and it
  carries `boardChange`, the box of the blocks that changed, in the frame's pixels, which phase 3 sends cut out and
  enlarged when it covers at most half the frame;
- a change lying wholly in the CHROME_ROWS at the top or bottom (an address bar, a clock) is dropped as the same board;
- otherwise another board, kept whole.

With `text-diff-integration` (`frames.boardHandler`) a step also carries `boardTextDiff`, what changed between the text
the reader finds on it and on the board frame before it (`board_text_diff_utils`), which phase 3 lists beside the whole
frame.

Every board frame kept gets `isBoard`. On the 5 board recordings measured, 190 board frames after a board frame changed
a median 9.3% of the blocks, 3 at most 1%, and the box round their change covered a median 26% of the screen.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy

from services import knowledge_filter_service
from services import recording_frame_service
from services import board_text_reader_service
from utils import board_text_diff_utils
from utils import change_capture_utils

__all__ = ["BOARD", "IMAGE_DIFF_HANDLER", "TEXT_DIFF_HANDLER", "TEXT_ONLY_HANDLER", "TEXT_HANDLERS", "read_gray_frame",
           "handle_board_frames"]

BOARD = "board"
IMAGE_DIFF_HANDLER = "image-diff-only"
TEXT_DIFF_HANDLER = "text-diff-integration"
TEXT_ONLY_HANDLER = "text-only-integration"
TEXT_HANDLERS = (TEXT_DIFF_HANDLER, TEXT_ONLY_HANDLER)
# A ticket shows its fields by label ("Status:", "Assignee:", "Bearbeiter:"); a board or a backlog list shows almost
# none. With text-only-integration a ticket keeps its image: in an offline measurement no reader kept a ticket panel's small
# tables or its rows of linked tickets, while a board's columns and a list's rows were read right
TICKET_FIELD_LABELS = 4
DROP_CHANGE = 0.01                          # a board frame changed in fewer blocks than this shows nothing new
SAME_BOARD_CHANGE = 0.40                    # under this, the same board with a part changed: a step
# rows of blocks at the window's top and bottom (the address and tab bars, the taskbar's clock): a patch wholly within
# them is left out of the box; every other changed block counts, however small its patch: patches under 3 blocks were left out as the pointer, and a
# ticket's date changed in 2 blocks fell outside the box (round 3 audit); a box widened by the pointer costs at most a
# frame sent whole (8 more of 139 steps on the board recordings), a change left out is never seen
CHROME_ROWS = 2
MARGIN_BLOCKS = 1                           # the change's box is widened by this many blocks on each side
PIXEL_CHANGE = 40                           # grey levels a pixel must move to count as changed, as in the capture loop
SHIFT_PX = 1                                # a pixel matched this near in the other frame has not moved
BLOCK_MIN_SHARE = 0.02                      # a block changed when this share of its pixels moved
# the change box counts a block from this share: cards moved by one place keep their layout, so a few letters differ,
# 0.2% to 1.1% of a block, and a real board's first column fell outside the box; every block it added on the board
# recordings was a real change (a ticket's key, a selected row)
BOX_BLOCK_MIN_SHARE = 0.002
# A frame the classifier scores at least this for board, beside a frame tagged board, is a board too (chained): 11 of
# 199 real board frames scored 0.61 to 0.83 against its bar of 0.84, each beside a board frame (a ticket page, a board
# beside a call's settings panel); at 0.6 all 199 were found, and 6 non-boards beside boards taken, which the stage
# cannot lose. Retrained, the classifier scored a real board 0.5965 beside
# board frames; over the board recordings' 290 board frames the lowest is 0.5965 and over the 7 non-boards beside one
# the highest 0.4712, so the bar stands between (DEFECTS 305)
NEIGHBOUR_SCORE = 0.55


def _read_stored_frame(frames_dir: Path,
                       item: dict,
                       flags: int) -> numpy.ndarray:
    """A frame's image as phase 2 wrote it, read with `flags`; refused by name when it cannot be read, where OpenCV
    gives None and the caller failed later on its size with no file named."""

    path = frames_dir / Path(item["path"]).name
    image = cv2.imread(str(path), flags)
    if image is None:
        raise FileNotFoundError(f"phase 2 cannot read the frame image {path}")

    return image


def read_gray_frame(frames_dir: Path,
                    item: dict) -> numpy.ndarray:
    """A frame's image in grey, at the size phase 2 wrote it."""

    return _read_stored_frame(frames_dir, item, cv2.IMREAD_GRAYSCALE)


def _map_moved_pixels(gray: numpy.ndarray,
                      before: numpy.ndarray) -> numpy.ndarray:
    """The pixels that moved by more than PIXEL_CHANGE between the two frames, compared at the frames' own size with
    SHIFT_PX allowed."""

    if before.shape != gray.shape:
        before = cv2.resize(before, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)
    near = numpy.ones((2 * SHIFT_PX + 1,) * 2, numpy.uint8)
    one, other = gray.astype(numpy.int16), before.astype(numpy.int16)
    moved = numpy.zeros(gray.shape, bool)
    for first, second in ((one, other), (other, one)):
        moved |= first > cv2.dilate(second, near) + PIXEL_CHANGE
        moved |= first < cv2.erode(second, near) - PIXEL_CHANGE

    return moved


def _map_moved_shares(moved: numpy.ndarray) -> numpy.ndarray:
    """Per block of the capture loop's grid laid over the frame, the share of its pixels that moved. Compared shrunk to
    the capture loop's 320 x 180, a ticket panel showing another ticket in the same layout read as almost unchanged: its
    lines blur to the same grey bars, and the change box held only their ends (DEFECTS 120)."""

    rows, columns = change_capture_utils.BLOCK_ROWS, change_capture_utils.BLOCK_COLUMNS

    return cv2.resize(moved.astype(numpy.float32), (columns, rows), interpolation=cv2.INTER_AREA)


def _find_change_box(changed: numpy.ndarray,
                     size: tuple[int, int]) -> list[int] | None:
    """The box round the changed blocks, a patch lying wholly within the CHROME_ROWS at the top or the bottom left out,
    widened by MARGIN_BLOCKS, as x0, y0, x1, y1 in the frame's pixels; None when no patch is left. A patch reaching
    into a band from above counts whole: a list's last rows run down to the frame's bottom."""

    count, labels, stats, _ = cv2.connectedComponentsWithStats(changed.astype(numpy.uint8), connectivity=8)
    last_row = changed.shape[0] - CHROME_ROWS
    patches = [label for label in range(1, count)
               if stats[label, cv2.CC_STAT_TOP] + stats[label, cv2.CC_STAT_HEIGHT] > CHROME_ROWS
               and stats[label, cv2.CC_STAT_TOP] < last_row]
    if not patches:
        return None
    rows, columns = numpy.nonzero(numpy.isin(labels, patches))
    block_rows, block_columns = changed.shape
    width, height = size
    x0 = max(0, int(columns.min()) - MARGIN_BLOCKS) * width // block_columns
    x1 = min(block_columns, int(columns.max()) + 1 + MARGIN_BLOCKS) * width // block_columns
    y0 = max(0, int(rows.min()) - MARGIN_BLOCKS) * height // block_rows
    y1 = min(block_rows, int(rows.max()) + 1 + MARGIN_BLOCKS) * height // block_rows

    return [x0, y0, x1, y1]


def _mark_board_neighbours(items: list[dict]) -> list[dict]:
    """`items` with every frame scored at least NEIGHBOUR_SCORE for board (`boardScore`) and standing beside a frame
    tagged board retagged board and marked `isBoardByNeighbour`, again until no frame changes, so a run of them
    beside one board frame is taken whole."""

    items = [dict(item) for item in items]
    is_changed = True
    while is_changed:
        is_changed = False
        for index, item in enumerate(items):
            if item.get("tag") == BOARD or float(item.get("boardScore") or 0.0) < NEIGHBOUR_SCORE:
                continue
            beside = items[max(0, index - 1):index] + items[index + 1:index + 2]
            if any(other.get("tag") == BOARD for other in beside):
                item.update(tag=BOARD, isBoardByNeighbour=True)
                is_changed = True

    return items


def _read_board_words(item: dict,
                      box: list[int],
                      frames_dir: Path,
                      video_path: Path | None) -> list[dict]:
    """The words inside `box` (the stored frame's pixels) of one board frame, in those pixels: read on the recording at
    its own size when it can be read, where the frame shows the recording's `box`, else on the stored frame. Read on a
    screen recording's stored frame, 1280 wide of 2962, the reader read one ticket panel differently on two frames; on
    the recording, the same panel the same way, and the change it showed (a drop-down opened) whole."""

    x0, y0, x1, y1 = box
    stored = _read_stored_frame(frames_dir, item, cv2.IMREAD_COLOR)
    is_recording_readable = video_path is not None and item.get("box") and item.get("frameNumber") is not None
    full = (recording_frame_service.read_recording_frame(video_path, int(item["frameNumber"]))
            if is_recording_readable else None)
    if full is None:
        return board_text_reader_service.find_words_in_image(stored[y0:y1, x0:x1])
    left, top, right, _ = item["box"]
    factor = (right - left) / stored.shape[1]
    part = full[top + int(y0 * factor):top + int(y1 * factor), left + int(x0 * factor):left + int(x1 * factor)]

    return [{**word, "box": [int(value / factor) for value in word["box"]]}
            for word in board_text_reader_service.find_words_in_image(part)]


def _read_whole_board(item: dict,
                      frames_dir: Path,
                      video_path: Path | None) -> list[dict]:
    """Every word of a board frame, `{"text", "box", "line"}` in the stored frame's pixels, read whole by the board
    reader on the recording at its own size."""

    stored = _read_stored_frame(frames_dir, item, cv2.IMREAD_COLOR)

    return _read_board_words(item, [0, 0, stored.shape[1], stored.shape[0]], frames_dir, video_path)


def _add_board_text(item: dict,
                    frames_dir: Path,
                    video_path: Path | None) -> dict:
    """The board item with its whole text (`boardWords`) and whether it goes as that text alone (`isBoardTextOnly`): a
    board or a list does, a ticket (TICKET_FIELD_LABELS labelled fields or more) keeps its image beside its text."""

    words = _read_whole_board(item, frames_dir, video_path)
    labels = sum(1 for word in words if len(word["text"]) > 2 and word["text"].endswith(":"))

    return {**item, "boardWords": words, "isBoardTextOnly": bool(words) and labels < TICKET_FIELD_LABELS}


def _compare_board_text(item: dict,
                        previous: dict,
                        frames_dir: Path,
                        video_path: Path | None,
                        moved: numpy.ndarray) -> dict:
    """What changed in the text from the board frame before to this step, both read inside the step's `boardChange`,
    the part phase 2 found changed (the reader then sees one region twice), a line counting only where its pixels
    changed (`moved`, the stored frame's pixels)."""

    x0, y0, x1, y1 = box = item["boardChange"]
    words = [_read_board_words(board_item, box, frames_dir, video_path) for board_item in (previous, item)]

    return {"against": previous["slug"], **board_text_diff_utils.compare_board_words(*words, moved[y0:y1, x0:x1])}


def _drop_as_same_board(frames_dir: Path,
                        item: dict,
                        previous: dict,
                        why: str) -> dict:
    """The board frame's entry for `droppedFrames`, its image moved into `frames/dropped/`, as the board before it."""

    entry = knowledge_filter_service.move_to_dropped(frames_dir, item, None, BOARD)

    return {**entry, "coveredBy": [previous["slug"]], "reason": f"the same board as {previous['slug']}: {why}"}


def _add_repeat_text_diffs(kept: list[dict],
                           frames_dir: Path,
                           video_path: Path | None) -> list[dict]:
    """The kept items, each board frame marked `repeatOf` another kept board frame given `boardTextDiff` against it,
    read inside the box of the blocks that differ, when its text differs there."""

    by_slug = {item["slug"]: item for item in kept}
    added = []
    for item in kept:
        earlier = by_slug.get(item.get("repeatOf") or "")
        if not item.get("isBoard") or earlier is None or not earlier.get("isBoard") or item.get("boardTextDiff"):
            added.append(item)
            continue
        sample, earlier_sample = read_gray_frame(frames_dir, item), read_gray_frame(frames_dir, earlier)
        if sample.shape != earlier_sample.shape:
            added.append(item)
            continue
        moved = _map_moved_pixels(sample, earlier_sample)
        box = _find_change_box(_map_moved_shares(moved) >= BOX_BLOCK_MIN_SHARE, (sample.shape[1], sample.shape[0]))
        if box is None:
            added.append(item)
            continue
        diff = _compare_board_text({**item, "boardChange": box}, earlier, frames_dir, video_path, moved)
        added.append({**item, "boardTextDiff": diff} if diff.get("changes") else item)

    return added


def handle_board_frames(items: list[dict],
                        frames_dir: Path,
                        board_handler: str,
                        video_path: Path | None) -> tuple[list[dict], list[dict]]:
    """The kept items and the dropped ones, each dropped one `{"slug", "index", "frameNumber", "timestamp", "path",
    "dropScore", "droppedBy", "coveredBy", "reason"}` with its image moved into `frames/dropped/`. Reads each item's
    `tag`; a board frame kept gets `isBoard` (and `isBoardByNeighbour` when made one beside a board frame), a step of a
    board `stepOf` and `boardChange`, with a text handler (`TEXT_HANDLERS`) `boardTextDiff`, and with
    `TEXT_ONLY_HANDLER` every board frame `boardWords` and `isBoardTextOnly`, read on the recording at `video_path`
    when given."""

    # STEP 1: a frame just under the classifier's bar beside a board frame is a board
    items = _mark_board_neighbours(items)

    # STEP 2: each board frame against the kept frame before it, when that one is a board too
    kept, dropped = [], []
    before = None
    for item in items:
        if item.get("tag") != BOARD:
            kept.append(item)
            before = None
            continue
        sample = read_gray_frame(frames_dir, item)
        size = (sample.shape[1], sample.shape[0])
        if before is None:
            kept.append({**item, "isBoard": True})
            before = (kept[-1], sample)
            continue
        previous, previous_sample = before
        moved = _map_moved_pixels(sample, previous_sample)
        shares = _map_moved_shares(moved)
        changed = shares >= BLOCK_MIN_SHARE
        change = change_capture_utils.compute_change(changed, numpy.zeros(changed.shape, bool))
        if change < DROP_CHANGE:
            dropped.append(_drop_as_same_board(frames_dir, item, previous, f"{change:.1%} of it changed"))
            continue
        if change >= SAME_BOARD_CHANGE:
            kept.append({**item, "isBoard": True})
            before = (kept[-1], sample)
            continue
        box = _find_change_box(shares >= BOX_BLOCK_MIN_SHARE, size)
        # A change lying wholly in the window's top or bottom rows (an address bar, a tab bar, a taskbar) is no change of
        # the board: the step had no box, and the text handler stopped phase 2 on it
        if box is None:
            dropped.append(_drop_as_same_board(frames_dir, item, previous, "only the window's bars changed"))
            continue
        step = {**item, "isBoard": True, "stepOf": previous.get("stepOf") or previous["slug"], "boardChange": box}
        if board_handler in TEXT_HANDLERS:
            step["boardTextDiff"] = _compare_board_text(step, previous, frames_dir, video_path, moved)
        kept.append(step)
        before = (kept[-1], sample)

    # STEP 2b: a board frame marked a repeat of another board frame carries what its text changed against that one:
    # another ticket in the same panel, a few fields different, passed the capture loop's repeat test, and its notes
    # said "same as" the earlier ticket (DEFECTS 68). Its line counts only where its pixels changed, so a true repeat
    # lists nothing; the frame stays a repeat, read with the earlier showing's notes
    if board_handler in TEXT_HANDLERS:
        kept = _add_repeat_text_diffs(kept, frames_dir, video_path)

    # STEP 3: with text-only-integration every board frame kept carries its whole text, which phase 3 sends in place
    # of its image
    if board_handler == TEXT_ONLY_HANDLER:
        kept = [_add_board_text(item, frames_dir, video_path) if item.get("isBoard") else item for item in kept]

    # STEP 4: no kept frame points at a dropped one
    kept = knowledge_filter_service.build_relinked_frames(kept, {entry["slug"] for entry in dropped})

    return kept, dropped
