"""A recording's SHA-256, and the done run that processed the same recording, so one recording is not processed twice.

A file's hash is its SHA-256; a meeting recorded in parts hashes its parts in the order they are read. Hashes are kept
in `ui/recording-hashes.json` by path, with the size and modification time they were read at, so an unchanged file is
hashed once (a 294 MB recording takes about 0.2 s). A done run is one whose record holds every phase complete
(`run_recording_service.is_run_done`); its recording is found wherever it is now (`run_artifact_service.find_recording`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import BinaryIO

import config
from models.input_range_spec import InputRangeSpec
from services import inbox_service
from services import input_range_service
from services import recording_compression_service
from services import recording_parts_service
from services import run_artifact_service
from services import run_recording_service
from services import run_status_service
from utils import json_store_utils

__all__ = ["CACHE_PATH", "set_cache_path", "compute_recording_hash", "copy_with_hash", "find_done_run",
           "find_inbox_twin", "describe_duplicate", "build_upload_duplicate", "render_duplicate_reason"]

CACHE_PATH = config.UI_DIR / "recording-hashes.json"
_CHUNK_BYTES = 8 * 1024 * 1024


def set_cache_path(path: Path) -> None:
    """Where the hashes are kept; a test points it at its own folder."""

    global CACHE_PATH
    CACHE_PATH = path


def _read_cache() -> dict:
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    return cache if isinstance(cache, dict) else {}


def _add_file_to_digest(path: Path,
                        digest) -> None:
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)


def compute_recording_hash(recording: Path) -> str:
    """The recording's SHA-256: a video file's own, or its parts' in reading order for a meeting folder."""

    files = recording_parts_service.list_recording_parts(recording)
    key = str(recording.resolve())
    stamp = [[file.stat().st_size, file.stat().st_mtime_ns] for file in files]
    cache = _read_cache()
    if cache.get(key, {}).get("stamp") == stamp:
        return cache[key]["sha256"]
    digest = hashlib.sha256()
    for file in files:
        _add_file_to_digest(file, digest)
    cache[key] = {"stamp": stamp, "sha256": digest.hexdigest()}
    json_store_utils.write_json(CACHE_PATH, cache)

    return digest.hexdigest()


def copy_with_hash(stream: BinaryIO,
                   destination: BinaryIO) -> str:
    """Write the stream into the open file `destination` and return its SHA-256, read as it is written."""

    digest = hashlib.sha256()
    while chunk := stream.read(_CHUNK_BYTES):
        digest.update(chunk)
        destination.write(chunk)

    return digest.hexdigest()


def find_done_run(sha256: str,
                  excluded: Path | None = None,
                  excluded_names: frozenset[str] = frozenset(),
                  input_range: InputRangeSpec | None = None) -> dict | None:
    """The done run (`{root, name}`) whose recording has this SHA-256 and that processed the same part of it,
    `input_range` (None: the whole recording), leaving out the folder `excluded` and the folders named in
    `excluded_names`; None when no done run processed this part of this recording."""

    excluded_path = excluded.resolve() if excluded is not None else None
    for root_name, root in run_status_service.RUN_ROOTS.items():
        if not root.is_dir():
            continue
        for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
            if folder.resolve() == excluded_path or folder.name in excluded_names or not run_recording_service.is_run_done(
                    run_status_service.load_newest_record(folder)):
                continue
            # Another part of the same recording is another run
            if input_range_service.read_range(folder=folder) != input_range:
                continue
            # A kept recording compressed in place is still the recording it was before
            if recording_compression_service.read_compression(folder).get("original_sha256") == sha256:
                return {"root": root_name, "name": folder.name}
            recording = run_artifact_service.find_recording(folder, run_status_service.describe_run_video(folder))
            if recording is not None and compute_recording_hash(recording) == sha256:
                return {"root": root_name, "name": folder.name}

    return None


def find_inbox_twin(sha256: str,
                    inbox_dir: Path) -> Path | None:
    """The video waiting in the inbox that is this recording (the same SHA-256), or None."""

    for video in inbox_service.list_inbox_videos(inbox_dir):
        if compute_recording_hash(video) == sha256:
            return video

    return None


def describe_duplicate(sha256: str,
                       inbox_dir: Path) -> dict | None:
    """Why a new copy of this recording is not taken, or None: `kind` is `processed` (a done run processed it; `run` is
    its `{root, name}`) or `in_inbox` (the inbox holds it); `same_as` names that run or inbox video."""

    done = find_done_run(sha256)
    if done is not None:
        return {"kind": "processed", "same_as": f"{done['root']}/{done['name']}", "run": done}
    twin = find_inbox_twin(sha256, inbox_dir)
    if twin is not None:
        return {"kind": "in_inbox", "same_as": twin.name, "run": None}

    return None


def build_upload_duplicate(same_as: str) -> dict:
    """A `describe_duplicate` answer of kind `in_upload`: the same recording came earlier in one drop, as `same_as`."""

    return {"kind": "in_upload", "same_as": same_as, "run": None}


def render_duplicate_reason(duplicate: dict) -> str:
    """Why a copy is not taken, as the upload's refusal says it after "<name> is": for a `describe_duplicate` answer, or
    `in_upload` (the same recording came earlier in one drop)."""

    same_as = duplicate["same_as"]

    return {"processed": f"a recording processed already: {same_as} is done",
            "in_inbox": f"already in the inbox as {same_as}",
            "in_upload": f"the same recording as {same_as} in this upload"}[duplicate["kind"]]
