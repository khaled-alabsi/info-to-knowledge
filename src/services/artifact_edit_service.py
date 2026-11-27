"""Saving one markdown artifact of a run folder from the page, with every version it replaces kept.

A run folder is hours of model calls, so no edit here replaces bytes outright: the current ones are copied into
`<run folder>/.edits/<UTC stamp>/<path>` before the new ones land, and the last 20 versions of each file stay. A
version is read back by its stamp, and putting one back is an ordinary save, so it is itself undoable.

Only a `.md` file the folder already holds can be written: nothing is created, and no image, audio or program state
is ever written from the page.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from models.ui_errors import UnknownArtifactError
from services import run_artifact_service

__all__ = ["EDITS_DIR", "VERSIONS_KEPT", "save_markdown", "list_versions", "read_version"]

# Where the bytes an edit replaces go, inside the run folder itself, so a folder moved or zipped carries its history.
EDITS_DIR = ".edits"
# Per file, oldest pruned at the moment a version is written: a queue bounded by versions, not by the folder's age.
VERSIONS_KEPT = 20
_STAMP_FORMAT = "%Y%m%dT%H%M%S.%f"
_STAMP_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{6}$")


def _build_edits_dir(folder: Path) -> Path:
    return folder / EDITS_DIR


def _read_relative(folder: Path,
                   path: Path) -> Path:
    """`path` as the run folder names it, the form a kept version is filed under."""

    return path.relative_to(folder.resolve())


def _build_kept_path(folder: Path,
                     stamp: str,
                     relative: Path) -> Path:
    """Where the version of `relative` kept at `stamp` lies: `.edits/<stamp>/<relative>`."""

    return _build_edits_dir(folder) / stamp / relative


def _list_kept(folder: Path,
               relative: Path) -> list[tuple[str, Path]]:
    """Every kept copy of one file as its stamp and its path, oldest stamp first."""

    edits_dir = _build_edits_dir(folder)
    if not edits_dir.is_dir():
        return []
    # Looked up by path under each stamp folder, never as a glob pattern: a document named after its meeting can hold
    # `[`, `*` or `?`
    stamps = sorted(stamp_dir.name for stamp_dir in edits_dir.iterdir() if _STAMP_PATTERN.match(stamp_dir.name))

    return [(stamp, kept) for stamp in stamps if (kept := _build_kept_path(folder, stamp, relative)).is_file()]


def _prune_old_versions(folder: Path,
                        relative: Path) -> None:
    """Remove the versions of one file past the last `VERSIONS_KEPT`, and the folders that leaves empty. Pruning one
    file never touches another's history."""

    edits_dir = _build_edits_dir(folder)
    for _, stale in _list_kept(folder, relative)[:-VERSIONS_KEPT]:
        stale.unlink(missing_ok=True)
        empty = stale.parent
        while empty != edits_dir and not any(empty.iterdir()):
            empty.rmdir()
            empty = empty.parent


def _keep_current(folder: Path,
                  path: Path) -> str:
    """Copy the bytes about to be replaced into `.edits/<stamp>/<path>`, then prune that file's versions to the last
    `VERSIONS_KEPT`. This is the enforcement point for the cap."""

    relative = _read_relative(folder, path)
    stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)
    destination = _build_kept_path(folder, stamp, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    _prune_old_versions(folder, relative)

    return stamp


def resolve_editable_artifact(folder: Path,
                              relative: str) -> Path:
    """The markdown file `relative` names inside the run folder. Refused when it leaves the folder, is missing, is no
    markdown, or is one of the kept versions."""

    path = run_artifact_service.resolve_artifact(folder, relative)
    if path.suffix.lower() != ".md":
        raise UnknownArtifactError(f"{relative} is no markdown file: only a .md can be edited from the page")
    if EDITS_DIR in _read_relative(folder, path).parts:
        raise UnknownArtifactError(f"{relative} is a kept version, not the document: open the document and save it")

    return path


def list_versions(folder: Path,
                  relative: str) -> list[dict]:
    """Every version of one file this folder kept, newest first, each with its stamp and size."""

    path = resolve_editable_artifact(folder, relative)
    kept = _list_kept(folder, _read_relative(folder, path))

    return [{"at": stamp, "size_bytes": copy.stat().st_size} for stamp, copy in reversed(kept)]


def read_version(folder: Path,
                 relative: str,
                 stamp: str) -> str:
    """One kept version's text. `stamp` is matched against the stamps that exist, never joined onto a path: a stamp
    from a URL is as untrusted as a path from one."""

    path = resolve_editable_artifact(folder, relative)
    copy = next((kept for kept_at, kept in _list_kept(folder, _read_relative(folder, path)) if kept_at == stamp), None)
    if copy is None:
        raise UnknownArtifactError(f"{relative} has no version kept at {stamp}")

    return copy.read_text(encoding="utf-8", errors="replace")


def save_markdown(folder: Path,
                  relative: str,
                  markdown: str) -> dict:
    """Write `markdown` over the file `relative` names, keeping what it replaces. Returns the path, the new size and
    the stamp the previous bytes are kept under."""

    path = resolve_editable_artifact(folder, relative)
    kept_as = _keep_current(folder, path)
    path.write_text(markdown, encoding="utf-8")

    return {"path": relative, "size_bytes": path.stat().st_size, "kept_as": kept_as}
