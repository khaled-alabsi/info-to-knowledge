"""Judges every frame of a folder with a saved classifier: its task's decision, and the score behind it.

The folder is any folder of frame images, such as an `info-to-knowledge` run's `frames/`. The decisions are written as
JSON: the classifier's name, task and threshold (or the ink rule's cuts), then one entry per frame with its file name,
its score under the task's name for it (`dropScore` for the knowledge task, `textOnlyScore` for the text-only task),
its decision (one of the task's two labels) and the milliseconds it took. A tags classifier (`method: tags`) gives
each frame `tag` (people, text or other), `peopleScore` (0 to 1, people at or above `peopleThreshold`) and
`isPlainText` (whether it passes the ink rule's cuts) instead.
"""

from __future__ import annotations

import functools
import json
import time
from pathlib import Path

import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.decision_record import FrameDecision
from frame_knowledge_classifier.models.task_record import TAGS_TASK_NAME, TASKS
from frame_knowledge_classifier.services import (classifier_service, dataset_service, embedding_service, ink_rule_service,
                                                 ink_service, tags_service)


def read_method(classifier_name: str) -> str | None:
    """The saved classifier's method: `ink-rule` (a .json with cuts), `tags` (the tags task's one model) or None for
    SigLIP 2's weights alone."""

    description_path = config.CLASSIFIERS_DIR / f"{classifier_name}.json"

    return json.loads(description_path.read_text()).get("method") if description_path.exists() else None


def _list_frames(folder: Path) -> list[Path]:
    frames = sorted(path for path in folder.iterdir() if path.suffix.lower() in dataset_service.FRAME_SUFFIXES)
    if not frames:
        raise SystemExit(f"{folder} holds no frame image ({', '.join(dataset_service.FRAME_SUFFIXES)})")

    return frames


def tag_folder(folder: Path,
               classifier_name: str,
               out_path: Path,
               vectors_path: Path | None = None) -> list[dict]:
    """Every frame of `folder` tagged by the saved tags classifier, written to `out_path`; returns the frames' entries.
    With `vectors_path`, each frame's SigLIP vectors (the whole frame and its quarters) are saved there too, as
    `frames` and `vectors` in a .npz, for info-to-knowledge's similarity recheck, which then loads no model of its own."""

    classifier = tags_service.load_tags_classifier(classifier_name)
    entries, rows = [], []
    for frame in _list_frames(folder):
        started = time.perf_counter()
        vectors = embedding_service.embed_folder([frame])
        rows.append(vectors[0])
        tag, people_score, is_plain_text, board_score = tags_service.decide_tags(classifier, vectors,
                                                                                 ink_service.measure_frames([frame]))[0]
        entries.append({"frame": frame.name, "tag": tag, "peopleScore": round(people_score, 4),
                        "boardScore": round(board_score, 4),
                        "isPlainText": is_plain_text,
                        "milliseconds": round((time.perf_counter() - started) * 1000, 1)})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"classifier": classifier_name, "task": TAGS_TASK_NAME,
                                    "peopleThreshold": classifier.people_threshold,
                                    "boardThreshold": classifier.board_threshold, "cuts": classifier.cuts,
                                    "folder": str(folder), "frames": entries}, indent=2) + "\n")
    if vectors_path is not None:
        vectors_path.parent.mkdir(parents=True, exist_ok=True)
        numpy.savez(vectors_path, frames=numpy.array([entry["frame"] for entry in entries]),
                    vectors=numpy.stack(rows) if rows else numpy.zeros((0, 0)))

    return entries


def _score_with_cuts(frame: Path,
                     cuts: dict) -> float:
    return float(ink_rule_service.score_frames(ink_service.measure_frames([frame]), cuts)[0])


def _score_with_weights(frame: Path,
                        weights: numpy.ndarray) -> float:
    return float(classifier_service.score_frames(embedding_service.embed_folder([frame]), weights)[0])


def filter_folder(folder: Path,
                  classifier_name: str,
                  out_path: Path) -> list[FrameDecision]:
    # STEP 1: the classifier, and how a frame is scored with it: 1 or 0 for the ink rule, 0 to 1 for SigLIP 2's
    if read_method(classifier_name) == "ink-rule":
        cuts = ink_rule_service.load_cuts(classifier_name)
        task, threshold, settings = TASKS[cuts.task], 1.0, {"cuts": cuts.cuts}
        score_frame = functools.partial(_score_with_cuts, cuts=cuts.cuts)
    else:
        classifier = classifier_service.load_classifier(classifier_name)
        task, threshold, settings = TASKS[classifier.task], classifier.threshold, {"threshold": classifier.threshold}
        score_frame = functools.partial(_score_with_weights, weights=classifier.weights)

    # STEP 2: every frame's score and decision
    decisions = []
    for frame in _list_frames(folder):
        started = time.perf_counter()
        score = score_frame(frame)
        decisions.append(FrameDecision(key=frame.name, score=round(score, 4),
                                       decision=task.skip_label if score >= threshold else task.model_label,
                                       milliseconds=round((time.perf_counter() - started) * 1000, 1)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"classifier": classifier_name, "task": task.name, **settings,
                                    "folder": str(folder),
                                    "frames": [{"frame": d.key, task.score_field: d.score, "decision": d.decision,
                                                "milliseconds": d.milliseconds} for d in decisions]}, indent=2) + "\n")

    return decisions
