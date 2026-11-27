"""Renaming an inbox video. The page offers it only before a run has used the name; this service renames whatever
it is asked to.

The new name stays in the same folder (the inbox, or the video's meeting folder), keeps a video suffix (the old one
when none is typed), and never replaces a file that is there.
"""

from __future__ import annotations

from pathlib import Path

from models.ui_errors import UploadRefusedError
from services import inbox_service

__all__ = ["rename_inbox_video"]


def _is_name_taken(target: Path,
                   resolved: Path) -> bool:
    """Whether another file holds the name: on a disk that ignores letter case, `Talk.mp4` is `talk.mp4` itself."""

    return target.exists() and not target.samefile(resolved)


def rename_inbox_video(inbox_root: Path,
                       video: Path,
                       new_name: str) -> Path:
    """Rename one video the inbox lists to `new_name` and return its new path."""

    resolved = inbox_service.resolve_listed_video(inbox_root, video)
    name = new_name.strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise UploadRefusedError(f"{new_name!r} is no file name: no folders, and it may not start with a dot")
    # A meeting in parts is its folder: the folder is renamed as typed, its parts keep their names and order. A file keeps
    # its suffix when the new name has none, and must end in a recording's suffix.
    if not resolved.is_dir():
        if not Path(name).suffix:
            name += resolved.suffix
        if Path(name).suffix.lower() not in inbox_service.RECORDING_SUFFIXES:
            raise UploadRefusedError(f"{name} is not a video or an audio file name: the inbox takes "
                                     + ", ".join(inbox_service.RECORDING_SUFFIXES))
    target = resolved.with_name(name)
    if target == resolved:
        return resolved
    if _is_name_taken(target, resolved):
        raise UploadRefusedError(f"{name} is already in the inbox")
    resolved.rename(target)

    return target
