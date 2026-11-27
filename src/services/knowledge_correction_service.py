"""The mistakes a person corrects in a Refine proposal, saved as learning data for the frame knowledge classifier.

When a proposal is accepted, the person's ticks are compared with what the classifier proposed. A frame it proposed
that the person unticked is an FP (it would have dropped knowledge): saved with the label `keep`. A frame it did not
propose that the person ticked is an FN (a frame without knowledge it missed): saved with the label `drop`. A proposal
accepted as it stands saves nothing.

They go into the classifier project, beside its command: `corrections/<run folder name>/` holds `frames/` (copies of the
frames), `labels.csv` (frame,label, as a dataset's) and `corrections.json` (each frame's outcome, drop score and run).
A run refined twice adds to its folder, a frame's later correction replacing its earlier one. The classifier project's
`./classifier add-corrections` turns them into datasets when the person chooses to train on them.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import config
from models import output_contract
from models.run_paths import RunPaths
from services import knowledge_filter_service
from utils import json_store_utils

__all__ = ["CORRECTIONS_DIR_NAME", "save_corrections"]

CORRECTIONS_DIR_NAME = "corrections"


def _read_corrections(corrections_file: Path) -> dict[str, dict]:
    if not corrections_file.is_file():
        return {}

    return {entry["frame"]: entry for entry in json_store_utils.read_json(corrections_file)["frames"]}


def save_corrections(paths: RunPaths,
                     items: list[dict],
                     proposed: list[str],
                     chosen: list[str],
                     drop_scores: dict[str, float]) -> tuple[list[str], list[str], Path | None]:
    """The FP and FN slugs of one accepted proposal over the frames `items` (the frame list before the drop), saved
    as the module says, and the folder they went into (None when there was nothing to save). Call it before the
    dropped images move."""

    # STEP 1: the mistakes: proposed but kept by the person, and chosen but not proposed
    false_drop_slugs, missed_drop_slugs = set(proposed) - set(chosen), set(chosen) - set(proposed)
    false_drops = [item for item in items if item["slug"] in false_drop_slugs]
    missed_drops = [item for item in items if item["slug"] in missed_drop_slugs]
    if not false_drops and not missed_drops:
        return [], [], None
    project_dir = knowledge_filter_service.resolve_classifier_command(config.settings.frames).parent
    run_dir = project_dir / CORRECTIONS_DIR_NAME / paths.output_root.name

    # STEP 2: each frame copied in, its correction merged into the run's earlier ones
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    corrections_file = run_dir / "corrections.json"
    corrections = _read_corrections(corrections_file)
    for outcome, label, group in (("FP", "keep", false_drops), ("FN", "drop", missed_drops)):
        for item in group:
            name = Path(item["path"]).name
            shutil.copy2(paths.output_root / item["path"], frames_dir / name)
            corrections[name] = {"frame": name, "label": label, "outcome": outcome,
                                 "dropScore": drop_scores.get(item["slug"]), "timestamp": item["timestamp"],
                                 "run": str(paths.output_root), "framesFile": output_contract.LOOP_ITEMS_FILE}

    # STEP 3: the labels as a dataset holds them, and the record behind them
    ordered = [corrections[name] for name in sorted(corrections)]
    json_store_utils.write_json(corrections_file, {"frames": ordered})
    with (run_dir / "labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "label"])
        writer.writerows([entry["frame"], entry["label"]] for entry in ordered)

    return [item["slug"] for item in false_drops], [item["slug"] for item in missed_drops], run_dir
