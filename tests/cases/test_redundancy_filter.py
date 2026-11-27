"""The redundancy filter: phase 2 asks a small vision model, a batch of consecutive frames
at a time, which frames another frame holds whole, and drops those the program's checks let stand.

The model is a fake with the real one's interface (`invoke(messages)` -> a reply with `content`), set through the
service's `set_model_factory`; it records every call and answers from a script. Phase 2 runs for real on a rendered
three-scene video.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy
from support import harness
from support import media_fixtures

import config
from cli import run_command
from models import frame_redundancy_outcome
from models.run_paths import RunPaths
from models.run_start_checks_result import RunStartChecksResult
from prompts import frame_redundancy_messages
from services import frame_preparation_service
from services import frame_redundancy_service
from services import frames_preparing_progress_service
from services import worker_view_service
from services import phase_file_service
from services import run_paths_service
from utils import frame_coverage_utils
from validators import frame_redundancy_validator

_IDS = ["F001", "F002", "F003"]


class _ScriptedModel:
    """Answers each call with the next scripted reply, and keeps every call's messages."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list] = []

    def invoke(self, messages: list) -> SimpleNamespace:
        self.calls.append(messages)

        # shaped as a model's reply message: its content and the host's metadata, empty here
        return SimpleNamespace(content=self.replies.pop(0), response_metadata={}, additional_kwargs={})


def _reply(*entries: dict) -> str:
    return json.dumps({"decisions": list(entries)})


def test_a_return_adding_nothing_leaves_and_one_adding_a_line_or_a_drawing_stays_defect():
    # Defect: a return to an earlier slide with nothing new (the capture marks it repeatOf) reached phase 3, and such
    # frames were dropped by hand on six runs (DEFECTS 603). Fix: a return whose words and marks its first showing
    # holds leaves as repeat-copy; one bringing a new line of words or a drawing on the slide's blank part stays
    from unittest import mock

    import cv2
    import numpy

    base = ["Refund limits per merchant daily", "Gateway retries refunds twice", "Jonas reviews escalated refunds"]
    words = {"frame-001.jpg": base, "frame-002.jpg": ["Token expires after one hour", "Rotate keys weekly at night"],
             "frame-003.jpg": base, "frame-004.jpg": base + ["Merchants appeal within seven days"],
             "frame-005.jpg": base}
    with tempfile.TemporaryDirectory() as folder:
        frames_dir = Path(folder)
        for name, lines in words.items():
            image = numpy.full((360, 640), 255, numpy.uint8)
            for row, line in enumerate(lines):
                cv2.putText(image, line, (20, 40 + 40 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
            if name == "frame-005.jpg":
                cv2.rectangle(image, (380, 220), (600, 330), 0, 4)
                cv2.line(image, (380, 220), (600, 330), 0, 4)
            cv2.imwrite(str(frames_dir / name), image)
        items = [{"slug": name[:-4], "path": f"frames/{name}",
                  "repeatOf": "frame-001" if name in ("frame-003.jpg", "frame-004.jpg", "frame-005.jpg") else None}
                 for name in words]
        with mock.patch.object(frame_redundancy_service.text_detection_service, "find_words",
                               lambda path, *_: [{"text": line} for line in words[Path(path).name]]):
            gone = frame_redundancy_service.list_repeat_copies(items, frames_dir, config.settings.frames)
    harness.check("only the return adding nothing leaves, held by its first showing",
                  [(item["slug"], item["coveredBy"]) for item in gone] == [("frame-003", ["frame-001"])], str(gone))


def test_a_section_title_marked_a_return_of_another_title_stays_defect():
    # Defect: a section title slide "Gateway 17", marked by the capture a return of the title "Gateway 15", left as a
    # repeat-copy: of its three words one was missing, within the allowance. Fix: a return of fewer words than the
    # model stages' minimum is never dropped on its words
    from unittest import mock

    import cv2
    import numpy

    words = {"frame-001.jpg": ["GATEWAY 15", "AcmeSync"], "frame-002.jpg": ["Refund limits per merchant daily"],
             "frame-003.jpg": ["GATEWAY 17", "AcmeSync"]}
    with tempfile.TemporaryDirectory() as folder:
        frames_dir = Path(folder)
        for name, lines in words.items():
            image = numpy.full((360, 640), 255, numpy.uint8)
            for row, line in enumerate(lines):
                cv2.putText(image, line, (300, 60 + 260 * row), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
            cv2.imwrite(str(frames_dir / name), image)
        items = [{"slug": name[:-4], "path": f"frames/{name}",
                  "repeatOf": "frame-001" if name == "frame-003.jpg" else None} for name in words]
        with mock.patch.object(frame_redundancy_service.text_detection_service, "find_words",
                               lambda path, *_: [{"text": line} for line in words[Path(path).name]]):
            gone = frame_redundancy_service.list_repeat_copies(items, frames_dir, config.settings.frames)
    harness.check("the second section title stays", gone == [], str([item["slug"] for item in gone]))


def test_a_return_read_with_other_letters_leaves_and_one_with_a_new_line_stays_defect():
    # Defect: twelve returns identical to their first showing reached phase 3, Tesseract reading 4 to 9 words of their
    # small type otherwise on the two pictures. Fix: Apple's lines are compared loosely, so only a new line keeps one
    lines = {"frame-001": ["neue features", "sicherung für große tabellen", "© 2024 acmesync"],
             "frame-002": ["roadmap 2025"],
             "frame-003": ["neue features", "sicherung fiir grosse tabellen", "02224 acmesync"],
             "frame-004": ["neue features", "sicherung für große tabellen", "pg_dump now writes zstd"]}
    with tempfile.TemporaryDirectory() as folder:
        frames_dir = Path(folder)
        for slug in lines:
            cv2.imwrite(str(frames_dir / f"{slug}.jpg"), numpy.full((360, 640), 255, numpy.uint8))
        repeats = {"frame-003": "frame-001", "frame-004": "frame-001"}
        items = [{"slug": slug, "path": f"frames/{slug}.jpg", "repeatOf": repeats.get(slug)} for slug in lines]
        with mock.patch.object(frame_redundancy_service.apple_text_service, "is_available", lambda: True), \
                mock.patch.object(frame_redundancy_service, "_read_lines", lambda path: lines[Path(path).stem]):
            gone = frame_redundancy_service.list_repeat_copies(items, frames_dir, config.settings.frames)
    harness.check("only the return read with other letters leaves; the one adding a line stays",
                  [(item["slug"], item["coveredBy"]) for item in gone] == [("frame-003", ["frame-001"])], str(gone))


def test_a_slide_taken_again_after_three_minutes_held_stays_defect():
    # Defect: a slide held 3 minutes is taken again, a repeat of the frame before, so its speech is split (DEFECTS 157);
    # the step dropping returns that add nothing dropped that re-take too. Fix: a frame marked isHeldRetake stays
    words = {"frame-001.jpg": ["Refund limits per merchant daily", "Gateway retries refunds twice"],
             "frame-002.jpg": ["Refund limits per merchant daily", "Gateway retries refunds twice"]}
    with tempfile.TemporaryDirectory() as folder:
        frames_dir = Path(folder)
        for name, lines in words.items():
            image = numpy.full((360, 640), 255, numpy.uint8)
            for row, line in enumerate(lines):
                cv2.putText(image, line, (20, 40 + 40 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
            cv2.imwrite(str(frames_dir / name), image)
        items = [{"slug": "frame-001", "path": "frames/frame-001.jpg", "repeatOf": None},
                 {"slug": "frame-002", "path": "frames/frame-002.jpg", "repeatOf": "frame-001", "isHeldRetake": True}]
        with mock.patch.object(frame_redundancy_service.text_detection_service, "find_words",
                               lambda path, *_: [{"text": line} for line in words[Path(path).name]]):
            gone = frame_redundancy_service.list_repeat_copies(items, frames_dir, config.settings.frames)
    harness.check("the re-take of the held slide stays", gone == [], str([item["slug"] for item in gone]))


def test_a_removal_stands_only_with_a_reason_and_a_covering_frame_of_the_call():
    decisions, is_readable = frame_redundancy_validator.check_reply(_reply(
        {"frame_id": "F001", "decision": "DISCARD", "covered_by": ["F002"], "reason": "same ticket"},
        {"frame_id": "F002", "decision": "DISCARD", "covered_by": ["F009"], "reason": "shown later"},
        {"frame_id": "F003", "decision": "DISCARD", "covered_by": ["F001"]}), _IDS)
    by_id = {decision.frame_id: decision for decision in decisions}
    harness.check("the reply is read", is_readable)
    harness.check("a removal naming a frame of the call stands", by_id["F001"].decision == "DISCARD"
                  and by_id["F001"].covered_by == ("F002",), str(by_id["F001"]))
    harness.check("a removal naming a frame the call did not show is kept",
                  by_id["F002"].decision == "KEEP" and "covering frame" in (by_id["F002"].kept_reason or ""),
                  str(by_id["F002"]))
    harness.check("a removal with no reason is kept", by_id["F003"].decision == "KEEP"
                  and "reason" in (by_id["F003"].kept_reason or ""), str(by_id["F003"]))


def test_an_unreadable_reply_keeps_every_frame_and_a_known_bracket_slip_is_read():
    decisions, is_readable = frame_redundancy_validator.check_reply("I think frame 2 repeats frame 1.", _IDS)
    harness.check("an unreadable reply is reported so", not is_readable)
    harness.check("and keeps every frame of the call", all(d.decision == "KEEP" for d in decisions), str(decisions))
    slipped = '{"decisions": [{"frame_id": "F002", "decision": "DISCARD", "covered_by": ["F001"], "reason": "x"}}]'
    decisions, is_readable = frame_redundancy_validator.check_reply(slipped, _IDS)
    harness.check("a reply with the extra brace before the list ends is read", is_readable
                  and decisions[1].decision == "DISCARD", str(decisions))


def test_a_bare_list_of_decisions_in_a_fence_is_read_as_the_decisions():
    """qwen3.5-4b wrote its decisions as a bare list in a ```json fence on 42 of 46 calls; read as unreadable, every
    frame was kept."""

    reply = '```json\n[\n  {"frame_id": "F001", "decision": "KEEP", "reason": "a"},\n  {"frame_id": "F002", ' \
            '"decision": "DISCARD", "covered_by": ["F001"], "reason": "b"},\n  {"frame_id": "F003", "decision": "KEEP"}\n]\n```'
    decisions, is_readable = frame_redundancy_validator.check_reply(reply, _IDS)
    harness.check("the list is read: its removal stands", is_readable and decisions[1].decision == "DISCARD"
                  and decisions[1].covered_by == ("F001",), str(decisions))


def test_a_replacement_names_only_earlier_frames():
    decisions, _ = frame_redundancy_validator.check_reply(_reply(
        {"frame_id": "F002", "decision": "REPLACE_PREVIOUS", "replaces": ["F001", "F003"], "reason": "adds a row"}),
        _IDS)
    harness.check("only the earlier frame is replaced", decisions[1].replaces == ("F001",), str(decisions[1]))


def test_a_frame_whose_covers_were_all_removed_is_kept():
    removed: dict[str, list[str]] = {}
    frame_coverage_utils.apply_decisions(removed, [
        frame_redundancy_outcome.FrameRedundancyOutcome("F001", "DISCARD", "a", covered_by=("F002",)),
        frame_redundancy_outcome.FrameRedundancyOutcome("F002", "DISCARD", "b", covered_by=("F001",)),
        frame_redundancy_outcome.FrameRedundancyOutcome("F003", "DISCARD", "c", covered_by=("F004",))])
    standing = frame_coverage_utils.list_standing_removals(removed, ["F001", "F002", "F003", "F004"])
    harness.check("two frames covering only each other are both kept", "F001" not in standing
                  and "F002" not in standing, str(standing))
    harness.check("a frame covered by one that stays is removed", standing.get("F003") == ["F004"], str(standing))


def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                   "text": "eins", "segments": [{"text": " eins", "start": 0.0,
                                                                                 "end": 6.0, "words": []}]})
    run_dir = paths.output_root / "logs" / "2026-10-01_09-00"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"), control_check=None)


def _prepare_with(ctx: SimpleNamespace,
                  is_filter_on: bool,
                  model: _ScriptedModel) -> dict:
    frames = config.settings.frames
    previous = (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on,
                frames.is_text_only_mark_on, frames.is_redundancy_filter_on, frames.redundancy_frame_width,
                frames.redundancy_min_words, frames.is_board_stage_on)
    frames.selection_mode, frames.min_scene_seconds = "scene", 0.0
    frames.is_knowledge_filter_on, frames.is_text_only_mark_on, frames.is_board_stage_on = False, False, False
    # the scenes' frames hold no words, so the word count a removal needs is 0 here
    frames.is_redundancy_filter_on, frames.redundancy_frame_width, frames.redundancy_min_words = is_filter_on, 320, 0
    # nor do they look alike: the model is scripted to call one scene held by another
    max_change, frame_redundancy_service.MAX_HOLDER_CHANGE = frame_redundancy_service.MAX_HOLDER_CHANGE, 1.0
    built: list[str] = []
    frame_redundancy_service.set_model_factory(lambda name: built.append(name) or model)
    try:
        result = frame_preparation_service.prepare_frames(ctx)
    finally:
        frame_redundancy_service.set_model_factory(frame_redundancy_service.chat_model_service.get_named_model)
        frame_redundancy_service.MAX_HOLDER_CHANGE = max_change
        (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on, frames.is_text_only_mark_on,
         frames.is_redundancy_filter_on, frames.redundancy_frame_width, frames.redundancy_min_words,
         frames.is_board_stage_on) = previous

    return {**result, "built": built}


def test_phase_2_drops_the_frame_the_model_names_as_held_by_another_and_says_which_filter_dropped_it():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    model = _ScriptedModel([_reply(
        {"frame_id": "F001", "decision": "KEEP", "reason": "first scene"},
        {"frame_id": "F002", "decision": "DISCARD", "covered_by": ["F003"], "reason": "the same screen again"},
        {"frame_id": "F003", "decision": "KEEP", "reason": "third scene"})] * 3)
    result = _prepare_with(ctx, True, model)
    kept_names = [Path(item["path"]).name for item in result["items"]]
    dropped = result["droppedFrames"]
    harness.check("the scene video gives three frames, one call", len(kept_names) + len(dropped) == 3
                  and len(model.calls) == 1, f"{kept_names} {dropped} {len(model.calls)}")
    harness.check("the model the settings name is the one built",
                  result["built"] == [config.settings.frames.redundancy_model], str(result["built"]))
    images = [part for part in model.calls[0][1].content if part.get("type") == "image_url"]
    harness.check("each frame of the batch is sent as its image", len(images) == 3, str(len(images)))
    harness.check("the second frame is dropped, by the redundancy filter, with the frame that holds it and the reason",
                  len(dropped) == 1 and dropped[0]["droppedBy"] == "redundancy"
                  and dropped[0]["coveredBy"] == [result["items"][1]["slug"]]
                  and dropped[0]["reason"] == "the same screen again", json.dumps(dropped, default=str))
    stages = frames_preparing_progress_service.read_progress(ctx.run_dir)
    harness.check("each stage records the frames that survived it: 3 taken by the rules, 2 kept by the model",
                  [(stage["name"], stage["frames"], stage["kept"]) for stage in stages] == [("rules", 3, 3), ("model", 3, 2)],
                  str(stages))
    out = ctx.paths.output_root / "frames"
    harness.check("its image moved into frames/dropped/", (out / "dropped" / Path(dropped[0]["path"]).name).is_file()
                  and not (out / Path(dropped[0]["path"]).name).exists())
    steps = worker_view_service.list_worker_steps(ctx.paths.output_root, 2)
    harness.check("the call is saved as phase 2's step, named by its layer and its first and last frame",
                  [(step["step"], step["attempts"][0]["status"]) for step in steps]
                  == [(f"redundancy-{result['items'][0]['slug']}-{result['items'][1]['slug']}", "accepted")], str(steps))
    seen = worker_view_service.read_attempt(ctx.paths.output_root, steps[0]["attempts"][0]["ref"]) if steps else {}
    shown = [segment["path"] for segment in seen.get("input", []) if segment["kind"] == "image"]
    harness.check("what it was sent is shown with each image as its frame, the dropped one from frames/dropped/",
                  bool(seen.get("instructions")) and shown == [result["items"][0]["path"], dropped[0]["path"], result["items"][1]["path"]], str(shown))


def test_phase_2_waits_out_a_pause_between_model_calls_defect():
    # A paused run went on through phase 2 (its frames and its model calls), since only phase 3 read the pause.
    # Now phase 2 asks the run's pause check after each model call (and, in change mode, as the rules sample).
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    asked_in: list[str] = []
    ctx.control_check = lambda: asked_in.append(
        frames_preparing_progress_service.read_progress(ctx.run_dir)[-1]["name"])
    model = _ScriptedModel([_reply(*({"frame_id": f"F00{index}", "decision": "KEEP", "reason": "own scene"}
                                     for index in (1, 2, 3)))] * 3)
    _prepare_with(ctx, True, model)
    harness.check("the pause is asked after the model's call", "model" in asked_in, str(asked_in))


def test_the_filter_line_names_each_rule_a_reply_broke_with_its_count():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    lines: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    ctx.log = logging.getLogger(f"broken-rules-{folder.name}")
    ctx.log.addHandler(handler)
    ctx.log.setLevel(logging.INFO)
    model = _ScriptedModel([_reply(
        {"frame_id": "F001", "decision": "KEEP", "reason": "first scene"},
        {"frame_id": "F002", "decision": "DISCARD", "reason": "the same screen again"},
        {"frame_id": "F003", "decision": "DISCARD", "reason": "the same again"})] * 3)
    _prepare_with(ctx, True, model)
    line = next((line for line in lines if "redundancy filter" in line), "")
    harness.check("the line counts the overruled decisions by the rule each broke",
                  "2 decision(s) kept against a reply that broke a rule (DISCARD without a covering frame of this call: 2)"
                  in line, line)


def test_phase_2_run_again_keeps_no_frame_an_earlier_run_dropped():
    """Only the knowledge filter emptied `frames/dropped/`: with it off, a second phase 2 kept the first one's dropped
    images and list, and the page showed drops the run never made."""

    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    _prepare_with(ctx, True, _ScriptedModel([_reply(
        {"frame_id": "F001", "decision": "KEEP", "reason": "first scene"},
        {"frame_id": "F002", "decision": "DISCARD", "covered_by": ["F003"], "reason": "the same screen again"},
        {"frame_id": "F003", "decision": "KEEP", "reason": "third scene"})] * 3))
    dropped_dir = ctx.paths.output_root / "frames" / "dropped"
    harness.check("the first run dropped a frame", any(dropped_dir.glob("*.jpg")), str(list(dropped_dir.glob("*"))))
    result = _prepare_with(ctx, False, _ScriptedModel([]))
    harness.check("the second run dropped nothing and left nothing of the first",
                  result["droppedFrames"] == [] and not dropped_dir.exists(), str(list(dropped_dir.glob("*"))))


def test_phase_2_run_again_keeps_no_frame_image_this_run_did_not_take():
    """A second phase 2 that took fewer frames left the first one's extra images in `frames/`, and the classifier,
    handed the whole folder, tagged them too."""

    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    _prepare_with(ctx, False, _ScriptedModel([]))
    frames_dir = ctx.paths.output_root / "frames"
    stale = frames_dir / "frame-099.jpg"
    stale.write_bytes((frames_dir / "frame-000.jpg").read_bytes())
    result = _prepare_with(ctx, False, _ScriptedModel([]))
    kept = sorted(path.name for path in frames_dir.glob("frame-*.jpg"))
    harness.check("only the images of the frames this run took stay",
                  not stale.exists() and kept == sorted(Path(item["path"]).name for item in result["items"]), str(kept))


def test_with_the_filter_off_no_model_is_built_and_no_frame_is_dropped():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    model = _ScriptedModel([])
    result = _prepare_with(ctx, False, model)
    harness.check("no model is built or asked", result["built"] == [] and model.calls == [])
    harness.check("every frame stays", len(result["items"]) == 3 and result["droppedFrames"] == [],
                  str(len(result["items"])))


def test_each_call_is_sent_the_keep_and_remove_rules_and_each_frame_named_by_its_id():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_three_scene_video(folder)
    ctx = _build_context(video)
    frames = [Path(item["path"]).name for item in _prepare_with(ctx, False, _ScriptedModel([]))["items"]]
    messages = frame_redundancy_messages.build_frame_redundancy_messages(
        [(f"F00{n}", ctx.paths.output_root / "frames" / name) for n, name in enumerate(frames, start=1)], 320)
    system = messages[0].content
    harness.check("the instructions carry the keep and remove rules, the last copy kept",
                  "Which frames to keep and which to remove" in system and "keep the last one" in system, system[-600:])
    texts = [part["text"] for part in messages[1].content if part.get("type") == "text"]
    harness.check("each frame is named by its id before its image", texts[1:4] == ["Frame F001:", "Frame F002:",
                                                                               "Frame F003:"], str(texts))


def test_the_run_header_names_the_model_the_redundancy_filter_calls():
    frames = config.settings.frames
    previous = frames.is_redundancy_filter_on
    try:
        frames.is_redundancy_filter_on = True
        on_line = run_command.describe_redundancy_model()
        frames.is_redundancy_filter_on = False
        off_line = run_command.describe_redundancy_model()
    finally:
        frames.is_redundancy_filter_on = previous
    spec = config.settings.resolve_model(frames.redundancy_model).spec
    harness.check("with the filter on, the header names its model, spec and frames a call",
                  frames.redundancy_model in on_line and spec in on_line
                  and f"{frames.redundancy_frames_per_call} frames a call" in on_line, on_line)
    harness.check("with the filter off, the line says phase 2 calls no model", off_line.startswith("no model"), off_line)


def test_the_run_header_names_the_similarity_recheck_model_when_only_the_recheck_is_on():
    frames = config.settings.frames
    previous = (frames.is_redundancy_filter_on, frames.is_similarity_recheck_on)
    try:
        frames.is_redundancy_filter_on, frames.is_similarity_recheck_on = False, True
        line = run_command.describe_redundancy_model()
    finally:
        frames.is_redundancy_filter_on, frames.is_similarity_recheck_on = previous
    name = frames.similarity_model or frames.redundancy_model
    harness.check("the header names the recheck's model and frames a call, and no redundancy filter",
                  line.startswith(f"similarity recheck {name}:") and "redundancy filter" not in line
                  and f"{frames.similarity_frames_per_call} frames a call" in line, line)


def test_the_run_header_lists_each_phase_once_in_phase_order_and_a_video_phase_2_names_its_filter_model():
    checks = RunStartChecksResult(transcription="audio: mlx with model large-v3-turbo",
                                  diarization="diarization: off",
                                  capabilities=["capabilities not checked, this provider cannot be asked: critique_collection"])
    frames = config.settings.frames
    previous = frames.is_redundancy_filter_on
    try:
        frames.is_redundancy_filter_on = True
        video_lines = run_command.render_model_lines(checks, {"frames_processing": 4096}, is_audio=False)
        audio_lines = run_command.render_model_lines(checks, {}, is_audio=True)
    finally:
        frames.is_redundancy_filter_on = previous
    phases = [line.split()[0] for line in video_lines[:1 + len(config.PHASE_DEFAULT_MODELS)]]
    harness.check("phase 1 first, with its engine and diarization on one line, then phases 2 to 7 in order",
                  phases == ["transcribe", *config.PHASE_DEFAULT_MODELS]
                  and "large-v3-turbo · diarization: off" in video_lines[0], "\n".join(video_lines))
    harness.check("a video's phase 2 names the redundancy filter's model, never the document model",
                  "redundancy filter" in video_lines[1] and "document:" not in video_lines[1], video_lines[1])
    harness.check("an audio recording's phase 2 names its document model", "document:" in audio_lines[1], audio_lines[1])
    harness.check("the window a phase's host loaded is on its line", "window 4,096" in video_lines[2], video_lines[2])
    harness.check("phase 3's line says its frames a call", f"{config.settings.resolve_frames_per_call()} frames a call"
                  in video_lines[2], video_lines[2])
    harness.check("the capability lines come last", video_lines[-1].strip() == checks.capabilities[0],
                  "\n".join(video_lines))


def test_phase_2_records_each_stage_and_prints_a_line_with_its_seconds_a_frame_as_each_ends():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    lines: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    ctx.log.addHandler(handler)
    ctx.log.setLevel(logging.INFO)
    ticks = iter(range(100, 1000, 10))
    previous_clock = frames_preparing_progress_service.set_clock(lambda: float(next(ticks)))
    model = _ScriptedModel([_reply(*[{"frame_id": frame_id, "decision": "KEEP", "reason": "kept"}
                                     for frame_id in _IDS])] * 3)
    try:
        _prepare_with(ctx, True, model)
        stages = frames_preparing_progress_service.read_progress(ctx.run_dir)
    finally:
        frames_preparing_progress_service.set_clock(previous_clock)
        ctx.log.removeHandler(handler)
    harness.check("the record lists the rules and the model, the classifier being off, both done",
                  [(stage["name"], stage["status"]) for stage in stages] == [("rules", "done"), ("model", "done")],
                  str(stages))
    harness.check("the model stage counted the frames its call judged, of all of them",
                  (stages[1]["done"], stages[1]["total"], stages[1]["frames"]) == (3, 3, 3), str(stages[1]))
    done_lines = [line for line in lines if "done (" in line]
    harness.check("one console line per stage as it ends, numbered, with its frames, seconds and seconds a frame",
                  len(done_lines) == 2 and done_lines[0].startswith("  stage 1 of 2 done (rules): 3 frame(s) in ")
                  and done_lines[1].startswith("  stage 2 of 2 done (model): 3 frame(s) in ")
                  and all(" s a frame" in line for line in done_lines), "\n".join(lines))


def test_phase_1_records_its_transcription_and_speakers_stages_in_their_own_record():
    run_dir = Path(tempfile.mkdtemp())
    lines: list[str] = []
    log = logging.getLogger(f"phase-1-stages-{run_dir.name}")
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    ticks = iter([100.0, 160.0, 160.0, 190.0, 200.0])
    previous_clock = frames_preparing_progress_service.set_clock(lambda: next(ticks))
    progress = frames_preparing_progress_service
    try:
        progress.open_progress(run_dir, [progress.TRANSCRIPTION, progress.SPEAKERS], progress.TRANSCRIBE_PROGRESS_FILE)
        progress.start_stage(run_dir, progress.TRANSCRIPTION, None, progress.TRANSCRIBE_PROGRESS_FILE)
        progress.finish_transcribe_stage(run_dir, progress.TRANSCRIPTION, log, "956 segment(s)")
        progress.start_stage(run_dir, progress.SPEAKERS, None, progress.TRANSCRIBE_PROGRESS_FILE)
        progress.finish_transcribe_stage(run_dir, progress.SPEAKERS, log, "diarization done")
        stages = progress.read_progress(run_dir, progress.TRANSCRIBE_PROGRESS_FILE)
    finally:
        frames_preparing_progress_service.set_clock(previous_clock)
    harness.check("phase 1's two stages are done with their seconds, in a record of their own",
                  [(stage["name"], stage["status"], stage["seconds"]) for stage in stages]
                  == [("transcription", "done", 60.0), ("speakers", "done", 30.0)]
                  and not (run_dir / progress.PROGRESS_FILE).exists(), str(stages))
    harness.check("each stage's end is one console line with its seconds and what it found",
                  lines == ["  stage 1 of 2 done (transcription): 60.0 s · 956 segment(s)",
                            "  stage 2 of 2 done (speakers): 30.0 s · diarization done"], str(lines))

