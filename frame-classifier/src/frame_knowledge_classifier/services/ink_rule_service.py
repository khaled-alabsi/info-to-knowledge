"""The text-only task's rule: a frame is text-only when every ink measure is within its cut.

Training chooses the cuts: of the candidate values for each measure (`CANDIDATE_CUTS`), the combination that lets the
most training frames labelled text-only through while no frame labelled not-text-only would get through even with
every share cut `SAFETY_MARGIN` times looser, so FP on the training frames is 0 with room to spare: without the margin
the cuts drifted to the loosest values the training frames allowed, and a thin diagram of a group left out passed. Two cuts are fixed by the definition rather than chosen: no word of the body in
a second colour, and no fraction. A frame's score is 1 when it passes every cut and 0 when not. Saved as
`classifiers/<name>.json`.
"""

from __future__ import annotations

import itertools
import json

import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.cuts_record import CutsRecord
from frame_knowledge_classifier.models.task_record import TaskRecord
from frame_knowledge_classifier.services import ink_service

# The values each cut may take; one value is a cut the definition fixes
CANDIDATE_CUTS = {
    "otherInkShare": (0.0, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01),
    "largestOtherShare": (0.0, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.05),
    "saturatedOtherShare": (0.0, 0.0001, 0.0005, 0.001, 0.005),
    "inlineOffColourWords": (0,),   # any word of the body in a second colour has meaning
    "otherEdgeShare": (0.0, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.004),
    "stackedBars": (0,),            # a fraction is a formula
    "fillShare": (0.0, 0.001, 0.005, 0.01, 0.02, 0.05),
    "tableJunctions": (0, 1, 2, 4),
    "words": (3, 5, 8),             # a frame with almost no words has nothing for an OCR to read
}


# A not-text-only training frame must fail even with every share cut this many times looser
SAFETY_MARGIN = 2.0


def does_pass(measures: dict,
              cuts: dict) -> bool:
    for name, cut in cuts.items():
        if ink_service.MEASURES[name] == "max" and measures[name] > cut:
            return False
        if ink_service.MEASURES[name] == "min" and measures[name] < cut:
            return False

    return True


def choose_cuts(measures: list[dict],
                should_skip: numpy.ndarray) -> dict:
    """The cuts letting the most frames labelled text-only through with no frame labelled not-text-only through."""

    names = list(CANDIDATE_CUTS)
    passing, guarded = [], []
    for name in names:
        measured = numpy.array([entry[name] for entry in measures])
        is_max = ink_service.MEASURES[name] == "max"
        passing.append([measured <= cut if is_max else measured >= cut for cut in CANDIDATE_CUTS[name]])
        # a share cut loosened by the margin; a count (words, coloured words, fractions) keeps its cut
        guarded.append([measured <= cut * SAFETY_MARGIN if is_max and isinstance(cut, float) else
                        (measured <= cut if is_max else measured >= cut) for cut in CANDIDATE_CUTS[name]])
    best, best_count = None, -1
    for choice in itertools.product(*(range(len(CANDIDATE_CUTS[name])) for name in names)):
        loosened = numpy.logical_and.reduce([guarded[index][value] for index, value in enumerate(choice)])
        if loosened[~should_skip].any():
            continue
        through = numpy.logical_and.reduce([passing[index][value] for index, value in enumerate(choice)])
        count = int(through[should_skip].sum())
        if count > best_count:
            best, best_count = {name: CANDIDATE_CUTS[name][value] for name, value in zip(names, choice)}, count
    if best is None:
        raise SystemExit("no cuts let a text-only frame through without letting a not-text-only frame through: a "
                         "not-text-only frame measures like plain text")

    return best


def train_cuts(measures: list[dict],
               should_skip: numpy.ndarray,
               task: TaskRecord,
               trained_on: tuple[str, ...]) -> CutsRecord:
    return CutsRecord(cuts=choose_cuts(measures, should_skip), task=task.name, trained_on=trained_on,
                      frame_count=len(should_skip), skip_count=int(should_skip.sum()))


def score_frames(measures: list[dict],
                 cuts: dict) -> numpy.ndarray:
    """1 for a frame passing every cut, 0 for one that does not."""

    return numpy.array([1.0 if does_pass(entry, cuts) else 0.0 for entry in measures])


def save_cuts(record: CutsRecord,
              name: str) -> None:
    config.CLASSIFIERS_DIR.mkdir(parents=True, exist_ok=True)
    description = {"task": record.task, "method": "ink-rule", "cuts": record.cuts, "trainedOn": list(record.trained_on),
                   "frameCount": record.frame_count, "skipCount": record.skip_count}
    (config.CLASSIFIERS_DIR / f"{name}.json").write_text(json.dumps(description, indent=2) + "\n")


def load_cuts(name: str) -> CutsRecord:
    path = config.CLASSIFIERS_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"no classifier named {name} in {config.CLASSIFIERS_DIR}; `classifier train --task text-only` "
                         f"makes one")
    description = json.loads(path.read_text())

    return CutsRecord(cuts=description["cuts"], task=description["task"], trained_on=tuple(description["trainedOn"]),
                      frame_count=description["frameCount"], skip_count=description["skipCount"])
