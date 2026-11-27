"""The name a run is shown under, and the folder name it had before the Manager's Rename renamed it to its AI name,
kept in its `run-name.json`, with the rename asked while a run wrote into the folder, which waits there until the run
ends. No phase reads it: a resume goes on from the folder wherever it is, and the published document is named from the
agenda."""

from __future__ import annotations

import json
from pathlib import Path

from models import output_contract
from utils import json_store_utils

__all__ = ["read_display_name", "read_former_name", "read_waiting_rename", "write_display_name", "write_waiting_rename"]

_WAITING_KEY = "waitingRename"


def _read_name_file(folder: Path) -> dict:
    """The run's name file; empty when it has none, or it cannot be read."""

    try:
        data = json.loads((folder / output_contract.RUN_NAME_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_name_file(folder: Path,
                     name_record: dict) -> None:
    """The name file written with `name_record`, or removed when it holds nothing."""

    path = folder / output_contract.RUN_NAME_FILE
    if not name_record:
        path.unlink(missing_ok=True)
        return
    json_store_utils.write_json(path, name_record)


def read_display_name(folder: Path) -> str:
    """The run's display name; empty when it has none, or its file cannot be read."""

    return " ".join(str(_read_name_file(folder).get("name") or "").split())


def read_former_name(folder: Path) -> str:
    """The folder name the run had before the Manager's Rename renamed it; empty when it was never renamed."""

    return str(_read_name_file(folder).get("formerName") or "")


def read_waiting_rename(folder: Path) -> str:
    """The rename asked while a run wrote into the folder: "ai-name" or "file-name"; empty when none waits."""

    return str(_read_name_file(folder).get(_WAITING_KEY) or "")


def write_display_name(folder: Path,
                       name: str,
                       former_name: str = "") -> str:
    """The run shown as `name`, its spaces collapsed, with the folder name it had before a rename; an empty name shows
    the run under its folder's name again. A rename waiting for its run is kept."""

    cleaned = " ".join(str(name).split())
    waiting = read_waiting_rename(folder)
    _write_name_file(folder, {**({"name": cleaned} if cleaned else {}),
                              **({"formerName": former_name} if cleaned and former_name else {}),
                              **({_WAITING_KEY: waiting} if waiting else {})})

    return cleaned


def write_waiting_rename(folder: Path,
                         rename: str) -> None:
    """The rename ("ai-name" or "file-name") the folder gets once no run writes into it; an empty one clears it."""

    names = {key: value for key, value in _read_name_file(folder).items() if key != _WAITING_KEY}
    _write_name_file(folder, {**names, **({_WAITING_KEY: rename} if rename else {})})
