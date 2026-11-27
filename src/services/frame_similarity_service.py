"""Phase 2's similarity recheck, video only: the redundancy filter only ever shows a frame
beside the frames of its own batch, so a screen shown again later, or a build whose finished slide comes batches on,
stays. Here all kept frames are compared at once by the frame classifier's SigLIP vectors (the whole frame and its four
quarters, mean cosine); the frames at or above `frames.similarityThreshold` that sat in different batches make lists
of frames that look alike, each asked again of the model, `frames.similarityFramesPerCall` frames a call, with the redundancy filter's
own instruction and checks (`frame_redundancy_service.judge_calls`, with `build_judging_spec`). A frame removed leaves as `droppedBy:
"similarity"`. Measured once, offline."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import numpy

from config import FrameSettings
from models.frame_judging_spec import FrameJudgingSpec
from services import apple_text_service
from services import frame_redundancy_service
from utils import frame_group_utils

__all__ = ["DROPPED_BY", "VECTOR_PARTS", "read_vectors", "list_alike_groups_across_batches", "build_calls", "build_judging_spec",
           "list_alike_copies"]

DROPPED_BY = "similarity"
# The parts each frame's vector holds, each scaled to length 1: the whole frame and its four quarters
VECTOR_PARTS = 5
# The kept frames before a frame whose lines count as shown, as the repeat-copy step looks back
# (frame_redundancy_service._RETURN_LOOK_BACK, measured there)
LOOK_BACK = 12


def read_vectors(path: Path) -> dict[str, numpy.ndarray]:
    """Each frame's vector by its file name, from the classifier's `--vectors-out` file; refused by name when the
    classifier, asked to write it, did not: an empty set would read as no frames alike."""

    if not path.is_file():
        raise FileNotFoundError(f"the frame classifier wrote no vectors file at {path}, which phase 2 asked of it with "
                                f"--vectors-out for the similarity recheck")
    saved = numpy.load(path)

    return {str(name): vector for name, vector in zip(saved["frames"].tolist(), saved["vectors"])}


def list_alike_groups_across_batches(vectors: list[numpy.ndarray | None],
                                     batches: list[int],
                                     threshold: float) -> list[list[int]]:
    """The lists of positions whose frames look alike (mean part cosine at least `threshold`) and sat in different
    batches of the redundancy filter, which saw a batch's own frames together; a frame with no vector is in none."""

    present = numpy.array([vector is not None for vector in vectors])
    if present.sum() < 2:
        return []
    width = next(vector for vector in vectors if vector is not None).shape[0]
    matrix = numpy.stack([vector if vector is not None else numpy.zeros(width) for vector in vectors])
    batch = numpy.array(batches)
    alike = ((matrix @ matrix.T / VECTOR_PARTS) >= threshold) & (batch[:, None] != batch[None, :])
    alike &= present[:, None] & present[None, :]
    numpy.fill_diagonal(alike, False)

    return frame_group_utils.list_alike_groups(alike)


def _list_alike_earlier(vectors: list[numpy.ndarray | None],
                        threshold: float) -> list[list[int]]:
    """For each position, the earlier positions whose frames look alike to it (mean part cosine at least `threshold`),
    nearest first; a frame with no vector has none."""

    present = [index for index, vector in enumerate(vectors) if vector is not None]
    alike: list[list[int]] = [[] for _ in vectors]
    if len(present) < 2:
        return alike
    matrix = numpy.stack([vectors[index] for index in present])
    cosine = matrix @ matrix.T / VECTOR_PARTS
    for later_row, later in enumerate(present):
        earlier_rows = numpy.flatnonzero(cosine[later_row, :later_row] >= threshold)
        alike[later] = [present[int(row)] for row in reversed(earlier_rows)]

    return alike


def list_alike_copies(items: list[dict],
                      vectors: dict[str, numpy.ndarray],
                      frames_dir: Path,
                      settings: FrameSettings) -> list[dict]:
    """The kept frames that look alike to an earlier kept frame and add nothing to it, each with that frame as its
    `coveredBy` (`frame_redundancy_service.is_nothing_new`, the repeat-copy step's test), with no model call. The
    recheck paired openpv's slide shown again (frame 109 with 069) and its model removed none, so the frame reached
    phase 3 and was dropped by hand. A 3-minute re-take stays (DEFECTS
    665), and so does a frame whose earlier twin left here: it is then compared with the next alike frame kept. Two
    slides of one template look alike far more often than the capture's returns, so a frame showing a number its
    earlier frame lacks stays too; that needs Apple's recognizer, and without it nothing leaves here."""

    if not apple_text_service.is_available():
        return []
    # Tesseract may miss no word here: its 2-word allowance let a terminal pass whose typed "push it" it never read
    # (Lecture 7 frame 094), a return the capture had not matched by its picture
    strict = settings.model_copy(update={"redundancy_max_missing_words": 0})
    names = [Path(item["path"]).name for item in items]
    alike = _list_alike_earlier([vectors.get(name) for name in names], settings.similarity_threshold)
    read: dict[str, list[str]] = {}
    left: set[int] = set()
    copies = []
    for index, item in enumerate(items):
        if item.get("isHeldRetake"):
            continue
        window = items[max(0, index - LOOK_BACK):index]
        for earlier in alike[index]:
            if earlier in left:
                continue
            new_numbers = frame_redundancy_service.list_new_numbers(item=item,
                                                                    original=items[earlier],
                                                                    frames_dir=frames_dir,
                                                                    read=read)
            if not new_numbers and frame_redundancy_service.is_nothing_new(item=item,
                                                       earlier=[items[earlier], *window],
                                                       frames_dir=frames_dir,
                                                       read=read,
                                                       settings=strict):
                copies.append({**item, "coveredBy": [items[earlier]["slug"]]})
                left.add(index)
                break

    return copies


def build_calls(items: list[dict],
                vectors: dict[str, numpy.ndarray],
                batches: dict[str, int],
                settings: FrameSettings) -> tuple[list[list[int]], int]:
    """The recheck's calls, as lists of positions in `items`, and how many lists of similar frames made them.
    `batches` names each frame's batch in the redundancy filter by its slug."""

    names = [Path(item["path"]).name for item in items]
    # A frame no call of the redundancy filter showed (the filter off) is a batch of its own, a number no other frame
    # has, so every pair of frames alike is asked
    own_batches = [batches.get(item["slug"], -1 - index) for index, item in enumerate(items)]
    groups = list_alike_groups_across_batches([vectors.get(name) for name in names], own_batches,
                                              settings.similarity_threshold)

    return frame_group_utils.split_into_calls(groups, settings.similarity_frames_per_call), len(groups)


def build_judging_spec(settings: FrameSettings,
                       report_progress: Callable[[int], None] | None = None,
                       run_dir: Path | None = None) -> FrameJudgingSpec:
    """How the recheck asks its model, for `frame_redundancy_service.judge_calls` with the calls of `build_calls`."""

    model = settings.similarity_model or settings.redundancy_model

    return dataclasses.replace(frame_redundancy_service.build_judging_spec(settings, report_progress, run_dir),
                               model=model, dropped_by=DROPPED_BY, label=f"similarity recheck ({model})")
