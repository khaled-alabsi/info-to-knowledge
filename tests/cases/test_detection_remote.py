"""Faces and people are found by a model server when `providers.detection.url` is set: the boxes the program gets are the
ones the in-process finder a test installs (`set_face_finder`, `set_person_finder`) gives for the same people, a detection
below its kind's score or of the other kind is dropped, a person's box is widened as the in-process detector widens it, the
picture sent is no larger than 1,000 pixels on its longer side, and a server that fails stops the step by naming its URL.
With the URL empty nothing is sent."""

from __future__ import annotations

import base64
import json

import cv2
import numpy

from support import fake_model_server
from support import harness

import config
from models.endpoint_errors import EndpointError
from services import face_detection_service
from services import person_detection_service

DETECTIONS_PATH = "/v1/images/detections"
FRAME_SIZE = (1600, 900)                                      # width, height: shrunk to 1,000 wide before it is sent
FACES = [(160, 80, 400, 320), (800, 160, 960, 400)]           # multiples of 16, so the shrink to 5/8 loses no pixel
PERSON = (320, 160, 960, 800)


def _build_frame() -> numpy.ndarray:
    """Give me a grey frame with a gradient, so the PNG sent is not a single colour."""

    row = numpy.linspace(0, 255, FRAME_SIZE[0], dtype=numpy.uint8)

    return numpy.tile(row, (FRAME_SIZE[1], 1))


def _set_detection_url(url: str) -> object:
    kept = config.settings
    config.settings = config.Settings(**{"providers": {"detection": {"url": url, "model": "detector"}}})

    return kept


def _build_reply(box_list: list[tuple[int, int, int, int]],
                 kind: str,
                 score: float,
                 scale: float) -> bytes:
    """Give me the reply of a server that finds `box_list` on an image shrunk to `scale` of the frame, each of `kind`."""

    return json.dumps({"detections": [{"kind": kind, "box": [round(value * scale) for value in box], "score": score}
                                      for box in box_list]}).encode("utf-8")


def test_faces_from_the_server_are_the_ones_the_in_process_finder_gives():
    frame = _build_frame()
    face_detection_service.set_face_finder(lambda gray: list(FACES))
    try:
        own = face_detection_service.detect_faces(frame)
    finally:
        face_detection_service.set_face_finder(face_detection_service.find_faces)
    server = fake_model_server.FakeModelServer()
    server.set_reply(DETECTIONS_PATH, 200, _build_reply(box_list=FACES, kind="face", score=0.93, scale=0.625))
    with fake_model_server.serving(server):
        kept = _set_detection_url(server.build_url())
        try:
            remote = face_detection_service.detect_faces(frame)
        finally:
            config.settings = kept
    request_list = [request for request in server.request_list if request.path == DETECTIONS_PATH]
    body = request_list[0].read_json()
    sent = cv2.imdecode(numpy.frombuffer(base64.b64decode(body["image"].split(",", 1)[1]), numpy.uint8), cv2.IMREAD_UNCHANGED)
    harness.check("the server's faces are the in-process finder's, in the frame's own pixels", remote == own, f"{remote} {own}")
    harness.check("one request for the frame, asking for faces and naming the model",
                  len(request_list) == 1 and body["kinds"] == ["face"] and body["model"] == "detector", str(body["kinds"]))
    harness.check("the picture sent is the frame shrunk to 1,000 pixels wide", sent.shape == (562, 1000), str(sent.shape))


def test_a_detection_below_its_score_or_of_the_other_kind_is_dropped():
    server = fake_model_server.FakeModelServer()
    found = [{"kind": "face", "box": [10, 10, 50, 50], "score": 0.59},
             {"kind": "face", "box": [100, 100, 150, 150], "score": 0.60},
             {"kind": "person", "box": [0, 0, 200, 400], "score": 0.99}]
    server.set_reply(DETECTIONS_PATH, 200, json.dumps({"detections": found}).encode("utf-8"))
    small = numpy.zeros((300, 400), numpy.uint8)
    with fake_model_server.serving(server):
        kept = _set_detection_url(server.build_url())
        try:
            faces = face_detection_service.detect_faces(small)
        finally:
            config.settings = kept
    harness.check("only the face scoring 0.60, the program's face score, is kept; the person is not a face",
                  faces == [(100, 100, 150, 150)], str(faces))


def test_a_person_from_the_server_is_widened_as_the_in_process_detector_widens_one():
    frame = _build_frame()
    margin = person_detection_service.MARGIN
    box_width, box_height = PERSON[2] - PERSON[0], PERSON[3] - PERSON[1]
    widened = (max(0, round(PERSON[0] - margin * box_width)), max(0, round(PERSON[1] - margin * box_height)),
               min(FRAME_SIZE[0], round(PERSON[0] + (1 + margin) * box_width)),
               min(FRAME_SIZE[1], round(PERSON[1] + (1 + margin) * box_height)))
    person_detection_service.set_person_finder(lambda image: [widened])
    try:
        own = person_detection_service.detect_people(frame)
    finally:
        person_detection_service.set_person_finder(person_detection_service.find_people)
    edge = (1500, 800, 1600, 900)
    server = fake_model_server.FakeModelServer()
    server.set_reply(DETECTIONS_PATH, 200, _build_reply(box_list=[PERSON, edge], kind="person", score=0.8, scale=0.625))
    with fake_model_server.serving(server):
        kept = _set_detection_url(server.build_url())
        try:
            remote = person_detection_service.detect_people(frame)
        finally:
            config.settings = kept
    body = [request for request in server.request_list if request.path == DETECTIONS_PATH][0].read_json()
    harness.check("the person's box is the in-process finder's, widened by the margin on every side", remote[0] == own[0],
                  f"{remote[0]} {own[0]}")
    harness.check("a box at the frame's edge is cut at the edge, never past it", remote[1][2] == FRAME_SIZE[0] and remote[1][3] == FRAME_SIZE[1],
                  str(remote[1]))
    harness.check("the request asks for people", body["kinds"] == ["person"], str(body["kinds"]))


def test_a_server_that_fails_stops_the_step_naming_its_url():
    server = fake_model_server.FakeModelServer()
    server.set_reply(DETECTIONS_PATH, 500, b'{"error": {"message": "model not loaded"}}')
    refused = ""
    with fake_model_server.serving(server):
        kept = _set_detection_url(server.build_url())
        try:
            person_detection_service.detect_people(_build_frame())
        except EndpointError as failed:
            refused = str(failed)
        finally:
            config.settings = kept
    harness.check("the error names the URL called and the status, and no empty list stands in for it",
                  DETECTIONS_PATH in refused and "500" in refused, refused)


def test_an_empty_url_sends_nothing():
    server = fake_model_server.FakeModelServer()
    blank = numpy.zeros((300, 400), numpy.uint8)
    with fake_model_server.serving(server):
        kept = _set_detection_url("")
        try:
            faces = face_detection_service.detect_faces(blank)
        finally:
            config.settings = kept
    harness.check("the in-process detector runs and the server is not asked", faces == [] and not server.request_list,
                  f"{faces} {len(server.request_list)}")
