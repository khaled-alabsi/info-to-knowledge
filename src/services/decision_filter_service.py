"""Phase 2's knowledge filter decided by Liquid AI's decision model d1-2B (`frames.knowledgeFilterModel`
"liquidai-d1-2B"), in place of the frame classifier's people tag (`google-SigLIP-2`, the default).

d1-2B runs as its own tool (`tools/d1-knowledge-filter/`, its own environment), asked for every frame whether it holds
knowledge and which kind of empty frame it is, in one pass a frame. A frame whose knowledge probability is under
`frames.decisionFilterCut` (0.20) is tagged `people`, the tag the knowledge filter drops, with the class of empty frame
d1-2B gives most (`dropClass`); a frame the classifier tagged people that d1-2B finds knowledge on is tagged `other`
and kept. The classifier still runs: its text and board tags and its SigLIP vectors serve the other stages.
Measured once, offline: 57 of 73 empty frames dropped at 0.20, none holding knowledge.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

import config
from config import FrameSettings
from models import output_contract
from services import knowledge_filter_service
from utils import json_store_utils

__all__ = ["CLASSIFIER_MODEL", "DECISION_MODEL", "FILTER_MODELS", "read_chosen_model", "write_chosen_model",
           "read_decisions", "apply_decisions"]

_LOG = logging.getLogger(__name__)

# The knowledge filter's models, by their publisher and full name (the owner: "use full name like google-SigLIP-2")
CLASSIFIER_MODEL = "google-SigLIP-2"
DECISION_MODEL = "liquidai-d1-2B"
FILTER_MODELS = (CLASSIFIER_MODEL, DECISION_MODEL)
# The tool's script, beside its environment's folder
_SCRIPT_NAME = "filter_frames.py"
# The tag a frame keeps when d1-2B finds knowledge on a frame the classifier tagged people
_KEPT_TAG = "other"


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


def read_decisions(frames_dir: Path,
                   settings: FrameSettings) -> dict[str, dict]:
    """Every image of `frames_dir` by file name -> d1-2B's `{"frame", "pKnowledge", "classes"}`; a missing tool or a
    failed run stops phase 2 by name."""

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
