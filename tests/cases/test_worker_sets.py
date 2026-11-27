"""Worker sets: `workerSets` in the settings files holds named parts of the settings, each laid over the
default settings, the local file and the profile; the default set is the files as they are. The owner: a set names only
what it overrides, and a bad set is refused by its name when the settings load."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

import config

_DEFAULT_FILE_VALUES = {
    "budgets": {"maxAttempts": 3},
    "hosts": {"lmstudio": {"url": "http://localhost:1234/v1"}, "ollama": {"url": "http://localhost:11434"}},
    "workerSets": {
        "_note": "a note beside the sets is dropped like every note",
        "second-host": {"_note": "a second machine",
               "hosts": {"lmstudio": {"url": "http://second-host.example:1234/v1"},
                         "second-llama": {"url": "http://second-host.example:8090/v1"}},
               "models": {"frame": {"spec": "openai-compatible:qwen3.6-35b-a3b-mtp", "host": "second-llama"}},
               "budgets": {"maxAttempts": 5}},
        "Mac": {"hosts": {"lmstudio": {"url": "http://mac.example:1234/v1"}}},
    },
}


def _write_files(folder: Path,
                 default_values: dict,
                 profile_values: dict | None = None) -> None:
    (folder / "playbook.default.json").write_text(json.dumps(default_values), encoding="utf-8")
    if profile_values is not None:
        (folder / "playbook.strict.json").write_text(json.dumps(profile_values), encoding="utf-8")


def _use_folder(folder: Path) -> tuple:
    """Point the settings files at `folder`, with no profile and the default worker set in force; returns what to give back
    to `_give_back`."""

    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
                config.WORKER_SET_NAME, config.settings)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME, config.WORKER_SET_NAME = folder, "", config.DEFAULT_WORKER_SET_NAME

    return previous


def _give_back(previous: tuple) -> None:
    (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
     config.WORKER_SET_NAME, config.settings) = previous


def _read_refusal(values: dict) -> str:
    try:
        config.Settings(**values)
    except ValueError as error:
        return str(error)

    return "it was accepted"


def test_a_worker_set_overrides_only_what_it_names():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values=_DEFAULT_FILE_VALUES)
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("second-host")
        on_pc = config.settings
        config.apply_worker_set("Mac")
        on_mac = config.settings
    finally:
        _give_back(previous)
    harness.check("the second-host set's host and model are the second host's",
                  on_pc.hosts["lmstudio"].url == "http://second-host.example:1234/v1"
                  and on_pc.find_model_host(on_pc.models["frame"]).url == "http://second-host.example:8090/v1",
                  f"{on_pc.hosts["lmstudio"].url}, {on_pc.models.get('frame')}")
    harness.check("a value the second-host set does not name comes from the default settings",
                  on_pc.hosts["ollama"].url == config.Settings().hosts["ollama"].url, on_pc.hosts["ollama"].url)
    harness.check("the Mac set overrides the host only, and takes the default settings' attempts",
                  on_mac.hosts["lmstudio"].url == "http://mac.example:1234/v1" and on_mac.budgets.max_attempts == 3
                  and "frame" not in on_mac.models,
                  f"{on_mac.hosts["lmstudio"].url}, {on_mac.budgets.max_attempts}, {sorted(on_mac.models)}")
    harness.check("a note inside a set is dropped, never read as a setting", on_pc.worker_sets["second-host"].get("_note") is None)


def test_the_default_worker_set_is_the_settings_files_as_they_are():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values=_DEFAULT_FILE_VALUES)
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("")
        in_force = config.settings
        name_in_force = config.WORKER_SET_NAME
    finally:
        _give_back(previous)
    harness.check("no name takes `defaultWorkerSet`, which is `default` unless the files say otherwise",
                  name_in_force == "default" and in_force.hosts["lmstudio"].url == "http://localhost:1234/v1"
                  and in_force.budgets.max_attempts == 3,
                  f"{name_in_force}, {in_force.hosts["lmstudio"].url}, {in_force.budgets.max_attempts}")
    harness.check("the sets stay readable beside the default one", sorted(in_force.worker_sets) == ["Mac", "second-host"],
                  str(sorted(in_force.worker_sets)))


def test_the_default_worker_set_setting_names_the_set_a_job_takes_when_none_is_chosen():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values={**_DEFAULT_FILE_VALUES, "defaultWorkerSet": "Mac"})
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("")
        name_in_force, host = config.WORKER_SET_NAME, config.settings.hosts["lmstudio"].url
    finally:
        _give_back(previous)
    harness.check("an empty name takes the set `defaultWorkerSet` names", name_in_force == "Mac"
                  and host == "http://mac.example:1234/v1", f"{name_in_force}, {host}")


def test_a_bad_worker_set_is_refused_by_its_name():
    wrong_value = _read_refusal({"workerSets": {"second-host": {"budgets": {"maxAttempts": "many"}}}})
    harness.check("a set with a wrong value is refused under its name and the key", "workerSets.second-host" in wrong_value
                  and "budgets.maxAttempts" in wrong_value, wrong_value)
    unknown_key = _read_refusal({"workerSets": {"second-host": {"noSuchSetting": 1}}})
    harness.check("a set with a key the settings do not have is refused under its name",
                  "workerSets.second-host" in unknown_key and "noSuchSetting" in unknown_key, unknown_key)
    named_default = _read_refusal({"workerSets": {"default": {"hosts": {"lmstudio": {"url": "http://x/v1"}}}}})
    harness.check("a set named `default` is refused: the default set is the files as they are",
                  "workerSets.default" in named_default, named_default)
    nested_sets = _read_refusal({"workerSets": {"second-host": {"workerSets": {"Mac": {}}}}})
    harness.check("a set holding `workerSets` is refused", "workerSets.second-host" in nested_sets and "workerSets" in nested_sets,
                  nested_sets)
    not_an_object = _read_refusal({"workerSets": {"second-host": "http://second-host.example"}})
    harness.check("a set that is not an object is refused", "workerSets.second-host" in not_an_object, not_an_object)
    unknown_default = _read_refusal({"workerSets": {"second-host": {}}, "defaultWorkerSet": "spare"})
    harness.check("`defaultWorkerSet` naming no set is refused with the sets that exist",
                  "defaultWorkerSet" in unknown_default and "second-host" in unknown_default, unknown_default)


def test_an_unknown_worker_set_is_refused_by_name_and_leaves_the_settings_in_force():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values=_DEFAULT_FILE_VALUES)
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("Mac")
        try:
            config.apply_worker_set("spare")
            refusal = "it was accepted"
        except ValueError as error:
            refusal = str(error)
        name_in_force, host = config.WORKER_SET_NAME, config.settings.hosts["lmstudio"].url
    finally:
        _give_back(previous)
    harness.check("the refusal names the set and the sets that exist", "'spare'" in refusal and "second-host" in refusal
                  and "Mac" in refusal and "default" in refusal, refusal)
    harness.check("the set in force stays", name_in_force == "Mac" and host == "http://mac.example:1234/v1",
                  f"{name_in_force}, {host}")


def test_the_worker_set_survives_a_speed_choice_and_a_profile():
    """Merge order, last wins: default settings, local file, profile, worker set, speed values."""

    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values=_DEFAULT_FILE_VALUES,
                 profile_values={"budgets": {"maxAttempts": 9},
                                 "hosts": {"lmstudio": {"url": "http://profile.example:1234/v1"}}})
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("second-host")
        config.apply_speed_values({"budgets.askWhyAfterRejection": False})
        after_speed = config.settings
        config.load_profile("strict")
        after_profile = config.settings
        config.apply_speed_values({"budgets.maxAttempts": 7})
        after_both = config.settings
        profile_in_force, name_in_force = config.PROFILE_NAME, config.WORKER_SET_NAME
    finally:
        _give_back(previous)
    harness.check("a speed choice keeps the set's host and attempts",
                  after_speed.hosts["lmstudio"].url == "http://second-host.example:1234/v1" and after_speed.budgets.max_attempts == 5,
                  f"{after_speed.hosts["lmstudio"].url}, {after_speed.budgets.max_attempts}")
    harness.check("a profile loaded after the set is under the set: the set's host and attempts win over the profile's",
                  after_profile.hosts["lmstudio"].url == "http://second-host.example:1234/v1" and after_profile.budgets.max_attempts == 5,
                  f"{after_profile.hosts["lmstudio"].url}, {after_profile.budgets.max_attempts}")
    harness.check("the speed values are on top of the set", after_both.budgets.max_attempts == 7
                  and after_both.hosts["lmstudio"].url == "http://second-host.example:1234/v1",
                  f"{after_both.budgets.max_attempts}, {after_both.hosts["lmstudio"].url}")
    harness.check("the profile and the set in force are the ones asked for",
                  profile_in_force == "strict" and name_in_force == "second-host", f"{profile_in_force}, {name_in_force}")


def test_a_profile_alone_wins_over_the_default_settings_and_the_set_is_the_default():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values=_DEFAULT_FILE_VALUES,
                 profile_values={"budgets": {"maxAttempts": 9}})
    previous = _use_folder(folder)
    try:
        config.load_profile("strict")
        attempts, name_in_force = config.settings.budgets.max_attempts, config.WORKER_SET_NAME
    finally:
        _give_back(previous)
    harness.check("with no worker set chosen a profile loads as before", attempts == 9 and name_in_force == "default",
                  f"{attempts}, {name_in_force}")


def test_the_shipped_default_file_holds_no_worker_set_and_a_set_laid_over_it_keeps_its_other_values():
    """The shipped default file is the public one: it names the keys and holds no set of a machine of its own (a person's
    sets live in `playbook.local.json`); a set laid over it changes only the values it names."""

    shipped_default = json.loads(config.DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    harness.check("the default settings file holds `workerSets` and `defaultWorkerSet`",
                  "workerSets" in shipped_default and "defaultWorkerSet" in shipped_default, f"{sorted(shipped_default)[:3]}")
    harness.check("it holds no set of its own", [name for name in shipped_default["workerSets"] if not name.startswith("_")] == [])
    built = config.read_settings_files(config.DEFAULT_CONFIG_FILE, Path(tempfile.mkdtemp()) / "none.json")
    harness.check("the file builds into settings", config.Settings(**built).default_worker_set == "default")
    host = "http://second-host.example:8090/v1"
    with_set = {**built, "workerSets": {"second-host": {"hosts": {"second-llama": {"url": host}}, "models": {
        "frame": {"spec": "openai-compatible:qwen3.6-35b-a3b-mtp", "host": "second-llama"},
        "document": {"spec": "openai-compatible:qwen3.6-35b-a3b-mtp", "host": "second-llama"}}}}}
    on_second = config.Settings(**config.merge_worker_set(with_set, "second-host"))
    plain = config.Settings(**built)
    harness.check("a set points the frame and document models at its host, other values kept",
                  all(on_second.models[name].spec == "openai-compatible:qwen3.6-35b-a3b-mtp"
                      and on_second.find_model_host(on_second.models[name]).url == host
                      and on_second.models[name].num_ctx == plain.models[name].num_ctx for name in ("frame", "document")),
                  str({name: (on_second.models[name].spec, on_second.models[name].host) for name in ("frame", "document")}))
    harness.check("the set's host is added beside the shipped hosts",
                  sorted(on_second.hosts) == sorted([*plain.hosts, "second-llama"]), str(sorted(on_second.hosts)))


def test_the_hosts_a_run_calls_are_the_sets():
    from services import chat_model_service
    from services import settings_service

    folder = Path(tempfile.mkdtemp())
    _write_files(folder=folder, default_values={**_DEFAULT_FILE_VALUES, "models": {
        "frame": {"spec": "lmstudio:qwen3.6-35b-a3b-mtp"}}})
    previous = _use_folder(folder)
    try:
        config.apply_worker_set("second-host")
        pc_model = config.settings.resolve_model("frame")
        on_pc = chat_model_service.resolve_host_url(pc_model)
        config.apply_worker_set("Mac")
        on_mac = chat_model_service.resolve_host_url(config.settings.resolve_model("frame"))
        next_on_mac = settings_service.read_next_job_settings(worker_set="Mac").hosts["lmstudio"].url
        next_default = settings_service.read_next_job_settings().hosts["lmstudio"].url
        try:
            settings_service.read_next_job_settings(worker_set="spare")
            refusal = "it was accepted"
        except ValueError as error:
            refusal = str(error)
    finally:
        _give_back(previous)
    harness.check("the second-host set's model call goes to the second host's address (the host its `frame` model names)",
                  on_pc == "http://second-host.example:8090/v1", str(on_pc))
    harness.check("the Mac set's model call goes to the Mac's LM Studio", on_mac == "http://mac.example:1234/v1", str(on_mac))
    harness.check("the next job's settings on a set are the set's, and the default set's are the files'",
                  next_on_mac == "http://mac.example:1234/v1" and next_default == "http://localhost:1234/v1",
                  f"{next_on_mac}, {next_default}")
    harness.check("an unknown set is refused with the sets that exist", "'spare'" in refusal and "second-host" in refusal, refusal)
