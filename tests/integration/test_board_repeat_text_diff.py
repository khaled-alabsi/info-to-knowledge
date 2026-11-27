"""A board frame marked a repeat of another board frame names, in its message, the text that differs from that one
(`board_frame_service._add_repeat_text_diffs`, DEFECTS 68): drawn ticket panels read by the real board reader, a twin
ticket whose status and assignee differ, and a true repeat that names nothing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness

from services import board_frame_service
from services import board_text_reader_service

FIELDS = (("Ticket:", "ACME-1042"), ("Status:", "In Progress"), ("Assignee:", "Jonas Berger"), ("Priority:", "Major"))


def _draw_ticket(fields: tuple[tuple[str, str], ...]) -> numpy.ndarray:
    image = numpy.full((720, 1280, 3), 250, numpy.uint8)
    cv2.putText(image, "AcmeSync Board", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (30, 30, 30), 2, cv2.LINE_AA)
    for column in range(3):
        for card in range(4):
            x0, y0 = 40 + column * 200, 120 + card * 130
            image[y0:y0 + 100, x0:x0 + 170] = 225
    image[:, 690:692] = 160
    for row, (label, value) in enumerate(fields):
        y = 160 + row * 80
        cv2.putText(image, label, (730, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (60, 60, 60), 2, cv2.LINE_AA)
        cv2.putText(image, value, (930, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)

    return image


def _draw_other_board() -> numpy.ndarray:
    # a dark backlog view: another screen, over 40% of its blocks changed against a ticket
    image = numpy.full((720, 1280, 3), 35, numpy.uint8)
    for row in range(8):
        cv2.putText(image, f"Sprint item {row + 1} refund rules", (40, 80 + row * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (230, 230, 230), 2, cv2.LINE_AA)

    return image


def test_a_twin_ticket_marked_a_repeat_names_the_fields_that_differ_defect():
    # Another ticket in the same panel passed as a repeat and its notes said "same as"; the board stage now reads what
    # differs from the frame it repeats, and a true repeat names nothing
    board_text_reader_service.set_reader(board_text_reader_service.AUTO)
    twin_fields = ((FIELDS[0][0], "ACME-1047"), (FIELDS[1][0], "Done"), FIELDS[2], FIELDS[3])
    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    images = [_draw_ticket(FIELDS), _draw_other_board(), _draw_ticket(twin_fields), _draw_other_board(), _draw_ticket(FIELDS)]
    items = []
    for index, image in enumerate(images):
        cv2.imwrite(str(frames_dir / f"frame-{index:03d}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        items.append({"slug": f"frame-{index:03d}", "index": index, "frameNumber": index * 30, "timestamp": float(index),
                      "path": f"frames/frame-{index:03d}.jpg", "tag": "board"})
    items[2]["repeatOf"], items[4]["repeatOf"] = "frame-000", "frame-000"
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, "text-diff-integration", None)
    by_slug = {item["slug"]: item for item in kept}
    changes = (by_slug["frame-002"].get("boardTextDiff") or {}).get("changes") or []
    read = " ".join(f"{change['old']} {change['text']}" for change in changes)
    # the key's one changed digit reads as a misread letter, which the text diff allows by design; the status is named
    harness.check("the twin names its new status against the frame it repeats",
                  (by_slug["frame-002"].get("boardTextDiff") or {}).get("against") == "frame-000"
                  and "Done" in read and "In Progress" in read, str(changes))
    harness.check("a true repeat names nothing", not by_slug["frame-004"].get("boardTextDiff"),
                  str(by_slug["frame-004"].get("boardTextDiff")))
