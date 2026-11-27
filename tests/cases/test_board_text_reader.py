"""Which text reader the board stage reads a board's words with (`frames.boardTextReader`): Apple's recognizer on a Mac
that can run it, Tesseract everywhere else (the owner: the best method compatible with the machine)."""

from __future__ import annotations

from support import harness

from services import apple_text_service
from services import board_text_reader_service
from services import text_detection_service


def _choose(choice: str,
            is_apple_here: bool) -> str:
    original = apple_text_service.is_available
    apple_text_service.is_available = lambda: is_apple_here
    try:
        return board_text_reader_service.set_reader(choice)
    finally:
        apple_text_service.is_available = original
        board_text_reader_service.set_reader(board_text_reader_service.TESSERACT)


def test_auto_takes_apple_where_it_runs_and_tesseract_elsewhere():
    harness.check("auto on a Mac that runs it", _choose("auto", True) == board_text_reader_service.APPLE)
    harness.check("auto elsewhere", _choose("auto", False) == board_text_reader_service.TESSERACT)


def test_a_named_reader_is_used_and_apple_falls_back_where_it_cannot_run():
    harness.check("tesseract named on a Mac", _choose("tesseract", True) == board_text_reader_service.TESSERACT)
    harness.check("apple named where it cannot run", _choose("apple", False) == board_text_reader_service.TESSERACT)


def test_the_words_come_from_the_reader_in_use():
    calls = []
    original_apple, original_tesseract = apple_text_service.find_words_in_image, text_detection_service.find_words_in_image
    apple_text_service.find_words_in_image = lambda image: calls.append("apple") or []
    text_detection_service.find_words_in_image = lambda image, page_mode=None: calls.append(f"tesseract {page_mode}") or []
    original_available = apple_text_service.is_available
    apple_text_service.is_available = lambda: True
    try:
        board_text_reader_service.set_reader("auto")
        board_text_reader_service.find_words_in_image(None)
        board_text_reader_service.set_reader("tesseract")
        board_text_reader_service.find_words_in_image(None)
    finally:
        apple_text_service.find_words_in_image, text_detection_service.find_words_in_image = original_apple, original_tesseract
        apple_text_service.is_available = original_available
        board_text_reader_service.set_reader(board_text_reader_service.TESSERACT)
    harness.check("Apple's reader, then Tesseract reading one block", calls == ["apple", "tesseract 6"], str(calls))
