"""Phase 2: each kept frame's words read on the recording at its own size, when the stored frame is a shrunk copy of it
(DEFECTS 41, 69).

Phase 2 stores a frame at most 1280 pixels wide. A shared screen recorded at 2,676 pixels and stored at 1,185 shrinks a
code font below what the text reader reads: on a rendered table of ten field names in a small monospace font, it read
all ten at the recording's size, five at 1,185 and seven at 1,280; on Lecture 9's terminals, shrunk from 1,920, it read
1,628 words at full size where it read 1,506 on the stored frames. Phase 3's outline check and its completion can only
quote what the reader reads, so the words are read here, where the recording is at hand, and kept on the frame's item as
`screenWords`, in the stored frame's pixels.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2

from models.video_errors import UnreadableVideo
from services import recording_parts_service
from services import text_detection_service

__all__ = ["add_screen_words", "MIN_SHRINK", "LEAST_READ_WIDTH"]

_LOG = logging.getLogger(__name__)

# A frame stored at least this many times smaller than the recording's part it shows is read again at full size: at
# 1.5 (Lecture 9) the reader found 8% more words, at 2.26 (a screen recording) twice the field names
MIN_SHRINK = 1.3
# A frame whose part of the recording is narrower than this, the width phase 3 sends a small frame at
# (`framesProcessing.minSentFrameWidth`), is read enlarged to it: an SD broadcast stored 720 wide, its dialog's text
# about 9 pixels high, gave the reader 21 words of the frame, 3 lines of the dialog, and 49 words read twice as large,
# the dialog's results among them; the outline left the dialog out and nothing asked for it (38c3-openpv frame 17,
# DEFECTS 622)
LEAST_READ_WIDTH = 1280


def _read_item_words(capture: recording_parts_service.RecordingContext,
                     item: dict,
                     stored_width: int) -> list[dict] | None:
    """The words of the recording's `box` at `item`'s frame number, in the stored frame's pixels, or None when the
    recording has no such frame."""

    if not capture.move_to_frame(int(item["frameNumber"])):
        return None
    is_read, full = capture.read_frame()
    if not is_read or full is None:
        return None
    left, top, right, bottom = item["box"]
    part = full[top:bottom, left:right]
    # A part narrower than the width phase 3 sends a frame at is read enlarged at least to it, by a whole factor: the
    # 720-pixel dialog enlarged 1.78 times gave 16 words, twice 47
    if part.shape[1] < LEAST_READ_WIDTH:
        scale = -(-LEAST_READ_WIDTH // part.shape[1])
        part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    factor = part.shape[1] / stored_width
    words = text_detection_service.find_words_in_image(part, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)

    return [{**word, "box": [int(value / factor) for value in word["box"]]} for word in words]


def add_screen_words(items: list[dict],
                     frames_dir: Path,
                     video_path: Path) -> list[dict]:
    """`items` with `screenWords` on each frame stored at least MIN_SHRINK times smaller than the part of the recording
    its `box` shows; a board frame, whose text its own reader read (`boardWords`), and a frame the recording cannot give
    are left as they are."""

    if not text_detection_service.is_available():
        return items
    try:
        capture = recording_parts_service.open_recording(video_path)
    except UnreadableVideo as error:
        _LOG.warning("the screen words are read on the stored frames: the recording could not be read (%s)", error)
        return items
    added = []
    try:
        for item in items:
            stored = cv2.imread(str(frames_dir / Path(item["path"]).name), cv2.IMREAD_GRAYSCALE)
            box = item.get("box")
            is_shrunk = (stored is not None and box and item.get("frameNumber") is not None
                         and ((box[2] - box[0]) >= MIN_SHRINK * stored.shape[1] or (box[2] - box[0]) < LEAST_READ_WIDTH))
            words = _read_item_words(capture, item, stored.shape[1]) if is_shrunk and not item.get("boardWords") else None
            added.append(item if words is None else {**item, "screenWords": words})
    finally:
        capture.release()

    return added
