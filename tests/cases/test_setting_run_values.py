"""The Settings page's lines for a setting with no value of its own: each says what a run uses instead (`resolved`, and
`resolved_note` in a person's words when that is no value), so no line reads "(not set)" or an empty "default:"."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

import config
from services import host_model_service
from services import setting_run_value_service
from services import settings_service


def _list_no_models(provider: str,
                    base_url: str) -> list[str] | None:
    return None


def _write_files(folder: Path,
                 values: dict) -> None:
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text(json.dumps(values), encoding="utf-8")


def _list_rows(values: dict) -> dict:
    folder = Path(tempfile.mkdtemp())
    _write_files(folder, values)
    previous = config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = folder / "playbook.default.json", folder / "playbook.local.json", folder
    settings_service.set_host_lister(_list_no_models)
    try:
        rows = {row["key"]: row for row in settings_service.list_settings()}
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
        settings_service.set_host_lister(host_model_service.list_host_models)

    return rows


_VALUES = {"hosts": {"lmstudio": {"url": "http://localhost:1234/v1"},
                     "cloud": {"url": "http://cloud.example/v1", "apiKeyEnv": "CLOUD_KEY"}},
           "models": {"frame": {"spec": "lmstudio:big-model"},
                      "remote": {"spec": "openai-compatible:big-model", "host": "cloud"},
                      "hosted": {"spec": "openai:gpt-x"}}}


def test_a_model_with_no_host_shows_the_host_its_provider_names_defect():
    # A model's `host` row showed "(not set)" and an empty "default:" while a run used the host named like the model's
    # provider. The row now carries that host as `resolved`, with a note saying where it comes from
    rows = _list_rows(_VALUES)
    host = rows["models.frame.host"]
    harness.check("the host a run uses is the provider's, with its note",
                  (host["resolved"], host["resolved_note"]) == ("lmstudio", "from the model's provider"), str(host))
    harness.check("a model with a host of its own carries nothing to resolve", "resolved" not in rows["models.remote.host"],
                  str(rows["models.remote.host"]))
    harness.check("the empty entry of the list of hosts says what a run uses",
                  host["choice_options"][0]["label"] == "default: lmstudio (from the model's provider)",
                  str(host["choice_options"][0]))
    harness.check("a model called at OpenAI's service says so, never a host",
                  (rows["models.hosted.host"]["resolved"], rows["models.hosted.host"]["resolved_note"])
                  == (None, "the model is called at OpenAI's own service"), str(rows["models.hosted.host"]))


def test_a_setting_a_run_turns_into_no_value_says_what_happens_defect():
    # A language, a speaker limit, a prompt addition and a model's key showed "(not set)" while a run found, sent or
    # added something. Each row now carries a short note in plain words saying so
    rows = _list_rows(_VALUES)
    notes = {key: rows[key].get("resolved_note") for key in ("audio.language", "audio.diarization.minSpeakers",
                                                              "prompt.userPrompt", "models.frame.apiKey",
                                                              "models.frame.topP", "providers.speech.host")}
    harness.check("the language is found from the recording", notes["audio.language"] == "found from the recording", str(notes))
    harness.check("a speaker limit says there is none and the recording decides",
                  notes["audio.diarization.minSpeakers"] == "no lower limit: found from the recording", str(notes))
    harness.check("an empty prompt addition says nothing is added",
                  notes["prompt.userPrompt"] == "nothing is added to a frame's message", str(notes))
    harness.check("a key a host does not need says a placeholder is sent",
                  notes["models.frame.apiKey"] == "no key is needed: a placeholder is sent", str(notes))
    harness.check("a value a run does not send says the host uses its own",
                  notes["models.frame.topP"] == "not sent: the model host uses its own value", str(notes))
    harness.check("a provider's empty host says where the work runs",
                  notes["providers.speech.host"] == "speech to text runs on this machine", str(notes))


def test_a_model_names_the_key_variable_its_host_reads_defect():
    # A model on a host that names a key variable showed "(not set)" for its key, while a run read that variable. The
    # row now carries the variable's name
    rows = _list_rows(_VALUES)
    harness.check("the variable named by the host", (rows["models.remote.apiKey"]["resolved"],
                                                       rows["models.remote.apiKey"]["resolved_note"])
                  == ("CLOUD_KEY", "the key's environment variable, named by its host"), str(rows["models.remote.apiKey"]))
    harness.check("OpenAI's own key variable for an OpenAI model", rows["models.hosted.apiKey"]["resolved"] == "OPENAI_API_KEY",
                  str(rows["models.hosted.apiKey"]))


def test_an_empty_choice_names_the_value_a_run_uses_defect():
    # A phase's model, the similarity model and a model's structured method offered an empty entry labelled "(empty)" or a
    # bare "default:". Each now names what a run uses
    rows = _list_rows(_VALUES)
    harness.check("a phase with no model of its own names its default",
                  (rows["phaseModels.frames_processing"]["resolved"], rows["phaseModels.frames_processing"]["resolved_note"])
                  == ("frame", "the phase's own model"), str(rows["phaseModels.frames_processing"]))
    similarity = rows["frames.similarityModel"]
    harness.check("the similarity model is the redundancy model, and its list says so",
                  (similarity["resolved"], similarity["choice_options"][0]["label"])
                  == (config.Settings().frames.redundancy_model,
                      f"default: {config.Settings().frames.redundancy_model} (the redundancy filter's model)"), str(similarity))
    method = rows["models.frame.structuredMethod"]
    harness.check("the structured method is the provider's", (method["resolved"], method["resolved_note"])
                  == ("function_calling", "from the model's provider"), str(method))
    harness.check("the speech model is the audio setting's",
                  rows["providers.speech.model"]["resolved"] == config.Settings().audio.whisper_model,
                  str(rows["providers.speech.model"]))


def test_a_row_with_a_value_of_its_own_gets_no_run_value():
    folder_rows = _list_rows({**_VALUES, "audio": {"language": "de"}})
    harness.check("a language that is set carries nothing to resolve", "resolved" not in folder_rows["audio.language"],
                  str(folder_rows["audio.language"]))


def test_a_run_value_is_worded_with_its_origin_or_alone():
    said = setting_run_value_service.describe_run_value
    result = setting_run_value_service.SettingRunValueResult
    harness.check("a value is followed by its origin", said(result(used_value="lmstudio", note="from the model's provider"))
                  == "lmstudio (from the model's provider)")
    harness.check("no value leaves the note alone", said(result(used_value=None, note="found from the recording"))
                  == "found from the recording")
