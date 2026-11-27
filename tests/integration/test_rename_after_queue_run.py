"""The Manager's Rename refused a run folder a queued run was writing into ("kept: a run is writing into it", in red on
the page), and the run never got its AI name. Here the real queue worker runs a job whose process the real process
list reads as a run of this playbook on that folder; the rename is asked through the server's route while it writes,
and the folder must carry its AI name once the job ends."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from models.run_job import RunJobRequest
from models.run_paths import RunPaths
from services import document_file_service
from services import job_queue_service
from services import run_name_service
from services import run_operations_service
from services import run_status_service
from services import web_api_service

# A stand-in run: says it started through one pipe, then waits on another until the test lets it end
_FAKE_RUN = ("import sys; open(sys.argv[1], 'w').write('started'); open(sys.argv[2]).read()")


def test_a_run_renamed_while_its_queue_job_writes_is_renamed_when_the_job_ends_defect():
    # A rename while the run wrote was refused for good; now it is shown at once and made on disk at the job's end
    base = Path(tempfile.mkdtemp())
    root = base / "output"
    folder = root / "Recording-20260105"
    parts = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                            output_root_override=folder))
    parts.parent.mkdir(parents=True)
    parts.write_text(json.dumps({"topic": "Gateway refund rules"}), encoding="utf-8")
    started, release = base / "started", base / "release"
    os.mkfifo(started)
    os.mkfifo(release)
    queue_dir = base / "ui"
    settings = config.QueueSettings(retries=0, retry_pause_seconds=0, on_failure="continue")
    # The job's command reads `python -c <stand-in> <started> <release> <this playbook's main.py> run ... --output
    # <folder>`, which the process list takes for a run of this playbook writing into the folder
    previous = job_queue_service.set_command_prefix([sys.executable, "-c", _FAKE_RUN, str(started), str(release),
                                                     str(config.PLAYBOOK_ROOT / "main.py")])
    previous_reader = job_queue_service.set_queue_settings_reader(lambda profile: settings)
    previous_lister = job_queue_service.set_run_lister(lambda: [])
    previous_rename = job_queue_service.set_rename_after_run(run_operations_service.rename_after_run)
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"), output_root=folder)])
        worker = threading.Thread(target=job_queue_service.run_next_job, args=(queue_dir,))
        worker.start()
        started.read_text(encoding="utf-8")
        answer = TestClient(web_api_service.build_app()).post(
            "/api/runs/display-names", json={"runs": [{"root": "output", "name": folder.name}], "is_by_topic": True})
        record = answer.json()["records"][0]
        harness.check("while the run writes, the folder keeps its name and the answer says it is renamed when the run ends",
                      folder.is_dir() and record["outcome"] == "renamed when its run ends", answer.text)
        harness.check("the run is shown under its AI name at once",
                      run_name_service.read_display_name(folder) == "Gateway refund rules", "")
        release.write_text("end", encoding="utf-8")
        worker.join()
    finally:
        job_queue_service.set_command_prefix(previous)
        job_queue_service.set_queue_settings_reader(previous_reader)
        job_queue_service.set_run_lister(previous_lister)
        job_queue_service.set_rename_after_run(previous_rename)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
    renamed = root / "gateway-refund-rules"
    harness.check("once the job ends, the folder carries its AI name and no rename waits",
                  renamed.is_dir() and not folder.exists() and not run_name_service.read_waiting_rename(renamed),
                  str(sorted(path.name for path in root.iterdir())))


def test_a_queued_resume_follows_its_folder_renamed_through_the_page_defect():
    # The page renamed a run folder two queued resumes named; they kept the old path and would have run from phase 1
    # in a new folder. Through the server's route and the queue's own file, the queued job now follows the folder
    root = Path(tempfile.mkdtemp()) / "output"
    folder = root / "Recording-20260108"
    parts = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                            output_root_override=folder))
    parts.parent.mkdir(parents=True)
    parts.write_text(json.dumps({"topic": "Gateway launch plan"}), encoding="utf-8")
    queue_dir = root.parent / "ui"
    previous_queue = job_queue_service.QUEUE_DIR
    previous_roots = dict(run_status_service.RUN_ROOTS)
    job_queue_service.QUEUE_DIR = queue_dir
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"), output_root=folder,
                                                                        is_resuming=True)])
        TestClient(web_api_service.build_app()).post(
            "/api/runs/display-names", json={"runs": [{"root": "output", "name": folder.name}], "is_by_topic": True})
        stored = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    finally:
        job_queue_service.QUEUE_DIR = previous_queue
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
    job = next(row for row in stored if row["job_id"] == queued[0].job_id)
    harness.check("the queue's file names the renamed folder for the queued resume",
                  Path(job["request"]["output_root"]).resolve() == (root / "gateway-launch-plan").resolve(),
                  job["request"]["output_root"])
    # The command the queue starts names it too: left on the old folder, a resume was refused "no run to resume"
    command = job["command"]
    harness.check("the command the queue starts names the renamed folder after --output",
                  Path(command[command.index("--output") + 1]).resolve() == (root / "gateway-launch-plan").resolve(),
                  str(command))


def test_a_resume_queued_through_the_page_under_a_folder_s_old_name_runs_in_the_renamed_folder_defect():
    # A resume queued under a run's old folder name after the page renamed it was refused "no run to resume"; through
    # the server's rename and jobs routes and the queue's own file, it is queued for the renamed folder
    root = Path(tempfile.mkdtemp()) / "output"
    folder = root / "Recording-20260109"
    parts = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                            output_root_override=folder))
    parts.parent.mkdir(parents=True)
    parts.write_text(json.dumps({"topic": "Gateway billing rules"}), encoding="utf-8")
    queue_dir = root.parent / "ui"
    previous_queue = job_queue_service.QUEUE_DIR
    previous_roots = dict(run_status_service.RUN_ROOTS)
    job_queue_service.QUEUE_DIR = queue_dir
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        client = TestClient(web_api_service.build_app())
        client.post("/api/runs/display-names", json={"runs": [{"root": "output", "name": folder.name}], "is_by_topic": True})
        answer = client.post("/api/jobs", json={"items": [{"video": "/in/v.mp4", "output": str(folder)}],
                                                "is_resuming": True})
        stored = json.loads((queue_dir / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    finally:
        job_queue_service.QUEUE_DIR = previous_queue
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
    renamed = (root / "gateway-billing-rules").resolve()
    harness.check("the page renamed the folder", renamed.is_dir() and not folder.exists(), answer.text)
    job = stored[-1]
    harness.check("the queued resume names the renamed folder", Path(job["request"]["output_root"]).resolve() == renamed,
                  job["request"]["output_root"])
    command = job["command"]
    harness.check("and so does the command it starts",
                  Path(command[command.index("--output") + 1]).resolve() == renamed, str(command))
