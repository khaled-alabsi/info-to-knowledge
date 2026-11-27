"""The model library and the worker sets that link to it: a worker set names, per job (each phase that calls a model), the
library model it uses; the models themselves are entries of `models`, added and removed one by one. The fast workflow has
no models of its own unless `video.fastProcessing` names them: it calls the set's frames processing and document writing
models. A set written the older way, overriding `models.frame` itself, is read the same as before."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

import config
from services import settings_service

_FRAME_SETTINGS = {"numCtx": 183040, "temperature": 1.0, "maxTokens": 40960, "topP": 0.95,
                   "sampling": {"top_k": 20, "presence_penalty": 1.0}}
_DOCUMENT_SETTINGS = {"numCtx": 183040, "temperature": 1.0, "maxTokens": 32768, "topP": 0.95,
                      "sampling": {"top_k": 20, "presence_penalty": 0.0}}
_PHASES = ("frames_preparing", "frames_processing", "finalize", "review", "critique_collection", "document_reshaping")
_SHIPPED = {
    "hosts": {"lmstudio": {"url": "http://localhost:1234/v1"}},
    "models": {"frame": {"spec": "lmstudio:big-model", **_FRAME_SETTINGS},
               "document": {"spec": "lmstudio:big-model", **_DOCUMENT_SETTINGS}},
}
# the older way: the set overrides the two models the phases call by default
_OLD_SET = {"machine": "Office", "models": {"frame": {"spec": "openai-compatible:gpu-model", "host": "gpu-box"},
                                            "document": {"spec": "openai-compatible:gpu-model", "host": "gpu-box"}}}
# the library way: two library models copied from them, and the set links each job to one
_LIBRARY = {"gpu-frame": {**_FRAME_SETTINGS, "spec": "openai-compatible:gpu-model", "host": "gpu-box"},
            "gpu-document": {**_DOCUMENT_SETTINGS, "spec": "openai-compatible:gpu-model", "host": "gpu-box"}}
_NEW_SET = {"machine": "Office",
            "phaseModels": {"frames_preparing": "gpu-document", "frames_processing": "gpu-frame", "finalize": "gpu-document",
                            "review": "gpu-document", "critique_collection": "gpu-document",
                            "document_reshaping": "gpu-document"}}


def _write_files(folder: Path,
                 local_values: dict) -> None:
    (folder / "playbook.default.json").write_text(json.dumps(_SHIPPED), encoding="utf-8")
    (folder / "playbook.local.json").write_text(json.dumps(local_values), encoding="utf-8")


def _use_folder(folder: Path) -> tuple:
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
                config.WORKER_SET_NAME, config.settings)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME, config.WORKER_SET_NAME = folder, "", config.DEFAULT_WORKER_SET_NAME

    return previous


def _give_back(previous: tuple) -> None:
    (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME,
     config.WORKER_SET_NAME, config.settings) = previous


def _read_models_of_set(worker_set: str) -> dict:
    """What each job of the set calls: every phase's model and the fast workflow's document model, as the settings hold it."""

    loaded = settings_service.read_next_job_settings(worker_set=worker_set)
    calls = {phase: loaded.resolve_phase_model(phase).model_dump() for phase in _PHASES}
    calls["fast document"] = loaded.resolve_model(loaded.resolve_fast_model_name(config.FINALIZE_PHASE)).model_dump()

    return calls


def test_a_set_linking_jobs_to_library_models_runs_the_same_models_as_the_older_set_defect():
    # A set overrode the settings of the two models the phases call. It now links each job to a library
    # model copied from them, and a job must call exactly the model it called before
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, {"hosts": {"gpu-box": {"url": "http://gpu-box.local:8090/v1"}}, "models": _LIBRARY,
                          "workerSets": {"Office-old": _OLD_SET, "Office": _NEW_SET}})
    previous = _use_folder(folder)
    try:
        old_calls = _read_models_of_set("Office-old")
        new_calls = _read_models_of_set("Office")
        default_calls = _read_models_of_set("default")
    finally:
        _give_back(previous)
    harness.check("every job of the linked set calls the model the older set gave it", old_calls == new_calls, str(new_calls))
    harness.check("the frame job runs on the set's host with the frame settings",
                  new_calls["frames_processing"]["host"] == "gpu-box" and new_calls["frames_processing"]["max_tokens"] == 40960,
                  str(new_calls["frames_processing"]))
    harness.check("a set links only the jobs it names: the default set still calls the shipped models",
                  default_calls["finalize"]["host"] == "" and default_calls["finalize"]["spec"] == "lmstudio:big-model",
                  str(default_calls["finalize"]))


def test_a_set_that_links_one_job_leaves_the_other_jobs_on_the_default_models():
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, {"hosts": {"gpu-box": {"url": "http://gpu-box.local:8090/v1"}}, "models": _LIBRARY,
                          "workerSets": {"frames-on-gpu": {"phaseModels": {"frames_processing": "gpu-frame"}}}})
    previous = _use_folder(folder)
    try:
        calls = _read_models_of_set("frames-on-gpu")
    finally:
        _give_back(previous)
    harness.check("the linked job runs the library model", calls["frames_processing"]["host"] == "gpu-box",
                  str(calls["frames_processing"]))
    harness.check("the others run the default models", calls["finalize"]["spec"] == "lmstudio:big-model"
                  and calls["review"]["host"] == "", str(calls["finalize"]))


def test_the_fast_workflow_calls_the_sets_own_models_unless_it_names_its_own():
    # The owner: the fast workflow is another flow on the same models, not a place to pick models. Its two model settings
    # are empty by default and then follow the set's frames processing and document writing jobs
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, {"hosts": {"gpu-box": {"url": "http://gpu-box.local:8090/v1"}}, "models": _LIBRARY,
                          "workerSets": {"Office": _NEW_SET}})
    previous = _use_folder(folder)
    try:
        on_office = settings_service.read_next_job_settings(worker_set="Office")
        on_default = settings_service.read_next_job_settings(worker_set="default")
    finally:
        _give_back(previous)
    harness.check("on the Office set, phase 3 describes with the set's frame model and phase 4 writes with its document model",
                  (on_office.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE),
                   on_office.resolve_fast_model_name(config.FINALIZE_PHASE)) == ("gpu-frame", "gpu-document"),
                  str(on_office.video.fast_processing))
    harness.check("on the default set they are the shipped ones",
                  (on_default.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE),
                   on_default.resolve_fast_model_name(config.FINALIZE_PHASE)) == ("frame", "document"), "")
    named = config.Settings.model_validate({"models": _LIBRARY, "video": {"fastProcessing": {"frameModel": "gpu-document"}}})
    harness.check("a model the fast workflow names itself still wins",
                  named.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE) == "gpu-document", "")


def test_a_set_runs_on_the_provider_of_its_frames_processing_model_unless_an_old_file_names_a_machine():
    # The Machine box is gone from the page: a set's lane in the job queue is the provider its frames processing model
    # runs on, and a `machine` an older file still holds keeps winning
    harness.check("an old file's machine wins", config.read_set_machine({"machine": "Office", "models": {}}) == "Office", "")
    harness.check("the provider of the frames processing model, by its host",
                  config.read_set_machine({"phaseModels": {"frames_processing": "a"},
                                           "models": {"a": {"spec": "openai-compatible:x", "host": "gpu-box"}}}) == "gpu-box", "")
    harness.check("else the provider named like the spec's start",
                  config.read_set_machine({"models": {"frame": {"spec": "lmstudio:x"}}}) == "lmstudio", "")
    harness.check("none when no model is known", config.read_set_machine({}) == "", "")


def test_a_model_can_list_the_jobs_it_suits_and_nothing_else_is_accepted():
    harness.check("a model suits every job until it lists some", config.ModelSettings().suits == [], "")
    listed = config.ModelSettings.model_validate({"suits": ["frames", "filtering"]})
    harness.check("the jobs are read", listed.suits == ["frames", "filtering"], str(listed.suits))
    try:
        config.ModelSettings.model_validate({"suits": ["cooking"]})
    except ValueError:
        refused = True
    else:
        refused = False
    harness.check("a job that is none of the three is refused", refused, "")


def test_a_model_added_by_the_owner_is_removed_and_a_shipped_model_is_not_defect():
    # The Settings page's Remove button for a library model had nothing to call: settings could be changed, never taken
    # out. A model the owner added goes; one the shipped file holds stays, since that file is never written
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, {"_comment": ["mine"], "models": {"mine": {"spec": "lmstudio:small"},
                                                         "frame": {"temperature": 0.5}}})
    previous = _use_folder(folder)
    try:
        removed = settings_service.remove_setting(key_parts=["models", "mine"])
        refused = settings_service.remove_setting(key_parts=["models", "frame"])
        missing = settings_service.remove_setting(key_parts=["models", "nothing"])
        note = settings_service.remove_setting(key_parts=["_comment"])
    finally:
        _give_back(previous)
    local = json.loads((folder / "playbook.local.json").read_text(encoding="utf-8"))
    harness.check("the owner's model is taken out and its other settings and notes stay",
                  removed.exit_code == 0 and "mine" not in local["models"] and local["models"]["frame"] == {"temperature": 0.5}
                  and local["_comment"] == ["mine"], f"{removed.output} {local}")
    harness.check("a shipped model is refused by its name", refused.exit_code == 2 and "models.frame" in refused.output
                  and "shipped" in refused.output, refused.output)
    harness.check("a model that is not in the file is said so", missing.exit_code == 2 and "nothing to remove" in missing.output,
                  missing.output)
    harness.check("a note is no setting", note.exit_code == 2, note.output)


def test_a_job_linked_to_a_removed_model_names_it_when_a_run_reads_the_job():
    # Removing a model that a set links leaves a set naming no model: a run on that set says so by name when it
    # reads the job, the same as a phase named after a model that is gone
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, {"models": {"mine": {"spec": "lmstudio:small"}},
                          "workerSets": {"S": {"phaseModels": {"review": "mine"}}}})
    previous = _use_folder(folder)
    try:
        settings_service.remove_setting(key_parts=["models", "mine"])
        try:
            settings_service.read_next_job_settings(worker_set="S").resolve_phase_model("review")
            refusal = "it was accepted"
        except ValueError as error:
            refusal = str(error)
    finally:
        _give_back(previous)
    harness.check("the job names the model that is gone", "phaseModels.review" in refusal and "'mine'" in refusal, refusal)
