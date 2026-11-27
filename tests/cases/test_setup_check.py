"""The setup wizard's check and its saving: a run needs the one model host its models name, so a
host no model uses is not needed, installed or not; a needed host this machine lacks is needed and not installed; an
API counts as installed when its key is set; a model is checked against its host's list, unknown while the host does
not answer; no configured model is its own state; and the wizard's choice of host and model is written into both
default settings files, with an API key in `.env` only.

Programs are found by a fake (`set_program_finder`), hosts listed by a fake (`set_host_lister`) and the settings read
from a copy (`set_settings_reader`), so the suite runs with no network and none of this machine's programs.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from cli import mlx_setup_command
from models.setup_runtime_vocabulary import SYSTEMS
from services import host_model_service
from services import mlx_setup_service
from services import setup_check_service
from services import text_detection_service
from services import web_api_service


def _check_with(programs: set[str],
                hosts: dict[str, list[str] | None],
                specs: dict[str, str]) -> dict:
    """`check_setup` with these programs on the PATH, these hosts' model lists by provider, these models configured
    (name -> spec), the redundancy filter off and the knowledge filter on with a classifier command that does not
    exist."""

    settings = config.settings.model_copy(deep=True)
    settings.models = {name: config.ModelSettings(spec=spec) for name, spec in specs.items()}
    settings.phase_models = {}
    settings.frames.is_redundancy_filter_on, settings.frames.is_knowledge_filter_on = False, True
    settings.frames.knowledge_classifier_command = "/no/such/classifier"
    addresses = {settings.hosts["lmstudio"].url: "lmstudio", settings.hosts["ollama"].url: "ollama"}
    setup_check_service.set_settings_reader(lambda: settings)
    setup_check_service.set_program_finder(lambda name: f"/bin/{name}" if name in programs else None)
    setup_check_service.set_host_lister(lambda provider, base_url: hosts.get(addresses.get(base_url, provider)))
    try:
        return setup_check_service.check_setup()
    finally:
        setup_check_service.set_settings_reader(setup_check_service.settings_service.read_next_job_settings)
        setup_check_service.set_program_finder(setup_check_service.shutil.which)
        setup_check_service.set_host_lister(host_model_service.list_host_models)


def _find(rows: list[dict],
          field: str,
          wanted: str) -> dict:
    return next(row for row in rows if row[field] == wanted)


def test_the_text_reader_counts_as_installed_only_with_every_language_it_is_set_to_read():
    """Tesseract reads the frames in `frames.textReaderLanguages` (English and German by default); with a language's
    model missing, the setup check says so instead of reading without it unseen."""

    previous = text_detection_service.LANGUAGE_LISTER
    try:
        for installed, expected in ((("eng", "osd"), False), (("deu", "eng", "osd"), True)):
            text_detection_service.set_language_lister(lambda installed=installed: installed)
            reader = _find(_check_with({"ffmpeg", "ffprobe", "tesseract"}, {}, {})["runtimes"], "key", "tesseract")
            harness.check(f"installed {installed}: counted installed {expected}, and needed",
                          reader["is_installed"] is expected and reader["is_needed"], str(reader))
        reader = _find(_check_with({"ffmpeg", "ffprobe"}, {}, {})["runtimes"], "key", "tesseract")
        harness.check("no tesseract program: not installed", reader["is_installed"] is False, str(reader))
    finally:
        text_detection_service.set_language_lister(previous)


def test_the_reader_reads_english_and_the_recording_s_own_language_when_the_settings_list_it():
    """Read in English and German on an English lecture, the German model read three words of a slide differently on
    two frames, and a frame was kept as new: a second language is read only where it is spoken."""

    previous_lister, previous_languages = text_detection_service.LANGUAGE_LISTER, config.settings.frames.text_reader_languages
    text_detection_service.set_language_lister(lambda: ("deu", "eng", "fra"))
    config.settings.frames.text_reader_languages = ["eng", "deu"]
    try:
        chosen = {}
        for heard in ("en", "de", "fr", ""):
            text_detection_service.set_recording_language(heard)
            chosen[heard] = text_detection_service._select_languages()
    finally:
        text_detection_service.set_recording_language("")
        text_detection_service.set_language_lister(previous_lister)
        config.settings.frames.text_reader_languages = previous_languages
    harness.check("English alone for an English or unknown recording, with German for a German one, never a language "
                  "the settings do not list", chosen == {"en": "eng", "de": "eng+deu", "fr": "eng", "": "eng"}, str(chosen))


def test_only_the_host_the_models_name_is_needed():
    on_ollama = _check_with(set(), {"ollama": None}, {"frame": "ollama:qwen3-vl:8b", "document": "ollama:qwen3-vl:8b"})
    ollama = _find(on_ollama["runtimes"], "key", "ollama")
    harness.check("a host the models name and this machine lacks is needed, not installed and not running",
                  ollama["is_needed"] and not ollama["is_installed"] and ollama["is_running"] is False, str(ollama))
    harness.check("then no other host and no API is needed",
                  not any(row["is_needed"] for row in on_ollama["runtimes"] if row["kind"] in ("host", "api")
                          and row["key"] != "ollama"), str([(row["key"], row["is_needed"]) for row in on_ollama["runtimes"]]))
    harness.check("ffmpeg is needed whatever the host",
                  _find(on_ollama["runtimes"], "key", "ffmpeg")["is_needed"])
    classifier = _find(on_ollama["runtimes"], "key", "classifier")
    harness.check("the classifier is needed while the knowledge filter is on, and its command is missing",
                  classifier["is_needed"] and not classifier["is_installed"], str(classifier))
    harness.check("every runtime carries steps for macOS, Windows and Linux",
                  all(set(row["steps"]) == set(SYSTEMS) == {"mac", "windows", "linux"} for row in on_ollama["runtimes"]))

    on_lmstudio = _check_with({"ollama", "ffmpeg", "ffprobe"}, {"ollama": ["qwen3-vl:8b", "nomic-embed-text"], "lmstudio": []},
                              {"frame": "lmstudio:lfm2.5-vl-3b", "document": "lmstudio:lfm2.5-vl-3b"})
    ollama = _find(on_lmstudio["runtimes"], "key", "ollama")
    harness.check("a host installed and running that no model names is not needed",
                  not ollama["is_needed"] and ollama["is_installed"] and ollama["is_running"], str(ollama))
    harness.check("the host the models name is needed", _find(on_lmstudio["runtimes"], "key", "lmstudio")["is_needed"])
    harness.check("a running host offers the models it has", ollama["available_models"] == ["qwen3-vl:8b"], str(ollama))


def test_the_d1_knowledge_filter_is_needed_only_when_chosen():
    """The d1-2B tool (tools/d1-knowledge-filter/) is needed while frames.knowledgeFilterModel names it, never with the
    default classifier."""

    default = _find(_check_with({"ffmpeg", "ffprobe"}, {}, {})["runtimes"], "key", "d1-knowledge-filter")
    harness.check("with google-SigLIP-2, the default, it is not needed", not default["is_needed"], str(default))
    previous = config.settings.frames.knowledge_filter_model
    config.settings.frames.knowledge_filter_model = "liquidai-d1-2B"
    try:
        chosen = _find(_check_with({"ffmpeg", "ffprobe"}, {}, {})["runtimes"], "key", "d1-knowledge-filter")
    finally:
        config.settings.frames.knowledge_filter_model = previous
    harness.check("with liquidai-d1-2B it is needed, with the steps to build it",
                  chosen["is_needed"] and "setup.sh --download" in " ".join(chosen["steps"]["mac"]), str(chosen))


def test_the_scanned_page_reader_names_its_model_for_the_models_table_defect():
    # The Tools step said the reader was missing and to press Download on the Models step, whose table did not list it.
    # Its tool row now names its model, which the Models step lists as a row with its state and its own Download
    runtimes = _check_with({"ffmpeg", "ffprobe"}, {}, {})["runtimes"]
    reader = _find(runtimes, "key", "unlimited-ocr")
    harness.check("the reader names the model it runs, and no other tool names one",
                  reader["model_id"] == config.settings.pdf.unlimited_ocr_model
                  and all(runtime["model_id"] == "" for runtime in runtimes if runtime["key"] != "unlimited-ocr"),
                  str([(runtime["key"], runtime["model_id"]) for runtime in runtimes]))


def test_an_api_counts_as_installed_when_its_key_is_set():
    specs = {"frame": "anthropic:claude-test", "document": "anthropic:claude-test"}
    previous = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        without = _find(_check_with(set(), {}, specs)["runtimes"], "key", "anthropic")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        with_key = _find(_check_with(set(), {}, specs)["runtimes"], "key", "anthropic")
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if previous is not None:
            os.environ["ANTHROPIC_API_KEY"] = previous
    harness.check("an API the models name with no key is needed and not set up",
                  without["is_needed"] and not without["is_installed"] and without["is_running"] is None, str(without))
    harness.check("with its key in the environment it is set up", with_key["is_installed"], str(with_key))


def test_a_model_is_checked_against_its_hosts_list_and_unknown_while_the_host_is_down():
    specs = {"frame": "lmstudio:lfm2.5-vl-3b", "document": "lmstudio:other-model"}
    served = _check_with(set(), {"lmstudio": ["lfm2.5-vl-3b"]}, specs)
    frame, document = _find(served["models"], "name", "frame"), _find(served["models"], "name", "document")
    harness.check("a model the host lists is on the host", frame["is_on_host"] is True, str(frame))
    harness.check("a model the host does not list is not on it", document["is_on_host"] is False, str(document))
    harness.check("each model names the phases that call it",
                  frame["used_by"] == ["frames_processing"] and "finalize" in document["used_by"], str(served["models"]))
    harness.check("a recommended model names its Hugging Face repository",
                  frame["recommended_repository"] == "LiquidAI/LFM2.5-VL-3B-GGUF"
                  and document["recommended_repository"] == "", str(served["models"]))

    down = _check_with(set(), {"lmstudio": None}, specs)
    harness.check("while the host does not answer, whether it has the model is unknown",
                  all(row["is_on_host"] is None for row in down["models"]), str(down["models"]))


def test_no_configured_model_is_its_own_state():
    empty = _check_with(set(), {}, {})
    harness.check("with no model configured, has_models is false", empty["has_models"] is False)
    harness.check("each model a phase calls reads as not configured",
                  all(not row["is_configured"] and row["spec"] == "" for row in empty["models"]), str(empty["models"]))
    harness.check("then no host is needed yet",
                  not any(row["is_needed"] for row in empty["runtimes"] if row["kind"] in ("host", "api")))


def test_the_chosen_host_and_model_are_saved_in_both_default_settings_files_and_the_key_in_env_only():
    folder = Path(tempfile.mkdtemp())
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text("{}\n", encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, setup_check_service.ENV_FILE,
                os.environ.get("OPENAI_API_KEY"))
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    setup_check_service.ENV_FILE = folder / ".env"
    try:
        saved = setup_check_service.configure_models("openai", "gpt-test", "", "sk-test")
        unknown = setup_check_service.configure_models("nowhere", "x", "", "")
        no_address = setup_check_service.configure_models("openai-compatible", "x", "", "")
        lmstudio = setup_check_service.configure_models("lmstudio", "", "", "")
        files = [json.loads((folder / name).read_text(encoding="utf-8"))
                 for name in ("playbook.default.json", "playbook.local.json")]
        env_text = (folder / ".env").read_text(encoding="utf-8")
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, setup_check_service.ENV_FILE = previous[:3]
        os.environ.pop("OPENAI_API_KEY", None)
        if previous[3] is not None:
            os.environ["OPENAI_API_KEY"] = previous[3]
    harness.check("the choice is saved", saved.exit_code == 0 and lmstudio.exit_code == 0,
                  str(saved.output + lmstudio.output))
    harness.check("an unknown host and a server with no address are refused by name",
                  unknown.exit_code == 2 and no_address.exit_code == 2, str(unknown.output + no_address.output))
    harness.check("the owner's own settings file names the recommended model for every phase after LM Studio was chosen",
                  all(files[1]["models"][name]["spec"] == "lmstudio:qwen3.6-35b-a3b-mtp"
                      for name in ("frame", "document")), str(files))
    harness.check("the key is in .env and in no settings file",
                  "OPENAI_API_KEY=sk-test" in env_text and all("sk-test" not in json.dumps(values) for values in files),
                  env_text)


def test_a_download_whose_process_ended_is_not_running():
    folder = Path(tempfile.mkdtemp())
    previous = (setup_check_service.DOWNLOAD_STATE_PATH, setup_check_service.DOWNLOAD_LOG_PATH)
    setup_check_service.DOWNLOAD_STATE_PATH = folder / "setup-models.json"
    setup_check_service.DOWNLOAD_LOG_PATH = folder / "setup-models.log"
    # a pid no process holds: above the largest pid macOS and Linux hand out
    setup_check_service.DOWNLOAD_STATE_PATH.write_text(json.dumps({"pid": 4_000_000}), encoding="utf-8")
    setup_check_service.DOWNLOAD_LOG_PATH.write_text("objc[1]: Class AVFFrameReceiver is implemented in both\ndownloading x\nalready downloaded: y\n", encoding="utf-8")
    try:
        download = _check_with(set(), {}, {})["download"]
    finally:
        setup_check_service.DOWNLOAD_STATE_PATH, setup_check_service.DOWNLOAD_LOG_PATH = previous
    harness.check("an ended download is not running, and its log is shown",
                  download["is_running"] is False and "already downloaded: y" in download["log"]
                  and "objc[" not in download["log"], str(download))


def _check_in_a_container(programs: set[str],
                          hosts: dict[str, list[str] | None],
                          specs: dict[str, str]) -> dict:
    """`_check_with` while the server runs in a container (`I2K_CONTAINER`)."""

    previous = config.IS_CONTAINER_MODE
    config.IS_CONTAINER_MODE = True
    try:
        return _check_with(programs, hosts, specs)
    finally:
        config.IS_CONTAINER_MODE = previous


def test_a_container_check_lists_no_setup_and_names_each_missing_provider():
    specs = {"frame": "lmstudio:lfm2.5-vl-3b", "document": "lmstudio:other-model"}
    served = _check_in_a_container(set(), {"lmstudio": ["lfm2.5-vl-3b"]}, specs)
    harness.check("the page is told it runs in a container, with no runtime to set up and nothing to download",
                  served["is_container"] is True and served["runtimes"] == [] and served["recommended"] == []
                  and served["download"]["is_running"] is False, str(served))
    harness.check("a model the host does not have is named with its role's phases and the reason",
                  [row["name"] for row in served["missing_providers"]] == ["document"]
                  and "finalize" in served["missing_providers"][0]["used_by"]
                  and "does not have the model other-model" in served["missing_providers"][0]["reason"],
                  str(served["missing_providers"]))
    harness.check("the models are still listed with their state", _find(served["models"], "name", "frame")["is_on_host"] is True)

    down = _check_in_a_container(set(), {"lmstudio": None}, specs)
    harness.check("a host that does not answer is named for every model it serves",
                  sorted(row["name"] for row in down["missing_providers"]) == ["document", "frame"]
                  and all("does not answer" in row["reason"] for row in down["missing_providers"]),
                  str(down["missing_providers"]))

    empty = _check_in_a_container(set(), {}, {})
    harness.check("no model set for a role is named, with where to add it",
                  sorted(row["name"] for row in empty["missing_providers"]) == ["document", "frame"]
                  and all("add models." in row["reason"] for row in empty["missing_providers"]),
                  str(empty["missing_providers"]))

    ready = _check_in_a_container(set(), {"lmstudio": ["m"]}, {"frame": "lmstudio:m", "document": "lmstudio:m"})
    harness.check("every provider set up and answering: nothing is missing", ready["missing_providers"] == [],
                  str(ready["missing_providers"]))


def test_a_container_names_an_api_with_no_key_and_accepts_it_with_one():
    specs = {"frame": "anthropic:claude-test", "document": "anthropic:claude-test"}
    previous = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        without = _check_in_a_container(set(), {}, specs)["missing_providers"]
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        with_key = _check_in_a_container(set(), {}, specs)["missing_providers"]
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if previous is not None:
            os.environ["ANTHROPIC_API_KEY"] = previous
    harness.check("an API with no key is named with the variable to set",
                  len(without) == 2 and all("ANTHROPIC_API_KEY" in row["reason"] for row in without), str(without))
    harness.check("with its key set nothing is missing", with_key == [], str(with_key))


def test_outside_a_container_the_check_is_the_full_wizard_check():
    check = _check_with({"ffmpeg", "ffprobe"}, {"lmstudio": None}, {"frame": "lmstudio:m", "document": "lmstudio:m"})
    harness.check("the runtimes and the recommended models are listed, and the page is not told it is in a container",
                  check["is_container"] is False and len(check["runtimes"]) > 0 and len(check["recommended"]) > 0,
                  str(check["is_container"]))


def test_a_container_refuses_the_models_download_the_mlx_setup_and_a_run_with_no_provider():
    started_downloads, started_installs = [], []
    previous = (config.IS_CONTAINER_MODE, setup_check_service.start_models_download, mlx_setup_service.set_up_mlx)
    setup_check_service.start_models_download = lambda token: started_downloads.append(token) or 1
    mlx_setup_service.set_up_mlx = lambda *arguments, **keywords: started_installs.append(arguments) or 0
    settings = config.settings.model_copy(deep=True)
    settings.models, settings.phase_models = {}, {}
    setup_check_service.set_settings_reader(lambda: settings)
    setup_check_service.set_host_lister(lambda provider, base_url: None)
    config.IS_CONTAINER_MODE = True
    try:
        client = TestClient(web_api_service.build_app())
        download = client.post("/api/setup/models", json={"hf_token": ""})
        queued = client.post("/api/jobs", json={"items": [{"video": "/in/a.mp3", "output": "/out/a"}]})
        mlx_exit_code = mlx_setup_command.cmd_setup_mlx(argparse.Namespace(model=None, venv_dir=Path("~/no-venv")))
    finally:
        config.IS_CONTAINER_MODE = previous[0]
        setup_check_service.start_models_download, mlx_setup_service.set_up_mlx = previous[1], previous[2]
        setup_check_service.set_settings_reader(setup_check_service.settings_service.read_next_job_settings)
        setup_check_service.set_host_lister(host_model_service.list_host_models)
    harness.check("the page's models download is refused, by the container, and starts nothing",
                  download.status_code == 409 and "container" in download.text and started_downloads == [], download.text)
    harness.check("a run with no model provider is refused, naming the role and where to set it",
                  queued.status_code == 409 and "frame" in queued.text and "document" in queued.text
                  and "add models." in queued.text, queued.text)
    harness.check("the Mac's mlx setup is refused with exit code 2 and installs nothing",
                  mlx_exit_code == 2 and started_installs == [], str(mlx_exit_code))
