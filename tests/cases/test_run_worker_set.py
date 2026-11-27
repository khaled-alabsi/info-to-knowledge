"""A run keeps its worker set: `run-worker-set.json` in the run folder holds the name the run was
started on, a resume with no flag reads it, and a name no `workerSets` entry holds refuses the run before anything is
written. Made data: a made settings folder with two sets, an invented recording."""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path

from support import harness

import config
from cli import run_command
from models import output_contract
from services import model_capability_service
from services import run_worker_set_service
from services import transcription_service

_SETTINGS = {"lmstudioBaseUrl": "http://localhost:1234/v1",
             "workerSets": {"second-host": {"lmstudioBaseUrl": "http://second-host.example:1234/v1"},
                            "Mac": {"lmstudioBaseUrl": "http://mac.example:1234/v1"}}}


def _use_settings(folder: Path, values: dict) -> tuple:
    (folder / "playbook.default.json").write_text(json.dumps(values), encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
                config.WORKER_SET_NAME, config.settings)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME, config.WORKER_SET_NAME = folder, "", config.DEFAULT_WORKER_SET_NAME

    return previous


def _give_back(previous: tuple) -> None:
    (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
     config.WORKER_SET_NAME, config.settings) = previous


def test_a_run_folder_keeps_the_worker_set_and_a_resume_reads_it():
    folder = Path(tempfile.mkdtemp())
    run_folder = folder / "run"
    run_folder.mkdir()
    previous = _use_settings(folder, _SETTINGS)
    try:
        before = run_worker_set_service.read_chosen_worker_set(run_folder)
        chosen = run_worker_set_service.resolve_worker_set(run_folder, "second-host")
        run_worker_set_service.write_chosen_worker_set(run_folder, "second-host")
        file_text = (run_folder / output_contract.RUN_WORKER_SET_FILE).read_text(encoding="utf-8")
        resumed = run_worker_set_service.resolve_worker_set(run_folder, "")
        moved = run_worker_set_service.resolve_worker_set(run_folder, "Mac")
    finally:
        _give_back(previous)
    harness.check("a new folder keeps no set", before is None, str(before))
    harness.check("a named set is to be kept", chosen == ("second-host", True), str(chosen))
    harness.check("the file holds the name", json.loads(file_text) == {"name": "second-host"}, file_text)
    harness.check("a resume with no flag takes the kept set and writes nothing", resumed == ("second-host", False), str(resumed))
    harness.check("another name given to a run that has one replaces it", moved == ("Mac", True), str(moved))


def test_a_run_with_no_choice_is_on_the_default_set_and_keeps_none_unless_the_settings_name_another():
    folder = Path(tempfile.mkdtemp())
    run_folder = folder / "run"
    run_folder.mkdir()
    previous = _use_settings(folder, _SETTINGS)
    try:
        plain = run_worker_set_service.resolve_worker_set(run_folder, "")
        explicit = run_worker_set_service.resolve_worker_set(run_folder, "default")
    finally:
        _give_back(previous)
    other = Path(tempfile.mkdtemp())
    previous = _use_settings(other, {**_SETTINGS, "defaultWorkerSet": "Mac"})
    try:
        named_by_settings = run_worker_set_service.resolve_worker_set(run_folder, "")
    finally:
        _give_back(previous)
    harness.check("no flag, no file, no `defaultWorkerSet`: the default set, nothing to keep", plain == ("default", False),
                  str(plain))
    harness.check("`default` asked for by name is kept", explicit == ("default", True), str(explicit))
    harness.check("the settings' `defaultWorkerSet` is kept at once, so a resume does not follow a later change",
                  named_by_settings == ("Mac", True), str(named_by_settings))


def test_an_unknown_set_refuses_the_run_and_leaves_no_folder():
    root = Path(tempfile.mkdtemp())
    settings_folder = root / "settings"
    settings_folder.mkdir()
    inbox = root / "inbox"
    inbox.mkdir()
    video = inbox / "Gateway-weekly.mp4"
    video.write_bytes(b"<an invented recording>")
    run_folder = root / "output" / "Gateway-weekly"
    logger = logging.getLogger("unknown-worker-set")
    messages: list[str] = []
    logger.error = lambda message, *args: messages.append(str(message))
    args = argparse.Namespace(target_file=video, output_root=run_folder, only_phase=None, from_phase=None, to_phase=None,
                              worker_indexes=[], is_resuming=False, is_dry_run=False, is_forced=True, is_queued=False,
                              profile_name="", tags=[], ingesting_profile="", is_run_as_audio=False, phase_models=[],
                              workflow="", speed_preset="", speed_settings=[], worker_set="spare", logger=logger)
    previous = _use_settings(settings_folder, _SETTINGS)
    saved = (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities)
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    model_capability_service.check_phase_capabilities = lambda: []
    try:
        code = run_command.cmd_run(args)
        name_in_force = config.WORKER_SET_NAME
    finally:
        (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities) = saved
        _give_back(previous)
    harness.check("the run is refused", code == 2, str(code))
    harness.check("the refusal names the set and the sets that exist",
                  any("'spare'" in message and "second-host" in message and "Mac" in message for message in messages), str(messages))
    harness.check("no run folder is made", not run_folder.exists())
    harness.check("the set in force is unchanged", name_in_force == "default", name_in_force)
