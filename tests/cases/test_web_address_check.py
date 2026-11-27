"""A frame's web addresses are held to what the frame shows and what was said over it (DEFECTS 255): an address the
model carried from the batch's other slides onto a frame that does not show it is dropped as the notes are written.
The text recognizer is a stand-in returning set words, so the check's own path runs."""

from __future__ import annotations

import cv2
import numpy

from support import fixtures as fx
from support import harness
from models import output_contract
from services import apple_text_service
from services import phase_file_service
from services import web_address_check_service

ADDRESS = {"url": "https://acmesync.example.org/gateway", "what_it_is": "the Gateway's page",
           "how_it_appears": "at the bottom right of the slide"}


def _run(read: list[str] | None,
         spoken: str) -> list:
    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-000.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((720, 1280, 3), 250, numpy.uint8))
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-000", "index": 0, "path": "frames/frame-000.jpg", "transcriptCurrent": spoken}]})
    is_available, try_find = apple_text_service.is_available, apple_text_service.try_find_words_in_image
    apple_text_service.is_available = lambda: True
    apple_text_service.try_find_words_in_image = lambda image: (
        None if read is None else [{"text": text, "box": [0, 0, 1, 1], "line": 0} for text in read])
    try:
        return web_address_check_service.drop_unseen_web_addresses(paths, "frame-000", {"urls": [ADDRESS]})["urls"]
    finally:
        apple_text_service.is_available, apple_text_service.try_find_words_in_image = is_available, try_find


def test_an_address_is_kept_when_the_frame_could_not_be_read_defect():
    # A reading that timed out came back as no words, and an address a frame's code showed was dropped as shown nowhere
    # (Lecture 7 frame 8, DEFECTS 553). A frame that could not be read keeps every address
    harness.check("the address stays when the reading failed", _run(None, "") == [ADDRESS], "")


def test_an_address_neither_shown_nor_said_is_dropped_defect():
    # Defect: a title card's notes listed the address of the later slides' footer as shown on it; the card has none.
    # Fix: an address whose host the frame's reading and the speech both lack is dropped as the notes are written.
    harness.check("the address on no screen and in no speech goes", _run(["AcmeSync", "Gateway", "Jonas"],
                                                                         "AcmeSync opens the Gateway") == [])
    harness.check("one the frame shows stays", _run(["see", "acmesync.example.org"], "") == [ADDRESS])
    harness.check("one the speaker says stays", _run([], "go to acmesync dot example dot org") == [ADDRESS])
