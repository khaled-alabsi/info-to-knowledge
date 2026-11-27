"""The groups of `i2k serve`'s accounts: a group is a name and the accounts in it, kept in
`ui/groups.json` beside the accounts. A project gives a role to a group, and every account in it has that role there.
With a single sign-on later, an identity's groups come from its token's group claim instead of this file, and the
projects' grants stay as they are.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from models.ui_errors import AccountError
from services import job_queue_service
from utils import json_store_utils

__all__ = ["GROUPS_FILE", "list_groups", "add_group", "remove_group", "set_group_accounts", "list_account_groups"]

GROUPS_FILE = "groups.json"
_lock = threading.Lock()


def _build_path() -> Path:
    return job_queue_service.QUEUE_DIR / GROUPS_FILE


def _load() -> dict[str, Any]:
    path = _build_path()
    record = json_store_utils.read_json(path) if path.is_file() else {}

    return record if isinstance(record, dict) else {}


def _save(record: dict[str, Any]) -> None:
    path = _build_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store_utils.write_json(path, record)


def list_groups() -> list[dict[str, Any]]:
    """Every group as `{"name", "accounts"}`, by name."""

    return [{"name": name, "accounts": sorted(group.get("accounts") or [])} for name, group in sorted(_load().items())]


def add_group(name: str) -> None:
    """A new group of no account; refused by name when the name is empty or taken."""

    cleaned = name.strip()
    if not cleaned:
        raise AccountError("a group needs a name")
    with _lock:
        record = _load()
        if cleaned in record:
            raise AccountError(f"a group named {cleaned!r} exists")
        record[cleaned] = {"accounts": []}
        _save(record)


def remove_group(name: str) -> None:
    """The group gone; a project's grant to it then gives nobody anything."""

    with _lock:
        record = _load()
        if name not in record:
            raise AccountError(f"no group named {name!r}")
        del record[name]
        _save(record)


def set_group_accounts(name: str,
                       accounts: list[str]) -> None:
    """The group holding exactly `accounts`."""

    with _lock:
        record = _load()
        if name not in record:
            raise AccountError(f"no group named {name!r}")
        record[name]["accounts"] = sorted({account.strip() for account in accounts if account.strip()})
        _save(record)


def list_account_groups(account: str) -> list[str]:
    """The groups `account` is in, by name."""

    return sorted(name for name, group in _load().items() if account in (group.get("accounts") or []))
