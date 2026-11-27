"""The recording a run keeps in its own folder once the run is 100% done.

A run whose record holds every phase complete moves its recording out of the inbox into `<run folder>/recording/`: a
video file, or a meeting's folder whole. `recording/.origin.json` names the path it had in the inbox, which is the path
the run record names, so the run, the UI and a second run folder of the same video find it again. While it is there,
phases 1 and 2 (which read it) do not run; `move_recording_to_inbox` puts it back where it was, and then they can.
A video given from outside the inbox is the person's own file and never moves.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import BinaryIO

from models import output_contract
from models.ui_errors import UploadRefusedError
from services import inbox_move_service
from services import inbox_service
from utils import json_store_utils

__all__ = ["RECORDING_DIR", "is_run_done", "would_run_recording_phases", "find_run_recording",
           "find_stored_recording", "read_origin_video", "find_recording_by_origin", "can_keep_recording", "keep_recording_if_done",
           "move_recording_to_inbox", "move_recording_to_trash", "save_uploaded_recording"]

RECORDING_DIR = "recording"
_ORIGIN_FILE = ".origin.json"
# The phases that read the recording itself: the transcript and the frames
_RECORDING_PHASES = (output_contract.PHASE_NUMBERS["transcribe"], output_contract.PHASE_NUMBERS["frames_preparing"])
_CHUNK_BYTES = 8 * 1024 * 1024


def _read_origin(run_folder: Path) -> dict:
    try:
        origin = json.loads((run_folder / RECORDING_DIR / _ORIGIN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    return origin if isinstance(origin, dict) else {}


def _choose_free_target(folder: Path,
                        name: str) -> Path:
    """`folder / name`, numbered before its suffix while that is taken: nothing in the inbox is replaced."""

    # Only a video's suffix is kept apart: a meeting folder `Weekly v1.2` becomes `Weekly v1.2-2`
    suffix = Path(name).suffix if Path(name).suffix.lower() in inbox_service.RECORDING_SUFFIXES else ""
    stem = name[:len(name) - len(suffix)] if suffix else name
    target, number = folder / name, 2
    while target.exists():
        target = folder / f"{stem}-{number}{suffix}"
        number += 1

    return target


def is_run_done(record: dict) -> bool:
    """The run record's status is complete and every phase the reader's document depends on is complete (a held review
    is not; one switched off, "off", is). Phase 6 is not among them: it writes rules for this program's own
    instructions, and it is switchable, so a run whose phase 6 is off or held is a finished run."""

    phases = {output_contract.RETIRED_STEP_NAMES.get(name, name): status
              for name, status in record.get("phases", {}).items()}

    # A review switched off (`review.isOn`) is recorded "off": the document is then phase 4's, and the run is done
    return record.get("status") == "complete" and all(
        phases.get(row["phase"]) == "complete"
        or (number == output_contract.PHASE_NUMBERS["review"] and phases.get(row["phase"]) == "off")
        for number, row in output_contract.PHASE_FILES.items() if number <= output_contract.LAST_DOCUMENT_PHASE)


def would_run_recording_phases(first_phase: int,
                               phases: dict[str, str],
                               is_resuming: bool) -> bool:
    """Whether a run asked to start at `first_phase` runs phase 1 or 2: a new run from either, or a resume where either
    is not complete in the record."""

    asked = [number for number in _RECORDING_PHASES if number >= first_phase]
    if not is_resuming:
        return bool(asked)
    named = {output_contract.RETIRED_STEP_NAMES.get(name, name): status for name, status in phases.items()}

    return any(named.get(output_contract.PHASE_FILES[number]["phase"]) != "complete" for number in asked)


def find_stored_recording(run_folder: Path) -> Path | None:
    """The recording kept in the run folder, or None when it keeps none."""

    origin = _read_origin(run_folder)
    if not origin.get("name"):
        return None
    entry = run_folder / RECORDING_DIR / origin["name"]
    recording = entry / origin["inner"] if origin.get("inner") else entry

    return recording if recording.exists() else None


def find_run_recording(run_folder: Path,
                       record: dict) -> Path | None:
    """The recording a converted run reads for its figures: the one the run keeps in its folder, else the one its run
    record names while it is still there; None when neither is."""

    stored = find_stored_recording(run_folder)
    if stored is not None:
        return stored
    if not record.get("repository") or not record.get("module"):
        return None
    named = Path(record["repository"]) / record["module"]

    return named if named.exists() else None


def read_origin_video(run_folder: Path) -> str:
    """The inbox path the recording the run keeps came from; empty when it keeps none."""

    return str(_read_origin(run_folder).get("video") or "")


def find_recording_by_origin(video: str,
                             roots: list[Path]) -> Path | None:
    """Where the inbox video a run record names is kept now, in the `recording/` of any run folder under `roots`."""

    resolved = str(Path(video).resolve())
    for root in roots:
        for origin_file in sorted(root.glob(f"*/{RECORDING_DIR}/{_ORIGIN_FILE}")):
            run_folder = origin_file.parent.parent
            # An origin left by a move cut short names a recording its folder no longer keeps: look on
            stored = find_stored_recording(run_folder) if _read_origin(run_folder).get("video") == resolved else None
            if stored is not None:
                return stored

    return None


def can_keep_recording(record: dict,
                       inbox_dir: Path,
                       video: Path) -> bool:
    """Whether the run is done and its recording is an inbox video, so it can be kept in the run folder now."""

    return is_run_done(record) and video.resolve() in {listed.resolve()
                                                       for listed in inbox_service.list_inbox_videos(inbox_dir)}


def keep_recording_if_done(record: dict,
                           inbox_dir: Path,
                           run_folder: Path,
                           video: Path) -> Path | None:
    """Move the run's recording into its folder's `recording/` when the run is done, the recording is an inbox video and
    the folder keeps none yet; the path it has there, or None when it stays."""

    # 1. Only a done run, only a video the inbox lists, and one recording a folder: `.origin.json` names one
    resolved = video.resolve()
    if not can_keep_recording(record, inbox_dir, video) or find_stored_recording(run_folder) is not None:
        return None

    # 2. A video in a meeting folder of its own moves with the folder.
    moved = resolved if resolved.parent == inbox_dir.resolve() else resolved.parent
    target_dir = run_folder / RECORDING_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _choose_free_target(target_dir, moved.name)
    inner = "" if moved == resolved else str(resolved.relative_to(moved))
    # The origin first, atomically: written after the move, a kill between the two left a recording nobody finds
    origin = target_dir / _ORIGIN_FILE
    json_store_utils.write_json(origin, {"video": str(resolved), "name": target.name, "inbox_name": moved.name,
                                         "inner": inner})
    try:
        shutil.move(str(moved), str(target))
    except OSError:
        origin.unlink(missing_ok=True)
        raise

    return target / inner if inner else target


def move_recording_to_inbox(inbox_dir: Path,
                            run_folder: Path) -> Path:
    """Move the recording the run folder keeps back into the inbox under the name it had there (numbered when that is
    taken now) and return the video's path; raises FileNotFoundError when the folder keeps none."""

    origin = _read_origin(run_folder)
    if find_stored_recording(run_folder) is None:
        raise FileNotFoundError(f"{run_folder} keeps no recording in {RECORDING_DIR}/")
    inbox_dir.mkdir(parents=True, exist_ok=True)
    target = _choose_free_target(inbox_dir, origin.get("inbox_name") or origin["name"])
    shutil.move(str(run_folder / RECORDING_DIR / origin["name"]), str(target))
    (run_folder / RECORDING_DIR / _ORIGIN_FILE).unlink()
    # The folder this module made goes when it is empty; anything else put there stays
    if not any((run_folder / RECORDING_DIR).iterdir()):
        (run_folder / RECORDING_DIR).rmdir()

    return target / origin["inner"] if origin.get("inner") else target


def move_recording_to_trash(inbox_dir: Path,
                            run_folder: Path,
                            video: Path) -> Path:
    """Delete the run's recording the reversible way: into `inbox/.trash/`, where Restore puts it back in the inbox. A
    recording kept in the run folder goes back to the inbox first, so the trash names the inbox path the run names."""

    if find_stored_recording(run_folder) is not None:
        video = move_recording_to_inbox(inbox_dir, run_folder)

    return inbox_move_service.move_out_of_inbox(inbox_dir, video, inbox_move_service.TRASH)


def save_uploaded_recording(stream: BinaryIO,
                            filename: str,
                            inbox_dir: Path,
                            run_folder: Path,
                            record: dict) -> Path:
    """Save an uploaded file as the recording the run record names, at the inbox path it names (whatever the upload
    was called), and keep it in the run folder at once when the run is done; where it landed. Refused by name when the
    recording is there already, the file is no video, or the run names a path outside the inbox or a meeting in parts."""

    # 1. The run's own recording path, inside the inbox, one video file.
    if not record.get("repository") or not record.get("module"):
        raise UploadRefusedError(f"{run_folder.name} has no run record naming its recording")
    named = Path(record["repository"]) / record["module"]
    if not named.resolve().is_relative_to(inbox_dir.resolve()):
        raise UploadRefusedError(f"the run names {named}, outside the inbox: put the recording there")
    if named.suffix.lower() not in inbox_service.RECORDING_SUFFIXES:
        raise UploadRefusedError(f"the run names {named.name}, a meeting recorded in parts: put its parts in that folder")
    if Path(filename).suffix.lower() not in inbox_service.RECORDING_SUFFIXES:
        raise UploadRefusedError(f"{filename} is not a video or an audio file: the inbox takes "
                                 + ", ".join(inbox_service.RECORDING_SUFFIXES))
    if (named.exists() or find_stored_recording(run_folder) is not None
            or find_recording_by_origin(str(named), [run_folder.parent]) is not None):
        raise UploadRefusedError(f"the recording of {run_folder.name} is there already")

    # 2. Written to `.part` first, so a half-sent upload is never listed as a video.
    named.parent.mkdir(parents=True, exist_ok=True)
    partial = named.with_name(named.name + ".part")
    try:
        with partial.open("wb") as written:
            shutil.copyfileobj(stream, written, _CHUNK_BYTES)
        partial.rename(named)
    finally:
        partial.unlink(missing_ok=True)

    # 3. A done run keeps its recording in its folder, as when it ended.
    return keep_recording_if_done(record, inbox_dir, run_folder, named) or named
