"""The tags task: one model that gives every frame one of four tags, people, board, text or other.

People is asked first: a logistic regression over SigLIP 2's five views and the frame's ink measures (each on a log
scale, then centred and scaled to a tenth, so the 3,840 image numbers still lead), trained people against the rest,
with its threshold just above every training frame tagged text or other plus the safety margin, so a frame with
knowledge is not dropped. A frame that is not people is board (a task board or a ticket tracker's view) when the board
head, a second logistic regression over the same features trained board against every other frame that is not people,
scores it at or above a threshold just above every such training frame not tagged board plus the margin, so no slide,
table or chat that only names ticket keys is handed on as a board. A frame that is neither is text when it passes the ink rule's cuts, chosen on the
training frames that are not people so no frame tagged other passes even with every share cut twice as loose
(`ink_rule_service`); otherwise it is other. The image alone never found a text frame on the fair test (a red word or
a thin arrow looks like text to it), so text is the ink rule's alone. `research/notes.md`, "variant D".
"""

from __future__ import annotations

import json
from dataclasses import asdict

import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.config import TrainingSettings
from frame_knowledge_classifier.models.label_vocabulary import BOARD, OTHER, PEOPLE, TAGS, TEXT
from frame_knowledge_classifier.models.tags_classifier_record import TagsClassifierRecord
from frame_knowledge_classifier.services import classifier_service, ink_rule_service, ink_service

# The ink measures' scale beside the image numbers: a tenth of one standard deviation per unit
INK_WEIGHT = 0.1


def _scale_ink(measures: list[dict]) -> numpy.ndarray:
    """Each frame's ink measures on a log scale, so 0.0001 and 0.01 lie as far apart as 0.01 and 1; one row per
    frame, in `ink_service.MEASURES`' order."""

    return numpy.array([[numpy.log10(entry[name] + 1) if name == "words" else numpy.log10(entry[name] + 1e-5)
                         for name in ink_service.MEASURES] for entry in measures]).reshape(len(measures),
                                                                                           len(ink_service.MEASURES))


def build_features(embeddings: numpy.ndarray,
                   measures: list[dict],
                   ink_mean: numpy.ndarray,
                   ink_spread: numpy.ndarray) -> numpy.ndarray:
    """The people head's input: each frame's embedding, then its scaled ink measures."""

    return numpy.hstack([embeddings, (_scale_ink(measures) - ink_mean) / ink_spread * INK_WEIGHT])


def train_tags_classifier(embeddings: numpy.ndarray,
                          measures: list[dict],
                          tags: numpy.ndarray,
                          settings: TrainingSettings,
                          trained_on: tuple[str, ...]) -> TagsClassifierRecord:
    # STEP 1: the people head, people against every other tag, its threshold above every frame with knowledge
    scaled = _scale_ink(measures)
    ink_mean, ink_spread = scaled.mean(axis=0), scaled.std(axis=0) + 1e-9
    features = build_features(embeddings, measures, ink_mean, ink_spread)
    is_people = tags == PEOPLE
    weights = classifier_service.train_weights(features, is_people, settings)
    threshold = classifier_service.find_threshold(classifier_service.score_frames(features, weights), is_people, settings)

    # STEP 2: the board head, board against every other frame that is not people, its threshold above all of them
    has_knowledge = ~is_people
    is_board = tags[has_knowledge] == BOARD
    board_weights, board_threshold = None, 1.0
    if is_board.any() and (~is_board).any():
        board_weights = classifier_service.train_weights(features[has_knowledge], is_board, settings)
        board_threshold = classifier_service.find_threshold(
            classifier_service.score_frames(features[has_knowledge], board_weights), is_board, settings)

    # STEP 3: the ink rule's cuts, text against other, on the frames that are neither people nor board
    is_plain_kind = has_knowledge & (tags != BOARD)
    cuts = ink_rule_service.choose_cuts([entry for entry, keep in zip(measures, is_plain_kind) if keep],
                                        tags[is_plain_kind] == TEXT)

    return TagsClassifierRecord(people_weights=weights, people_threshold=threshold, board_weights=board_weights,
                                board_threshold=board_threshold, ink_mean=ink_mean,
                                ink_spread=ink_spread, cuts=cuts, trained_on=trained_on,
                                tag_counts={tag: int((tags == tag).sum()) for tag in TAGS}, settings=asdict(settings),
                                embedding_model=f"{config.EMBEDDING_ARCHITECTURE}/{config.EMBEDDING_WEIGHTS}")


def decide_tags(classifier: TagsClassifierRecord,
                embeddings: numpy.ndarray,
                measures: list[dict]) -> list[tuple[str, float, bool, float]]:
    """Each frame's tag, its people score (0 to 1), whether it passes the ink rule's cuts, and its board score (0 to 1,
    0 when the classifier has no board head)."""

    features = build_features(embeddings, measures, classifier.ink_mean, classifier.ink_spread)
    people_scores = classifier_service.score_frames(features, classifier.people_weights)
    board_scores = (classifier_service.score_frames(features, classifier.board_weights)
                    if classifier.board_weights is not None else numpy.zeros(len(measures)))
    decisions = []
    for people_score, board_score, entry in zip(people_scores, board_scores, measures):
        is_plain_text = ink_rule_service.does_pass(entry, classifier.cuts)
        if people_score >= classifier.people_threshold:
            tag = PEOPLE
        elif classifier.board_weights is not None and board_score >= classifier.board_threshold:
            tag = BOARD
        else:
            tag = TEXT if is_plain_text else OTHER
        decisions.append((tag, float(people_score), is_plain_text, float(board_score)))

    return decisions


def save_tags_classifier(classifier: TagsClassifierRecord,
                         name: str) -> None:
    """`classifiers/<name>.npz` (the people head's weights and threshold, the ink scale) and `classifiers/<name>.json`
    (`method: tags`, the cuts, what it was trained on and how)."""

    config.CLASSIFIERS_DIR.mkdir(parents=True, exist_ok=True)
    board = {"board_weights": classifier.board_weights} if classifier.board_weights is not None else {}
    numpy.savez(config.CLASSIFIERS_DIR / f"{name}.npz", weights=classifier.people_weights,
                threshold=classifier.people_threshold, ink_mean=classifier.ink_mean, ink_spread=classifier.ink_spread,
                **board)
    description = {"task": "tags", "method": "tags", "peopleThreshold": classifier.people_threshold,
                   "boardThreshold": classifier.board_threshold,
                   "cuts": classifier.cuts, "trainedOn": list(classifier.trained_on), "tagCounts": classifier.tag_counts,
                   "settings": classifier.settings, "embeddingModel": classifier.embedding_model}
    (config.CLASSIFIERS_DIR / f"{name}.json").write_text(json.dumps(description, indent=2) + "\n")


def load_tags_classifier(name: str) -> TagsClassifierRecord:
    weights_path = config.CLASSIFIERS_DIR / f"{name}.npz"
    if not weights_path.exists():
        raise SystemExit(f"no classifier named {name} in {config.CLASSIFIERS_DIR}; `classifier train --task tags` "
                         f"makes one")
    stored = numpy.load(weights_path)
    description = json.loads((config.CLASSIFIERS_DIR / f"{name}.json").read_text())

    # a classifier trained before the board tag has no board head, and tags no frame board
    return TagsClassifierRecord(people_weights=stored["weights"], people_threshold=float(stored["threshold"]),
                                board_weights=stored["board_weights"] if "board_weights" in stored else None,
                                board_threshold=float(description.get("boardThreshold", 1.0)), ink_mean=stored["ink_mean"], ink_spread=stored["ink_spread"], cuts=description["cuts"],
                                trained_on=tuple(description["trainedOn"]), tag_counts=description["tagCounts"],
                                settings=description["settings"], embedding_model=description["embeddingModel"])
