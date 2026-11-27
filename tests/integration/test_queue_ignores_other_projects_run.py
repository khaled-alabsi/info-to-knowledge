"""The queue held a job's retry for as long as another project's `main.py run` went on (a sibling
program, started as `main.py run` from its own folder), counted as a run of this playbook (DEFECTS 703). Here a real
process runs another project's `main.py run` from its own folder, and the real queue worker, with its real process
list, starts the waiting job."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness

import config
from models.run_job import RunJobRequest
from services import job_queue_service

_FAKE_MAIN = "import sys\nprint('child ran', flush=True)\nsys.exit(0)\n"


def test_another_projects_main_py_run_holds_no_job_of_the_queue_defect():
    # The retry waited on another project's run; now the queue starts its job beside it
    other = Path(tempfile.mkdtemp())
    (other / "main.py").write_text("import sys\nprint('ready', flush=True)\nsys.stdin.read()\n", encoding="utf-8")
    foreign = subprocess.Popen([sys.executable, "main.py", "run", "--output", "."], cwd=other, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    foreign.stdout.readline()
    stand_in = Path(tempfile.mkdtemp()) / "fake_main.py"
    stand_in.write_text(_FAKE_MAIN, encoding="utf-8")
    queue_dir = Path(tempfile.mkdtemp()) / "ui"
    settings = config.QueueSettings(retries=0, retry_pause_seconds=0, on_failure="continue")
    previous = job_queue_service.set_command_prefix([sys.executable, str(stand_in)])
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: settings)
    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"), output_root=Path("/out/v"))])
        ran = job_queue_service.run_next_job(queue_dir)
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_queue_settings_reader(previous_reader)
        foreign.kill()
    harness.check("the queued job runs beside another project's main.py run", ran is not None, str(ran))
