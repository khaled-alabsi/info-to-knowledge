"""Compress recording: the recording a done run keeps in its folder is re-encoded to 720p in place, and is still known
as the recording it was. The videos are made by ffmpeg, a few seconds long."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import signal
import tempfile
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from support import fixtures as fx
from support import harness

import config

from cli import recording_command
from models import output_contract
from services import recording_compression_service
from services import recording_hash_service
from services import run_recording_service
from services import run_status_service
from services import web_api_service

from cases import test_recording_in_run_folder as recording_tests


# The longest the page test waits for its compress process to end
_COMPRESS_WAIT_SECONDS = 120


def _start_and_reap_child() -> int:
    """The pid of a child process that has ended and been waited for."""

    child = subprocess.Popen(["true"])
    child.wait()

    return child.pid


def _wait_for_exit(pid: int) -> int:
    """The exit status of child `pid`, waited for at most _COMPRESS_WAIT_SECONDS; past it the child is killed and
    the status is the kill's."""

    statuses: list[int] = []
    waiter = threading.Thread(target=lambda: statuses.append(os.waitpid(pid, 0)[1]), daemon=True)
    waiter.start()
    waiter.join(_COMPRESS_WAIT_SECONDS)
    if waiter.is_alive():
        os.kill(pid, signal.SIGKILL)
        waiter.join()

    if not statuses:
        raise AssertionError(f"process {pid} was never waited for")

    return statuses[0]


def _make_video(path: Path,
                size: str = "1920x1080") -> None:
    """Three seconds of a moving test picture and a tone, encoded near losslessly so a 720p copy is smaller."""

    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-crf", "8", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True)


def _read_height(path: Path) -> int:
    probed = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)

    return json.loads(probed.stdout)["streams"][0]["height"]


def _build_kept_run(root: Path) -> tuple[Path, Path]:
    """A done run folder under `root/output` keeping a 1080p recording, as a run leaves it when it ends."""

    inbox, run_folder = root / "inbox", root / "output" / "AcmeSync-review"
    inbox.mkdir(parents=True)
    run_folder.mkdir(parents=True)
    video = inbox / "AcmeSync-review.mp4"
    _make_video(video)
    kept = run_recording_service.keep_recording_if_done({"status": "complete", "phases": fx.DONE_PHASES},
                                                        inbox, run_folder, video)
    record_path = run_folder / output_contract.RUNS_DIR_NAME / "20261001-120000" / output_contract.RUN_STATE_FILE
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({"runId": "20261001-120000", "status": "complete", "phases": fx.DONE_PHASES,
                                       "video": str(video)}), encoding="utf-8")

    return run_folder, kept


def _run_command(run_folder: Path) -> tuple[int, list[str]]:
    logger, handler = recording_tests._build_logger()
    code = recording_command.cmd_compress_recording(argparse.Namespace(output_root=run_folder, logger=logger))

    return code, handler.lines


def _count_real_cache_keys_under(root: Path) -> int:
    """How many recordings under `root` the real `ui/recording-hashes.json` holds a hash of: a child process does not
    see the suite's own cache path, and one left 8 temp recordings in the real file."""

    real_cache = config.UI_DIR / "recording-hashes.json"
    keys = json.loads(real_cache.read_text(encoding="utf-8")) if real_cache.is_file() else {}

    return sum(1 for key in keys if key.startswith(str(root.resolve())))


def _with_own_hash_cache(test) -> None:
    previous = recording_hash_service.CACHE_PATH
    recording_hash_service.set_cache_path(Path(tempfile.mkdtemp()) / "recording-hashes.json")
    try:
        test()
    finally:
        recording_hash_service.set_cache_path(previous)


def test_the_kept_recording_is_re_encoded_to_720p_in_place_and_still_known_by_its_old_hash():
    def check() -> None:
        run_folder, kept = _build_kept_run(Path(tempfile.mkdtemp()))
        before_bytes = kept.stat().st_size
        original_hash = recording_hash_service.compute_recording_hash(kept)
        code, lines = _run_command(run_folder)
        harness.check("the command ends 0", code == 0, "\n".join(lines))
        harness.check("the file keeps its name and place, now 720 lines high and smaller",
                      kept.is_file() and _read_height(kept) == recording_compression_service.TARGET_HEIGHT and kept.stat().st_size < before_bytes,
                      f"{_read_height(kept)} {kept.stat().st_size} {before_bytes}")
        harness.check("no copy is left beside it",
                      sorted(path.name for path in kept.parent.iterdir()) == [recording_compression_service.COMPRESSION_FILE, ".origin.json",
                                                                               kept.name],
                      str(sorted(path.name for path in kept.parent.iterdir())))
        described = recording_compression_service.describe_compression(run_folder)
        harness.check("the record says done, with the sizes before and after",
                      described == {"status": "done", "error": "", "before_bytes": before_bytes,
                                    "after_bytes": kept.stat().st_size, "files": 1, "progress": 1.0}, str(described))
        harness.check("the record keeps the hash the recording had",
                      recording_compression_service.read_compression(run_folder)["original_sha256"] == original_hash, "")
        harness.check("the run still finds its recording", run_recording_service.find_stored_recording(run_folder) == kept,
                      "")
        previous_roots = dict(run_status_service.RUN_ROOTS)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS["output"] = run_folder.parent
        try:
            harness.check("the original recording dropped in again is still the done run's",
                          recording_hash_service.find_done_run(original_hash) == {"root": "output",
                                                                                   "name": run_folder.name}, "")
        finally:
            run_status_service.RUN_ROOTS.clear()
            run_status_service.RUN_ROOTS.update(previous_roots)

    _with_own_hash_cache(check)


def test_a_failed_encode_leaves_the_recording_untouched_and_says_failed():
    def check() -> None:
        run_folder, kept = _build_kept_run(Path(tempfile.mkdtemp()))
        kept.write_bytes(b"not a video")
        code, lines = _run_command(run_folder)
        harness.check("the command ends 1 naming ffmpeg", code == 1 and "ffmpeg failed" in "\n".join(lines),
                      "\n".join(lines))
        harness.check("the recording is untouched and no copy is left",
                      kept.read_bytes() == b"not a video"
                      and not [path for path in kept.parent.iterdir() if ".compressing" in path.name], "")
        harness.check("the record says failed, with ffmpeg's error",
                      recording_compression_service.describe_compression(run_folder)["status"] == "failed"
                      and "ffmpeg failed" in recording_compression_service.read_compression(run_folder)["error"], "")

    _with_own_hash_cache(check)


def test_a_folder_keeping_no_recording_is_refused_by_name():
    run_folder = Path(tempfile.mkdtemp()) / "AcmeSync-review"
    run_folder.mkdir()
    code, lines = _run_command(run_folder)
    harness.check("exit 2, saying it keeps no recording", code == 2 and "keeps no recording" in "\n".join(lines),
                  "\n".join(lines))


def test_a_record_left_running_by_a_gone_process_reads_stopped():
    run_folder = Path(tempfile.mkdtemp())
    # A real process that has ended: pid 0 or below is refused before any process is looked for
    gone_pid = _start_and_reap_child()
    recording_compression_service.write_compression(run_folder, {"status": recording_compression_service.COMPRESSION_RUNNING,
                                                                 "pid": gone_pid, "files": []})
    harness.check("stopped, never running", recording_compression_service.describe_compression(run_folder)["status"]
                  == "stopped", "")


def test_an_encode_reports_how_far_it_has_come_and_the_record_holds_it():
    """The page showed only a spinner while an 8 GB recording took minutes: nobody could tell how far it was."""

    folder = Path(tempfile.mkdtemp())
    video = folder / "Gateway.mp4"
    _make_video(video)
    shares: list[float] = []
    recording_compression_service.shrink_video(video, on_progress=shares.append)
    harness.check("shares rise from above 0 to 1, never past it", bool(shares) and shares == sorted(shares)
                  and 0 < shares[0] and shares[-1] == 1.0, str(shares))
    run_folder = Path(tempfile.mkdtemp())
    recording_compression_service.write_compression(run_folder, {"status": recording_compression_service.COMPRESSION_RUNNING,
                                                                 "pid": os.getpid(), "files": [], "progress": 0.42})
    harness.check("the running record's share reaches the page",
                  recording_compression_service.describe_compression(run_folder)["progress"] == 0.42, "")


def test_a_file_already_small_is_kept_as_it_was():
    folder = Path(tempfile.mkdtemp())
    video = folder / "Gateway.mp4"
    _make_video(video, "320x180")
    small = folder / "small.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-c:v", "libx264",
                    "-crf", "51", "-c:a", "aac", "-b:a", "8k", str(small)], check=True)
    before = small.read_bytes()
    entry = recording_compression_service.shrink_video(small)
    harness.check("not smaller: the file stays byte for byte", entry["outcome"] == "kept: not smaller"
                  and small.read_bytes() == before, str(entry))
    other = folder / "Gateway.avi"
    other.write_bytes(b"avi bytes")
    harness.check("a kind of file it does not re-encode is kept",
                  recording_compression_service.shrink_video(other)["outcome"] == "kept: .avi is not re-encoded", "")


def test_the_page_starts_it_in_a_process_of_its_own_and_reads_its_state():
    def check() -> None:
        root = Path(tempfile.mkdtemp())
        run_folder, kept = _build_kept_run(root)
        previous_roots = dict(run_status_service.RUN_ROOTS)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS["output"] = run_folder.parent
        previous_inbox = config.INBOX_DIR
        config.INBOX_DIR = root / "inbox"
        try:
            client = TestClient(web_api_service.build_app())
            empty = root / "output" / "Gateway-empty"
            empty.mkdir()
            refused = client.post("/api/runs/output/Gateway-empty/recording/compress")
            harness.check("a folder keeping no recording is refused", refused.status_code == 409, refused.text)
            # The child reads the real hash cache, never this suite's: handed the hash in the record, it hashes nothing
            recording_compression_service.write_compression(
                run_folder, {"original_sha256": recording_hash_service.compute_recording_hash(kept)})
            started = client.post(f"/api/runs/output/{run_folder.name}/recording/compress").json()
            # the process is this test's child: wait for its exit, which is the signal that it is done, at most
            # _COMPRESS_WAIT_SECONDS, then stop it so a stuck process fails this test instead of hanging the suite
            status = _wait_for_exit(started["pid"])
            harness.check("the process ends 0", os.waitstatus_to_exitcode(status) == 0,
                          (kept.parent / recording_compression_service.COMPRESSION_LOG_FILE).read_text(encoding="utf-8"))
            detail = run_status_service.describe_run_folder("output", run_folder.name)
            harness.check("the run's state says the recording is compressed",
                          detail["recording_compression"]["status"] == "done" and _read_height(kept) == recording_compression_service.TARGET_HEIGHT,
                          str(detail["recording_compression"]))
            harness.check("the child process wrote no hash of this test's recording into the real hash cache",
                          _count_real_cache_keys_under(root) == 0, f"{_count_real_cache_keys_under(root)} key(s)")
        finally:
            run_status_service.RUN_ROOTS.clear()
            run_status_service.RUN_ROOTS.update(previous_roots)
            config.INBOX_DIR = previous_inbox

    _with_own_hash_cache(check)


def test_ffmpeg_is_stopped_when_reporting_its_progress_fails_and_a_missing_ffmpeg_is_named():
    import sys

    started: list = []
    real_popen = subprocess.Popen

    def remember_popen(*arguments, **keywords):
        process = real_popen(*arguments, **keywords)
        started.append(process)
        return process

    def fail_to_report(share: float) -> None:
        raise OSError("no space left on device")

    # A child that says it is 10 % through and then goes on, as an encode does: it waits to read from a pipe nobody
    # writes to, until the test ends it
    pipe = Path(tempfile.mkdtemp()) / "never-written"
    os.mkfifo(pipe)
    command = [sys.executable, "-c", f"print('out_time_us=1000000', flush=True); open({str(pipe)!r}).read()"]
    subprocess.Popen = remember_popen
    try:
        try:
            recording_compression_service._run_reporting_progress(command, 10.0, fail_to_report)
            outcome = "returned"
        except OSError:
            outcome = "raised"
        child = started[0]
        is_stopped = child.poll() is not None
        if not is_stopped:
            child.kill()
            child.wait()
        try:
            recording_compression_service._run_reporting_progress(["/no/such/ffmpeg"], 10.0, lambda share: None)
            missing = "returned"
        except RuntimeError as error:
            missing = f"named: {error}"
        except OSError as error:
            missing = f"raised {type(error).__name__}"
    finally:
        subprocess.Popen = real_popen
    harness.check("the failure is raised and the encode does not go on", outcome == "raised" and is_stopped,
                  f"{outcome} stopped={is_stopped}")
    harness.check("an ffmpeg that cannot start is a RuntimeError naming it", missing.startswith("named:") and "ffmpeg" in missing,
                  missing)
