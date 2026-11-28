"""A run keeps its provider set: `run-provider-set.json` in the run folder holds the name the run was
started on, a resume with no flag reads it, and a name no `providerSets` entry holds refuses the run before anything is
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
from models.run_job import RunJobRequest
from services import model_capability_service
from services import run_provider_set_service
from services import transcription_service

_SETTINGS = {"hosts": {"lmstudio": {"url": "http://localhost:1234/v1"}},
             "providerSets": {"second-host": {"hosts": {"lmstudio": {"url": "http://second-host.example:1234/v1"}}},
                              "Mac": {"hosts": {"lmstudio": {"url": "http://mac.example:1234/v1"}}}}}


def _use_settings(folder: Path, values: dict) -> tuple:
    (folder / "playbook.default.json").write_text(json.dumps(values), encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
                config.PROVIDER_SET_NAME, config.settings)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME, config.PROVIDER_SET_NAME = folder, "", config.DEFAULT_PROVIDER_SET_NAME

    return previous


def _give_back(previous: tuple) -> None:
    (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
     config.PROVIDER_SET_NAME, config.settings) = previous


def test_a_run_folder_keeps_the_provider_set_and_a_resume_reads_it():
    folder = Path(tempfile.mkdtemp())
    run_folder = folder / "run"
    run_folder.mkdir()
    previous = _use_settings(folder, _SETTINGS)
    try:
        before = run_provider_set_service.read_chosen_provider_set(run_folder)
        chosen = run_provider_set_service.resolve_provider_set(run_folder, "second-host")
        run_provider_set_service.write_chosen_provider_set(run_folder, "second-host")
        file_text = (run_folder / output_contract.RUN_PROVIDER_SET_FILE).read_text(encoding="utf-8")
        resumed = run_provider_set_service.resolve_provider_set(run_folder, "")
        moved = run_provider_set_service.resolve_provider_set(run_folder, "Mac")
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
        plain = run_provider_set_service.resolve_provider_set(run_folder, "")
        explicit = run_provider_set_service.resolve_provider_set(run_folder, "default")
    finally:
        _give_back(previous)
    other = Path(tempfile.mkdtemp())
    previous = _use_settings(other, {**_SETTINGS, "defaultProviderSet": "Mac"})
    try:
        named_by_settings = run_provider_set_service.resolve_provider_set(run_folder, "")
    finally:
        _give_back(previous)
    harness.check("no flag, no file, no `defaultProviderSet`: the default set, nothing to keep", plain == ("default", False),
                  str(plain))
    harness.check("`default` asked for by name is kept", explicit == ("default", True), str(explicit))
    harness.check("the settings' `defaultProviderSet` is kept at once, so a resume does not follow a later change",
                  named_by_settings == ("Mac", True), str(named_by_settings))


def test_a_run_folder_made_before_the_rename_keeps_its_set_defect():
    # Defect: a run folder made before the rename holds `run-worker-set.json`, which the renamed reader never opened, so a
    # resume fell back to the default set. Fix: the reader opens the old file when the new one is missing.
    run_folder = Path(tempfile.mkdtemp())
    (run_folder / output_contract.RETIRED_RUN_WORKER_SET_FILE).write_text('{"name": "second-host"}', encoding="utf-8")
    old_only = run_provider_set_service.read_chosen_provider_set(run_folder)
    run_provider_set_service.write_chosen_provider_set(run_folder, "Mac")
    both = run_provider_set_service.read_chosen_provider_set(run_folder)
    harness.check("the old file's name is read", old_only == "second-host", str(old_only))
    harness.check("the new file wins once written", both == "Mac", str(both))


def test_a_queued_job_saved_with_the_old_field_keeps_its_set_defect():
    # Defect: a job saved in `ui/jobs.json` before the rename holds `worker_set`, which the renamed request refused to
    # build. Fix: `RunJobRequest.from_dict` reads the old field as `provider_set`.
    request = RunJobRequest.from_dict({"video": "inbox/Gateway-weekly.mp4", "output_root": "output/Gateway-weekly",
                                       "worker_set": "second-host"})
    harness.check("the old field becomes the provider set", request.provider_set == "second-host", str(request))
    harness.check("the request writes the new field only",
                  request.as_dict()["provider_set"] == "second-host" and "worker_set" not in request.as_dict(),
                  str(request.as_dict()))


def test_a_stored_command_with_the_old_flag_still_parses_defect():
    # Defect: a queued job's stored command holds `--worker-set NAME`, which the renamed run line refused as an unknown
    # option. Fix: `--worker-set` stays as a second spelling of `--provider-set`.
    parser = argparse.ArgumentParser()
    run_command.add_run_arguments(parser)
    required = ["--video", "Gateway-weekly.mp4", "--output", "output/Gateway-weekly"]
    old = parser.parse_args([*required, "--worker-set", "second-host"])
    new = parser.parse_args([*required, "--provider-set", "second-host"])
    harness.check("the old flag sets the provider set", old.provider_set == "second-host", str(old.provider_set))
    harness.check("the new flag sets it the same way", new.provider_set == "second-host", str(new.provider_set))


def test_an_unknown_set_refuses_the_run_and_leaves_no_folder():
    root = Path(tempfile.mkdtemp())
    settings_folder = root / "settings"
    settings_folder.mkdir()
    inbox = root / "inbox"
    inbox.mkdir()
    video = inbox / "Gateway-weekly.mp4"
    video.write_bytes(b"<an invented recording>")
    run_folder = root / "output" / "Gateway-weekly"
    logger = logging.getLogger("unknown-provider-set")
    messages: list[str] = []
    logger.error = lambda message, *args: messages.append(str(message))
    args = argparse.Namespace(target_file=video, output_root=run_folder, only_phase=None, from_phase=None, to_phase=None,
                              worker_indexes=[], is_resuming=False, is_dry_run=False, is_forced=True, is_queued=False,
                              profile_name="", tags=[], ingesting_profile="", is_run_as_audio=False, phase_models=[],
                              workflow="", speed_preset="", speed_settings=[], provider_set="spare", logger=logger)
    previous = _use_settings(settings_folder, _SETTINGS)
    saved = (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities)
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    model_capability_service.check_phase_capabilities = lambda: []
    try:
        code = run_command.cmd_run(args)
        name_in_force = config.PROVIDER_SET_NAME
    finally:
        (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities) = saved
        _give_back(previous)
    harness.check("the run is refused", code == 2, str(code))
    harness.check("the refusal names the set and the sets that exist",
                  any("'spare'" in message and "second-host" in message and "Mac" in message for message in messages), str(messages))
    harness.check("no run folder is made", not run_folder.exists())
    harness.check("the set in force is unchanged", name_in_force == "default", name_in_force)
