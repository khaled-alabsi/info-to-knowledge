"""Finding the faces a frame shows, with OpenCV's YuNet detector. The side effect is loading its model file, once per
path; everything runs on this machine's processor, inside the program, with the 0.2 MB YuNet file in `weights/` (which
the container image ships). No model server is asked for faces.

A face alone decides nothing: `screen_crop_utils` cuts a panel only where at least two faces stand in a column beside
the screen and a straight edge runs along them, so a photo inside a slide or a web page is never cut.

`FACE_FINDER` is this service's seam: a test swaps in a finder that knows where its synthetic faces are, through
`set_face_finder`, and restores it afterwards.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import numpy

import config
from models.video_errors import MissingFaceModel

__all__ = ["detect_faces", "find_faces_with_yunet", "set_face_finder", "FACE_FINDER"]

DETECT_WIDTH = 1000                         # faces are looked for on a copy of the frame this wide
FACE_SCORE = 0.6                            # a detection scoring below this is not a face
NMS_THRESHOLD = 0.3                         # two boxes overlapping more than this are one face
TOP_K = 100                                 # at most this many candidates are kept before overlaps are merged


# A face as x0, y0, x1, y1 in the frame's own pixels (YuNet itself answers x, y, width, height)
FaceBox = tuple[int, int, int, int]
FaceFinder = Callable[[numpy.ndarray], list[FaceBox]]


@lru_cache(maxsize=2)
def _load_detector(model_path: Path) -> cv2.FaceDetectorYN:
    if not model_path.is_file():
        raise MissingFaceModel(f"the face detector's model {model_path} is missing: set `frames.faceModel` to the "
                               f"YuNet .onnx file, or put it back in the playbook's weights/ folder")
    # (320, 320) is a placeholder: the input size is set for each frame before it is read

    return cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), FACE_SCORE, NMS_THRESHOLD, TOP_K)


def find_faces_with_yunet(gray: numpy.ndarray) -> list[FaceBox]:
    """Face boxes as x0, y0, x1, y1 in the frame's own pixels, found on a copy DETECT_WIDTH wide."""

    model_path = Path(config.settings.frames.face_model)
    detector = _load_detector(model_path if model_path.is_absolute() else config.PLAYBOOK_ROOT / model_path)
    scale = min(1.0, DETECT_WIDTH / gray.shape[1])
    small = cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, found = detector.detect(small)
    faces = [] if found is None else [(round(x / scale), round(y / scale), round((x + w) / scale), round((y + h) / scale))
                                      for x, y, w, h in found[:, :4].tolist()]

    return faces


FACE_FINDER: FaceFinder = find_faces_with_yunet


def set_face_finder(finder: FaceFinder) -> None:
    global FACE_FINDER
    FACE_FINDER = finder


def detect_faces(gray: numpy.ndarray) -> list[FaceBox]:
    """The faces in a grey frame, as boxes in its own pixels, found by whichever finder is installed."""

    return FACE_FINDER(gray)
