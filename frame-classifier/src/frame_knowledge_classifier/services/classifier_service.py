"""A task's classifier: a weighted sum of a frame's 3,840 numbers, squeezed into a score from 0 to 1.

Training (logistic regression by gradient descent): every weight starts at 0; each step scores every labelled frame,
compares each score with its label (1 for the task's skip label, drop or text-only; 0 for its model label, keep or
not-text-only), and moves every weight a little in the direction that brings the scores closer to the labels, while
keeping the weights small. The threshold is set just above the highest score any frame labelled for the model got,
plus a safety margin, so a frame is taken from the model only when it scores higher than every frame the model must
read did.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.config import TrainingSettings
from frame_knowledge_classifier.models.classifier_record import ClassifierRecord
from frame_knowledge_classifier.models.task_record import TaskRecord


def _add_offset_column(embeddings: numpy.ndarray) -> numpy.ndarray:
    """Each row with a constant 1 appended, so the last weight acts as the offset."""

    return numpy.hstack([embeddings, numpy.ones((len(embeddings), 1))])


def train_weights(embeddings: numpy.ndarray,
                  should_skip: numpy.ndarray,
                  settings: TrainingSettings) -> numpy.ndarray:
    """The 3,841 weights learned from `embeddings` (one row per frame) and `should_skip` (True where labelled with
    the task's skip label)."""

    features = _add_offset_column(embeddings)
    weights = numpy.zeros(features.shape[1])
    target = should_skip.astype(float)
    for _ in range(settings.steps):
        scores = 1 / (1 + numpy.exp(-features @ weights))
        error_share = features.T @ (scores - target) / len(target)
        weights -= settings.learning_rate * (error_share + settings.regularisation * weights)

    return weights


def score_frames(embeddings: numpy.ndarray,
                 weights: numpy.ndarray) -> numpy.ndarray:
    """Each frame's score, 0 to 1: higher is further on the skip side."""

    return 1 / (1 + numpy.exp(-_add_offset_column(embeddings) @ weights))


def find_threshold(scores: numpy.ndarray,
                   should_skip: numpy.ndarray,
                   settings: TrainingSettings) -> float:
    """Just above the highest score a frame labelled for the model got, plus the safety margin."""

    model_scores = scores[~should_skip]
    if not len(model_scores):
        raise SystemExit("no frame labelled for the model among the training frames: the threshold needs at least one")

    return float(model_scores.max()) + settings.safety_margin


def train_classifier(embeddings: numpy.ndarray,
                     should_skip: numpy.ndarray,
                     settings: TrainingSettings,
                     task: TaskRecord,
                     trained_on: tuple[str, ...]) -> ClassifierRecord:
    weights = train_weights(embeddings, should_skip, settings)
    threshold = find_threshold(score_frames(embeddings, weights), should_skip, settings)

    return ClassifierRecord(weights=weights, threshold=threshold, task=task.name, trained_on=trained_on,
                            frame_count=len(should_skip), skip_count=int(should_skip.sum()), settings=asdict(settings),
                            embedding_model=f"{config.EMBEDDING_ARCHITECTURE}/{config.EMBEDDING_WEIGHTS}")


def save_classifier(classifier: ClassifierRecord,
                    name: str) -> None:
    """`classifiers/<name>.npz` (the weights and the threshold) and `classifiers/<name>.json` (its task, what it was
    trained on and how)."""

    config.CLASSIFIERS_DIR.mkdir(parents=True, exist_ok=True)
    numpy.savez(config.CLASSIFIERS_DIR / f"{name}.npz", weights=classifier.weights, threshold=classifier.threshold)
    description = {"task": classifier.task, "threshold": classifier.threshold, "trainedOn": list(classifier.trained_on),
                   "frameCount": classifier.frame_count, "skipCount": classifier.skip_count,
                   "settings": classifier.settings, "embeddingModel": classifier.embedding_model}
    (config.CLASSIFIERS_DIR / f"{name}.json").write_text(json.dumps(description, indent=2) + "\n")


def load_classifier(name: str) -> ClassifierRecord:
    weights_path = config.CLASSIFIERS_DIR / f"{name}.npz"
    if not weights_path.exists():
        raise SystemExit(f"no classifier named {name} in {config.CLASSIFIERS_DIR}; `classifier train` makes one")
    stored = numpy.load(weights_path)
    description = json.loads((config.CLASSIFIERS_DIR / f"{name}.json").read_text())

    return ClassifierRecord(weights=stored["weights"], threshold=float(stored["threshold"]), task=description["task"],
                            trained_on=tuple(description["trainedOn"]), frame_count=description["frameCount"],
                            skip_count=description["skipCount"], settings=description["settings"],
                            embedding_model=description["embeddingModel"])
