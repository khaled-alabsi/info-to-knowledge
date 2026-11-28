"""The words the text reader finds on a frame, read from its tab-separated output.

A quote mark inside a word read on screen (`"Tenant`) opened a quoted field in the parser, which then swallowed the rows
after it into one word: a frame's box showed raw rows of numbers as its text (2026-09-29).
"""

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness

from services import text_detection_service

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
ROWS = ["5\t1\t2\t1\t1\t1\t10\t20\t30\t8\t91.0\t\"Tenant",
        "5\t1\t2\t1\t1\t2\t45\t20\t40\t8\t90.0\tAccount",
        "5\t1\t3\t1\t1\t1\t10\t60\t50\t8\t92.0\tPrelive"]


def test_a_frame_s_words_are_read_once_across_processes_with_a_words_cache_defect():
    # Defect: the integration tests read every frame of a real recording with Tesseract anew in each process, 1.3 to 5 s
    # a frame, and five files ran past five minutes (DEFECTS 499). Fix: with a cache folder set, the words are kept on
    # disk by the frame's content, and a later process (the memory cache empty) reads none again
    folder = Path(tempfile.mkdtemp())
    frame = folder / "frame-000.jpg"
    image = numpy.full((120, 400, 3), 255, numpy.uint8)
    cv2.putText(image, "Gateway", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    cv2.imwrite(str(frame), image)
    reads = []
    original = text_detection_service._read_words_in_image

    def counting(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    text_detection_service._read_words_in_image = counting
    previous_cache = text_detection_service._WORDS_CACHE_DIR
    text_detection_service.set_words_cache_dir(folder / "cache")
    try:
        first = text_detection_service.find_words(frame, 60)
        text_detection_service._WORDS_BY_FILE.clear()
        second = text_detection_service.find_words(frame, 60)
    finally:
        text_detection_service._read_words_in_image = original
        # the suite's own words cache, given back so every later case file reads through it
        text_detection_service.set_words_cache_dir(previous_cache)
    harness.check("read once, the second process takes the kept words", len(reads) == 1, str(len(reads)))
    harness.check("the same words both times", bool(second) and first == second, str(second))


def test_a_quote_mark_in_a_word_is_read_as_part_of_the_word():
    words = text_detection_service.read_tsv_words("\n".join([HEADER, *ROWS]))
    harness.check("three words, each its own row, the quote kept in its word",
                  [word["text"] for word in words] == ['"Tenant', "Account", "Prelive"], str(words))


def test_small_screen_text_is_read_whole_and_boxed_in_the_frames_own_pixels():
    """Small interface text was read as almost nothing: one word on a cut of a settings page, so a mask over any of its
    lines looked like a mask over unreadable text (2026-09-30). The reader works on a doubled image."""

    import tempfile
    import cv2
    import numpy
    if not text_detection_service.is_available():
        harness.check("skipped: the text reader is not installed", True)
        return
    page = numpy.full((60, 400, 3), 255, dtype=numpy.uint8)
    cv2.putText(page, "Refund limit 3000", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (40, 40, 40), 1, cv2.LINE_AA)
    path = tempfile.mktemp(suffix=".png")
    cv2.imwrite(path, page)
    words = text_detection_service.find_words(path)
    harness.check("every word of a small line is read", [word["text"] for word in words] == ["Refund", "limit", "3000"],
                  str(words))
    harness.check("and boxed in the image's own pixels", all(word["box"][2] <= 400 and word["box"][3] <= 60 for word in words),
                  str(words))


def test_a_mask_is_painted_as_the_background_before_reading():
    """On a dark cut with a black mask, Tesseract read no word at all, the unmasked lines included (2026-09-30), so the
    secret check saw nothing left: a mask's pure black is painted in the image's own background first."""

    import numpy
    image = numpy.full((40, 100, 3), 30, dtype=numpy.uint8)
    image[5:10, 5:60] = 200
    image[20:35, 0:100] = 0
    painted = text_detection_service.fill_masks_with_background(image)
    harness.check("the mask takes the background's colour", int(painted[25, 50, 0]) == 30, str(painted[25, 50]))
    harness.check("the text keeps its colour", int(painted[7, 10, 0]) == 200)
    dark = numpy.zeros((40, 100, 3), dtype=numpy.uint8)
    dark[5:10, 5:60] = 200
    harness.check("an image whose background is black is left as it is",
                  int(text_detection_service.fill_masks_with_background(dark)[25, 50, 0]) == 0)


def test_a_word_below_the_confidence_floor_is_kept_when_asked():
    """An e-mail address on a cut was read at confidence 22 and dropped, so the check for it saw nothing (2026-09-30)."""

    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t10\t10\t50\t10\t22.5\tjonas@acme.example.com\n")
    harness.check("dropped by default", text_detection_service.read_tsv_words(tsv) == [])
    harness.check("kept with no floor", [word["text"] for word in text_detection_service.read_tsv_words(tsv, min_confidence=0)]
                  == ["jonas@acme.example.com"])


def test_a_file_is_read_once_until_it_changes():
    """The check for a figure placed twice read every placed cut again for every pair of cuts, and a resume stood
    minutes on it before its first call (2026-10-04)."""

    folder = Path(tempfile.mkdtemp())
    image = folder / "cut-001.png"
    cv2.imwrite(str(image), numpy.full((40, 80, 3), 255, numpy.uint8))
    reads = []
    original = text_detection_service._read_words_in_image
    text_detection_service._read_words_in_image = lambda frame, min_confidence=0, is_read_larger=False: (
        reads.append(1) or [{"text": "Gateway", "box": [1, 2, 30, 12], "block": 1, "line": 1}])
    # no words cache on disk here: the made-up words would be kept for every blank image of this size, and a second run
    # would read them from disk instead of reading the file
    previous_cache = text_detection_service._WORDS_CACHE_DIR
    text_detection_service.set_words_cache_dir(None)
    try:
        first = text_detection_service.find_words(image)
        first[0]["box"][0] = 99
        second = text_detection_service.find_words(image)
        cv2.imwrite(str(image), numpy.full((40, 81, 3), 255, numpy.uint8))
        text_detection_service.find_words(image)
    finally:
        text_detection_service._read_words_in_image = original
        text_detection_service.set_words_cache_dir(previous_cache)
    harness.check("read once, a caller's change not kept, read again once changed",
                  len(reads) == 2 and second[0]["box"][0] == 1, f"reads {len(reads)}, {second}")


def test_a_failed_read_is_never_kept_as_no_words():
    """A Tesseract run that timed out gave the same empty list as a cut with no text, was kept for the rest of the run,
    and two such cuts then read alike, so a table of one slide template was refused as the other placed twice."""

    import subprocess
    folder = Path(tempfile.mkdtemp())
    image = folder / "cut-001.png"
    cv2.imwrite(str(image), numpy.full((40, 80, 3), 255, numpy.uint8))
    runs = []

    def fake_run(command, **kwargs):
        runs.append(command)
        if len(runs) == 1:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
        return subprocess.CompletedProcess(command, 0, "\n".join([HEADER, *ROWS]), "")

    original_run, original_available = text_detection_service.subprocess.run, text_detection_service.is_available
    text_detection_service.subprocess.run = fake_run
    text_detection_service.is_available = lambda: True
    try:
        failed = text_detection_service.read_figure_words(image)
        alike = text_detection_service.is_same_wording(failed, set())
        second = text_detection_service.find_words(image)
    finally:
        text_detection_service.subprocess.run = original_run
        text_detection_service.is_available = original_available
    harness.check("a cut whose read failed never reads alike another", not alike, str(failed))
    harness.check("the next read runs the reader again and gets the words", len(second) == 3, str(second))


def test_a_small_frame_is_read_enlarged_more_than_a_full_size_one():
    """A 640-pixel editor frame of a 360p recording read 2 words at twice its size and 17 at four times; a 1920-pixel
    frame reads the same at twice (2026-10-12)."""

    harness.check("640 is read at 4x, 960 at 3x, 1280 and 1920 at 2x, and anything at least 2x",
                  [text_detection_service._choose_scale(width) for width in (320, 640, 960, 1280, 1920, 3840)]
                  == [4, 4, 3, 2, 2, 2])
