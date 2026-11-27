"""A run that ends 100% done keeps its recording in its own folder, `recording/`; phases 1 and 2 wait until it is moved
back to the inbox."""

from __future__ import annotations

import argparse
import io
import json
import logging
import tempfile
from pathlib import Path

from support import fixtures as fx
from support import harness

import config

from cli import recording_command
from cli import reset_command
from cli import run_command
from models.pipeline_errors import EngineUnavailable
from models.ui_errors import UploadRefusedError
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import run_recording_service
from services import run_store_service
from services import transcription_service



def _build_place() -> tuple[Path, Path, Path]:
    """An inbox with one video directly in it, and an empty run folder."""

    root = Path(tempfile.mkdtemp())
    inbox, run_folder = root / "inbox", root / "output" / "AcmeSync-review"
    inbox.mkdir()
    run_folder.mkdir(parents=True)
    video = inbox / "AcmeSync-review.mp4"
    video.write_bytes(b"<video bytes>")

    return inbox, run_folder, video


class LogLinesHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _build_logger() -> tuple[logging.Logger, LogLinesHandler]:
    logger = logging.getLogger(f"test-recording-{id(object())}")
    handler = LogLinesHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    return logger, handler


def test_a_run_is_done_only_when_every_phase_is_complete():
    harness.check("all five complete", run_recording_service.is_run_done({"status": "complete", "phases": fx.DONE_PHASES}),
                  "")
    harness.check("a held review is not done",
                  not run_recording_service.is_run_done({"status": "complete",
                                                         "phases": {**fx.DONE_PHASES, "review": "held"}}), "")
    harness.check("a review switched off is done at phase 4's document",
                  run_recording_service.is_run_done({"status": "complete",
                                                     "phases": {**fx.DONE_PHASES, "review": "off"}}), "")
    harness.check("a phase before it recorded off is not done",
                  not run_recording_service.is_run_done({"status": "complete",
                                                         "phases": {**fx.DONE_PHASES, "finalize": "off"}}), "")
    harness.check("a run that is not complete is not done",
                  not run_recording_service.is_run_done({"status": "incomplete", "phases": fx.DONE_PHASES}), "")
    old_names = {"transcribe": "complete", "enumerate": "complete", "work": "complete", "finalize": "complete",
                 "review": "complete"}
    harness.check("a record with the phases' earlier names is done",
                  run_recording_service.is_run_done({"status": "complete", "phases": old_names}), "")


def test_a_done_run_keeps_its_inbox_recording_in_its_folder_and_finds_it_there():
    inbox, run_folder, video = _build_place()
    kept = run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES},
                                                        inbox, run_folder, video)
    harness.check("moved into recording/", kept == run_folder / "recording" / video.name and kept.is_file(), str(kept))
    harness.check("gone from the inbox", not video.exists(), "")
    harness.check("found again by the run folder", run_recording_service.find_stored_recording(run_folder) == kept, "")
    harness.check("found again by the path the run record names",
                  run_recording_service.find_recording_by_origin(str(video), [run_folder.parent]) == kept, "")


def test_a_kept_recording_names_the_inbox_path_it_came_from_for_the_lists_defect():
    # The defect: a done run of a list, run again from its kept recording, named that copy as its video and left its
    # list for Running. The fix: the run names the inbox path the copy came from, by which a list holds it.
    inbox, run_folder, video = _build_place()
    harness.check("none while the run keeps no recording", run_recording_service.read_origin_video(run_folder) == "")
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    harness.check("the inbox path it came from", run_recording_service.read_origin_video(run_folder)
                  == str(video.resolve()), run_recording_service.read_origin_video(run_folder))


def test_a_run_not_done_or_a_video_outside_the_inbox_moves_nothing():
    inbox, run_folder, video = _build_place()
    kept = run_recording_service.keep_recording_if_done({"status": "complete",
                                                         "phases": {**fx.DONE_PHASES, "review": "held"}},
                                                        inbox, run_folder, video)
    harness.check("a held review keeps it in the inbox", kept is None and video.exists(), str(kept))
    elsewhere = inbox.parent / "Downloads" / "AcmeSync-demo.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"<video bytes>")
    kept = run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES},
                                                        inbox, run_folder, elsewhere)
    harness.check("a video outside the inbox stays where it is", kept is None and elsewhere.exists(), str(kept))


def test_a_meeting_folder_moves_whole_and_comes_back_whole():
    inbox, run_folder, _ = _build_place()
    meeting = inbox / "Jonas-weekly"
    meeting.mkdir()
    video = meeting / "Jonas-weekly.mp4"
    video.write_bytes(b"<video bytes>")
    (meeting / "Jonas-weekly.vtt").write_text("<transcript>", encoding="utf-8")
    kept = run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES},
                                                        inbox, run_folder, video)
    harness.check("the folder moved with its transcript",
                  kept == run_folder / "recording" / "Jonas-weekly" / "Jonas-weekly.mp4"
                  and (run_folder / "recording" / "Jonas-weekly" / "Jonas-weekly.vtt").is_file(), str(kept))
    back = run_recording_service.move_recording_to_inbox(inbox, run_folder)
    harness.check("back in the inbox as the same folder", back == video and video.is_file()
                  and (meeting / "Jonas-weekly.vtt").is_file(), str(back))
    harness.check("the run folder keeps no recording/", not (run_folder / "recording").exists(), "")


def test_moving_back_never_replaces_a_video_now_in_the_inbox_under_that_name():
    inbox, run_folder, video = _build_place()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    video.write_bytes(b"<another upload>")
    back = run_recording_service.move_recording_to_inbox(inbox, run_folder)
    harness.check("numbered beside it", back == inbox / "AcmeSync-review-2.mp4" and video.read_bytes() == b"<another upload>",
                  str(back))


def test_phases_1_and_2_run_only_when_asked_for_and_not_already_complete():
    complete = {"transcribe": "complete", "frames_preparing": "complete"}
    harness.check("a resume with both complete runs neither",
                  not run_recording_service.would_run_recording_phases(1, complete, is_resuming=True), "")
    harness.check("a resume with phase 2 not complete runs it",
                  run_recording_service.would_run_recording_phases(1, {"transcribe": "complete"}, is_resuming=True), "")
    harness.check("a new run from phase 1 runs them",
                  run_recording_service.would_run_recording_phases(1, complete, is_resuming=False), "")
    harness.check("a new run from phase 3 runs neither",
                  not run_recording_service.would_run_recording_phases(3, {}, is_resuming=False), "")


def test_reset_to_0_or_1_is_refused_while_the_recording_is_kept_in_the_run_folder():
    inbox, run_folder, video = _build_place()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    logger, handler = _build_logger()
    code = reset_command.cmd_reset_to(argparse.Namespace(phase=1, output_root=run_folder, is_confirmed=True,
                                                         logger=logger))
    harness.check("refused with exit 2", code == 2, str(code))
    harness.check("says to move it back first", any("recording-to-inbox" in line for line in handler.lines),
                  "\n".join(handler.lines))


def test_the_command_moves_the_recording_back_and_says_where():
    inbox, run_folder, video = _build_place()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    logger, handler = _build_logger()
    original = config.INBOX_DIR
    config.INBOX_DIR = inbox
    try:
        code = recording_command.cmd_recording_to_inbox(argparse.Namespace(output_root=run_folder, logger=logger))
        again = recording_command.cmd_recording_to_inbox(argparse.Namespace(output_root=run_folder, logger=logger))
    finally:
        config.INBOX_DIR = original
    harness.check("moved back, exit 0", code == 0 and video.is_file(), "\n".join(handler.lines))
    harness.check("a second time there is nothing to move: exit 2", again == 2, "\n".join(handler.lines))


def test_the_command_keeps_a_done_run_s_inbox_recording_in_its_folder():
    inbox, run_folder, video = _build_place()
    paths = RunPaths(repository=inbox, module=video.name, output_root_override=run_folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    store.data.update({"status": "complete", "phases": fx.DONE_PHASES})
    store.save()
    logger, handler = _build_logger()
    original = config.INBOX_DIR
    config.INBOX_DIR = inbox
    try:
        is_offered = run_recording_service.can_keep_recording(store.data, inbox, video)
        code = recording_command.cmd_recording_to_run(argparse.Namespace(output_root=run_folder, logger=logger))
        again = recording_command.cmd_recording_to_run(argparse.Namespace(output_root=run_folder, logger=logger))
    finally:
        config.INBOX_DIR = original
    harness.check("a done run with its recording in the inbox can keep it", is_offered, "")
    harness.check("kept in recording/, exit 0", code == 0 and (run_folder / "recording" / video.name).is_file()
                  and not video.exists(), "\n".join(handler.lines))
    harness.check("a second time it is no inbox video any more: exit 2", again == 2, "\n".join(handler.lines))


def test_deleting_a_kept_recording_moves_it_to_the_inbox_trash_where_restore_finds_it():
    inbox, run_folder, video = _build_place()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    trashed = run_recording_service.move_recording_to_trash(inbox, run_folder, video)
    origins = json.loads((inbox / ".trash" / ".origins.json").read_text(encoding="utf-8"))
    harness.check("in the trash, gone from the run folder and the inbox",
                  trashed == inbox / ".trash" / video.name and trashed.is_file() and not (run_folder / "recording").exists()
                  and not video.exists(), str(trashed))
    harness.check("the trash remembers the inbox path the run names", origins[video.name]["video"] == str(video.resolve()),
                  str(origins))


def test_deleting_an_inbox_recording_moves_it_to_the_trash():
    inbox, run_folder, video = _build_place()
    trashed = run_recording_service.move_recording_to_trash(inbox, run_folder, video)
    harness.check("in the trash", trashed == inbox / ".trash" / video.name and not video.exists(), str(trashed))


def test_an_uploaded_recording_lands_where_the_run_record_names_it_and_a_done_run_keeps_it():
    inbox, run_folder, _ = _build_place()
    (inbox / "AcmeSync-review.mp4").unlink()
    named = inbox / "Jonas-weekly" / "Jonas-weekly.mp4"
    record = {"status": "incomplete", "phases": {}, "repository": str(named.parent), "module": named.name}
    saved = run_recording_service.save_uploaded_recording(io.BytesIO(b"<video bytes>"), "phone-copy.mp4", inbox,
                                                          run_folder, record)
    harness.check("saved under the run record's path, not the upload's name", saved == named and named.read_bytes()
                  == b"<video bytes>", str(saved))
    named.unlink()
    kept = run_recording_service.save_uploaded_recording(io.BytesIO(b"<video bytes>"), "phone-copy.mp4", inbox,
                                                         run_folder, {**record, "status": "complete", "phases": fx.DONE_PHASES})
    harness.check("a done run keeps it in its folder at once",
                  kept == run_folder / "recording" / "Jonas-weekly" / "Jonas-weekly.mp4" and kept.is_file(), str(kept))


def test_an_upload_is_refused_when_the_recording_is_there_or_the_file_is_no_video():
    inbox, run_folder, video = _build_place()
    record = {"status": "incomplete", "phases": {}, "repository": str(inbox), "module": video.name}
    for name, label in (("again.mp4", "a recording already there"), ("notes.txt", "a file that is no video")):
        if label == "a file that is no video":
            video.unlink()
        try:
            run_recording_service.save_uploaded_recording(io.BytesIO(b"x"), name, inbox, run_folder, record)
            harness.check(f"refused: {label}", False, "saved")
        except UploadRefusedError as exc:
            harness.check(f"refused: {label}", True, str(exc))


def _build_fresh_run_args(video: Path,
                          run_folder: Path,
                          logger: logging.Logger) -> argparse.Namespace:
    return argparse.Namespace(target_file=video, output_root=run_folder, only_phase=None, from_phase=None, to_phase=None,
                              worker_indexes=[], is_resuming=False, is_dry_run=True, is_forced=True, is_queued=False,
                              profile_name="", tags=[], ingesting_profile="", is_run_as_audio=False, phase_models=[], workflow="", logger=logger)


def _list_run_records(run_folder: Path) -> list[str]:
    return sorted(str(path.relative_to(run_folder)) for path in run_folder.rglob("*") if path.suffix == ".json")


def test_a_refused_run_leaves_no_run_record_behind():
    """A run refused at its start writes nothing a later `--resume` would mistake for a run."""

    inbox, run_folder, video = _build_place()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
    video.write_bytes(b"<the same meeting, uploaded again>")
    before = _list_run_records(run_folder)
    logger, handler = _build_logger()
    code = run_command.cmd_run(_build_fresh_run_args(video, run_folder, logger))
    harness.check("a new run over a kept recording is refused", code == 2, "\n".join(handler.lines))
    harness.check("and leaves the run folder's records as they were", _list_run_records(run_folder) == before,
                  str(_list_run_records(run_folder)))

    inbox, run_folder, video = _build_place()
    original = transcription_service.describe_available_engine

    def refuse_engine(settings) -> str:
        raise EngineUnavailable("the engine is not installed")

    transcription_service.describe_available_engine = refuse_engine
    try:
        logger, handler = _build_logger()
        code = run_command.cmd_run(_build_fresh_run_args(video, run_folder, logger))
    finally:
        transcription_service.describe_available_engine = original
    harness.check("a run whose engine cannot run is refused", code == 2, "\n".join(handler.lines))
    harness.check("and writes no run record", _list_run_records(run_folder) == [], str(_list_run_records(run_folder)))


class _RaisingGraph:
    def invoke(self, state, config):
        raise RuntimeError("an error nothing caught")


def test_a_run_that_stops_on_an_error_is_recorded_failed():
    """A run record left "running" by a dead process reads as a live run on the page."""

    inbox, run_folder, video = _build_place()
    original_build, original_engine = run_command.graph_assembly.build_graph, transcription_service.describe_available_engine
    run_command.graph_assembly.build_graph = lambda ctx: _RaisingGraph()
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    try:
        logger, handler = _build_logger()
        try:
            run_command.cmd_run(_build_fresh_run_args(video, run_folder, logger))
            outcome = "returned"
        except RuntimeError:
            outcome = "raised"
    finally:
        run_command.graph_assembly.build_graph = original_build
        transcription_service.describe_available_engine = original_engine
    # A dry run's record, which `load_latest` passes over
    statuses = [json.loads(path.read_text()).get("status") for path in run_folder.rglob("run.json")]
    harness.check("the error still reaches the caller", outcome == "raised", outcome)
    harness.check("and the run record says failed", statuses == ["failed"], str(statuses))


class _InterruptedGraph:
    def invoke(self, state, config):
        raise KeyboardInterrupt


def test_a_run_stopped_with_ctrl_c_is_recorded_interrupted():
    """Ctrl-C is no `Exception`: without its own branch the record kept no end status, read as a live run."""

    inbox, run_folder, video = _build_place()
    original_build, original_engine = run_command.graph_assembly.build_graph, transcription_service.describe_available_engine
    run_command.graph_assembly.build_graph = lambda ctx: _InterruptedGraph()
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    try:
        logger, handler = _build_logger()
        code = run_command.cmd_run(_build_fresh_run_args(video, run_folder, logger))
    finally:
        run_command.graph_assembly.build_graph = original_build
        transcription_service.describe_available_engine = original_engine
    statuses = [json.loads(path.read_text()).get("status") for path in run_folder.rglob("run.json")]
    harness.check("the run ends with 130", code == 130, str(code))
    harness.check("and the record says interrupted", statuses == ["interrupted"], str(statuses))
    harness.check("and the log says why, not an empty reason", any(line.endswith("stopped: Ctrl-C") for line in handler.lines),
                  str(handler.lines))


def _interrupt_building_the_graph(ctx):
    raise KeyboardInterrupt


def test_a_run_stopped_with_ctrl_c_before_the_graph_runs_is_recorded_interrupted():
    inbox, run_folder, video = _build_place()
    original_build, original_engine = run_command.graph_assembly.build_graph, transcription_service.describe_available_engine
    run_command.graph_assembly.build_graph = _interrupt_building_the_graph
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    try:
        logger, handler = _build_logger()
        code = run_command.cmd_run(_build_fresh_run_args(video, run_folder, logger))
    finally:
        run_command.graph_assembly.build_graph = original_build
        transcription_service.describe_available_engine = original_engine
    statuses = [json.loads(path.read_text()).get("status") for path in run_folder.rglob("run.json")]
    harness.check("the run ends with 130", code == 130, str(code))
    harness.check("and the record says interrupted", statuses == ["interrupted"], str(statuses))


def test_a_recording_whose_move_fails_leaves_no_origin_behind_and_stays_in_the_inbox():
    from unittest import mock

    inbox, run_folder, video = _build_place()

    def failing_move(source, target):
        raise OSError("disk full")

    with mock.patch.object(run_recording_service.shutil, "move", failing_move):
        try:
            run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox, run_folder, video)
        except OSError:
            pass
    harness.check("the video is still in the inbox, and no origin points at a recording that is not there",
                  video.is_file() and not (run_folder / "recording" / ".origin.json").exists(), "")


def test_a_meeting_folder_with_a_dot_in_its_name_is_numbered_after_its_name():
    folder = Path(tempfile.mkdtemp())
    (folder / "Weekly v1.2").mkdir()
    harness.check("Weekly v1.2-2", run_recording_service._choose_free_target(folder, "Weekly v1.2").name == "Weekly v1.2-2",
                  run_recording_service._choose_free_target(folder, "Weekly v1.2").name)


def test_a_folder_that_keeps_a_recording_keeps_no_second_one():
    inbox, run_folder, video = _build_place()
    done = {"status": "complete", "phases": fx.DONE_PHASES}
    first = run_recording_service.keep_recording_if_done(done, inbox, run_folder, video)
    video.write_bytes(b"<video bytes>")
    second = run_recording_service.keep_recording_if_done(done, inbox, run_folder, video)
    harness.check("the copy stays in the inbox, the first is still the one found",
                  second is None and video.exists() and run_recording_service.find_stored_recording(run_folder) == first,
                  f"{second}, {run_recording_service.find_stored_recording(run_folder)}")


def test_a_stale_origin_never_hides_the_recording_another_folder_keeps():
    inbox, run_folder, video = _build_place()
    stale = run_folder.parent / "AcmeSync-earlier"
    (stale / "recording").mkdir(parents=True)
    (stale / "recording" / ".origin.json").write_text(json.dumps({"video": str(video.resolve()), "name": video.name}),
                                                      encoding="utf-8")
    kept = run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES},
                                                        inbox, run_folder, video)
    harness.check("found in the folder that keeps it",
                  run_recording_service.find_recording_by_origin(str(video), [run_folder.parent]) == kept, str(kept))


def test_an_audio_run_measures_its_windows_as_a_quality_run_even_when_the_workflow_says_fast():
    inbox, run_folder, video = _build_place()
    windows_for = []
    originals = (run_command.read_phase_windows, run_command.video_workflow_service.is_fast_processing,
                 run_command.graph_assembly.build_graph, transcription_service.describe_available_engine)
    run_command.read_phase_windows = lambda log, is_fast: windows_for.append(is_fast) or {}
    run_command.video_workflow_service.is_fast_processing = lambda folder: True
    run_command.graph_assembly.build_graph = lambda ctx: _InterruptedGraph()
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    try:
        logger, handler = _build_logger()
        args = _build_fresh_run_args(video, run_folder, logger)
        args.is_run_as_audio = True
        run_command.cmd_run(args)
    finally:
        (run_command.read_phase_windows, run_command.video_workflow_service.is_fast_processing,
         run_command.graph_assembly.build_graph, transcription_service.describe_available_engine) = originals
    harness.check("the windows are read for the audio workflow, not the fast-processing models", windows_for == [False],
                  str(windows_for))
