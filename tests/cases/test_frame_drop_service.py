"""Frames dropped from a run after phase 2, before phase 3 reads them: by hand or from a Refine proposal. They leave
the frames file and every run record, their images move into frames/dropped/, their speech goes to the frame before,
and a frame phase 3 already read is never dropped.

The run folder is built the way phases 2 and 3 leave it: four frames, the first already read (its `record`), a
transcript whose words fall in each frame's window, and two run records holding the frames' items.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import stat
import tempfile
from pathlib import Path

from support import harness

from utils import document_plan_utils

from cli import frame_drop_command
from models.frame_drop_result import FrameDropResult
from models.run_paths import RunPaths
from services import frame_drop_service
from services import integration_service
from services import knowledge_correction_service
from services import phase_file_service
from services import run_operations_service

FAKE_CLASSIFIER = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
folder, out = Path(sys.argv[2]), Path(sys.argv[4])
if sys.argv[sys.argv.index("--task") + 1] != "tags":
    sys.exit("only the tags task is asked")
frames = [{"frame": p.name, "peopleScore": 0.9 if p.name == "frame-002.jpg" else 0.1,
           "tag": "people" if p.name == "frame-002.jpg" else "text", "isPlainText": True, "milliseconds": 1.0}
          for p in sorted(folder.glob("*.jpg"))]
out.write_text(json.dumps({"classifier": "tags", "task": "tags", "peopleThreshold": 0.8, "folder": str(folder),
                           "frames": frames}))
"""


def _build_run_folder() -> RunPaths:
    folder = Path(tempfile.mkdtemp()) / "AcmeSync"
    (folder / "frames").mkdir(parents=True)
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    items = []
    for index, (timestamp, step_of) in enumerate([(0.0, None), (10.0, None), (20.0, None), (30.0, "frame-002")]):
        slug = f"frame-{index:03d}"
        (folder / "frames" / f"{slug}.jpg").write_bytes(b"jpg")
        items.append({"slug": slug, "index": index, "frameNumber": index * 100, "timestamp": timestamp,
                      "path": f"frames/{slug}.jpg", "repeatOf": None, "stepOf": step_of,
                      "transcriptBefore": "", "transcriptCurrent": f"old {index}"})
    items[0]["record"] = {"facts": ["Jonas opens the gateway"]}
    # One spoken line per frame's window: "w1" while frame 0 is on screen, "w11" while frame 1 is, and so on
    segments = [{"text": f" w{second}", "start": float(second), "end": second + 0.5,
                 "words": [{"word": f" w{second}", "start": float(second), "end": second + 0.5}]}
                for second in (1, 11, 21, 31)]
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "en",
                                                   "text": "", "segments": segments})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "items": items})
    for run_id in ("2026-10-10_09-00", "2026-10-10_10-00"):
        (folder / "logs" / run_id).mkdir(parents=True)
        (folder / "logs" / run_id / "run.json").write_text(json.dumps(
            {"runId": run_id, "phases": {"frames_preparing": "complete"},
             "items": {item["slug"]: {**item, "status": "written" if "record" in item else "pending", "attempts": 0}
                       for item in items}}), encoding="utf-8")

    return paths


def _build_classifier(paths: RunPaths) -> Path:
    """A stand-in classifier command in a project folder of its own, beside the run folder, so learning data a test
    saves lands there and never in the real classifier project."""

    project = paths.output_root.parent / "frame-knowledge-classifier"
    project.mkdir(exist_ok=True)
    command = project / "classifier"
    command.write_text(FAKE_CLASSIFIER, encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)

    return command


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_dropped_frame_leaves_the_frames_file_and_every_run_record_and_its_speech_goes_to_the_frame_before():
    paths = _build_run_folder()
    result = frame_drop_service.drop_frames(paths, ["frame-002"], [], {})
    dropped = result.dropped
    harness.check("with no Refine nothing is saved as learning data",
                  result == FrameDropResult(["frame-002"]) and result.corrections_dir is None, str(result))
    frames_file = _read(paths.loop_items)
    items = {item["slug"]: item for item in frames_file["items"]}

    harness.check("the frame is dropped and named back", dropped == ["frame-002"], str(dropped))
    harness.check("it leaves the frame list and is listed under droppedFrames, saying who dropped it",
                  list(items) == ["frame-000", "frame-001", "frame-003"]
                  and [(entry["slug"], entry["path"], entry["droppedBy"]) for entry in frames_file["droppedFrames"]]
                  == [("frame-002", "frames/dropped/frame-002.jpg", "hand")], json.dumps(frames_file)[:500])
    harness.check("its image moved into frames/dropped/",
                  (paths.output_root / "frames/dropped/frame-002.jpg").is_file()
                  and not (paths.output_root / "frames/frame-002.jpg").exists())
    harness.check("what was said while it was on screen goes to the frame before it",
                  items["frame-001"]["transcriptCurrent"] == "w11 w21", str(items["frame-001"]))
    harness.check("a frame phase 3 already read keeps the speech its notes were written from",
                  items["frame-000"]["transcriptCurrent"] == "old 0", str(items["frame-000"]))
    harness.check("the step of the dropped frame's build becomes a frame of its own",
                  items["frame-003"]["stepOf"] is None, str(items["frame-003"]))
    for store in sorted((paths.output_root / "logs").glob("*/run.json")):
        stored = _read(store)["items"]
        harness.check(f"{store.parent.name}'s record no longer holds the frame, and its kept frames hold the new speech",
                      list(stored) == ["frame-000", "frame-001", "frame-003"]
                      and stored["frame-001"]["transcriptCurrent"] == "w11 w21"
                      and stored["frame-001"]["status"] == "pending" and stored["frame-000"]["status"] == "written",
                      json.dumps(stored)[:400])


def test_a_frame_refine_proposed_is_recorded_as_dropped_by_refine_with_its_score_one_added_by_hand_as_hand():
    paths = _build_run_folder()
    command = _build_classifier(paths)
    previous = frame_drop_service.config.settings.frames.knowledge_classifier_command
    frame_drop_service.config.settings.frames.knowledge_classifier_command = str(command)
    try:
        frame_drop_service.drop_frames(paths, ["frame-002", "frame-003"], ["frame-002"],
                                       {"frame-002": 0.91, "frame-003": 0.2})
    finally:
        frame_drop_service.config.settings.frames.knowledge_classifier_command = previous
    dropped = {entry["slug"]: (entry["droppedBy"], entry["dropScore"]) for entry in _read(paths.loop_items)["droppedFrames"]}

    harness.check("the proposed frame reads refine, the one added reads hand, each with its score",
                  dropped == {"frame-002": ("refine", 0.91), "frame-003": ("hand", 0.2)}, str(dropped))


def test_a_frame_phase_3_read_or_not_in_the_list_is_refused_and_nothing_is_dropped():
    paths = _build_run_folder()
    before = paths.loop_items.read_text(encoding="utf-8")
    for slugs, expected in ((["frame-002", "frame-000"], "frame-000"), (["frame-009"], "frame-009")):
        try:
            frame_drop_service.drop_frames(paths, slugs, [], {})
            message = "no refusal"
        except ValueError as exc:
            message = str(exc)
        harness.check(f"dropping {slugs} is refused naming {expected}", expected in message, message)
    harness.check("the frames file is unchanged and no image moved",
                  paths.loop_items.read_text(encoding="utf-8") == before
                  and not (paths.output_root / "frames/dropped").exists())


def test_refine_proposes_only_frames_phase_3_has_not_read_and_changes_nothing():
    paths = _build_run_folder()
    command = _build_classifier(paths)
    before = paths.loop_items.read_text(encoding="utf-8")
    frames = frame_drop_service.config.settings.frames
    previous = frames.knowledge_classifier_command
    frames.knowledge_classifier_command = str(command)
    try:
        result = run_operations_service.list_or_drop_proposed_frames(paths.output_root)
    finally:
        frames.knowledge_classifier_command = previous

    harness.check("every frame phase 3 has not read is judged, the read one is not",
                  [(record["slug"], record["decision"]) for record in result.records]
                  == [("frame-001", "keep"), ("frame-002", "drop"), ("frame-003", "keep")], str(result.records))
    harness.check("the lines name the proposed frame with its score", result.exit_code == 0
                  and "  frame-002  drop score 0.90" in result.lines and "1 of 3" in result.lines[0], result.output)
    harness.check("nothing in the run folder changed", paths.loop_items.read_text(encoding="utf-8") == before
                  and (paths.output_root / "frames/frame-002.jpg").is_file())

    frames.knowledge_classifier_command = str(command)
    try:
        accepted = run_operations_service.list_or_drop_proposed_frames(paths.output_root, True)
    finally:
        frames.knowledge_classifier_command = previous
    dropped = {entry["slug"]: (entry["droppedBy"], entry["dropScore"]) for entry in _read(paths.loop_items)["droppedFrames"]}
    harness.check("with --yes the whole proposal is dropped, as dropped by refine with its score",
                  accepted.exit_code == 0 and dropped == {"frame-002": ("refine", 0.9)}
                  and "  frame-002" in accepted.lines
                  and accepted.lines[-1] == "learning data: the proposal was accepted as it stands; nothing to correct",
                  accepted.output + str(dropped))


def test_drop_without_yes_lists_and_a_live_run_is_refused():
    paths = _build_run_folder()
    listed = run_operations_service.drop_run_frames(paths.output_root, ["frame-002"], False)
    harness.check("without confirmation it only lists", listed.exit_code == 0 and "  frame-002" in listed.lines
                  and (paths.output_root / "frames/frame-002.jpg").is_file(), listed.output)
    previous = run_operations_service.run_reset_service.list_live_runs
    run_operations_service.run_reset_service.list_live_runs = lambda output_root: ["123 python main.py run"]
    try:
        refused = run_operations_service.drop_run_frames(paths.output_root, ["frame-002"], True)
    finally:
        run_operations_service.run_reset_service.list_live_runs = previous
    harness.check("a folder a run writes into is refused, and nothing is dropped",
                  refused.exit_code == 2 and "stop it first" in refused.output
                  and (paths.output_root / "frames/frame-002.jpg").is_file(), refused.output)


def test_the_drop_frames_command_drops_only_with_yes_and_says_so():
    paths = _build_run_folder()
    logger = logging.getLogger("test-drop-frames")
    listed = frame_drop_command.cmd_drop_frames(argparse.Namespace(frames=["frame-002"], output_root=paths.output_root,
                                                                   is_confirmed=False, logger=logger))
    is_kept_after_listing = (paths.output_root / "frames/frame-002.jpg").is_file()
    dropped = frame_drop_command.cmd_drop_frames(argparse.Namespace(frames=["frame-002"], output_root=paths.output_root,
                                                                    is_confirmed=True, logger=logger))
    harness.check("without --yes the command lists and exits 0; with it the frame is dropped, by hand",
                  listed == 0 and is_kept_after_listing and dropped == 0
                  and [entry["droppedBy"] for entry in _read(paths.loop_items)["droppedFrames"]] == ["hand"])


def test_an_accepted_proposal_saves_its_fp_and_fn_as_learning_data_and_one_accepted_unchanged_saves_nothing():
    paths = _build_run_folder()
    command = _build_classifier(paths)
    corrections_dir = command.parent / knowledge_correction_service.CORRECTIONS_DIR_NAME / paths.output_root.name
    frames = frame_drop_service.config.settings.frames
    previous = frames.knowledge_classifier_command
    frames.knowledge_classifier_command = str(command)
    scores = {"frame-001": 0.85, "frame-002": 0.9, "frame-003": 0.2}
    try:
        unchanged = run_operations_service.drop_run_frames(paths.output_root, ["frame-002"], True, ["frame-002"], scores)
        is_saved_unchanged = corrections_dir.exists()
        nothing_kept = run_operations_service.drop_run_frames(paths.output_root, [], True, ["frame-001"], scores)
        corrected = run_operations_service.drop_run_frames(paths.output_root, ["frame-003"], True, [], scores)
    finally:
        frames.knowledge_classifier_command = previous
    saved = _read(corrections_dir / "corrections.json")["frames"]

    harness.check("a proposal accepted as it stands saves nothing, and says so",
                  not is_saved_unchanged and "nothing to correct" in unchanged.output, unchanged.output)
    harness.check("a proposed frame the person kept is saved as FP, labelled keep, though nothing is dropped",
                  nothing_kept.exit_code == 0 and "  FP frame-001 (proposed, kept)" in nothing_kept.lines
                  and (paths.output_root / "frames/frame-001.jpg").is_file(), nothing_kept.output)
    harness.check("a frame the person dropped that was not proposed is saved as FN, labelled drop, beside the first",
                  "  FN frame-003 (dropped, not proposed)" in corrected.lines
                  and [(entry["frame"], entry["outcome"], entry["label"], entry["dropScore"]) for entry in saved]
                  == [("frame-001.jpg", "FP", "keep", 0.85), ("frame-003.jpg", "FN", "drop", 0.2)], str(saved))
    harness.check("the frames are copied in and labels.csv lists them as a dataset does",
                  sorted(path.name for path in (corrections_dir / "frames").iterdir()) == ["frame-001.jpg", "frame-003.jpg"]
                  and (corrections_dir / "labels.csv").read_text(encoding="utf-8").splitlines()
                  == ["frame,label", "frame-001.jpg,keep", "frame-003.jpg,drop"])


def _find_speech_entry(paths: RunPaths,
                       word: str) -> tuple[dict, bool]:
    """The entry phase 4 writes from that holds the transcript line with `word`, and whether a section must cite it."""

    entries = document_plan_utils.select_document_entries(integration_service.build_integration(paths))
    entry = next(entry for entry in entries if entry["kind"] == "speech" and word in entry["text"])

    return entry, entry["id"] in document_plan_utils.list_required_ids(entries)


def test_speech_of_a_frame_dropped_after_the_frame_before_it_was_read_reaches_phase_4_as_speech_no_frame_heard():
    paths = _build_run_folder()
    frame_drop_service.drop_frames(paths, ["frame-001"], [], {})
    entry, is_required = _find_speech_entry(paths, "w11")

    harness.check("the dropped frame's words are not filed as heard by the frame before it, whose notes never had them",
                  not entry["heardByFrame"] and "w1" not in entry["text"].split(), str(entry))
    harness.check("so a section must cite them, and they cannot be left out of the document", is_required, str(entry))


def test_speech_of_a_frame_dropped_after_an_unread_frame_goes_to_that_frame_which_will_hear_it():
    paths = _build_run_folder()
    frame_drop_service.drop_frames(paths, ["frame-002"], [], {})
    frame_one = next(item for item in _read(paths.loop_items)["items"] if item["slug"] == "frame-001")

    harness.check("the frame before, not read yet, is handed the dropped frame's words for phase 3",
                  frame_one["transcriptCurrent"] == "w11 w21", str(frame_one))
    harness.check("and no span of unread speech is made for them", integration_service.build_integration(paths)
                  ["unreadSpeech"] == [{"start": 10.0, "end": 30.0, "why": "frame-001 has no accepted record"},
                                       {"start": 30.0, "end": None, "why": "frame-003 has no accepted record"}],
                  str(integration_service.build_integration(paths)["unreadSpeech"]))


def test_frames_after_the_talk_s_last_words_of_substance_are_dropped_defect():
    # Defect: a broadcast's closing cards (its noise transition, its credits) were taken as frames after the last word
    # of the talk, said over with thanks alone, and phase 3 read each. Fix: frames after the talk's last segment of substance go.
    from utils import talk_end_utils

    segments = [{"start": 0.0, "end": 50.0, "text": "The gateway splits each refund into three parts."},
                {"start": 50.0, "end": 60.0, "text": "Thank you. Thank you very much."},
                {"start": 60.0, "end": 64.0, "text": "Thanks again, everyone!"}]
    harness.check("the talk ends with its last segment of substance", talk_end_utils.find_talk_end(segments) == 50.0)
    harness.check("a transcript of thanks alone has no end to cut at", talk_end_utils.find_talk_end(segments[1:]) is None)


def test_words_nobody_said_over_the_closing_card_do_not_end_the_talk_defect():
    # A talk ended on its thanks, then the speech model wrote two segments of words at a mean probability under 0.1 over
    # the licence card, which counted as the talk and kept the card. Now such a segment is no talk
    from utils import talk_end_utils

    def words(text: str, probability: float) -> list[dict]:
        return [{"word": word, "probability": probability} for word in text.split()]

    segments = [{"start": 0.0, "end": 50.0, "text": "The gateway splits each refund.",
                 "words": words("The gateway splits each refund.", 0.95)},
                {"start": 50.0, "end": 51.0, "text": "Danke.", "words": words("Danke.", 1.0)},
                {"start": 55.0, "end": 58.0, "text": "Also ist es nicht ein", "words": words("Also ist es nicht ein", 0.04)}]
    harness.check("the talk ends where its heard words end", talk_end_utils.find_talk_end(segments) == 50.0,
                  str(talk_end_utils.find_talk_end(segments)))


def test_phase_2_drops_the_frames_after_the_talk_and_keeps_the_talk_s():
    import tempfile
    from pathlib import Path
    from services import frame_preparation_service

    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    items = []
    for index, timestamp in enumerate((10.0, 45.0, 58.0)):
        (frames_dir / f"frame-{index:03d}.jpg").write_bytes(b"jpeg")
        items.append({"slug": f"frame-{index:03d}", "index": index, "frameNumber": index, "timestamp": timestamp,
                      "path": f"frames/frame-{index:03d}.jpg"})
    segments = [{"start": 0.0, "end": 50.0, "text": "The gateway splits each refund into three parts."},
                {"start": 50.0, "end": 60.0, "text": "Thank you very much."}]
    kept, dropped = frame_preparation_service._drop_after_talk(items, segments, frames_dir)
    harness.check("the frame after the talk is dropped as after-talk, its image moved, the talk's frames kept",
                  [item["slug"] for item in kept] == ["frame-000", "frame-001"]
                  and [(entry["slug"], entry["droppedBy"]) for entry in dropped] == [("frame-002", "after-talk")]
                  and (frames_dir / "dropped" / "frame-002.jpg").is_file(), str(dropped))


def test_phase_2_drops_a_frame_with_no_text_before_the_talk_and_keeps_a_title_card_defect():
    # A broadcast's opening animation, the conference's logo with no word, reached phase 3 as the first frame. Now a
    # frame taken before the first words of substance on which no reader finds three words leaves as before-talk
    import tempfile
    from pathlib import Path
    from services import apple_text_service
    from services import frame_preparation_service
    from services import text_detection_service

    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    words = {"frame-000.jpg": 0, "frame-001.jpg": 4, "frame-002.jpg": 0}
    items = []
    for index, timestamp in enumerate((6.0, 10.0, 40.0)):
        (frames_dir / f"frame-{index:03d}.jpg").write_bytes(b"jpeg")
        items.append({"slug": f"frame-{index:03d}", "index": index, "frameNumber": index, "timestamp": timestamp,
                      "path": f"frames/frame-{index:03d}.jpg"})
    segments = [{"start": 3.0, "end": 5.0, "text": "Okay, thanks."},
                {"start": 15.0, "end": 30.0, "text": "The gateway splits each refund into three parts."}]
    saved = (text_detection_service.is_available, text_detection_service.find_words, apple_text_service.is_available)
    text_detection_service.is_available = lambda: True
    text_detection_service.find_words = lambda path, confidence: [{"text": "word"}] * words[path.name]
    apple_text_service.is_available = lambda: False
    try:
        kept, dropped = frame_preparation_service._drop_before_talk(items, segments, frames_dir)
    finally:
        text_detection_service.is_available, text_detection_service.find_words, apple_text_service.is_available = saved
    harness.check("the wordless frame before the talk goes, the title card before it and the empty frame in it stay",
                  [item["slug"] for item in kept] == ["frame-001", "frame-002"]
                  and [(entry["slug"], entry["droppedBy"]) for entry in dropped] == [("frame-000", "before-talk")],
                  f"{[item['slug'] for item in kept]} {dropped}")


def _draw_slide(path: Path,
                hand_x: int | None = None,
                is_box_added: bool = False) -> None:
    """A slide of text bars in its upper part, free space below, a striped hand over the bars at `hand_x`, and a box
    added on the free space."""

    import cv2
    import numpy

    image = numpy.full((720, 1280, 3), 255, numpy.uint8)
    for row in range(80, 420, 34):
        for column in range(80, 1180, 140):
            cv2.rectangle(image, (column, row), (column + 110, row + 14), (40, 40, 40), -1)
    if hand_x is not None:
        hand = numpy.zeros((720, 1280), numpy.uint8)
        cv2.ellipse(hand, (hand_x, 250), (110, 150), 0, 0, 360, 255, -1)
        stripes = numpy.where((numpy.arange(1280)[None, :] // 4) % 2 == 0, 90, 200).astype(numpy.uint8)
        image[hand > 0] = numpy.repeat(numpy.broadcast_to(stripes, (720, 1280))[..., None], 3, axis=2)[hand > 0]
    if is_box_added:
        cv2.rectangle(image, (300, 500), (900, 660), (30, 90, 200), -1)
    cv2.imwrite(str(path), image)


def test_a_build_step_whose_only_change_is_a_hand_moved_over_the_screen_is_dropped_defect():
    # A frame whose only change was a drawn hand moved over the same screen was taken as a build step and read as a
    # frame. Phase 2 now drops a step whose changed blocks held content before and which shows no new word
    from services import frame_preparation_service
    from services import text_detection_service

    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    _draw_slide(frames_dir / "frame-000.jpg", hand_x=400)
    _draw_slide(frames_dir / "frame-001.jpg", hand_x=700)
    _draw_slide(frames_dir / "frame-002.jpg", hand_x=700, is_box_added=True)
    _draw_slide(frames_dir / "frame-003.jpg", hand_x=900)
    words = {"frame-000.jpg": ["Gateway", "refunds"], "frame-001.jpg": ["Gateway", "refunds"],
             "frame-002.jpg": ["Gateway", "refunds"], "frame-003.jpg": ["Gateway", "refunds", "AcmeSync", "rur"]}
    items = [{"slug": f"frame-{index:03d}", "index": index, "frameNumber": index, "timestamp": index * 6.0,
              "path": f"frames/frame-{index:03d}.jpg", "stepOf": "frame-000" if index else None} for index in range(4)]
    previous = text_detection_service.find_words, text_detection_service.is_available
    text_detection_service.find_words = lambda path, *args, **kwargs: [
        {"text": text, "box": [0, 0, 10, 10]} for text in words.get(Path(path).name, [])]
    text_detection_service.is_available = lambda: True
    try:
        kept, dropped = frame_preparation_service._drop_empty_steps(items, frames_dir)
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the hand moved over the bars is dropped as an empty step, its image moved",
                  [(entry["slug"], entry["droppedBy"]) for entry in dropped] == [("frame-001", "empty-step")]
                  and (frames_dir / "dropped" / "frame-001.jpg").is_file(), str(dropped))
    harness.check("a box added on free space and a step showing a new word stay",
                  [item["slug"] for item in kept] == ["frame-000", "frame-002", "frame-003"], str(kept))


def test_a_build_step_that_is_the_frame_before_moved_is_dropped_defect():
    # A talk's broadcast showed one slide a few pixels off or at another scale eight times over, each copy a step read
    # by phase 3; a step that is the frame before moved, unchanged once lined up, is now dropped, a typed line is not
    import cv2
    import numpy

    from services import empty_step_service
    from services import text_detection_service

    frames_dir = Path(tempfile.mkdtemp())
    slide = numpy.full((720, 1280, 3), 250, numpy.uint8)
    for row in range(60, 660, 40):
        for column in range(60, 1220, 150):
            cv2.rectangle(slide, (column, row), (column + 120 - (row % 3) * 20, row + 18), (60, 60, 60), -1)
    cv2.imwrite(str(frames_dir / "before.jpg"), slide)
    moved = cv2.warpAffine(slide, numpy.float32([[0.96, 0, 30], [0, 0.96, 24]]), (1280, 720), borderValue=(250, 250, 250))
    cv2.imwrite(str(frames_dir / "moved.jpg"), moved)
    typed = slide.copy()
    cv2.rectangle(typed, (60, 620), (700, 640), (20, 20, 20), -1)
    cv2.imwrite(str(frames_dir / "typed.jpg"), typed)
    previous = text_detection_service.find_words
    # read at another scale, a word comes back a letter off, which the rule before counted as new
    words = {"before.jpg": ["Gateway", "refunds", "AcmeSync"], "moved.jpg": ["Gatewav", "refunds", "AcmeSync"],
             "typed.jpg": ["Gateway", "refunds", "AcmeSync"]}
    text_detection_service.find_words = lambda path, *args, **kwargs: [
        {"text": text, "box": [0, 0, 10, 10]} for text in words[Path(path).name]]
    try:
        harness.check("the slide moved and scaled adds nothing",
                      empty_step_service.is_empty_step(frames_dir / "before.jpg", frames_dir / "moved.jpg"))
        harness.check("a typed line is no moved copy",
                      not empty_step_service._is_moved_copy(frames_dir / "before.jpg", frames_dir / "typed.jpg",
                                                            empty_step_service.change_capture_utils.map_changed_blocks(
                                                                empty_step_service._read_sample(frames_dir / "before.jpg"),
                                                                empty_step_service._read_sample(frames_dir / "typed.jpg"))))
    finally:
        text_detection_service.find_words = previous


def test_a_frame_no_step_joined_that_is_the_frame_before_moved_is_dropped_defect():
    # One slide shown a few pixels off reached phase 3 as frames no build step joined; phase 2 now drops a frame that is
    # the kept frame before it moved or scaled, as `moved-copy`, and keeps a frame of its own screen
    import cv2
    import numpy

    from services import frame_preparation_service
    from services import text_detection_service

    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    slide = numpy.full((720, 1280, 3), 250, numpy.uint8)
    for row in range(60, 660, 40):
        for column in range(60, 1220, 150):
            cv2.rectangle(slide, (column, row), (column + 120 - (row % 3) * 20, row + 18), (60, 60, 60), -1)
    other = numpy.full((720, 1280, 3), 250, numpy.uint8)
    cv2.circle(other, (640, 360), 250, (40, 90, 200), -1)
    cv2.imwrite(str(frames_dir / "frame-000.jpg"), slide)
    cv2.imwrite(str(frames_dir / "frame-001.jpg"),
                cv2.warpAffine(slide, numpy.float32([[0.96, 0, 30], [0, 0.96, 24]]), (1280, 720), borderValue=(250, 250, 250)))
    cv2.imwrite(str(frames_dir / "frame-002.jpg"), other)
    words = {"frame-000.jpg": ["Gateway", "refunds"], "frame-001.jpg": ["Gatewav", "refunds"], "frame-002.jpg": ["AcmeSync"]}
    items = [{"slug": f"frame-{index:03d}", "index": index, "frameNumber": index, "timestamp": index * 6.0,
              "path": f"frames/frame-{index:03d}.jpg", "stepOf": None} for index in range(3)]
    previous = text_detection_service.find_words, text_detection_service.is_available
    text_detection_service.find_words = lambda path, *args, **kwargs: [
        {"text": text, "box": [0, 0, 10, 10]} for text in words.get(Path(path).name, [])]
    text_detection_service.is_available = lambda: True
    try:
        kept, dropped = frame_preparation_service._drop_moved_copies(items, frames_dir)
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the slide moved is dropped as a moved copy",
                  [(entry["slug"], entry["droppedBy"]) for entry in dropped] == [("frame-001", "moved-copy")], str(dropped))
    harness.check("the slide and the new screen stay", [item["slug"] for item in kept] == ["frame-000", "frame-002"],
                  str(kept))


def _draw_terminal(lines: list[str]) -> "numpy.ndarray":
    import cv2
    import numpy

    image = numpy.full((360, 520, 3), 250, numpy.uint8)
    for row, line in enumerate(lines):
        cv2.putText(image, line, (6, 16 + row * 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (20, 20, 20), 1)

    return image


def test_a_terminal_scrolled_with_nothing_new_is_a_copy_and_one_with_a_new_line_is_not_defect():
    # A terminal scrolled by one line with its last line the same was kept as a frame: ECC lined it up under a pixel
    # off, never by the scroll. The scroll is found from its rows now, and the two last text lines are compared
    import cv2

    from services import empty_step_service

    words = ("set", "get", "read", "module", "channel", "value", "frequency", "address", "memory", "sensor", "output",
             "reset", "status", "scan", "noise", "power", "baud")
    help_lines = [f"{words[index % 17]}{index}: " + " ".join(words[(index * 5 + offset * 3) % 17]
                                                         for offset in range(3 + index % 6)) for index in range(25)]
    with tempfile.TemporaryDirectory() as folder:
        paths = {name: Path(folder) / f"{name}.png" for name in ("before", "copy", "added")}
        cv2.imwrite(str(paths["before"]), _draw_terminal(help_lines))
        cv2.imwrite(str(paths["copy"]), _draw_terminal(help_lines[1:] + [""]))
        cv2.imwrite(str(paths["added"]), _draw_terminal(help_lines[1:] + ["RESULT From: 10 Data: 349 305 0 0 Err: 1"]))
        harness.check("scrolled one line with nothing new is a copy",
                      empty_step_service.is_moved_copy(paths["before"], paths["copy"]))
        harness.check("scrolled one line with a new output line is not",
                      not empty_step_service.is_moved_copy(paths["before"], paths["added"]))


def test_a_screen_shown_again_after_two_other_frames_is_a_copy_defect():
    # A terminal scrolled away and back showed an earlier frame's screen again, two frames between them, and was kept:
    # the copy rule compared each frame with the one before only. It looks back over three kept frames now
    import cv2

    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    words = ("set", "get", "read", "module", "channel", "value", "frequency", "address", "memory", "sensor", "output",
             "reset", "status", "scan", "noise", "power", "baud")
    lines = [f"{words[index % 17]}{index}: " + " ".join(words[(index * 5 + offset * 3) % 17]
                                                     for offset in range(3 + index % 6)) for index in range(60)]
    with tempfile.TemporaryDirectory() as folder:
        screens = {"first": lines[:25], "other": lines[30:55], "third": lines[35:60], "again": lines[1:25] + [""]}
        kept = []
        for name, shown in screens.items():
            cv2.imwrite(str(Path(folder) / f"{name}.png"), _draw_terminal(shown))
            kept.append({"slug": name, "path": f"frames/{name}.png"})
        copies = [item["slug"] for item in empty_step_service.list_moved_copies(kept, Path(folder))]
        harness.check("the first screen scrolled a line, after two other screens, is a copy", copies == ["again"], str(copies))


def test_a_panel_drawn_over_a_blurred_photo_is_no_empty_step_defect():
    # A zoom panel drawn over a talk's blurred photo was dropped as a step adding nothing: the blur counted as content
    # and the panel held no word. A step whose changed blocks turn from smooth to sharp is kept
    import cv2
    import numpy

    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    noise = numpy.random.default_rng(7).integers(0, 255, (360, 640, 3)).astype(numpy.uint8)
    photo = cv2.normalize(cv2.GaussianBlur(noise, (0, 0), 3), None, 20, 235, cv2.NORM_MINMAX)   # a soft, grainy photo
    panel = photo.copy()
    cv2.rectangle(panel, (380, 120), (520, 260), (255, 255, 255), -1)
    for offset in range(130, 260, 14):
        cv2.line(panel, (380, offset), (520, offset), (0, 0, 0), 1)
        cv2.line(panel, (380 + offset - 120, 120), (380 + offset - 120, 260), (0, 0, 0), 1)
    cv2.circle(panel, (450, 190), 50, (0, 0, 0), 2)
    with tempfile.TemporaryDirectory() as folder:
        before, step = Path(folder) / "before.png", Path(folder) / "step.png"
        cv2.imwrite(str(before), photo)
        cv2.imwrite(str(step), panel)
        harness.check("a sharp panel drawn over the blur is no empty step",
                      not empty_step_service.is_empty_step(before, step))


def test_a_step_bringing_its_build_s_first_screen_back_is_an_empty_step_defect():
    # Defect: a closed drop-down list brought the start page's map back, its field holding a word the list showed, and
    # the step was kept. Fix: a changed block showing the build's first frame again is no change
    import cv2
    import numpy

    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    start = numpy.full((360, 640, 3), 255, numpy.uint8)
    cv2.putText(start, "REFUNDS", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    for x in range(40, 620, 30):
        for y in range(110, 350, 30):
            cv2.circle(start, (x, y), 9, (40, 90, 40), -1)   # a map's markers
    before, step = start.copy(), start.copy()
    cv2.putText(before, "LEDGER", (300, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    cv2.rectangle(before, (20, 90), (620, 350), (235, 235, 235), -1)   # the open list over the map
    cv2.putText(before, "LEDGER", (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    cv2.putText(step, "LEDGER", (300, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    with tempfile.TemporaryDirectory() as folder:
        paths = [Path(folder) / f"{name}.png" for name in ("start", "before", "step")]
        for path, image in zip(paths, (start, before, step)):
            cv2.imwrite(str(path), image)
        harness.check("the step showing the start's map again, its field a word the list showed, is empty",
                      empty_step_service.is_empty_step(paths[1], paths[2], paths[0]))


def test_a_scroll_step_is_handed_the_lines_its_scroll_brings_in_defect():
    # A terminal scrolled step by step was outlined with its old lines, the new output left out: the step was told to
    # outline what it adds and never which lines those were. Its message now lists the lines the scroll brings in
    import cv2

    from prompts import frames_processing_messages
    from services import text_detection_service
    from support import fixtures as fx

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    words = ("set", "get", "read", "module", "channel", "value", "frequency", "address", "memory", "sensor", "output",
             "reset", "status", "scan", "noise", "power", "baud")
    lines = [f"{words[index % 17]}{index}: " + " ".join(words[(index * 5 + offset * 3) % 17]
                                                     for offset in range(3 + index % 6)) for index in range(25)]
    paths = fx.make_paths()
    (paths.output_root / "frames").mkdir(exist_ok=True)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-001.png"), _draw_terminal(lines))
    cv2.imwrite(str(paths.output_root / "frames" / "frame-002.png"),
                _draw_terminal(lines[2:] + ["RESULT Gateway answered", "select Jonas"]))
    items = [{"slug": "frame-001", "index": 1, "path": "frames/frame-001.png"},
             {"slug": "frame-002", "index": 2, "path": "frames/frame-002.png", "stepOf": "frame-001"}]
    phase_file_service.write_phase_file(paths, 2, {"items": items})
    request = argparse.Namespace(paths=paths)
    note = frames_processing_messages.render_scrolled_in_note(request, items[1])
    harness.check("the step's message names the frame it scrolled and its two new lines",
                  "frame 1 scrolled" in note and "Gateway" in note and "Jonas" in note, note)
    harness.check("the first frame gets no such note", frames_processing_messages.render_scrolled_in_note(request, items[0]) == "")


def test_a_slide_stored_larger_with_nothing_new_is_a_copy_defect():
    # A broadcast's slide shown beside the speaker, then in full, was kept twice: 13 blocks changed and the text reader
    # read 3 words differently at the two sizes, so no copy rule named it. The words Apple's recognizer reads now decide
    import cv2
    import numpy

    from services import apple_text_service
    from services import empty_step_service
    from support import fixtures as fx

    def draw(width: int) -> numpy.ndarray:
        screen = numpy.full((width * 4 // 5, width, 3), 250, numpy.uint8)
        for row, line in enumerate(("Gateway limits", "AcmeSync retries", "Jonas owns the queue")):
            cv2.putText(screen, line, (width // 10, width // 5 + row * width // 10), cv2.FONT_HERSHEY_SIMPLEX,
                        width / 700, (20, 20, 20), 2)
        return screen

    folder = fx.make_paths().output_root
    small, large = folder / "small.png", folder / "large.png"
    cv2.imwrite(str(small), draw(539))
    cv2.imwrite(str(large), draw(720))
    read = {"words": []}
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: [{"text": text} for text in read["words"]]
    try:
        read["words"] = ["Gateway limits", "AcmeSync retries", "Jonas owns the queue"]
        harness.check("the same slide stored larger, its words the same, is a copy",
                      empty_step_service.is_resized_copy(small, large))
        harness.check("the same size is left to the other rules", not empty_step_service.is_resized_copy(small, small))
        words = iter([["Gateway limits"], ["Gateway limits", "python ledger.py", "Trying to connect"]])
        apple_text_service.find_words_in_image = lambda image: [{"text": text} for text in next(words)]
        harness.check("a frame at another size with new lines is no copy",
                      not empty_step_service.is_resized_copy(small, large))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_slide_kept_again_in_the_other_view_is_a_copy_defect():
    # A broadcast's slide as a panel beside the speaker and then in full, framed with other margins, changed over 100
    # blocks and was kept twice. Now the two pictures scaled to one size and the words read on each decide
    import cv2
    import numpy

    from services import apple_text_service
    from services import empty_step_service
    from support import fixtures as fx

    lines = ("Gateway limits apply", "AcmeSync retries every minute", "Jonas owns the queue today")

    def draw(width: int, margin: int, extra: str = "") -> numpy.ndarray:
        screen = numpy.full((width * 4 // 5, width, 3), 250, numpy.uint8)
        for row, line in enumerate(lines + ((extra,) if extra else ())):
            cv2.putText(screen, line, (width // 10 + margin, width // 5 + margin + row * width // 10),
                        cv2.FONT_HERSHEY_SIMPLEX, width / 700, (20, 20, 20), 2)
        return screen

    folder = fx.make_paths().output_root
    panel, full, added = folder / "panel.png", folder / "full.png", folder / "added.png"
    # the panel is the slide in full scaled down, as the broadcast draws it
    cv2.imwrite(str(panel), cv2.resize(draw(719, 2), (538, 430), interpolation=cv2.INTER_AREA))
    cv2.imwrite(str(full), draw(719, 2))
    cv2.imwrite(str(added), draw(719, 2, "Rollback window closes Friday"))
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: [{"text": line} for line in lines]
    try:
        harness.check("the same slide in the other view is a copy", empty_step_service.is_view_copy(panel, full))
        words = iter([lines, lines + ("Rollback window closes Friday",)])
        apple_text_service.find_words_in_image = lambda image: [{"text": line} for line in next(words)]
        harness.check("a slide that gained a line in the other view is no copy",
                      not empty_step_service.is_view_copy(panel, added))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_view_copy_whose_scaled_picture_differs_in_31_percent_of_its_content_is_a_copy_defect():
    # A slide shown in the other view 4 s later, its words matching, differed in 30.2% of its content pixels at the
    # scaled size, and the 30% of the repeat test kept it. The view-copy test now holds the picture the same under 33%
    import cv2
    import numpy

    from services import apple_text_service
    from services import empty_step_service
    from support import fixtures as fx

    lines = ("Gateway limits apply", "AcmeSync retries every minute", "Jonas owns the queue today")
    small = numpy.full((120, 160), 250, numpy.uint8)
    small[20:60, 20:45] = 20
    other = small.copy()
    # 31% of the 1,000 content pixels turned to background, under 2% of the picture
    other[20:60, 20:27] = 250
    other[20:50, 27] = 250
    folder = fx.make_paths().output_root
    panel, full = folder / "panel.png", folder / "full.png"
    cv2.imwrite(str(panel), cv2.cvtColor(cv2.resize(small, (640, 480), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR))
    cv2.imwrite(str(full), cv2.cvtColor(cv2.resize(other, (800, 600), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR))
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: [{"text": line} for line in lines]
    try:
        harness.check("the slide in the other view at 31% is a copy", empty_step_service.is_view_copy(panel, full))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_view_copy_stored_at_the_same_width_with_its_bar_cut_off_is_a_copy_defect():
    # A broadcast's slide in full and as a panel were both stored 1,280 wide, and the panel cut the slide's left bar
    # off: the stored-width and scaled-picture tests kept both. Now the widths of the parts of the recording they came
    # from are compared, and the panel laid onto the full slide shows the same picture
    import cv2
    import numpy

    from services import apple_text_service
    from services import empty_step_service
    from support import fixtures as fx

    lines = ("Gateway limits apply", "AcmeSync retries every minute", "Jonas owns the queue today")
    slide = numpy.full((720, 1280, 3), 250, numpy.uint8)
    slide[:, :24] = (120, 100, 0)
    for row, line in enumerate(lines):
        cv2.putText(slide, line, (120, 200 + row * 110), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3)
    folder = fx.make_paths().output_root
    full, panel = folder / "full.png", folder / "panel.png"
    cv2.imwrite(str(full), slide)
    cv2.imwrite(str(panel), cv2.resize(slide[:, 24:], (1280, 727), interpolation=cv2.INTER_AREA))
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: [{"text": line} for line in lines]
    try:
        harness.check("the panel cut from 1,417 of the 1,920 pixels is a copy of the slide in full",
                      empty_step_service.is_view_copy(full, panel, (1920, 1417)))
        harness.check("compared at their stored widths alone they are one view", not empty_step_service.is_view_copy(full, panel))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_view_copy_cut_at_another_shape_with_backdrop_below_the_slide_is_a_copy_defect():
    # A broadcast's panel view was cut 555 by 503 with backdrop under the slide, its full view 720 by 576: stretched to
    # one shape the two needed a height scale 12% off and were kept twice. Now the later view keeps its own shape
    import cv2
    import numpy

    from services import apple_text_service
    from services import empty_step_service
    from support import fixtures as fx

    lines = ("Gateway limits apply", "AcmeSync retries every minute", "Jonas owns the queue today")
    slide = numpy.full((576, 720, 3), 40, numpy.uint8)
    cv2.rectangle(slide, (120, 140), (600, 420), (200, 230, 250), -1)
    for row, line in enumerate(lines):
        cv2.putText(slide, line, (150, 200 + row * 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 160), 2)
    panel = numpy.full((503, 555, 3), 40, numpy.uint8)
    panel[:444] = cv2.resize(slide, (555, 444), interpolation=cv2.INTER_AREA)
    folder = fx.make_paths().output_root
    panel_path, full_path = folder / "panel.png", folder / "full.png"
    cv2.imwrite(str(panel_path), panel)
    cv2.imwrite(str(full_path), slide)
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: [{"text": line} for line in lines]
    try:
        harness.check("the slide in full is a copy of its panel view cut at another shape",
                      empty_step_service.is_view_copy(panel_path, full_path, (555, 720)))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_build_start_shown_seconds_before_a_step_holding_its_words_leaves_and_the_step_starts_the_build_defect():
    # A slide's title alone, its bullets filled in 2 s later, reached phase 3 as the first frame of its build. Now it
    # leaves, covered by the step, which starts the build; a title alone for 20 s, or with a word the step lacks, stays
    from services import empty_step_service
    from services import knowledge_filter_service
    from services import text_detection_service
    from support import fixtures as fx

    words = {"title.jpg": {"general", "hints"}, "bullets.jpg": {"general", "hints", "others", "run", "code"},
             "more.jpg": {"general", "hints", "others", "run", "code", "paths"},
             "long-title.jpg": {"release", "basics"}, "long-bullets.jpg": {"release", "basics", "tag"},
             "lost.jpg": {"gateway", "limits"}, "lost-step.jpg": {"gateway", "retries"},
             "scrolled.jpg": {"queue"}, "scrolled-step.jpg": {"queue", "owner"}}
    folder = fx.make_paths().output_root
    items = [{"slug": "title", "path": "frames/title.jpg", "timestamp": 578.0},
             {"slug": "bullets", "path": "frames/bullets.jpg", "timestamp": 580.0, "stepOf": "title"},
             {"slug": "more", "path": "frames/more.jpg", "timestamp": 641.0, "stepOf": "title"},
             {"slug": "long-title", "path": "frames/long-title.jpg", "timestamp": 700.0},
             {"slug": "long-bullets", "path": "frames/long-bullets.jpg", "timestamp": 720.0, "stepOf": "long-title"},
             {"slug": "lost", "path": "frames/lost.jpg", "timestamp": 800.0},
             {"slug": "lost-step", "path": "frames/lost-step.jpg", "timestamp": 802.0, "stepOf": "lost"},
             # an editor scrolled 3 s later: its words read on the step, but the step changed what it showed
             {"slug": "scrolled", "path": "frames/scrolled.jpg", "timestamp": 900.0},
             {"slug": "scrolled-step", "path": "frames/scrolled-step.jpg", "timestamp": 903.0, "stepOf": "scrolled"}]
    saved = (text_detection_service.is_available, empty_step_service._read_words, empty_step_service._is_only_added)
    text_detection_service.is_available = lambda: True
    empty_step_service._read_words = lambda path: words[path.name]
    empty_step_service._is_only_added = lambda start, step: start.name != "scrolled.jpg"
    try:
        passed = empty_step_service.list_passed_build_starts(items, folder, 5.0)
    finally:
        text_detection_service.is_available, empty_step_service._read_words, empty_step_service._is_only_added = saved
    harness.check("only the title shown 2 s before a step holding all its words leaves, covered by that step",
                  [(item["slug"], item["coveredBy"]) for item in passed] == [("title", ["bullets"])], str(passed))
    staying = knowledge_filter_service.build_relinked_frames([item for item in items if item["slug"] != "title"],
                                                             {"title"}, {"title": ["bullets"]})
    harness.check("the step starts the build and the next step is its step",
                  [(item["slug"], item.get("stepOf")) for item in staying[:2]] == [("bullets", None), ("more", "bullets")],
                  str(staying[:2]))


def test_a_step_switching_a_pane_to_another_item_is_told_where_it_changed_defect():
    # A step whose details pane switched to another item was outlined with its step line alone, the pane's new text
    # summed up there. Its message now names the places its pixels changed, to be outlined from the frame
    import cv2
    import numpy

    from prompts import frames_processing_messages
    from support import fixtures as fx

    def draw(pane: list[str]) -> numpy.ndarray:
        screen = numpy.full((396, 545, 3), 20, numpy.uint8)
        for row, name in enumerate(("gateway", "acmesync", "queue", "ledger")):
            cv2.putText(screen, name, (20, 60 + 24 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
        for row, line in enumerate(pane):
            cv2.putText(screen, line, (330, 60 + 30 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 2)
        return screen

    paths = fx.make_paths()
    (paths.output_root / "frames").mkdir(exist_ok=True)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-001.png"), draw(["name: gateway", "owner: Jonas", "size: 16 kB"]))
    cv2.imwrite(str(paths.output_root / "frames" / "frame-002.png"), draw(["name: ledger", "owner: Mira", "size: 94 kB"]))
    items = [{"slug": "frame-001", "index": 1, "path": "frames/frame-001.png"},
             {"slug": "frame-002", "index": 2, "path": "frames/frame-002.png", "stepOf": "frame-001"}]
    phase_file_service.write_phase_file(paths, 2, {"items": items})
    request = argparse.Namespace(paths=paths)
    note = frames_processing_messages.render_changed_places_note(request, items[1])
    harness.check("the step's message names frame 1 and a changed place in the pane's columns",
                  "from frame 1" in note and any(int(left) >= 60 for left in re.findall(r"(\d+)% to \d+% across", note)),
                  note)
    harness.check("the first frame gets no such note",
                  frames_processing_messages.render_changed_places_note(request, items[0]) == "")


def test_a_terminal_of_short_lines_scrolled_one_line_with_nothing_new_is_a_copy_defect():
    # A terminal scrolled by one line, nothing new, was kept: its short lines changed under 150 blocks, and the copy rule
    # looked for a line-up only from 150. A scroll is looked for from 30 now; a typed word in the new band keeps it
    import cv2

    from services import empty_step_service
    from utils import change_capture_utils

    words = ("set", "get", "read", "module", "channel", "value", "frequency", "address", "memory", "sensor", "output",
             "reset", "status", "scan", "noise", "power", "baud")
    lines = [f"{words[index % 17]}{index}: {words[(index * 5) % 17]}" for index in range(25)]
    with tempfile.TemporaryDirectory() as folder:
        paths = {name: Path(folder) / f"{name}.png" for name in ("before", "copy", "typed")}
        cv2.imwrite(str(paths["before"]), _draw_terminal(lines))
        cv2.imwrite(str(paths["copy"]), _draw_terminal(lines[1:] + [""]))
        cv2.imwrite(str(paths["typed"]), _draw_terminal(lines[1:] + ["bootloader"]))
        changed = int(change_capture_utils.map_changed_blocks(empty_step_service._read_sample(paths["before"]),
                                                              empty_step_service._read_sample(paths["copy"])).sum())
        harness.check("the scroll changes fewer than 150 blocks", changed < 150, str(changed))
        harness.check("scrolled one line with nothing new is a copy",
                      empty_step_service.is_moved_copy(paths["before"], paths["copy"]))
        harness.check("scrolled one line with a typed word is not",
                      not empty_step_service.is_moved_copy(paths["before"], paths["typed"]))


def test_a_terminal_shown_again_after_five_other_screens_is_a_copy_defect():
    # A terminal came back scrolled one line after five slides, nothing new on it, and was kept: the copy rule looked
    # back three kept frames. A scroll alone, which reads no words, is looked for up to eight kept frames back now
    import cv2

    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    words = ("set", "get", "read", "module", "channel", "value", "frequency", "address", "memory", "sensor", "output",
             "reset", "status", "scan", "noise", "power", "baud")
    lines = [f"{words[index % 17]}{index}: " + " ".join(words[(index * 5 + offset * 3) % 17]
                                                     for offset in range(3 + index % 6)) for index in range(80)]
    with tempfile.TemporaryDirectory() as folder:
        screens = {"terminal": lines[:25], **{f"slide{n}": lines[30 + n * 8:55 + n * 8] for n in range(5)},
                   "again": lines[1:25] + [""]}
        kept = []
        for name, shown in screens.items():
            cv2.imwrite(str(Path(folder) / f"{name}.png"), _draw_terminal(shown))
            kept.append({"slug": name, "path": f"frames/{name}.png"})
        copies = [item["slug"] for item in empty_step_service.list_moved_copies(kept, Path(folder))]
        harness.check("the terminal scrolled a line, after five other screens, is a copy", copies == ["again"], str(copies))


def _draw_bullet_slide(lines: list[str]) -> "numpy.ndarray":
    import cv2
    import numpy

    image = numpy.full((576, 720, 3), 248, numpy.uint8)
    cv2.putText(image, "Applications", (250, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 150, 120), 2)
    for row, line in enumerate(lines):
        cv2.putText(image, line, (40, 130 + row * 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 2)

    return image


def test_a_slide_zoomed_in_with_nothing_added_is_a_copy_defect():
    # A slide shown again zoomed in was kept as a build step: ECC from no shift found no line-up for the zoom.
    # Fix: the line-up also starts from matched keypoints' shift and scale; a zoomed slide with a line added stays
    import cv2

    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    lines = ["Recommendation systems for every store", "Gateway answers what a buyer asks",
             "AcmeSync finds the closest documents", "Augment the prompt with their content",
             "Jonas stores each vector beside its row", "Search the vectors by their distance"]

    def zoom(image):
        height, width = image.shape[:2]
        big = cv2.resize(image, (int(width * 1.15), int(height * 1.15)))
        return big[30:30 + height, 40:40 + width]

    with tempfile.TemporaryDirectory() as folder:
        paths = {name: Path(folder) / f"{name}.png" for name in ("before", "copy", "added")}
        cv2.imwrite(str(paths["before"]), _draw_bullet_slide(lines))
        cv2.imwrite(str(paths["copy"]), zoom(_draw_bullet_slide(lines)))
        cv2.imwrite(str(paths["added"]), zoom(_draw_bullet_slide(lines[:5] + ["Rebuild the index after every delete"])))
        harness.check("the slide zoomed in with nothing new is a copy",
                      empty_step_service.is_moved_copy(paths["before"], paths["copy"]))
        harness.check("zoomed in with a new line it is not",
                      not empty_step_service.is_moved_copy(paths["before"], paths["added"]))


def test_a_frame_showing_past_the_edges_of_the_frame_before_is_no_copy_defect():
    # A blackboard's camera tilted up showed the same table and new chalk rows under it, which the line-up never compared
    # and the text reader cannot read. Fix: a frame is a copy only when what the frame before does not show holds no edges
    import cv2
    import numpy

    from services import empty_step_service

    board = numpy.full((450, 800), 40, numpy.uint8)
    for row in range(4):
        cv2.rectangle(board, (80, 20 + row * 100), (600, 70 + row * 100), 230, 3)
    blank = numpy.full((450, 800), 40, numpy.uint8)
    tilted = numpy.float32([[1, 0, 0], [0, 1, -200]])
    zoomed = numpy.float32([[1.1, 0, -40], [0, 1.1, -22]])
    with tempfile.TemporaryDirectory() as folder:
        marked, empty = Path(folder) / "marked.png", Path(folder) / "empty.png"
        cv2.imwrite(str(marked), board)
        cv2.imwrite(str(empty), blank)
        harness.check("new marks past the frame before's edges make no copy",
                      not empty_step_service._is_uncovered_blank(marked, tilted, (450, 800)))
        harness.check("a blank band there does", empty_step_service._is_uncovered_blank(empty, tilted, (450, 800)))
        harness.check("a zoom that leaves nothing outside does",
                      empty_step_service._is_uncovered_blank(marked, zoomed, (450, 800)))


def _draw_pointer_slide(path, pointer_at: tuple[int, int], scribble: bool = False) -> None:
    import cv2
    import numpy

    image = numpy.full((540, 960, 3), 255, numpy.uint8)
    for row, line in enumerate(("Gateway rollout plan", "Refunds settle within three days", "Invoices close monthly")):
        cv2.putText(image, line, (60, 120 + row * 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    cv2.circle(image, pointer_at, 9, (0, 0, 200), 2)
    if scribble:
        for step in range(6):
            cv2.line(image, (600 + step * 22, 420), (612 + step * 22, 470), (0, 0, 0), 4)
    cv2.imwrite(str(path), image)


def test_a_frame_with_only_a_pointer_moved_is_a_copy_and_a_scribble_is_not_defect():
    # A frame that was the frame before with a magnifier moved reached phase 3: no build step, so the empty-step rule
    # never asked, and too few blocks for a moved copy. Now a few changed blocks that gain no ink make it a copy
    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("no text reader: skipped")
        return
    folder = Path(tempfile.mkdtemp())
    _draw_pointer_slide(folder / "before.jpg", (200, 112))
    _draw_pointer_slide(folder / "pointer.jpg", (400, 192))
    _draw_pointer_slide(folder / "scribble.jpg", (200, 112), scribble=True)
    harness.check("the pointer moved is a copy", empty_step_service.is_pointer_copy(folder / "before.jpg", folder / "pointer.jpg"))
    harness.check("marks written are not", not empty_step_service.is_pointer_copy(folder / "before.jpg", folder / "scribble.jpg"))


def _draw_card(path, scale: float) -> None:
    import cv2
    import numpy

    card = numpy.full((540, 960, 3), 255, numpy.uint8)
    cv2.putText(card, "Questions and answers", (110, 250), cv2.FONT_HERSHEY_DUPLEX, 2.0, (0, 0, 0), 3)
    cv2.putText(card, "Thanks for listening", (150, 350), cv2.FONT_HERSHEY_DUPLEX, 1.8, (0, 0, 0), 3)
    height, width = int(540 * scale), int(960 * scale)
    grown = cv2.resize(card, (width, height))
    top, left = (height - 540) // 2, (width - 960) // 2
    cv2.imwrite(str(path), grown[top:top + 540, left:left + 960] if scale >= 1 else card)


def test_a_card_drawn_larger_or_taken_again_unchanged_is_a_copy_defect():
    # A closing card came back drawn larger (too few blocks changed for a moved copy) and then unchanged (no rule took
    # it), and both reached phase 3. Now a rescaled line-up needs fewer changed blocks, and an unchanged frame is a copy
    from services import empty_step_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("no text reader: skipped")
        return
    folder = Path(tempfile.mkdtemp())
    _draw_card(folder / "card.jpg", 1.0)
    _draw_card(folder / "larger.jpg", 1.08)
    _draw_card(folder / "again.jpg", 1.0)
    harness.check("the card drawn larger is a copy", empty_step_service.is_moved_copy(folder / "card.jpg", folder / "larger.jpg"))
    harness.check("the card taken again unchanged is a copy",
                  empty_step_service.is_unchanged_copy(folder / "card.jpg", folder / "again.jpg"))


def _draw_text_slide(path, lines: tuple[str, ...]) -> None:
    import cv2
    import numpy

    image = numpy.full((540, 960, 3), 255, numpy.uint8)
    for row, line in enumerate(lines):
        cv2.putText(image, line, (60, 120 + row * 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    cv2.imwrite(str(path), image)


def test_a_step_replacing_the_text_of_the_frame_before_is_no_build_step_defect():
    # a quote shown in place of a slide's question was marked its build step, and its notes said the quote was added
    # below the question; a step whose frame before's words changed in place and are gone is now a screen of its own
    from services import empty_step_service
    from services import frame_preparation_service
    from services import text_detection_service

    if not text_detection_service.is_available():
        print("text reader not installed: skipped")
        return
    folder = Path(tempfile.mkdtemp())
    _draw_text_slide(folder / "frame-001.jpg", ("Gateway rollout plan",))
    _draw_text_slide(folder / "frame-002.jpg", ("Refunds settle within days",))
    _draw_text_slide(folder / "frame-003.jpg", ("Refunds settle within days", "Invoices close monthly"))
    _draw_text_slide(folder / "frame-004.jpg", ("Gateway rollout plan", "Invoices close monthly"))
    harness.check("text replaced in place is a replacing step",
                  empty_step_service.is_replacing_step(folder / "frame-001.jpg", folder / "frame-002.jpg"))
    harness.check("text added under it is not",
                  not empty_step_service.is_replacing_step(folder / "frame-001.jpg", folder / "frame-004.jpg"))
    kept = [{"slug": "frame-001", "path": "frames/frame-001.jpg"},
            {"slug": "frame-002", "path": "frames/frame-002.jpg", "stepOf": "frame-001"},
            {"slug": "frame-003", "path": "frames/frame-003.jpg", "stepOf": "frame-001"}]
    staying, replacing = frame_preparation_service._drop_replacing_step_marks(kept, folder)
    harness.check("the replacing step is a screen of its own, the next step a step of it",
                  replacing == ["frame-002"] and "stepOf" not in staying[1] and staying[2]["stepOf"] == "frame-002",
                  str(staying))


def test_a_slide_whose_only_text_is_one_address_is_judged_by_its_pieces_defect():
    # A new slide after a slide holding only an address stayed a build step: the address, read as one word, was under
    # the rule's two words (DEFECTS 341). Its pieces count now
    import cv2
    import numpy

    from services import empty_step_service
    from services import text_detection_service
    from support import fixtures as fx

    if not text_detection_service.is_available():
        print("the text reader is not installed: skipped")
        return
    folder = fx.make_paths().output_root
    before, step = folder / "address.png", folder / "question.png"
    slide = numpy.full((396, 545, 3), 25, numpy.uint8)
    first = slide.copy()
    cv2.putText(first, "https://acmesync.example/ledger-gateway", (40, 330), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 2)
    second = slide.copy()
    cv2.putText(second, "Who owns the Gateway?", (120, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
    cv2.imwrite(str(before), first)
    cv2.imwrite(str(step), second)
    harness.check("the address's slide replaced by a question is a screen of its own",
                  empty_step_service.is_replacing_step(before, step))

