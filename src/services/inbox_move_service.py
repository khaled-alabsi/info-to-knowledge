"""Moving inbox videos out of the inbox into one of its two dot folders, and back: `inbox/.trash/` for what is not
wanted, `inbox/.archive/` for what is done with. `i2k inbox` and the UI's inbox lists see neither, and nothing is
deleted. Only the recording moves: its run folders under `output/` and `knowledge/` stay where they are. A run folder
no inbox video owns moves there whole, and goes back to the folder it came from.

A video that sits in a meeting folder of its own (the recording beside the meeting tool's transcript, R14) is moved
with its folder. A name already there gets a number, so nothing is replaced. Each folder keeps `.origins.json`: for
every entry, the video path the inbox listed (the one its run records name) and the name it had in the inbox, so an
archived video still finds its runs and recording, and comes back under its own name.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from models.ui_errors import UnknownInboxItemError, UnreadableOriginsError
from services import inbox_service
from utils import json_store_utils

__all__ = ["TRASH", "ARCHIVE", "PLACE_DIRS", "move_out_of_inbox", "move_run_folder_out", "list_moved", "move_back",
           "find_moved_video", "delete_from_trash"]

_LOG = logging.getLogger(__name__)

TRASH = "trash"
ARCHIVE = "archive"
# where a video can be moved to -> its folder in the inbox
PLACE_DIRS = {TRASH: ".trash", ARCHIVE: ".archive"}
_ORIGINS_FILE = ".origins.json"


def _build_place_dir(inbox_root: Path,
                     place: str) -> Path:
    if place not in PLACE_DIRS:
        raise UnknownInboxItemError(f"{place!r} is no place a video moves to: {', '.join(PLACE_DIRS)}")

    return inbox_root / PLACE_DIRS[place]


def _read_origins(place_dir: Path) -> dict[str, dict]:
    """Where each entry of the place came from. No record yet is none; a record that does not read stops the move by
    name, since writing over it would forget where every entry in it came from."""

    path = place_dir / _ORIGINS_FILE
    try:
        origins = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise UnreadableOriginsError(f"{path} could not be read ({exc}): repair or remove it by hand, nothing was changed") from exc
    if not isinstance(origins, dict):
        raise UnreadableOriginsError(f"{path} holds no record of names: repair or remove it by hand, nothing was changed")

    return origins


def _read_origins_to_show(place_dir: Path) -> tuple[dict[str, dict], bool]:
    """The record for a listing, and whether it read: a place whose record does not read is still listed, with no
    origins, and its record is left as it is."""

    try:
        return _read_origins(place_dir), True
    except UnreadableOriginsError as exc:
        _LOG.warning("%s: listed with no origins, so a video moved there shows no run (%s)", place_dir, exc)
        return {}, False


def _write_origins(place_dir: Path,
                   origins: dict[str, dict]) -> None:
    json_store_utils.write_json(place_dir / _ORIGINS_FILE, origins)


def _describe_moved_entry(path: Path,
                          origin: dict) -> dict:
    """One entry of a place's folder for a listing. An entry moved before the time was recorded (or by hand) has the time
    its folder entry last changed."""

    moved_at = origin["moved_at"] if "moved_at" in origin else path.stat().st_ctime

    return {"name": path.name, "is_folder": path.is_dir(), "size_bytes": _count_bytes(path), "moved_at": moved_at,
            "video": origin.get("video", ""), "run_folder": origin.get("run_folder", "")}


def _find_entry(place_dir: Path,
                name: str,
                place: str) -> Path:
    """The entry `name` directly in the place's folder; a name outside it, hidden or missing is refused by name."""

    entry = (place_dir / name).resolve()
    if entry.parent != place_dir.resolve() or name.startswith(".") or not entry.exists():
        raise UnknownInboxItemError(f"{name} is not in the {place}")

    return entry


def _choose_free_target(folder: Path,
                        name: str) -> Path:
    """`name` in `folder`, or `<stem>-2<suffix>` and on when it is taken. Only a video's suffix is kept apart: a meeting
    folder named `Weekly.Review` becomes `Weekly.Review-2`."""

    suffix = Path(name).suffix if Path(name).suffix.lower() in inbox_service.RECORDING_SUFFIXES else ""
    stem = name[:len(name) - len(suffix)] if suffix else name
    target, number = folder / name, 2
    while target.exists():
        target = folder / f"{stem}-{number}{suffix}"
        number += 1

    return target


def _count_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size

    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _move_into_place(inbox_root: Path,
                     place: str,
                     source: Path,
                     origin_fields: dict[str, str]) -> Path:
    """Move `source` into the place's folder under a free name, record where it came from (`origin_fields` and its own
    name) and return where it landed."""

    place_dir = _build_place_dir(inbox_root, place)
    place_dir.mkdir(parents=True, exist_ok=True)
    origins = _read_origins(place_dir)
    target = _choose_free_target(place_dir, source.name)
    shutil.move(str(source), str(target))
    origins[target.name] = {**origin_fields, "name": source.name, "moved_at": time.time()}
    _write_origins(place_dir, origins)

    return target


def move_out_of_inbox(inbox_root: Path,
                      video: Path,
                      place: str) -> Path:
    """Move one inbox video (or its meeting folder) into the place's folder and return where it landed. Refused when
    the path is no video the inbox lists."""

    resolved = inbox_service.resolve_listed_video(inbox_root, video)
    moved = resolved if resolved.parent == inbox_root.resolve() else resolved.parent

    return _move_into_place(inbox_root, place, moved, {
        "video": str(resolved), "inner": "" if moved == resolved else str(resolved.relative_to(moved))})


def move_run_folder_out(inbox_root: Path,
                        run_folder: Path,
                        place: str) -> Path:
    """Move one run folder whole into the place's folder and return where it landed; its origin keeps the folder it
    came from, so `move_back` puts it there again."""

    return _move_into_place(inbox_root, place, run_folder, {"run_folder": str(run_folder.resolve())})


def list_moved(inbox_root: Path,
               place: str) -> list[dict]:
    """What the place's folder holds, the newest move first: a video or a meeting folder, its size, when it was moved
    there (`moved_at`, in seconds), the inbox video path its runs name ("" for an entry moved there before that
    was recorded), and for a run folder moved whole the path it came from (`run_folder`, "" for a video)."""

    place_dir = _build_place_dir(inbox_root, place)
    if not place_dir.is_dir():
        return []
    origins, is_readable = _read_origins_to_show(place_dir)
    # An entry removed by hand (in Finder) leaves no record behind: the record is put right whenever the folder is read
    present = {name: origin for name, origin in origins.items() if (place_dir / name).exists()}
    if is_readable and present != origins:
        _write_origins(place_dir, present)
        origins = present

    entries = [_describe_moved_entry(path, origins.get(path.name, {}))
               for path in place_dir.iterdir() if not path.name.startswith(".")]

    return sorted(entries, key=lambda entry: entry["moved_at"], reverse=True)


def move_back(inbox_root: Path,
              place: str,
              name: str) -> Path:
    """Move one entry of the place's folder back where it came from under the name it had there, numbered when that
    name is taken now: a video into the inbox, a run folder into the folder it was moved out of."""

    place_dir = _build_place_dir(inbox_root, place)
    source = _find_entry(place_dir, name, place)
    origins = _read_origins(place_dir)
    origin = origins.pop(source.name, {})
    home = Path(origin["run_folder"]).parent if origin.get("run_folder") else inbox_root
    target = _choose_free_target(home, origin.get("name") or source.name)
    shutil.move(str(source), str(target))
    _write_origins(place_dir, origins)

    return target


def find_moved_video(inbox_root: Path,
                     place: str,
                     video: str) -> Path | None:
    """Where an inbox video path a run record names sits now in the place's folder, or None when it is not there."""

    place_dir = _build_place_dir(inbox_root, place)
    resolved = str(Path(video).resolve())
    origins, _ = _read_origins_to_show(place_dir)
    for name, origin in origins.items():
        if origin.get("video") == resolved and (place_dir / name).exists():
            return place_dir / name / origin["inner"] if origin.get("inner") else place_dir / name

    return None


def delete_from_trash(inbox_root: Path,
                      name: str) -> None:
    """Delete one trash entry for good (a video, a meeting folder or a run folder moved there) and its record. Only an
    entry directly in `inbox/.trash/` is deleted: the trash page's Delete permanently, confirmed by the person."""

    place_dir = _build_place_dir(inbox_root, TRASH)
    target = _find_entry(place_dir, name, TRASH)
    origins = _read_origins(place_dir)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    if origins.pop(name, None) is not None:
        _write_origins(place_dir, origins)
