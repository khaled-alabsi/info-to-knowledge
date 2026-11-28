"""The services behind `serve` and `tui`: the job queue, the run folder status, uploads, slash commands and the API.

The queue runs a stand-in for `main.py` (a script that exits with the code its video's name asks
for), so every exit code `run` defines is recorded without a model, a video or a wait. The run
folder is written by the real `RunStore`, the way a run writes it.
"""

from __future__ import annotations

import io
import json
import re
import argparse
import logging
import os
import socket
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import cv2
import numpy
from fastapi.testclient import TestClient

from support import harness

import cli
import config
from cli import recording_command
from cli import reset_command
from cli import serve_command
from cli import setup_commands
from cli import tui_command
from models import slash_command_vocabulary
from models import output_contract
from models.operation_result import OperationResult
from models.phase_guide import PHASE_GUIDES
from models.run_job import RunJobRequest
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.slash_command_request import SlashCommandRequest
from models.web_api_request import StartJobsRequest
from models.ui_errors import SlashCommandError, UnknownInboxItemError, UnknownRunFolderError, UploadRefusedError
from services import document_file_service
from services import document_folder_service
from services import document_step_history_service
from services import settings_service
from services import run_branch_service
from services import run_speed_service
from services import run_reset_service
from services import run_operations_service
from services import control_signal_service
from services import hidden_artifact_service
from services import inbox_rename_service
from services import inbox_service
from services import inbox_move_service
from services import job_queue_service
from services import recording_hash_service
from services import artifact_edit_service
from services import knowledge_filter_service
from services import run_artifact_service
from services import run_recording_service
from services import performance_log_service
from services import phase_file_service
from services import run_status_service
from services import worker_feedback_service
from services import run_store_service
from services import run_provider_set_service
from services import upload_service
from services import inbox_tags_service
from services import web_api_service
from utils import json_store_utils
from utils import slash_command_utils

# The stand-in for `main.py`: prints its arguments and exits with the number after the first dash in the video's
# stem; a negative number is the signal it ends itself with, as an interrupt during start-up does.
_FAKE_MAIN = """
import os
import signal
import sys
from pathlib import Path
arguments = sys.argv[1:]
print("child ran " + " ".join(arguments), flush=True)
code = int(Path(arguments[arguments.index("--video") + 1]).stem.split("-", 1)[1])
if code < 0:
    signal.signal(-code, signal.SIG_DFL)
    os.kill(os.getpid(), -code)
sys.exit(code)
"""


def _make_fake_main() -> Path:
    path = Path(tempfile.mkdtemp()) / "fake_main.py"
    path.write_text(_FAKE_MAIN, encoding="utf-8")

    return path


def test_the_run_line_carries_every_flag():
    request = RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"), from_phase=3, to_phase=4,
                            worker_indexes=(7, 9), is_resuming=True, is_dry_run=True)
    command = job_queue_service.build_run_command(request)
    harness.check("the run line starts with main.py run", command[1].endswith("main.py") and command[2] == "run",
                  str(command))
    harness.check("the run line carries every flag the request holds",
                  command[3:] == ["--video", "/in/a.mp4", "--output", "/out/a", "--from", "3", "--to", "4",
                                  "--worker", "7", "--worker", "9", "--resume", "--dry-run"], str(command))
    plain = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a")))
    harness.check("a plain request is a plain run line", plain[3:] == ["--video", "/in/a.mp4", "--output", "/out/a"],
                  str(plain))
    previous = config.PROFILE_NAME
    config.PROFILE_NAME = "strict"
    try:
        named = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a")))
    finally:
        config.PROFILE_NAME = previous
    harness.check("the profile the server runs under is written into the job's own run line",
                  named[-2:] == ["--profile", "strict"], str(named))


def test_the_queue_runs_jobs_in_order_and_records_each_exit_code():
    queue_dir = Path(tempfile.mkdtemp())
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    # No retries: each exit code's status is checked here; tests/cases/test_queue_retry.py checks the retries
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: config.QueueSettings(retries=0))
    try:
        requests = [RunJobRequest(video=Path(f"/in/v-{code}.mp4"), output_root=Path("/out/v"))
                    for code in (0, 2, 1, 130, -2, 7)]
        queued = job_queue_service.queue_jobs(queue_dir, requests)
        cancelled = job_queue_service.cancel_job(queue_dir, queued[5].job_id)
        ended = [job_queue_service.run_next_job(queue_dir) for _ in range(5)]
        after = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_queue_settings_reader(previous_reader)

    harness.check("the jobs ran in the order they were queued",
                  [job.job_id for job in ended] == [job.job_id for job in queued[:5]],
                  str([job.job_id for job in ended]))
    harness.check("each exit code became its status",
                  [job.status for job in ended] == ["finished", "refused", "incomplete", "stopped", "stopped"],
                  str([job.status for job in ended]))
    harness.check("a cancelled job never runs", cancelled.status == "cancelled" and after is None, str(after))
    stored = {job.job_id: job for job in job_queue_service.list_jobs(queue_dir)}
    harness.check("the queue file holds the outcomes", stored[queued[1].job_id].exit_code == 2
                  and stored[queued[5].job_id].status == "cancelled", str(stored))
    console = job_queue_service.build_console_path(queue_dir, queued[0].job_id).read_text(encoding="utf-8")
    harness.check("the job's console holds what its process printed", "child ran run --video /in/v-0.mp4" in console,
                  console)


def test_a_waiting_job_moved_up_passes_the_job_before_it_on_its_own_provider_set_defect():
    # Defect: the queue shows each provider set's jobs in their own group, but "run sooner" swapped a job with the job
    # before it in the whole queue, often another set's, so nothing moved in its group. Fix: it passes the job before
    # it on its own set.
    queue_dir = Path(tempfile.mkdtemp())
    queued = job_queue_service.queue_jobs(queue_dir, [
        RunJobRequest(video=Path(f"/in/{name}.mp4"), output_root=Path(f"/out/{name}"), provider_set=provider_set)
        for name, provider_set in (("host-1", "second-host"), ("mac-1", "Mac"), ("host-2", "second-host"))])
    job_queue_service.move_job(queue_dir, queued[2].job_id, "up")
    order = [job.job_id for job in job_queue_service.list_jobs(queue_dir) if job.status == "queued"]
    harness.check("the second-host job moved up runs before the second-host job it passed, the Mac job keeps its place",
                  order == [queued[2].job_id, queued[1].job_id, queued[0].job_id], str(order))


def test_waiting_jobs_move_up_and_down_and_ended_jobs_are_cleared_with_their_consoles():
    queue_dir = Path(tempfile.mkdtemp())
    queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path(f"/in/{name}.mp4"), output_root=Path(f"/out/{name}"))
                                                      for name in ("first", "second", "third")])
    job_queue_service.move_job(queue_dir, queued[2].job_id, "up")
    order = [job.job_id for job in job_queue_service.list_jobs(queue_dir) if job.status == "queued"]
    harness.check("a waiting job moved up runs before the one it passed",
                  order == [queued[0].job_id, queued[2].job_id, queued[1].job_id], str(order))
    job_queue_service.move_job(queue_dir, queued[0].job_id, "up")
    order = [job.job_id for job in job_queue_service.list_jobs(queue_dir) if job.status == "queued"]
    harness.check("the first waiting job moved up stays first", order[0] == queued[0].job_id, str(order))
    job_queue_service.cancel_job(queue_dir, queued[1].job_id)
    console = job_queue_service.build_console_path(queue_dir, queued[1].job_id)
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text("printed", encoding="utf-8")
    cleared = job_queue_service.clear_ended_jobs(queue_dir)
    left = [job.job_id for job in job_queue_service.list_jobs(queue_dir)]
    harness.check("clearing removes the ended job and its console, and keeps the waiting ones",
                  cleared == 1 and left == [queued[0].job_id, queued[2].job_id] and not console.exists(), str((cleared, left)))


def test_a_running_job_whose_process_is_gone_is_lost():
    queue_dir = Path(tempfile.mkdtemp())
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True, check=True)
    job = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"),
                                                                 output_root=Path("/out/v"))])[0]
    rows = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    rows[0].update(status="running", pid=int(finished.stdout))
    (queue_dir / job_queue_service.JOBS_FILE).write_text(json.dumps(rows), encoding="utf-8")

    # its run's own record says it completed, so nothing is retried (tests/cases/test_queue_retry.py has the other case)
    previous = job_queue_service.set_run_status_reader(lambda output_root: "complete")
    try:
        listed = job_queue_service.list_jobs(queue_dir)
    finally:
        job_queue_service.set_run_status_reader(previous)
    harness.check("a job running when its server went away, with no process left, is lost",
                  listed[0].status == "lost" and listed[0].ended_at, str(listed[0]))


def test_a_running_job_whose_run_folder_is_gone_says_so():
    """A job ran on with its run folder gone, and the page showed its video as "starting…" for good."""

    queue_dir = Path(tempfile.mkdtemp())
    output_root = Path(tempfile.mkdtemp()) / "Gateway"
    job = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/Gateway.mp4"),
                                                                 output_root=output_root)])[0]
    rows = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    rows[0].update(status="running", pid=os.getpid())
    (queue_dir / job_queue_service.JOBS_FILE).write_text(json.dumps(rows), encoding="utf-8")
    running = job_queue_service.list_jobs(queue_dir)[0]
    console = job_queue_service.build_console_path(queue_dir, job.job_id)
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text("phase 1: loading\n", encoding="utf-8")
    harness.check("a run that has not made its folder yet is not missing it",
                  not job_queue_service.is_run_folder_missing(queue_dir, running), "")
    console.write_text(f"run x\n{output_contract.RUN_LOG_LINE}output/Gateway/logs/x.log\nphase 1: done\n", encoding="utf-8")
    harness.check("a run whose folder is gone after it made it is missing it",
                  job_queue_service.is_run_folder_missing(queue_dir, running), "")
    output_root.mkdir(parents=True)
    harness.check("a run whose folder is there is not", not job_queue_service.is_run_folder_missing(queue_dir, running), "")
    output_root.rmdir()
    harness.check("a job not running is never missing its folder",
                  not job_queue_service.is_run_folder_missing(queue_dir, job), "")


def _make_run_folder(root: Path,
                     name: str) -> Path:
    """A run folder as a run leaves it: a run record with phase and frame statuses, and a console log."""

    folder = root / name
    paths = RunPaths(repository=Path("/in"), module=f"{name}.mp4", output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    store.set_phase("transcribe", "complete")
    store.set_phase("frames_preparing", "blocked")
    store.data["items"] = {"frame-001": {"status": "written"}, "frame-002": {"status": "written"},
                           "frame-003": {"status": "blocked"}}
    store.save()
    (paths.run_dir("2026-10-01_09-00") / "09-00.log").write_text("line one\nline two\n", encoding="utf-8")
    json_store_utils.write_json(folder / output_contract.BATCH_SUMMARIES_FILE, {"schemaVersion": 1, "batches": [
        {"slug": "frames-000-004", "frames": [0, 4], "start": 0.0, "end": 231.5, "title": "Gateway login flow",
         "summary": "Jonas logs in through the gateway.", "source": "batch"}]})

    return folder


def test_the_page_is_told_a_worker_reported_a_problem_and_how_long_a_frame_takes():
    """The owner: a report nobody reads is a call spent for nothing, so the detail view says there is one and opens the
    file; and the Frames bar says how long one frame is taking."""

    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-feedback")
    worker_feedback_service.record_report(
        folder / output_contract.WORKER_FEEDBACK_FILE, "unclear_instruction",
        "the draft length rule can be read two ways", "It says 3 to 8 sentences and also at most 500 words.",
        where="executive-summary-draft")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        detail = run_status_service.describe_run_folder("knowledge", "AcmeSync-feedback")
    finally:
        _swap_run_roots(previous)
    harness.check("the page is told how many problems were reported, the newest one, and the file to open",
                  detail["worker_feedback"] == {"entries": 1, "reports": 1, "open": 1,
                                                "newest_kind": "unclear_instruction",
                                                "newest_summary": "the draft length rule can be read two ways",
                                                "newest_answer": "",
                                                "file": output_contract.WORKER_FEEDBACK_FILE},
                  str(detail["worker_feedback"]))
    harness.check("with no finished unit of phase 3 there is no average to show",
                  detail["seconds_per_frame"] == 0.0, str(detail["seconds_per_frame"]))
    # Two finished batch units of phase 3, over four frames in all: 100 s + 60 s over 4 frames is 40 s a frame. The
    # batch in flight writes no row, so its seconds never count against frames it has not written.
    log = folder / output_contract.RUNS_DIR_NAME / "2026-10-01_09-00" / performance_log_service.PERFORMANCE_FILE
    with log.open("a", encoding="utf-8") as rows:
        for slugs, seconds in ((["frame-001", "frame-002"], 100.0), (["frame-003", "frame-004"], 60.0)):
            rows.write(json.dumps({"level": "batch", "phase": "frames_processing", "durationS": seconds,
                                   "frameSlugs": slugs}) + "\n")
    harness.check("the average is the finished units' seconds over the frames they covered",
                  run_status_service._compute_seconds_per_frame(folder) == 40.0,
                  str(run_status_service._compute_seconds_per_frame(folder)))
    # A run written before phase 3's rename logged it as `work`; the timing card counts those rows, and so does this
    with log.open("a", encoding="utf-8") as rows:
        rows.write(json.dumps({"level": "batch", "phase": "work", "durationS": 140.0,
                               "frameSlugs": ["frame-005", "frame-006"]}) + "\n")
    harness.check("a unit logged under the retired name counts: 300 s over 6 frames",
                  run_status_service._compute_seconds_per_frame(folder) == 50.0,
                  str(run_status_service._compute_seconds_per_frame(folder)))

    empty = _make_run_folder(root / "knowledge", "AcmeSync-quiet")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        quiet = run_status_service.describe_run_folder("knowledge", "AcmeSync-quiet")
    finally:
        _swap_run_roots(previous)
    harness.check("a run whose workers reported nothing says so, and names no file",
                  quiet["worker_feedback"]["entries"] == 0 and quiet["worker_feedback"]["file"] == "",
                  str(quiet["worker_feedback"]))
    harness.check("the folder is there and holds no feedback file",
                  not (empty / output_contract.WORKER_FEEDBACK_FILE).is_file())


def _swap_run_roots(roots: dict[str, Path]) -> dict[str, Path]:
    previous = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update(roots)

    return previous


def test_a_folder_holding_only_chosen_settings_is_no_run_in_the_list_defect():
    # Defect: a refused start's folder, holding only its speed and workflow files, showed as an unfinished run at
    # phase 1. Fix: a folder holding nothing but a run line's chosen settings is left out of the run list
    root = Path(tempfile.mkdtemp())
    _make_run_folder(root / "output", "AcmeSync-demo")
    leftover = root / "output" / "AcmeSync-weekly"
    leftover.mkdir()
    (leftover / "run-speed.json").write_text('{"preset": "balanced", "values": {}}', encoding="utf-8")
    (leftover / "run-workflow.json").write_text('{"workflow": "quality"}', encoding="utf-8")
    previous = _swap_run_roots({"output": root / "output"})
    try:
        listed = [row["name"] for row in run_status_service.list_run_folders()]
    finally:
        _swap_run_roots(previous)
    harness.check("the leftover folder is not listed, the run beside it is", listed == ["AcmeSync-demo"], str(listed))


def test_a_run_folder_is_described_from_its_files():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-demo")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        detail = run_status_service.describe_run_folder("knowledge", "AcmeSync-demo")
        listed = run_status_service.list_run_folders()
        try:
            run_status_service.resolve_run_folder("knowledge", "../knowledge")
            is_escape_refused = False
        except UnknownRunFolderError:
            is_escape_refused = True
    finally:
        _swap_run_roots(previous)

    harness.check("the folder is listed with its run's status", [row["name"] for row in listed] == ["AcmeSync-demo"]
                  and listed[0]["status"] == "running" and listed[0]["run_id"] == "2026-10-01_09-00", str(listed))
    harness.check("each phase shows the status its run recorded, and the frame loop with written frames and no status of"
                  " its own is incomplete, never not run",
                  [row["status"] for row in detail["phases"]][:4] == ["complete", "blocked", "incomplete", "not run"],
                  str(detail["phases"]))
    harness.check("the frames are counted by status", detail["frames"]["written"] == 2
                  and detail["frames"]["blocked"] == 1, str(detail["frames"]))
    harness.check("the blocked frames are named, so they can be asked again", detail["blocked_items"] == ["frame-003"],
                  str(detail["blocked_items"]))
    harness.check("the video is the one the run record names", detail["video"] == "/in/AcmeSync-demo.mp4",
                  detail["video"])
    harness.check("each batch of phase 3 is a place to jump to in the recording, with its title, seconds and summary",
                  detail["batches"] == [{"title": "Gateway login flow", "start_s": 0.0, "end_s": 231.5,
                                         "summary": "Jonas logs in through the gateway."}],
                  str(detail["batches"]))
    harness.check("the newest log is found", detail["log"].endswith("09-00.log"), detail["log"])
    harness.check("a folder outside the run roots is refused", is_escape_refused)
    harness.check("no process writes into the fixture folder", detail["live_pids"] == [] and not detail["is_live"])
    log = run_status_service.find_newest_log(folder)
    text, offset = run_status_service.read_log_text(log, 0)
    rest, _ = run_status_service.read_log_text(log, offset)
    harness.check("a log is read from an offset, and nothing is read twice",
                  text == "line one\nline two\n" and rest == "", repr((text, rest)))
    again, _ = run_status_service.read_log_text(log, offset + 100)
    harness.check("an offset past the end reads the replaced file from its start", again == text, repr(again))


def test_phase_4_counts_its_steps_from_its_follow_up_records():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-steps")
    writing = folder / output_contract.WRITING_DIR_NAME
    writing.mkdir(parents=True)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        before_agenda = run_status_service.describe_run_folder("knowledge", "AcmeSync-steps")
        json_store_utils.write_json(writing / "parts.json", {"points": [{"number": 1}, {"number": 2}]})
        json_store_utils.write_json(writing / "follow-ups.json", {
            "executive-summary-draft": {"status": "accepted"}, "parts": {"status": "accepted"},
            "chapter-01": {"status": "accepted"}, "chapter-02": {"status": "accepted"},
            "chapter-01-text": {"status": "blocked"}, "chapter-02-text-1": {"status": "accepted"},
            "chapter-02-text-2": {"status": "accepted"}})
        after = run_status_service.describe_run_folder("knowledge", "AcmeSync-steps")
    finally:
        _swap_run_roots(previous)
    harness.check("before the agenda, phase 4 knows its summary draft, agenda and final step, and says the total is open",
                  before_agenda["document_stages"] == [{"name": "table of contents", "counts": {"waiting": 2}},
                                                       {"name": "chapters", "counts": {}},
                                                       {"name": "final summary", "counts": {"waiting": 1}}]
                  and not before_agenda["is_document_total_known"], str(before_agenda["document_stages"]))
    harness.check("each chapter adds its headings and its text, one step per part once asked in parts",
                  after["document_stages"] == [{"name": "table of contents", "counts": {"accepted": 4}},
                                               {"name": "chapters", "counts": {"blocked": 1, "accepted": 2}},
                                               {"name": "final summary", "counts": {"waiting": 1}}]
                  and after["is_document_total_known"], str(after["document_stages"]))


def test_an_upload_lands_in_the_inbox_under_a_free_name():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    previous_roots = _swap_run_roots({"output": root / "output"})
    previous_cache = recording_hash_service.CACHE_PATH
    recording_hash_service.set_cache_path(root / "recording-hashes.json")
    try:
        first = upload_service.save_upload(io.BytesIO(b"video one"), inbox_root, "Gateway review.mp4")
        second = upload_service.save_upload(io.BytesIO(b"video two"), inbox_root, "C:\\Users\\<name>\\Gateway review.mp4")
    finally:
        _swap_run_roots(previous_roots)
        recording_hash_service.set_cache_path(previous_cache)
    harness.check("an upload keeps its own name", first.name == "Gateway review.mp4", first.name)
    harness.check("a second recording of the same name gets a number, and a Windows path is dropped",
                  second.name == "Gateway review-2.mp4", second.name)
    harness.check("no partial file is left", not list(inbox_root.glob("*.part")), str(list(inbox_root.iterdir())))
    for name in ("notes.txt", "", ".hidden.mp4"):
        try:
            upload_service.choose_upload_path(inbox_root, name)
            message = ""
        except UploadRefusedError as exc:
            message = str(exc)
        harness.check(f"{name!r} is refused with a reason", bool(message), message)


def test_a_slash_command_is_split_into_name_words_and_options():
    request = slash_command_utils.parse_slash_command("/process 1 3 --from 2 --dry-run")
    harness.check("name, words and options", request.name == "process" and request.words == ("1", "3")
                  and request.options == {"from": "2", "dry-run": True}, str(request))
    quoted = slash_command_utils.parse_slash_command('/open "AcmeSync demo"')
    harness.check("a quoted folder name is one word", quoted.words == ("AcmeSync demo",), str(quoted))
    for line, expected in (("/bogus", "/bogus is no command"), ("inbox", "starts with /"),
                           ("/process 1 --from", "--from needs a number"), ("/process --fast", "--fast is no option")):
        try:
            slash_command_utils.parse_slash_command(line)
            message = ""
        except SlashCommandError as exc:
            message = str(exc)
        harness.check(f"{line!r} is refused: {expected}", expected in message, message)
    harness.check("a phase number is read", slash_command_utils.parse_phase_number("4") == 4)
    try:
        slash_command_utils.parse_phase_number("9")
        message = ""
    except SlashCommandError as exc:
        message = str(exc)
    harness.check("a phase that does not exist is refused with the phases there are", "1, 2, 3, 4, 5" in message,
                  message)
    harness.check("every command has a suggestion", len(slash_command_utils.list_suggestions())
                  == len(slash_command_vocabulary.SLASH_COMMANDS))


def test_the_api_lists_uploads_queues_and_signals():
    root = Path(tempfile.mkdtemp())
    _make_run_folder(root / "knowledge", "AcmeSync-demo")
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    previous_inbox, previous_documents = config.INBOX_DIR, config.DOCUMENT_ROOT
    previous_queue = job_queue_service.QUEUE_DIR
    config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = root / "inbox", root / "knowledge", root / "ui"
    try:
        client = TestClient(web_api_service.build_app())
        uploaded = client.post("/api/inbox/upload", files=[("files", ("Gateway.mp4", b"video", "video/mp4")),
                                                           ("files", ("notes.txt", b"text", "text/plain"))]).json()
        inbox = client.get("/api/inbox").json()
        queued = client.post("/api/jobs", json={"items": [{"video": inbox[0]["video"], "output": inbox[0]["output"]}],
                                                "from_phase": 2, "is_dry_run": True, "workflow": "fast-processing"}).json()
        jobs = client.get("/api/jobs").json()
        signalled = client.post("/api/runs/knowledge/AcmeSync-demo/control", json={"signal": "pause"}).json()
        run_dir = run_status_service.find_newest_run_dir(root / "knowledge" / "AcmeSync-demo")
        signal_on_disk = control_signal_service.read_signal(run_dir)
        continued = client.post("/api/runs/knowledge/AcmeSync-demo/control", json={"signal": "continue"}).json()
        bad_signal = client.post("/api/runs/knowledge/AcmeSync-demo/control", json={"signal": "explode"})
        missing = client.get("/api/runs/knowledge/nothing-here")
        phases = client.get("/api/phases").json()
    finally:
        _swap_run_roots(previous_roots)
        config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = (previous_inbox, previous_documents,
                                                                               previous_queue)

    harness.check("a video is saved and a text file refused with its reason",
                  uploaded[0]["saved_as"] == "Gateway.mp4" and "not a video" in uploaded[1]["error"], str(uploaded))
    harness.check("the inbox lists the upload with the run folder it lands in",
                  inbox[0]["name"] == "Gateway.mp4" and inbox[0]["output"] == str(root / "knowledge" / "Gateway")
                  and not inbox[0]["is_finished"], str(inbox))
    harness.check("a queued job carries the phase selection and the workflow",
                  queued[0]["request"]["from_phase"] == 2 and queued[0]["request"]["is_dry_run"]
                  and "--dry-run" in queued[0]["command"]
                  and queued[0]["command"][queued[0]["command"].index("--workflow") + 1] == "fast-processing", str(queued))
    harness.check("the queue lists it", [job["job_id"] for job in jobs] == [queued[0]["job_id"]], str(jobs))
    harness.check("each job says whether its run folder went missing", jobs[0]["is_output_missing"] is False, str(jobs))
    harness.check("pause reaches the run's control file", signalled["signal"] == "pause" and signal_on_disk == "pause",
                  str(signalled))
    harness.check("continue removes it", continued["signal"] is None, str(continued))
    harness.check("an unknown signal is refused with the ones there are", bad_signal.status_code == 400
                  and "pause" in bad_signal.json()["detail"], bad_signal.text)
    harness.check("the phases are the contract's, in order", [row["number"] for row in phases] == [1, 2, 3, 4, 5, 6, 7]
                  and phases[0]["name"] == "transcribe", str(phases))
    harness.check("every phase says what it does, reads and writes, and whether a model is called",
                  all(row.get("summary") and row.get("reads") and row.get("writes") and "is_model_used" in row
                      for row in phases) and set(PHASE_GUIDES) == {row["number"] for row in phases}, str(phases))
    harness.check("phase 2's help names its three stages and says a model is called",
                  phases[1]["is_model_used"] and all(stage in phases[1]["summary"]
                                                     for stage in ("Rules:", "Classifier:", "Redundancy model:")),
                  str(phases[1]))
    harness.check("an unknown run folder is a 404 that names it", missing.status_code == 404
                  and "nothing-here" in missing.json()["detail"], missing.text)


def test_serve_and_tui_parse_and_tui_refuses_a_server_it_cannot_reach():
    serve = cli.build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])
    harness.check("serve takes a host and a port", (serve.host, serve.port) == ("0.0.0.0", 9000), str(serve))
    # The server listened on this machine only, so the owner's phone on the same network could not open the page until
    # it was started again with --host; it listens on the network by default now, and the clients still call this machine
    harness.check("serve listens on the network by default, and the clients call this machine",
                  cli.build_parser().parse_args(["serve"]).host == "0.0.0.0"
                  and config.SERVER_HOST in config.LOCAL_HOSTS)
    own = argparse.ArgumentParser()
    serve_command.add_serve_arguments(own)
    harness.check("and its own flags parse alone", own.parse_args(["--port", "9001"]).port == 9001)
    harness.check("serve takes a socket `i2k tui` hands over", cli.build_parser().parse_args(["serve", "--fd", "7"]).socket_fd == 7
                  and cli.build_parser().parse_args(["serve"]).socket_fd is None)
    tui = cli.build_parser().parse_args(["tui", "--server", "http://ui.invalid:1"])
    stream = io.StringIO()
    logger = logging.getLogger("test-tui-refusal")
    logger.addHandler(logging.StreamHandler(stream))
    tui.logger = logger
    code = tui_command.cmd_tui(tui)
    harness.check("tui exits 2 when a server on another machine does not answer, and starts none",
                  code == 2 and "no server answers at http://ui.invalid:1" in stream.getvalue(), stream.getvalue())


def test_serve_starts_no_queue_worker_when_its_port_is_taken():
    holder = socket.create_server(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    # the address the port is taken on: macOS lets another listen on every address beside it
    serve = cli.build_parser().parse_args(["serve", "--host", "127.0.0.1", "--port", str(port)])
    serve.logger = logging.getLogger("test-serve-port-taken")
    started = []
    original_worker, original_run = serve_command.job_queue_service.start_worker, serve_command.uvicorn.run
    serve_command.job_queue_service.start_worker = lambda queue_dir: started.append(queue_dir)
    serve_command.uvicorn.run = lambda app, **where: None
    try:
        code = serve_command.cmd_serve(serve)
    finally:
        serve_command.job_queue_service.start_worker = original_worker
        serve_command.uvicorn.run = original_run
        holder.close()
    harness.check("no worker was started for a server that cannot listen", started == [], str(started))
    harness.check("and the command ends with 2", code == 2, str(code))


def test_tui_given_a_port_with_no_host_starts_a_server_on_this_machine():
    tui = cli.build_parser().parse_args(["tui", "--server", "http://:9000"])
    tui.logger = logging.getLogger("test-tui-no-host")
    started = []
    original_is_up, original_start = tui_command.ui_api_client.is_server_up, tui_command._start_local_server
    tui_command.ui_api_client.is_server_up = lambda base_url: False
    tui_command._start_local_server = lambda host, port: started.append((host, port)) or "test: not started"
    try:
        code = tui_command.cmd_tui(tui)
    finally:
        tui_command.ui_api_client.is_server_up = original_is_up
        tui_command._start_local_server = original_start
    harness.check("the server is started on this machine's address", started == [(config.SERVER_HOST, 9000)], str(started))
    harness.check("and its refusal ends the command with 2", code == 2, str(code))


def test_the_page_and_the_command_line_call_the_same_backend_operation():
    """Each short operation is one function, which the endpoint and the command both call; no
    `main.py` is started for it."""

    root = Path(tempfile.mkdtemp())
    _make_run_folder(root / "knowledge", "AcmeSync-demo")
    folder = (root / "knowledge" / "AcmeSync-demo").resolve()
    names = ["reset_run_to_phase", "reset_run_from_chapter", "move_recording_to_inbox", "keep_recording_in_run",
             "describe_models", "probe_model_hosts"]
    previous = {name: getattr(run_operations_service, name) for name in names}
    calls: list[tuple] = []
    for name in names:
        setattr(run_operations_service, name,
                lambda *arguments, _name=name: calls.append((_name, *arguments)) or OperationResult(0, [f"{_name} done"]))
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        answers = [client.post("/api/runs/knowledge/AcmeSync-demo/reset", json={"kind": "phase", "number": 2}).json(),
                   client.post("/api/runs/knowledge/AcmeSync-demo/reset",
                               json={"kind": "chapter", "number": 3, "is_confirmed": True}).json(),
                   client.post("/api/runs/knowledge/AcmeSync-demo/recording/to-inbox").json(),
                   client.post("/api/runs/knowledge/AcmeSync-demo/recording/to-run").json(),
                   client.get("/api/models").json(), client.get("/api/probe").json()]
        page_calls, calls[:] = list(calls), []
        logger = logging.getLogger("test-one-backend")
        reset_command.cmd_reset_to(argparse.Namespace(output_root=folder, phase=2, is_confirmed=False, logger=logger))
        reset_command.cmd_reset_chapter(argparse.Namespace(output_root=folder, chapter=3, is_confirmed=True,
                                                           is_text_only=False, logger=logger))
        recording_command.cmd_recording_to_inbox(argparse.Namespace(output_root=folder, logger=logger))
        recording_command.cmd_recording_to_run(argparse.Namespace(output_root=folder, logger=logger))
        setup_commands.cmd_models(argparse.Namespace())
        setup_commands.cmd_probe(argparse.Namespace())
    finally:
        _swap_run_roots(previous_roots)
        for name, function in previous.items():
            setattr(run_operations_service, name, function)
    harness.check("the page called each operation with the folder and the request",
                  page_calls == [("reset_run_to_phase", folder, 2, False), ("reset_run_from_chapter", folder, 3, True, False),
                                 ("move_recording_to_inbox", folder), ("keep_recording_in_run", folder),
                                 ("describe_models",), ("probe_model_hosts",)], str(page_calls))
    harness.check("and returned each one's code and lines",
                  [answer["output"] for answer in answers] == [f"{name} done" for name in names], str(answers))
    harness.check("the command line called the same functions",
                  [call[0] for call in calls] == names and calls[1] == ("reset_run_from_chapter", folder, 3, True, False),
                  str(calls))
    harness.check("the server no longer starts main.py for a short command",
                  "main.py" not in Path(web_api_service.__file__).read_text(encoding="utf-8"), "")


def test_a_slash_command_request_holds_its_parts():
    request = SlashCommandRequest(name="stop")
    harness.check("a command with nothing after it has no words and no options",
                  request.words == () and request.options == {}, str(request))


def test_a_start_request_selects_no_phase_unless_asked():
    body = StartJobsRequest(items=[{"video": "/in/a.mp4", "output": "/out/a"}])
    harness.check("a request with only items runs every phase, fresh, for real",
                  (body.only_phase, body.from_phase, body.to_phase, body.worker_indexes, body.is_resuming,
                   body.is_dry_run) == (None, None, None, [], False, False), str(body))


def test_a_live_run_shows_the_phase_its_log_is_in_not_the_status_the_last_run_left():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-resumed")
    log = run_status_service.find_newest_log(folder)
    log.write_text("run 2026-10-01_09-00  phases 1..5\nphase 2: reading 02_frames.json from disk\n"
                   "phase 2: preparing frames\n", encoding="utf-8")
    stand_in = Path(tempfile.mkdtemp()) / "main.py"
    stand_in.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    # A process that looks like `main.py run --output <folder>` to `ps`, and waits on its input until it is closed.
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        detail = run_status_service.describe_run_folder("knowledge", "AcmeSync-resumed")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    frames_preparing_row = detail["phases"][1]
    harness.check("the live run's folder reads running, not the status its record still holds",
                  detail["status"] == "running" and detail["is_live"] and detail["live_pids"] == [live.pid], str(detail))
    harness.check("the phase its log is in reads running, not the status the last run left",
                  frames_preparing_row["status"] == "running", str(frames_preparing_row))
    harness.check("the other phases keep their recorded status",
                  [row["status"] for row in detail["phases"]][::2] == ["complete", "incomplete", "not run"],
                  str(detail["phases"]))


def test_a_frame_dropped_while_phase_2_runs_shows_the_layer_that_dropped_it():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "AcmeSync-dropping")
    frames_dir = folder / "frames"
    (frames_dir / "dropped").mkdir(parents=True)
    (frames_dir / "dropped" / "frame-004.jpg").write_bytes(b"jpg")
    knowledge_filter_service.write_dropped_list(frames_dir, [
        {"slug": "frame-004", "path": "frames/dropped/frame-004.jpg", "timestamp": 12.0, "dropScore": None,
         "droppedBy": "redundancy", "coveredBy": ["frame-005"], "reason": "the same board"}])
    dropped = {group["key"]: group["items"] for group in run_artifact_service.list_artifacts(folder)}["dropped"]
    harness.check("before 02_frames.json exists, a dropped frame names its layer, its covering frame and the reason",
                  [(item["dropped_by"], item["covered_by"], item["drop_reason"]) for item in dropped]
                  == [("redundancy", ["frame-005"], "the same board")], str(dropped))
    harness.check("the list beside the images is no file a person opens",
                  not any(item["path"].endswith(".json") for item in dropped), str(dropped))


def test_each_filter_stage_lists_the_frames_it_removed_from_the_dropped_frames():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "AcmeSync-removed")
    (folder / "frames" / "dropped").mkdir(parents=True)
    for name in ("frame-002", "frame-004"):
        (folder / "frames" / "dropped" / f"{name}.jpg").write_bytes(b"jpg")
    (folder / "02_frames.json").write_text(json.dumps({"items": [], "droppedFrames": [
        {"slug": "frame-002", "path": "frames/dropped/frame-002.jpg", "droppedBy": "filter"},
        {"slug": "frame-004", "path": "frames/dropped/frame-004.jpg", "droppedBy": "redundancy",
         "coveredBy": ["frame-005"], "reason": "the same board"},
        {"slug": "frame-007", "path": "frames/dropped/frame-007.jpg", "droppedBy": "hand"}]}), encoding="utf-8")
    stages = [{"name": "rules"}, {"name": "classifier"}, {"name": "model"}]
    listed = run_status_service.add_removed_frames(stages, folder)
    harness.check("the classifier lists the frame it dropped, the model its frame with what holds it and why, the rules "
                  "nothing, and a frame whose image is gone is not listed",
                  "removed" not in listed[0]
                  and [frame["slug"] for frame in listed[1]["removed"]] == ["frame-002"]
                  and listed[2]["removed"] == [{"slug": "frame-004", "path": "frames/dropped/frame-004.jpg",
                                                 "covered_by": ["frame-005"], "reason": "the same board"}], str(listed))


def test_a_pdf_run_s_text_is_an_artifact_and_a_recording_s_transcript_stays_under_its_audio_defect():
    # Defect: a PDF's text stood in a panel stacked under the PDF in its own tab. Fix: the text is an artifact group of
    # its own when the run has no audio; a video's or an audio recording's transcript stays under its audio
    root = Path(tempfile.mkdtemp())
    pdf_run = _make_run_folder(root / "output", "AcmeSync-pdf")
    (pdf_run / "01_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    video_run = _make_run_folder(root / "output", "AcmeSync-video")
    (video_run / "01_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (video_run / "audio.wav").write_bytes(b"wav")
    pdf_groups = {group["key"]: group for group in run_artifact_service.list_artifacts(pdf_run)}
    video_groups = {group["key"] for group in run_artifact_service.list_artifacts(video_run)}
    harness.check("the PDF run lists its text as a group of kind text",
                  pdf_groups.get("text", {}).get("kind") == "text"
                  and [item["path"] for item in pdf_groups["text"]["items"]] == ["01_transcript.json"], str(pdf_groups))
    harness.check("a run with audio keeps its transcript under its audio and lists no text group",
                  "text" not in video_groups and "audio" in video_groups, str(video_groups))


def test_the_artifacts_are_the_files_a_person_reads_never_program_state():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-artifacts")
    for relative, content in (("frames/frame-000.jpg", b"jpg"), ("frames/frame-001.jpg", b"jpg"), ("audio.wav", b"wav"),
                              ("03_processed-frames/frame-000.md", b"# Frame 0"), ("03_notes.md", b"# Notes"),
                              ("04_writing/draft.md", b"# Draft"), ("04_document.md", b"# Doc"),
                              ("AcmeSync Review/AcmeSync Review.md", b"![figure](resources/figure-01.png)"),
                              ("AcmeSync Review/resources/figure-01.png", b"png"), ("02_frames.json", b"{}"),
                              ("04_writing/parts.json", b"{}"), ("04_writing/images/cuts/cut-002.png", b"png"),
                              ("04_writing/images/cuts/cut-001.png", b"png"), ("04_writing/images.json", b"{}"),
                              ("02_speech.md", b"# Speech"), ("03_prepared/items.md", b"# Items"),
                              ("03_prepared/details/01.md", b"# Item 1"), ("03_frame-descriptions.md", b"# Frames"),
                              ("04_writing/requests/fast-document.md", b"# Request")):
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(content)
    groups = {group["key"]: [item["path"] for item in group["items"]] for group in run_artifact_service.list_artifacts(folder)}
    harness.check("the groups are the document, draft, requests, notes, frame descriptions, frame notes, an audio run's speech and prepared notes, cuts, "
                  "frames and audio with the transcript, in that order",
                  list(groups) == ["document", "draft", "requests", "notes", "descriptions", "processed", "speech", "prepared",
                                   "cuts", "frames", "audio"]
                  and groups["requests"] == ["04_writing/requests/fast-document.md"]
                  and groups["prepared"] == ["03_prepared/details/01.md", "03_prepared/items.md"]
                  and [group["title"] for group in run_artifact_service.list_artifacts(folder)][-1] == "Audio and transcript",
                  str(groups))
    harness.check("the cuts are phase 4's cut images, in name order",
                  groups["cuts"] == ["04_writing/images/cuts/cut-001.png", "04_writing/images/cuts/cut-002.png"],
                  str(groups["cuts"]))
    harness.check("once published, the published document is the only document listed",
                  groups["document"] == ["AcmeSync Review/AcmeSync Review.md"], str(groups["document"]))
    unpublished_folder = _make_run_folder(root / "knowledge", "AcmeSync-unpublished")
    (unpublished_folder / "04_document.md").write_text("# Doc", encoding="utf-8")
    unpublished = {group["key"]: [item["path"] for item in group["items"]]
                   for group in run_artifact_service.list_artifacts(unpublished_folder)}
    harness.check("before phase 4 publishes, its own document is listed",
                  unpublished["document"] == ["04_document.md"], str(unpublished["document"]))
    harness.check("no json file is listed", not any(path.endswith(".json") for paths in groups.values() for path in paths))
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        base = "/api/runs/knowledge/AcmeSync-artifacts/files"
        figure = client.get(f"{base}/AcmeSync%20Review/resources/figure-01.png")
        state = client.get(f"{base}/02_frames.json")
        outside = client.get(f"{base}/..%2F..%2Fsecret.md")
    finally:
        _swap_run_roots(previous)
    harness.check("a document's image is served from beside it", figure.status_code == 200 and figure.content == b"png",
                  figure.text)
    harness.check("a json file is refused with the kinds that are served", state.status_code == 404
                  and ".md" in state.json()["detail"], state.text)
    harness.check("a path leaving the run folder is refused", outside.status_code == 404, outside.text)


def test_a_document_with_figures_downloads_as_a_zip_and_a_frame_as_itself():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-download")
    document = folder / "AcmeSync Review" / "AcmeSync Review.md"
    (document.parent / "resources").mkdir(parents=True)
    document.write_text("# AcmeSync\n![a figure](resources/figure-01.png \"caption\")\n"
                        "<img src=\"resources/figure-02.png\">\n[the web](https://example.org/x.png)\n"
                        "![missing](resources/figure-09.png)\n![again](resources/figure-01.png)\n", encoding="utf-8")
    for name in ("figure-01.png", "figure-02.png"):
        (document.parent / "resources" / name).write_bytes(b"png")
    (folder / "frames").mkdir()
    (folder / "frames" / "frame-000.jpg").write_bytes(b"jpg")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        base = "/api/runs/knowledge/AcmeSync-download/download"
        zipped = client.get(f"{base}/AcmeSync%20Review/AcmeSync%20Review.md")
        frame = client.get(f"{base}/frames/frame-000.jpg")
    finally:
        _swap_run_roots(previous)
    names = sorted(zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()) if zipped.status_code == 200 else []
    harness.check("a document that links figures downloads as a zip of it and each figure once, at the paths it uses",
                  names == ["AcmeSync Review.md", "resources/figure-01.png", "resources/figure-02.png"]
                  and "AcmeSync%20Review.zip" in zipped.headers.get("content-disposition", ""), str(names))
    harness.check("a frame downloads as the file itself", frame.status_code == 200 and frame.content == b"jpg"
                  and "frame-000.jpg" in frame.headers.get("content-disposition", ""), str(frame.headers))


def test_a_document_is_saved_from_the_page_and_every_version_it_replaces_is_kept():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-edit")
    document = folder / "AcmeSync Review" / "AcmeSync Review.md"
    document.parent.mkdir(parents=True)
    document.write_text("# AcmeSync\n\nthe first text\n", encoding="utf-8")
    (folder / "frames").mkdir()
    (folder / "frames" / "frame-000.jpg").write_bytes(b"jpg")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        base = "/api/runs/knowledge/AcmeSync-edit"
        name = "AcmeSync%20Review/AcmeSync%20Review.md"
        first = client.put(f"{base}/files/{name}", json={"markdown": "# AcmeSync\n\nthe second text\n"})
        second = client.put(f"{base}/files/{name}", json={"markdown": "# AcmeSync\n\nthe third text\n"})
        versions = client.get(f"{base}/versions/{name}")
        oldest = client.get(f"{base}/version/{name}", params={"at": versions.json()[-1]["at"]})
        unknown = client.get(f"{base}/version/{name}", params={"at": "20200101T000000.000000"})
        frame = client.put(f"{base}/files/frames/frame-000.jpg", json={"markdown": "# not a frame"})
        missing = client.put(f"{base}/files/04_document.md", json={"markdown": "# nothing there"})
        kept_path = f"{artifact_edit_service.EDITS_DIR}/{versions.json()[0]['at']}/AcmeSync%20Review/AcmeSync%20Review.md"
        kept = client.put(f"{base}/files/{kept_path}", json={"markdown": "# over a version"})
    finally:
        _swap_run_roots(previous)
    harness.check("the file on disk holds what the page last saved",
                  document.read_text(encoding="utf-8") == "# AcmeSync\n\nthe third text\n", document.read_text(encoding="utf-8"))
    harness.check("each save names the stamp its version is kept under",
                  first.status_code == 200 and second.status_code == 200
                  and {first.json()["kept_as"], second.json()["kept_as"]} == {entry["at"] for entry in versions.json()},
                  f"{first.text} {second.text} {versions.text}")
    harness.check("the versions come back newest first, one per save",
                  len(versions.json()) == 2 and versions.json()[0]["at"] > versions.json()[1]["at"], versions.text)
    harness.check("the oldest version is the text the first save replaced",
                  oldest.json()["markdown"] == "# AcmeSync\n\nthe first text\n", oldest.text)
    harness.check("a stamp no version has is refused", unknown.status_code == 404, unknown.text)
    harness.check("a frame is refused: only a markdown file is written from the page",
                  frame.status_code == 404 and "no markdown file" in frame.json()["detail"], frame.text)
    harness.check("a file the folder does not hold is refused, never created",
                  missing.status_code == 404 and not (folder / "04_document.md").exists(), missing.text)
    harness.check("a kept version cannot be written over", kept.status_code == 404
                  and "kept version" in kept.json()["detail"], kept.text)


def test_only_the_last_twenty_versions_of_a_document_are_kept():
    root = Path(tempfile.mkdtemp())
    folder = root / "knowledge" / "AcmeSync-versions"
    (folder / "AcmeSync Review").mkdir(parents=True)
    document = folder / "AcmeSync Review" / "AcmeSync Review.md"
    other = folder / "03_notes.md"
    document.write_text("# save 0\n", encoding="utf-8")
    other.write_text("# Notes 0\n", encoding="utf-8")
    artifact_edit_service.save_markdown(folder, "03_notes.md", "# Notes 1\n")
    for number in range(artifact_edit_service.VERSIONS_KEPT + 3):
        artifact_edit_service.save_markdown(folder, "AcmeSync Review/AcmeSync Review.md", f"# save {number + 1}\n")
    kept = artifact_edit_service.list_versions(folder, "AcmeSync Review/AcmeSync Review.md")
    texts = [artifact_edit_service.read_version(folder, "AcmeSync Review/AcmeSync Review.md", entry["at"]) for entry in kept]
    harness.check(f"the last {artifact_edit_service.VERSIONS_KEPT} versions are kept and the older ones removed",
                  len(kept) == artifact_edit_service.VERSIONS_KEPT, str(len(kept)))
    harness.check("what is kept is the newest versions, newest first",
                  texts[0] == f"# save {artifact_edit_service.VERSIONS_KEPT + 2}\n" and texts[-1] == "# save 3\n", str(texts[:2]))
    harness.check("pruning one file never touches another file's history",
                  [entry["at"] for entry in artifact_edit_service.list_versions(folder, "03_notes.md")] != []
                  and artifact_edit_service.read_version(folder, "03_notes.md",
                                                        artifact_edit_service.list_versions(folder, "03_notes.md")[0]["at"])
                  == "# Notes 0\n")


def test_a_document_named_with_brackets_keeps_its_versions_and_is_pruned_like_any_other():
    root = Path(tempfile.mkdtemp())
    folder = root / "knowledge" / "bracket-versions"
    (folder / "Daily [Team A]").mkdir(parents=True)
    document = folder / "Daily [Team A]" / "Daily [Team A].md"
    document.write_text("# save 0\n", encoding="utf-8")
    name = "Daily [Team A]/Daily [Team A].md"
    for number in range(artifact_edit_service.VERSIONS_KEPT + 3):
        artifact_edit_service.save_markdown(folder, name, f"# save {number + 1}\n")
    kept = artifact_edit_service.list_versions(folder, name)
    harness.check("the versions of a bracketed name are found and capped",
                  len(kept) == artifact_edit_service.VERSIONS_KEPT, str(len(kept)))
    harness.check("one of them is read back",
                  artifact_edit_service.read_version(folder, name, kept[0]["at"]) == f"# save {artifact_edit_service.VERSIONS_KEPT + 2}\n")


def test_an_inbox_video_run_by_hand_under_another_folder_is_not_new():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    for name in ("AcmeSync.mp4", "Gateway.mp4", "Jonas.mp4", "Weekly.mp4"):
        (inbox_root / name).write_bytes(b"video")
    # AcmeSync was run by hand into output/acme-by-hand, which holds its document; Gateway's hand run has none yet;
    # Weekly's run was reset to 0: its record stays, no phase's file does
    for video_name, folder_name, has_document in (("AcmeSync.mp4", "acme-by-hand", True), ("Gateway.mp4", "gate-1", False),
                                                  ("Weekly.mp4", "weekly-reset", False)):
        folder = root / "output" / folder_name
        paths = RunPaths(repository=inbox_root, module=video_name, output_root_override=folder)
        store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                                  RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
        store.save()
        if has_document:
            (folder / "04_document.md").write_text("# AcmeSync", encoding="utf-8")
        elif video_name == "Gateway.mp4":
            # Gateway's run holds its transcript and no frames: it stopped in phase 2
            (folder / output_contract.TRANSCRIPT_FILE).write_text('{"segments": []}', encoding="utf-8")
    previous = _swap_run_roots({"knowledge": root / "knowledge", "output": root / "output"})
    try:
        items = {item["name"]: item for item in run_status_service.list_inbox_items(inbox_root, root / "knowledge")}
    finally:
        _swap_run_roots(previous)
    harness.check("a video whose hand run holds its document is done, and names that run",
                  items["AcmeSync.mp4"]["state"] == "done"
                  and [run["name"] for run in items["AcmeSync.mp4"]["runs"]] == ["acme-by-hand"], str(items["AcmeSync.mp4"]))
    harness.check("a video with a run and no document yet is started", items["Gateway.mp4"]["state"] == "started",
                  str(items["Gateway.mp4"]))
    harness.check("a started run names the phase it stopped in, a done run none",
                  items["Gateway.mp4"]["runs"][0]["stopped_phase"] == 2 and items["AcmeSync.mp4"]["runs"][0]["stopped_phase"] == 0,
                  str((items["Gateway.mp4"]["runs"], items["AcmeSync.mp4"]["runs"])))
    harness.check("a video no run names is new", items["Jonas.mp4"]["state"] == "new" and items["Jonas.mp4"]["runs"] == [],
                  str(items["Jonas.mp4"]))
    harness.check("a video whose run was reset to 0 is new again, as if it never ran",
                  items["Weekly.mp4"]["state"] == "new" and items["Weekly.mp4"]["runs"] == [], str(items["Weekly.mp4"]))
    harness.check("and its run folder is still named as its own, so the page lists it nowhere else",
                  items["Weekly.mp4"]["empty_runs"] == [{"root": "output", "name": "weekly-reset"}]
                  and items["Gateway.mp4"]["empty_runs"] == [], str(items["Weekly.mp4"]))


def test_a_started_run_names_the_phase_its_own_page_would_resume_from():
    root = Path(tempfile.mkdtemp())
    # the record says phases 1 to 3 are complete (a PDF's phase 3 writes no file of its own), and no phase file is on
    # disk: the Inbox's "phase N" and the run's own "Resume from N" must name the same phase
    folder = root / "output" / "pdf-run"
    paths = RunPaths(repository=root, module="report.pdf", output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    for phase in ("transcribe", "frames_preparing", "frames_processing"):
        store.set_phase(phase, "complete")
    store.save()
    previous = _swap_run_roots({"output": root / "output"})
    try:
        run = next(run for run in run_status_service.list_run_folders() if run["name"] == "pdf-run")
        detail = run_status_service.describe_run_folder("output", "pdf-run")
    finally:
        _swap_run_roots(previous)
    first_open = next(phase["number"] for phase in detail["phases"] if phase["status"] != "complete")
    harness.check("the stopped phase is the first phase the run's own page does not call complete",
                  run["stopped_phase"] == first_open == 4, f"stopped_phase {run['stopped_phase']}, first open {first_open}")


def test_a_listed_run_held_at_its_pause_says_paused_and_one_still_ending_its_call_says_pausing_defect():
    # Defect: the owner paused a run on a second machine, it held, and its pill in the runs list went on saying running,
    # since the list's rows carried no pause. Fix: each live row says "paused" or "pausing", the Inbox's rows too.
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-paused")
    log = run_status_service.find_newest_log(folder)
    run_dir = run_status_service.find_newest_run_dir(folder)
    stand_in = Path(tempfile.mkdtemp()) / "main.py"
    stand_in.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        before = next(run for run in run_status_service.list_run_folders() if run["name"] == "AcmeSync-paused")
        control_signal_service.request(run_dir, control_signal_service.PAUSE)
        log.write_text("phase 3: batch 4 sent to the model\n", encoding="utf-8")
        pausing = next(run for run in run_status_service.list_run_folders() if run["name"] == "AcmeSync-paused")
        log.write_text(f"phase 3: paused {control_signal_service.HELD_LINE_END}\n", encoding="utf-8")
        paused = next(run for run in run_status_service.list_run_folders() if run["name"] == "AcmeSync-paused")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    harness.check("a live run with no pause asked says nothing of one", before["is_live"] and before["pause"] == "",
                  str(before.get("pause")))
    harness.check("a pause asked while a model call still ends reads pausing", pausing["pause"] == "pausing",
                  str(pausing.get("pause")))
    harness.check("a run holding at its pause reads paused", paused["pause"] == "paused", str(paused.get("pause")))


def test_a_listed_run_names_its_provider_set_so_the_running_menu_can_show_it_defect():
    # Defect: the running count opened whichever running run came first, and the run list carried no provider set to
    # tell three running runs apart. Fix: each listed run names its provider set and, while it runs, its phase.
    root = Path(tempfile.mkdtemp())
    folder = root / "output" / "second-host-run"
    paths = RunPaths(repository=root, module="call.mp4", output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    store.set_phase("transcribe", "complete")
    store.save()
    run_provider_set_service.write_chosen_provider_set(folder, "second-host")
    previous = _swap_run_roots({"output": root / "output"})
    try:
        run = next(run for run in run_status_service.list_run_folders() if run["name"] == "second-host-run")
    finally:
        _swap_run_roots(previous)
    harness.check("the listed run names the provider set it chose", run["provider_set"] == "second-host", str(run["provider_set"]))
    harness.check("a run not going names no live phase", run["live_phase"] == 0, str(run["live_phase"]))
    # Defect: the Inbox's own run entries copied 11 fields of each run without its provider set, so every running pill
    # said "running · default". Fix: they carry the provider set too.
    entries = run_status_service._list_video_runs([run], str(Path(run["video"]).resolve())) if run["video"] else []
    harness.check("the Inbox's entry of the run names its provider set",
                  [entry.get("provider_set") for entry in entries] == ["second-host"], f"video {run['video']!r}, entries {entries}")


def test_a_branch_of_an_inbox_video_stands_apart_and_a_blocked_run_says_so():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    (inbox_root / "Gateway.mp4").write_bytes(b"video")
    # Gateway's run is done; a branch of it from phase 3 is blocked in phase 3
    for folder_name, is_branch in (("gateway", False), ("gateway-from-3", True)):
        folder = root / "output" / folder_name
        paths = RunPaths(repository=inbox_root, module="Gateway.mp4", output_root_override=folder)
        store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                                  RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
        store.set_phase("transcribe", "complete")
        store.set_phase("frames_preparing", "complete")
        if is_branch:
            store.set_phase("frames_processing", "blocked")
        store.save()
        if is_branch:
            json_store_utils.write_json(folder / run_branch_service.BRANCH_FILE, {"from": "gateway", "phase": 3})
        else:
            (folder / "04_document.md").write_text("# Gateway", encoding="utf-8")
    previous = _swap_run_roots({"output": root / "output"})
    try:
        items = {item["name"]: item for item in run_status_service.list_inbox_items(inbox_root, root / "knowledge")}
        folders = {run["name"]: run for run in run_status_service.list_run_folders()}
    finally:
        _swap_run_roots(previous)
    harness.check("the video's row keeps its own run, never the branch made from it",
                  [run["name"] for run in items["Gateway.mp4"]["runs"]] == ["gateway"], str(items["Gateway.mp4"]["runs"]))
    harness.check("the branch is a derived run folder of its own, so the page lists it apart",
                  folders["gateway-from-3"]["is_derived"] and not folders["gateway"]["is_derived"],
                  str({name: run["is_derived"] for name, run in folders.items()}))
    harness.check("a run with a blocked phase says it is blocked, a done one does not",
                  folders["gateway-from-3"]["is_blocked"] and not folders["gateway"]["is_blocked"],
                  str({name: run["is_blocked"] for name, run in folders.items()}))


def test_a_run_lists_its_chapters_once_the_agenda_is_written():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "Gateway")
    paths = RunPaths(repository=Path("/in"), module="Gateway.mp4", output_root_override=folder)
    previous = _swap_run_roots({"output": root / "output"})
    try:
        before = run_status_service.describe_run_folder("output", "Gateway")["chapters"]
        document_file_service.write_parts(paths, {"points": [{"title": "Login flow"}, {"title": "Token refresh"}]})
        after = run_status_service.describe_run_folder("output", "Gateway")["chapters"]
    finally:
        _swap_run_roots(previous)
    harness.check("no chapter before the agenda, then each agenda point as a numbered chapter",
                  before == [] and after == [{"number": 1, "title": "Login flow"}, {"number": 2, "title": "Token refresh"}],
                  str((before, after)))


def test_phase_5_lists_its_steps_with_each_rejection_from_its_conversation_log():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "Gateway")
    log = folder / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"followUp": "gaps", "tool": "read_notes", "result": "Rejected words in a note"},
            {"followUp": "gaps", "tool": "submit_gaps", "result": "Rejected. 1 finding\n- GAP_UNSOURCED: a gap names no line"},
            {"followUp": "gaps", "tool": "submit_gaps", "result": "accepted"},
            {"followUp": "resolutions", "tool": "submit_resolutions", "result": "accepted"}]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    steps = document_step_history_service.list_review_steps(folder)
    harness.check("each follow-up once, in the order it ran, accepted, the rejected submission kept with its finding",
                  [(step["step"], step["status"], len(step["rejections"])) for step in steps]
                  == [("gaps", "accepted", 1), ("resolutions", "accepted", 0)]
                  and steps[0]["rejections"][0]["findings"][0]["code"] == "GAP_UNSOURCED", str(steps))


def test_phase_5_lists_a_step_asked_and_not_answered_yet_defect():
    # Phase 5's card said "No step has been asked yet" through its first step's 25 minutes: a turn reaches the log only when it ends.
    # Now a follow-up whose request is written and whose turn has not ended is listed as waiting, which the page shows as running
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "Gateway")
    requests = folder / output_contract.REVIEW_DIR_NAME / "logs" / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / "review-todos.md").write_text("List what a reader still cannot know.", encoding="utf-8")
    steps = document_step_history_service.list_review_steps(folder)
    harness.check("the asked step is listed, waiting, with no call yet",
                  [(step["step"], step["status"], step["calls"]) for step in steps] == [("review todos", "waiting", 0)],
                  str(steps))


def test_a_run_names_its_published_document_once_there_is_one():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "Gateway")
    paths = RunPaths(repository=Path("/in"), module="Gateway.mp4", output_root_override=folder)
    previous = _swap_run_roots({"output": root / "output"})
    try:
        before = run_status_service.describe_run_folder("output", "Gateway")["document_file"]
        paths.summary.write_text("# Gateway login\n\nText.\n", encoding="utf-8")
        published = document_folder_service.build_document_path(paths)
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_text("# Gateway login\n", encoding="utf-8")
        after = run_status_service.describe_run_folder("output", "Gateway")["document_file"]
    finally:
        _swap_run_roots(previous)
    harness.check("no document file before it is published, then its path inside the run folder",
                  before == "" and after == str(published.relative_to(folder)), str((before, after)))


def test_the_inbox_lists_each_state_newest_first_by_the_date_that_state_means():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    # Two done videos, their documents written at 1000 and 3000; two started ones, their logs last changed at 2000
    # and 4000. The names run the other way, so a list in name order fails.
    for video_name, document_at, log_at in (("A-done-old.mp4", 1000, None), ("B-done-new.mp4", 3000, None),
                                            ("C-started-old.mp4", None, 2000), ("D-started-new.mp4", None, 4000)):
        (inbox_root / video_name).write_bytes(b"video")
        folder = root / "output" / video_name.removesuffix(".mp4")
        paths = RunPaths(repository=inbox_root, module=video_name, output_root_override=folder)
        store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                                  RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
        store.save()
        # a started run has written phase 1 at least: one with no phase file is as if it never ran
        (folder / output_contract.TRANSCRIPT_FILE).write_text('{"segments": []}', encoding="utf-8")
        log = paths.run_dir("2026-10-01_09-00") / "09-00.log"
        log.write_text("line\n", encoding="utf-8")
        os.utime(log, (log_at or 500, log_at or 500))
        if document_at:
            document = folder / output_contract.SUMMARY_FILE
            document.write_text("# Doc", encoding="utf-8")
            os.utime(document, (document_at, document_at))
    previous = _swap_run_roots({"output": root / "output"})
    try:
        items = run_status_service.list_inbox_items(inbox_root, root / "output")
    finally:
        _swap_run_roots(previous)
    done = [(item["name"], item["state_at"]) for item in items if item["state"] == "done"]
    started = [(item["name"], item["state_at"]) for item in items if item["state"] == "started"]
    harness.check("done videos come newest first by when their document was written",
                  done == [("B-done-new.mp4", 3000), ("A-done-old.mp4", 1000)], str(done))
    harness.check("started videos come newest first by when their run last changed its log",
                  started == [("D-started-new.mp4", 4000), ("C-started-old.mp4", 2000)], str(started))

    (inbox_root / ".trash").mkdir()
    for name in ("Z-moved-first.mp4", "A-moved-last.mp4"):
        (inbox_root / name).write_bytes(b"video")
        (inbox_root / name).rename(inbox_root / ".trash" / name)
    trash = [entry["name"] for entry in inbox_move_service.list_moved(inbox_root, "trash")]
    harness.check("the trash lists the newest move first", trash == ["A-moved-last.mp4", "Z-moved-first.mp4"], str(trash))


def test_a_run_folder_no_inbox_video_owns_moves_whole_to_the_archive_or_trash_and_back():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    _make_run_folder(root / "output", "Jonas-comparison")
    _make_run_folder(root / "output", "Gateway-live")
    previous_roots = _swap_run_roots({"output": root / "output"})
    previous_inbox = config.INBOX_DIR
    previous_live = run_status_service.list_live_pids
    config.INBOX_DIR = inbox_root
    run_status_service.list_live_pids = lambda folder: [4242] if folder.name == "Gateway-live" else []
    try:
        client = TestClient(web_api_service.build_app())
        moved = client.post("/api/inbox/archive", json={"run_folders": ["output/Jonas-comparison", "output/Gateway-live",
                                                                         "output/../escape"]}).json()
        listed = [row["name"] for row in run_status_service.list_run_folders()]
        archive = client.get("/api/inbox/archive").json()
        is_in_archive = (inbox_root / ".archive" / "Jonas-comparison" / output_contract.RUNS_DIR_NAME).is_dir()
        restored = client.post("/api/inbox/archive/restore", json={"names": ["Jonas-comparison"]}).json()
        listed_after = [row["name"] for row in run_status_service.list_run_folders()]
    finally:
        config.INBOX_DIR = previous_inbox
        run_status_service.list_live_pids = previous_live
        _swap_run_roots(previous_roots)
    harness.check("a run folder moves whole into the archive and leaves the run lists",
                  moved[0] == {"video": "output/Jonas-comparison", "moved_as": "Jonas-comparison", "error": ""}
                  and is_in_archive and "Jonas-comparison" not in listed, str((moved, listed)))
    harness.check("a run folder a run is writing into does not move", "a run is writing into it" in moved[1]["error"]
                  and (root / "output" / "Gateway-live").is_dir(), str(moved[1]))
    harness.check("a name outside the run roots is refused", moved[2]["error"] != "", str(moved[2]))
    harness.check("the archive lists the run folder with the path it came from",
                  [(entry["name"], entry["run_folder"]) for entry in archive]
                  == [("Jonas-comparison", str((root / "output" / "Jonas-comparison").resolve()))], str(archive))
    harness.check("unarchive puts the run folder back where it was, not into the inbox",
                  restored[0]["error"] == "" and (root / "output" / "Jonas-comparison" / output_contract.RUNS_DIR_NAME).is_dir()
                  and not (inbox_root / "Jonas-comparison").exists() and "Jonas-comparison" in listed_after,
                  str((restored, listed_after)))


def test_a_trash_entry_removed_by_hand_leaves_no_record_behind():
    """A video deleted from the trash in Finder left its line in `.origins.json` (2026-10-04)."""

    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    inbox_root.mkdir()
    for name in ("AcmeSync.mp4", "Jonas.mp4"):
        (inbox_root / name).write_bytes(b"video")
        inbox_move_service.move_out_of_inbox(inbox_root, inbox_root / name, "trash")
    (inbox_root / ".trash" / "Jonas.mp4").unlink()
    listed = [entry["name"] for entry in inbox_move_service.list_moved(inbox_root, "trash")]
    origins = json.loads((inbox_root / ".trash" / ".origins.json").read_text(encoding="utf-8"))
    harness.check("the listing and the record hold only what is there", listed == ["AcmeSync.mp4"]
                  and list(origins) == ["AcmeSync.mp4"], f"{listed} {list(origins)}")


def test_trash_entries_are_deleted_for_good_only_from_the_trash():
    """The trash's Delete permanently removes the chosen entries and their records; nothing outside the trash."""

    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    inbox_root.mkdir()
    for name in ("AcmeSync.mp4", "Jonas.mp4"):
        (inbox_root / name).write_bytes(b"video")
        inbox_move_service.move_out_of_inbox(inbox_root, inbox_root / name, "trash")
    (inbox_root / "Gateway.mp4").write_bytes(b"video")
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        deleted = client.post("/api/inbox/trash/delete", json={"names": ["AcmeSync.mp4", "../Gateway.mp4", "Nothing.mp4"]}).json()
    finally:
        config.INBOX_DIR = previous_inbox
    origins = json.loads((inbox_root / ".trash" / ".origins.json").read_text(encoding="utf-8"))
    harness.check("the chosen entry and its record are gone; the other stays",
                  not (inbox_root / ".trash" / "AcmeSync.mp4").exists() and list(origins) == ["Jonas.mp4"], str(origins))
    harness.check("a name leading out of the trash, or not in it, is refused and nothing else is touched",
                  [row["error"] != "" for row in deleted] == [False, True, True] and (inbox_root / "Gateway.mp4").exists(),
                  str(deleted))


def test_the_step_a_live_run_works_on_counts_as_running_not_as_its_old_block():
    """A resume asks its blocked chapter again, and the page showed that chapter blocked beside the running run
    (2026-10-04)."""

    steps = [("chapter-06-text", "accepted"), ("chapter-07-text-1", "blocked"), ("chapter-07-text-2", "waiting")]
    live = run_status_service.mark_step_in_work(steps, is_live=True)
    stopped = run_status_service.mark_step_in_work(steps, is_live=False)
    harness.check("live: the first step not accepted is running", [status for _, status in live]
                  == ["accepted", "running", "waiting"], str(live))
    harness.check("stopped: the block stays a block", stopped == steps, str(stopped))


def test_an_inbox_video_moves_to_the_trash_and_leaves_the_listing():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    (inbox_root / "Gateway meeting").mkdir(parents=True)
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    (inbox_root / "Gateway meeting" / "Gateway.mp4").write_bytes(b"video")
    (inbox_root / "Gateway meeting" / "Gateway.vtt").write_text("WEBVTT", encoding="utf-8")
    (inbox_root / ".trash").mkdir()
    (inbox_root / ".trash" / "AcmeSync.mp4").write_bytes(b"older")
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        moved = client.post("/api/inbox/trash", json={"videos": [str(inbox_root / "AcmeSync.mp4"),
                                                                 str(inbox_root / "Gateway meeting" / "Gateway.mp4"),
                                                                 str(root / "elsewhere.mp4")]}).json()
    finally:
        config.INBOX_DIR = previous_inbox
    harness.check("a video moves into the trash, numbered when the trash holds its name",
                  moved[0]["moved_as"] == "AcmeSync-2.mp4" and (inbox_root / ".trash" / "AcmeSync.mp4").read_bytes()
                  == b"older", str(moved))
    harness.check("a video in a meeting folder moves with its folder and transcript",
                  moved[1]["moved_as"] == "Gateway meeting"
                  and (inbox_root / ".trash" / "Gateway meeting" / "Gateway.vtt").is_file(), str(moved))
    harness.check("a path that is no inbox video is refused by name", "elsewhere.mp4 is not a recording in the inbox"
                  in moved[2]["error"], str(moved))
    harness.check("the inbox no longer lists the moved videos", inbox_service.list_inbox_videos(inbox_root) == [],
                  str(inbox_service.list_inbox_videos(inbox_root)))
    trash = {entry["name"]: entry for entry in inbox_move_service.list_moved(inbox_root, "trash")}
    harness.check("the trash lists what it holds, a meeting folder as a folder",
                  sorted(trash) == ["AcmeSync-2.mp4", "AcmeSync.mp4", "Gateway meeting"]
                  and trash["Gateway meeting"]["is_folder"], str(trash))
    restored = inbox_move_service.move_back(inbox_root, "trash", "Gateway meeting")
    harness.check("a trashed meeting folder comes back into the inbox and is listed again",
                  restored == inbox_root / "Gateway meeting"
                  and [video.name for video in inbox_service.list_inbox_videos(inbox_root)] == ["Gateway.mp4"],
                  str(restored))
    try:
        inbox_move_service.move_back(inbox_root, "trash", "../AcmeSync.mp4")
        message = ""
    except UnknownInboxItemError as exc:
        message = str(exc)
    harness.check("a name outside the trash is refused", "is not in the trash" in message, message)


def test_an_archived_video_keeps_its_run_and_recording_and_comes_back_under_its_name():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    (inbox_root / "Jonas.mp4").write_bytes(b"video")
    (inbox_root / ".archive").mkdir()
    (inbox_root / ".archive" / "AcmeSync.mp4").write_bytes(b"older")
    folder = root / "output" / "AcmeSync"
    paths = RunPaths(repository=inbox_root, module="AcmeSync.mp4", output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    store.save()
    (folder / "04_document.md").write_text("# AcmeSync", encoding="utf-8")
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge", "output": root / "output"})
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        moved = client.post("/api/inbox/archive", json={"videos": [str(inbox_root / "AcmeSync.mp4")]}).json()
        archive = client.get("/api/inbox/archive").json()
        recording = run_artifact_service.find_recording(folder, run_status_service.describe_run_video(folder))
        listed = [item["name"] for item in client.get("/api/inbox").json()]
        restored = client.post("/api/inbox/archive/restore", json={"names": [moved[0]["moved_as"]]}).json()
        archive_after = client.get("/api/inbox/archive").json()
        items = {item["name"]: item for item in run_status_service.list_inbox_items(inbox_root, root / "output")}
    finally:
        config.INBOX_DIR = previous_inbox
        _swap_run_roots(previous_roots)
    entries = {entry["name"]: entry for entry in archive}
    harness.check("a video moves into the archive, numbered when the archive holds its name, and leaves the inbox",
                  moved[0]["moved_as"] == "AcmeSync-2.mp4" and listed == ["Jonas.mp4"], str(moved) + str(listed))
    harness.check("the archive lists the video with the run that names it, its state and its topic field",
                  [run["name"] for run in entries["AcmeSync-2.mp4"]["runs"]] == ["AcmeSync"]
                  and entries["AcmeSync-2.mp4"]["state"] == "done" and entries["AcmeSync.mp4"]["runs"] == []
                  and "topic" in entries["AcmeSync-2.mp4"], str(entries))
    harness.check("the run still finds its recording in the archive",
                  recording == inbox_root / ".archive" / "AcmeSync-2.mp4", str(recording))
    harness.check("Unarchive puts the video back under the name it had, and its run is its own again",
                  restored[0]["restored_as"] == "AcmeSync.mp4" and [entry["name"] for entry in archive_after]
                  == ["AcmeSync.mp4"] and items["AcmeSync.mp4"]["state"] == "done", str(restored) + str(items))


def test_frames_carry_their_second_and_the_recording_plays_while_it_is_there():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    video = inbox_root / "AcmeSync.mp4"
    video.write_bytes(b"0123456789")
    folder = root / "knowledge" / "AcmeSync"
    paths = RunPaths(repository=inbox_root, module="AcmeSync.mp4", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    (folder / "frames").mkdir()
    (folder / "frames" / "frame-000.jpg").write_bytes(b"jpg")
    (folder / "frames" / "dropped").mkdir()
    (folder / "frames" / "dropped" / "frame-001.jpg").write_bytes(b"jpg")
    (folder / "02_frames.json").write_text(json.dumps({"items": [{"slug": "frame-000", "path": "frames/frame-000.jpg",
                                                                  "timestamp": 419.0, "dropScore": 0.12, "tag": "text"}],
                                                       "droppedFrames": [{"slug": "frame-001", "timestamp": 425.0,
                                                                          "path": "frames/dropped/frame-001.jpg",
                                                                          "dropScore": 0.97, "tag": "people"}]}),
                                             encoding="utf-8")
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        groups = {group["key"]: group for group in client.get("/api/runs/knowledge/AcmeSync/artifacts").json()}
        has_recording_before = client.get("/api/runs/knowledge/AcmeSync").json()["has_recording"]
        played = client.get("/api/runs/knowledge/AcmeSync/recording", headers={"Range": "bytes=2-5"})
        inbox_played = client.get("/api/inbox/video", params={"path": str(video)})
        outsider = client.get("/api/inbox/video", params={"path": str(root / "elsewhere.mp4")})
        video.rename(root / "moved.mp4")
        gone = client.get("/api/runs/knowledge/AcmeSync/recording")
        has_recording_after = client.get("/api/runs/knowledge/AcmeSync").json()["has_recording"]
    finally:
        _swap_run_roots(previous_roots)
        config.INBOX_DIR = previous_inbox
    harness.check("a frame carries its second in the recording, its drop score and its tag",
                  [(item["name"], item["time_s"], item["drop_score"], item["tag"]) for item in groups["frames"]["items"]]
                  == [("frame-000.jpg", 419.0, 0.12, "text")], str(groups["frames"]))
    harness.check("a frame the knowledge filter dropped is shown in its own group, with its second and drop score",
                  [(item["path"], item["time_s"], item["drop_score"], item["tag"]) for item in groups["dropped"]["items"]]
                  == [("frames/dropped/frame-001.jpg", 425.0, 0.97, "people")] and groups["dropped"]["title"] == "Dropped frames",
                  str(groups.get("dropped")))
    harness.check("the recording is no artifact group; the run says it has one", "recording" not in groups
                  and has_recording_before, str(list(groups)))
    harness.check("the recording is served in ranges, so a player can seek", played.status_code == 206
                  and played.content == b"2345", f"{played.status_code} {played.content!r}")
    harness.check("an inbox video plays, and a path outside the inbox is refused by name",
                  inbox_played.status_code == 200 and outsider.status_code == 404
                  and "elsewhere.mp4 is not a recording in the inbox" in outsider.json()["detail"], outsider.text)
    harness.check("a recording moved away is a 404 that says so, and the run no longer says it has one",
                  gone.status_code == 404 and "not at" in gone.json()["detail"] and not has_recording_after, gone.text)


def test_a_recording_kept_in_its_run_folder_still_plays_and_the_page_can_move_it_back():
    """A done run keeps its recording in `recording/`."""

    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    video = inbox_root / "AcmeSync.mp4"
    video.write_bytes(b"0123456789")
    folder = root / "knowledge" / "AcmeSync"
    paths = RunPaths(repository=inbox_root, module="AcmeSync.mp4", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": {
        "transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete",
        "finalize": "complete", "review": "complete"}}, inbox_root, folder, video)
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        detail = client.get("/api/runs/knowledge/AcmeSync").json()
        played = client.get("/api/runs/knowledge/AcmeSync/recording", headers={"Range": "bytes=2-5"})
        moved = client.post("/api/runs/knowledge/AcmeSync/recording/to-inbox").json()
        kept_back = client.post("/api/runs/knowledge/AcmeSync/recording/to-run").json()
    finally:
        _swap_run_roots(previous_roots)
        config.INBOX_DIR = previous_inbox
    harness.check("the run says its recording is kept in its folder, and it still has one",
                  detail["is_recording_kept"] and detail["has_recording"], str(detail))
    harness.check("the recording plays from the run folder", played.status_code == 206 and played.content == b"2345",
                  f"{played.status_code} {played.content!r}")
    harness.check("moving it back puts the recording in the inbox and says so",
                  moved["exit_code"] == 0 and "recording moved back to" in moved["output"] and video.is_file(), str(moved))
    harness.check("keeping it again is refused by name for a run whose record is not done",
                  kept_back["exit_code"] == 2 and "is not a done run" in kept_back["output"], str(kept_back))
    harness.check("a kept recording is not offered to keep again", detail["can_keep_recording"] is False, str(detail))


def test_a_done_run_row_shows_the_size_of_the_recording_kept_in_its_folder_defect():
    # A done run's row showed no file size, since its recording had moved from the inbox into the run folder; the run
    # list now gives each run the size of the recording it keeps there
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    video = inbox_root / "AcmeSync.mp4"
    video.write_bytes(b"0123456789")
    folder = root / "knowledge" / "AcmeSync"
    paths = RunPaths(repository=inbox_root, module="AcmeSync.mp4", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    run_recording_service.keep_recording_if_done({"status": "complete", "phases": {
        "transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete",
        "finalize": "complete", "review": "complete"}}, inbox_root, folder, video)
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    run_status_service.clear_shared_run_list()
    try:
        rows = run_status_service.list_run_folders(60.0)
    finally:
        run_status_service.clear_shared_run_list()
        _swap_run_roots(previous_roots)
    sizes = {row["name"]: row["size_bytes"] for row in rows}
    harness.check("the done run carries the bytes of the recording in its folder", sizes == {"AcmeSync": 10}, str(sizes))


def test_the_page_deletes_a_recording_into_the_trash_and_uploads_a_missing_one():
    """Delete moves a run's recording into the inbox trash; Upload saves a missing one where the run record names it."""

    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    video = inbox_root / "AcmeSync.mp4"
    video.write_bytes(b"0123456789")
    folder = root / "knowledge" / "AcmeSync"
    paths = RunPaths(repository=inbox_root, module="AcmeSync.mp4", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        deleted = client.post("/api/runs/knowledge/AcmeSync/recording/delete")
        nothing = client.post("/api/runs/knowledge/AcmeSync/recording/delete")
        has_after_delete = client.get("/api/runs/knowledge/AcmeSync").json()["has_recording"]
        uploaded = client.post("/api/runs/knowledge/AcmeSync/recording/upload",
                               files={"file": ("phone-copy.mp4", b"<video bytes>", "video/mp4")})
        again = client.post("/api/runs/knowledge/AcmeSync/recording/upload",
                            files={"file": ("phone-copy.mp4", b"<video bytes>", "video/mp4")})
        has_after_upload = client.get("/api/runs/knowledge/AcmeSync").json()["has_recording"]
    finally:
        _swap_run_roots(previous_roots)
        config.INBOX_DIR = previous_inbox
    harness.check("Delete moves it into the inbox trash", deleted.status_code == 200
                  and (inbox_root / ".trash" / "AcmeSync.mp4").is_file() and not has_after_delete, deleted.text)
    harness.check("with nothing left, Delete is a 404 that says so", nothing.status_code == 404
                  and "no recording" in nothing.json()["detail"], nothing.text)
    harness.check("Upload saves it under the name the run record names", uploaded.status_code == 200
                  and video.read_bytes() == b"<video bytes>" and has_after_upload, uploaded.text)
    harness.check("a second upload is refused: the recording is there", again.status_code == 409
                  and "there already" in again.json()["detail"], again.text)


def test_each_phase_and_the_whole_run_have_their_time_over_every_session():
    root = Path(tempfile.mkdtemp())
    folder = root / "AcmeSync"
    first, second = folder / "logs" / "2026-10-01_09-00", folder / "logs" / "2026-10-01_10-00"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    rows_first = [
        {"level": "session", "session": "s1", "startedAt": "2026-10-01T09:00:00.000"},
        {"level": "phase", "session": "s1", "phase": "transcribe", "durationS": 30.0},
        {"level": "item", "session": "s1", "phase": "frames_processing", "startedAtS": 1000.0, "durationS": 20.0},
        {"level": "item", "session": "s1", "phase": "frames_processing", "startedAtS": 1020.0, "durationS": 25.0},
        {"level": "video", "session": "s1", "durationS": 90.0},
    ]
    session_start = datetime.fromisoformat("2026-10-01T10:00:00.000").timestamp()
    rows_second = [
        {"level": "session", "session": "s2", "startedAt": "2026-10-01T10:00:00.000"},
        {"level": "item", "session": "s2", "phase": "frames_processing", "startedAtS": session_start + 10, "durationS": 15.0},
    ]
    (first / "performance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows_first), encoding="utf-8")
    (second / "performance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows_second), encoding="utf-8")

    ended = run_status_service.describe_timing(folder, None)
    live = run_status_service.describe_timing(folder, "frames_processing", now_s=session_start + 70)
    harness.check("a finished session adds its phase lines, and its unit lines for a phase it never closed",
                  ended["phases"] == {"transcribe": 30.0, "frames_processing": 60.0} and not ended["is_counting"], str(ended))
    harness.check("the whole run is each ended session's length, one cut off counted by its phases",
                  ended["total_s"] == 90.0 + 15.0, str(ended))
    harness.check("while live, the running phase counts from its first unit of the session up to now",
                  live["phases"]["frames_processing"] == 45.0 + 60.0 and live["is_counting"], str(live))
    harness.check("while live, the run counts the current session from its start up to now",
                  live["total_s"] == 90.0 + 70.0, str(live))


def test_phase_2_counts_only_its_newest_session_since_every_run_starts_it_over():
    folder = Path(tempfile.mkdtemp()) / "AcmeSync"
    first, second = folder / "logs" / "2026-10-01_09-00", folder / "logs" / "2026-10-01_10-00"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    rows_first = [{"level": "session", "session": "s1", "startedAt": "2026-10-01T09:00:00.000"},
                  {"level": "phase", "session": "s1", "phase": "frames_preparing", "durationS": 3000.0},
                  {"level": "item", "session": "s1", "phase": "frames_processing", "startedAtS": 1000.0, "durationS": 900.0},
                  {"level": "video", "session": "s1", "durationS": 3900.0}]
    session_start = datetime.fromisoformat("2026-10-01T10:00:00.000").timestamp()
    rows_second = [{"level": "session", "session": "s2", "startedAt": "2026-10-01T10:00:00.000"}]
    (first / "performance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows_first), encoding="utf-8")
    (second / "performance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows_second), encoding="utf-8")

    live = run_status_service.describe_timing(folder, "frames_preparing", now_s=session_start + 240)
    (second / "performance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows_second + [
        {"level": "phase", "session": "s2", "phase": "frames_preparing", "durationS": 600.0}]), encoding="utf-8")
    ended = run_status_service.describe_timing(folder, None)
    harness.check("while it runs again, phase 2 counts this session alone, never the earlier try",
                  live["phases"]["frames_preparing"] == 240.0, str(live))
    harness.check("once ended, phase 2 is its newest session's time", ended["phases"]["frames_preparing"] == 600.0,
                  str(ended))
    harness.check("phase 3's time from before phase 2 ran again is gone, while it runs and after",
                  "frames_processing" not in live["phases"] and "frames_processing" not in ended["phases"],
                  f"{live} {ended}")


def test_a_phase_that_printed_its_time_has_ended_and_the_next_one_runs():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-phase3")
    # Phase 3 prints no line when it starts: its first lines name frames. The last phase line is phase 2's time.
    run_status_service.find_newest_log(folder).write_text(
        "phase 2: preparing frames\nphase 2: 30 frame(s)\nphase 2 time: 0.2 s, no model call, complete\n"
        "frames 0 → 4 of 30 at 0:00 …\n", encoding="utf-8")
    stand_in = Path(tempfile.mkdtemp()) / "main.py"
    stand_in.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        detail = run_status_service.describe_run_folder("knowledge", "AcmeSync-phase3")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    statuses = {row["number"]: row["status"] for row in detail["phases"]}
    harness.check("after `phase 2 time:` the running phase is 3, and phase 2 keeps its own status",
                  statuses[3] == "running" and statuses[2] != "running", str(statuses))
    run_status_service.find_newest_log(folder).write_text("phase 5 time: 9.0 s, complete\n", encoding="utf-8")
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        after_review = run_status_service.describe_run_folder("knowledge", "AcmeSync-phase3")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    harness.check("after `phase 5 time:` the running phase is 6, the last one",
                  {row["number"]: row["status"] for row in after_review["phases"]}[6] == "running",
                  str(after_review["phases"]))
    run_status_service.find_newest_log(folder).write_text("phase 6 time: 2.0 s, complete\n", encoding="utf-8")
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        closing = run_status_service.describe_run_folder("knowledge", "AcmeSync-phase3")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    harness.check("after the last phase's time line no phase reads running", all(
        row["status"] != "running" for row in closing["phases"]), str(closing["phases"]))


def test_the_queue_waits_while_a_run_started_on_the_command_line_is_live():
    queue_dir = Path(tempfile.mkdtemp())
    live_runs = ["4242 /venv/bin/python main.py run --video /in/Gateway.mp4 --output /out/Gateway"]
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: list(live_runs))
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=Path("/out/v"))])
        while_live = job_queue_service.run_next_job(queue_dir)
        status_while_live = job_queue_service.list_jobs(queue_dir)[0].status
        live_runs.clear()
        after_it_ended = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
    harness.check("while a run typed on the command line is live, the queued job waits",
                  while_live is None and status_while_live == "queued", f"{while_live} {status_while_live}")
    harness.check("once that run has ended, the queued job runs", after_it_ended is not None
                  and after_it_ended.job_id == queued[0].job_id and after_it_ended.status == "finished", str(after_it_ended))


def test_a_resumed_run_that_only_read_a_finished_phase_shows_the_next_phase_running():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-resumed-3")
    store = run_store_service.RunStore.load(next((folder / "logs").glob("*/run.json")))
    store.set_phase("frames_preparing", "complete")
    store.save()
    # A resume reads phase 2 from its file: it prints a phase 2 line and no time line, then phase 3 starts silently.
    run_status_service.find_newest_log(folder).write_text(
        "phase 2: reading 02_frames.json from disk\nphase 2: 30 frame(s)\n\nframes 40 → 44 of 205 …\n", encoding="utf-8")
    stand_in = Path(tempfile.mkdtemp()) / "main.py"
    stand_in.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        detail = run_status_service.describe_run_folder("knowledge", "AcmeSync-resumed-3")
    finally:
        _swap_run_roots(previous)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    statuses = {row["number"]: row["status"] for row in detail["phases"]}
    harness.check("the log names complete phase 2, so the running phase is the next one not complete: 3",
                  statuses[2] == "complete" and statuses[3] == "running", str(statuses))
    harness.check("a blocked frame is asked again while the resumed run reads frames: pending, never blocked",
                  detail["frames"]["blocked"] == 0 and detail["frames"]["pending"] == 1 and detail["blocked_items"] == [],
                  f"{detail['frames']} {detail['blocked_items']}")


def test_chosen_frames_download_as_one_zip_and_hidden_ones_stay_hidden():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-frames")
    (folder / "frames").mkdir()
    for index in range(3):
        (folder / "frames" / f"frame-00{index}.jpg").write_bytes(b"jpg%d" % index)
    (folder / "02_frames.json").write_text("{}", encoding="utf-8")
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    previous_queue = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        client = TestClient(web_api_service.build_app())
        base = "/api/runs/knowledge/AcmeSync-frames"
        zipped = client.post(f"{base}/zip", json={"paths": ["frames/frame-000.jpg", "frames/frame-002.jpg"],
                                                  "label": "frames"})
        refused = client.post(f"{base}/zip", json={"paths": ["02_frames.json"]})
        hidden = client.post(f"{base}/hidden", json={"paths": ["frames/frame-001.jpg", "frames/frame-002.jpg"],
                                                     "is_hidden": True}).json()
        listed = {item["name"]: item["is_hidden"] for group in client.get(f"{base}/artifacts").json()
                  if group["key"] == "frames" for item in group["items"]}
        shown_again = client.post(f"{base}/hidden", json={"paths": ["frames/frame-002.jpg"], "is_hidden": False}).json()
        record = json.loads((root / "ui" / hidden_artifact_service.HIDDEN_FILE).read_text(encoding="utf-8"))
    finally:
        _swap_run_roots(previous_roots)
        job_queue_service.QUEUE_DIR = previous_queue
    archive = zipfile.ZipFile(io.BytesIO(zipped.content))
    harness.check("the chosen frames come as one zip named after the folder, each at its path",
                  sorted(archive.namelist()) == ["frames/frame-000.jpg", "frames/frame-002.jpg"]
                  and archive.read("frames/frame-002.jpg") == b"jpg2"
                  and "AcmeSync-frames-frames.zip" in zipped.headers["content-disposition"], str(archive.namelist()))
    harness.check("a file that is no artifact is refused from a zip", refused.status_code == 404, refused.text)
    harness.check("hidden frames are listed as hidden, the others not",
                  hidden["hidden"] == ["frames/frame-001.jpg", "frames/frame-002.jpg"]
                  and listed == {"frame-000.jpg": False, "frame-001.jpg": True, "frame-002.jpg": True}, str(listed))
    harness.check("a frame shown again leaves the record, which is kept per folder in ui/",
                  shown_again["hidden"] == ["frames/frame-001.jpg"]
                  and record == {"knowledge/AcmeSync-frames": ["frames/frame-001.jpg"]}, str(record))
    harness.check("hiding touches no file", (folder / "frames" / "frame-001.jpg").read_bytes() == b"jpg1")


def test_a_new_video_s_tags_are_kept_until_its_run_and_go_with_a_rename():
    from services import inbox_tags_service
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    previous = _swap_run_roots({"output": root / "output"})
    try:
        client = TestClient(web_api_service.build_app())
        saved = client.put("/api/inbox/tags", json={"video": str(inbox_root / "AcmeSync.mp4"), "tags": ["lecture", " Lecture ", "week 42"]})
        listed = run_status_service.list_inbox_items(inbox_root, root / "output")[0]["tags"]
        outsider = client.put("/api/inbox/tags", json={"video": str(root / "elsewhere.mp4"), "tags": ["x"]})
        client.post("/api/inbox/rename", json={"video": str(inbox_root / "AcmeSync.mp4"), "name": "AcmeSync review"})
        renamed = run_status_service.list_inbox_items(inbox_root, root / "output")[0]
        client.put("/api/inbox/tags", json={"video": renamed["video"], "tags": []})
        emptied = run_status_service.list_inbox_items(inbox_root, root / "output")[0]["tags"]
    finally:
        config.INBOX_DIR = previous_inbox
        _swap_run_roots(previous)
    harness.check("saved, cleaned as a run's tags are", saved.json()["tags"] == ["lecture", "week 42"], saved.text)
    harness.check("the inbox lists them on the video, so its page shows them after a refresh",
                  listed == ["lecture", "week 42"], str(listed))
    harness.check("a video not in the inbox is refused by name", outsider.status_code == 404 and "elsewhere.mp4" in outsider.text,
                  outsider.text)
    harness.check("a rename keeps them on the video under its new name",
                  renamed["name"] == "AcmeSync review.mp4" and renamed["tags"] == ["lecture", "week 42"], str(renamed))
    harness.check("taking every tag off takes the entry out", emptied == []
                  and str(inbox_root / "AcmeSync.mp4") not in inbox_tags_service.STORE_PATH.read_text(encoding="utf-8"))


def test_a_new_inbox_video_is_renamed_in_place_and_a_bad_name_is_refused():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    (inbox_root / "Gateway meeting").mkdir(parents=True)
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    (inbox_root / "Jonas.mp4").write_bytes(b"video")
    (inbox_root / "Gateway meeting" / "Gateway.mp4").write_bytes(b"video")
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        client = TestClient(web_api_service.build_app())
        renamed = client.post("/api/inbox/rename", json={"video": str(inbox_root / "AcmeSync.mp4"), "name": "AcmeSync review"})
        in_folder = client.post("/api/inbox/rename", json={"video": str(inbox_root / "Gateway meeting" / "Gateway.mp4"),
                                                           "name": "Gateway kickoff.mov"})
        answers = {name: client.post("/api/inbox/rename", json={"video": str(inbox_root / "Jonas.mp4"), "name": name})
                   for name in ("AcmeSync review.mp4", "../Jonas.mp4", "notes.txt", " ")}
        outsider = client.post("/api/inbox/rename", json={"video": str(root / "elsewhere.mp4"), "name": "x.mp4"})
    finally:
        config.INBOX_DIR = previous_inbox
    harness.check("a name typed without its suffix keeps the old one, in the same folder",
                  renamed.json()["name"] == "AcmeSync review.mp4" and (inbox_root / "AcmeSync review.mp4").is_file()
                  and not (inbox_root / "AcmeSync.mp4").exists(), renamed.text)
    harness.check("a video in a meeting folder is renamed inside that folder",
                  (inbox_root / "Gateway meeting" / "Gateway kickoff.mov").is_file(), in_folder.text)
    harness.check("a taken name, a folder, a name that is no video, and an empty name are refused with the reason",
                  "already in the inbox" in answers["AcmeSync review.mp4"].json()["detail"]
                  and "no folders" in answers["../Jonas.mp4"].json()["detail"]
                  and "not a video or an audio file name" in answers["notes.txt"].json()["detail"]
                  and answers[" "].status_code == 400 and (inbox_root / "Jonas.mp4").is_file(),
                  str({name: answer.text for name, answer in answers.items()}))
    harness.check("a path that is no inbox video is refused", outsider.status_code == 404, outsider.text)
    same = inbox_rename_service.rename_inbox_video(inbox_root, inbox_root / "Jonas.mp4", "Jonas")
    harness.check("renaming to the name it has changes nothing", same == (inbox_root / "Jonas.mp4").resolve(), str(same))


def test_a_run_carries_the_topic_phase_4_wrote_and_its_inbox_video_shows_it():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    (inbox_root / "Gateway.mp4").write_bytes(b"video")
    folder = root / "output" / "Gateway"
    paths = RunPaths(repository=inbox_root, module="Gateway.mp4", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    # phase 4 wrote the topic, so phases 1 to 3 wrote their files before it
    (folder / output_contract.TRANSCRIPT_FILE).write_text('{"segments": []}', encoding="utf-8")
    previous = _swap_run_roots({"output": root / "output"})
    try:
        before = run_status_service.list_run_folders()[0]["topic"]
        (folder / "04_writing").mkdir()
        (folder / "04_writing" / "parts.json").write_text(json.dumps({"topic": " Gateway token exchange ", "points": []}),
                                                         encoding="utf-8")
        after = run_status_service.list_run_folders()[0]["topic"]
        item = run_status_service.list_inbox_items(inbox_root, root / "output")[0]
        (folder / "04_writing" / "parts.json").write_text("{torn", encoding="utf-8")
        torn = run_status_service.list_run_folders()[0]["topic"]
    finally:
        _swap_run_roots(previous)
    harness.check("no topic before phase 4 writes one, then the topic it wrote", before == ""
                  and after == "Gateway token exchange", repr((before, after)))
    harness.check("the inbox video carries its run's topic", item["topic"] == "Gateway token exchange"
                  and item["runs"][0]["topic"] == "Gateway token exchange", str(item))
    harness.check("a file that cannot be read is no topic, never an error", torn == "", repr(torn))


def _write_clip(path: Path,
                seconds: int) -> None:
    """A tiny real video of `seconds` seconds at 5 frames a second, so its length can be read back."""

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (32, 32))
    for _ in range(seconds * 5):
        writer.write(numpy.zeros((32, 32, 3), dtype=numpy.uint8))
    writer.release()


def test_a_meeting_in_parts_lists_its_parts_and_plays_each_one_from_where_it_starts():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    meeting = inbox_root / "Weekly AcmeSync"
    meeting.mkdir(parents=True)
    _write_clip(meeting / "part 2.mp4", 3)
    _write_clip(meeting / "part 10.mp4", 2)
    _write_clip(meeting / "part 1.mp4", 2)
    part_2_bytes, part_10_bytes = (meeting / "part 2.mp4").read_bytes(), (meeting / "part 10.mp4").read_bytes()
    folder = root / "output" / "Weekly AcmeSync"
    paths = RunPaths(repository=inbox_root, module="Weekly AcmeSync", output_root_override=folder)
    run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"), RunIdentity.for_run("2026-10-01_09-00", paths),
                                      budgets={}, models=[]).save()
    previous_roots = _swap_run_roots({"output": root / "output"})
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    try:
        item = run_status_service.list_inbox_items(inbox_root, root / "output")[0]
        detail = run_status_service.describe_run_folder("output", "Weekly AcmeSync")
        client = TestClient(web_api_service.build_app())
        second_part = client.get("/api/runs/output/Weekly AcmeSync/recording", params={"part": 1})
        no_such_part = client.get("/api/runs/output/Weekly AcmeSync/recording", params={"part": 7})
        inbox_part = client.get("/api/inbox/video", params={"path": str(meeting), "part": 2})
        renamed = client.post("/api/inbox/rename", json={"video": str(meeting), "name": "Weekly Gateway"})
    finally:
        _swap_run_roots(previous_roots)
        config.INBOX_DIR = previous_inbox
    names = [part["name"] for part in item["parts"]]
    harness.check("the meeting is one inbox item, its size the parts' total, its parts in natural name order",
                  names == ["part 1.mp4", "part 2.mp4", "part 10.mp4"]
                  and item["size_bytes"] == sum(part["size_bytes"] for part in item["parts"]), str(item))
    starts = [(part["name"], part["start_s"]) for part in detail["recording_parts"]]
    harness.check("each part starts where the ones before it end", detail["has_recording"]
                  and starts == [("part 1.mp4", 0.0), ("part 2.mp4", 2.0), ("part 10.mp4", 5.0)], str(starts))
    harness.check("part 2 of the run's recording is served, and a part that does not exist is refused by name",
                  second_part.status_code == 200 and second_part.content == part_2_bytes
                  and no_such_part.status_code == 404 and "has no part 8" in no_such_part.json()["detail"], no_such_part.text)
    harness.check("an inbox meeting plays its part 3", inbox_part.status_code == 200
                  and inbox_part.content == part_10_bytes, str(inbox_part.status_code))
    harness.check("renaming a meeting renames its folder and keeps its parts",
                  renamed.json()["name"] == "Weekly Gateway" and (inbox_root / "Weekly Gateway" / "part 10.mp4").is_file(),
                  renamed.text)


def test_several_files_a_folder_or_a_zip_go_into_the_inbox_each_as_its_own_and_ticked_ones_merge():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr("Gateway review part 1.mp4", b"g1")
        packed.writestr("Gateway review part 2.mp4", b"g2")
        packed.writestr("Gateway review.vtt", b"WEBVTT")
        # A file of a kind no workflow reads (a PDF and a Word file are read now)
        packed.writestr("notes.txt", b"notes")
        packed.writestr("__MACOSX/._Gateway review part 1.mp4", b"x")
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = inbox_root
    # No real run folder is read for a done run with the same recording
    previous_roots = _swap_run_roots({"output": inbox_root.parent / "output"})
    try:
        client = TestClient(web_api_service.build_app())
        saved = client.post("/api/inbox/upload-bundle", files=[
            ("files", ("part 1.mp4", b"w1", "video/mp4")), ("files", ("part 2.mp4", b"w2", "video/mp4")),
            ("files", ("Jonas.mp4", b"j", "video/mp4")), ("files", ("bundle.zip", archive.getvalue(), "application/zip"))],
            data={"paths": ["Weekly AcmeSync/part 1.mp4", "Weekly AcmeSync/part 2.mp4", "", ""]}).json()
        listed = sorted(path.name for path in inbox_service.list_inbox_videos(inbox_root))
        inbox_tags_service.write_video_tags(inbox_root / "part 1.mp4", ["acmesync"])
        merged = client.post("/api/inbox/merge", json={"videos": [str(inbox_root / "part 2.mp4"),
                                                                  str(inbox_root / "part 1.mp4")], "name": "Weekly AcmeSync"})
        one = client.post("/api/inbox/merge", json={"videos": [str(inbox_root / "Jonas.mp4")]})
        unknown = client.post("/api/inbox/merge", json={"videos": [str(inbox_root / "Jonas.mp4"),
                                                                   str(inbox_root / "nowhere.mp4")]})
        nothing = client.post("/api/inbox/upload-bundle", files=[("files", ("notes.txt", b"t", "text/plain"))])
    finally:
        config.INBOX_DIR = previous_inbox
        _swap_run_roots(previous_roots)
    harness.check("the zip is unpacked to its videos, each its own recording, its transcript in no folder of its name",
                  saved["added"] == ["part 1.mp4", "part 2.mp4", "Jonas.mp4", "Gateway review part 1.mp4",
                                     "Gateway review part 2.mp4"] and [entry["name"] for entry in saved["refused"]]
                  == ["Gateway review.vtt"], str(saved))
    harness.check("the inbox lists each of them, nothing of the text file or the mac folder",
                  listed == ["Gateway review part 1.mp4", "Gateway review part 2.mp4", "Jonas.mp4", "part 1.mp4",
                             "part 2.mp4"], str(listed))
    folder = inbox_root / "Weekly AcmeSync"
    harness.check("two ticked recordings merge into one folder, parts in name order, their tags with it",
                  merged.status_code == 200 and sorted(path.name for path in folder.iterdir())
                  == ["01 part 1.mp4", "02 part 2.mp4"] and inbox_tags_service.read_video_tags(folder) == ["acmesync"],
                  merged.text)
    harness.check("merging one recording is refused, one the inbox does not list a 404",
                  one.status_code == 422 and unknown.status_code == 404, f"{one.text} | {unknown.text}")
    harness.check("an upload with no video is refused", nothing.status_code == 400 and "nothing in the upload is a video"
                  in nothing.json()["detail"], nothing.text)


def test_the_transcript_is_served_line_by_line_with_its_seconds():
    """The Recording tab lists phase 1's transcript; a run before phase 1 has no lines."""

    root = Path(tempfile.mkdtemp())
    folder = root / "knowledge" / "AcmeSync"
    folder.mkdir(parents=True)
    json_store_utils.write_json(folder / output_contract.TRANSCRIPT_FILE, {"segments": [
        {"start": 0.0, "end": 4.5, "text": " Jonas opens the gateway. ", "words": []},
        {"start": 4.5, "end": 9.25, "text": "The token goes to the provider.", "words": []}]})
    (root / "knowledge" / "Empty").mkdir()
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        served = client.get("/api/runs/knowledge/AcmeSync/transcript")
        empty = client.get("/api/runs/knowledge/Empty/transcript")
        outside = client.get("/api/runs/knowledge/nothing-here/transcript")
        downloaded = client.get("/api/runs/knowledge/AcmeSync/transcript/download")
        no_download = client.get("/api/runs/knowledge/Empty/transcript/download")
    finally:
        _swap_run_roots(previous)

    harness.check("each line with its first and last second, its text trimmed, no speaker without diarization", served.status_code == 200
                  and served.json() == {"lines": [{"start_s": 0.0, "end_s": 4.5, "text": "Jonas opens the gateway.",
                                                   "speaker": None},
                                                  {"start_s": 4.5, "end_s": 9.25,
                                                   "text": "The token goes to the provider.", "speaker": None}]},
                  served.text)
    harness.check("a run with no transcript yet has no lines", empty.json() == {"lines": []}, empty.text)
    harness.check("a folder that is no run is a 404", outside.status_code == 404, outside.text)
    harness.check("the transcript downloads as text, one line per thing said with its time, named after the run",
                  downloaded.status_code == 200 and downloaded.text == "[00:00] Jonas opens the gateway.\n"
                  "[00:04] The token goes to the provider.\n"
                  and "AcmeSync-transcript.txt" in downloaded.headers["content-disposition"]
                  and downloaded.headers["content-type"].startswith("text/plain"),
                  f"{downloaded.status_code} {downloaded.text!r} {dict(downloaded.headers)}")
    harness.check("a run with no transcript has nothing to download", no_download.status_code == 404, no_download.text)


def test_the_phase_list_says_which_phase_is_switched_off_reading_the_files_now():
    root = Path(tempfile.mkdtemp())
    (root / "default.json").write_text('{"review": {"isOn": true}}', encoding="utf-8")
    (root / "local.json").write_text('{"review": {"isOn": false}}', encoding="utf-8")
    previous = config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME = root / "default.json", root / "local.json", ""
    try:
        phases = TestClient(web_api_service.build_app()).get("/api/phases").json()
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME = previous
    switches = {phase["name"]: phase["is_on"] for phase in phases}
    harness.check("phase 5 is off as the local file says, phase 7 off since no clean up is asked, every other phase on",
                  switches == {"transcribe": True, "frames_preparing": True, "frames_processing": True,
                               "finalize": True, "review": False, "critique_collection": True,
                               "document_reshaping": False}, str(switches))


def test_the_run_form_names_what_an_untouched_choice_resolves_to_defect():
    # Defect: the run form's untouched Mode and Reader boxes read "default", which did not say what the run would do
    # Fix: /api/run-defaults gives the values a run started now takes, under the profile the form names
    client = TestClient(web_api_service.build_app())
    for profile in ("", "strict"):
        answered = client.get("/api/run-defaults", params={"profile": profile}).json()
        expected = settings_service.read_next_job_settings(profile or config.PROFILE_NAME)
        harness.check(f"the defaults under {profile or 'the server settings'} are the settings files' values",
                      answered == {"pdf_mode": expected.pdf.default_mode,
                                   "pdf_images_page_limit": expected.pdf.images_page_limit,
                                   "pdf_model_writing_page_limit": expected.pdf.model_writing_page_limit,
                                   "pdf_ocr_engine": expected.pdf.ocr_engine,
                                   "knowledge_filter_model": expected.frames.knowledge_filter_model,
                                   "workflow": expected.video.workflow,
                                   "speed": run_speed_service.describe_speed_options(expected),
                                   "document_wishes": {"cleanup": expected.pdf.cleanup_wishes,
                                                       "rewrite": expected.pdf.rewrite_wishes,
                                                       "knowledge": expected.pdf.knowledge_wishes}}, str(answered))
    refused = client.get("/api/run-defaults", params={"profile": "nope"})
    harness.check("a profile with no file is refused by name", refused.status_code == 404 and "nope" in refused.text,
                  refused.text)


def test_a_job_runs_on_its_own_profile_and_an_unknown_one_is_refused_by_name():
    own = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"),
                                                            profile="strict"))
    harness.check("the job's profile is on its run line", own[-2:] == ["--profile", "strict"], str(own))
    previous = config.PROFILE_NAME
    config.PROFILE_NAME = "server-wide"
    try:
        chosen = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"),
                                                                   profile="strict"))
    finally:
        config.PROFILE_NAME = previous
    harness.check("and wins over the server's", chosen.count("--profile") == 1 and chosen[-1] == "strict", str(chosen))
    harness.check("an old queue entry without a profile still loads",
                  RunJobRequest.from_dict({"video": "/in/a.mp4", "output_root": "/out/a"}).profile == "")
    client = TestClient(web_api_service.build_app())
    listed = client.get("/api/profiles").json()
    harness.check("the profiles listed are the profile files, not the shared or spec files",
                  "strict" in listed["profiles"] and not {"default", "local", "spec"} & set(listed["profiles"]), str(listed))
    refused = client.post("/api/jobs", json={"items": [{"video": "/in/a.mp4", "output": "/out/a"}], "profile": "nope"})
    harness.check("a job naming a profile with no file is refused by name",
                  refused.status_code == 400 and "playbook.nope.json" in refused.text, refused.text)


def test_a_name_with_one_backslash_is_refused_as_holding_a_folder():
    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    inbox_root.mkdir()
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    try:
        inbox_rename_service.rename_inbox_video(inbox_root, inbox_root / "AcmeSync.mp4", "talk\\part")
        is_refused = False
    except ValueError:
        is_refused = True
    harness.check("talk\\part is refused, as talk/part is", is_refused)


def test_an_unreadable_origins_record_is_never_overwritten_with_an_empty_one():
    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    (inbox_root / ".trash").mkdir(parents=True)
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    (inbox_root / ".trash" / ".origins.json").write_text('{"old.mp4": {"video": "x"', encoding="utf-8")
    try:
        inbox_move_service.move_out_of_inbox(inbox_root, inbox_root / "AcmeSync.mp4", "trash")
        message = "moved"
    except UnknownInboxItemError as exc:
        message = str(exc)
    harness.check("the move stops naming the record, and the record keeps what it held",
                  ".origins.json" in message
                  and (inbox_root / ".trash" / ".origins.json").read_text(encoding="utf-8") == '{"old.mp4": {"video": "x"',
                  message)


def test_a_video_renamed_to_its_own_name_in_other_letters_is_renamed():
    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    inbox_root.mkdir()
    (inbox_root / "talk.mp4").write_bytes(b"video")
    renamed = inbox_rename_service.rename_inbox_video(inbox_root, inbox_root / "talk.mp4", "Talk")
    harness.check("talk.mp4 becomes Talk.mp4", [path.name for path in inbox_root.iterdir()] == ["Talk.mp4"], str(renamed))


def test_an_origins_record_that_is_no_object_stops_the_move_and_a_listing_still_shows_the_place():
    inbox_root = Path(tempfile.mkdtemp()) / "inbox"
    (inbox_root / ".trash").mkdir(parents=True)
    (inbox_root / ".trash" / "old.mp4").write_bytes(b"video")
    (inbox_root / "AcmeSync.mp4").write_bytes(b"video")
    (inbox_root / ".trash" / ".origins.json").write_text("[]", encoding="utf-8")
    try:
        inbox_move_service.move_out_of_inbox(inbox_root, inbox_root / "AcmeSync.mp4", "trash")
        moved = "moved"
    except UnknownInboxItemError as exc:
        moved = str(exc)
    listed = inbox_move_service.list_moved(inbox_root, "trash")
    harness.check("the move is refused the way the page answers a move it cannot make, and nothing moved",
                  ".origins.json" in moved and (inbox_root / "AcmeSync.mp4").is_file(), moved)
    harness.check("the listing shows the place without touching its record",
                  [entry["name"] for entry in listed] == ["old.mp4"]
                  and (inbox_root / ".trash" / ".origins.json").read_text(encoding="utf-8") == "[]", str(listed))


def test_a_job_whose_process_cannot_start_ends_crashed_and_the_queue_goes_on():
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix(["/no/such/program"])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a")),
                                                 RunJobRequest(video=Path("/in/b.mp4"), output_root=Path("/out/b"))])
        try:
            first = job_queue_service.run_next_job(queue_dir)
            outcome = first.status if first else "none"
        except OSError as exc:
            outcome = f"raised {type(exc).__name__}"
        statuses = [job.status for job in job_queue_service.list_jobs(queue_dir)]
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
    harness.check("the job that cannot start is crashed and the next one waits its turn",
                  outcome == "crashed" and statuses == ["crashed", "queued"], f"{outcome} {statuses}")


def test_a_job_whose_console_cannot_be_made_ends_crashed_and_the_queue_goes_on():
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"))])
        # The consoles' folder is a file, so no console can be opened in it
        (queue_dir / job_queue_service.CONSOLES_DIR).write_text("not a folder", encoding="utf-8")
        try:
            job = job_queue_service.run_next_job(queue_dir)
            outcome = job.status if job else "none"
        except OSError as exc:
            outcome = f"raised {type(exc).__name__}"
        statuses = [job.status for job in job_queue_service.list_jobs(queue_dir)]
    finally:
        job_queue_service.set_run_lister(previous_lister)
    harness.check("a job with no console is crashed, never left queued", outcome == "crashed" and statuses == ["crashed"],
                  f"{outcome} {statuses}")


def test_a_job_whose_start_cannot_be_saved_still_runs_and_its_end_is_recorded():
    from unittest import mock

    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix([sys.executable, "-c", "pass"])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    real_save = job_queue_service._save_jobs
    saves = []

    def failing_once(folder, jobs):
        saves.append(len(saves))
        if len(saves) == 1:
            raise OSError("no space left on device")
        real_save(folder, jobs)

    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"))])
        with mock.patch.object(job_queue_service, "_save_jobs", failing_once):
            try:
                ended = job_queue_service.run_next_job(queue_dir)
                outcome = ended.status if ended else "none"
            except OSError as exc:
                outcome = f"raised {exc}"
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
    harness.check("the worker goes on and records how the job ended", not outcome.startswith("raised") and outcome != "none",
                  outcome)


def test_a_job_whose_end_cannot_be_saved_leaves_the_worker_free_for_the_next():
    from unittest import mock

    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix([sys.executable, "-c", "pass"])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    real_save = job_queue_service._save_jobs
    saves = []

    def failing_end(folder, jobs):
        saves.append(len(saves))
        if len(saves) == 2:
            raise OSError("no space left on device")
        real_save(folder, jobs)

    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"))])
        with mock.patch.object(job_queue_service, "_save_jobs", failing_end):
            try:
                job_queue_service.run_next_job(queue_dir)
                outcome = "returned"
            except OSError as exc:
                outcome = f"raised {exc}"
        waited = job_queue_service._waited_job_ids or None
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
    harness.check("the worker goes on, and waits on no job", outcome == "returned" and waited is None, f"{outcome} {waited}")


def test_a_document_zip_keeps_a_figure_linked_through_a_parent_folder_at_the_path_the_link_names():
    import io
    import zipfile

    root = Path(tempfile.mkdtemp())
    (root / "04_writing").mkdir()
    (root / "images").mkdir()
    document = root / "04_writing" / "draft.md"
    document.write_text("![a](../images/cut-001.png)\n", encoding="utf-8")
    (root / "images" / "cut-001.png").write_bytes(b"png")
    names = zipfile.ZipFile(io.BytesIO(run_artifact_service.build_document_zip(document, [root / "images" / "cut-001.png"]))).namelist()
    harness.check("the figure sits where ../images/cut-001.png from the document points",
                  sorted(names) == ["04_writing/draft.md", "images/cut-001.png"], str(names))


def test_a_job_s_update_is_one_short_answer_and_an_unknown_job_is_a_404():
    """A page held one open event stream per detail view; with a few tabs they took every connection Firefox allows one
    server (6), and a recording's video waited for good. An update is one answer, the connection freed at once."""

    root = Path(tempfile.mkdtemp())
    previous_queue = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        job = job_queue_service.queue_jobs(job_queue_service.QUEUE_DIR, [RunJobRequest(video=Path("/in/Gateway.mp4"),
                                                                                       output_root=root / "Gateway")])[0]
        console = job_queue_service.build_console_path(job_queue_service.QUEUE_DIR, job.job_id)
        console.parent.mkdir(parents=True, exist_ok=True)
        console.write_text("phase 1: transcribing\n", encoding="utf-8")
        client = TestClient(web_api_service.build_app())
        first = client.get(f"/api/jobs/{job.job_id}/update?offset=0")
        later = client.get(f"/api/jobs/{job.job_id}/update?offset={first.json()['offset']}").json()
        unknown = client.get("/api/jobs/nosuchjob/update")
    finally:
        job_queue_service.QUEUE_DIR = previous_queue
    harness.check("one JSON answer with the job, the new console text and the offset to ask from next",
                  first.headers["content-type"].startswith("application/json") and first.json()["job"]["job_id"] == job.job_id
                  and first.json()["text"] == "phase 1: transcribing\n" and first.json()["offset"] > 0, first.text)
    harness.check("asked from that offset, no text again", later["text"] == "", str(later))
    harness.check("an unknown job is a 404", unknown.status_code == 404, unknown.text)


def test_a_trash_entry_or_an_upload_that_fails_on_disk_is_a_row_and_the_others_go_on():
    from services import inbox_move_service, upload_service

    root = Path(tempfile.mkdtemp())
    previous_inbox = config.INBOX_DIR
    config.INBOX_DIR = root / "inbox"
    config.INBOX_DIR.mkdir()
    original_delete, original_save = inbox_move_service.delete_from_trash, upload_service.save_upload
    deleted = []

    def delete(inbox_root, name):
        if name == "busy.mp4":
            raise PermissionError("busy.mp4 is in use")
        deleted.append(name)

    def save(stream, inbox_root, filename):
        if filename == "first.mp4":
            raise OSError("no space left on device")
        return inbox_root / filename

    inbox_move_service.delete_from_trash, upload_service.save_upload = delete, save
    try:
        client = TestClient(web_api_service.build_app(), raise_server_exceptions=False)
        rows = client.post("/api/inbox/trash/delete", json={"names": ["busy.mp4", "old.mp4"]})
        uploads = client.post("/api/inbox/upload", files=[("files", ("first.mp4", b"a")), ("files", ("second.mp4", b"b"))])
    finally:
        inbox_move_service.delete_from_trash, upload_service.save_upload = original_delete, original_save
        config.INBOX_DIR = previous_inbox
    harness.check("the busy entry is a row with its reason, and the next is deleted",
                  rows.status_code == 200 and rows.json()[0]["error"] and deleted == ["old.mp4"], rows.text)
    harness.check("the failed upload is a refused row, and the next is saved",
                  uploads.status_code == 200 and uploads.json()[0]["kind"] == "refused"
                  and uploads.json()[1]["saved_as"] == "second.mp4", uploads.text)


def test_a_zip_label_of_only_symbols_names_the_zip_files():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-zip")
    (folder / "frames").mkdir(exist_ok=True)
    (folder / "frames" / "frame-000.jpg").write_bytes(b"jpg")
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        zipped = client.post("/api/runs/knowledge/AcmeSync-zip/zip", json={"paths": ["frames/frame-000.jpg"], "label": "!!"})
    finally:
        _swap_run_roots(previous_roots)
    disposition = zipped.headers.get("content-disposition", "")
    harness.check("the zip is named <folder>-files.zip", disposition.endswith("AcmeSync-zip-files.zip"), disposition)


def test_the_page_drops_frames_through_the_server_and_a_read_frame_is_refused():
    root = Path(tempfile.mkdtemp())
    folder = root / "knowledge" / "AcmeSync"
    (folder / "frames").mkdir(parents=True)
    paths = RunPaths(repository=folder, module="AcmeSync", output_root_override=folder)
    items = [{"slug": f"frame-00{index}", "index": index, "frameNumber": index, "timestamp": index * 10.0,
              "path": f"frames/frame-00{index}.jpg", "transcriptBefore": "", "transcriptCurrent": ""} for index in range(3)]
    items[0]["record"] = {"facts": ["Jonas opens the gateway"]}
    for item in items:
        (folder / item["path"]).write_bytes(b"jpg")
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "segments": []})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "items": items})
    previous_roots = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        client = TestClient(web_api_service.build_app())
        refused = client.post("/api/runs/knowledge/AcmeSync/frames/drop", json={"frames": ["frame-000"]}).json()
        dropped = client.post("/api/runs/knowledge/AcmeSync/frames/drop",
                              json={"frames": ["frame-002"], "proposed": ["frame-002"], "drop_scores": {"frame-002": 0.9}}).json()
        groups = {group["key"]: group for group in client.get("/api/runs/knowledge/AcmeSync/artifacts").json()}
    finally:
        _swap_run_roots(previous_roots)
    harness.check("a frame phase 3 read is refused by name", refused["exit_code"] == 2 and "frame-000" in refused["output"],
                  str(refused))
    harness.check("a proposed frame is dropped and the page then lists it under Dropped frames with its score",
                  dropped["exit_code"] == 0 and [(item["name"], item["drop_score"]) for item in groups["dropped"]["items"]]
                  == [("frame-002.jpg", 0.9)] and [item["name"] for item in groups["frames"]["items"]]
                  == ["frame-000.jpg", "frame-001.jpg"], str(dropped) + str(groups.get("dropped")))


def test_the_document_writing_card_lists_every_phase_4_step_with_its_calls_and_rejections():
    """The owner: the document writing card opens like the frames card, on every stage's steps and their details."""

    import json as json_module
    from services import call_log_service
    from services import document_file_service
    from services import document_step_history_service
    run = Path(tempfile.mkdtemp()) / "AcmeSync-weekly"
    writing = run / "04_writing"
    (writing / "logs").mkdir(parents=True)
    (writing / "follow-ups.json").write_text(json_module.dumps({"executive-summary-draft": {"status": "accepted"},
                                                                "parts": {"status": "blocked"}}), encoding="utf-8")
    call_log_service.append_call_metrics(run / "logs" / "a", {"phase": "finalize", "slug": "parts", "durationS": 20.0,
                                                              "promptTokens": 100, "outputTokens": 10})
    call_log_service.append_call_metrics(run / "logs" / "b", {"phase": "finalize", "slug": "parts", "durationS": 30.0,
                                                              "promptTokens": 200, "outputTokens": 20})
    call_log_service.append_call_metrics(run / "logs" / "b", {"phase": "frames_preparing", "slug": "parts", "durationS": 99.0})
    (writing / "logs" / "conversation.jsonl").write_text("\n".join(json_module.dumps(row) for row in [
        {"followUp": "parts", "tool": "submit_agenda", "result": "Rejected.\n- AGENDA_POINT_EMPTY: point 2 holds nothing"},
        {"followUp": "parts", "tool": "read_notes", "result": "Rejected. is a word this read found"}]), encoding="utf-8")
    steps = {step["slug"]: step for step in document_step_history_service.list_document_steps(run)}
    harness.check("the steps in the order they are asked, each with its stage and a name a person reads",
                  [(step["stage"], step["step"], step["status"]) for step in steps.values()][:2]
                  == [("table of contents", "executive summary draft", "accepted"), ("table of contents", "agenda", "blocked")]
                  and steps[document_file_service.SUMMARY_SLUG]["step"] == "final summary and title", str(steps))
    harness.check("a step's calls over every run and resume, phase 4's only", (steps["parts"]["calls"], steps["parts"]["seconds"],
                  steps["parts"]["prompt_tokens"], steps["parts"]["output_tokens"]) == (2, 50.0, 300, 30), str(steps["parts"]))
    harness.check("each refused answer with its findings; a read is never one",
                  steps["parts"]["rejections"] == [{"findings": [{"code": "AGENDA_POINT_EMPTY", "text": "point 2 holds nothing"}]}],
                  str(steps["parts"]["rejections"]))


def test_another_projects_main_py_run_is_no_run_of_this_playbook_defect():
    # The queue held a retry while another project's `main.py run` went on, counted as this playbook's run
    # (DEFECTS 703); now only this playbook's own main.py counts
    folder = Path(tempfile.mkdtemp())
    (folder / "main.py").write_text("import sys\nprint('ready', flush=True)\nsys.stdin.read()\n", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(folder / "main.py"), "run"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    process.stdout.readline()
    try:
        listed = [line for line in run_reset_service.list_run_processes() if line.split()[0] == str(process.pid)]
    finally:
        process.kill()
    harness.check("another project's main.py run is not listed", not listed, str(listed))
    harness.check("this playbook's own main.py counts",
                  run_reset_service._is_own_main(str(os.getpid()), str(run_reset_service._OWN_MAIN)), "")


def test_the_run_list_reads_the_machines_processes_once_for_every_folder():
    root = Path(tempfile.mkdtemp())
    for name in ("AcmeSync-one", "AcmeSync-two", "AcmeSync-three"):
        _make_run_folder(root / "knowledge", name)
    calls: list[int] = []
    original = run_reset_service.list_run_processes
    run_reset_service.list_run_processes = lambda: calls.append(1) or []
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        listed = run_status_service.list_run_folders()
    finally:
        run_reset_service.list_run_processes = original
        _swap_run_roots(previous)
    harness.check("three folders listed with one read of the process table, never one per folder",
                  len(listed) == 3 and len(calls) == 1, f"{len(listed)} folders, {len(calls)} reads")


def test_a_run_stopped_after_phase_2_is_incomplete_though_its_record_says_complete():
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-to-2")
    record_path = folder / "logs" / "2026-10-01_09-00" / output_contract.RUN_STATE_FILE
    record = json_store_utils.read_json(record_path)
    record["status"], record["phases"] = "complete", {"transcribe": "complete", "frames_preparing": "complete"}
    json_store_utils.write_json(record_path, record)
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        status = run_status_service.list_run_folders()[0]["status"]
    finally:
        _swap_run_roots(previous)
    harness.check("phases 1 and 2 done of a run asked for --to 2: incomplete, never complete", status == "incomplete",
                  status)



def test_a_new_video_s_tags_store_that_does_not_read_is_not_written_over():
    from models.ui_errors import UnknownInboxItemError
    from services import inbox_tags_service
    root = Path(tempfile.mkdtemp())
    previous_path = inbox_tags_service.STORE_PATH
    inbox_tags_service.set_store_path(root / "inbox-tags.json")
    try:
        inbox_tags_service.STORE_PATH.write_text('{"a.mp4": ["lecture"], "b.mp4": ["wee', encoding="utf-8")
        shown = inbox_tags_service.read_video_tags(root / "a.mp4")
        try:
            inbox_tags_service.write_video_tags(root / "c.mp4", ["x"])
            outcome = "written"
        except UnknownInboxItemError as refused:
            outcome = f"refused: {refused}"
        kept = inbox_tags_service.STORE_PATH.read_text(encoding="utf-8")
    finally:
        inbox_tags_service.set_store_path(previous_path)
    harness.check("an unreadable store shows no tags", shown == [], str(shown))
    harness.check("a write is refused by name and the store is left as it was",
                  outcome.startswith("refused:") and "inbox-tags.json" in outcome and kept.endswith('"wee'), f"{outcome} / {kept}")


def test_a_rename_with_a_tags_store_that_does_not_read_refuses_instead_of_dropping_the_tags():
    from models.ui_errors import UnreadableTagsError
    from services import inbox_tags_service
    root = Path(tempfile.mkdtemp())
    previous_path = inbox_tags_service.STORE_PATH
    inbox_tags_service.set_store_path(root / "inbox-tags.json")
    try:
        inbox_tags_service.STORE_PATH.write_text('{"a.mp4": ["lecture"], "b.mp4": ["wee', encoding="utf-8")
        try:
            inbox_tags_service.move_video_tags(root / "a.mp4", root / "renamed.mp4")
            outcome = "moved silently"
        except UnreadableTagsError as refused:
            outcome = f"refused: {refused}"
        kept = inbox_tags_service.STORE_PATH.read_text(encoding="utf-8")
    finally:
        inbox_tags_service.set_store_path(previous_path)
    harness.check("the move of a video's tags in a store that does not read is refused by name",
                  outcome.startswith("refused:") and "inbox-tags.json" in outcome, outcome)
    harness.check("the store is left as it was", kept.endswith('"wee'), kept)


def test_a_rename_moves_a_video_s_tags_to_its_new_path_in_one_write():
    from services import inbox_tags_service
    root = Path(tempfile.mkdtemp())
    previous_path = inbox_tags_service.STORE_PATH
    inbox_tags_service.set_store_path(root / "inbox-tags.json")
    try:
        inbox_tags_service.write_video_tags(root / "a.mp4", ["lecture"])
        inbox_tags_service.write_video_tags(root / "b.mp4", ["week"])
        inbox_tags_service.move_video_tags(root / "a.mp4", root / "renamed.mp4")
        stored = json.loads(inbox_tags_service.STORE_PATH.read_text(encoding="utf-8"))
    finally:
        inbox_tags_service.set_store_path(previous_path)
    harness.check("the tags stand under the new path only, the other video's tags kept",
                  stored == {str(root / "renamed.mp4"): ["lecture"], str(root / "b.mp4"): ["week"]}, str(stored))


def test_the_time_a_video_was_moved_is_kept_when_the_entry_is_touched_afterwards():
    from services import inbox_move_service
    inbox_root = Path(tempfile.mkdtemp())
    video = inbox_root / "AcmeSync.mp4"
    video.write_bytes(b"x")
    inbox_move_service.move_out_of_inbox(inbox_root, video, "trash")
    first = inbox_move_service.list_moved(inbox_root, "trash")[0]["moved_at"]
    (inbox_root / ".trash" / "AcmeSync.mp4").chmod(0o600)
    second = inbox_move_service.list_moved(inbox_root, "trash")[0]["moved_at"]
    harness.check("a change to the entry after the move does not change when it was moved", first == second,
                  f"{first} then {second}")


def test_a_job_whose_end_cannot_be_saved_is_returned_with_the_exit_code_it_ended_with():
    from unittest import mock

    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix([sys.executable, "-c", "pass"])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    real_save = job_queue_service._save_jobs
    saves = []

    def failing_end(folder, jobs):
        saves.append(len(saves))
        if len(saves) == 2:
            raise OSError("no space left on device")
        real_save(folder, jobs)

    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"))])
        with mock.patch.object(job_queue_service, "_save_jobs", failing_end):
            ended = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
    harness.check("the job returned says how it ended, not that it still runs",
                  ended is not None and ended.exit_code == 0 and ended.status != "running",
                  f"{ended.status} {ended.exit_code}" if ended else "none")


def test_a_queue_file_that_does_not_read_does_not_end_the_worker():
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    queue_dir.mkdir(parents=True)
    (queue_dir / job_queue_service.JOBS_FILE).write_text("{", encoding="utf-8")
    try:
        ran = job_queue_service.run_next_job_or_report(queue_dir)
        outcome = f"returned {ran}"
    except (OSError, ValueError) as exc:
        outcome = f"raised {type(exc).__name__}"
    harness.check("the worker's step reports the unreadable queue and carries on", outcome == "returned None", outcome)


def test_a_hidden_artifacts_record_that_is_not_an_object_is_refused_and_not_written_over():
    root = Path(tempfile.mkdtemp())
    previous_queue = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        (root / "ui").mkdir()
        record_path = root / "ui" / hidden_artifact_service.HIDDEN_FILE
        record_path.write_text('["hand edited"]', encoding="utf-8")
        try:
            hidden_artifact_service.set_hidden("output", "AcmeSync", ["frames/frame-000.jpg"], True)
            outcome = "written"
        except ValueError as refused:
            outcome = f"refused: {refused}"
        kept = record_path.read_text(encoding="utf-8")
    finally:
        job_queue_service.QUEUE_DIR = previous_queue
    harness.check("the record is refused by name", outcome.startswith("refused:") and hidden_artifact_service.HIDDEN_FILE in outcome, outcome)
    harness.check("and left as it was", kept == '["hand edited"]', kept)


def test_a_rejection_the_conversation_writes_is_read_back_by_the_page_s_table():
    """The table of rejected attempts reads the text the conversation writes; both go through one module."""

    import json as json_module
    from services import conversation_log_service
    from services import document_step_history_service
    run = Path(tempfile.mkdtemp()) / "AcmeSync-weekly"
    (run / "04_writing" / "logs").mkdir(parents=True)
    (run / "04_writing" / "follow-ups.json").write_text(json_module.dumps({"parts": {"status": "blocked"}}), encoding="utf-8")
    written = conversation_log_service.render_rejection([{"code": "AGENDA_POINT_EMPTY", "detail": "point 2 holds nothing",
                                                          "fix": "write it"}])
    (run / "04_writing" / "logs" / "conversation.jsonl").write_text(
        json_module.dumps({"followUp": "parts", "tool": "submit_agenda", "result": written}), encoding="utf-8")
    steps = {step["slug"]: step for step in document_step_history_service.list_document_steps(run)}
    harness.check("the page counts the rejection and names its code",
                  [finding["code"] for rejection in steps["parts"]["rejections"] for finding in rejection["findings"]]
                  == ["AGENDA_POINT_EMPTY"], str(steps["parts"]["rejections"]))


def test_a_pdf_run_s_artifacts_and_phases_are_named_by_what_a_document_holds():
    """The owner, seeing "Speech paragraphs", "Frames" and "notes preparing" on a PDF read as text: "what is frames in
    pdf?"."""

    import json

    from models import phase_guide
    from services import run_status_service

    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "output", "AcmeSync-guide")
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    (folder / "frames" / "frame-000.jpg").write_bytes(b"jpg")
    (folder / "02_speech.md").write_text("# Speech", encoding="utf-8")
    (folder / "02_frames.json").write_text(json.dumps({"medium": "pdf-text-only", "items": [
        {"slug": "frame-000", "path": "frames/frame-000.jpg", "timestamp": 59.99, "page": 3}]}), encoding="utf-8")
    groups = {group["key"]: group for group in run_artifact_service.list_artifacts(folder)}
    harness.check("its paragraphs are Paragraphs and its frames Figures, each with its page",
                  groups["speech"]["title"] == "Paragraphs" and groups["frames"]["title"] == "Figures"
                  and groups["frames"]["items"][0]["page"] == 3, str(groups["frames"]))
    harness.check("its phases 2 and 3 are named by the paragraphs and figures, read as pages by the pages",
                  run_status_service.describe_phase(3, True, "text-only")["title"] == "figures reading"
                  and run_status_service.describe_phase(3, False, "pages-as-images")["title"] == "pages reading"
                  and run_status_service.describe_phase(3, True)["title"] == phase_guide.AUDIO_PHASE_TITLES[3]
                  and run_status_service.describe_phase(1, True, "text-only")["title"] == "text reading")
    # Each card's help is a PDF's too: the cards said "Turns the speech of the recording into a timed transcript, and
    # tells the speakers apart" and "each one speaker's turn" on a PDF's detail view
    for mode, is_audio in (("text-only", True), ("pages-as-images", False)):
        helps = " ".join(f"{card.get('summary', '')} {card.get('reads', '')} {card.get('writes', '')}"
                         for card in (run_status_service.describe_phase(number, is_audio, mode) for number in range(1, 7)))
        harness.check(f"a PDF's phase help ({mode}) names no speech, speaker, recording, meeting or video",
                      not re.search(r"(?<!\w)(?:speech|speakers?|recording|meeting|video)(?!\w)", helps, re.IGNORECASE), helps)


def test_a_run_file_changed_on_disk_is_read_again_by_the_page_defect():
    # A document whose tags were written into it showed without them: the file was sent with no Cache-Control, and the
    # browser kept its own copy. Every run file is now sent "no-cache", so the page asks again on each read
    root = Path(tempfile.mkdtemp())
    folder = _make_run_folder(root / "knowledge", "AcmeSync-tags")
    (folder / "review.md").write_text("---\ntags: [\"gateway\"]\n---\n\n# AcmeSync\n", encoding="utf-8")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    try:
        answer = TestClient(web_api_service.build_app()).get("/api/runs/knowledge/AcmeSync-tags/files/review.md")
    finally:
        _swap_run_roots(previous)
    harness.check("the file is sent, to be asked for again each time",
                  answer.status_code == 200 and answer.headers.get("cache-control") == "no-cache", str(answer.headers))


def test_the_page_itself_is_asked_for_again_after_it_is_built_anew_defect():
    # A phone kept the page's index.html, and with it the stylesheet of the build before: a layout fixed and built
    # showed broken. The page's HTML is now sent "no-cache"; its hashed assets keep their caching
    dist = Path(tempfile.mkdtemp())
    (dist / "assets").mkdir()
    (dist / "index.html").write_text("<html><link href=\"assets/index-1.css\"></html>", encoding="utf-8")
    (dist / "assets" / "index-1.css").write_text("body{}", encoding="utf-8")
    previous = web_api_service.WEB_DIST_DIR
    web_api_service.WEB_DIST_DIR = dist
    try:
        client = TestClient(web_api_service.build_app())
        page, stylesheet = client.get("/"), client.get("/assets/index-1.css")
    finally:
        web_api_service.WEB_DIST_DIR = previous
    harness.check("the page is asked for again each time", page.status_code == 200
                  and page.headers.get("cache-control") == "no-cache", str(page.headers))
    harness.check("its hashed stylesheet is not", stylesheet.status_code == 200
                  and stylesheet.headers.get("cache-control") is None, str(stylesheet.headers))


def test_the_server_s_list_calls_share_one_run_list_and_a_change_clears_it_defect():
    # The runs, inbox and tags routes each built the run list on every 3-second refresh of each open page, and the
    # server spun a core. Now they share one build for 2 seconds, and a request that changes something clears it
    root = Path(tempfile.mkdtemp())
    _make_run_folder(root / "knowledge", "AcmeSync-demo")
    previous = _swap_run_roots({"knowledge": root / "knowledge"})
    built = []
    summarise = run_status_service._summarise_folder
    run_status_service._summarise_folder = lambda *args: built.append(args[1].name) or summarise(*args)
    run_status_service.clear_shared_run_list()
    try:
        client = TestClient(web_api_service.build_app())
        client.get("/api/runs")
        client.get("/api/tags")
        shared = len(built)
        client.post("/api/runs/display-names", json={"names": []})
        client.get("/api/runs")
        after_change = len(built)
        run_status_service.list_run_folders()
    finally:
        run_status_service._summarise_folder = summarise
        run_status_service.clear_shared_run_list()
        _swap_run_roots(previous)
    harness.check("the runs and the tags share one build", shared == 1, str(built))
    harness.check("a request that changes something has it built again", after_change == 2, str(built))
    harness.check("a caller asking no age gets a fresh build", len(built) == 3, str(built))


def test_a_shared_run_list_is_never_handed_out_for_other_roots_defect():
    # The shared list of DEFECTS 405 outlived a change of run roots: the console's next test got the folders of the
    # test before and could not open its own run. Now a list built over other roots is built afresh
    first, second = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_run_folder(first / "knowledge", "AcmeSync-demo")
    _make_run_folder(second / "knowledge", "Gateway-review")
    previous = _swap_run_roots({"knowledge": first / "knowledge"})
    run_status_service.clear_shared_run_list()
    try:
        run_status_service.list_run_folders(60.0)
        _swap_run_roots({"knowledge": second / "knowledge"})
        names = [row["name"] for row in run_status_service.list_run_folders(60.0)]
    finally:
        run_status_service.clear_shared_run_list()
        _swap_run_roots(previous)
    harness.check("the list holds the new roots' folder", names == ["Gateway-review"], str(names))
