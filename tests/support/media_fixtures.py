"""Synthetic media whose content is known, and fake speech models. No download, no network.

`ffmpeg` builds one-second clips with and without an audio stream; OpenCV builds a video of three
flat-coloured scenes whose cut points are exact. The fake speech models stand in for faster-whisper
through `faster_whisper_client.set_model_factory`, that engine's own seam.
"""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy

from services import face_detection_service
from services import person_detection_service
from services import text_detection_service

FPS = 10
SCENES = ((0, 20), (255, 10), (128, 30))          # (gray level, frames): black 2 s, white 1 s, gray 3 s


def build_clip(is_with_audio: bool) -> Path:
    """A one-second test-pattern clip, with a 440 Hz tone or with no audio stream at all."""

    target = Path(tempfile.mkdtemp()) / ("with-audio.mp4" if is_with_audio else "silent.mp4")
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x36:rate=5"]
    if is_with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest"]
    subprocess.run([*command, str(target)], check=True, capture_output=True)

    return target


def build_three_scene_video(folder: Path) -> Path:
    """Six seconds at 10 fps: 20 black frames, 10 white, 30 gray. Cuts at frames 20 and 30. Each scene carries a stripe
    128 grey levels off its background, since phase 2 never takes a frame of one flat colour."""

    target = folder / "clip.mp4"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (64, 36))
    for level, count in SCENES:
        scene = numpy.full((36, 64, 3), level, dtype=numpy.uint8)
        scene[12:24, 8:56] = (level + 128) % 256
        for _ in range(count):
            writer.write(scene)
    writer.release()

    return target


MEETING_FPS = 5
MEETING_SIZE = (480, 270)                                  # width, height
MEETING_SCREEN = (0, 20, 400, 245)                         # the shared screen: x0, y0, x1, y1
MEETING_TIMELINE = (("slide-a", 6), ("slide-b", 6), ("slide-a", 5), ("gallery", 7))   # what, for how many seconds
GALLERY_FACES = [(90, 40, 130, 90), (90, 170, 130, 220), (270, 40, 310, 90), (270, 170, 310, 220)]


def _draw_slide(bar_tops: tuple[int, ...]) -> numpy.ndarray:
    """A pale slide with dark bars at the given rows: lines of text, as far as a block comparison can tell."""

    slide = numpy.full((225, 400, 3), 230, numpy.uint8)
    for top in bar_tops:
        slide[top:top + 8, 30:370] = 40

    return slide


def draw_meeting_frame(scene: str,
                        rng: numpy.random.Generator) -> numpy.ndarray:
    """One frame of a Teams recording: black stage, and either a shared slide with a moving camera panel beside it, or a
    gallery of four touching camera tiles with a thin dark line between the columns and nothing shared."""

    frame = numpy.zeros((MEETING_SIZE[1], MEETING_SIZE[0], 3), numpy.uint8)
    if scene == "gallery":
        frame[:, 0:198] = 80
        frame[:, 198:202] = 10
        frame[:, 202:400] = 150
        frame[:, 0:400] = numpy.clip(frame[:, 0:400].astype(int) + rng.integers(-4, 5, (270, 400, 1)), 0, 255)

        return frame
    x0, y0, x1, y1 = MEETING_SCREEN
    frame[y0:y1, x0:x1] = _draw_slide((20, 60, 100, 140, 180) if scene == "slide-a" else (40, 70, 120, 150, 200))
    frame[20:100, 405:475] = rng.integers(40, 200, (80, 70, 1))
    frame[110:190, 405:475] = rng.integers(40, 200, (80, 70, 1))

    return frame


def build_meeting_video(folder: Path) -> Path:
    """24 seconds at 5 fps of a meeting whose content is known: slide A for 6 s, slide B for 6 s, slide A again for 5 s,
    then 7 s of a gallery of faces. A camera panel keeps moving beside the slides the whole time they are shown."""

    target = folder / "meeting.mp4"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), MEETING_FPS, MEETING_SIZE)
    rng = numpy.random.default_rng(7)
    for scene, seconds in MEETING_TIMELINE:
        for _ in range(seconds * MEETING_FPS):
            writer.write(draw_meeting_frame(scene, rng))
    writer.release()

    return target


def find_gallery_faces(gray: numpy.ndarray) -> list[tuple[int, int, int, int]]:
    """A stand-in face finder for the meeting video: the four gallery faces whenever the top rows are lit, which only
    the gallery's tiles do; nothing on a frame with a shared slide. Judged on the mean of the top rows, not one pixel:
    the codec smudges the first frame's black by a few grey levels."""

    faces = list(GALLERY_FACES) if gray[:10, :400].mean() > 40 else []

    return faces


class FakeWhisperModel:
    """Records what it was asked, and answers with two segments, one of them with no word timings."""

    calls: list[dict] = []

    def __init__(self, *arguments, **keywords):
        FakeWhisperModel.calls.append({"init": (arguments, keywords)})

    def transcribe(self, audio_path: str, **keywords):
        FakeWhisperModel.calls.append({"transcribe": keywords})
        word = SimpleNamespace(word=" Hallo", start=0.1, end=0.4, probability=0.9)
        segments = [SimpleNamespace(text=" Hallo", start=0.0, end=0.5, words=[word]),
                    SimpleNamespace(text=" Welt", start=0.5, end=1.0, words=None)]

        return iter(segments), SimpleNamespace(language="de", language_probability=1.0)


class SilentWhisperModel(FakeWhisperModel):
    """Audio in which the model hears no speech."""

    def transcribe(self, audio_path: str, **keywords):
        return iter([]), SimpleNamespace(language="de", language_probability=0.4)


class BrokenWhisperModel(FakeWhisperModel):
    """A model that fails the way a wrong device does: at the first transcription."""

    def transcribe(self, audio_path: str, **keywords):
        raise ValueError("unsupported device mps")


@contextlib.contextmanager
def face_finder(finder):
    """The face finder phase 2 uses, swapped for `finder` for a block and put back after it."""

    previous = face_detection_service.FACE_FINDER
    face_detection_service.set_face_finder(finder)
    try:
        yield
    finally:
        face_detection_service.set_face_finder(previous)


@contextlib.contextmanager
def person_finder(finder):
    """The person finder phase 2 uses, swapped for `finder` for a block and put back after it."""

    previous = person_detection_service.PERSON_FINDER
    person_detection_service.set_person_finder(finder)
    try:
        yield
    finally:
        person_detection_service.set_person_finder(previous)


@contextlib.contextmanager
def word_finder(finder):
    """The text reader's `find_words`, swapped for `finder` for a block and put back after it: every caller reads it
    through its module, so the swap reaches them all."""

    previous = text_detection_service.find_words
    text_detection_service.find_words = finder
    try:
        yield
    finally:
        text_detection_service.find_words = previous
