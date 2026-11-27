"""The instructions a person edited on the page, kept in the workspace beside the runs, with every version.

The program ships every instruction as a default under `agent-context/`, which nothing here writes: an installed copy or
a container has no repository to keep edits in, and its defaults are read-only. An edit is kept in
`<workspace>/instructions/edits/<file id>`, and the text a model is sent is that edit when one exists, else the default
(`read_active`). Every save, reset and restore adds a version, `history/<file id>/<UTC stamp>.md`, with its record
beside it (`.json`: when, who, the note, the kind, the default it was made over); each default an edit was made over is
kept once as `defaults/<sha256>.md`, so a default an upgrade changed can be compared with the one the edit was made
over. Nothing is pruned. A file id is the file's path under `agent-context/`, `instructions/write.md`.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import config
from models.instruction_errors import UnknownInstructionFileError

__all__ = ["STORE_DIR", "VERSION_KINDS", "set_store_dir", "is_editable", "list_editable_files", "read_default", "read_active",
           "is_edited", "list_edited", "use_drafts", "save", "reset", "list_versions", "read_version", "describe"]

STORE_DIR = config.WORKSPACE_ROOT / "instructions"  # the workspace's instruction store; a test points it elsewhere
EDITS_DIR_NAME = "edits"                            # the text in force of each edited file
HISTORY_DIR_NAME = "history"                        # every version of each file, with its record
DEFAULTS_DIR_NAME = "defaults"                      # each default an edit was made over, by its sha256
VERSION_KINDS = ("edit", "restore", "reset")        # a text saved, an earlier version saved again, the default put back
_STAMP_FORMAT = "%Y%m%dT%H%M%S.%f"                  # a version's name: its UTC time, sortable as text
_STAMP_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{6}$")
_FOLDER_DESCRIPTION = "README.md"                   # a folder's description for its maintainers, sent to no model
_LOCAL_SUFFIX = ".local.md"                         # this machine's own terms, appended to the tracked ones
# Texts not saved yet, read in place of the store while a save's check builds the prompts they go into
_DRAFTS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("instruction_drafts", default={})


def set_store_dir(path: Path) -> None:
    """Where the edits and their versions are kept; a test points it at its own folder."""

    global STORE_DIR
    STORE_DIR = path


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_default(file_id: str) -> Path:
    """The shipped default `file_id` names, or a refusal: an id from a URL is as untrusted as a path from one."""

    if not is_editable(file_id):
        raise UnknownInstructionFileError(f"{file_id} is no instruction file the program ships")

    return config.AGENT_CONTEXT_DIR / file_id


def _build_edit_path(file_id: str) -> Path:
    return STORE_DIR / EDITS_DIR_NAME / file_id


def _build_history_dir(file_id: str) -> Path:
    return STORE_DIR / HISTORY_DIR_NAME / file_id


def _list_stamps(file_id: str) -> list[str]:
    """The stamps of one file's versions, oldest first."""

    history = _build_history_dir(file_id)
    if not history.is_dir():
        return []

    return sorted(path.stem for path in history.glob("*.json") if _STAMP_PATTERN.match(path.stem))


def _read_record(file_id: str,
                 stamp: str) -> dict:
    return json.loads((_build_history_dir(file_id) / f"{stamp}.json").read_text(encoding="utf-8"))


def _read_kept_default(sha: str) -> str:
    """The default text kept under `sha`, or nothing when it was never kept."""

    path = STORE_DIR / DEFAULTS_DIR_NAME / f"{sha}.md"

    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _keep_default(text: str) -> str:
    """Keep one default text under its hash, once, and give the hash."""

    sha = _compute_sha256(text)
    path = STORE_DIR / DEFAULTS_DIR_NAME / f"{sha}.md"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    return sha


def _add_version(file_id: str,
                 text: str,
                 kind: str,
                 account: str,
                 note: str) -> str:
    """Write one version of `file_id` and its record, and give its stamp."""

    stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)
    history = _build_history_dir(file_id)
    history.mkdir(parents=True, exist_ok=True)
    (history / f"{stamp}.md").write_text(text, encoding="utf-8")
    record = {"at": stamp, "kind": kind, "account": account, "note": note,
              "default_sha256": _keep_default(read_default(file_id)), "size_bytes": len(text.encode("utf-8"))}
    (history / f"{stamp}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")

    return stamp


def is_editable(file_id: str) -> bool:
    """Whether `file_id` names, in its own spelling, a markdown instruction the program ships under `agent-context/`:
    never a folder's description, this machine's own terms, a path leaving the folder or one written another way."""

    root = config.AGENT_CONTEXT_DIR.resolve()
    path = (root / file_id).resolve()
    if not path.is_relative_to(root) or path.relative_to(root).as_posix() != file_id:
        return False

    return (path.suffix == ".md" and path.name != _FOLDER_DESCRIPTION and not path.name.endswith(_LOCAL_SUFFIX)
            and path.is_file())


def list_editable_files() -> list[str]:
    """Every instruction file the program ships, by its path under `agent-context/`, sorted."""

    root = config.AGENT_CONTEXT_DIR.resolve()

    return sorted(file_id for path in root.rglob("*.md") if is_editable(file_id := path.relative_to(root).as_posix()))


def read_default(file_id: str) -> str:
    """The text the program ships for `file_id`."""

    return _resolve_default(file_id).read_text(encoding="utf-8")


def read_active(file_id: str) -> str:
    """The text a model is sent for `file_id` now: a draft a check is building with, else the edit, else the default."""

    drafts = _DRAFTS.get()
    if file_id in drafts:
        return drafts[file_id]
    default_path = _resolve_default(file_id)
    edit_path = _build_edit_path(file_id)

    return (edit_path if edit_path.is_file() else default_path).read_text(encoding="utf-8")


def is_edited(file_id: str) -> bool:
    """Whether a person's edit is in force for `file_id`."""

    _resolve_default(file_id)

    return _build_edit_path(file_id).is_file()


def list_edited() -> list[str]:
    """Every shipped file an edit is in force for, sorted."""

    edits = STORE_DIR / EDITS_DIR_NAME
    shipped = set(list_editable_files())

    return sorted(file_id for file_id in shipped if (edits / file_id).is_file())


@contextlib.contextmanager
def use_drafts(drafts: dict[str, str]) -> Iterator[None]:
    """Read `drafts` ({file id: text}) in place of the store inside the block, for this thread alone."""

    for file_id in drafts:
        _resolve_default(file_id)
    token = _DRAFTS.set({**_DRAFTS.get(), **drafts})
    try:
        yield
    finally:
        _DRAFTS.reset(token)


def reset(file_id: str,
          account: str,
          note: str = "") -> dict:
    """Put the default back in force for `file_id`, the edit kept as a version. A file already at its default is left
    as it is, with no version added."""

    edit_path = _build_edit_path(file_id)
    if not edit_path.is_file():
        _resolve_default(file_id)
        return {"at": "", "is_changed": False}
    stamp = _add_version(file_id, read_default(file_id), "reset", account, note)
    edit_path.unlink()

    return {"at": stamp, "is_changed": True}


def save(file_id: str,
         text: str,
         account: str,
         note: str = "",
         kind: str = "edit") -> dict:
    """Put `text` in force for `file_id` and keep it as a version of `kind` ("edit" or "restore"). The text in force
    already is no change and adds no version; the default's own text is a reset."""

    if kind not in VERSION_KINDS[:2]:
        raise ValueError(f"a save is an edit or a restore, never {kind!r}")
    if text == read_active(file_id):
        return {"at": "", "is_changed": False}
    if text == read_default(file_id):
        return reset(file_id, account, note)
    stamp = _add_version(file_id, text, kind, account, note)
    edit_path = _build_edit_path(file_id)
    edit_path.parent.mkdir(parents=True, exist_ok=True)
    edit_path.write_text(text, encoding="utf-8")

    return {"at": stamp, "is_changed": True}


def list_versions(file_id: str) -> list[dict]:
    """Every version of `file_id`, newest first: its stamp, kind, account, note and size."""

    _resolve_default(file_id)
    records = [_read_record(file_id, stamp) for stamp in reversed(_list_stamps(file_id))]

    return [{key: record[key] for key in ("at", "kind", "account", "note", "size_bytes")} for record in records]


def read_version(file_id: str,
                 stamp: str) -> dict:
    """One version's text, and the default it was made over. `stamp` is matched against the stamps that exist."""

    _resolve_default(file_id)
    if stamp not in _list_stamps(file_id):
        raise UnknownInstructionFileError(f"{file_id} has no version saved at {stamp}")
    record = _read_record(file_id, stamp)

    return {"at": stamp, "kind": record["kind"], "account": record["account"], "note": record["note"],
            "text": (_build_history_dir(file_id) / f"{stamp}.md").read_text(encoding="utf-8"),
            "default_text": _read_kept_default(record["default_sha256"])}


def describe(file_id: str) -> dict:
    """What the page shows of one file: its default, the text in force, whether it is edited, and, when the default
    changed since the edit in force was made, the default that edit was made over."""

    default = read_default(file_id)
    edited = is_edited(file_id)
    stamps = _list_stamps(file_id)
    default_at_edit = ""
    if edited and stamps:
        last_sha = _read_record(file_id, stamps[-1])["default_sha256"]
        if last_sha != _compute_sha256(default):
            default_at_edit = _read_kept_default(last_sha)

    return {"id": file_id, "default": default, "text": read_active(file_id), "is_edited": edited,
            "is_default_changed": bool(default_at_edit), "default_at_edit": default_at_edit,
            "versions": len(stamps)}
