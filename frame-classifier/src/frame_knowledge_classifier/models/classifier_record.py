"""A trained classifier: its weights, its threshold and what it was trained on."""

from __future__ import annotations

from dataclasses import dataclass

import numpy


@dataclass(frozen=True)
class ClassifierRecord:
    weights: numpy.ndarray          # one weight per embedding number, then the offset: 3,841 numbers
    threshold: float                # a frame scoring at or above it gets the task's skip label
    task: str                       # the task it was trained for, a key of task_record.TASKS
    trained_on: tuple[str, ...]     # the datasets whose labelled frames trained it
    frame_count: int
    skip_count: int                 # the frames it trained on labelled with the task's skip label
    settings: dict                  # the TrainingSettings it was trained with
    embedding_model: str            # open_clip architecture and weights, "ViT-B-16-SigLIP2/webli"
