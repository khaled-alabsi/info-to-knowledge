"""The queue runs a failed job again, with `--resume`, before it moves on, and after the last try either goes on or
holds, as the job's own choice or the `queue.onFailure` setting says.

The queue runs a stand-in for `main.py` that exits with the code its video's name asks for, so every try fails the
same way with no model and no wait.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from support import harness

import config
from models import output_contract
from models import run_job
from models.run_job import RunJobRequest
from services import control_signal_service
from services import job_queue_service

# The stand-in for `main.py`: prints its arguments and exits with the number after the first dash in the video's stem
_FAKE_MAIN = """
import sys
from pathlib import Path
arguments = sys.argv[1:]
print("child ran " + " ".join(arguments), flush=True)
sys.exit(int(Path(arguments[arguments.index("--video") + 1]).stem.split("-", 1)[1]))
"""


def _make_fake_main() -> Path:
    path = Path(tempfile.mkdtemp()) / "fake_main.py"
    path.write_text(_FAKE_MAIN, encoding="utf-8")

    return path


def _run_queue(codes: tuple[int, ...],
               steps: int,
               retries: int,
               pause_seconds: int = 0,
               on_failure: str = "continue",
               job_on_failure: str = "") -> tuple[Path, list, list]:
    """Queue one job per exit code, take `steps` turns of the worker under these queue settings, and give back the
    queue folder, the queued jobs and what each turn returned."""

    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    queue_settings = config.QueueSettings(retries=retries, retry_pause_seconds=pause_seconds, on_failure=on_failure)
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: queue_settings)
    try:
        requests = [RunJobRequest(video=Path(f"/in/v-{code}.mp4"), output_root=Path("/out/v"), on_failure=job_on_failure)
                    for code in codes]
        queued = job_queue_service.queue_jobs(queue_dir, requests)
        turns = [job_queue_service.run_next_job(queue_dir) for _ in range(steps)]
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_queue_settings_reader(previous_reader)

    return queue_dir, queued, turns


def test_a_failed_job_is_run_again_with_resume_before_the_next_job_defect():
    # A job that failed was followed at once by the next one: 14 jobs in a row ended "incomplete" in seconds each.
    # Now a failed job is run again, with --resume, up to queue.retries times, and no other job starts in between.
    queue_dir, queued, turns = _run_queue(codes=(1, 0), steps=4, retries=2)
    ids = [turn.job_id if turn else None for turn in turns]
    harness.check("the failed job runs three times before the next job", ids == [queued[0].job_id] * 3 + [queued[1].job_id],
                  str(ids))
    harness.check("each try but the last waits for the next", [turn.status for turn in turns[:3]]
                  == [run_job.JOB_RETRYING, run_job.JOB_RETRYING, "incomplete"], str([turn.status for turn in turns]))
    harness.check("the job counts its tries", turns[2].attempt == 3, str(turns[2].attempt))
    console = job_queue_service.build_console_path(queue_dir, queued[0].job_id).read_text(encoding="utf-8")
    runs = [line for line in console.splitlines() if line.startswith("child ran")]
    harness.check("the first try runs the job's own line, each retry adds --resume",
                  len(runs) == 3 and "--resume" not in runs[0] and all(line.endswith("--resume") for line in runs[1:]),
                  console)
    harness.check("the console says which try comes next", "try 2 of 3" in console and "try 3 of 3" in console, console)


def test_a_retry_waits_its_pause_and_no_other_job_starts_meanwhile():
    _, queued, turns = _run_queue(codes=(1, 0), steps=2, retries=1, pause_seconds=600)
    harness.check("the failed job waits for its retry", turns[0].status == run_job.JOB_RETRYING and turns[0].retry_at,
                  f"{turns[0].status} {turns[0].retry_at!r}")
    harness.check("while it waits nothing else starts", turns[1] is None, str(turns[1]))


def test_a_stopped_or_refused_job_is_not_run_again():
    _, queued, turns = _run_queue(codes=(130, 2, 0), steps=3, retries=2)
    harness.check("a stopped job and a refused one end at once and the next runs",
                  [turn.status for turn in turns] == ["stopped", "refused", "finished"], str([turn.status for turn in turns]))


def test_a_job_set_to_block_holds_the_queue_after_its_last_try_until_skipped():
    queue_dir, queued, turns = _run_queue(codes=(1, 0), steps=2, retries=0, job_on_failure="block")
    harness.check("after its last try the job holds the queue", turns[0].status == run_job.JOB_BLOCKED
                  and turns[1] is None, f"{turns[0].status} {turns[1]}")
    skipped = job_queue_service.skip_blocked_job(queue_dir, queued[0].job_id)
    harness.check("a skipped blocked job ends with the status its exit code gives", skipped.status == "incomplete",
                  skipped.status)


def test_a_blocked_job_retried_by_hand_runs_again_first():
    queue_dir, queued, _ = _run_queue(codes=(1, 0), steps=1, retries=0, on_failure="block")
    job_queue_service.retry_blocked_job(queue_dir, queued[0].job_id)
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    queue_settings = config.QueueSettings(retries=0, on_failure="block")
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: queue_settings)
    try:
        again = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_queue_settings_reader(previous_reader)
    harness.check("the setting's block holds the queue, and a retry by hand runs that job again before the next",
                  again.job_id == queued[0].job_id and again.attempt == 2 and again.status == run_job.JOB_BLOCKED,
                  f"{again.job_id} {again.attempt} {again.status}")


def test_a_job_s_own_choice_wins_over_the_setting():
    _, _, turns = _run_queue(codes=(1, 0), steps=2, retries=0, on_failure="block", job_on_failure="continue")
    harness.check("a job set to continue goes on under a setting of block",
                  [turn.status for turn in turns] == ["incomplete", "finished"], str([turn.status for turn in turns]))


def test_a_job_s_choice_is_changed_and_an_unknown_one_refused():
    queue_dir, queued, _ = _run_queue(codes=(0,), steps=0, retries=0)
    changed = job_queue_service.set_on_failure(queue_dir, queued[0].job_id, "block")
    harness.check("the job keeps the choice", changed.request.on_failure == "block"
                  and job_queue_service.find_job(queue_dir, queued[0].job_id).request.on_failure == "block",
                  changed.request.on_failure)
    try:
        job_queue_service.set_on_failure(queue_dir, queued[0].job_id, "explode")
        outcome = "accepted"
    except ValueError as refused:
        outcome = str(refused)
    harness.check("an unknown choice is refused by name", "explode" in outcome, outcome)


def _lose_first_job(run_status: str) -> tuple[Path, list, list]:
    """Two queued jobs, the first recorded running under a process that is gone (its server restarted while it ran), its
    run's own record ending `run_status`; then two turns of the worker."""

    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: config.QueueSettings(
        retries=2, retry_pause_seconds=0))
    previous_status = job_queue_service.set_run_status_reader(lambda output_root: run_status)
    try:
        requests = [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=Path(f"/out/v{number}")) for number in (1, 2)]
        queued = job_queue_service.queue_jobs(queue_dir, requests)
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        jobs_path = queue_dir / job_queue_service.JOBS_FILE
        stored = json.loads(jobs_path.read_text(encoding="utf-8"))
        stored[0].update(status=run_job.JOB_RUNNING, pid=ended.pid, attempt=1)
        jobs_path.write_text(json.dumps(stored), encoding="utf-8")
        turns = [job_queue_service.run_next_job(queue_dir) for _ in range(2)]
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_queue_settings_reader(previous_reader)
        job_queue_service.set_run_status_reader(previous_status)

    return queue_dir, queued, turns


def test_a_job_lost_while_its_run_did_not_finish_is_run_again_before_the_next_defect():
    # Defect: a knowledge pass's job, started before a server restart, ended unseen and was marked lost; the queue went
    # on to the next document though the pass had stopped at part 1. Now the run's own record decides: not complete, the
    # lost job is retried with --resume before any other job
    queue_dir, queued, turns = _lose_first_job("incomplete")
    harness.check("the lost job runs again first, then the next", [turn.job_id for turn in turns]
                  == [queued[0].job_id, queued[1].job_id], str([turn.job_id for turn in turns]))
    console = job_queue_service.build_console_path(queue_dir, queued[0].job_id).read_text(encoding="utf-8")
    harness.check("its retry resumes, and the console says why", "--resume" in console and "ended lost" in console,
                  console)


def test_a_job_lost_after_its_run_completed_is_not_run_again():
    queue_dir, queued, turns = _lose_first_job("complete")
    harness.check("the next job runs at once", turns[0] is not None and turns[0].job_id == queued[1].job_id,
                  str([turn.job_id if turn else None for turn in turns]))
    lost = job_queue_service.find_job(queue_dir, queued[0].job_id)
    harness.check("the lost job whose run completed stays lost", lost.status == run_job.JOB_LOST, lost.status)


def test_a_job_lost_after_its_run_completed_gets_its_waiting_rename_defect():
    # A rename asked while a run wrote waited for the job's end, and a job that outlived a server restart ends unseen:
    # its folder kept its old name. Now the lost job's end asks for the rename as a seen end does
    asked = []
    previous = job_queue_service.set_rename_after_run(lambda output_root: asked.append(output_root) or output_root)
    try:
        _, queued, _ = _lose_first_job("complete")
    finally:
        job_queue_service.set_rename_after_run(previous)
    harness.check("the lost job's folder is asked for its rename", Path("/out/v1") in asked, str(asked))


def test_a_waiting_retry_is_cancelled_and_kept_when_ended_jobs_are_cleared():
    queue_dir, queued, _ = _run_queue(codes=(1,), steps=1, retries=1, pause_seconds=600)
    cleared = job_queue_service.clear_ended_jobs(queue_dir)
    harness.check("clearing ended jobs keeps a job waiting for its retry", cleared == 0, str(cleared))
    cancelled = job_queue_service.cancel_job(queue_dir, queued[0].job_id)
    harness.check("a job waiting for its retry can be cancelled", cancelled.status == run_job.JOB_CANCELLED,
                  cancelled.status)


def _check_pause_holds_until_resumed(minutes: int | None) -> None:
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    previous = job_queue_service.set_command_prefix([sys.executable, str(_make_fake_main())])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=Path("/out/v"))])
        pause = job_queue_service.pause_queue(queue_dir, minutes)
        harness.check(f"the pause of {minutes} minutes reads back as set", job_queue_service.read_pause(queue_dir) == pause
                      and pause["is_until_resumed"] == (minutes is None) and bool(pause["until"]) == (minutes is not None),
                      str(pause))
        harness.check("no job starts while paused", job_queue_service.run_next_job(queue_dir) is None)
        job_queue_service.resume_queue(queue_dir)
        harness.check("no pause after resume", not job_queue_service.is_queue_paused(queue_dir))
        turn = job_queue_service.run_next_job(queue_dir)
        harness.check("the job starts once resumed", turn is not None and turn.status == "finished",
                      str(turn and turn.status))
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)


def test_a_paused_queue_starts_no_job_until_resumed():
    _check_pause_holds_until_resumed(minutes=10)


def test_a_queue_paused_with_no_time_holds_until_resumed_defect():
    # The top bar's Pause queue offered only 5 to 60 minutes, each ending by itself; a pause that waits for the person
    # was missing. Now a pause with no minutes holds the queue until Resume now.
    _check_pause_holds_until_resumed(minutes=None)


def test_a_queue_pause_pauses_the_running_run_and_resume_lets_it_go_on_defect():
    # Pause queue held back only the waiting jobs, so the running run went on and the owner saw nothing paused.
    # Now it writes the running run's own pause signal, and Resume (or the pause's end) clears it.
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    output_root = Path(tempfile.mkdtemp())
    run_dir = output_root / output_contract.RUNS_DIR_NAME / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / output_contract.RUN_STATE_FILE).write_text("{}", encoding="utf-8")
    queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=output_root)])
    jobs = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    jobs[0].update(status=run_job.JOB_RUNNING, pid=os.getpid())
    (queue_dir / job_queue_service.JOBS_FILE).write_text(json.dumps(jobs), encoding="utf-8")

    job_queue_service.pause_queue(queue_dir, None)
    harness.check("the running run is told to pause", control_signal_service.read_signal(run_dir)
                  == control_signal_service.PAUSE, str(control_signal_service.read_signal(run_dir)))
    job_queue_service.resume_queue(queue_dir)
    harness.check("resume lets the run go on", control_signal_service.read_signal(run_dir) is None,
                  str(control_signal_service.read_signal(run_dir)))

    (queue_dir / job_queue_service.PAUSE_FILE).write_text(json.dumps(
        {"until": "2000-01-01T00:00:00", "pausedRuns": [str(run_dir)]}), encoding="utf-8")
    control_signal_service.request(run_dir, control_signal_service.PAUSE, by="queue")
    job_queue_service.run_next_job(queue_dir)
    harness.check("a timed pause that has passed lets the run go on", control_signal_service.read_signal(run_dir) is None
                  and not (queue_dir / job_queue_service.PAUSE_FILE).exists(), str(queued[0].job_id))


# A stand-in for `main.py` that says it is ready and waits for Ctrl-C, ending as `run` does on it; run with --resume, it
# finishes at once
_WAITING_MAIN = """
import signal, sys
if "--resume" in sys.argv:
    print("resumed", flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
print("ready", flush=True)
signal.pause()
"""


def test_a_pause_that_stops_the_running_run_now_runs_it_again_first_once_resumed_defect():
    # A queue pause let the running run finish its long model call first; the owner asked for a switch that stops it at
    # once, as Ctrl-C does. Now it is stopped, and runs again first, with --resume, once the pause ends.
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    main = Path(tempfile.mkdtemp()) / "waiting_main.py"
    main.write_text(_WAITING_MAIN, encoding="utf-8")
    previous = job_queue_service.set_command_prefix([sys.executable, str(main)])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=Path("/out/v")),
                                                          RunJobRequest(video=Path("/in/w-0.mp4"), output_root=Path("/out/w"))])
        turns: list = []
        worker = threading.Thread(target=lambda: turns.append(job_queue_service.run_next_job(queue_dir)))
        worker.start()
        console = job_queue_service.build_console_path(queue_dir, queued[0].job_id)
        deadline = time.monotonic() + 20
        while ("ready" not in (console.read_text(encoding="utf-8") if console.is_file() else "")
               and time.monotonic() < deadline):
            pass
        harness.check("the child started", time.monotonic() < deadline)
        job_queue_service.pause_queue(queue_dir, None, is_stopping_now=True)
        worker.join(timeout=20)
        harness.check("the running run was stopped at once and waits to run again", turns and turns[0].status
                      == run_job.JOB_RETRYING, str(turns and turns[0].status))
        harness.check("nothing starts while the pause holds", job_queue_service.run_next_job(queue_dir) is None)
        job_queue_service.resume_queue(queue_dir)
        again = job_queue_service.run_next_job(queue_dir)
        harness.check("once resumed, the stopped run goes first, with --resume", again is not None
                      and again.job_id == queued[0].job_id and again.status == "finished"
                      and "resumed" in console.read_text(encoding="utf-8"), str(again and again.status))
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)


def test_a_run_the_pause_stopped_with_no_server_watching_runs_again_at_once_on_resume_defect():
    # The server restarted while a run went on; stopped by the pause, its end was read as a lost job's, which waits the
    # failure's retry pause: it started a minute after Resume. Now it goes first and at once.
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v-0.mp4"), output_root=Path("/out/v"))])
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    jobs = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    jobs[0].update(status=run_job.JOB_RUNNING, pid=gone.pid)
    (queue_dir / job_queue_service.JOBS_FILE).write_text(json.dumps(jobs), encoding="utf-8")
    (queue_dir / job_queue_service.PAUSE_FILE).write_text(json.dumps(
        {"isUntilResumed": True, "stoppedJobs": [queued[0].job_id]}), encoding="utf-8")
    previous = job_queue_service.set_queue_settings_reader(
        lambda profile: config.QueueSettings(retries=2, retry_pause_seconds=600, on_failure="continue"))
    try:
        job_queue_service.resume_queue(queue_dir)
    finally:
        job_queue_service.set_queue_settings_reader(previous)
    job = job_queue_service.find_job(queue_dir, queued[0].job_id)
    harness.check("the stopped run waits for no retry pause", job.status == run_job.JOB_RETRYING
                  and job.retry_at <= time.strftime("%Y-%m-%dT%H:%M:%S"), f"{job.status} {job.retry_at}")


def test_a_pause_that_has_passed_holds_nothing_and_zero_minutes_is_refused():
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    queue_dir.mkdir(parents=True)
    (queue_dir / job_queue_service.PAUSE_FILE).write_text(json.dumps({"until": "2000-01-01T00:00:00"}), encoding="utf-8")
    harness.check("a past pause reads as none", not job_queue_service.is_queue_paused(queue_dir))
    try:
        job_queue_service.pause_queue(queue_dir, 0)
        harness.check("a pause of 0 minutes is refused", False)
    except ValueError:
        harness.check("a pause of 0 minutes is refused", True)
