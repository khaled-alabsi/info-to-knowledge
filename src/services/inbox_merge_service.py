"""Inbox recordings merged into one recording in parts: the person ticks the files
that are one meeting and presses the inbox's merge button, in place of being asked at every upload.

The files move into a folder of their own in the inbox, which the program reads as one recording in parts
(`recording_parts_service`); each gets `01 `, `02 ` in front, in the natural order of their names, so the order the
pipeline reads is the one the inbox showed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from models.ui_errors import UploadRefusedError
from services import inbox_service
from services import recording_parts_service
from utils import upload_name_utils

__all__ = ["merge_recordings", "suggest_merged_name"]

# "Weekly part " -> "Weekly"; the part word follows a separator, so "Workshop " stays "Workshop", never "Worksho"
_PART_WORD_END = re.compile(r"(?:[\s._-]+(?:part|teil|p))?[\s._-]*$", re.IGNORECASE)
_MIN_PART_DIGITS = 2                                                              # "01 ", "02 ": names sort as numbers


def suggest_merged_name(names: list[str]) -> str:
    """The beginning the recordings' names share, without a trailing part word or separator ("Weekly part 1.mp4" and
    "Weekly part 2.mp4" make "Weekly"); the first name's stem when they share none."""

    stems = [Path(name).stem for name in names]
    prefix = stems[0]
    for stem in stems[1:]:
        while not stem.startswith(prefix):
            prefix = prefix[:-1]

    return _PART_WORD_END.sub("", prefix).strip() or stems[0]


def merge_recordings(inbox_root: Path,
                     videos: list[Path],
                     name: str = "") -> Path:
    """Move the inbox recordings `videos` (two or more single files, all video or all audio) into one new folder of the
    inbox, `name` or the name they share, their parts numbered in natural name order; returns the folder. A recording the
    inbox does not list is `UnknownInboxItemError`; anything else that cannot be merged is `UploadRefusedError`, named."""

    # STEP 1: every recording is one the inbox lists, a single file, not a PDF, of one kind, each named once
    resolved = [inbox_service.resolve_listed_video(inbox_root, video) for video in videos]
    if len(set(resolved)) < len(resolved):
        raise UploadRefusedError("a recording is named twice")
    if len(resolved) < 2:
        raise UploadRefusedError("merging takes two or more recordings")
    for recording in resolved:
        if recording.is_dir():
            raise UploadRefusedError(f"{recording.name} is a recording in parts already: merge single files")
        if inbox_service.is_document(recording):
            kind = "PDF" if inbox_service.describe_file_type(recording) == "pdf" else "Word or OpenDocument file"
            raise UploadRefusedError(f"{recording.name} is a {kind}: only videos or audio recordings are merged")
    if len({inbox_service.is_audio_recording(recording) for recording in resolved}) > 1:
        raise UploadRefusedError("a video and an audio recording are not one recording: merge videos or audio alone")
    folder_name = (name.strip() or suggest_merged_name([recording.name for recording in resolved]))
    if "/" in folder_name or "\\" in folder_name or folder_name.startswith("."):
        raise UploadRefusedError(f"{folder_name!r} is no name: no folders, and it may not start with a dot")

    # STEP 2: the folder, and each part moved into it numbered in name order
    folder = upload_name_utils.choose_free_folder(inbox_root, folder_name)
    folder.mkdir(parents=True)
    ordered = sorted(resolved, key=lambda recording: recording_parts_service.build_natural_key(recording.name))
    width = max(_MIN_PART_DIGITS, len(str(len(ordered))))
    for position, recording in enumerate(ordered, start=1):
        shutil.move(str(recording), str(folder / f"{position:0{width}d} {recording.name}"))

    return folder
