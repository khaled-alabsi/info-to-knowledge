"""A frame of one flat colour shows nothing and is never taken, decided on a recorded video run through phase 2 with
the real face detector.

A lecture recording opened on a plain red title card: change mode took it as its first frame, since the whole frame
was the screen and nothing before it had been taken, and the model could only describe a red screen. The video here
is shaped like that recording, 640 x 360: three seconds of a flat red card, then a slide with text. In change mode
and in interval mode only the slide may be written.
"""

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from services import face_detection_service
from services import frame_extraction_service

FPS = 10
SIZE = (640, 360)                                          # width, height
CARD_BGR = (56, 0, 163)                                    # the lecture's red title card
TIMELINE = (("card", 3), ("slide", 6))                     # what, for how many seconds
FLAT_SPREAD = 3.0                                          # grey levels of spread a written frame of the card stays under


def _draw_slide() -> numpy.ndarray:
    slide = numpy.full((SIZE[1], SIZE[0], 3), 245, numpy.uint8)
    cv2.putText(slide, "Gateway rollout", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (30, 30, 30), 3)
    for row, line in enumerate(("Tokens go to the provider first", "Refunds wait for an approval",
                                "Jonas owns the release plan")):
        cv2.putText(slide, line, (60, 150 + 50 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 40), 2)

    return slide


def _build_lecture_video(folder: Path) -> Path:
    target = folder / "lecture.mp4"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), FPS, SIZE)
    card = numpy.full((SIZE[1], SIZE[0], 3), CARD_BGR, numpy.uint8)
    slide = _draw_slide()
    for scene, seconds in TIMELINE:
        for _ in range(seconds * FPS):
            writer.write(card if scene == "card" else slide)
    writer.release()

    return target


def _list_flat_frames(folder: Path) -> list[str]:
    flat = [path.name for path in sorted(folder.glob("*.jpg"))
            if float(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE).std()) < FLAT_SPREAD]

    return flat


def test_a_flat_title_card_is_never_taken_and_the_slide_after_it_is():
    folder = Path(tempfile.mkdtemp())
    video = _build_lecture_video(folder)
    with media_fixtures.face_finder(face_detection_service.find_faces_with_yunet):
        change = frame_extraction_service.extract_frames(video, folder / "change",
                                                         config.FrameSettings(mode="change", max_width=0))
        interval = frame_extraction_service.extract_frames(video, folder / "interval",
                                                           config.FrameSettings(mode="interval", per_minute=60,
                                                                                max_width=0))
    seconds = [round(item["timestamp"]) for item in change["items"]]
    harness.check("change mode: the slide alone, taken after the card", len(seconds) == 1 and seconds[0] >= 3,
                  str(seconds))
    for name, frames in (("change", change), ("interval", interval)):
        flat = _list_flat_frames(folder / name)
        harness.check(f"{name} mode: no frame of one flat colour written", not flat and bool(frames["items"]),
                      f"{flat} of {len(frames['items'])}")
