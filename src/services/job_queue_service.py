"""The UI's job queue: one `main.py run` child process at a time, in the order the jobs were queued.

ONE QUEUE FOR BOTH FRONT ENDS. The web page and the terminal UI reach it through the one server, so
two runs never start at once whichever window asked for them. The queue is `ui/jobs.json` beside the
playbook, rewritten whole on every change; the console of each job is `ui/jobs/<job id>.log`.

ONE LANE PER MACHINE. A job runs on a provider set (its request's, else the one its run folder kept, else the settings'
`defaultProviderSet`), and its lane is the machine that set names (`machine`; the set's own name when it names none). Jobs
of one machine run one after another, whichever of its sets they use, so two sets on one machine never load two models
on its card at once; jobs of different machines run at the same time. A job that holds the queue after its last failed
try holds its own lane only; the person's pause stops every lane.

A JOB IS A CHILD PROCESS, NEVER AN IN-PROCESS CALL. `run` keeps every refusal and every exit code in
one place, and a child in its own session outlives the server: a server started again adopts a job
whose process is still alive, and marks one whose process is gone `lost`, since its exit code went
with the server that waited on it.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from functools import lru_cache
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import config
from models import output_contract
from models import run_job
from models.run_job import RunJob, RunJobRequest
from models.ui_errors import UnknownJobError
from services import control_signal_service
from services import run_name_service
from services import run_reset_service
from services import run_status_service
from services import run_provider_set_service
from services import settings_service
from utils import process_utils
from utils import json_store_utils

__all__ = ["QUEUE_DIR", "build_run_command", "build_tool_command","set_command_prefix", "set_run_lister",
           "set_queue_settings_reader", "list_jobs", "find_job", "queue_jobs", "cancel_job", "send_interrupt",
           "build_console_path", "is_run_folder_missing", "run_next_job", "run_next_job_or_report", "start_worker",
           "move_job", "clear_ended_jobs", "count_tries", "set_on_failure", "retry_blocked_job", "skip_blocked_job",
           "pause_queue", "resume_queue", "read_pause", "is_queue_paused", "set_rename_after_run", "move_jobs_to_folder"]

_LOG = logging.getLogger(__name__)

QUEUE_DIR = config.UI_DIR
JOBS_FILE = "jobs.json"
# The person's pause of the queue: `{"until": "<time>"}`, or `{"isUntilResumed": true}`. No file, no pause
PAUSE_FILE = "queue-pause.json"
# The console line of a job interrupted with "Interrupt and pause"
_HELD_LINE = ("interrupted and paused: no other job starts on this machine until you resume it (Retry runs it again "
              "first, with --resume) or let it go (Skip)")
CONSOLES_DIR = "jobs"
# How often the worker looks again while a run it did not start (an adopted job, or a run typed on the command line)
# is still alive.
_ADOPTED_POLL_S = 2.0
# How a job's times are written in `jobs.json`
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# The program a job runs, before `run` and its flags. A test sets a stand-in with `set_command_prefix`.
_command_prefix = [sys.executable, str(config.PLAYBOOK_ROOT / "main.py")]
# What lists the runs going on this machine, the queue's own children included. A test sets a fixed answer with
# `set_run_lister`, so a real run elsewhere on the machine never decides a test.
_list_runs = run_reset_service.list_run_processes
# What reads the queue's settings for a job's profile when it fails: the files as they are now, so a value saved on the
# Settings page reaches the next failure with no restart. A test sets fixed settings with `set_queue_settings_reader`.
_read_queue_settings: Callable[[str], config.QueueSettings] = (
    lambda profile: settings_service.read_next_job_settings(profile).queue)
# What reads the status a run's own newest record holds (`complete`, `incomplete`, ...): the one witness of how a job
# ended that no server saw
_read_run_status: Callable[[Path], str] = (
    lambda output_root: str(run_status_service.load_newest_record(output_root).get("status", "")))
# What makes the Manager's rename asked while a run wrote into a folder, once no job will write into it again; returns
# where the folder is then. `i2k serve` sets the real one (`set_rename_after_run`); by default nothing is renamed
_rename_after_run: Callable[[Path], Path] = lambda output_root: output_root
# The states of a job that will still write into its run folder: its retry and a resume run at the folder's path
_WRITING_STATES = (run_job.JOB_QUEUED, run_job.JOB_RUNNING, run_job.JOB_RETRYING, run_job.JOB_BLOCKED)
# Re-entrant: a job's end makes the rename that waited for it under the lock, and the rename moves the queued jobs
# under it too
_lock = threading.RLock()
_wake = threading.Event()
# The jobs this process is waiting on right now (one per running lane), so `list_jobs` never marks one lost while its
# child starts.
_waited_job_ids: set[str] = set()
# The lanes whose waiter thread runs now, so a lane never has two
_running_lanes: set[str] = set()


def _read_now() -> str:
    return time.strftime(_TIME_FORMAT)


def set_rename_after_run(rename: Callable[[Path], Path]) -> Callable[[Path], Path]:
    """Swap what makes a rename that waited for its run (the server's `rename_after_run`, or a test's); returns the one
    it replaced."""

    global _rename_after_run
    previous, _rename_after_run = _rename_after_run, rename

    return previous


def move_jobs_to_folder(queue_dir: Path,
                        old_root: Path,
                        new_root: Path) -> int:
    """Every job that will still run (queued, waiting for its retry, holding the queue) pointed from the run folder
    `old_root` to `new_root`, where a rename moved it; how many were moved. Left on the old path, a queued resume
    started the run again from phase 1 in a new folder of the old name."""

    # compared resolved: the page resolves a folder (`/private/var/...`) a job may name through a link (`/var/...`)
    old = Path(old_root).resolve()
    with _lock:
        jobs = _load_jobs(queue_dir)
        moved = [job for job in jobs if job.status in (run_job.JOB_QUEUED, run_job.JOB_RETRYING, run_job.JOB_BLOCKED)
                 and Path(job.request.output_root).resolve() == old]
        for job in moved:
            job.request = replace(job.request, output_root=Path(new_root))
            # The command the queue starts is kept beside the request: left on the old folder, a resume was refused
            # "no run to resume" and left an empty folder of the old name
            job.command = _move_output_argument(job.command, Path(new_root))
        if moved:
            _save_jobs(queue_dir, jobs)

    return len(moved)


def _move_output_argument(command: list[str],
                          new_root: Path) -> list[str]:
    """`command` with the folder after its `--output` replaced by `new_root`."""

    if "--output" not in command[:-1]:
        return command
    at = command.index("--output") + 1

    return [*command[:at], str(new_root), *command[at + 1:]]


def _rename_ended_run_folders(jobs: list[RunJob],
                              output_roots: list[Path]) -> None:
    """Each of `output_roots` given the rename that waited for its run, unless a job of the queue will still write into
    it: a retry or a queued resume names the folder by its path. A folder that cannot be renamed keeps its rename for
    the next end."""

    writing = {Path(job.request.output_root) for job in jobs if job.status in _WRITING_STATES}
    for output_root in output_roots:
        if Path(output_root) in writing:
            continue
        try:
            _rename_after_run(Path(output_root))
        except OSError as error:
            _LOG.warning("%s could not be renamed after its run: %s", output_root, error)


def set_command_prefix(prefix: list[str]) -> list[str]:
    """Swap the program a job runs (a test's stand-in for `main.py`); returns the one it replaced."""

    global _command_prefix
    previous, _command_prefix = _command_prefix, list(prefix)

    return previous


def set_run_lister(lister: Callable[[], list]) -> Callable[[], list]:
    """Swap what lists the live runs on this machine (a test's fixed answer); returns the one it replaced."""

    global _list_runs
    previous, _list_runs = _list_runs, lister

    return previous


def set_queue_settings_reader(reader: Callable[[str], config.QueueSettings]) -> Callable[[str], config.QueueSettings]:
    """Swap what reads the queue's settings for a profile (a test's fixed settings); returns the one it replaced."""

    global _read_queue_settings
    previous, _read_queue_settings = _read_queue_settings, reader

    return previous


def set_run_status_reader(reader: Callable[[Path], str]) -> Callable[[Path], str]:
    """Swap what reads a run's own recorded status (a test's fixed status); returns the one it replaced."""

    global _read_run_status
    previous, _read_run_status = _read_run_status, reader

    return previous


def build_tool_command(arguments: list[str]) -> list[str]:
    """A command of this program that is no queued run, on the same program a job runs."""

    return [*_command_prefix, *arguments]


def build_run_command(request: RunJobRequest) -> list[str]:
    """The `run` line a request stands for, flag for flag as `cli/__init__.py` defines them."""

    command = [*_command_prefix, "run", "--video", str(request.video), "--output", str(request.output_root)]
    # The job's own profile, else the one the server was started with, written into the line rather than left to the
    # environment the child inherits: the queue, the job's console and its record then say which settings it used.
    profile = request.profile or config.PROFILE_NAME
    if profile:
        command += ["--profile", profile]
    if request.only_phase is not None:
        command += ["--phase", str(request.only_phase)]
    if request.from_phase is not None:
        command += ["--from", str(request.from_phase)]
    if request.to_phase is not None:
        command += ["--to", str(request.to_phase)]
    for index in request.worker_indexes:
        command += ["--worker", str(index)]
    for tag in request.tags:
        command += ["--tag", tag]
    if request.ingesting_profile:
        command += ["--ingesting-profile", request.ingesting_profile]
    if request.is_run_as_audio:
        command.append("--as-audio")
    if request.pdf_mode:
        command += ["--pdf-mode", request.pdf_mode]
    if request.pdf_ocr_engine:
        command += ["--pdf-ocr-engine", request.pdf_ocr_engine]
    if request.document_pass:
        command += ["--document-pass", request.document_pass]
        if request.document_wishes:
            command += ["--document-wishes", request.document_wishes]
        if request.document_source:
            command += ["--document-source", request.document_source]
    if request.knowledge_filter_model:
        command += ["--knowledge-filter-model", request.knowledge_filter_model]
    if request.range_start:
        command += ["--start", request.range_start]
    if request.range_end:
        command += ["--end", request.range_end]
    if request.range_pages:
        command += ["--pages", request.range_pages]
    if request.workflow:
        command += ["--workflow", request.workflow]
    if request.speed_preset:
        command += ["--speed-preset", request.speed_preset]
    for pair in request.speed_settings:
        command += ["--speed-setting", pair]
    if request.provider_set:
        command += ["--provider-set", request.provider_set]
    for pair in request.phase_models:
        command += ["--phase-model", pair]
    if request.is_resuming:
        command.append("--resume")
    if request.is_dry_run:
        command.append("--dry-run")
    if request.is_forced:
        command.append("--force")

    return command


def build_console_path(queue_dir: Path,
                       job_id: str) -> Path:
    """Where one job's console lines are written: everything its child printed, refusals included."""

    return queue_dir / CONSOLES_DIR / f"{job_id}.log"


def _load_jobs(queue_dir: Path) -> list[RunJob]:
    path = queue_dir / JOBS_FILE
    rows = json_store_utils.read_json(path) if path.is_file() else []

    return [RunJob.from_dict(row) for row in rows or []]


def _save_jobs(queue_dir: Path,
               jobs: list[RunJob]) -> None:
    json_store_utils.write_json(queue_dir / JOBS_FILE, [job.as_dict() for job in jobs])


def _find_job(jobs: list[RunJob],
              job_id: str) -> RunJob:
    for job in jobs:
        if job.job_id == job_id:
            return job
    raise UnknownJobError(f"no job {job_id} in the queue")


def _mark_lost_jobs(queue_dir: Path,
                    jobs: list[RunJob]) -> list[tuple[RunJob, str]]:
    """Each `running` job whose process is gone, and that the worker does not wait on, marked `lost`; one whose run's
    own record says the run did not complete is then retried or holds the queue like any failed job (a knowledge pass
    ended unseen across a server restart, stopped at its first part, and the queue went on to the next document). Each
    job it changed, with the console line saying what comes next (empty for a run that completed)."""

    changed = []
    for job in jobs:
        if (job.status != run_job.JOB_RUNNING or process_utils.is_process_alive(job.pid)
                or job.job_id in _waited_job_ids):
            continue
        job.ended_at, job.status, line = _read_now(), run_job.JOB_LOST, ""
        # a run the queue's pause stopped, ended while no server waited on it, goes on first and at once once the pause
        # ends, never after a failure's retry pause (it restarted a minute after Resume)
        if _is_stopped_by_pause(queue_dir, job.job_id):
            job.status, job.retry_at = run_job.JOB_RETRYING, job.ended_at
            line = "the queue's pause stopped the run; it runs again first, with --resume, once the pause ends"
        elif job.is_held_after_interrupt:
            job.status, line = run_job.JOB_BLOCKED, _HELD_LINE
        elif _read_run_status(job.request.output_root) != "complete":
            job.status, job.retry_at, line = _decide_after_failure(job, run_job.JOB_LOST)
        changed.append((job, line))

    return changed


def list_jobs(queue_dir: Path) -> list[RunJob]:
    """Every job, oldest first. A `running` job whose process is gone is marked `lost` as it is read, or retried when
    its run did not complete."""

    with _lock:
        jobs = _load_jobs(queue_dir)
        changed = _mark_lost_jobs(queue_dir, jobs)
        if changed:
            _save_jobs(queue_dir, jobs)
    for job, line in changed:
        if line:
            _write_console_line(queue_dir, job.job_id, line)

    return jobs


def find_job(queue_dir: Path,
             job_id: str) -> RunJob:
    """The job of this id, read as `list_jobs` reads it; UnknownJobError when the queue holds none."""

    return _find_job(list_jobs(queue_dir), job_id)


def is_run_folder_missing(queue_dir: Path,
                          job: RunJob) -> bool:
    """Whether a running job's run folder is gone after its run made it: its console holds the run header's log line,
    which the run prints once the folder exists, and the folder is not there. A run folder deleted under a running
    job left its video shown as starting for good."""

    if job.status != run_job.JOB_RUNNING or job.request.output_root.is_dir():
        return False
    try:
        console = build_console_path(queue_dir, job.job_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    return f"\n{output_contract.RUN_LOG_LINE}" in console


def queue_jobs(queue_dir: Path,
               requests: list[RunJobRequest]) -> list[RunJob]:
    """Add one job per request at the end of the queue, and wake the worker."""

    queued_at = _read_now()
    requests = [_move_to_renamed_folder(request) for request in requests]
    added = [RunJob(job_id=uuid.uuid4().hex[:8], request=request, queued_at=queued_at,
                    command=build_run_command(request)) for request in requests]
    with _lock:
        _save_jobs(queue_dir, _load_jobs(queue_dir) + added)
    _wake.set()

    return added


def _move_to_renamed_folder(request: RunJobRequest) -> RunJobRequest:
    """`request` pointed at the run folder its folder was renamed to, when its own folder is gone and a folder beside it
    names it as its former name: a resume queued under a run's old name after the rename was refused "no run to
    resume" (DEFECTS 711)."""

    old = Path(request.output_root)
    if old.exists() or not old.parent.is_dir():
        return request
    renamed = [folder for folder in sorted(old.parent.iterdir())
               if folder.is_dir() and run_name_service.read_former_name(folder) == old.name]

    return replace(request, output_root=renamed[0]) if len(renamed) == 1 else request


def cancel_job(queue_dir: Path,
               job_id: str) -> RunJob:
    """Take a queued job, or one waiting for its retry, off the queue. A job running is left as it is and returned
    unchanged."""

    with _lock:
        jobs = _load_jobs(queue_dir)
        job = _find_job(jobs, job_id)
        if job.status in (run_job.JOB_QUEUED, run_job.JOB_RETRYING):
            job.status, job.ended_at = run_job.JOB_CANCELLED, _read_now()
            _save_jobs(queue_dir, jobs)

    return job


def send_interrupt(queue_dir: Path,
                  job_id: str,
                  is_holding: bool = False) -> RunJob:
    """Send a running job's process the interrupt Ctrl-C sends, which `run` ends with exit code 130. With `is_holding`
    the job then holds its machine's queue, blocked, until it is retried (run again first, with --resume) or skipped;
    without it the next job of that machine starts (the owner: "interrupt and pause")."""

    job = _find_job(list_jobs(queue_dir), job_id)
    if job.status != run_job.JOB_RUNNING or not process_utils.is_process_alive(job.pid):
        return job
    if is_holding:
        # written before the interrupt, so the worker that sees the job end knows to hold
        with _lock:
            jobs = _load_jobs(queue_dir)
            job = _find_job(jobs, job_id)
            job.is_held_after_interrupt = True
            _save_jobs(queue_dir, jobs)
    os.kill(job.pid, signal.SIGINT)

    return job


def _is_held_after_interrupt(queue_dir: Path,
                             job_id: str) -> bool:
    """Whether the person interrupted this job with "Interrupt and pause", as the jobs file records it."""

    try:
        return _find_job(_load_jobs(queue_dir), job_id).is_held_after_interrupt
    except UnknownJobError:
        return False


def move_job(queue_dir: Path,
             job_id: str,
             direction: str) -> RunJob:
    """A waiting job swapped with the waiting job of its own provider set before it (`up`) or after it (`down`), so it
    runs sooner or later on that set; the first or last waiting job of the set stays where it is. A job of another set
    is never passed: sets run side by side, so passing it would change nothing on screen or in what runs next."""

    with _lock:
        jobs = _load_jobs(queue_dir)
        job = _find_job(jobs, job_id)
        default_set = read_default_provider_set()
        lane = read_job_lane(job, default_set)
        waiting = [index for index, candidate in enumerate(jobs)
                   if candidate.status == run_job.JOB_QUEUED and read_job_lane(candidate, default_set) == lane]
        if job.status == run_job.JOB_QUEUED:
            place = waiting.index(jobs.index(job))
            other = place - 1 if direction == "up" else place + 1
            if 0 <= other < len(waiting):
                first, second = waiting[place], waiting[other]
                jobs[first], jobs[second] = jobs[second], jobs[first]
                _save_jobs(queue_dir, jobs)

    return job


def count_tries(job: RunJob) -> int:
    """How many tries this job gets in all, for the page's "retry N of M": the setting's retries and the first try for
    a job that may still run, the tries it had for one that ended."""

    if job.status not in (run_job.JOB_QUEUED, run_job.JOB_RUNNING, run_job.JOB_RETRYING):
        return job.attempt

    return _read_queue_settings(job.request.profile or config.PROFILE_NAME).retries + 1


def set_on_failure(queue_dir: Path,
                   job_id: str,
                   choice: str) -> RunJob:
    """Set what the queue does after this job's last failed try: "continue", "block", or "" for the setting's."""

    if choice and choice not in run_job.ON_FAILURE_CHOICES:
        raise ValueError(f"no on-failure choice {choice!r}: the choices are {', '.join(run_job.ON_FAILURE_CHOICES)}")
    with _lock:
        jobs = _load_jobs(queue_dir)
        job = _find_job(jobs, job_id)
        job.request = replace(job.request, on_failure=choice)
        _save_jobs(queue_dir, jobs)

    return job


def retry_blocked_job(queue_dir: Path,
                      job_id: str) -> RunJob:
    """A job holding the queue run once more, now and before any other job, with `--resume`. Any other job is
    returned unchanged."""

    with _lock:
        jobs = _load_jobs(queue_dir)
        job = _find_job(jobs, job_id)
        if job.status == run_job.JOB_BLOCKED:
            job.status, job.retry_at, job.is_held_after_interrupt = run_job.JOB_RETRYING, _read_now(), False
            _save_jobs(queue_dir, jobs)
    _wake.set()

    return job


def skip_blocked_job(queue_dir: Path,
                     job_id: str) -> RunJob:
    """A job holding the queue let go: it keeps the end its last try had, and the next job starts. Any other job is
    returned unchanged."""

    with _lock:
        jobs = _load_jobs(queue_dir)
        job = _find_job(jobs, job_id)
        if job.status == run_job.JOB_BLOCKED:
            job.status = run_job.EXIT_STATUSES.get(job.exit_code, run_job.JOB_CRASHED)
            job.is_held_after_interrupt = False
            _save_jobs(queue_dir, jobs)
    _wake.set()

    return job


def clear_ended_jobs(queue_dir: Path) -> int:
    """Every job that is neither waiting, running, waiting for its retry nor holding the queue taken off the list, with
    its console file; how many. A run keeps its own logs in its folder."""

    kept = (run_job.JOB_QUEUED, run_job.JOB_RUNNING, run_job.JOB_RETRYING, run_job.JOB_BLOCKED)
    with _lock:
        jobs = _load_jobs(queue_dir)
        ended = [job for job in jobs if job.status not in kept]
        _save_jobs(queue_dir, [job for job in jobs if job not in ended])
    for job in ended:
        build_console_path(queue_dir, job.job_id).unlink(missing_ok=True)

    return len(ended)


def _build_try_command(job: RunJob) -> list[str]:
    """The line a job's next try runs: its own line the first time, with `--resume` after that, so a retry goes on
    where the run stopped and never starts it over. Its folder is the request's: a job queued before a rename's fix
    kept the old folder in its line, and its resume was refused "no run to resume"."""

    # the folder is looked up again as the job starts: one renamed after the job was queued is found by its rename
    command = _move_output_argument(job.command, Path(_move_to_renamed_folder(job.request).output_root))
    if job.attempt == 0 or "--resume" in command:
        return command

    return [*command, "--resume"]


def _start_child(queue_dir: Path,
                 job: RunJob) -> subprocess.Popen:
    console_path = build_console_path(queue_dir, job.job_id)
    console_path.parent.mkdir(parents=True, exist_ok=True)
    with console_path.open("ab") as console:
        # A session of its own: Ctrl-C on the server does not reach the run, and the run outlives the server.
        return subprocess.Popen(_build_try_command(job), cwd=config.PLAYBOOK_ROOT, stdout=console,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})


def read_pause(queue_dir: Path) -> dict:
    """The queue's pause: `until`, when it starts its next job again ("" with no time), and `is_until_resumed`, held
    until the person resumes it. Both empty when it is not paused, or its time has passed."""

    path = queue_dir / PAUSE_FILE
    stored = (json_store_utils.read_json(path) or {}) if path.is_file() else {}
    if stored.get("isUntilResumed"):
        return {"until": "", "is_until_resumed": True}
    until = str(stored.get("until", ""))

    return {"until": until if until > _read_now() else "", "is_until_resumed": False}


def is_queue_paused(queue_dir: Path) -> bool:
    """Whether the person's pause holds the next job back now."""

    pause = read_pause(queue_dir)

    return pause["is_until_resumed"] or bool(pause["until"])


def _pause_running_runs(jobs: list[RunJob]) -> list[str]:
    """Each running job's run told to pause, as the run's own Pause does; gives back the run log folders it paused. A
    run already holding a signal of its own (paused, draining, stopping) is left as it is."""

    paused_run_dirs = []
    for job in jobs:
        if job.status != run_job.JOB_RUNNING or not process_utils.is_process_alive(job.pid):
            continue
        run_dir = run_status_service.find_newest_run_dir(Path(job.request.output_root))
        if run_dir is None or control_signal_service.read_signal(run_dir) is not None:
            continue
        control_signal_service.request(run_dir, control_signal_service.PAUSE, by="queue")
        paused_run_dirs.append(str(run_dir))

    return paused_run_dirs


def _end_pause(queue_dir: Path) -> None:
    """The queue's pause taken away, and every run it paused let go on (a pause the person set on a run since stays)."""

    path = queue_dir / PAUSE_FILE
    stored = (json_store_utils.read_json(path) or {}) if path.is_file() else {}
    for run_dir in stored.get("pausedRuns", []):
        if control_signal_service.read_signal(Path(run_dir)) == control_signal_service.PAUSE:
            control_signal_service.clear(Path(run_dir))
    path.unlink(missing_ok=True)


def _stop_running_jobs(jobs: list[RunJob]) -> list[str]:
    """Each running job's process sent the interrupt Ctrl-C sends; gives back their ids."""

    stopped_ids = []
    for job in jobs:
        if job.status == run_job.JOB_RUNNING and process_utils.is_process_alive(job.pid):
            os.kill(job.pid, signal.SIGINT)
            stopped_ids.append(job.job_id)

    return stopped_ids


def pause_queue(queue_dir: Path,
                minutes: int | None,
                is_stopping_now: bool = False) -> dict:
    """The whole queue paused for `minutes` minutes, or until resumed when `minutes` is None: the running run pauses at
    its next check and no waiting job starts. With `is_stopping_now` the running run is stopped at once, as Ctrl-C
    stops it, and runs again first, with --resume, once the pause ends. Gives back the pause as `read_pause` reads it."""

    if minutes is not None and minutes < 1:
        raise ValueError(f"a pause of {minutes} minutes: give 1 minute or more")
    stored = {"isUntilResumed": True} if minutes is None else {
        "until": time.strftime(_TIME_FORMAT, time.localtime(time.time() + minutes * 60))}
    with _lock:
        queue_dir.mkdir(parents=True, exist_ok=True)
        path = queue_dir / PAUSE_FILE
        earlier = (json_store_utils.read_json(path) or {}) if path.is_file() else {}
        jobs = _load_jobs(queue_dir)
        # a pause changed keeps the runs the earlier one paused, so Resume lets them go on
        stored["pausedRuns"] = [*earlier.get("pausedRuns", []), *_pause_running_runs(jobs)]
        # written before the interrupt, so the worker that sees the job end knows the pause stopped it
        stored["stoppedJobs"] = [*earlier.get("stoppedJobs", []),
                                 *[job.job_id for job in jobs if is_stopping_now and job.status == run_job.JOB_RUNNING]]
        json_store_utils.write_json(path, stored)
        if is_stopping_now:
            _stop_running_jobs(jobs)

    return read_pause(queue_dir)


def _is_stopped_by_pause(queue_dir: Path,
                         job_id: str) -> bool:
    """Whether the queue's pause stopped this job, as Ctrl-C stops it."""

    path = queue_dir / PAUSE_FILE
    stored = (json_store_utils.read_json(path) or {}) if path.is_file() else {}

    return job_id in stored.get("stoppedJobs", [])


def resume_queue(queue_dir: Path) -> None:
    """The pause ended now: the run it paused goes on, and the next job starts as soon as no run is going."""

    with _lock:
        # a run the pause stopped and no server saw end is decided while the pause still names it, so it goes first
        jobs = _load_jobs(queue_dir)
        if _mark_lost_jobs(queue_dir, jobs):
            _save_jobs(queue_dir, jobs)
        _end_pause(queue_dir)
    _wake.set()


def read_default_provider_set(profile_name: str = "") -> str:
    """The provider set a job with none chosen is on: the `defaultProviderSet` of the settings files with the profile
    `profile_name` on top (the server's own profile when empty), read as they are now. A profile that names no
    `defaultProviderSet` leaves the default settings' one in force."""

    return str(config.read_layers(profile_name=profile_name or config.PROFILE_NAME,
                                  provider_set_name=config.DEFAULT_PROVIDER_SET_NAME).get(
        "defaultProviderSet", config.DEFAULT_PROVIDER_SET_NAME))


def read_job_provider_set(job: RunJob,
                        default_set: str) -> str:
    """The provider set a job runs on: the one its request names, else the one its run folder kept, else the
    `defaultProviderSet` of the job's own profile when it names one, else `default_set`."""

    return (job.request.provider_set or run_provider_set_service.read_chosen_provider_set(Path(job.request.output_root))
            or (read_default_provider_set(job.request.profile) if job.request.profile else default_set))


def read_job_lane(job: RunJob,
                  default_set: str) -> str:
    """The lane a job runs in: the machine its provider set names, so jobs of two sets on one machine wait for each
    other."""

    files = [config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE]
    if job.request.profile:
        files.append(config.build_profile_path(job.request.profile))
    stamps = tuple((str(path), path.stat().st_mtime_ns if path.is_file() else 0) for path in files)

    return _read_set_machine(read_job_provider_set(job, default_set), job.request.profile, stamps)


@lru_cache(maxsize=64)
def _read_set_machine(provider_set: str,
                      profile: str,
                      file_stamps: tuple[tuple[str, int], ...]) -> str:
    """The machine `provider_set` runs on (`config.read_set_machine`: the `machine` an old file names, else the provider its
    frames processing model runs on), its own name when that is empty or the set is gone from the files. `file_stamps` (each
    settings file's path and change time) makes another file, or a file saved since the last call, read again."""

    try:
        merged = config.read_layers(profile_name=profile or config.PROFILE_NAME, provider_set_name=provider_set)
    except (ValueError, OSError):
        return provider_set

    return config.read_set_machine(merged) or provider_set


def _select_next_job(jobs: list[RunJob],
                     lane: str | None = None) -> RunJob | None:
    """The job whose turn it is: one waiting for its retry before any queued job, and nothing while that retry's pause
    runs or while a job holds the queue. With a `lane`, only the jobs of that provider set count: its own retry, its own
    block, its own queue; with none, every job is one lane."""

    if lane is not None:
        default_set = read_default_provider_set()
        jobs = [job for job in jobs if read_job_lane(job, default_set) == lane]
    if any(job.status == run_job.JOB_BLOCKED for job in jobs):
        return None
    retrying = next((job for job in jobs if job.status == run_job.JOB_RETRYING), None)
    if retrying is not None:
        return retrying if retrying.retry_at <= _read_now() else None

    return next((job for job in jobs if job.status == run_job.JOB_QUEUED), None)


def _list_lanes(jobs: list[RunJob]) -> list[str]:
    """The lanes with a job that may still run (queued, waiting for its retry, holding the queue), oldest job first."""

    default_set = read_default_provider_set()
    lanes: list[str] = []
    for job in jobs:
        if job.status in (run_job.JOB_QUEUED, run_job.JOB_RETRYING, run_job.JOB_BLOCKED):
            lane = read_job_lane(job, default_set)
            if lane not in lanes:
                lanes.append(lane)

    return lanes


def _is_folder_in_use(jobs: list[RunJob],
                      job: RunJob) -> bool:
    """Whether another running job of the queue writes into this job's run folder: two lanes never run one folder."""

    root = Path(job.request.output_root).resolve()
    return any(other.job_id != job.job_id and other.status == run_job.JOB_RUNNING
               and process_utils.is_process_alive(other.pid) and Path(other.request.output_root).resolve() == root
               for other in jobs)


def _list_foreign_runs(jobs: list[RunJob]) -> list[str]:
    """The runs on this machine that no job of the queue started (one typed on the command line): the process list
    without the pids of the queue's own running jobs, whichever lane they are in."""

    own_pids = {job.pid for job in jobs if job.status == run_job.JOB_RUNNING}
    foreign = []
    for line in _list_runs():
        fields = line.split()
        if not (fields and fields[0].isdigit() and int(fields[0]) in own_pids):
            foreign.append(line)

    return foreign


def _decide_after_failure(job: RunJob,
                          status: str) -> tuple[str, str, str]:
    """The status a job that ended with `status` keeps, when its next try may start, and the console line saying
    so: retried while it has tries left, then holding the queue or letting it go on as its choice or the setting
    says."""

    if status not in run_job.RETRIED_STATUSES:
        return status, "", ""
    queue_settings = _read_queue_settings(job.request.profile or config.PROFILE_NAME)
    tries = queue_settings.retries + 1
    if job.attempt < tries:
        retry_at = time.strftime(_TIME_FORMAT, time.localtime(time.time() + queue_settings.retry_pause_seconds))
        line = (f"the job ended {status}; try {job.attempt + 1} of {tries} starts at {retry_at[11:]}, with --resume, "
                f"before any other job")

        return run_job.JOB_RETRYING, retry_at, line
    on_failure = job.request.on_failure or queue_settings.on_failure
    if on_failure == "block":
        return run_job.JOB_BLOCKED, "", (f"the job ended {status} on its last try ({job.attempt} of {tries}); the queue "
                                         f"holds until it is retried or skipped")

    return status, "", f"the job ended {status} on its last try ({job.attempt} of {tries}); the queue goes on"


def _write_console_line(queue_dir: Path,
                        job_id: str,
                        line: str) -> None:
    """One line of the queue's own added to the job's console, where the page shows it."""

    path = build_console_path(queue_dir, job_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as console:
            console.write(f"{line}\n")
    except OSError as error:
        # The decision is in jobs.json already; the console line only says it
        _LOG.warning("job %s: %s (the console could not take it: %s)", job_id, line, error)


def _record_start_failure(queue_dir: Path,
                          jobs: list[RunJob],
                          job: RunJob,
                          error: OSError) -> RunJob:
    """A job that cannot start ends `crashed`, and the worker goes on: left queued, it stopped every job after it.
    Recorded first: the console that could not be made may not take the reason either."""

    job.status, job.ended_at = run_job.JOB_CRASHED, _read_now()
    _save_jobs(queue_dir, jobs)
    _waited_job_ids.discard(job.job_id)
    try:
        with build_console_path(queue_dir, job.job_id).open("a", encoding="utf-8") as console:
            console.write(f"the run could not start: {error}\n")
    except OSError:
        pass

    return job


def run_next_job(queue_dir: Path,
                 lane: str | None = None) -> RunJob | None:
    """Run the job whose turn it is to its end and record how it ended: a failed job again before any other, then the
    oldest queued one. None when another run is still going (a job of the queue in this lane, or a run started on the
    command line), a retry's pause runs, a job holds the queue, or nothing is queued. With a `lane` (a provider set), only
    that lane's jobs count, and a job of another lane running does not hold this one back. Blocks while the child runs."""

    # STEP 1: under the lock, pick the job, unless another run still runs: one of the queue's own, or one typed on the
    # command line, which the queue knows only from the process list
    with _lock:
        # a timed pause that has passed lets the run it paused go on, before anything waits on that run
        if (queue_dir / PAUSE_FILE).is_file() and not is_queue_paused(queue_dir):
            _end_pause(queue_dir)
        jobs = _load_jobs(queue_dir)
        # a job that ended with no server watching is decided first, so a run it left unfinished is tried again now
        for lost, line in _mark_lost_jobs(queue_dir, jobs):
            _save_jobs(queue_dir, jobs)
            # a job that outlived a server restart ended unseen: its waiting rename is made here, as at a seen end
            _rename_ended_run_folders(jobs, [lost.request.output_root])
            if line:
                _write_console_line(queue_dir, lost.job_id, line)
        default_set = read_default_provider_set() if lane is not None else ""
        lane_jobs = jobs if lane is None else [job for job in jobs if read_job_lane(job, default_set) == lane]
        if any(job.status == run_job.JOB_RUNNING and process_utils.is_process_alive(job.pid) for job in lane_jobs):
            return None
        # the person paused the queue: a run stopped by hand is not followed at once by the next job
        if is_queue_paused(queue_dir):
            return None
        job = _select_next_job(jobs, lane)
        # The process list is read only when a job waits: read first, it ran `ps` every two seconds on an empty queue
        if job is None or _is_folder_in_use(jobs, job) or _list_foreign_runs(jobs):
            return None
        _waited_job_ids.add(job.job_id)
        try:
            process = _start_child(queue_dir, job)
        except OSError as error:
            return _record_start_failure(queue_dir, jobs, job, error)
        job.status, job.pid, job.started_at = run_job.JOB_RUNNING, process.pid, _read_now()
        job.attempt, job.retry_at = job.attempt + 1, ""
        try:
            _save_jobs(queue_dir, jobs)
        except OSError as error:
            # The child runs: step 3 records how it ended. Raised here, the worker ended and every later job waited
            _LOG.warning("job %s started, but %s could not be written: %s", job.job_id, JOBS_FILE, error)

    # STEP 2: wait for the child without the lock, so the API can read and add jobs while it runs
    exit_code = process.wait()

    # STEP 3: record the outcome the exit code reports, and whether a failed job is tried again or holds the queue.
    # Whatever the save does, the worker waits on this job no longer: a save that raised here ended the worker, and the
    # job read `running` for good
    with _lock:
        ended_at = _read_now()
        status, retry_at, line = _decide_after_failure(job, run_job.EXIT_STATUSES.get(exit_code, run_job.JOB_CRASHED))
        # a run the queue's pause stopped is no run the person stopped: it goes on first once the pause ends
        if status == run_job.EXIT_STATUSES[run_job.EXIT_INTERRUPTED] and _is_stopped_by_pause(queue_dir, job.job_id):
            status, retry_at = run_job.JOB_RETRYING, ended_at
            line = "the queue's pause stopped the run; it runs again first, with --resume, once the pause ends"
        elif status == run_job.EXIT_STATUSES[run_job.EXIT_INTERRUPTED] and _is_held_after_interrupt(queue_dir, job.job_id):
            status, retry_at, line = run_job.JOB_BLOCKED, "", _HELD_LINE
        try:
            jobs = _load_jobs(queue_dir)
            ended = _find_job(jobs, job.job_id)
            ended.exit_code, ended.ended_at, ended.status, ended.retry_at = exit_code, ended_at, status, retry_at
            _save_jobs(queue_dir, jobs)
            # a rename the Manager asked while the run wrote is made once no try of it is left to come
            _rename_ended_run_folders(jobs, [ended.request.output_root])
        except (OSError, UnknownJobError) as error:
            # The job returned tells how it ended, though the file could not
            _LOG.warning("job %s ended with exit code %s, but %s could not be written: %s", job.job_id, exit_code,
                         JOBS_FILE, error)
            job.exit_code, job.ended_at, job.status, job.retry_at = exit_code, ended_at, status, retry_at
            ended = job
        finally:
            _waited_job_ids.discard(job.job_id)
    if line:
        _write_console_line(queue_dir, job.job_id, line)

    return ended


def run_next_job_or_report(queue_dir: Path,
                           lane: str | None = None) -> RunJob | None:
    """`run_next_job`, except that a queue file that does not read, or a process list that cannot be taken, is said on
    the console and answered with None: the worker then waits and looks again, where an error would end its thread and
    leave every queued job waiting for good."""

    try:
        return run_next_job(queue_dir, lane)
    except (OSError, ValueError) as error:
        _LOG.warning("the job queue in %s could not be read, it is looked at again: %s", queue_dir, error)

        return None


def _run_lane(queue_dir: Path,
              lane: str) -> None:
    """One lane's waiter: runs the lane's next job to its end, then lets the dispatcher look again."""

    ran = None
    try:
        ran = run_next_job_or_report(queue_dir, lane)
    finally:
        with _lock:
            _running_lanes.discard(lane)
        # woken only by a job that ran: a lane with nothing to start is looked at again at the dispatcher's own pace
        if ran is not None:
            _wake.set()


def _start_lane_waiters(queue_dir: Path) -> None:
    """A waiter thread for each lane that has a job to run and none waiting yet."""

    with _lock:
        lanes = _list_lanes(_load_jobs(queue_dir))
        new_lanes = [lane for lane in lanes if lane not in _running_lanes]
        _running_lanes.update(new_lanes)
    for lane in new_lanes:
        threading.Thread(target=_run_lane, args=(queue_dir, lane), name=f"i2k-job-queue-{lane}", daemon=True).start()


def _process_the_queue(queue_dir: Path) -> None:
    """The dispatcher: every two seconds, or when woken, a waiter for each lane that has a job and none running."""

    while True:
        try:
            _start_lane_waiters(queue_dir)
        except (OSError, ValueError) as error:
            _LOG.warning("the job queue in %s could not be read, it is looked at again: %s", queue_dir, error)
        _wake.wait(_ADOPTED_POLL_S)
        _wake.clear()


def start_worker(queue_dir: Path) -> threading.Thread:
    """Start the one thread that runs the queue's jobs, for the life of the server."""

    queue_dir.mkdir(parents=True, exist_ok=True)
    # a run that ended with no server watching (a run typed on the command line, a job outliving a restart) gets its
    # waiting rename here
    _rename_ended_run_folders(_load_jobs(queue_dir), [folder for root in run_status_service.RUN_ROOTS.values()
                                                      if root.is_dir() for folder in root.iterdir() if folder.is_dir()])
    worker = threading.Thread(target=_process_the_queue, args=(queue_dir,), name="i2k-job-queue", daemon=True)
    worker.start()

    return worker
