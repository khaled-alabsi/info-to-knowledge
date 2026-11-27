"""Phase 2's fourth stage, the similarity recheck: frames that look alike by the frame
classifier's SigLIP vectors but sat in different batches of the redundancy filter are asked again of its model, with
its checks.

The classifier is a stand-in command with the real one's interface, writing each frame's vectors when asked with
`--vectors-out`; the model is a scripted fake set through `frame_redundancy_service.set_model_factory`. Phase 2 runs
for real on a rendered three-scene video.
"""

from __future__ import annotations

import collections
import json
import logging
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from models.frame_judging_spec import FrameJudgingSpec
from models.run_paths import RunPaths
from services import frame_preparation_service
from services import frame_redundancy_service
from services import frame_similarity_service
from services import frames_preparing_progress_service
from services import performance_log_service
from services import phase_file_service
from services import run_paths_service
from services import text_detection_service
from utils import frame_group_utils

# Frames 000 and 002 get the same vector, 001 another: only the first and the last look alike
FAKE_CLASSIFIER = f"""#!{sys.executable}
import json, sys
from pathlib import Path
import numpy
folder, out = Path(sys.argv[2]), Path(sys.argv[4])
names = [path.name for path in sorted(folder.glob("*.jpg"))]
frames = [{{"frame": name, "peopleScore": 0.1, "tag": "other", "isPlainText": False, "milliseconds": 1.0}}
          for name in names]
out.write_text(json.dumps({{"classifier": "tags", "task": "tags", "peopleThreshold": 0.7, "frames": frames}}))
if "--vectors-out" in sys.argv:
    alike = numpy.ones(10) / numpy.sqrt(10)
    other = numpy.zeros(10)
    other[0] = 1.0
    rows = [numpy.tile(other if name == "frame-001.jpg" else alike, 5) for name in names]
    numpy.savez(sys.argv[sys.argv.index("--vectors-out") + 1], frames=numpy.array(names), vectors=numpy.stack(rows))
"""


class _ScriptedModel:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list] = []

    def invoke(self, messages: list) -> SimpleNamespace:
        self.calls.append(messages)

        return SimpleNamespace(content=self.replies.pop(0), usage_metadata={"input_tokens": 100, "output_tokens": 10},
                               response_metadata={})


def _reply(*entries: dict) -> str:
    return json.dumps({"decisions": list(entries)})


def test_frames_that_look_alike_form_one_list_and_a_long_list_goes_in_overlapping_calls():
    alike = numpy.zeros((8, 8), bool)
    for first, second in ((0, 5), (5, 7), (2, 3)):
        alike[first, second] = alike[second, first] = True
    groups = frame_group_utils.list_alike_groups(alike)
    harness.check("frames alike through a shared frame make one list, in time order; a frame alike to none is in none",
                  groups == [[0, 5, 7], [2, 3]], str(groups))
    calls = frame_group_utils.split_into_calls([[0, 1, 2, 3, 4], [6, 7]], 4)
    harness.check("a list of 5 in calls of 4 goes as two calls of 4 that overlap, never a call of one frame; a short "
                  "list gets its own call when the one before is full", calls == [[0, 1, 2, 3], [1, 2, 3, 4], [6, 7]],
                  str(calls))
    harness.check("two short lists that fit share one call",
                  frame_group_utils.split_into_calls([[0, 4], [6, 7]], 4) == [[0, 4, 6, 7]])
    harness.check("overlapping calls show each frame once in the count: 10 showings, 7 frames",
                  frame_group_utils.count_distinct_frames(calls) == 7, str(frame_group_utils.count_distinct_frames(calls)))


def test_only_alike_frames_of_different_batches_are_listed_together():
    alike, other = numpy.tile(numpy.ones(4) / 2, 5), numpy.tile(numpy.array([1.0, 0, 0, 0]), 5)
    groups = frame_similarity_service.list_alike_groups_across_batches([alike, alike, other, alike, None], [0, 0, 1, 1, 2], 0.97)
    harness.check("frames 0, 1 and 3 look alike, but 0 and 1 sat in one batch: one list joins them through frame 3; "
                  "a frame unlike the rest or with no vector is in no list", groups == [[0, 1, 3]], str(groups))


def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                   "text": "eins", "segments": [{"text": " eins", "start": 0.0,
                                                                                 "end": 6.0, "words": []}]})
    run_dir = paths.output_root / "logs" / "2026-10-01_09-00"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"), store=SimpleNamespace(count_attempts=lambda *_: 0),
                           control_check=None)


def _prepare_three_scenes(replies: list[str],
                          is_filter_on: bool) -> tuple[dict, "_ScriptedModel", list[str], list[dict]]:
    """Phase 2 on the three-scene video with a fake classifier (frames 1 and 3 look alike) and a scripted model:
    the result, the model, the console lines and the stages recorded."""

    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    command = folder / "classifier"
    command.write_text(FAKE_CLASSIFIER, encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    model = _ScriptedModel(replies)
    frames = config.settings.frames
    names = ("selection_mode", "min_scene_seconds", "is_knowledge_filter_on", "knowledge_classifier_command",
             "is_text_only_mark_on", "is_redundancy_filter_on", "redundancy_frame_width", "redundancy_frames_per_call",
             "is_similarity_recheck_on", "redundancy_min_words")
    previous = {name: getattr(frames, name) for name in names}
    # the scenes' frames hold no words, so the word count a removal needs is 0 here
    for name, value in zip(names, ("scene", 0.0, True, str(command), False, is_filter_on, 320, 2, True, 0)):
        setattr(frames, name, value)
    frame_redundancy_service.set_model_factory(lambda name: model)
    # nor do they look alike: three different scenes, the classifier faked to call two of them alike
    max_change = frame_redundancy_service.MAX_HOLDER_CHANGE
    frame_redundancy_service.MAX_HOLDER_CHANGE = 1.0
    printed: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: printed.append(record.getMessage())
    ctx.log.addHandler(handler)
    ctx.log.setLevel(logging.INFO)
    # The model's path is under test: with no Apple recognizer the program's own pass over alike frames drops nothing,
    # which it would here, the scenes holding no word and the word count a drop needs set to 0
    no_lines = mock.patch.object(frame_similarity_service.apple_text_service, "is_available", return_value=False)
    try:
        no_lines.start()
        handles = performance_log_service.start_unit(ctx, "frames_preparing", None)
        result = frame_preparation_service.prepare_frames(ctx)
        performance_log_service.record_phase(ctx, "frames_preparing", "complete", handles)
    finally:
        no_lines.stop()
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
        frame_redundancy_service.MAX_HOLDER_CHANGE = max_change
        for name, value in previous.items():
            setattr(frames, name, value)
    ctx.log.removeHandler(handler)

    return result, model, printed, frames_preparing_progress_service.read_progress(ctx.run_dir)


_RECHECK_REPLY = _reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F003"], "reason": "the same screen again"},
                        {"frame_id": "F003", "decision": "KEEP", "reason": "c"})


def test_phase_2_rechecks_alike_frames_the_redundancy_filter_never_showed_together():
    # The redundancy filter's one call (frames 1 and 2; the third alone is not asked) keeps both; the recheck's call
    # holds the first and the third, which look alike, and removes the first as held by the third
    result, model, printed, stages = _prepare_three_scenes(
        [_reply({"frame_id": "F001", "decision": "KEEP", "reason": "a"}, {"frame_id": "F002", "decision": "KEEP", "reason": "b"}),
         _RECHECK_REPLY], is_filter_on=True)
    stage_line = next((line for line in printed if "similarity" in line and "stage 4" in line), "")
    harness.check("the stage's line counts the lists of similar frames, never pairs",
                  "1 list(s) of similar frames (2 frames" in stage_line and "pair" not in stage_line, stage_line)
    dropped = result["droppedFrames"]
    harness.check("two calls: the redundancy filter's batch, then the recheck of the alike frames across batches",
                  len(model.calls) == 2, str(len(model.calls)))
    phase_line = next((line for line in printed if line.startswith("phase 2 time:")), "")
    harness.check("phase 2's line counts both stages' calls and their tokens, never \"no model call\"",
                  "2 calls, 200 prompt + 20 output tokens" in phase_line, phase_line)
    harness.check("the first frame is dropped by the similarity recheck, held by the third, with the model's reason",
                  [(entry["slug"], entry["droppedBy"], entry["coveredBy"], entry["reason"]) for entry in dropped]
                  == [("frame-000", "similarity", ["frame-002"], "the same screen again")], json.dumps(dropped, default=str))
    harness.check("phase 2 records four stages, all done; the recheck asked 2 frames and 2 of the run's frames stay",
                  [(stage["name"], stage["status"]) for stage in stages] == [("rules", "done"), ("classifier", "done"),
                   ("model", "done"), ("similarity", "done")] and (stages[3]["frames"], stages[3]["kept"]) == (2, 2),
                  str(stages))


def test_with_the_redundancy_filter_off_the_similarity_recheck_still_runs_on_every_list_of_alike_frames():
    result, model, _, stages = _prepare_three_scenes([_RECHECK_REPLY], is_filter_on=False)
    harness.check("one call, the recheck's, and the first frame is dropped by it as held by the third",
                  len(model.calls) == 1 and [(entry["slug"], entry["droppedBy"]) for entry in result["droppedFrames"]]
                  == [("frame-000", "similarity")], f"{len(model.calls)} {result['droppedFrames']}")
    harness.check("phase 2 records the rules, the classifier and the recheck, with no redundancy stage",
                  [stage["name"] for stage in stages] == ["rules", "classifier", "similarity"], str(stages))


def _write_text_frame(path: Path,
                      lines: list[str]) -> None:
    image = numpy.full((540, 960, 3), 255, numpy.uint8)
    for row, line in enumerate(lines):
        cv2.putText(image, line, (40, 80 + row * 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(path), image)


def test_a_removal_stands_only_when_its_holder_shows_the_removed_frames_words():
    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    base = ["def fizzbuzz number", "return result value", "print output total"]
    _write_text_frame(frames_dir / "frame-000.jpg", base)
    _write_text_frame(frames_dir / "frame-001.jpg", base + ["subprocess pipe stdout added"])
    _write_text_frame(frames_dir / "frame-002.jpg", ["coverage report missing lines", "branch statements excluded"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(3)]
    # The model removes frame 0 as held by frame 1 (which shows its words and more) and frame 2 as held by frame 1
    # (which shows none of its words)
    model = _ScriptedModel([_reply(
        {"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "the same code"},
        {"frame_id": "F002", "decision": "KEEP", "reason": "more code"},
        {"frame_id": "F003", "decision": "DISCARD", "covered_by": ["F002"], "reason": "the same screen"})])
    frame_redundancy_service.set_model_factory(lambda name: model)
    try:
        kept, dropped = frame_redundancy_service.judge_calls(
            items, [[0, 1, 2]], frames_dir,
            FrameJudgingSpec("fake", 320, "similarity", "recheck", None, 2),
            logging.getLogger("test"))
    finally:
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
    harness.check("the frame whose words its holder shows is removed; the one whose words it lacks is kept",
                  [entry["slug"] for entry in dropped] == ["frame-000"]
                  and [item["slug"] for item in kept] == ["frame-001", "frame-002"],
                  f"{[entry['slug'] for entry in dropped]} {[item['slug'] for item in kept]}")


def test_a_frame_whose_new_lines_repeat_words_its_holder_shows_once_is_kept():
    """DEFECTS.md 89: a terminal's result lines repeat words that stand once in a table above on the holder; compared
    as sets of words nothing was missing, and the result was lost."""

    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    table = ["found renaming imports python", "tests conftest import mock", "setup import extension"]
    _write_text_frame(frames_dir / "frame-000.jpg", table + ["running search now"])
    _write_text_frame(frames_dir / "frame-001.jpg", table + ["found renaming import", "tests conftest import mock"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    model = _ScriptedModel([_reply({"frame_id": "F001", "decision": "KEEP", "reason": "the search"},
                                   {"frame_id": "F002", "decision": "DISCARD", "covered_by": ["F001"], "reason": "same"})])
    frame_redundancy_service.set_model_factory(lambda name: model)
    try:
        _, dropped = frame_redundancy_service.judge_calls(
            items, [[0, 1]], frames_dir, FrameJudgingSpec("fake", 320, "similarity", "recheck", None, 2),
            logging.getLogger("test"))
    finally:
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
    harness.check("the frame with the result lines stays: its holder shows those words once, not twice",
                  dropped == [], str([entry["slug"] for entry in dropped]))


def test_a_frame_whose_picture_its_holder_does_not_show_is_kept_beside_the_same_words():
    """DEFECTS.md 94: a slide's bullets stood the same on the holder while its picture was another one (a 3D model
    against a point cloud); the word check saw nothing missing."""

    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    bullets = ["vegetation data from laser scans", "scans hold one gigabyte each", "only done for one region"]
    for number, is_model in ((0, True), (1, False)):
        _write_text_frame(frames_dir / f"frame-00{number}.jpg", bullets)
        image = cv2.imread(str(frames_dir / f"frame-00{number}.jpg"))
        if is_model:                                        # a picture of blocks below the bullets
            for left in range(40, 900, 120):
                cv2.rectangle(image, (left, 290), (left + 90, 520), (40, 160, 40), -1)
        else:                                               # another picture there: a cloud of dots
            for x in range(40, 920, 14):
                for y in range(290, 520, 14):
                    cv2.circle(image, (x, y), 4, (200, 60, 20), -1)
        cv2.imwrite(str(frames_dir / f"frame-00{number}.jpg"), image)
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    model = _ScriptedModel([_reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "same"},
                                   {"frame_id": "F002", "decision": "KEEP", "reason": "the scan"})])
    frame_redundancy_service.set_model_factory(lambda name: model)
    # Both frames read the same words, as the real slides did; the text reader misreads this drawn font
    original = frame_redundancy_service._count_bare_words
    frame_redundancy_service._count_bare_words = lambda item, folder: collections.Counter(
        "vegetation data from laser scans hold one gigabyte".split())
    try:
        _, dropped = frame_redundancy_service.judge_calls(
            items, [[0, 1]], frames_dir, FrameJudgingSpec("fake", 320, "redundancy", "filter", None, 2),
            logging.getLogger("test"))
    finally:
        frame_redundancy_service._count_bare_words = original
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
    harness.check("the frame whose picture its holder does not show stays", dropped == [],
                  str([entry["slug"] for entry in dropped]))


def test_a_frame_showing_too_few_words_to_check_is_never_removed_under_the_word_check():
    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    # A title card of two words, removed as held by a slide that shows both words and more: the word check sees
    # nothing missing, so only the frame's own word count can keep it
    _write_text_frame(frames_dir / "frame-000.jpg", ["Firmware analysis"])
    _write_text_frame(frames_dir / "frame-001.jpg", ["Firmware analysis results", "memory regions mapped boot"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    reply = _reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "a title"},
                   {"frame_id": "F002", "decision": "KEEP", "reason": "the slide"})
    dropped_by_minimum = {}
    for min_words in (5, 0):
        model = _ScriptedModel([reply])
        frame_redundancy_service.set_model_factory(lambda name: model)
        try:
            _, dropped = frame_redundancy_service.judge_calls(
                [dict(item) for item in items], [[0, 1]], frames_dir,
                FrameJudgingSpec("fake", 320, "redundancy", "filter", None, 2, None, min_words), logging.getLogger("test"))
        finally:
            frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
        dropped_by_minimum[min_words] = [entry["slug"] for entry in dropped]
        for entry in dropped:
            (frames_dir / "dropped" / Path(entry["path"]).name).rename(frames_dir / Path(entry["path"]).name)
    harness.check("with at least 5 words required the two-word title card stays; with none it is removed",
                  dropped_by_minimum == {5: [], 0: ["frame-000"]}, str(dropped_by_minimum))


def test_a_removal_with_no_reason_stands_under_the_word_check_and_never_without_it():
    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    base = ["def fizzbuzz number", "return result value", "print output total"]
    _write_text_frame(frames_dir / "frame-000.jpg", base)
    _write_text_frame(frames_dir / "frame-001.jpg", base + ["subprocess pipe stdout added"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    reply = _reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"]},
                   {"frame_id": "F002", "decision": "KEEP"})
    dropped_by_check = {}
    for max_missing in (2, None):
        model = _ScriptedModel([reply])
        frame_redundancy_service.set_model_factory(lambda name: model)
        try:
            _, dropped = frame_redundancy_service.judge_calls(
                [dict(item) for item in items], [[0, 1]], frames_dir,
                FrameJudgingSpec("fake", 320, "redundancy", "filter", None, max_missing), logging.getLogger("test"))
        finally:
            frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
        dropped_by_check[max_missing] = [entry["slug"] for entry in dropped]
        for entry in dropped:
            (frames_dir / "dropped" / f"{entry['slug']}.jpg").replace(frames_dir / f"{entry['slug']}.jpg")
    harness.check("with the word check the reasonless removal stands; without it the frame is kept",
                  dropped_by_check == {2: ["frame-000"], None: []}, str(dropped_by_check))


def test_the_word_check_counts_two_letter_words():
    """A `return "hi"` changed into a real body differed by short words only; counted from three letters, the check let
    the earlier frame's removal through (Lecture 9 frame-100)."""

    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    base = ["def fizzbuzz number", "return result value", "print output total"]
    _write_text_frame(frames_dir / "frame-000.jpg", base + ["go do it ok"])
    _write_text_frame(frames_dir / "frame-001.jpg", base + ["subprocess pipe stdout added"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    model = _ScriptedModel([_reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "same"},
                                   {"frame_id": "F002", "decision": "KEEP", "reason": "more"})])
    frame_redundancy_service.set_model_factory(lambda name: model)
    try:
        _, dropped = frame_redundancy_service.judge_calls(
            items, [[0, 1]], frames_dir, FrameJudgingSpec("fake", 320, "redundancy", "filter", None, 2),
            logging.getLogger("test"))
    finally:
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
    harness.check("four two-letter words its holder lacks keep the frame", dropped == [], str(dropped))


def test_a_frame_with_marks_drawn_where_its_holder_is_blank_is_kept():
    """DEFECTS 114: a lecture's slide with handwritten labels under its bullets was removed as held by the same slide
    without them: the text reader read none of the handwriting and under 15% of the blocks changed."""

    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    bullets = ["flow of control needs a test", "if something is true do this", "otherwise do that"]
    for number in range(2):
        _write_text_frame(frames_dir / f"frame-00{number}.jpg", bullets)
    image = cv2.imread(str(frames_dir / "frame-000.jpg"))
    for left in (60, 360, 660):                             # three handwritten labels under the second bullet
        cv2.ellipse(image, (left + 90, 300), (90, 30), 0, 0, 360, (40, 40, 200), 3)
        cv2.line(image, (left + 20, 360), (left + 160, 390), (40, 40, 200), 3)
    cv2.imwrite(str(frames_dir / "frame-000.jpg"), image)
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n),
              "path": f"frames/frame-00{n}.jpg"} for n in range(2)]
    model = _ScriptedModel([_reply({"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "same"},
                                   {"frame_id": "F002", "decision": "KEEP", "reason": "the bullets"})])
    frame_redundancy_service.set_model_factory(lambda name: model)
    original = frame_redundancy_service._count_bare_words
    frame_redundancy_service._count_bare_words = lambda item, folder: collections.Counter(" ".join(bullets).split())
    try:
        _, dropped = frame_redundancy_service.judge_calls(
            items, [[0, 1]], frames_dir, FrameJudgingSpec("fake", 320, "redundancy", "filter", None, 2),
            logging.getLogger("test"))
    finally:
        frame_redundancy_service._count_bare_words = original
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
    harness.check("the frame with the handwritten labels stays", dropped == [], str([entry["slug"] for entry in dropped]))


def test_a_slide_shown_again_unchanged_leaves_the_recheck_with_no_model_call_defect():
    # A slide shown again after other slides looked alike to its first showing and the recheck's model removed none, so
    # it was dropped by hand. Now the program drops it as similarity; a re-take and a showing with a new line stay
    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    slide = ["balcony panels are rather cheap", "installation with a normal plug", "panels released in 2022"]
    _write_text_frame(frames_dir / "frame-000.jpg", slide)
    _write_text_frame(frames_dir / "frame-001.jpg", ["coverage report missing lines", "branch statements excluded"])
    _write_text_frame(frames_dir / "frame-002.jpg", slide)
    _write_text_frame(frames_dir / "frame-003.jpg", slide)
    _write_text_frame(frames_dir / "frame-004.jpg", slide + ["sticker handed out after the talk"])
    _write_text_frame(frames_dir / "frame-005.jpg", slide[:2] + ["panels released in 2023"])
    items = [{"slug": f"frame-00{n}", "index": n, "frameNumber": n, "timestamp": float(n * 60),
              "path": f"frames/frame-00{n}.jpg"} for n in range(6)]
    items[3]["isHeldRetake"] = True
    alike, other = numpy.ones(10) / numpy.sqrt(10), numpy.eye(10)[0]
    vectors = {f"frame-00{n}.jpg": numpy.tile(other if n == 1 else alike, 5) for n in range(6)}
    copies = frame_similarity_service.list_alike_copies(items, vectors, frames_dir, config.settings.frames)
    harness.check("the unchanged showing leaves, held by the first; the re-take, the new line and the new year stay",
                  [(item["slug"], item["coveredBy"]) for item in copies] == [("frame-002", ["frame-000"])],
                  str([(item["slug"], item["coveredBy"]) for item in copies]))
