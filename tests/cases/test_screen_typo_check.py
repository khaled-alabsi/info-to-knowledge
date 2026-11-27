"""A frame's open question claiming a typo on the screen is held to what the frame shows (DEFECTS 348): a question built
on a misread of the screen is dropped as the notes are written. The text recognizer is a stand-in returning set words,
so the check's own path runs."""

from __future__ import annotations

import cv2
import numpy

from support import fixtures as fx
from support import harness
from models import output_contract
from services import apple_text_service
from services import phase_file_service
from services import screen_typo_check_service


def _run(read: list[str],
         questions: list[str],
         field: str = "open_questions",
         record: dict | None = None) -> list:
    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-000.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((720, 1280, 3), 250, numpy.uint8))
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-000", "index": 0, "path": "frames/frame-000.jpg"}]})
    is_available, try_find = apple_text_service.is_available, apple_text_service.try_find_words_in_image
    apple_text_service.is_available = lambda: True
    apple_text_service.try_find_words_in_image = lambda image: [{"text": text, "box": [0, 0, 1, 1], "line": 0}
                                                                for text in read]
    try:
        return screen_typo_check_service.drop_unread_typo_claims(paths, "frame-000", {**(record or {}), field: questions})[field]
    finally:
        apple_text_service.is_available, apple_text_service.try_find_words_in_image = is_available, try_find


def test_a_typo_question_built_on_a_misread_is_dropped_defect():
    # Defect: the model misread the slide's "Gateways" as "Gatewys" and asked whether the slide's typo was meant; the
    # question outlived the repair. Fix: a claimed misspelling the frame's reading lacks drops its question.
    misread = 'The slide contains a typo "Gatewys" instead of "Gateways"; is it intended?'
    true_typo = 'The slide misspells "refund" as "refnud".'
    heard = 'The term spoken as "Akme Sink" may be "AcmeSync".'
    harness.check("the misread's question goes, the true typo and the speech question stay",
                  _run(["Gateways", "refnud", "limits"], [misread, true_typo, heard]) == [true_typo, heard])


def test_a_fact_claiming_a_typo_the_frame_does_not_show_is_dropped_defect():
    # Defect: a fact said the footer reads "vorbehaltn", a misread of its small print, and only open questions were
    # held to the frame's reading. Fix: a fact or a take-away claiming a one-word misspelling is held the same way.
    misread = 'The footer has a visible typo "vorbehaltn" instead of "vorbehalten".'
    true_typo = 'The slide misspells "refund" as "refnud".'
    plain = "The gateway retries a refund three times."
    harness.check("the misread's fact goes, the true typo and the plain fact stay",
                  _run(["Rechte", "vorbehalten", "refnud"], [misread, true_typo, plain], "facts") == [true_typo, plain])
    heard = '- "checks for what the user typed in" → a strict match: typos break it.'
    harness.check("a take-away quoting the speech is no one-word claim and stays",
                  _run(["while"], [heard], "take_aways") == [heard])
    harness.check("a fact list is never emptied", _run(["vorbehalten"], [misread], "facts") == [misread])


def test_the_claimed_misspelling_is_the_text_after_as_or_the_first_quoted():
    harness.check("after \"as\"", screen_typo_check_service.read_claimed_misspelling(
        'The slide misspells "refund" as "refnud".') == "refnud")
    harness.check("the first quoted", screen_typo_check_service.read_claimed_misspelling(
        'A typo "Gatewys" instead of "Gateways".') == "Gatewys")
    harness.check("a question about the speech claims none", screen_typo_check_service.read_claimed_misspelling(
        '"Kleba" is likely a transcription error for "Chleba".') == "")
