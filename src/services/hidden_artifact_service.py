"""Which artifacts a person hid from the UI's view of a run folder, kept in `ui/hidden-artifacts.json`.

Hiding is a display setting only: the file stays in the run folder, and the pipeline still uses it. The record is
keyed by the folder as the UI names it (`output/<name>`), so a folder moved to another root shows everything again.
"""

from __future__ import annotations

import threading
from pathlib import Path

from services import job_queue_service
from utils import json_store_utils

__all__ = ["HIDDEN_FILE", "list_hidden", "set_hidden"]

HIDDEN_FILE = "hidden-artifacts.json"
_lock = threading.Lock()


def _build_record_path() -> Path:
    """`ui/hidden-artifacts.json`, in the queue's folder."""

    return job_queue_service.QUEUE_DIR / HIDDEN_FILE


def _load_record() -> dict[str, list[str]]:
    path = _build_record_path()
    record = json_store_utils.read_json(path) if path.is_file() else {}
    if not isinstance(record, dict):
        raise ValueError(f"{path} must hold an object of run folder -> hidden paths: repair or remove it by hand")

    return record


def _build_key(root: str,
               name: str) -> str:
    """The record's key for a run folder: `<root>/<name>`."""

    return f"{root}/{name}"


def list_hidden(root: str,
                name: str) -> set[str]:
    """The artifact paths hidden for the run folder `<root>/<name>`."""

    return set(_load_record().get(_build_key(root, name), []))


def set_hidden(root: str,
               name: str,
               paths: list[str],
               is_hidden: bool) -> set[str]:
    """Hide or show `paths` for the run folder `<root>/<name>`; returns what is hidden for it afterwards. The record
    lives in the queue's folder (`ui/`), read at each call."""

    key = _build_key(root, name)
    with _lock:
        record = _load_record()
        hidden = set(record.get(key, []))
        changed = set(paths)
        hidden = hidden | changed if is_hidden else hidden - changed
        if hidden:
            record[key] = sorted(hidden)
        else:
            record.pop(key, None)
        json_store_utils.write_json(_build_record_path(), record)

    return hidden
