"""Compress videos, the Manager tab's tool: every done run's video listed with its compression state, PDFs and audio
recordings left out, the ticked ones compressed one after another, a compressed one never offered again, and never two
compressions at once. The videos are made by ffmpeg, a few seconds long."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from support import fixtures as fx
from support import harness

import config

from models import output_contract
from services import batch_compression_service
from services import recording_compression_service
from services import recording_hash_service
from services import run_recording_service
from services import run_status_service

from cases import test_compress_recording as compress_tests


def _write_record(run_folder: Path,
                  recording: Path) -> None:
    record_path = run_folder / output_contract.RUNS_DIR_NAME / "20261001-120000" / output_contract.RUN_STATE_FILE
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({"runId": "20261001-120000", "status": "complete", "phases": fx.DONE_PHASES,
                                       "repository": str(recording.parent), "module": recording.name}),
                           encoding="utf-8")


def _build_run(root: Path,
               name: str,
               file_name: str,
               is_kept: bool) -> Path:
    """A done run folder under `root/output` whose recording `file_name` is kept in it, or still in `root/inbox`."""

    inbox, run_folder = root / "inbox", root / "output" / name
    inbox.mkdir(parents=True, exist_ok=True)
    run_folder.mkdir(parents=True)
    recording = inbox / file_name
    if recording.suffix == ".mp4":
        compress_tests._make_video(recording)
    else:
        recording.write_bytes(b"not a video")
    _write_record(run_folder, recording)
    if is_kept:
        run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES}, inbox,
                                                     run_folder, recording)

    return run_folder


def _with_made_runs(test) -> None:
    """`test(root)` with the run roots, the inbox, the ui folder and the hash cache all in a folder of its own."""

    root = Path(tempfile.mkdtemp())
    previous = (dict(run_status_service.RUN_ROOTS), config.INBOX_DIR, config.UI_DIR, recording_hash_service.CACHE_PATH)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root / "output"
    config.INBOX_DIR = root / "inbox"
    config.UI_DIR = root / "ui"
    config.UI_DIR.mkdir()
    recording_hash_service.set_cache_path(root / "recording-hashes.json")
    try:
        test(root)
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous[0])
        config.INBOX_DIR, config.UI_DIR = previous[1], previous[2]
        recording_hash_service.set_cache_path(previous[3])


def _states(rows: list[dict]) -> dict[str, str]:
    return {row["name"]: row["state"] for row in rows}


def test_done_videos_are_compressed_in_turn_and_a_compressed_one_is_not_compressed_again():
    def check(root: Path) -> None:
        kept = _build_run(root, "Gateway-review", "Gateway-review.mp4", is_kept=True)
        in_inbox = _build_run(root, "AcmeSync-kickoff", "AcmeSync-kickoff.mp4", is_kept=False)
        _build_run(root, "Gateway-guide", "Gateway-guide.pdf", is_kept=True)
        _build_run(root, "Jonas-call", "Jonas-call.m4a", is_kept=True)
        listed = batch_compression_service.list_videos()
        harness.check("only the two videos are listed, not the PDF or the audio, neither compressed yet",
                      _states(listed) == {"Gateway-review": "not compressed", "AcmeSync-kickoff": "not compressed"},
                      str(_states(listed)))
        harness.check("the video still in the inbox says so", {row["name"]: row["is_in_inbox"] for row in listed}
                      == {"Gateway-review": False, "AcmeSync-kickoff": True}, str(listed))

        code = batch_compression_service.shrink_in_turn([kept, in_inbox])
        harness.check("both compress", code == 0, "")
        harness.check("both are listed compressed, each smaller",
                      all(row["state"] == "compressed" and 0 < row["after_bytes"] < row["before_bytes"]
                          for row in batch_compression_service.list_videos()), str(batch_compression_service.list_videos()))
        harness.check("the inbox video was kept in its run folder first, at 720p",
                      compress_tests._read_height(run_recording_service.find_stored_recording(in_inbox))
                      == recording_compression_service.TARGET_HEIGHT, "")
        harness.check("a compressed video is refused when ticked again",
                      batch_compression_service.find_batch_refusal([kept]) == "Gateway-review is compressed already",
                      batch_compression_service.find_batch_refusal([kept]))

    _with_made_runs(check)


def test_two_compressions_never_run_at_once_and_the_rest_of_a_batch_waits():
    def check(root: Path) -> None:
        first = _build_run(root, "Gateway-review", "Gateway-review.mp4", is_kept=True)
        second = _build_run(root, "AcmeSync-kickoff", "AcmeSync-kickoff.mp4", is_kept=True)
        # a batch whose process is alive (this test's own), on its first video
        (config.UI_DIR / batch_compression_service.BATCH_FILE_NAME).write_text(json.dumps(
            {"pid": os.getpid(), "folders": [str(first), str(second)], "position": 0}), encoding="utf-8")
        harness.check("the video the batch starts on, its record not yet running, and the one after it both wait",
                      _states(batch_compression_service.list_videos())
                      == {"Gateway-review": "waiting", "AcmeSync-kickoff": "waiting"}, str(batch_compression_service.list_videos()))
        harness.check("a second batch is refused while one runs",
                      batch_compression_service.find_batch_refusal([second]).startswith("a compression batch is running"),
                      batch_compression_service.find_batch_refusal([second]))
        (config.UI_DIR / batch_compression_service.BATCH_FILE_NAME).unlink()
        recording_compression_service.write_compression(first, {"status": recording_compression_service.COMPRESSION_RUNNING,
                                                                "pid": os.getpid(), "files": []})
        harness.check("a batch is refused while a run page's own compression runs",
                      batch_compression_service.find_batch_refusal([second])
                      == "Gateway-review is being compressed: wait until it ends",
                      batch_compression_service.find_batch_refusal([second]))
        harness.check("nothing ticked is refused", batch_compression_service.find_batch_refusal([]) != "", "")

    _with_made_runs(check)
