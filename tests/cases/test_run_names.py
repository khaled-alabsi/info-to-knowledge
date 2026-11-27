"""Run names: the Manager's Rename renames a run folder to its AI name (the topic phase 4 wrote, as a safe folder name)
and shows the AI name as written, and back to the folder's name before; the name it had is kept in its
`run-name.json`."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from models.run_job import RunJob
from models.run_job import RunJobRequest
from models.run_paths import RunPaths
from services import document_file_service
from services import job_queue_service
from services import run_name_service
from services import run_operations_service
from services import run_reset_service
from services import run_status_service
from services import web_api_service


def _build_run(root: Path,
               name: str,
               topic: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    if topic:
        parts = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                                output_root_override=folder))
        parts.parent.mkdir(parents=True, exist_ok=True)
        parts.write_text(json.dumps({"topic": topic}), encoding="utf-8")

    return folder


def test_a_run_folder_is_renamed_to_its_ai_name_and_back_and_a_run_with_no_topic_or_a_taken_name_keeps_its_own():
    root = Path(tempfile.mkdtemp()) / "output"
    named = _build_run(root, "Recording-20260101", "Gateway refund rules")
    bare = _build_run(root, "Recording-20260102", "")
    result = run_operations_service.name_runs([named, bare], True)
    renamed = root / "gateway-refund-rules"
    harness.check("the folder is renamed to its AI name as a folder name, and shows the AI name as written",
                  renamed.is_dir() and not named.exists()
                  and run_name_service.read_display_name(renamed) == "Gateway refund rules", result.output)
    harness.check("the run with no topic keeps its name, and says so",
                  bare.is_dir() and "kept: no AI name yet" in result.output, result.output)
    run_operations_service.name_runs([renamed], False)
    harness.check("back to its file name: the folder has its old name and no name file",
                  named.is_dir() and not renamed.exists() and not (named / "run-name.json").exists(), "")
    run_operations_service.name_runs([named], True)
    named.mkdir()
    taken = run_operations_service.name_runs([renamed], False)
    harness.check("a file name another run folder holds is never taken over", renamed.is_dir() and "is taken" in taken.output,
                  taken.output)


def test_a_run_whose_ai_name_a_branch_already_holds_takes_the_next_free_name_defect():
    # A run and the branch made from it had one AI name; the branch was renamed first, and the run was kept with
    # "is taken" for good. Now the run takes the name with the next free "-2", and the branch keeps its folder.
    root = Path(tempfile.mkdtemp()) / "output"
    run = _build_run(root, "report-v2", "Gateway refund rules")
    branch = _build_run(root, "report-v2-from-4", "Gateway refund rules")
    run_operations_service.name_runs([branch], True)
    result = run_operations_service.name_runs([run], True)
    harness.check("the branch keeps the name, the run takes it with -2 and shows the AI name as written",
                  (root / "gateway-refund-rules").is_dir() and (root / "gateway-refund-rules-2").is_dir()
                  and run_name_service.read_display_name(root / "gateway-refund-rules-2") == "Gateway refund rules",
                  result.output)
    run_operations_service.name_runs([root / "gateway-refund-rules-2"], False)
    harness.check("renamed back, the run has its file name again", (root / "report-v2").is_dir(), "")


def test_the_page_names_the_ticked_runs_and_the_run_list_carries_the_display_name():
    root = Path(tempfile.mkdtemp()) / "output"
    _build_run(root, "Recording-20260101", "Gateway refund rules")
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.post("/api/runs/display-names", json={"runs": [{"root": "output", "name": "Recording-20260101"}]})
        harness.check("named, exit 0", answer.status_code == 200 and answer.json()["exit_code"] == 0, answer.text)
        listed = next(run for run in client.get("/api/runs").json() if run["name"] == "gateway-refund-rules")
        harness.check("the run list holds the renamed folder, shown under its AI name",
                      listed["display_name"] == "Gateway refund rules" and listed["topic"] == "Gateway refund rules", str(listed))
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)


def test_the_pencil_shows_a_run_under_a_name_typed_and_an_empty_name_shows_its_folder_again():
    root = Path(tempfile.mkdtemp()) / "output"
    folder = _build_run(root, "Recording-20260104", "")
    named = run_operations_service.set_run_display_name(folder, "  Gateway   launch plan ")
    harness.check("the run is shown under the name typed, its spaces collapsed, and its folder keeps its name",
                  named.exit_code == 0 and run_name_service.read_display_name(folder) == "Gateway launch plan"
                  and folder.is_dir(), named.output)
    cleared = run_operations_service.set_run_display_name(folder, "")
    harness.check("an empty name shows the run under its folder's name again",
                  cleared.exit_code == 0 and not (folder / "run-name.json").exists(), cleared.output)
    missing = run_operations_service.set_run_display_name(root / "Recording-gone", "Acme")
    harness.check("a folder that is not there is refused", missing.exit_code != 0, missing.output)


def test_a_queued_resume_follows_its_run_folder_when_the_folder_is_renamed_defect():
    # Two queued resumes of a run kept its old folder name after the Manager renamed the folder, and would have started
    # the run again from phase 1 in a new folder. Now each queued job is pointed at the folder's new name
    root = Path(tempfile.mkdtemp()) / "output"
    folder = _build_run(root, "Recording-20260107", "Gateway refund rules")
    queue_dir = root.parent / "ui"
    previous = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = queue_dir
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"), output_root=folder,
                                                                        is_resuming=True)])
        run_operations_service.name_runs([folder], True)
    finally:
        job_queue_service.QUEUE_DIR = previous
    job = job_queue_service.find_job(queue_dir, queued[0].job_id)
    harness.check("the queued resume now names the renamed folder",
                  Path(job.request.output_root) == root / "gateway-refund-rules", str(job.request.output_root))


def test_a_queued_resume_starts_its_process_in_the_renamed_folder_defect():
    # A queued resume renamed under its run kept the old folder in the command it starts, so it was refused ("no run to
    # resume") and left an empty folder of the old name. Now the command names the new folder too
    root = Path(tempfile.mkdtemp()) / "output"
    folder = _build_run(root, "Recording-20260109", "Gateway refund rules")
    queue_dir = root.parent / "ui"
    previous = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = queue_dir
    try:
        queued = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"), output_root=folder,
                                                                        is_resuming=True)])
        run_operations_service.name_runs([folder], True)
    finally:
        job_queue_service.QUEUE_DIR = previous
    command = job_queue_service.find_job(queue_dir, queued[0].job_id).command
    harness.check("the command the queue starts names the renamed folder after --output",
                  command[command.index("--output") + 1] == str(root / "gateway-refund-rules"), str(command))


def test_a_job_queued_with_an_old_folder_in_its_command_starts_in_its_request_s_folder_defect():
    # Jobs queued before the fix above kept the old folder in their command after a rename, and were refused; the
    # line a job runs now takes its folder from the job's request
    folder = Path(tempfile.mkdtemp()) / "output" / "gateway-refund-rules"
    old = folder.parent / "Recording-20260110"
    job = RunJob(job_id="j1", request=RunJobRequest(video=Path("/in/v.mp4"), output_root=folder, is_resuming=True),
                 command=job_queue_service.build_run_command(RunJobRequest(video=Path("/in/v.mp4"), output_root=old,
                                                                           is_resuming=True)))
    command = job_queue_service._build_try_command(job)
    harness.check("the line names the request's folder", command[command.index("--output") + 1] == str(folder),
                  str(command))


def test_a_resume_queued_under_a_renamed_folder_s_old_name_runs_in_the_renamed_folder_defect():
    # A resume queued under a run's old folder name after its rename was refused "no run to resume". Now a job naming a
    # folder that is gone, which a folder beside it names as its former name, is queued for that folder
    root = Path(tempfile.mkdtemp()) / "output"
    folder = root / "gateway-refund-rules"
    folder.mkdir(parents=True)
    run_name_service.write_display_name(folder, "Gateway refund rules", "Recording-20260111")
    (root / "other-run").mkdir()
    queue_dir = root.parent / "ui"
    job = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/v.mp4"),
                                                                 output_root=root / "Recording-20260111",
                                                                 is_resuming=True)])[0]
    harness.check("the job is queued for the renamed folder", job.request.output_root == folder, str(job.request))
    harness.check("and its command names it", job.command[job.command.index("--output") + 1] == str(folder),
                  str(job.command))
    fresh = job_queue_service.queue_jobs(queue_dir, [RunJobRequest(video=Path("/in/w.mp4"),
                                                                   output_root=root / "Recording-20260112")])[0]
    harness.check("a folder no run was renamed from stays as named", fresh.request.output_root == root / "Recording-20260112",
                  str(fresh.request))
    # A job already holding the old name when the folder was renamed by another way starts in the renamed folder too
    stale = RunJob(job_id="j2", request=RunJobRequest(video=Path("/in/v.mp4"), output_root=root / "Recording-20260111",
                                                      is_resuming=True),
                   command=job_queue_service.build_run_command(RunJobRequest(
                       video=Path("/in/v.mp4"), output_root=root / "Recording-20260111", is_resuming=True)))
    command = job_queue_service._build_try_command(stale)
    harness.check("a job holding the old name starts in the renamed folder",
                  command[command.index("--output") + 1] == str(folder), str(command))


def test_a_run_renamed_while_a_run_writes_into_it_is_shown_at_once_and_renamed_when_it_ends_defect():
    # The rename of a folder a run wrote into was refused ("kept: a run is writing into it") and never made. Now it is
    # shown under its AI name at once, waits in its name file, and is made once no run writes into it
    root = Path(tempfile.mkdtemp()) / "output"
    folder = _build_run(root, "Recording-20260106", "Gateway refund rules")
    live = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()", str(config.PLAYBOOK_ROOT / "main.py"),
                             "run", "--output", str(folder)], stdin=subprocess.PIPE)
    try:
        while not run_reset_service.list_live_runs(folder):
            harness.check("the stand-in run is still alive", live.poll() is None, "")
        result = run_operations_service.name_runs([folder], True)
        harness.check("while it writes, the folder keeps its name, is shown under its AI name, and its rename waits",
                      folder.is_dir() and result.records[0]["outcome"] == run_operations_service.RENAMED_WHEN_RUN_ENDS
                      and run_name_service.read_display_name(folder) == "Gateway refund rules"
                      and run_name_service.read_waiting_rename(folder) == "ai-name", result.output)
        harness.check("nothing is renamed while the run writes", run_operations_service.rename_after_run(folder) == folder, "")
    finally:
        live.communicate(b"")
    moved = run_operations_service.rename_after_run(folder)
    harness.check("once the run ended, the folder carries its AI name and no rename waits",
                  moved == root / "gateway-refund-rules" and moved.is_dir() and not folder.exists()
                  and not run_name_service.read_waiting_rename(moved), str(moved))
