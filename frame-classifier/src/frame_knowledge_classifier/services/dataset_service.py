"""The datasets under `data/`: finding them, reading and writing their labels, and adding a new one.

A dataset is a folder `data/<name>/` holding `frames/` (the frame images), one labels file per task (`frame,label`,
one row per labelled frame: `labels.csv` for the knowledge task, `text-only-labels.csv` for the text-only task) and
`dataset.json` (`source`, `labelledBy`, `rotationGroup`, `isHeldOut`). The tags task reads `tags.csv` instead
(`frame,tag,note`, the tag people, text or other). A frame with no row in a task's labels file is
unlabelled for that task: shown on its label page, never trained on, never judged.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.dataset_record import DatasetRecord, LabelledFrame
from frame_knowledge_classifier.models.label_vocabulary import TAGS
from frame_knowledge_classifier.models.task_record import TaskRecord

FRAME_SUFFIXES = (".jpg", ".jpeg", ".png")
# Each dataset's tags, one per frame, set by looking at it
TAGS_FILE = "tags.csv"


def _read_dataset(folder: Path) -> DatasetRecord:
    description_path = folder / "dataset.json"
    if not description_path.exists():
        raise SystemExit(f"{folder} has no dataset.json; `classifier add-dataset` writes one")
    description = json.loads(description_path.read_text())

    return DatasetRecord(name=folder.name, folder=folder, source=description["source"],
                         labelled_by=description["labelledBy"], rotation_group=description["rotationGroup"],
                         is_held_out=bool(description.get("isHeldOut", False)))


def list_datasets() -> list[DatasetRecord]:
    """Every dataset under `data/`, by name."""

    if not config.DATA_DIR.exists():
        return []

    return [_read_dataset(folder) for folder in sorted(config.DATA_DIR.iterdir()) if folder.is_dir()]


def list_frames(dataset: DatasetRecord) -> list[Path]:
    """Every frame image of `dataset`, by file name."""

    return sorted(path for path in dataset.frames_dir.iterdir() if path.suffix.lower() in FRAME_SUFFIXES)


def read_labels(dataset: DatasetRecord,
                task: TaskRecord) -> dict[str, str]:
    """frame file name -> one of `task`'s two labels; a label outside those two stops the program by name."""

    labels_path = dataset.labels_path(task)
    if not labels_path.exists():
        return {}
    with labels_path.open(newline="") as labels_file:
        labels = {row["frame"]: row["label"] for row in csv.DictReader(labels_file)}
    unknown = sorted({label for label in labels.values() if label not in task.labels})
    if unknown:
        raise SystemExit(f"{labels_path} holds labels other than {task.labels}: {unknown}")

    return labels


def read_tags(dataset: DatasetRecord) -> dict[str, str]:
    """frame file name -> people, text or other; a tag outside those three stops the program by name."""

    tags_path = dataset.folder / TAGS_FILE
    if not tags_path.exists():
        return {}
    with tags_path.open(newline="", encoding="utf-8") as tags_file:
        tags = {row["frame"]: row["tag"] for row in csv.DictReader(tags_file)}
    unknown = sorted({tag for tag in tags.values() if tag not in TAGS})
    if unknown:
        raise SystemExit(f"{tags_path} holds tags other than {TAGS}: {unknown}")

    return tags


def list_tagged_frames(datasets: list[DatasetRecord]) -> list[LabelledFrame]:
    """Every frame of `datasets` with a tag, dataset by dataset, frame by frame; its tag is its label."""

    frames = []
    for dataset in datasets:
        tags = read_tags(dataset)
        frames += [LabelledFrame(dataset=dataset.name, path=path, label=tags[path.name])
                   for path in list_frames(dataset) if path.name in tags]

    return frames


def write_labels(dataset: DatasetRecord,
                 labels: dict[str, str],
                 task: TaskRecord) -> None:
    with dataset.labels_path(task).open("w", newline="") as labels_file:
        writer = csv.writer(labels_file)
        writer.writerow(["frame", "label"])
        writer.writerows(sorted(labels.items()))


def list_labelled_frames(datasets: list[DatasetRecord],
                         task: TaskRecord) -> list[LabelledFrame]:
    """Every frame of `datasets` labelled for `task`, dataset by dataset, frame by frame; unlabelled frames are left
    out."""

    frames = []
    for dataset in datasets:
        labels = read_labels(dataset, task)
        frames += [LabelledFrame(dataset=dataset.name, path=path, label=labels[path.name])
                   for path in list_frames(dataset) if path.name in labels]

    return frames


def import_labels(labels_csv: Path,
                  task: TaskRecord) -> dict[str, int]:
    """Writes the labels of a file `task`'s label page downloaded (`dataset,frame,label`) into each dataset's labels
    file of that task, replacing the rows it names and keeping every other row; returns how many rows each dataset
    got."""

    datasets = {dataset.name: dataset for dataset in list_datasets()}
    with labels_csv.open(newline="") as labels_file:
        rows = list(csv.DictReader(labels_file))
    counts: dict[str, int] = {}
    by_dataset: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["dataset"] not in datasets:
            raise SystemExit(f"{labels_csv} names a dataset not under data/: {row['dataset']}")
        if row["label"] not in task.labels:
            raise SystemExit(f"{labels_csv} holds a label other than {task.labels}: {row['label']} ({row['frame']})")
        by_dataset.setdefault(row["dataset"], {})[row["frame"]] = row["label"]
    for name, new_labels in by_dataset.items():
        dataset = datasets[name]
        write_labels(dataset, {**read_labels(dataset, task), **new_labels}, task)
        counts[name] = len(new_labels)

    return counts


def add_dataset(name: str,
                frames_dir: Path,
                source: str,
                labelled_by: str,
                rotation_group: str | None,
                is_held_out: bool) -> DatasetRecord:
    """A new dataset `data/<name>/` with a copy of every frame image of `frames_dir` and no labels yet."""

    folder = config.DATA_DIR / name
    if folder.exists():
        raise SystemExit(f"{folder} already exists")
    frames = sorted(path for path in frames_dir.iterdir() if path.suffix.lower() in FRAME_SUFFIXES)
    if not frames:
        raise SystemExit(f"{frames_dir} holds no frame image ({', '.join(FRAME_SUFFIXES)})")
    (folder / "frames").mkdir(parents=True)
    for frame in frames:
        shutil.copy2(frame, folder / "frames" / frame.name)
    description = {"source": source, "labelledBy": labelled_by, "rotationGroup": rotation_group or name,
                   "isHeldOut": is_held_out}
    (folder / "dataset.json").write_text(json.dumps(description, indent=2) + "\n")

    return _read_dataset(folder)
