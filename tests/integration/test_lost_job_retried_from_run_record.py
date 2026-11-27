"""A queued job that ended with no server watching, decided by its run's own record on disk (made data: two run folders
holding only `logs/<run id>/run.json`, one `incomplete`, one `complete`, and a real process that has ended). The real
queue, the real record reader and a stand-in for `main.py` run: the knowledge pass whose job ended unseen across a
server restart was marked lost and the queue went on to the next document; now it is run again first."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness

import config
from models import output_contract
from models import run_job
from models.run_job import RunJobRequest
from services import job_queue_service

_FAKE_MAIN = "import sys\nprint('child ran ' + ' '.join(sys.argv[1:]), flush=True)\n"


def _make_run_folder(root: Path,
                     name: str,
                     status: str) -> Path:
    folder = root / name
    record_dir = folder / output_contract.RUNS_DIR_NAME / "2026-11-10_12-00"
    record_dir.mkdir(parents=True)
    (record_dir / output_contract.RUN_STATE_FILE).write_text(json.dumps({"runId": "2026-11-10_12-00",
                                                                         "status": status}), encoding="utf-8")

    return folder


def test_a_lost_job_whose_run_record_is_incomplete_runs_again_and_a_complete_one_does_not_defect():
    # Defect: a lost job was never retried, though its run's record said it stopped at part 1. Fix: the record decides
    root = Path(tempfile.mkdtemp())
    queue_dir = root / "ui"
    fake_main = root / "fake_main.py"
    fake_main.write_text(_FAKE_MAIN, encoding="utf-8")
    unfinished = _make_run_folder(root, "unfinished", "incomplete")
    finished = _make_run_folder(root, "finished", "complete")
    previous = job_queue_service.set_command_prefix([sys.executable, str(fake_main)])
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    previous_reader = job_queue_service.set_queue_settings_reader(
        lambda profile: config.QueueSettings(retries=2, retry_pause_seconds=0))
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [
            RunJobRequest(video=root / "a.pdf", output_root=finished),
            RunJobRequest(video=root / "b.pdf", output_root=unfinished),
            RunJobRequest(video=root / "c.pdf", output_root=root / "next")])
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        jobs_path = queue_dir / job_queue_service.JOBS_FILE
        stored = json.loads(jobs_path.read_text(encoding="utf-8"))
        for row in stored[:2]:
            row.update(status=run_job.JOB_RUNNING, pid=ended.pid, attempt=1)
        jobs_path.write_text(json.dumps(stored), encoding="utf-8")
        listed = {job.job_id: job.status for job in job_queue_service.list_jobs(queue_dir)}
        first = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_queue_settings_reader(previous_reader)
    harness.check("the job whose run completed is lost, the unfinished one waits for its retry",
                  listed[queued[0].job_id] == run_job.JOB_LOST and listed[queued[1].job_id] == run_job.JOB_RETRYING,
                  str(listed))
    harness.check("the unfinished run's job runs again before the next document, with --resume",
                  first is not None and first.job_id == queued[1].job_id
                  and "--resume" in job_queue_service.build_console_path(queue_dir, first.job_id).read_text(),
                  str(first))
