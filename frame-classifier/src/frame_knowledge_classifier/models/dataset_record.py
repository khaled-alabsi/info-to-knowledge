"""One dataset: a folder of frames from one recording or one scene set, with its labels and its rotation group."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from frame_knowledge_classifier.models.task_record import TaskRecord


@dataclass(frozen=True)
class DatasetRecord:
    name: str
    folder: Path
    source: str             # where the frames come from, in words
    labelled_by: str        # who set the labels
    rotation_group: str     # datasets of one group are left out together in the fair test
    is_held_out: bool       # never trained on; judged only by the classifier trained on everything else

    @property
    def frames_dir(self) -> Path:
        return self.folder / "frames"

    def labels_path(self,
                    task: TaskRecord) -> Path:
        return self.folder / task.labels_file


@dataclass(frozen=True)
class LabelledFrame:
    dataset: str
    path: Path
    label: str              # one of its task's two labels, or its tag (people, text or other)

    @property
    def key(self) -> str:
        """`<dataset>/<frame file>`: how a frame is named in reports, decisions and the label page."""

        return f"{self.dataset}/{self.path.name}"
