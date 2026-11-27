"""Phase 1's node and the validator of the file it writes. The node is called directly, over real
one-second clips, with the speech model swapped for a fake through the transcription service's own
seam. No speech model is loaded here."""

import logging
import tempfile
from pathlib import Path

from support import harness
from support import media_fixtures

from graph import graph_state
from graph.nodes import transcribe_audio_node
from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import frames_preparing_progress_service
from services import phase_file_service
from services import run_paths_service
import config as app_config
from services import faster_whisper_client
from services import transcription_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from validators import transcript_file_validator

TRANSCRIBE = output_contract.PHASE_NUMBERS["transcribe"]
LAST = max(output_contract.PHASE_FILES)
SPOKEN = {"language": "de", "languageProbability": 1.0, "text": "Guten Abend",
          "segments": [{"text": " Guten Abend", "start": 0.0, "end": 1.5, "words": []}]}


def _load_run_context(video: Path) -> RunContext:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(video.parent), module=video.name,
                                        output_root=str(paths.output_root)), budgets={}, models=[])

    return RunContext(repository=video.parent, module=video.name, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList())


def _run_node(ctx: RunContext,
              model_class,
              first_phase: int = TRANSCRIBE) -> dict:
    # THE ENGINE IS PINNED, not inherited. The node reads the live settings, and those include
    # `playbook.local.json`: on a machine whose local file selects another engine these cases would
    # drive that engine's real model instead of the fake, transcribe the fixture clip for real, and
    # pass or fail for reasons that have nothing to do with the node. Measured once, here.
    # Diarization is pinned off for the same reason: the settings files turn it on, and these cases would load pyannote
    # on the fixture clip. `test_speaker_diarization` drives it with a fake pipeline.
    previous_engine, previous_diarization = app_config.settings.audio.engine, app_config.settings.audio.diarization.is_on
    app_config.settings.audio.engine = faster_whisper_client.ENGINE_NAME
    app_config.settings.audio.diarization.is_on = False
    previous = faster_whisper_client.MODEL_FACTORY
    faster_whisper_client.set_model_factory(model_class)
    try:
        return transcribe_audio_node.produce_transcript_node(graph_state.new_run_state(first_phase, LAST),
                                                     {"configurable": {"ctx": ctx}})
    finally:
        faster_whisper_client.set_model_factory(previous)
        app_config.settings.audio.engine = previous_engine
        app_config.settings.audio.diarization.is_on = previous_diarization


def test_a_video_with_speech_writes_the_transcript_file():
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    update = _run_node(ctx, media_fixtures.FakeWhisperModel)
    harness.check("phase 1 completes", update["phases"]["transcribe"] == "complete", str(update))
    written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
    harness.check("the file says the video has audio", written["hasAudio"] is True, str(written))
    harness.check("and carries both segments", [s["text"] for s in written["segments"]] == [" Hallo", " Welt"],
                  str(written))
    harness.check("the store records the phase", ctx.store.get_phase("transcribe") == "complete")
    harness.check("the extracted audio is under the output folder",
                  (ctx.paths.output_root / output_contract.AUDIO_FILE).is_file())


def test_a_video_with_no_audio_still_writes_a_transcript_file_that_says_so():
    media_fixtures.FakeWhisperModel.calls.clear()
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=False))
    update = _run_node(ctx, media_fixtures.FakeWhisperModel)
    written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
    harness.check("phase 1 completes on a silent video", update["phases"]["transcribe"] == "complete", str(update))
    harness.check("the file says there was no audio, with no segments",
                  (written["hasAudio"], written["segments"]) == (False, []), str(written))
    harness.check("and no speech model was even built", media_fixtures.FakeWhisperModel.calls == [],
                  str(media_fixtures.FakeWhisperModel.calls))


def test_a_phase_that_was_not_asked_for_touches_no_audio():
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    update = _run_node(ctx, media_fixtures.FakeWhisperModel, first_phase=TRANSCRIBE + 2)
    harness.check("phase 1 is passed over", update["phases"]["transcribe"] == "complete", str(update))
    harness.check("and wrote nothing", not phase_file_service.has_phase_file(ctx.paths, TRANSCRIBE))


def test_a_transcription_that_raises_fails_the_phase_without_killing_the_run():
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    update = _run_node(ctx, media_fixtures.BrokenWhisperModel)
    harness.check("the phase is failed", update["phases"]["transcribe"] == "failed", str(update))
    harness.check("and the store says so", ctx.store.get_phase("transcribe") == "failed")
    stage = frames_preparing_progress_service.read_progress(
        ctx.run_dir, frames_preparing_progress_service.TRANSCRIBE_PROGRESS_FILE)[0]
    harness.check("its transcription stage is failed, not running for good", stage["status"] == "failed", str(stage))


def test_the_transcript_file_is_checked_for_its_shape():
    paths = _load_run_context(media_fixtures.build_clip(is_with_audio=False)).paths
    harness.check("a missing file is T_MISSING",
                  harness.codes(transcript_file_validator.validate_transcript_file(paths)) == {"T_MISSING"})
    phase_file_service.write_phase_file(paths, TRANSCRIBE, {"hasAudio": True})
    harness.check("a file with no segments list is T_SEGMENTS",
                  harness.codes(transcript_file_validator.validate_transcript_file(paths)) == {"T_SEGMENTS"})
    backwards = {"hasAudio": True, "segments": [{"text": "x", "start": 5.0, "end": 2.0, "words": []}]}
    phase_file_service.write_phase_file(paths, TRANSCRIBE, backwards)
    harness.check("a segment that ends before it starts is T_SEGMENT_TIMES",
                  harness.codes(transcript_file_validator.validate_transcript_file(paths)) == {"T_SEGMENT_TIMES"})
    phase_file_service.write_phase_file(paths, TRANSCRIBE, {"hasAudio": True, **SPOKEN})
    harness.check("a well-formed transcript passes", transcript_file_validator.validate_transcript_file(paths).ok)
    phase_file_service.write_phase_file(paths, TRANSCRIBE, {"hasAudio": False, "segments": []})
    harness.check("a silent video's empty transcript passes too",
                  transcript_file_validator.validate_transcript_file(paths).ok)


def test_a_transcript_beside_the_recording_is_recorded_and_never_replaces_phase_1s_text():
    """R14: measured worse on names and terms, so it is described, and its speakers kept, and nothing more."""

    video = media_fixtures.build_clip(is_with_audio=True)
    (video.parent / "meeting-en-US.vtt").write_text(
        "WEBVTT\n\nc-1\n00:00:00.000 --> 00:00:01.000\n<v Dana Weber>Hallo Welt</v>\n", encoding="utf-8")
    ctx = _load_run_context(video)
    update = _run_node(ctx, media_fixtures.FakeWhisperModel)
    harness.check("phase 1 completes", update["phases"]["transcribe"] == "complete", str(update))
    written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
    harness.check("the text is still phase 1's own", [s["text"] for s in written["segments"]] == [" Hallo", " Welt"],
                  str(written))
    provided = written.get("providedTranscript", {})
    harness.check("the transcript beside it is recorded with its speakers and agreement",
                  provided.get("file") == "meeting-en-US.vtt" and provided.get("speakers") == ["Dana Weber"]
                  and provided.get("agreement") == 1.0, str(provided))


def test_a_pdf_s_phase_1_moves_step_by_step_with_its_time_left_and_its_last_progress_defect():
    # Defect: a 6,761-page PDF showed one stage at 6,761 of 6,761 for hours, with no sign whether it still moved
    # Fix: each step of a PDF's reading is its own stage, counted page by page, with the time left at its pace and the
    # time since it last counted a page
    progress = frames_preparing_progress_service
    run_dir = Path(tempfile.mkdtemp())
    now = [1_000.0]
    previous = progress.set_clock(lambda: now[0])
    try:
        progress.open_progress(run_dir, progress.PDF_STAGES, progress.TRANSCRIBE_PROGRESS_FILE)
        progress.start_stage(run_dir, progress.TEXT_LAYER, None, progress.TRANSCRIBE_PROGRESS_FILE)
        now[0] = 1_010.0
        progress.report_step(run_dir=run_dir, name=progress.TEXT_LAYER, done=10, total=10,
                             file_name=progress.TRANSCRIBE_PROGRESS_FILE)
        now[0] = 1_020.0
        progress.report_step(run_dir=run_dir, name=progress.PARSING, done=1, total=10,
                             file_name=progress.TRANSCRIBE_PROGRESS_FILE)
        now[0] = 1_040.0
        progress.report_step(run_dir=run_dir, name=progress.PARSING, done=5, total=10,
                             file_name=progress.TRANSCRIBE_PROGRESS_FILE)
        now[0] = 1_100.0
        stages = {stage["name"]: stage for stage in progress.read_progress(run_dir, progress.TRANSCRIBE_PROGRESS_FILE)}
    finally:
        progress.set_clock(previous)
    harness.check("the step before is done, the one never entered skipped, the current running, the rest waiting",
                  [stages[name]["status"] for name in progress.PDF_STAGES]
                  == ["done", "skipped", "running", "waiting", "waiting", "waiting"], str(stages))
    harness.check("80 s for 5 of 10 pages: 80 s left at that pace", stages["parsing"]["seconds_left"] == 80,
                  str(stages["parsing"]))
    harness.check("the last page counted 60 s ago", stages["parsing"]["seconds_since_progress"] == 60,
                  str(stages["parsing"]))
    harness.check("a stage not running says neither", stages["text layer"]["seconds_left"] is None
                  and stages["text layer"]["seconds_since_progress"] is None, str(stages["text layer"]))
