"""What asking a model server for faces and people costs for one frame, measured against the stand-in server: the requests one
frame makes, the bytes each carries, and the time of one request beside the time of the in-process detector on the same
frame. The server answers at once and loads no model, so the time is what the program adds to a real server's own time:
the picture's encoding and the round trip on this machine. Nothing waits on the clock; the times are printed for the plan's
decision (D3) and never checked, since a check on a time fails on a busy machine."""

from __future__ import annotations

import time

import cv2
import numpy

from support import fake_model_server
from support import harness

import config
from services import detection_request_service
from services import face_detection_service
from services import person_detection_service

FRAME_SIZE = (1280, 720)
TIMED_FRAMES = 20                                             # frames timed one after another, the first one warming the detector
MAX_REQUEST_BYTES = 1_000_000                                 # a request this large or larger is a transfer the plan counts


def _build_frame() -> numpy.ndarray:
    """Give me a frame of a slide: a white page with 30 lines of text-sized black bars, in colour."""

    frame = numpy.full((FRAME_SIZE[1], FRAME_SIZE[0], 3), 255, numpy.uint8)
    for line in range(30):
        cv2.rectangle(frame, (60, 30 + line * 22), (60 + 300 + (line * 37) % 700, 44 + line * 22), (0, 0, 0), -1)

    return frame


def _measure_milliseconds(find, image: numpy.ndarray) -> float:
    started = time.perf_counter()
    for _ in range(TIMED_FRAMES):
        find(image)

    return (time.perf_counter() - started) * 1000 / TIMED_FRAMES


def test_a_frame_costs_one_request_of_under_a_megabyte_for_faces_and_for_people():
    frame = _build_frame()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        kept = config.settings
        config.settings = config.Settings(**{"providers": {"detection": {"url": server.build_url()}}})
        try:
            face_detection_service.detect_faces(gray)
            face_request = server.request_list[-1]
            person_detection_service.detect_people(frame)
            person_request = server.request_list[-1]
            remote_face_ms = _measure_milliseconds(find=face_detection_service.detect_faces, image=gray)
            remote_person_ms = _measure_milliseconds(find=person_detection_service.detect_people, image=frame)
        finally:
            config.settings = kept
    own_face_ms = _measure_milliseconds(find=face_detection_service.detect_faces, image=gray)
    own_person_ms = _measure_milliseconds(find=person_detection_service.detect_people, image=frame)
    print(f"  per frame {FRAME_SIZE[0]}x{FRAME_SIZE[1]}, picture sent at most {detection_request_service.SENT_MAX_SIDE} px: "
          f"faces {len(face_request.body):,} bytes, {remote_face_ms:.1f} ms to the stand-in server, {own_face_ms:.1f} ms in "
          f"process; people {len(person_request.body):,} bytes, {remote_person_ms:.1f} ms to the stand-in server, "
          f"{own_person_ms:.1f} ms in process")
    harness.check("one frame is one request for faces and one for people, so the capture loop's cost is the count of its calls",
                  len(server.request_list) == 2 + 2 * TIMED_FRAMES, str(len(server.request_list)))
    harness.check("each request is under a megabyte for a frame of 1,280 x 720",
                  len(face_request.body) < MAX_REQUEST_BYTES and len(person_request.body) < MAX_REQUEST_BYTES,
                  f"{len(face_request.body):,} {len(person_request.body):,}")
