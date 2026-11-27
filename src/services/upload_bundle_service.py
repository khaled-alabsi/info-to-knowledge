"""An upload of several files, a folder or a zip: every recording in it saved into the inbox as a recording of its own,
with no question asked (the owner found asking which files are one meeting "annoying",
since it is the rare case; recordings that are one meeting are merged in the inbox afterwards, `inbox_merge_service`).

The files are first held in `inbox/.staging/<id>/`, which the inbox never lists (a dot folder), while a zip is
unpacked (only its recordings and transcripts, each under its own name, nothing outside the holding folder) and every
recording is checked against the runs done and the inbox. Then each recording moves into `inbox/`, and into a folder
`inbox/<its stem>/` with its transcript when a transcript of the same name came with it. The holding folder is removed
whatever happens.
"""

from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from models.ui_errors import UploadRefusedError
from services import inbox_service
from services import recording_hash_service
from services import upload_service
from utils import upload_name_utils

__all__ = ["STAGING_DIR_NAME", "TRANSCRIPT_SUFFIXES", "save_upload_bundle"]

STAGING_DIR_NAME = ".staging"                  # the inbox's dot folder an upload is held in while it is checked
TRANSCRIPT_SUFFIXES = (".vtt", ".srt")         # a meeting tool's transcript, kept beside its recording
_STAGING_ID_LENGTH = 12                        # an upload's holding folder is named by this many hex digits
_CHUNK_BYTES = 8 * 1024 * 1024                 # bytes copied at a time from an upload's stream


def _read_kind(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in inbox_service.RECORDING_SUFFIXES:
        return "recording"
    if suffix in TRANSCRIPT_SUFFIXES:
        return "transcript"

    return None


def _keep_file(staging_dir: Path,
               manifest: list[dict],
               stream: BinaryIO,
               original: str) -> None:
    name = upload_name_utils.read_upload_name(original)
    kind = _read_kind(name) if name else None
    if kind is None:
        return
    stored = upload_name_utils.choose_free_path(staging_dir, name).name
    with (staging_dir / stored).open("wb") as written:
        shutil.copyfileobj(stream, written, _CHUNK_BYTES)
    manifest.append({"stored": stored, "original": original.replace("\\", "/"), "kind": kind})


def _unpack_zip(staging_dir: Path,
                manifest: list[dict],
                archive_path: Path,
                original: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if member.is_dir() or "__MACOSX" in path.parts or path.name.startswith("."):
                continue
            with archive.open(member) as stream:
                _keep_file(staging_dir, manifest, stream, f"{PurePosixPath(original).stem}/{member.filename}")


def _write_held_files(staging_dir: Path,
                uploads: list[tuple[BinaryIO, str]]) -> list[dict]:
    """Every recording and transcript of the upload written into the holding folder, each zip unpacked; the manifest
    of what is held. Refused when nothing in it is a recording."""

    manifest: list[dict] = []
    for stream, original in uploads:
        if PurePosixPath(original).suffix.lower() == ".zip":
            archive_path = staging_dir / ".upload.zip"
            with archive_path.open("wb") as written:
                shutil.copyfileobj(stream, written, _CHUNK_BYTES)
            try:
                _unpack_zip(staging_dir, manifest, archive_path, original)
            except zipfile.BadZipFile as exc:
                raise UploadRefusedError(f"{PurePosixPath(original).name} is not a zip that can be opened") from exc
            archive_path.unlink()
        else:
            _keep_file(staging_dir, manifest, stream, original)
    if not any(entry["kind"] == "recording" for entry in manifest):
        raise UploadRefusedError("nothing in the upload is a video, an audio file or a PDF: the inbox takes "
                                 + ", ".join(inbox_service.RECORDING_SUFFIXES))

    return manifest


def _drop_seen_recordings(inbox_root: Path,
                          staging_dir: Path,
                          manifest: list[dict]) -> list[dict]:
    """The recordings seen before taken out of the manifest and the holding folder, each described as refused with the
    run or the inbox recording it is: one a done run processed, one the inbox holds (the same SHA-256), and a second
    copy within this upload."""

    refused = []
    held_by_hash: dict[str, str] = {}
    for entry in [entry for entry in manifest if entry["kind"] == "recording"]:
        sha256 = recording_hash_service.compute_recording_hash(staging_dir / entry["stored"])
        name = PurePosixPath(entry["original"]).name
        duplicate = recording_hash_service.describe_duplicate(sha256, inbox_root)
        if duplicate is None and sha256 in held_by_hash:
            duplicate = recording_hash_service.build_upload_duplicate(held_by_hash[sha256])
        held_by_hash.setdefault(sha256, name)
        if duplicate is not None:
            (staging_dir / entry["stored"]).unlink()
            manifest.remove(entry)
            refused.append(upload_service.describe_upload(
                name, error=f"{name} is {recording_hash_service.render_duplicate_reason(duplicate)}", duplicate=duplicate))

    return refused


def _move_into_inbox(inbox_root: Path,
                     staging_dir: Path,
                     manifest: list[dict]) -> tuple[list[str], list[dict]]:
    """Each held recording moved into the inbox as its own, into a folder of its stem with its transcripts when a
    transcript of the same name (letter case aside) was held; the names the inbox now lists, and each transcript that
    went with no recording, described as refused."""

    transcripts = [entry for entry in manifest if entry["kind"] == "transcript"]
    added, used = [], set()
    for entry in [entry for entry in manifest if entry["kind"] == "recording"]:
        stem = PurePosixPath(entry["stored"]).stem
        own = [transcript["stored"] for transcript in transcripts
               if PurePosixPath(transcript["stored"]).stem.casefold() == stem.casefold()]
        if own:
            folder = upload_name_utils.choose_free_folder(inbox_root, stem)
            folder.mkdir(parents=True)
            for stored in [entry["stored"], *own]:
                shutil.move(str(staging_dir / stored), str(folder / stored))
            used.update(own)
            added.append(folder.name)
        else:
            target = upload_name_utils.choose_free_path(inbox_root, entry["stored"])
            shutil.move(str(staging_dir / entry["stored"]), str(target))
            added.append(target.name)
    unused = [upload_service.describe_upload(PurePosixPath(transcript["original"]).name,
                                             error="no recording of the same name came with this transcript")
              for transcript in transcripts if transcript["stored"] not in used]

    return added, unused


def save_upload_bundle(inbox_root: Path,
                       uploads: list[tuple[BinaryIO, str]]) -> dict:
    """Save every recording of an upload of several files, a folder or a zip (each a stream and its name or path in the
    dropped folder) into the inbox as its own: `{"added": [names the inbox lists], "refused": [...]}`, a refused entry
    for each recording seen before (with the run or inbox recording it is) and each transcript no recording of its
    name came with. Refused whole when nothing in it is a recording."""

    staging_dir = inbox_root / STAGING_DIR_NAME / uuid.uuid4().hex[:_STAGING_ID_LENGTH]
    staging_dir.mkdir(parents=True)
    # Whatever ends the upload, the holding folder goes with it: nothing else ever reads it
    try:
        manifest = _write_held_files(staging_dir, uploads)
        refused = _drop_seen_recordings(inbox_root, staging_dir, manifest)
        added, unused = _move_into_inbox(inbox_root, staging_dir, manifest)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return {"added": added, "refused": [*refused, *unused]}
