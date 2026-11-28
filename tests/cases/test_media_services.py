"""Phase 1 and phase 2 are plain code, and they are tested as plain code: against synthetic media
whose content is known. No model, no network. `ffmpeg` builds the two one-second clips; OpenCV
builds the video with three scenes.
"""

import json
import logging
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from models.run_paths import RunPaths
from models.shared_screen_result import SharedScreenResult
from models.video_errors import AudioExtractionFailed, MissingMediaTool, UnreadableVideo
from services import audio_extraction_service
from services import frame_extraction_service
from services import frame_preparation_service
from services import phase_file_service
from services import run_paths_service
from services import faster_whisper_client
from services import recording_frame_service
from services import transcription_service
from utils import frame_slug_utils



def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = paths.output_root / "logs" / "2026-10-01_09-00"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"), control_check=None)


def test_a_video_with_no_audio_stream_is_detected_by_asking_not_by_reading_an_error():
    harness.check("the silent clip has no audio stream",
                  audio_extraction_service.has_audio_stream(media_fixtures.build_clip(is_with_audio=False)) is False)
    harness.check("the other clip has one",
                  audio_extraction_service.has_audio_stream(media_fixtures.build_clip(is_with_audio=True)) is True)


def test_audio_is_extracted_as_16_khz_mono_and_a_silent_video_yields_none():
    folder = Path(tempfile.mkdtemp())
    harness.check("a video with no audio stream extracts to None",
                  audio_extraction_service.extract_audio(media_fixtures.build_clip(is_with_audio=False), folder / "a.wav") is None)
    extracted = audio_extraction_service.extract_audio(media_fixtures.build_clip(is_with_audio=True), folder / "b.wav")
    with wave.open(str(extracted), "rb") as handle:
        harness.check("the WAV is 16 kHz mono", (handle.getframerate(), handle.getnchannels()) == (16000, 1),
                      f"{handle.getframerate()} Hz, {handle.getnchannels()} channel(s)")


def test_a_missing_media_tool_is_named():
    with mock.patch.object(audio_extraction_service, "FFPROBE", "no-such-ffprobe-binary"):
        try:
            audio_extraction_service.has_audio_stream(Path("whatever.mp4"))
        except MissingMediaTool as exc:
            harness.check("the refusal names the binary", "no-such-ffprobe-binary" in str(exc), str(exc))
        else:
            harness.check("a missing ffprobe raises", False)


def test_a_container_with_no_stored_duration_is_a_named_failure_not_a_bare_value_error():
    probed = SimpleNamespace(stdout=b"N/A\n")
    with mock.patch.object(audio_extraction_service, "_run_tool", return_value=probed):
        try:
            audio_extraction_service.read_duration(Path("part.ogg"))
            outcome = "returned a length"
        except AudioExtractionFailed as exc:
            outcome = f"named: {exc}"
        except ValueError as exc:
            outcome = f"bare ValueError: {exc}"
    harness.check("the failure names the file", outcome.startswith("named:") and "part.ogg" in outcome, outcome)


def test_the_transcription_carries_the_settings_that_worked_and_every_word_timing():
    media_fixtures.FakeWhisperModel.calls.clear()
    previous = faster_whisper_client.MODEL_FACTORY
    faster_whisper_client.set_model_factory(media_fixtures.FakeWhisperModel)
    try:
        transcript = transcription_service.produce_transcript(Path("audio.wav"), config.AudioSettings())
    finally:
        faster_whisper_client.set_model_factory(previous)
    asked = next(call["transcribe"] for call in media_fixtures.FakeWhisperModel.calls if "transcribe" in call)
    harness.check("VAD is on with the carried silence length",
                  asked["vad_filter"] is True and asked["vad_parameters"] == {"min_silence_duration_ms": 500}, str(asked))
    harness.check("beam size 5, word timings on, language detected",
                  (asked["beam_size"], asked["word_timestamps"], asked["language"]) == (5, True, None), str(asked))
    harness.check("the detected language is recorded", transcript["language"] == "de", str(transcript))
    harness.check("segments keep their times", [(s["start"], s["end"]) for s in transcript["segments"]]
                  == [(0.0, 0.5), (0.5, 1.0)], str(transcript["segments"]))
    harness.check("a segment with no word timings gets an empty list, not a crash",
                  transcript["segments"][1]["words"] == [], str(transcript["segments"][1]))
    harness.check("the text is the segments joined", transcript["text"] == "Hallo Welt", repr(transcript["text"]))


def test_audio_with_no_speech_is_an_empty_transcript_not_a_failure():
    previous = faster_whisper_client.MODEL_FACTORY
    faster_whisper_client.set_model_factory(media_fixtures.SilentWhisperModel)
    try:
        transcript = transcription_service.produce_transcript(Path("audio.wav"), config.AudioSettings())
    finally:
        faster_whisper_client.set_model_factory(previous)
    harness.check("no speech is an empty segment list", transcript["segments"] == [] and transcript["text"] == "",
                  str(transcript))


def test_scene_mode_cuts_one_frame_per_scene_in_order():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_three_scene_video(folder)
    settings = config.FrameSettings(mode="scene", scene_threshold=0.3, min_scene_seconds=0.0, max_count=60)
    frames = frame_extraction_service.extract_frames(video, folder / "frames", settings)
    harness.check("three scenes give three frames", len(frames["items"]) == 3, str(frames["items"]))
    timestamps = [item["timestamp"] for item in frames["items"]]
    harness.check("timestamps increase", timestamps == sorted(timestamps), str(timestamps))
    harness.check("each frame is cut mid-scene", timestamps == [1.0, 2.5, 4.5], str(timestamps))
    harness.check("slugs are zero padded and in order", [item["slug"] for item in frames["items"]]
                  == ["frame-000", "frame-001", "frame-002"], str(frames["items"]))
    harness.check("every JPEG is on disk and not empty",
                  all((folder / "frames" / Path(item["path"]).name).stat().st_size > 0 for item in frames["items"]))
    harness.check("the video's own facts are recorded", (frames["video"]["fps"], frames["video"]["frameCount"])
                  == (float(media_fixtures.FPS), 60), str(frames["video"]))


def test_interval_mode_follows_the_clock_and_still_comes_back_in_order():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_three_scene_video(folder)
    settings = config.FrameSettings(mode="interval", per_minute=30, max_count=60)
    frames = frame_extraction_service.extract_frames(video, folder / "frames", settings)
    harness.check("6 seconds at 30 per minute is 3 frames", len(frames["items"]) == 3, str(frames["items"]))
    timestamps = [item["timestamp"] for item in frames["items"]]
    harness.check("in chronological order, never by score", timestamps == sorted(timestamps), str(timestamps))
    capped = frame_extraction_service.extract_frames(video, folder / "capped",
                                                     config.FrameSettings(mode="interval", per_minute=600, max_count=2))
    harness.check("max_count caps interval mode too", len(capped["items"]) == 2, str(capped["items"]))


def test_a_file_that_is_not_a_video_is_refused_by_name():
    folder = Path(tempfile.mkdtemp())
    not_a_video = folder / "notes.mp4"
    not_a_video.write_text("not a video", encoding="utf-8")
    try:
        frame_extraction_service.extract_frames(not_a_video, folder / "frames", config.FrameSettings())
    except UnreadableVideo as exc:
        harness.check("the refusal names the file", "notes.mp4" in str(exc), str(exc))
    else:
        harness.check("an unreadable video raises", False)


def test_frames_preparing_reads_the_transcript_from_disk_and_gives_every_frame_its_speech():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    try:
        frame_preparation_service.prepare_frames(ctx)
    except MissingPhaseInput as exc:
        harness.check("with no transcript on disk, frames preparing refuses naming the file",
                      output_contract.TRANSCRIPT_FILE in str(exc), str(exc))
    else:
        harness.check("frames preparing refuses without phase 1's file", False)

    segments = [{"text": " eins zwei", "start": 0.0, "end": 2.0,
                 "words": [{"word": " eins", "start": 0.2, "end": 0.6}, {"word": " zwei", "start": 5.0, "end": 5.5}]}]
    phase_file_service.write_phase_file(ctx.paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                       "text": "eins zwei", "segments": segments})
    previous = dict(config.settings.frames)
    config.settings.frames.selection_mode, config.settings.frames.min_scene_seconds = "scene", 0.0
    # the filter, the text-only mark and the board stage have their own case files; any one of them on starts the real
    # frame classifier, which took this test 19 of its 20 s
    config.settings.frames.is_knowledge_filter_on = False
    config.settings.frames.is_text_only_mark_on = False
    config.settings.frames.is_board_stage_on = False
    try:
        items = frame_preparation_service.prepare_frames(ctx)["items"]
    finally:
        for key in ("selection_mode", "min_scene_seconds", "is_knowledge_filter_on", "is_text_only_mark_on",
                    "is_board_stage_on"):
            setattr(config.settings.frames, key, previous[key])
    harness.check("one item per frame, each carrying both slices",
                  len(items) == 3 and all({"transcriptBefore", "transcriptCurrent"} <= set(item) for item in items),
                  json.dumps(items)[:300])
    harness.check("the first word lands in frame 0's window", items[0]["transcriptCurrent"] == "eins", str(items[0]))
    harness.check("the late word lands in the last frame's window", items[2]["transcriptCurrent"] == "zwei", str(items[2]))
    harness.check("and the last frame has heard the first word", items[2]["transcriptBefore"] == "eins", str(items[2]))
    harness.check("the frames are under the output folder",
                  (ctx.paths.output_root / items[0]["path"]).is_file(), items[0]["path"])


def test_a_frame_whose_jpeg_cannot_be_made_stops_the_phase_by_name():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_three_scene_video(folder)
    settings = config.FrameSettings(mode="scene", scene_threshold=0.3, min_scene_seconds=0.0, max_count=60)
    with mock.patch.object(frame_extraction_service.cv2, "imencode", return_value=(False, None)):
        try:
            frames = frame_extraction_service.extract_frames(video, folder / "frames", settings)
            message = f"listed {len(frames['items'])} frames with no file"
        except OSError as exc:
            message = str(exc)
    harness.check("the frame that could not be encoded is named, and nothing is listed", "frame-000" in message, message)


def test_a_change_recording_whose_first_second_does_not_decode_is_still_read():
    class OneBadSecond:
        """A recording of three seconds at 2 fps whose first sampled frame does not decode."""

        def __init__(self):
            self.number = -1

        def skip_frame(self):
            self.number += 1
            return self.number < 6

        def decode_frame(self):
            if self.number == 0:
                return False, None
            return True, numpy.full((90, 160, 3), 200, numpy.uint8)

    settings = config.FrameSettings(mode="change")
    try:
        taken = frame_extraction_service._take_screen_changes(OneBadSecond(), {"fps": 2.0, "frameCount": 6, "durationS": 3.0, "frameTimes": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]}, settings)
        outcome = f"read, {len(taken)} taken"
    except TypeError as exc:
        outcome = f"raised {exc}"
    harness.check("the seconds after an undecodable first one are read", outcome.startswith("read"), outcome)


def test_a_recording_with_a_variable_frame_rate_is_sampled_and_timed_by_its_own_frame_times():
    """A screen recording runs at 120 frames a second while the screen moves and a few while it is still, so a frame
    number divided by the average rate was up to 107 s off: the notes got another moment's speech, and the cut tool
    another moment's picture. Each second is sampled by the frames' own times, and each item keeps its frame's time."""

    class VariableRate:
        """Ten frames in the first second, then one a second for four seconds."""

        def __init__(self):
            self.number = -1
            self.decoded: list[int] = []

        def skip_frame(self):
            self.number += 1
            return self.number < 14

        def decode_frame(self):
            self.decoded.append(self.number)
            return True, numpy.full((90, 160, 3), 200, numpy.uint8)

    times = [index / 10 for index in range(10)] + [1.0, 2.0, 3.0, 4.0]
    video = {"fps": 2.8, "frameCount": 14, "durationS": 5.0, "frameTimes": times}
    capture = VariableRate()
    frame_extraction_service._take_screen_changes(capture, video, config.FrameSettings(mode="change"))
    harness.check("one frame is decoded for each second of the recording, never one per average count of frames",
                  capture.decoded == [0, 10, 11, 12, 13], str(capture.decoded))
    item = frame_extraction_service._build_item(0, 12, 0.0, video, Path("frames"))
    harness.check("a taken frame's time is its own, not its number divided by the average rate",
                  item["timestamp"] == 3.0, str(item))


def test_a_frame_whose_moment_shows_another_screen_waits_for_the_new_box():
    """The box changes on its second check, and checks come every few seconds: a talk that went from its slide beside
    the speaker to the slide in full had its full slides taken with the old box, cut through their left side
    (38c3-fault-injection frames 11, 16 and 40). A moment whose own screen is another box is taken once that box holds."""

    side_box, full_box = (40, 10, 120, 80), (0, 0, 160, 90)
    # Two drawn slides, never random noise, which phase 2 refuses to take as noise (81034e7)
    patterns = [numpy.full((90, 160, 3), 240, numpy.uint8) for _ in range(2)]
    cv2.rectangle(patterns[0], (50, 20), (110, 70), (30, 90, 200), -1)
    cv2.circle(patterns[1], (80, 45), 30, (20, 160, 60), -1)
    cv2.rectangle(patterns[1], (10, 10), (150, 20), (40, 40, 40), -1)

    class SideThenFull:
        """Sixteen seconds at one frame a second: one slide beside the speaker until 7 s, another in full after."""

        def __init__(self):
            self.number = -1

        def skip_frame(self):
            self.number += 1
            return self.number < 16

        def decode_frame(self):
            return True, patterns[0 if self.number < 7 else 1].copy()

    capture = SideThenFull()

    def find_screen(frame, gray):
        return SharedScreenResult(side_box if capture.number < 7 else full_box, None)

    video = {"fps": 1.0, "frameCount": 16, "durationS": 16.0, "frameTimes": [float(index) for index in range(16)]}
    settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                    box_every_seconds=5, max_width=0)
    with mock.patch.object(frame_extraction_service, "_find_shared_screen", side_effect=find_screen):
        taken = frame_extraction_service._take_screen_changes(capture, video, settings)
    boxes = [(item["frameNumber"], tuple(item["box"])) for item in taken]
    harness.check("each frame is cut with the box its own moment shows, and the full slide is still taken",
                  boxes and all(box == (side_box if number < 7 else full_box) for number, box in boxes)
                  and boxes[-1][1] == full_box, str(boxes))


def test_a_frame_whose_seek_fails_is_no_frame_not_another_moment():

    class FailedSeek:
        def is_open(self):
            return True

        def move_to_frame(self, frame_number):
            return False

        def read_frame(self):
            return True, numpy.full((4, 4, 3), 9, numpy.uint8)

        def release(self):
            pass

    with mock.patch.object(recording_frame_service.recording_parts_service, "open_recording", lambda path: FailedSeek()):
        frame = recording_frame_service.read_recording_frame(Path("meeting.mp4"), 500)
    harness.check("a seek that failed gives no frame", frame is None, str(None if frame is None else frame.shape))


def test_a_frame_s_slug_is_its_number_in_three_digits():
    harness.check("frame 7 is frame-007, frame 1234 keeps every digit",
                  (frame_slug_utils.build_frame_slug(7), frame_slug_utils.build_frame_slug(1234)) == ("frame-007", "frame-1234"))



def test_a_moving_stretch_with_nothing_new_to_read_is_sampled_more_slowly_defect():
    # Defect: a tour video in a talk, a camera moving through a tunnel, gave a frame every 10 s, most with only the
    # caption the frame before held. Fix: after two moving frames with no new word, the next waits three times as long.
    def moving(second):
        # a new picture every second, as a camera moving through a tunnel: never still, nothing repeated
        draw, image = numpy.random.default_rng(second), numpy.full((180, 320, 3), 128, numpy.uint8)
        for _ in range(40):
            cv2.rectangle(image, tuple(int(value) for value in draw.integers(0, 300, 2)),
                          tuple(int(value) for value in draw.integers(20, 320, 2)),
                          tuple(int(value) for value in draw.integers(0, 255, 3)), -1)
        return image

    class Moving:
        def __init__(self):
            self.number = -1

        def skip_frame(self):
            self.number += 1
            return self.number < 70

        def decode_frame(self):
            return True, moving(self.number)

    def find_screen(frame, gray):
        return SharedScreenResult((0, 0, 320, 180), None)

    video = {"fps": 1.0, "frameCount": 70, "durationS": 70.0, "frameTimes": [float(index) for index in range(70)]}
    settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                    box_every_seconds=5, max_width=0)
    with mock.patch.object(frame_extraction_service, "_find_shared_screen", side_effect=find_screen), \
            mock.patch.object(frame_extraction_service, "_read_band_words", return_value={"linear", "accelerator"}):
        taken = frame_extraction_service._take_screen_changes(Moving(), video, settings)
    harness.check("70 s of motion with one caption give a frame every 30 s after the second, never every 10 s",
                  [item["frameNumber"] for item in taken] == [0, 10, 40], str([item["frameNumber"] for item in taken]))
    # each read a word of its own, no two one letter apart, which the reader would count as one word misread
    reads = iter(range(1000))

    def new_word(image):
        number = next(reads)
        return {"".join(chr(97 + (number * 7 + place * 5) % 26) for place in range(6))}

    with mock.patch.object(frame_extraction_service, "_find_shared_screen", side_effect=find_screen), \
            mock.patch.object(frame_extraction_service, "_read_band_words",
                              side_effect=new_word):
        changing = frame_extraction_service._take_screen_changes(Moving(), video, settings)
    harness.check("motion whose words change is still taken every 10 s",
                  [item["frameNumber"] for item in changing] == [0, 10, 20, 30, 40, 50, 60],
                  str([item["frameNumber"] for item in changing]))


def test_a_screen_held_for_minutes_is_taken_again_as_a_repeat_defect():
    # Defect: one frame stayed on screen for 6 minutes and carried the notes of several subjects, so a heading started
    # inside its speech got none of them. Fix: a screen held still for 3 minutes is taken again, a repeat of itself.
    slides = [numpy.full((180, 320, 3), level, numpy.uint8) for level in (200, 90)]
    for index, slide in enumerate(slides):
        cv2.putText(slide, f"Slide {index}", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)

    class Held:
        def __init__(self):
            self.number = -1

        def skip_frame(self):
            self.number += 1
            return self.number < 500

        def decode_frame(self):
            return True, slides[0 if self.number < 420 else 1].copy()

    video = {"fps": 1.0, "frameCount": 500, "durationS": 500.0, "frameTimes": [float(index) for index in range(500)]}
    settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                    box_every_seconds=5, max_width=0)
    with mock.patch.object(frame_extraction_service, "_find_shared_screen",
                           side_effect=lambda frame, gray: SharedScreenResult((0, 0, 320, 180), None)):
        taken = frame_extraction_service._take_screen_changes(Held(), video, settings)
    harness.check("the slide held 7 minutes is taken again every 3 minutes, each a repeat of it, then the next slide",
                  [(item["frameNumber"], item.get("repeatIndex")) for item in taken]
                  == [(0, None), (180, 0), (360, 0), (421, None)], str([item["frameNumber"] for item in taken]))
