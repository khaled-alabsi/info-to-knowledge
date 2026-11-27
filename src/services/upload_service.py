"""A recording uploaded from the web page lands in the inbox, where `i2k inbox` and the UI's list find it.

The file is written to `<name>.part` first and renamed when complete, so a half-sent upload is never
listed as a video. A name already in the inbox gets a number (`meeting-2.mp4`): an upload never
replaces a recording that is there. A recording a done run processed already (the same SHA-256, read while it is
written) or one the inbox holds already is refused, naming that run or video, and nothing of it is kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from models.ui_errors import DuplicateRecordingError, UploadRefusedError
from services import inbox_service
from services import recording_hash_service
from utils import upload_name_utils

__all__ = ["choose_upload_path", "save_upload", "describe_upload"]

# An upload is written under its name with this added, and renamed when complete
_PARTIAL_SUFFIX = ".part"


def choose_upload_path(inbox_root: Path,
                       filename: str) -> Path:
    """Where an upload called `filename` is saved: its own base name, refused when it is not a video or an audio file, numbered when
    the inbox already holds that name."""

    name = upload_name_utils.read_upload_name(filename)
    if not name:
        raise UploadRefusedError(f"{filename!r} has no file name the inbox can list")
    if Path(name).suffix.lower() not in inbox_service.RECORDING_SUFFIXES:
        raise UploadRefusedError(f"{name} is not a video or an audio file: the inbox takes "
                                 + ", ".join(inbox_service.RECORDING_SUFFIXES))

    return upload_name_utils.choose_free_path(inbox_root, name, taken_suffix=_PARTIAL_SUFFIX)


def describe_upload(name: str | None,
                    saved_as: str = "",
                    error: str = "",
                    duplicate: dict | None = None) -> dict:
    """One uploaded file's outcome as the page reads it, for a single upload and a held one alike: saved, refused (an
    `error`), or a duplicate, whose `describe_duplicate` record gives its kind, the run or inbox video it is the same
    as, and its run."""

    if duplicate is not None:
        kind = duplicate["kind"]
    else:
        kind = "refused" if error else "saved"

    return {"name": name, "saved_as": saved_as, "error": error, "kind": kind,
            "same_as": duplicate["same_as"] if duplicate else "", "duplicate_of": duplicate["run"] if duplicate else None}


def save_upload(stream: BinaryIO,
                inbox_root: Path,
                filename: str) -> Path:
    """Copy an uploaded file into the inbox and return where it landed."""

    inbox_root.mkdir(parents=True, exist_ok=True)
    # The part file is created only when it is not there: two uploads of one name at once both find the name free,
    # and the one that comes second takes the next free name instead of writing into the first one's file
    while True:
        target = choose_upload_path(inbox_root, filename)
        partial = target.with_name(target.name + _PARTIAL_SUFFIX)
        try:
            claimed = partial.open("xb")
            break
        except FileExistsError:
            continue
    try:
        with claimed:
            sha256 = recording_hash_service.copy_with_hash(stream, claimed)
        duplicate = recording_hash_service.describe_duplicate(sha256, inbox_root)
        if duplicate is not None:
            raise DuplicateRecordingError(
                f"{upload_name_utils.read_upload_name(filename)} is "
                f"{recording_hash_service.render_duplicate_reason(duplicate)}", duplicate)
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)

    return target
