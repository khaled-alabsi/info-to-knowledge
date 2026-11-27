"""A trained ink rule: the cut on each measure, and what it was chosen on."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CutsRecord:
    cuts: dict                      # measure name -> its cut; a frame passing every cut gets the task's skip label
    task: str                       # the task it was trained for, a key of task_record.TASKS
    trained_on: tuple[str, ...]     # the datasets whose labelled frames chose the cuts
    frame_count: int
    skip_count: int                 # the frames it was chosen on labelled with the task's skip label
