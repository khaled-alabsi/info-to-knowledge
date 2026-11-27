"""Compress the videos of many done runs, one after another (the Manager tab's Compress videos).

The run page compresses one run's kept recording at a time. This lists every done run whose recording is a video (a
PDF or an audio recording has no picture to shrink) with how far its compression is, and compresses the ticked ones
in one process of its own, each in turn, never two at once: ffmpeg on two 8 GB meetings at once takes every core.
A done run whose video still sits in the inbox is first kept in its run folder, as the run does when it ends, since
only a kept recording is compressed. `ui/compression-batch.json` names the batch's process, its run folders in order
and the one it is on, so the page shows the others as waiting.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import config
from services import inbox_service
from services import recording_compression_service
from services import recording_parts_service
from services import run_operations_service
from services import run_recording_service
from services import run_status_service
from utils import json_store_utils
from utils import process_utils

__all__ = ["BATCH_FILE_NAME", "VIDEO_NOT_COMPRESSED", "VIDEO_WAITING", "VIDEO_COMPRESSING", "VIDEO_COMPRESSED",
           "VIDEO_FAILED", "list_videos", "read_batch", "find_batch_refusal", "start_batch", "shrink_in_turn"]

BATCH_FILE_NAME = "compression-batch.json"

# A video's state on the page
VIDEO_NOT_COMPRESSED = "not compressed"
VIDEO_WAITING = "waiting"
VIDEO_COMPRESSING = "compressing"
VIDEO_COMPRESSED = "compressed"
VIDEO_FAILED = "failed"

_CHILD_CODE = ("import sys; sys.path.insert(0, {src!r}); from pathlib import Path; "
               "from services import batch_compression_service; "
               "sys.exit(batch_compression_service.shrink_in_turn([Path(path) for path in {folders!r}], print))")


def _build_batch_path() -> Path:
    return config.UI_DIR / BATCH_FILE_NAME


def _read_batch_file() -> dict:
    try:
        batch = json_store_utils.read_json(_build_batch_path())
    except (OSError, ValueError):
        return {}

    return batch if isinstance(batch, dict) else {}


def read_batch() -> dict:
    """The batch running now: its process, its run folders in order and the position of the one it is on; {} when no
    batch process is alive."""

    batch = _read_batch_file()
    if not process_utils.is_process_alive(int(batch.get("pid") or 0)):
        return {}

    return batch


def _find_video(folder: Path) -> tuple[Path | None, bool]:
    """The run's video and whether it still sits in the inbox; (None, False) for a run that is not done, whose
    recording is gone, or is a PDF or an audio recording."""

    record = run_status_service.load_newest_record(folder)
    if not run_recording_service.is_run_done(record):
        return None, False
    kept = run_recording_service.find_stored_recording(folder)
    if kept is not None:
        return (kept, False) if _is_video(kept) else (None, False)
    named = run_status_service.describe_run_video(folder)
    if named and run_recording_service.can_keep_recording(record, config.INBOX_DIR, Path(named)) and _is_video(Path(named)):
        return Path(named), True

    return None, False


def _is_video(recording: Path) -> bool:
    if inbox_service.is_audio_recording(recording):
        return False
    parts = recording_parts_service.list_recording_parts(recording)

    return bool(parts) and all(part.suffix.lower() in inbox_service.VIDEO_SUFFIXES for part in parts)


def _decide_state(folder: Path,
                  batch: dict) -> tuple[str, dict]:
    compression = recording_compression_service.describe_compression(folder)
    status = compression.get("status", "")
    if status == recording_compression_service.COMPRESSION_RUNNING:
        return VIDEO_COMPRESSING, compression
    if status == recording_compression_service.COMPRESSION_DONE:
        return VIDEO_COMPRESSED, compression
    # the batch's own video before its record says running (the process starting, an inbox video being kept) waits
    # too: read "not compressed", it could be ticked into a second batch
    folders = batch.get("folders", [])
    if str(folder) in folders and folders.index(str(folder)) >= int(batch.get("position") or 0):
        return VIDEO_WAITING, compression
    if status in (recording_compression_service.COMPRESSION_FAILED, recording_compression_service.COMPRESSION_STOPPED):
        return VIDEO_FAILED, compression

    return VIDEO_NOT_COMPRESSED, compression


def list_videos() -> list[dict]:
    """Every done run whose recording is a video, the most recently changed first: its name, the video's size, where it
    is (`run folder` or `inbox`) and its compression state with the sizes before and after and the share done."""

    batch = read_batch()
    rows = []
    for run in run_status_service.list_run_folders():
        folder = Path(run["folder"])
        video, is_in_inbox = _find_video(folder)
        if video is None:
            continue
        state, compression = _decide_state(folder, batch)
        rows.append({"root": run["root"], "name": run["name"], "display_name": run.get("display_name") or "",
                     "video": video.name, "is_in_inbox": is_in_inbox,
                     "size_bytes": sum(part.stat().st_size for part in recording_parts_service.list_recording_parts(video)),
                     "state": state, "progress": compression.get("progress", 0.0), "error": compression.get("error", ""),
                     "before_bytes": compression.get("before_bytes", 0), "after_bytes": compression.get("after_bytes", 0)})

    return rows


def find_batch_refusal(folders: list[Path]) -> str:
    """Why the batch cannot start, or "": a batch or a single compression runs (never two at once), nothing is ticked,
    or a ticked run's video is compressed already, is no video of a done run, or has a run writing into its folder."""

    if read_batch():
        return "a compression batch is running: wait until it ends"
    running = [run["name"] for run in run_status_service.list_run_folders()
               if recording_compression_service.is_compression_running(Path(run["folder"]))]
    if running:
        return f"{running[0]} is being compressed: wait until it ends"
    if not folders:
        return "no video is ticked"
    for folder in folders:
        video, _ = _find_video(folder)
        if video is None:
            return f"{folder.name} has no video of a done run"
        if recording_compression_service.describe_compression(folder).get("status") == recording_compression_service.COMPRESSION_DONE:
            return f"{folder.name} is compressed already"
        if run_status_service.list_live_pids(folder):
            return f"a run writes into {folder.name}"

    return ""


def start_batch(folders: list[Path]) -> int:
    """`shrink_in_turn` in a process of its own session, its lines in `ui/compression-batch.log`; the pid. The batch
    record is written before the process starts, so the page shows every ticked video as waiting at once."""

    log_path = config.UI_DIR / "compression-batch.log"
    code = _CHILD_CODE.format(src=str(config.PLAYBOOK_ROOT / "src"), folders=[str(folder) for folder in folders])
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=config.PLAYBOOK_ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**os.environ, "PYTHONUNBUFFERED": "1"})
    json_store_utils.write_json(_build_batch_path(), {"pid": process.pid, "folders": [str(folder) for folder in folders],
                                                      "position": 0})

    return process.pid


def shrink_in_turn(folders: list[Path],
                     report=lambda line: None) -> int:
    """Each run folder's video compressed in turn, the batch record moved on before each: an inbox video kept in its
    run folder first. A video that fails is reported and the next one goes on; 1 when any failed, else 0."""

    failed = 0
    for position, folder in enumerate(folders):
        batch = _read_batch_file()
        if batch.get("pid") == os.getpid():
            json_store_utils.write_json(_build_batch_path(), {**batch, "position": position})
        report(f"[{position + 1}/{len(folders)}] {folder.name}")
        if run_recording_service.find_stored_recording(folder) is None:
            kept = run_operations_service.keep_recording_in_run(folder)
            for line in kept.output:
                report(f"  {line}")
            if kept.exit_code != 0:
                failed += 1
                continue
        result = run_operations_service.shrink_kept_recording(folder, lambda line: report(f"  {line}"))
        if result.exit_code != 0:
            failed += 1
            report(f"  failed: {result.output[-1] if result.output else 'no reason given'}")

    return 1 if failed else 0
