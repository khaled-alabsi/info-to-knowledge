"""A run's worker set: the name of the `workerSets` entry the run was started on, kept in
`run-worker-set.json` and laid over the settings at every start of the run, so a resume calls the same models at the
same hosts. No file: the run has the settings' `defaultWorkerSet` (the settings files as they are)."""

from __future__ import annotations

from pathlib import Path

import config
from models import output_contract
from utils import json_store_utils

__all__ = ["read_chosen_worker_set", "write_chosen_worker_set", "resolve_worker_set"]


def read_chosen_worker_set(folder: Path) -> str | None:
    """The worker set's name as the run folder keeps it, None when no file is there."""

    path = folder / output_contract.RUN_WORKER_SET_FILE
    if not path.is_file():
        return None
    name = (json_store_utils.read_json(path) or {}).get("name")

    return name if isinstance(name, str) and name else None


def write_chosen_worker_set(folder: Path,
                            name: str) -> None:
    json_store_utils.write_json(folder / output_contract.RUN_WORKER_SET_FILE, {"name": name})


def resolve_worker_set(folder: Path,
                       requested: str) -> tuple[str, bool]:
    """`(name, is_to_keep)`: the worker set a run starts on and whether its folder is to keep it now. The run line's
    `requested` name wins, else the one the folder kept, else the settings' `defaultWorkerSet`. A name no `workerSets`
    entry holds raises `ValueError` naming the sets that exist, before anything is written. The default set is kept
    only when asked for by name, so a run with no choice follows the settings' `defaultWorkerSet` until it has a set of
    its own: a set other than `default` that nobody asked for (the settings' `defaultWorkerSet`) is kept at once."""

    kept = read_chosen_worker_set(folder)
    name = requested or kept or ""
    if not name:
        name = config.read_layers(profile_name=config.PROFILE_NAME,
                                  worker_set_name=config.DEFAULT_WORKER_SET_NAME).get(
            "defaultWorkerSet", config.DEFAULT_WORKER_SET_NAME)
    # names the sets that exist when there is none of this name
    config.read_layers(profile_name=config.PROFILE_NAME, worker_set_name=name)
    is_to_keep = name != kept and (bool(requested) or name != config.DEFAULT_WORKER_SET_NAME)

    return name, is_to_keep
