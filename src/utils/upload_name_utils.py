"""The name an uploaded file is kept under: its base name, numbered when the folder already holds it."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

__all__ = ["read_upload_name", "choose_free_path", "choose_free_folder"]


def read_upload_name(original: str) -> str:
    """The base name of an uploaded file's name or path (a Windows path's backslashes read as folders), or "" when it
    has none the inbox can list: an empty name, or one starting with a dot."""

    name = PurePosixPath(original.replace("\\", "/")).name

    return "" if not name or name.startswith(".") else name


def choose_free_path(folder: Path,
                     name: str,
                     taken_suffix: str = "") -> Path:
    """`folder / name`, or `name-2`, `name-3` before its suffix when that is taken. With `taken_suffix`, a name whose
    file with that suffix added exists is taken too (an upload still being written as `<name>.part`)."""

    target, number = folder / name, 2
    while target.exists() or (taken_suffix and target.with_name(target.name + taken_suffix).exists()):
        target = folder / f"{PurePosixPath(name).stem}-{number}{PurePosixPath(name).suffix}"
        number += 1

    return target


def choose_free_folder(folder: Path,
                       name: str) -> Path:
    """`folder / name`, or `name-2`, `name-3` when that is taken: a folder's name has no suffix, so a dot in it
    ("Sprint 1.5") stays where it is."""

    target, number = folder / name, 2
    while target.exists():
        target = folder / f"{name}-{number}"
        number += 1

    return target
