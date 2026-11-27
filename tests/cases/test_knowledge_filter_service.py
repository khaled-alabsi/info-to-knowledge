"""Phase 2's second filter: the frames the frame classifier tags people leave the frame list, their images move into
`frames/dropped/`, their speech goes to the frame before, and no kept frame points at a dropped one.

The classifier is a stand-in command with the real one's interface (`classifier filter DIR --out FILE --task tags`), so
the program's own subprocess path runs; it tags people the frames named in `drops.txt` beside it, and refuses any task
but `tags`.
"""

from __future__ import annotations

import json
import logging
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import harness
from support import media_fixtures

import config
from models.run_paths import RunPaths
from services import frame_preparation_service
from services import knowledge_filter_service
from services import phase_file_service
from services import run_paths_service

FAKE_CLASSIFIER = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
folder, out = Path(sys.argv[2]), Path(sys.argv[4])
if (Path(__file__).parent / "fail.txt").exists():
    sys.exit("the image model could not be loaded")
if sys.argv[sys.argv.index("--task") + 1] != "tags":
    sys.exit("only the tags task is asked")
drops = (Path(__file__).parent / "drops.txt").read_text().split()
frames = [{"frame": path.name, "peopleScore": 0.95 if path.name in drops else 0.10,
           "tag": "people" if path.name in drops else "other", "isPlainText": False, "milliseconds": 1.0}
          for path in sorted(folder.glob("*.jpg"))]
out.write_text(json.dumps({"classifier": "tags", "task": "tags", "peopleThreshold": 0.7, "folder": str(folder),
                           "frames": frames}))
"""


def _build_classifier(folder: Path,
                      drops: list[str]) -> Path:
    command = folder / "classifier"
    command.write_text(FAKE_CLASSIFIER, encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    (folder / "drops.txt").write_text("\n".join(drops), encoding="utf-8")

    return command


def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    segments = [{"text": " eins drei zwei", "start": 0.0, "end": 6.0,
                 "words": [{"word": " eins", "start": 0.2, "end": 0.6}, {"word": " drei", "start": 2.2, "end": 2.5},
                           {"word": " zwei", "start": 5.0, "end": 5.5}]}]
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                   "text": "eins drei zwei", "segments": segments})
    run_dir = paths.output_root / "logs" / "2026-10-01_09-00"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"), control_check=None)


def _prepare_with(ctx: SimpleNamespace,
                  is_on: bool,
                  command: Path) -> dict:
    frames = config.settings.frames
    previous = (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on,
                frames.knowledge_classifier_command, frames.is_text_only_mark_on, frames.is_board_stage_on)
    frames.selection_mode, frames.min_scene_seconds = "scene", 0.0
    frames.is_knowledge_filter_on, frames.knowledge_classifier_command = is_on, str(command)
    frames.is_text_only_mark_on, frames.is_board_stage_on = False, False
    try:
        return frame_preparation_service.prepare_frames(ctx)
    finally:
        (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on,
         frames.knowledge_classifier_command, frames.is_text_only_mark_on, frames.is_board_stage_on) = previous


def test_a_dropped_frame_leaves_the_list_its_image_moves_and_its_speech_goes_to_the_frame_before():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    prepared = _prepare_with(ctx, True, _build_classifier(folder, ["frame-001.jpg"]))
    frames_dir = ctx.paths.output_root / "frames"

    harness.check("the kept frames are listed with their drop score, the dropped one is not",
                  [(item["slug"], item["dropScore"]) for item in prepared["items"]]
                  == [("frame-000", 0.10), ("frame-002", 0.10)], json.dumps(prepared["items"])[:400])
    harness.check("the dropped frame is listed apart with its time, path and drop score",
                  [(entry["slug"], entry["path"], entry["dropScore"]) for entry in prepared["droppedFrames"]]
                  == [("frame-001", "frames/dropped/frame-001.jpg", 0.95)]
                  and isinstance(prepared["droppedFrames"][0]["timestamp"], float), str(prepared["droppedFrames"]))
    harness.check("every frame keeps the classifier's tag, kept and dropped",
                  [item["tag"] for item in prepared["items"]] == ["other", "other"]
                  and prepared["droppedFrames"][0]["tag"] == "people", str(prepared))
    harness.check("its image moved into frames/dropped/ and left frames/",
                  (frames_dir / "dropped" / "frame-001.jpg").is_file() and not (frames_dir / "frame-001.jpg").exists()
                  and (frames_dir / "frame-000.jpg").is_file(), str(sorted(frames_dir.rglob("*.jpg"))))
    harness.check("what was said while the dropped frame was on screen goes to the frame before it",
                  prepared["items"][0]["transcriptCurrent"] == "eins drei", str(prepared["items"][0]))
    harness.check("the next kept frame keeps its own speech", prepared["items"][1]["transcriptCurrent"] == "zwei",
                  str(prepared["items"][1]))


def test_a_rerun_keeping_every_frame_leaves_nothing_in_dropped():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    _prepare_with(ctx, True, _build_classifier(folder, ["frame-001.jpg"]))
    prepared = _prepare_with(ctx, True, _build_classifier(folder, []))
    dropped_dir = ctx.paths.output_root / "frames" / "dropped"

    harness.check("a second run that drops nothing lists no dropped frame and leaves no stale image behind",
                  prepared["droppedFrames"] == [] and len(prepared["items"]) == 3 and not dropped_dir.exists(),
                  str(sorted(dropped_dir.glob("*"))) if dropped_dir.exists() else "")


def test_a_resume_of_the_same_run_reads_the_frames_its_rules_took_again_defect():
    # A run stopped by a reboot after phase 2's rules had taken its frames took them all again on its resume, 20
    # minutes. Now the same run reads them back, the image a filter had moved into frames/dropped/ put back first
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    command = _build_classifier(folder, ["frame-001.jpg"])
    first = _prepare_with(ctx, True, command)
    extract = frame_preparation_service.frame_extraction_service.extract_frames
    taken = []
    frame_preparation_service.frame_extraction_service.extract_frames = lambda *args: taken.append(1) or extract(*args)
    try:
        again = _prepare_with(ctx, True, command)
        ctx.run_dir = ctx.run_dir.parent / "2026-10-01_10-00"
        ctx.run_dir.mkdir()
        _prepare_with(ctx, True, command)
    finally:
        frame_preparation_service.frame_extraction_service.extract_frames = extract
    harness.check("the resume keeps and drops the same frames", [item["slug"] for item in again["items"]]
                  == [item["slug"] for item in first["items"]] and again["droppedFrames"] == first["droppedFrames"],
                  json.dumps(again)[:600])
    harness.check("only another run takes the frames again", taken == [1], str(taken))


def test_the_filter_off_keeps_every_frame_and_never_runs_the_classifier():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    prepared = _prepare_with(ctx, False, folder / "no-such-classifier")

    harness.check("with frames.isKnowledgeFilterOn false every frame is kept, with no drop score",
                  len(prepared["items"]) == 3 and prepared["droppedFrames"] == []
                  and not any("dropScore" in item for item in prepared["items"]), json.dumps(prepared["items"])[:300])
    harness.check("the switch is on by default", config.FrameSettings().is_knowledge_filter_on is True)


def test_a_missing_or_failing_classifier_stops_phase_2_by_name():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    try:
        _prepare_with(ctx, True, folder / "no-such-classifier")
        message = "no refusal"
    except FileNotFoundError as exc:
        message = str(exc)
    harness.check("a missing classifier is refused naming the setting and the path",
                  "frames.knowledgeClassifierCommand" in message and "no-such-classifier" in message, message)

    command = _build_classifier(folder, [])
    (folder / "fail.txt").write_text("", encoding="utf-8")
    try:
        _prepare_with(ctx, True, command)
        message = "no refusal"
    except RuntimeError as exc:
        message = str(exc)
    harness.check("a classifier that exits with an error stops the phase with its own message",
                  "exit code 1" in message and "the image model could not be loaded" in message, message)


def test_no_kept_frame_points_at_a_dropped_one():
    kept = [{"slug": "frame-002", "repeatOf": "frame-000", "stepOf": None},
            {"slug": "frame-004", "repeatOf": None, "stepOf": "frame-003"},
            {"slug": "frame-005", "repeatOf": None, "stepOf": "frame-003"},
            {"slug": "frame-006", "repeatOf": "frame-002", "stepOf": None},
            {"slug": "frame-007", "repeatOf": None, "stepOf": "frame-006"}]
    relinked = knowledge_filter_service.build_relinked_frames(kept, {"frame-000", "frame-003"})

    harness.check("a repeat of a dropped frame becomes an original; a repeat of a kept frame stays one",
                  [item["repeatOf"] for item in relinked] == [None, None, None, "frame-002", None], str(relinked))
    harness.check("the first kept step of a build whose first frame was dropped starts it, the next step points there, "
                  "and a build of kept frames is left alone",
                  [item["stepOf"] for item in relinked] == [None, None, "frame-004", None, "frame-006"], str(relinked))


def test_a_repeat_of_a_dropped_copy_repeats_the_frame_that_holds_it_defect():
    # Defect: a slide shown again repeated a moved copy of its first showing; the copy was dropped and the mark cleared.
    # Fix: a repeat of a dropped frame repeats the first kept frame that holds it, an original only when none does.
    kept = [{"slug": "frame-036", "repeatOf": None, "stepOf": None},
            {"slug": "frame-054", "repeatOf": "frame-037", "stepOf": None},
            {"slug": "frame-058", "repeatOf": "frame-019", "stepOf": None}]
    relinked = knowledge_filter_service.build_relinked_frames(kept, {"frame-037", "frame-019"},
                                                              {"frame-037": ["frame-036"], "frame-019": ["frame-018"]})
    harness.check("the repeat of the copy repeats its holder; one whose holder is gone becomes an original",
                  [item["repeatOf"] for item in relinked] == [None, "frame-036", None], str(relinked))


def test_a_repeat_whose_first_showing_is_held_by_itself_becomes_an_original_defect():
    # Defect: a closing card's first showing was dropped as held by its later showing, and the later showing was then
    # marked a repeat of itself. Fix: only a kept frame shown before a repeat can be its original
    kept = [{"slug": "frame-054", "repeatOf": None, "stepOf": None},
            {"slug": "frame-057", "repeatOf": "frame-055", "stepOf": None}]
    relinked = knowledge_filter_service.build_relinked_frames(kept, {"frame-055"}, {"frame-055": ["frame-057"]})
    harness.check("the later showing is now the original", relinked[1]["repeatOf"] is None, str(relinked))


def test_a_repeat_of_a_dropped_step_held_by_a_dropped_step_repeats_the_kept_first_defect():
    # Defect: empty steps 6 and 7 were each held by the step before, itself dropped, so a repeat of 7 became an original.
    # Fix: the holders are followed until a kept frame.
    kept = [{"slug": "frame-005", "repeatOf": None, "stepOf": None},
            {"slug": "frame-050", "repeatOf": "frame-007", "stepOf": None}]
    relinked = knowledge_filter_service.build_relinked_frames(kept, {"frame-006", "frame-007"},
                                                              {"frame-006": ["frame-005"], "frame-007": ["frame-006"]})
    harness.check("the repeat follows 7 to 6 to 5", relinked[1]["repeatOf"] == "frame-005", str(relinked))


def test_a_speaker_between_a_slide_and_its_repeat_is_dropped_as_a_cut_away_defect():
    # Defect: a broadcast cut to the speaker, the slide blurred behind them, between a slide and its repeat; its people
    # score 0.63 was under the classifier's bar, and phase 3 read it. Fix: such a cut-away from 0.6 is listed for drop.
    kept = [{"slug": "frame-054", "repeatOf": "frame-036", "stepOf": None, "dropScore": 0.005},
            {"slug": "frame-055", "repeatOf": None, "stepOf": None, "dropScore": 0.628},
            {"slug": "frame-056", "repeatOf": "frame-054", "stepOf": None, "dropScore": 0.005},
            {"slug": "frame-247", "repeatOf": None, "stepOf": None, "dropScore": 0.01},
            {"slug": "frame-248", "repeatOf": None, "stepOf": None, "dropScore": 0.577},
            {"slug": "frame-249", "repeatOf": "frame-247", "stepOf": None, "dropScore": 0.01}]
    cut_aways = knowledge_filter_service.list_cut_aways(kept)
    harness.check("the speaker between a slide and its repeat goes, held by the slide; a chalk board under 0.6 stays",
                  [(item["slug"], item["coveredBy"]) for item in cut_aways] == [("frame-055", ["frame-054"])],
                  str(cut_aways))
