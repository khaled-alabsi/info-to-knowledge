"""Phase 2's knowledge filter decided by Liquid AI's decision model d1-2B (`frames.knowledgeFilterModel`
"liquidai-d1-2B"), in place of the frame classifier's people tag (`google-SigLIP-2`, the default).

d1-2B runs as its own tool (`tools/d1-knowledge-filter/`, its own environment), asked for every frame whether it holds
knowledge and which kind of empty frame it is, in one pass a frame. A frame whose knowledge probability is under
`frames.decisionFilterCut` (0.20) is tagged `people`, the tag the knowledge filter drops, with the class of empty frame
d1-2B gives most (`dropClass`); a frame the classifier tagged people that d1-2B finds knowledge on is tagged `other`
and kept. The classifier still runs: its text and board tags and its SigLIP vectors serve the other stages.
Measured once, offline: 57 of 73 empty frames dropped at 0.20, none holding knowledge.

With `providers.decision.url` set, a model server answers the same questions instead of the tool
(`POST /v1/classifications`): the frames go in batches, one request for each question and batch, and each frame's score
for a question is its probability. The program applies `decisionFilterCut` to the knowledge question's score as before.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

import config
from config import FrameSettings
from models import output_contract
from models.endpoint_spec import EndpointSpec
from services import endpoint_client
from services import knowledge_filter_service
from utils import json_store_utils

__all__ = ["CLASSIFIER_MODEL", "DECISION_MODEL", "FILTER_MODELS", "QUESTIONS", "BATCH_FRAMES", "read_chosen_model",
           "write_chosen_model", "read_decisions", "apply_decisions"]

_LOG = logging.getLogger(__name__)

# The knowledge filter's models, by their publisher and full name (the owner: "use full name like google-SigLIP-2")
CLASSIFIER_MODEL = "google-SigLIP-2"
DECISION_MODEL = "liquidai-d1-2B"
FILTER_MODELS = (CLASSIFIER_MODEL, DECISION_MODEL)
# The tool's script, beside its environment's folder
_SCRIPT_NAME = "filter_frames.py"
# The tag a frame keeps when d1-2B finds knowledge on a frame the classifier tagged people
_KEPT_TAG = "other"
# The questions a server is asked, the same as the tool's (`tools/d1-knowledge-filter/filter_frames.py`, its POC's
# question having an AUC of 0.94 to 0.998 over three labelled runs): "knowledge" gives `pKnowledge`, each other one a class
# of empty frame
QUESTIONS = {
    "knowledge": "Does this screen show information a reader of the meeting's notes would need: text, numbers, a table, "
                 "a diagram, code or a page with content? A screen of people's cameras, an empty desktop, a blank or "
                 "loading window is not.",
    "people": "Does this screen show only people: one camera, a gallery of cameras, or a room, with no shared content?",
    "desktop": "Does this screen show only an empty desktop, a wallpaper or a file manager with no document open?",
    "blank": "Is this screen blank, a single colour, or a window still loading with nothing shown yet?",
    "joining": "Is this screen a call's join, waiting or lobby screen, or its controls with no shared content?",
}
BATCH_FRAMES = 8                                  # frames sent in one request
_FRAME_WIDTH = 640                                # the width a frame is asked at, as the tool asks it
_FRAME_JPEG_QUALITY = 90
_SCORE_DIGITS = 4                                 # a probability is kept to this many digits, as the tool keeps it


def read_chosen_model(folder: Path) -> str:
    """The knowledge filter's model of the run in `folder`: the one written for it, else `frames.knowledgeFilterModel`;
    a damaged or unknown choice reads as the settings' model, and says so."""

    path = folder / output_contract.RUN_KNOWLEDGE_FILTER_FILE
    if not path.is_file():
        return config.settings.frames.knowledge_filter_model
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _LOG.warning("%s/%s could not be read, the knowledge filter runs frames.knowledgeFilterModel: %s",
                     folder.name, path.name, error)
        return config.settings.frames.knowledge_filter_model
    chosen = recorded.get("model") if isinstance(recorded, dict) else None
    if chosen not in FILTER_MODELS:
        _LOG.warning("%s/%s names no model of %s, the knowledge filter runs frames.knowledgeFilterModel",
                     folder.name, path.name, ", ".join(FILTER_MODELS))
        return config.settings.frames.knowledge_filter_model

    return chosen


def write_chosen_model(folder: Path,
                       model: str) -> None:
    """The run folder's knowledge filter model set to `model`; a model not in the list is refused by name."""

    if model not in FILTER_MODELS:
        raise ValueError(f"no knowledge filter model {model!r}: the models are " + ", ".join(FILTER_MODELS))
    json_store_utils.write_json(folder / output_contract.RUN_KNOWLEDGE_FILTER_FILE, {"model": model})


def _build_frame_data_url(frame_path: Path) -> str:
    """Give me the frame as a JPEG data URL, shrunk to `_FRAME_WIDTH` wide when it is wider."""

    image = Image.open(frame_path).convert("RGB")
    if image.width > _FRAME_WIDTH:
        image = image.resize((_FRAME_WIDTH, round(image.height * _FRAME_WIDTH / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_FRAME_JPEG_QUALITY)

    return endpoint_client.build_data_url(content=buffer.getvalue(), media_type="image/jpeg")


def _read_decisions_from_server(endpoint: EndpointSpec,
                                frames_dir: Path) -> dict[str, dict]:
    """Every `frame-*.jpg` of `frames_dir` by file name -> `{"frame", "pKnowledge", "classes"}`, as the tool gives them, from
    the model server: each batch of `BATCH_FRAMES` frames asked every question in `QUESTIONS`, the reply's `score` the
    probability; the reply's `decision` word is not read."""

    frame_path_list = sorted(frames_dir.glob("frame-*.jpg"))
    if not frame_path_list:
        raise RuntimeError(f"no frame-*.jpg in {frames_dir}")
    decisions = {}
    for start in range(0, len(frame_path_list), BATCH_FRAMES):
        batch = frame_path_list[start:start + BATCH_FRAMES]
        data_url_list = [_build_frame_data_url(frame_path=frame_path) for frame_path in batch]
        score_by_question = {}
        for name, question in QUESTIONS.items():
            result_list = endpoint_client.request_decisions(endpoint=endpoint, image_url_list=data_url_list, question=question)
            score_by_question[name] = [round(float(result["score"]), _SCORE_DIGITS) for result in result_list]
        for index, frame_path in enumerate(batch):
            decisions[frame_path.name] = {"frame": frame_path.name, "pKnowledge": score_by_question["knowledge"][index],
                                          "classes": {name: scores[index] for name, scores in score_by_question.items()
                                                      if name != "knowledge"}}

    return decisions


def read_decisions(frames_dir: Path,
                   settings: FrameSettings) -> dict[str, dict]:
    """Every image of `frames_dir` by file name -> d1-2B's `{"frame", "pKnowledge", "classes"}`; a missing tool or a
    failed run stops phase 2 by name. With `providers.decision.url` set, the model server answers and the tool is not
    needed."""

    endpoint = endpoint_client.find_endpoint(kind="decision")
    if endpoint is not None:
        return _read_decisions_from_server(endpoint=endpoint, frames_dir=frames_dir)
    python = knowledge_filter_service.resolve_playbook_path(settings.decision_filter_python)
    script = python.parent.parent.parent / _SCRIPT_NAME
    if not python.is_file() or not script.is_file():
        raise FileNotFoundError(f"frames.decisionFilterPython points at {python}, which does not exist: run "
                                f"{script.parent / 'setup.sh'} --download, or set frames.knowledgeFilterModel to "
                                f"{CLASSIFIER_MODEL}")
    with tempfile.TemporaryDirectory() as scratch:
        out_path = Path(scratch) / "decisions.json"
        finished = subprocess.run([str(python), str(script), str(frames_dir), "--out", str(out_path)],
                                  capture_output=True, text=True)
        if finished.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"the d1-2B knowledge filter ({script}) failed with exit code {finished.returncode}: "
                               f"{finished.stderr.strip()[-2000:]}")

        return {row["frame"]: row for row in json.loads(out_path.read_text(encoding="utf-8"))["frames"]}


def apply_decisions(tags: dict[str, dict],
                    decisions: dict[str, dict],
                    cut: float) -> dict[str, dict]:
    """The classifier's `tags` with the knowledge filter's decision d1-2B's: a frame under `cut` tagged people, with
    `pKnowledge` and the `dropClass` of empty frame it scored highest; a frame the classifier tagged people and d1-2B
    keeps tagged other. A frame d1-2B returned nothing for stops phase 2 by name."""

    decided = {}
    for name, row in tags.items():
        decision = decisions.get(name)
        if decision is None:
            raise RuntimeError(f"the d1-2B knowledge filter returned no decision for {name}")
        p_knowledge = float(decision["pKnowledge"])
        classes = dict(decision.get("classes") or {})
        if p_knowledge < cut:
            drop_class = max(classes, key=classes.get) if classes else ""
            decided[name] = {**row, "tag": knowledge_filter_service.PEOPLE, "pKnowledge": p_knowledge,
                             "dropClass": drop_class}
            continue
        tag = _KEPT_TAG if row["tag"] == knowledge_filter_service.PEOPLE else row["tag"]
        decided[name] = {**row, "tag": tag, "pKnowledge": p_knowledge}

    return decided
