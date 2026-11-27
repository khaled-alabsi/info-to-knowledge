"""Apple's text recognizer, as the program calls it: a read that does not end in time gives no words, as a tool that
cannot run does, and never ends the step that asked."""

from __future__ import annotations

import logging
import subprocess

import numpy

from support import harness
from services import apple_text_service


def test_a_read_that_times_out_gives_no_words_defect():
    # Defect: the recogniser timed out while the model host read a long prompt, and the raised error would have ended
    # the step that asked. Fix: a read that does not end in time returns no words.
    saved = (apple_text_service._build_tool, subprocess.run)

    def _late(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("apple-text-reader", 60)

    apple_text_service._build_tool, subprocess.run = (lambda: True), _late
    try:
        words = apple_text_service.find_words_in_image(numpy.full((40, 40, 3), 255, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run = saved
    harness.check("no words, and no error", words == [], str(words))


def test_a_read_that_gives_no_words_says_why_defect():
    # A check fed by the recognizer failed once in a sweep beside a run, with an empty reading and no word of why: a
    # timeout and a failed tool looked like a frame with no text. Now each says its cause in the log
    saved = (apple_text_service._build_tool, subprocess.run)
    said = []
    handler = logging.Handler()
    handler.emit = lambda record: said.append(record.getMessage())
    apple_text_service._log.addHandler(handler)

    def _late(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("apple-text-reader", 60)

    def _failed(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout="", stderr="Vision request failed")

    apple_text_service._build_tool = lambda: True
    try:
        for run in (_late, _failed):
            subprocess.run = run
            apple_text_service.find_words_in_image(numpy.full((40, 40, 3), 255, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run = saved
        apple_text_service._log.removeHandler(handler)
    harness.check("a timeout and a failed read each say why",
                  len(said) == 2 and "10 s" in said[0] and "Vision request failed" in said[1], str(said))


def test_an_image_read_once_is_never_read_again_and_a_failed_read_is_not_kept_defect():
    # Under memory pressure the recognizer timed out on 16 of a run's frames, each frame read again by every follow-up
    # of its batch. A reading made is now kept for the process, so the same picture is read once; a failed one is not
    saved = (apple_text_service._build_tool, subprocess.run)
    calls = []

    def _reads(*_args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise subprocess.TimeoutExpired("apple-text-reader", kwargs.get("timeout"))
        return subprocess.CompletedProcess([], 0, stdout='[{"text": "Gateway", "box": [1, 2, 3, 4], "line": 0}]', stderr="")

    image = numpy.full((30, 50, 3), 200, numpy.uint8)
    apple_text_service._WORDS_BY_IMAGE.clear()
    apple_text_service._build_tool, subprocess.run = (lambda: True), _reads
    try:
        first = apple_text_service.find_words_in_image(image)
        second = apple_text_service.find_words_in_image(image)
        third = apple_text_service.find_words_in_image(image.copy())
    finally:
        apple_text_service._build_tool, subprocess.run = saved
        apple_text_service._WORDS_BY_IMAGE.clear()
    harness.check("the read that timed out on both tries is not kept, and the next read is made",
                  first == [] and [word["text"] for word in second] == ["Gateway"], f"{first} {second}")
    harness.check("the same picture again is answered from what was kept", third == second and len(calls) == 3, str(calls))


def test_a_failed_read_is_told_apart_from_a_picture_with_no_words_defect():
    # A reading that timed out came back as no words, and the web address check dropped an address a frame's code
    # showed as shown nowhere (Lecture 7 frame 8). The checks that drop now ask for a reading that may be none
    saved = (apple_text_service._build_tool, subprocess.run)

    def _late(*_args, **kwargs):
        raise subprocess.TimeoutExpired("apple-text-reader", kwargs.get("timeout"))

    apple_text_service._WORDS_BY_IMAGE.clear()
    apple_text_service._build_tool, subprocess.run = (lambda: True), _late
    try:
        failed = apple_text_service.try_find_words_in_image(numpy.full((20, 60, 3), 90, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run = saved
    harness.check("a read that could not be made is None, not an empty reading", failed is None, str(failed))


def test_a_recognizer_that_keeps_timing_out_is_left_alone_for_a_while_defect():
    # Under a full swap 80 of Lecture 7's readings timed out, 20 s each, and phase 3 stood half an hour on them. After
    # three timed-out reads in a row the tool is not called for a while, its readings None, then tried again
    saved = (apple_text_service._build_tool, subprocess.run, apple_text_service._now)
    calls, clock = [], [1000.0]

    def _late(*_args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired("apple-text-reader", kwargs.get("timeout"))

    apple_text_service._WORDS_BY_IMAGE.clear()
    apple_text_service._STALL.update(failures=0, until=0.0)
    apple_text_service._build_tool, subprocess.run = (lambda: True), _late
    apple_text_service._now = lambda: clock[0]
    try:
        for shade in range(3):
            apple_text_service.try_find_words_in_image(numpy.full((10, 10, 3), shade, numpy.uint8))
        made = len(calls)
        skipped = apple_text_service.try_find_words_in_image(numpy.full((10, 10, 3), 9, numpy.uint8))
        after_skip = len(calls)
        clock[0] += apple_text_service._STALL_SECONDS + 1
        apple_text_service.try_find_words_in_image(numpy.full((10, 10, 3), 8, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run, apple_text_service._now = saved
        apple_text_service._STALL.update(failures=0, until=0.0)
    harness.check("three failed reads call the tool, the fourth does not",
                  made == 6 and after_skip == 6 and skipped is None, f"{made} {after_skip}")
    harness.check("and after the pause the tool is called again", len(calls) > 6, str(len(calls)))


def test_a_stalled_read_is_tried_again_and_keeps_its_words_defect():
    # Under a full swap one read in four stalled for 60 s and the frame's words were left out, while the next read took
    # 0.4 s. Now a read is given 10 s and tried once more
    saved = (apple_text_service._build_tool, subprocess.run)
    tries = []

    def _stalls_once(*_args, **kwargs):
        tries.append(kwargs.get("timeout"))
        if len(tries) == 1:
            raise subprocess.TimeoutExpired("apple-text-reader", kwargs.get("timeout"))
        return subprocess.CompletedProcess([], 0, stdout='[{"text": "Save", "box": [1, 2, 3, 4], "line": 0}]', stderr="")

    apple_text_service._build_tool, subprocess.run = (lambda: True), _stalls_once
    apple_text_service._WARM["is_warm"] = True
    try:
        words = apple_text_service.find_words_in_image(numpy.full((40, 40, 3), 255, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run = saved
    harness.check("the second try's words are kept", [word["text"] for word in words] == ["Save"], str(words))
    harness.check("each try waits 10 s at most", tries == [10, 10], str(tries))


def test_a_process_s_first_read_waits_out_the_recognizer_s_cold_start_defect():
    # Defect: cold, the tool's first call took 1:52, and two tries of 10 s gave it up, a small frame's dialog unread.
    # Fix: the first read a process makes is given 180 s once; every later read keeps its two tries of 10 s
    saved = (apple_text_service._build_tool, subprocess.run)
    limits = []

    def _read(*_args, **kwargs):
        limits.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess([], 0, stdout='[{"text": "Refund", "box": [1, 2, 3, 4], "line": 0}]', stderr="")

    apple_text_service._build_tool, subprocess.run = (lambda: True), _read
    apple_text_service._WARM["is_warm"] = False
    try:
        apple_text_service.find_words_in_image(numpy.full((40, 40, 3), 200, numpy.uint8))
        apple_text_service.find_words_in_image(numpy.full((40, 40, 3), 100, numpy.uint8))
    finally:
        apple_text_service._build_tool, subprocess.run = saved
    harness.check("the first read waits up to 180 s, the next up to 10 s", limits == [180, 10], str(limits))
