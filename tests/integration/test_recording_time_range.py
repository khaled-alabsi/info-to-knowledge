"""A run's time range on made media: phase 1 cuts the audio of the window alone with the real ffmpeg
and keeps its times on the recording's clock, and phase 2's real capture loop takes frames only inside the window, each
at its own time. The audio is `tests/resources/audio/two-voices.wav` (28 s, two rendered voices); the video
`tests/resources/scenes/loop-slide-table.mp4` (39.8 s), rendered from HTML pages by its `render.sh`."""

import logging
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import harness

import config
from graph.nodes import transcribe_audio_node
from models.input_range_spec import InputRangeSpec
from models.run_paths import RunPaths
from services import audio_extraction_service
from services import frame_extraction_service
from services import frame_preparation_service
from services import input_range_service
from services import phase_file_service
from services import run_paths_service
from services import transcription_service

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
AUDIO = RESOURCES / "audio" / "two-voices.wav"
VIDEO = RESOURCES / "scenes" / "loop-slide-table.mp4"


def _transcribe_whole_wav(audio_path: Path,
                          settings) -> dict:
    """One segment over the whole WAV it is given, from the WAV's own length: what the window's audio holds is read
    from the file ffmpeg wrote, never from the range."""

    duration_s = audio_extraction_service.read_duration(audio_path)

    return {"language": "en", "languageProbability": 0.9, "engine": "e", "model": "m", "text": " words",
            "segments": [{"text": " words", "start": 0.0, "end": duration_s,
                          "words": [{"word": " words", "start": 0.0, "end": duration_s}]}]}


def test_phase_1_transcribes_the_window_alone_and_keeps_its_times_on_the_recording_s_clock():
    if not AUDIO.is_file():
        harness.check("the made audio is there", False, str(AUDIO))
        return
    folder = Path(tempfile.mkdtemp())
    recording = Path(shutil.copy(AUDIO, folder / AUDIO.name))
    output = folder / "out"
    output.mkdir()
    input_range_service.write_range(folder=output, input_range=InputRangeSpec(start_s=10.0, end_s=20.0))
    ctx = SimpleNamespace(repository=folder, module=recording.name, log=logging.getLogger("test"),
                          paths=SimpleNamespace(output_root=output))
    produce = transcription_service.produce_transcript
    transcription_service.produce_transcript = _transcribe_whole_wav
    try:
        transcript, audio_parts = transcribe_audio_node._build_recording_transcript(ctx)
    finally:
        transcription_service.produce_transcript = produce
    segment = transcript["segments"][0]
    harness.check("the audio cut is the window's 10 s", abs((segment["end"] - segment["start"]) - 10.0) < 0.1,
                  str(segment))
    harness.check("its speech stands at 10 s on the recording's clock", abs(segment["start"] - 10.0) < 0.01
                  and abs(segment["words"][0]["start"] - 10.0) < 0.01, str(segment))
    harness.check("diarization is handed the window's audio at its place", len(audio_parts) == 1
                  and audio_parts[0][1] == 10.0, str(audio_parts))


def test_phase_1_s_diarization_turns_stand_on_the_recording_s_clock():
    turns = {"turns": [{"start": 1.0, "end": 3.0, "label": "S0"}], "exclusiveTurns": [], "device": "cpu"}
    moved = transcribe_audio_node._move_turns(turns, 600.0)
    harness.check("a turn 1 s into a window at 10:00 stands at 10:01", moved["turns"][0]["start"] == 601.0
                  and moved["turns"][0]["end"] == 603.0, str(moved))
    harness.check("no window, nothing moved", transcribe_audio_node._move_turns(turns, 0.0) is turns)


def test_phase_2_takes_frames_only_inside_the_window_each_at_its_own_time():
    if not VIDEO.is_file():
        print(f"scene video not built: run {VIDEO.parent / 'render.sh'} (needs Chrome and ffmpeg): skipped")
        return
    settings = config.FrameSettings(mode="change")
    whole = frame_extraction_service.extract_frames(VIDEO, Path(tempfile.mkdtemp()) / "frames", settings)["items"]
    window = frame_extraction_service.extract_frames(VIDEO, Path(tempfile.mkdtemp()) / "frames", settings,
                                                     input_range=InputRangeSpec(start_s=15.0, end_s=30.0))["items"]
    times = [item["timestamp"] for item in window]
    harness.check("the whole video gives frames outside the window", any(not 15.0 <= item["timestamp"] < 30.0
                                                                       for item in whole),
                  str([item["timestamp"] for item in whole]))
    harness.check("the window gives frames", bool(window), str(times))
    harness.check("every frame lies inside the window, at the recording's own time",
                  all(15.0 <= time < 30.0 for time in times), str(times))
    harness.check("their slugs start at frame-000", [item["slug"] for item in window]
                  == [f"frame-{index:03d}" for index in range(len(window))], str([item["slug"] for item in window]))


def test_phase_2_reads_the_run_s_window_from_its_folder():
    if not VIDEO.is_file():
        return
    folder = Path(tempfile.mkdtemp())
    video = Path(shutil.copy(VIDEO, folder / VIDEO.name))
    paths = RunPaths(repository=folder, module=video.name, output_root_override=folder / "out")
    run_paths_service.ensure_run_paths(paths)
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": False, "language": None,
                                                   "text": "", "segments": []})
    input_range_service.write_range(folder=paths.output_root, input_range=InputRangeSpec(start_s=20.0))
    run_dir = paths.output_root / "logs" / "2026-11-09_21-00"
    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = SimpleNamespace(repository=folder, module=video.name, paths=paths, run_dir=run_dir,
                          log=logging.getLogger("test"))
    frames = config.settings.frames
    names = ("selection_mode", "is_knowledge_filter_on", "is_text_only_mark_on", "is_board_stage_on",
             "is_redundancy_filter_on", "is_similarity_recheck_on")
    previous = [getattr(frames, name) for name in names]
    for name, value in zip(names, ("change", False, False, False, False, False)):
        setattr(frames, name, value)
    try:
        prepared = frame_preparation_service.prepare_frames(ctx)
    finally:
        for name, value in zip(names, previous):
            setattr(frames, name, value)
    times = [item["timestamp"] for item in prepared["items"]]
    harness.check("every kept frame lies from 20 s on", bool(times) and min(times) >= 20.0, str(times))
