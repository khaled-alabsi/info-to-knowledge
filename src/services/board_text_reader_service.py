"""Which text reader phase 2's board stage reads a board's words with (`frames.boardTextReader`): Apple's recognizer
on a Mac that can run it, Tesseract (one block, `BOARD_PAGE_MODE`) everywhere else, or the one the setting names. With
`providers.ocr.url` set, the model server reads the words, unless the setting names Apple's recognizer. Set once per run
by `set_reader`, as `text_detection_service.set_recording_language` sets the language."""

from __future__ import annotations

import numpy

from services import apple_text_service
from services import endpoint_client
from services import text_detection_service

__all__ = ["set_reader", "describe_reader", "find_words_in_image", "APPLE", "TESSERACT", "AUTO", "SERVER"]

AUTO = "auto"
APPLE = "apple"
TESSERACT = "tesseract"
SERVER = "server"
# Tesseract reads a board's changed part as one block of text: in its own layout analysis it read 14 words of a
# ticket panel with a drop-down open and 38 of the same panel closed; as one block, 62 and 48
BOARD_PAGE_MODE = 6
_READER = TESSERACT


def set_reader(choice: str) -> str:
    """The reader `choice` comes to on this machine, kept for the run: "auto" is Apple's where it can run, else
    Tesseract; "apple" on a machine that cannot run it falls back to Tesseract. With a model server set for text, "auto"
    and "tesseract" come to the server, which reads in place of Tesseract; "auto" does not pick Apple's over it."""

    global _READER
    is_server_set = endpoint_client.find_endpoint(kind="ocr") is not None
    wants_apple = choice == APPLE or (choice == AUTO and not is_server_set)
    if wants_apple and apple_text_service.is_available():
        _READER = APPLE
    else:
        _READER = SERVER if is_server_set else TESSERACT

    return _READER


def describe_reader() -> str:
    """The reader in use, as the console names it."""

    return {APPLE: "Apple's text recognizer", SERVER: "the model server's text reader"}.get(_READER, "Tesseract")


def find_words_in_image(image: numpy.ndarray) -> list[dict]:
    """A board part's words, `{"text", "box", "line"}` in its pixels, read by the reader in use."""

    if _READER == APPLE:
        return apple_text_service.find_words_in_image(image)

    return text_detection_service.find_words_in_image(image, page_mode=BOARD_PAGE_MODE)
