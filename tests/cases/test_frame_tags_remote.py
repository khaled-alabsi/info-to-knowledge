"""The frame classifier asks a model server for the frames' image vectors: with `providers.embeddings.host` naming a host, the tags and
the vectors file are the ones the classifier's own copy gives on the same vectors and the real `tags.npz`, the server is
asked once for each frame with the five images of it, and a server whose vectors have the wrong width is refused by the
frame's name. With the URL empty the classifier is run as before, with no vectors handed over."""

from __future__ import annotations

import base64
import io
import stat
import sys
import tempfile
from pathlib import Path

import numpy
from PIL import Image
from PIL import ImageDraw

from support import fake_model_server
from support import harness

import config
from models.endpoint_spec import EndpointSpec
from services import endpoint_client
from services import frame_embedding_service
from services import knowledge_filter_service

CLASSIFIER_DIR = config.PLAYBOOK_ROOT / "frame-classifier"
sys.path.insert(0, str(CLASSIFIER_DIR / "src"))

from frame_knowledge_classifier import config as classifier_config  # noqa: E402
from frame_knowledge_classifier.services import embedding_service  # noqa: E402
from frame_knowledge_classifier.services import filter_service  # noqa: E402

WEIGHTS_DIR = config.PLAYBOOK_ROOT / "weights" / "frame-classifier"
FRAME_SIZE = (640, 360)
ANSWERED_LIST: list[str] = []    # the images the stand-in image model answered, in order; each test that uses it clears it first
COMMAND_TEXT = """#!/bin/sh
PYTHONPATH="{src}" exec "{python}" "{run_filter}" "$@"
"""
RECORDING_COMMAND_TEXT = """#!/bin/sh
echo "$@" > "{record}"
exit 1
"""


def _build_frames(folder: Path) -> list[Path]:
    """Give me three frames of different looks: a white one, a dark blue one, and a white one with black bars of text."""

    white = Image.new("RGB", FRAME_SIZE, "white")
    blue = Image.new("RGB", FRAME_SIZE, (20, 30, 120))
    text = Image.new("RGB", FRAME_SIZE, "white")
    draw = ImageDraw.Draw(text)
    for line in range(6):
        draw.rectangle((40, 40 + line * 50, 40 + 300 + line * 40, 60 + line * 50), fill="black")
    path_list = []
    for number, image in enumerate((white, blue, text)):
        path = folder / f"frame-{number:03d}.jpg"
        image.save(path, quality=95)
        path_list.append(path)

    return path_list


def _calculate_vector(part: Image.Image,
                      index: int) -> list[float]:
    """Give me the 768 numbers a stand-in image model gives one image: they depend on the image's colour and its place among
    the five, so each frame and each quarter gets its own."""

    mean_colour = numpy.asarray(part.convert("RGB"), dtype=float).mean(axis=(0, 1))
    seed = int(mean_colour[0] // 64) * 100 + int(mean_colour[1] // 64) * 10 + int(mean_colour[2] // 64) + 1000 * index
    return numpy.random.default_rng(seed).normal(size=frame_embedding_service.IMAGE_EMBEDDING_SIZE).tolist()


def _read_sent_data_url(request: fake_model_server.RecordedRequest) -> str:
    """Give me the `data:` URL of the one image a request holds, in the `messages` shape vLLM documents."""

    content = request.read_json()["messages"][0]["content"]

    return content[0]["image_url"]["url"]


def _build_embeddings_reply(request: fake_model_server.RecordedRequest) -> dict:
    """Answer one image at a time, as the program asks: its place among the five is how many images were answered before."""

    data_url = _read_sent_data_url(request=request)
    image = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    index = len(ANSWERED_LIST) % frame_embedding_service.FRAME_PARTS
    ANSWERED_LIST.append(data_url)

    return {"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": _calculate_vector(part=image, index=index)}]}


def _calculate_known_vectors(path_list: list[Path]) -> numpy.ndarray:
    """Give me the rows the classifier's own image model would give `path_list`, if it gave the stand-in's vectors."""

    row_list = []
    for path in path_list:
        image = Image.open(path).convert("RGB")
        width, height = image.size
        part_list = [image] + [image.crop((column * width // 2, row * height // 2, (column + 1) * width // 2, (row + 1) * height // 2))
                               for row in range(2) for column in range(2)]
        vector_array = numpy.array([_calculate_vector(part=part, index=index) for index, part in enumerate(part_list)])
        row_list.append((vector_array / numpy.linalg.norm(vector_array, axis=-1, keepdims=True)).reshape(-1))

    return numpy.stack(row_list)


def _build_command(folder: Path) -> Path:
    command = folder / "classifier"
    command.write_text(COMMAND_TEXT.format(src=CLASSIFIER_DIR / "src", python=sys.executable,
                                           run_filter=CLASSIFIER_DIR / "run_filter.py"), encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)

    return command


def _read_tag_columns(entries: list[dict]) -> list[tuple]:
    return [(entry["frame"], entry["tag"], entry["peopleScore"], entry["boardScore"], entry["isPlainText"])
            for entry in entries]


def _read_with_url(url: str,
                   frames_dir: Path,
                   command: Path,
                   vectors_path: Path | None) -> dict:
    kept = config.settings
    host_dict = {fake_model_server.TEST_HOST: {"url": url}} if url else {}
    host_name = fake_model_server.TEST_HOST if url else ""
    config.settings = config.Settings(**{"hosts": host_dict,
                                         "providers": {"embeddings": {"host": host_name}},
                                         "frames": {"knowledgeClassifierWeights": str(WEIGHTS_DIR)}})
    try:
        return knowledge_filter_service.read_decisions(frames_dir=frames_dir, command=command,
                                                       task=knowledge_filter_service.TAGS_TASK, vectors_path=vectors_path)
    finally:
        config.settings = kept


def test_the_tags_and_the_vectors_file_are_the_classifiers_own_on_the_servers_vectors():
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    path_list = _build_frames(frames_dir)
    command = _build_command(folder)
    route = (("POST", "/v1/embeddings"))
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = _build_embeddings_reply
    ANSWERED_LIST.clear()
    server = fake_model_server.FakeModelServer()
    vectors_path = folder / "frame-vectors.npz"
    try:
        with fake_model_server.serving(server):
            remote = _read_with_url(url=server.build_url(), frames_dir=frames_dir, command=command, vectors_path=vectors_path)
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route

    # the classifier's own copy, on the same vectors it would be given, with the real tags.npz
    known = _calculate_known_vectors(path_list)
    previous_dir, previous_embedder = classifier_config.CLASSIFIERS_DIR, embedding_service.EMBEDDER
    classifier_config.CLASSIFIERS_DIR = WEIGHTS_DIR
    embedding_service.set_embedder(lambda paths: known[[path_list.index(path) for path in paths]])
    try:
        own_entries = filter_service.tag_folder(frames_dir, "tags", folder / "own.json", folder / "own-vectors.npz")
    finally:
        classifier_config.CLASSIFIERS_DIR = previous_dir
        embedding_service.set_embedder(previous_embedder)

    harness.check("each frame gets the same tag, scores and plain-text mark as the classifier's own copy gives",
                  _read_tag_columns(remote["frames"]) == _read_tag_columns(own_entries), str(remote["frames"]))
    sent = numpy.load(vectors_path)
    own = numpy.load(folder / "own-vectors.npz")
    harness.check("the vectors file names the same frames", sent["frames"].tolist() == own["frames"].tolist(),
                  str(sent["frames"]))
    harness.check("the vectors file holds the same numbers, 3,840 for each frame",
                  sent["vectors"].shape == (3, 3840) and numpy.allclose(sent["vectors"], own["vectors"], atol=1e-9),
                  str(sent["vectors"].shape))


def test_the_server_is_asked_once_for_each_image_five_for_each_frame():
    folder = Path(tempfile.mkdtemp())
    path_list = _build_frames(folder)
    server = fake_model_server.FakeModelServer()
    route = ("POST", "/v1/embeddings")
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = _build_embeddings_reply
    ANSWERED_LIST.clear()
    try:
        with fake_model_server.serving(server):
            kept = config.settings
            config.settings = config.Settings(**{"hosts": {fake_model_server.TEST_HOST: {"url": server.build_url()}},
                                                 "providers": {"embeddings": {"host": fake_model_server.TEST_HOST,
                                                                              "model": "siglip2"}}})
            try:
                frame_embedding_service.request_frame_vectors(endpoint=endpoint_client.find_endpoint(kind="embeddings"),
                                                              frame_path_list=path_list)
            finally:
                config.settings = kept
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route
    request_list = [request for request in server.request_list if request.path == "/v1/embeddings"]
    size_list = []
    for request in request_list[:5]:
        size_list.append(Image.open(io.BytesIO(base64.b64decode(_read_sent_data_url(request=request).split(",", 1)[1]))).size)
    harness.check("fifteen requests, five for each of the three frames", len(request_list) == 15, str(len(request_list)))
    harness.check("each request holds one image, and a frame's five are the whole frame, then its four quarters",
                  all(len(request.read_json()["messages"][0]["content"]) == 1 for request in request_list)
                  and size_list == [FRAME_SIZE] + [(FRAME_SIZE[0] // 2, FRAME_SIZE[1] // 2)] * 4, str(size_list))
    harness.check("each request names the model the settings give", request_list[0].read_json()["model"] == "siglip2")


def test_vectors_of_the_wrong_width_are_refused_by_the_frames_name():
    folder = Path(tempfile.mkdtemp())
    path_list = _build_frames(folder)
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        endpoint = EndpointSpec(base_url=server.build_url(), api_key_env="", timeout_s=10)
        try:
            frame_embedding_service.request_frame_vectors(endpoint=endpoint, frame_path_list=path_list)
            refused = ""
        except ValueError as failed:
            refused = str(failed)
    harness.check("the stand-in server's 3 numbers for each image are refused, naming the first frame and the shape",
                  "frame-000.jpg" in refused and "(5, 3)" in refused, refused)


def test_an_empty_url_runs_the_classifier_with_no_vectors_handed_over():
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    _build_frames(frames_dir)
    record = folder / "arguments.txt"
    command = folder / "classifier"
    command.write_text(RECORDING_COMMAND_TEXT.format(record=record), encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    try:
        _read_with_url(url="", frames_dir=frames_dir, command=command, vectors_path=None)
    except RuntimeError:
        pass    # the stand-in command exits 1 on purpose: only the arguments it was started with matter here
    arguments = record.read_text(encoding="utf-8")
    harness.check("the classifier is started with no `--vectors-in`", "--vectors-in" not in arguments
                  and arguments.startswith("filter "), arguments)
