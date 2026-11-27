"""Make the recording a done run keeps in its folder smaller, in place.

Once a run is done its recording is only for watching, and a meeting can be 8 GB. Each video of the kept recording (a
file, or every part of a meeting's folder) is re-encoded by ffmpeg to at most 720 lines high, H.264 and AAC (VP9 and
Opus for a `.webm`), into a copy beside it; the copy replaces the file only when it is smaller, under the same name, so
the run record, `.origin.json` and the page find it as before. `recording/.compression.json` is the record: its status,
the process doing it, each file's size before and after, and the SHA-256 the recording had before, which
`recording_hash_service.find_done_run` matches too, so the original dropped into the inbox again is still known.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import av

from services import run_recording_service
from utils import json_store_utils
from utils import process_utils

__all__ = ["TARGET_HEIGHT", "COMPRESSION_FILE", "COMPRESSION_LOG_FILE", "build_ffmpeg_command", "shrink_video",
           "read_compression", "write_compression", "describe_compression", "COMPRESSION_RUNNING", "COMPRESSION_DONE", "COMPRESSION_FAILED",
           "is_compression_running"]

# HD: enough to see a shared screen's text and follow the meeting
TARGET_HEIGHT = 720
COMPRESSION_FILE = ".compression.json"
# The console of a compression run in a process of its own, beside its record
COMPRESSION_LOG_FILE = ".compression.log"
# The containers that hold H.264 and AAC; `.webm` holds VP9 and Opus. Any other kind of file is left as it is
_H264_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv", ".ts", ".flv")
_VP9_SUFFIXES = (".webm",)
_COPY_MARK = ".compressing"


def build_ffmpeg_command(source: Path,
                         target: Path) -> list[str] | None:
    """ffmpeg's line that writes `source` at most TARGET_HEIGHT lines high into `target`; None for a kind of file it
    does not re-encode."""

    suffix = source.suffix.lower()
    # A frame never grows: a recording already under 720 lines keeps its size and is only re-encoded
    scale = ["-vf", f"scale=-2:min({TARGET_HEIGHT}\\,ih)"]
    if suffix in _H264_SUFFIXES:
        codecs = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "96k"]
        codecs += ["-movflags", "+faststart"] if suffix in (".mp4", ".mov", ".m4v") else []
    elif suffix in _VP9_SUFFIXES:
        codecs = ["-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus", "-b:a", "96k"]
    else:
        return None

    # `-progress pipe:1` writes `out_time_us=<microseconds encoded>` lines on stdout, read for the page's percentage
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", *scale, *codecs, str(target)]


def _read_duration_s(source: Path) -> float:
    """The video's length in seconds from its container, 0.0 when it does not say."""

    try:
        with av.open(str(source)) as container:
            return (container.duration or 0) / av.time_base
    except av.FFmpegError:
        return 0.0


def _run_reporting_progress(command: list[str],
                            duration_s: float,
                            on_progress: Callable[[float], None]) -> tuple[int, str]:
    """Run ffmpeg, handing `on_progress` the share of `duration_s` encoded each time it says how far it is, and 1.0 at
    a clean end. Returns the exit code and ffmpeg's error text."""

    # stderr to a file: a pipe nobody reads while stdout is read could fill and stall ffmpeg
    with tempfile.TemporaryFile(mode="w+") as errors:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True)
        except OSError as error:
            raise RuntimeError(f"ffmpeg could not start ({command[0]}): {error}") from error
        try:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and duration_s > 0:
                    on_progress(min(1.0, int(value) / 1e6 / duration_s))
        except BaseException:
            # A progress that cannot be recorded must not leave the encode running on its own
            process.kill()
            process.wait()
            raise
        exit_code = process.wait()
        errors.seek(0)
        error_text = errors.read()
    if exit_code == 0:
        on_progress(1.0)

    return exit_code, error_text


def shrink_video(source: Path,
                 on_progress: Callable[[float], None] = lambda share: None) -> dict:
    """Re-encode one video beside itself and put the copy in its place when it is smaller. Returns the file's entry for
    the record: its name, its size before and after, and what happened (`compressed`, `kept: not smaller`, or
    `kept: <suffix> is not re-encoded`). `on_progress` hears the share encoded so far, 0 to 1. Raises when ffmpeg
    fails; the original is then untouched."""

    target = source.with_name(f".{source.stem}{_COPY_MARK}{source.suffix}")
    before = source.stat().st_size
    command = build_ffmpeg_command(source, target)
    if command is None:
        return {"name": source.name, "before_bytes": before, "after_bytes": before,
                "outcome": f"kept: {source.suffix} is not re-encoded"}
    try:
        exit_code, error_text = _run_reporting_progress(command, _read_duration_s(source), on_progress)
        if exit_code != 0:
            raise RuntimeError(f"ffmpeg failed on {source.name}: {error_text.strip()[-600:]}")
        after = target.stat().st_size
        if after >= before:
            return {"name": source.name, "before_bytes": before, "after_bytes": before, "outcome": "kept: not smaller"}
        os.replace(target, source)
    finally:
        target.unlink(missing_ok=True)

    return {"name": source.name, "before_bytes": before, "after_bytes": after, "outcome": "compressed"}


def _build_record_path(run_folder: Path) -> Path:
    return run_folder / run_recording_service.RECORDING_DIR / COMPRESSION_FILE


def read_compression(run_folder: Path) -> dict:
    """The run folder's compression record, or {} when its recording was never compressed."""

    try:
        record = json.loads(_build_record_path(run_folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    return record if isinstance(record, dict) else {}


def write_compression(run_folder: Path,
                      record: dict) -> None:
    # Atomic, as every record is: a kill while it wrote left a half file, read as never compressed, and the hash the
    # recording had before its bytes were replaced was gone
    json_store_utils.write_json(_build_record_path(run_folder), record)


# The status a compression's record holds while its process re-encodes
COMPRESSION_RUNNING = "running"
COMPRESSION_DONE = "done"
COMPRESSION_FAILED = "failed"
COMPRESSION_STOPPED = "stopped"


def describe_compression(run_folder: Path) -> dict:
    """What the page shows: the record's status (`running`, `done`, `failed`, or `stopped` for a run whose process is
    gone), the total size before and after, the share of the recording encoded so far (0 to 1), and the error. {} when the recording was never compressed."""

    record = read_compression(run_folder)
    if not record:
        return {}
    status = record.get("status", "")
    if status == COMPRESSION_RUNNING and not process_utils.is_process_alive(int(record.get("pid") or 0)):
        status = COMPRESSION_STOPPED
    files = record.get("files", [])

    return {"status": status, "error": record.get("error", ""),
            "before_bytes": sum(file["before_bytes"] for file in files),
            "after_bytes": sum(file["after_bytes"] for file in files),
            "files": len(files), "progress": float(record.get("progress") or 0.0)}


def is_compression_running(run_folder: Path) -> bool:
    """Whether the folder's recording is being compressed now, by a process still alive."""

    return describe_compression(run_folder).get("status") == COMPRESSION_RUNNING
