"""One meeting recorded in several video files, read as one video: a folder of two or more videos is one recording, its
parts in natural name order, frame numbers and times running on from one part into the next."""

import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from graph.nodes import transcribe_audio_node
from services import audio_extraction_service
from services import frame_extraction_service
from services import inbox_service
from services import provided_transcript_service
from services import recording_frame_service
from services import recording_parts_service
from services import transcription_service

PART_FRAMES = 10
PART_FPS = 5


def _write_part(path: Path,
                level: int) -> Path:
    """A part of PART_FRAMES frames, every frame one grey level, so a frame read back says which part it came from."""

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), PART_FPS, (64, 36))
    for _ in range(PART_FRAMES):
        writer.write(numpy.full((36, 64, 3), level, dtype=numpy.uint8))
    writer.release()

    return path


def _build_parts_folder() -> Path:
    folder = Path(tempfile.mkdtemp()) / "Weekly-Review"
    folder.mkdir()
    _write_part(folder / "Weekly-Review part 10.mp4", 200)
    _write_part(folder / "Weekly-Review part 2.mp4", 100)
    _write_part(folder / "Weekly-Review part 1.mp4", 0)

    return folder


def test_the_parts_are_read_in_natural_name_order():
    folder = _build_parts_folder()
    names = [path.name for path in recording_parts_service.list_recording_parts(folder)]
    harness.check("part 2 before part 10", names == ["Weekly-Review part 1.mp4", "Weekly-Review part 2.mp4",
                                                     "Weekly-Review part 10.mp4"], str(names))
    harness.check("a folder of several videos is a recording in parts", recording_parts_service.is_multi_part(folder))
    harness.check("a single file is not", not recording_parts_service.is_multi_part(folder / "Weekly-Review part 1.mp4"))


def test_the_parts_read_as_one_video_whose_frame_numbers_run_on():
    folder = _build_parts_folder()
    capture = recording_parts_service.open_recording(folder)
    count, fps = capture.frame_count, capture.fps
    grabbed = 0
    while capture.skip_frame():
        grabbed += 1
    durations = capture.list_part_durations()
    capture.release()
    harness.check("three parts of 10 frames are one video of 30", count == 3 * PART_FRAMES and grabbed == 3 * PART_FRAMES,
                  f"count={count} grabbed={grabbed}")
    harness.check("at the parts' frame rate", round(fps) == PART_FPS, str(fps))
    harness.check("each part's length is known where a file is opened", [round(value) for value in durations] == [2, 2, 2],
                  str(durations))
    levels = [int(recording_frame_service.read_recording_frame(folder, number).mean()) for number in (5, 15, 25)]
    harness.check("frame 15 is part 2's sixth frame, frame 25 part 3's: one numbering across the parts",
                  abs(levels[0] - 0) < 10 and abs(levels[1] - 100) < 10 and abs(levels[2] - 200) < 10, str(levels))


def test_phase_2_numbers_the_frames_of_every_part_on_one_timeline():
    folder = Path(tempfile.mkdtemp()) / "Demo"
    folder.mkdir()
    first = media_fixtures.build_three_scene_video(folder)
    first.rename(folder / "Demo-1.mp4")
    second = media_fixtures.build_three_scene_video(folder)
    second.rename(folder / "Demo-2.mp4")
    settings = config.FrameSettings(mode="scene", max_count=20, max_width=0)
    items = frame_extraction_service.extract_frames(folder, Path(tempfile.mkdtemp()) / "frames", settings)["items"]
    numbers = [item["frameNumber"] for item in items]
    harness.check("the frames run on from part 1 into part 2: part 2's frames carry numbers past part 1's last",
                  numbers == sorted(numbers) and any(number >= 60 for number in numbers), str(numbers))
    harness.check("their slugs never restart", [item["slug"] for item in items]
                  == [f"frame-{index:03d}" for index in range(len(items))], str([item["slug"] for item in items]))
    harness.check("no frame names its part", not any("part" in key.casefold() for item in items for key in item),
                  str(sorted(items[0])))


def test_a_part_s_transcript_moves_onto_the_meeting_s_time():
    part = {"language": "de", "segments": [{"text": "Hallo", "start": 1.0, "end": 2.5,
                                            "words": [{"word": "Hallo", "start": 1.0, "end": 2.5}]}]}
    moved = recording_parts_service.move_transcript_times(part, 1800.0)
    segment = moved["segments"][0]
    harness.check("the segment and its words move by the parts before it",
                  (segment["start"], segment["end"], segment["words"][0]["start"]) == (1801.0, 1802.5, 1801.0), str(moved))
    harness.check("the part's own transcript is left as it was", part["segments"][0]["start"] == 1.0)


def test_the_inbox_lists_a_folder_of_parts_once_as_one_meeting():
    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    inbox_root.mkdir()
    parts = _build_parts_folder()
    parts.rename(inbox_root / "Weekly-Review")
    single = inbox_root / "Standup"
    single.mkdir()
    _write_part(single / "standup.mp4", 50)
    items = inbox_service.list_inbox_videos(inbox_root)
    harness.check("the folder of parts is one item, the folder itself; a folder of one video is that video",
                  sorted(path.name for path in items) == ["Weekly-Review", "standup.mp4"], str(items))
    output_root = Path(tempfile.mkdtemp())
    harness.check("its run folder is named after the folder",
                  inbox_service.build_run_folder(output_root, inbox_root / "Weekly-Review") == output_root / "Weekly-Review")


def test_the_meeting_tool_s_transcript_inside_a_folder_of_parts_covers_the_meeting():
    folder = _build_parts_folder()
    (folder / "meeting.vtt").write_text("WEBVTT\n", encoding="utf-8")
    harness.check("the one transcript in the folder is the meeting's",
                  provided_transcript_service.find_provided_transcript(folder) == folder / "meeting.vtt")
    (folder / "second.vtt").write_text("WEBVTT\n", encoding="utf-8")
    harness.check("with one per part, none is used", provided_transcript_service.find_provided_transcript(folder) is None)


def test_a_recording_in_parts_takes_the_language_of_the_part_with_the_most_speech():
    """Each part is transcribed alone and each finds its own language; the joined transcript took the last part's, so a
    short last part of small talk decided the meeting's language."""

    folder = Path(tempfile.mkdtemp())
    parts = [folder / "meeting-1.mp4", folder / "meeting-2.mp4"]
    spoken = {"meeting-1": ("de", [(0.0, 50.0)]), "meeting-2": ("en", [(0.0, 3.0)])}

    def transcribe(audio_path, settings):
        language, spans = spoken[audio_path.stem.removesuffix("-audio")]
        return {"language": language, "languageProbability": 0.9, "engine": "e", "model": "m", "text": "",
                "segments": [{"text": " words", "start": start, "end": end, "words": []} for start, end in spans]}

    originals = (recording_parts_service.is_multi_part, recording_parts_service.list_part_durations,
                 recording_parts_service.list_recording_parts, audio_extraction_service.extract_audio,
                 transcription_service.produce_transcript)
    recording_parts_service.is_multi_part = lambda recording: True
    recording_parts_service.list_part_durations = lambda recording: [60.0, 5.0]
    recording_parts_service.list_recording_parts = lambda recording: parts
    audio_extraction_service.extract_audio = lambda part, target: part.with_name(f"{part.stem}-audio.wav")
    transcription_service.produce_transcript = transcribe
    try:
        ctx = SimpleNamespace(repository=folder, module="meeting", log=logging.getLogger("test"),
                              paths=SimpleNamespace(output_root=folder))
        transcript, _audio_parts = transcribe_audio_node._build_recording_transcript(ctx)
    finally:
        (recording_parts_service.is_multi_part, recording_parts_service.list_part_durations,
         recording_parts_service.list_recording_parts, audio_extraction_service.extract_audio,
         transcription_service.produce_transcript) = originals
    harness.check("the meeting is in the language of its longest speech", transcript["language"] == "de",
                  str(transcript["language"]))
    harness.check("and every part's segments are kept, on the meeting's time",
                  [segment["start"] for segment in transcript["segments"]] == [0.0, 60.0], str(transcript["segments"]))


def test_a_part_name_with_a_superscript_digit_still_sorts():
    from services import recording_parts_service

    names = ["part 10.mp4", "part 1\u00b22.mp4", "part 2.mp4"]
    try:
        outcome = sorted(names, key=recording_parts_service.build_natural_key)
    except ValueError as error:
        outcome = f"raised {error}"
    harness.check("the natural order is built without failing on a digit that is not a decimal digit",
                  outcome == ["part 1\u00b22.mp4", "part 2.mp4", "part 10.mp4"], str(outcome))
