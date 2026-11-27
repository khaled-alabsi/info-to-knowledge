"""A model filter's removal is kept back when its holder's camera inset, covered by phase 2 with one flat colour, hid
line ends the removed frame shows (DEFECTS 89, its first case): the words of those ends stand elsewhere on the holder,
so the word check sees nothing missing, and the marks check finds them where the holder is blank. The frames are drawn
here, a terminal's lines in a monospace font, and read by the real text reader through the real keep-back check."""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness

from models.frame_judging_spec import FrameJudgingSpec
from services import frame_redundancy_service
from services import text_detection_service

LINES = ["$ pytest tests/test_refunds.py", "test_refund_window passed in 0.02 seconds",
         "test_refund_split passed in 0.03 seconds", "test_refund_gateway passed in 0.04 seconds",
         "test_refund_ledger passed in 0.02 seconds", "test_refund_rounding passed in 0.05 seconds",
         "test_refund_currency passed in 0.03 seconds", "7 passed in 0.19 seconds"]
# The lecturer's camera inset at mid height on the right, 320 x 180 as on the MIT lectures' 1280-pixel frames
INSET = (960, 250, 1280, 430)


def _draw_terminal(path: Path,
                   is_inset_covered: bool,
                   is_bars: bool = False,
                   is_camera_left: bool = False) -> None:
    """The terminal's lines; with `is_bars`, each test line short and ending in a progress bar, which holds no word."""

    image = numpy.full((720, 1280, 3), 24, numpy.uint8)
    for index, line in enumerate(LINES):
        text = " ".join(line.split()[:2]) if is_bars and 0 < index < len(LINES) - 1 else line
        cv2.putText(image, text, (40, 80 + index * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (230, 230, 230), 2, cv2.LINE_AA)
        if is_bars and 0 < index < len(LINES) - 1:
            cv2.rectangle(image, (760, 58 + index * 70), (1240, 84 + index * 70), (90, 200, 90), -1)
    if is_inset_covered:
        # phase 2's cover of the lecturer's camera inset at mid height on the right: one flat colour
        cv2.rectangle(image, INSET[:2], INSET[2:], (60, 60, 60), -1)
    if is_camera_left:
        # the lecturer's camera as recorded, before phase 2 covered it: a picture with texture, never blank
        # (large shapes: fine noise shrinks to one grey at the size the checks compare)
        left, top, right, bottom = INSET
        rows = numpy.arange(bottom - top)[:, None]
        room = (110 + 80 * numpy.sin(rows / 9.0) * numpy.ones((1, right - left))).astype(numpy.uint8)
        image[top:bottom, left:right] = cv2.cvtColor(room, cv2.COLOR_GRAY2BGR)
    cv2.imwrite(str(path), image)


def test_a_removal_whose_holder_s_covered_inset_hid_line_ends_is_kept_back_defect():
    # A model filter removed a frame whose holder showed the same words but hid part of its content behind a camera
    # inset; with the inset covered flat, the marks check now keeps that removal back
    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    frames_dir = Path(tempfile.mkdtemp())
    removed, holder = {"path": "frames/frame-001.jpg"}, {"path": "frames/frame-000.jpg"}
    spec = FrameJudgingSpec(model="lfm-vl-3b", frame_width=640, dropped_by="redundancy", label="redundancy filter",
                            max_missing_words=2, min_words=8)
    _draw_terminal(frames_dir / "frame-001.jpg", is_inset_covered=False, is_bars=True)
    _draw_terminal(frames_dir / "frame-000.jpg", is_inset_covered=True, is_bars=True)
    reason = frame_redundancy_service._find_keep_back_reason(removed, [holder], frames_dir, spec)
    harness.check("line ends holding no word, only bars: kept back for the marks where the holder is blank",
                  reason == "marks added", f"reason {reason!r}")
    # Before phase 2 covered the inset (464532e), the holder's camera picture was no blank, and the removal stood
    _draw_terminal(frames_dir / "frame-000.jpg", is_inset_covered=False, is_bars=True, is_camera_left=True)
    reason = frame_redundancy_service._find_keep_back_reason(removed, [holder], frames_dir, spec)
    harness.check("with the camera picture left in the holder, the removal stands, as it did", reason is None,
                  f"reason {reason!r}")
