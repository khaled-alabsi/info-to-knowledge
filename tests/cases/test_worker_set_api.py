"""The API's worker sets: the list, a run queued on a set, an unknown set refused with 400, a run
already held by a job on another set refused with 409, and a set given to a run that is not running."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from support import harness

import config
from models import run_job
from models.run_job import RunJobRequest
from models.web_api_request import StartJobsRequest, WorkerSetRequest
from services import job_queue_service, run_worker_set_service, web_api_service

_SETTINGS = {"lmstudioBaseUrl": "http://localhost:1234/v1",
             "workerSets": {"_note": "a note", "second-host": {"lmstudioBaseUrl": "http://second-host.example:1234/v1"},
                            "Mac": {"lmstudioBaseUrl": "http://mac.example:1234/v1"}}}


def _use(folder: Path) -> tuple:
    (folder / "playbook.default.json").write_text(json.dumps(_SETTINGS), encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
                config.WORKER_SET_NAME, config.settings, job_queue_service.QUEUE_DIR)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME, config.WORKER_SET_NAME = folder, "", config.DEFAULT_WORKER_SET_NAME
    job_queue_service.QUEUE_DIR = folder / "queue"

    return previous


def _give_back(previous: tuple) -> None:
    (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
     config.WORKER_SET_NAME, config.settings, job_queue_service.QUEUE_DIR) = previous


def _refusal(call) -> tuple:
    try:
        call()
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)

    return 0, ""


def _queue(body: StartJobsRequest) -> list:
    return web_api_service.queue_jobs(body, SimpleNamespace(state=SimpleNamespace()))


def test_the_list_names_default_first_then_each_set_with_what_it_changes():
    previous = _use(Path(tempfile.mkdtemp()))
    try:
        listed = web_api_service.list_worker_sets()
    finally:
        _give_back(previous)
    harness.check("default first, the note is no set, each set names the settings it changes",
                  listed == {"default_set": "default",
                             "sets": [{"name": "default", "changes": [], "machine": ""},
                                      {"name": "Mac", "changes": ["lmstudioBaseUrl"], "machine": ""},
                                      {"name": "second-host", "changes": ["lmstudioBaseUrl"], "machine": ""}]},
                  str(listed))


def test_each_set_names_the_machine_it_runs_on_defect():
    # Defect: the owner could not find the main machine among the worker sets: its set showed only as "default".
    # Fix: `workerSetMachines` names each set's machine, and the list carries it beside each set's name.
    folder = Path(tempfile.mkdtemp())
    previous = _use(folder)
    try:
        settings = {**_SETTINGS, "workerSetMachines": {"default": "main machine", "second-host": "gpu-box"}}
        (folder / "playbook.default.json").write_text(json.dumps(settings), encoding="utf-8")
        listed = web_api_service.list_worker_sets()
    finally:
        _give_back(previous)
    machine_dict = {entry["name"]: entry["machine"] for entry in listed["sets"]}
    harness.check("default names the main machine, second-host the gpu-box, a set with no entry names none",
                  machine_dict == {"default": "main machine", "Mac": "", "second-host": "gpu-box"}, str(listed))


def test_a_run_is_queued_on_a_set_and_an_unknown_set_is_refused():
    root = Path(tempfile.mkdtemp())
    previous = _use(root)
    try:
        items = [{"video": "/in/a.mp4", "output": "/out/a"}]
        queued = _queue(StartJobsRequest(items=items, worker_set="second-host"))
        unknown = _refusal(lambda: _queue(StartJobsRequest(items=items, worker_set="spare")))
        defaults = web_api_service.read_run_defaults(worker_set="second-host")
        bad_defaults = _refusal(lambda: web_api_service.read_run_defaults(worker_set="spare"))
    finally:
        _give_back(previous)
    harness.check("the job carries the set", queued[0]["request"]["worker_set"] == "second-host", str(queued))
    harness.check("an unknown set is 400 and names the sets", unknown[0] == 400 and "second-host" in unknown[1] and "'spare'" in unknown[1],
                  str(unknown))
    harness.check("the run defaults of a set are read", "workflow" in defaults, str(defaults))
    harness.check("the run defaults of an unknown set are 400 too", bad_defaults[0] == 400, str(bad_defaults))


def test_a_run_held_by_a_job_on_another_set_is_refused_with_the_reason():
    root = Path(tempfile.mkdtemp())
    previous = _use(root)
    try:
        items = [{"video": "/in/a.mp4", "output": "/out/a"}]
        _queue(StartJobsRequest(items=items, worker_set="second-host"))
        again_other = _refusal(lambda: _queue(StartJobsRequest(items=items, worker_set="Mac")))
        again_same = _refusal(lambda: _queue(StartJobsRequest(items=items, worker_set="second-host")))
        no_choice = _refusal(lambda: _queue(StartJobsRequest(items=items)))
    finally:
        _give_back(previous)
    harness.check("another set is 409 and says which set holds it, and what to do",
                  again_other[0] == 409 and "already queued on worker set 'second-host'" in again_other[1]
                  and "cancel that job first" in again_other[1], str(again_other))
    harness.check("the same set again and no choice are not refused", (again_same[0], no_choice[0]) == (0, 0),
                  str((again_same, no_choice)))


def test_a_run_that_is_not_running_is_given_another_set_and_one_with_a_job_is_not():
    root = Path(tempfile.mkdtemp())
    folder = root / "run"
    folder.mkdir()
    previous = _use(root)
    saved = web_api_service._resolve_folder
    web_api_service._resolve_folder = lambda root_name, name: folder
    try:
        set_pc = web_api_service.set_run_worker_set("r", "run", WorkerSetRequest(worker_set="second-host"))
        kept = run_worker_set_service.read_chosen_worker_set(folder)
        unknown = _refusal(lambda: web_api_service.set_run_worker_set("r", "run", WorkerSetRequest(worker_set="spare")))
        job_queue_service.queue_jobs(job_queue_service.QUEUE_DIR, [RunJobRequest(video=Path("/in/a.mp4"), output_root=folder)])
        held = _refusal(lambda: web_api_service.set_run_worker_set("r", "run", WorkerSetRequest(worker_set="Mac")))
    finally:
        web_api_service._resolve_folder = saved
        _give_back(previous)
    harness.check("the folder keeps the set", set_pc == {"worker_set": "second-host"} and kept == "second-host", f"{set_pc} {kept}")
    harness.check("an unknown set is 400", unknown[0] == 400, str(unknown))
    harness.check("a run with a queued job is 409", held[0] == 409 and "cancel it" in held[1], str(held))
