"""Phase 2's knowledge filter decided by a model server when `providers.decision.host` names a host: the decisions the program reads
are the ones the tool gives for the same frames, the frames go to the server in batches of 8 with every question of the
tool, a frame the server scores under the cut is dropped naming the kind of empty frame it scored highest, and a folder
with no frame is refused by name. With no host named the tool runs and the server is not asked."""

from __future__ import annotations

import base64
import io
import stat
import sys
import tempfile
from pathlib import Path

import numpy
from PIL import Image

from support import fake_model_server
from support import harness

import config
from services import decision_filter_service
from services import knowledge_filter_service

CLASSIFICATIONS_PATH = "/v1/classifications"
FRAME_COUNT = 10                                              # one batch of 8 and one of 2
FRAME_SIZE = (1280, 720)                                      # shrunk to 640 wide before it is sent
QUESTION_NAMES = list(decision_filter_service.QUESTIONS)
TOOL_TEXT = """import json, sys
from pathlib import Path
names = {names}
rows = []
for path in sorted(Path(sys.argv[1]).glob("frame-*.jpg")):
    number = int(path.stem.split("-")[1])
    classes = {{name: round((number + position) / 20, 4) for position, name in enumerate(names, start=0) if name != "knowledge"}}
    rows.append({{"frame": path.name, "pKnowledge": round(number / 10, 4), "classes": classes}})
Path(sys.argv[sys.argv.index("--out") + 1]).write_text(json.dumps({{"frames": rows}}))
"""


def _build_frames(folder: Path) -> list[Path]:
    """Give me ten flat grey frames, the grey of frame N being 25 * N + 10, so a server tells N from its picture."""

    path_list = []
    for number in range(FRAME_COUNT):
        path = folder / f"frame-{number:03d}.jpg"
        Image.new("RGB", FRAME_SIZE, (25 * number + 10,) * 3).save(path, quality=95)
        path_list.append(path)

    return path_list


def _build_classifications_reply(request: fake_model_server.RecordedRequest) -> dict:
    """Give me the reply of a server that scores frame N for the question at position P of the tool's questions: N / 10 for
    the knowledge question, (N + P) / 20 for each other one."""

    body = request.read_json()
    position = QUESTION_NAMES.index(next(name for name, text in decision_filter_service.QUESTIONS.items()
                                         if text == body["question"]))
    result_list = []
    for data_url in body["images"]:
        grey = numpy.asarray(Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("L"), dtype=float).mean()
        number = int(grey // 25)
        score = number / 10 if position == 0 else (number + position) / 20
        result_list.append({"decision": "keep" if score >= 0.2 else "drop", "score": score})

    return {"results": result_list}


def _build_tool(folder: Path) -> None:
    """Make the tool's folder the program expects, `<tool>/.venv/bin/python` and `<tool>/filter_frames.py`, with a stand-in
    that gives the scores `_build_classifications_reply` gives."""

    python = folder / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    (folder / "filter_frames.py").write_text(TOOL_TEXT.format(names=QUESTION_NAMES), encoding="utf-8")


def _read_decisions(url: str,
                    frames_dir: Path,
                    tool_dir: Path) -> dict[str, dict]:
    """`url` "" leaves the decision model to the tool."""

    kept = config.settings
    host_dict = {fake_model_server.TEST_HOST: {"url": url}} if url else {}
    host_name = fake_model_server.TEST_HOST if url else ""
    config.settings = config.Settings(**{"hosts": host_dict,
                                         "providers": {"decision": {"host": host_name, "model": "d1"}},
                                         "frames": {"decisionFilterPython": str(tool_dir / ".venv" / "bin" / "python")}})
    try:
        return decision_filter_service.read_decisions(frames_dir=frames_dir, settings=config.settings.frames)
    finally:
        config.settings = kept


def test_the_decisions_are_the_ones_the_tool_gives_for_the_same_frames():
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    _build_frames(frames_dir)
    tool_dir = folder / "tool"
    _build_tool(tool_dir)
    route = ("POST", CLASSIFICATIONS_PATH)
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = _build_classifications_reply
    server = fake_model_server.FakeModelServer()
    try:
        with fake_model_server.serving(server):
            own = _read_decisions(url="", frames_dir=frames_dir, tool_dir=tool_dir)
            asked_by_tool = len(server.request_list)
            remote = _read_decisions(url=server.build_url(), frames_dir=frames_dir, tool_dir=tool_dir)
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route
    request_list = [request for request in server.request_list if request.path == CLASSIFICATIONS_PATH]
    body_list = [request.read_json() for request in request_list]
    sent = Image.open(io.BytesIO(base64.b64decode(body_list[0]["images"][0].split(",", 1)[1]))).size
    harness.check("the tool's own answers cover all ten frames", len(own) == FRAME_COUNT, str(len(own)))
    harness.check("with the URL empty the server is not asked", asked_by_tool == 0, str(asked_by_tool))
    harness.check("the server's decisions are the tool's: the same frames, knowledge scores and classes", remote == own,
                  f"{remote['frame-003.jpg']} {own['frame-003.jpg']}")
    harness.check("10 requests: 2 batches, each asked the 5 questions of the tool in turn", len(request_list) == 10
                  and [body["question"] for body in body_list[:5]] == list(decision_filter_service.QUESTIONS.values()),
                  str(len(request_list)))
    harness.check("a batch holds 8 frames, then the 2 left", [len(body["images"]) for body in body_list[::5]] == [8, 2],
                  str([len(body["images"]) for body in body_list]))
    harness.check("a frame is sent 640 pixels wide and the request names the model", sent == (640, 360)
                  and body_list[0]["model"] == "d1", f"{sent} {body_list[0].get('model')}")


def test_a_frame_under_the_cut_is_dropped_naming_the_kind_of_empty_frame_it_scored_highest():
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    _build_frames(frames_dir)
    route = ("POST", CLASSIFICATIONS_PATH)
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = _build_classifications_reply
    server = fake_model_server.FakeModelServer()
    try:
        with fake_model_server.serving(server):
            decisions = _read_decisions(url=server.build_url(), frames_dir=frames_dir, tool_dir=folder / "no-tool")
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route
    tags = {name: {"frame": name, "tag": "text", "peopleScore": 0.1, "boardScore": 0.0} for name in decisions}
    decided = decision_filter_service.apply_decisions(tags=tags, decisions=decisions, cut=0.20)
    harness.check("frame 1 scores 0.1 for knowledge: dropped as people, and the class it scored highest is joining",
                  decided["frame-001.jpg"]["tag"] == knowledge_filter_service.PEOPLE
                  and decided["frame-001.jpg"]["dropClass"] == "joining", str(decided["frame-001.jpg"]))
    harness.check("frame 2 scores 0.2, the cut, and is kept with its tag", decided["frame-002.jpg"]["tag"] == "text",
                  str(decided["frame-002.jpg"]))


def test_a_folder_with_no_frame_is_refused_by_name():
    folder = Path(tempfile.mkdtemp())
    server = fake_model_server.FakeModelServer()
    refused = ""
    with fake_model_server.serving(server):
        try:
            _read_decisions(url=server.build_url(), frames_dir=folder, tool_dir=folder / "no-tool")
        except RuntimeError as failed:
            refused = str(failed)
    harness.check("the error names the folder and the server is not asked", str(folder) in refused and not server.request_list,
                  refused)
