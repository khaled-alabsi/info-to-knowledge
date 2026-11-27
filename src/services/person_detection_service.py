"""Finding the people a frame shows, with OpenCV's NanoDet-Plus object detector (Apache 2.0, from the OpenCV model zoo,
as the face detector is), its "person" class only. The side effect is loading its model file, once per path;
everything runs on this machine's processor, inside the program, with the 3.8 MB NanoDet file in `weights/` (which the
container image ships). No model server is asked for people.

A person is never content: phase 2's capture loop leaves the blocks a person covers out of its change, so a lecturer
walking in front of an unchanged blackboard takes no new frame (DEFECTS 100).

`PERSON_FINDER` is this service's seam: a test swaps in a finder that knows where its synthetic people are, through
`set_person_finder`, and restores it afterwards.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import numpy

import config
from models.video_errors import MissingPersonModel

__all__ = ["detect_people", "find_people_with_nanodet", "set_person_finder", "PERSON_FINDER", "MARGIN"]

INPUT_PX = 416                              # the model reads the frame scaled into this square, its rest left black
STRIDES = (8, 16, 32)                       # the model's three output levels, each a grid of this many pixels a cell
REG_MAX = 7                                 # each box side is a distribution over 0 to REG_MAX cells
MEAN = numpy.array([103.53, 116.28, 123.675], numpy.float32)
STD = numpy.array([57.375, 57.12, 58.395], numpy.float32)
PERSON_CLASS = 0                            # COCO's "person"
# A detection scoring below this is no person. On Lecture 7's room camera the lecturer scored 0.58 to 0.83, walking and
# writing with his back turned, a congress talk's speaker beside the slide 0.55 to 0.74, and a live demo's 3D map on a
# slide, which PP-HumanSeg read as people with the lecturer's certainty, found nothing (DEFECTS 111)
PERSON_SCORE = 0.35
OVERLAP_IOU = 0.6                           # two person boxes overlapping more than this are one person
# Each box is widened by this share of its width and its height on every side: the body's edge fell in blocks just
# outside the box, and a rendered lecturer's moves took two frames
MARGIN = 0.1


@lru_cache(maxsize=2)
def _load_model(model_path: Path) -> cv2.dnn.Net:
    if not model_path.is_file():
        raise MissingPersonModel(f"the person model {model_path} is missing: set `frames.personModel` to the "
                                 f"NanoDet .onnx file, or put it back in the playbook's weights/ folder")
    model = cv2.dnn.readNet(str(model_path))

    return model


def find_people_with_nanodet(frame: numpy.ndarray) -> list[tuple[int, int, int, int]]:
    """Person boxes as x0, y0, x1, y1 in the frame's own pixels."""

    # STEP 1: the frame scaled into the model's square, its top-left corner kept, normalized as the model was trained
    model_path = Path(config.settings.frames.person_model)
    model = _load_model(model_path if model_path.is_absolute() else config.PLAYBOOK_ROOT / model_path)
    height, width = frame.shape[:2]
    colour = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    scale = INPUT_PX / max(height, width)
    square = numpy.zeros((INPUT_PX, INPUT_PX, 3), numpy.float32)
    square[:round(height * scale), :round(width * scale)] = cv2.resize(
        colour, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    model.setInput(cv2.dnn.blobFromImage((square - MEAN) / STD))
    outputs = model.forward(model.getUnconnectedOutLayersNames())

    # STEP 2: each level's class scores, then its boxes: every cell scoring as a person gives a box from its centre
    boxes, scores = [], []
    for stride, classes, distances in zip(STRIDES, outputs[:len(STRIDES)], outputs[len(STRIDES):]):
        cells = INPUT_PX // stride
        rows, columns = numpy.mgrid[0:cells, 0:cells]
        centre_x, centre_y = columns.ravel() * stride + (stride - 1) / 2, rows.ravel() * stride + (stride - 1) / 2
        spread = numpy.exp(distances[0].reshape(-1, REG_MAX + 1))
        sides = ((spread / spread.sum(axis=1, keepdims=True)) @ numpy.arange(REG_MAX + 1)).reshape(-1, 4) * stride
        person = classes[0][:, PERSON_CLASS]
        for x, y, (left, top, right, bottom), score in zip(centre_x[person >= PERSON_SCORE],
                                                          centre_y[person >= PERSON_SCORE],
                                                          sides[person >= PERSON_SCORE], person[person >= PERSON_SCORE]):
            boxes.append([(x - left) / scale, (y - top) / scale, (left + right) / scale, (top + bottom) / scale])
            scores.append(float(score))
    if not boxes:
        return []

    # STEP 3: one box per person
    kept = numpy.ravel(cv2.dnn.NMSBoxes(boxes, scores, PERSON_SCORE, OVERLAP_IOU))
    people = []
    for x, y, box_width, box_height in (boxes[i] for i in kept):
        people.append(_build_widened_box(x=x, y=y, box_width=box_width, box_height=box_height, frame_width=width, frame_height=height))

    return people


def _build_widened_box(x: float,
                       y: float,
                       box_width: float,
                       box_height: float,
                       frame_width: int,
                       frame_height: int) -> tuple[int, int, int, int]:
    """Give me the box at `x`, `y` of the size `box_width` by `box_height`, widened by MARGIN on every side, as x0, y0, x1, y1
    cut at the frame's edges."""

    return (max(0, round(x - MARGIN * box_width)), max(0, round(y - MARGIN * box_height)),
            min(frame_width, round(x + (1 + MARGIN) * box_width)), min(frame_height, round(y + (1 + MARGIN) * box_height)))


PERSON_FINDER: Callable[[numpy.ndarray], list[tuple[int, int, int, int]]] = find_people_with_nanodet


def set_person_finder(finder: Callable[[numpy.ndarray], list[tuple[int, int, int, int]]]) -> None:
    global PERSON_FINDER
    PERSON_FINDER = finder


def detect_people(frame: numpy.ndarray) -> list[tuple[int, int, int, int]]:
    """The people in a frame, as boxes in its own pixels, found by whichever finder is installed."""

    people = PERSON_FINDER(frame)

    return people
