"""A trained tags classifier: one model that gives every frame one of four tags, people, board, text or other.

People is decided by a logistic regression over SigLIP 2's five views and the frame's ink measures, with its threshold
above every training frame tagged text or other; text, of the frames not people, by the ink rule's cuts; every other
frame is other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy


@dataclass(frozen=True)
class TagsClassifierRecord:
    people_weights: numpy.ndarray   # one weight per embedding number, then per ink measure, then the offset
    people_threshold: float         # a frame scoring at or above it is people
    board_weights: numpy.ndarray | None  # the board head's weights, as the people head's; None when no training frame
                                         # was tagged board
    board_threshold: float          # a frame not people scoring at or above it is board
    ink_mean: numpy.ndarray         # each ink measure's mean on the training frames, after its log scale
    ink_spread: numpy.ndarray       # and its standard deviation, so each measure enters on the same scale
    cuts: dict                      # the ink rule's cuts: a frame that is not people and passes every one is text
    trained_on: tuple[str, ...]     # the datasets whose tagged frames trained it
    tag_counts: dict                # tag -> how many training frames had it
    settings: dict                  # the TrainingSettings it was trained with
    embedding_model: str            # open_clip architecture and weights, "ViT-B-16-SigLIP2/webli"
