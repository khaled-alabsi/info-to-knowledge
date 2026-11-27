"""What the setup wizard shows (`GET /api/setup`), what it saves when no model is configured
(`POST /api/setup/configure`), and the models download (`POST /api/setup/models`, `i2k setup-models` in a process of
its own session).

A run needs one model host, whichever its models' specs name, and the tools. Each one is reported with the facts found,
never folded into one guessed state: `is_needed` (a model a run calls uses it, or for a tool a setting does),
`is_installed` (found on this machine; for an API, a key is set) and, for a local host, `is_running` (its server
answers). A host no model uses is not needed, installed or not. The settings are read from the files as a job started
now would read them, so a model the wizard just saved is checked at once.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import config
from models.operation_result import OperationResult
from models.recommended_model_vocabulary import RECOMMENDED_MODELS
from models.setup_runtime_vocabulary import (API_KIND, HOST_KIND, PROVIDER_GUIDE_KEYS, RUNTIME_SPECS, SYSTEMS,
                                             TOOL_KIND)
from services import host_model_service
from services import knowledge_filter_service
from services import mlx_setup_service
from services import model_setup_service
from services import settings_service
from services import text_detection_service
from services import unlimited_ocr_reader_service

__all__ = ["check_setup", "configure_models", "start_models_download", "set_program_finder", "set_host_lister",
           "set_settings_reader", "describe_container_refusal", "list_missing_providers", "DOWNLOAD_LOG_PATH",
           "DOWNLOAD_STATE_PATH", "ENV_FILE"]

DOWNLOAD_LOG_PATH = config.UI_DIR / "setup-models.log"
DOWNLOAD_STATE_PATH = config.UI_DIR / "setup-models.json"
# The git-ignored file `i2k` exports before every command: an API key the wizard is given goes there, never into a
# settings file, which is in git
ENV_FILE = config.PLAYBOOK_ROOT / ".env"
LMSTUDIO_APP_PATH = Path("/Applications/LM Studio.app")
LOG_TAIL_CHARS = 4000
# The models every phase calls unless `phaseModels` names others: what the wizard configures
CONFIGURED_MODEL_NAMES = ("frame", "document")

# What finds a program on this machine, what lists a host's models, and what reads the settings. Module-level names
# with setters: the suite runs with no network, none of this machine's programs and its own settings.
PROGRAM_FINDER: Callable[[str], str | None] = shutil.which
HOST_LISTER: Callable[[str, str], list[str] | None] = host_model_service.list_host_models
SETTINGS_READER: Callable[[], config.Settings] = settings_service.read_next_job_settings


def set_program_finder(finder: Callable[[str], str | None]) -> None:
    global PROGRAM_FINDER
    PROGRAM_FINDER = finder


def set_host_lister(lister: Callable[[str, str], list[str] | None]) -> None:
    global HOST_LISTER
    HOST_LISTER = lister


def set_settings_reader(reader: Callable[[], config.Settings]) -> None:
    global SETTINGS_READER
    SETTINGS_READER = reader


def _list_called_models(settings: config.Settings) -> dict[str, list[str]]:
    """Each model name a run calls, with the phases (and the redundancy filter) that call it."""

    called: dict[str, list[str]] = {}
    for phase in config.PHASE_DEFAULT_MODELS:
        called.setdefault(settings.select_phase_model_name(phase), []).append(phase)
    if settings.frames.is_redundancy_filter_on:
        called.setdefault(settings.frames.redundancy_model, []).append("redundancy filter")
    if settings.frames.is_similarity_recheck_on:
        name = settings.frames.similarity_model or settings.frames.redundancy_model
        called.setdefault(name, []).append("similarity recheck")

    return called


def _resolve_base_url(settings: config.Settings,
                      model: config.ModelSettings) -> str:
    """The address a run calls `model` at: its host's, from `hosts`; "" for a model whose host is no entry there, which
    the check reports as having no address."""

    try:
        host = settings.find_model_host(model)
    except ValueError:
        # the model names a host `hosts` lacks: the check says so as "no address", as a run would refuse it
        return ""

    return host.url if host else ""


def _find_host_url(settings: config.Settings,
                   name: str) -> str:
    """The address of the entry of `hosts` under `name`; "" when there is none."""

    host = settings.hosts.get(name)

    return host.url if host else ""


def _is_installed(key: str,
                  settings: config.Settings,
                  api_keys: dict[str, bool]) -> bool:
    if key == "lmstudio":
        return (model_setup_service.LMS_COMMAND.is_file() or PROGRAM_FINDER("lms") is not None
                or LMSTUDIO_APP_PATH.exists())
    if key == "mlx":
        return (mlx_setup_service.DEFAULT_VENV / "bin" / "python").is_file()
    if key == "ffmpeg":
        return PROGRAM_FINDER("ffmpeg") is not None and PROGRAM_FINDER("ffprobe") is not None
    if key == "tesseract":
        return (PROGRAM_FINDER("tesseract") is not None
                and not text_detection_service.list_missing_languages(settings.frames.text_reader_languages))
    if key == "classifier":
        return knowledge_filter_service.resolve_playbook_path(settings.frames.knowledge_classifier_command).is_file()
    if key == "unlimited-ocr":
        return unlimited_ocr_reader_service.is_installed()
    if key == "d1-knowledge-filter":
        return knowledge_filter_service.resolve_playbook_path(settings.frames.decision_filter_python).is_file()
    if key in api_keys:
        return api_keys[key]

    return PROGRAM_FINDER(key) is not None


def _list_models_of_hosts(settings: config.Settings,
                          called_models: list[config.ModelSettings]) -> dict[str, list[str] | None]:
    """Each local host's model list by its address, for every host a called model uses and LM Studio and Ollama at
    their own address (so a host installed and running but used by no model is shown as running)."""

    addresses = {(model.spec.partition(":")[0], _resolve_base_url(settings, model)) for model in called_models
                 if model.spec.partition(":")[0] in ("lmstudio", "ollama", "openai-compatible")}
    addresses |= {(name, _find_host_url(settings, name)) for name in ("lmstudio", "ollama")}

    return {base_url: HOST_LISTER(provider, base_url) for provider, base_url in sorted(addresses) if base_url}


def _check_runtimes(settings: config.Settings,
                    called_models: list[config.ModelSettings],
                    host_models: dict[str, list[str] | None]) -> list[dict]:
    used = {PROVIDER_GUIDE_KEYS.get(model.spec.partition(":")[0], "") for model in called_models}
    api_keys = {guide.key: bool(os.environ.get(guide.key_variable)) or any(
        model.api_key for model in called_models if PROVIDER_GUIDE_KEYS.get(model.spec.partition(":")[0]) == guide.key)
                for guide in RUNTIME_SPECS if guide.kind == API_KIND}
    tools_needed = {"ffmpeg": True, "tesseract": True,
                    "classifier": settings.frames.is_knowledge_filter_on or settings.frames.is_text_only_mark_on
                    or settings.frames.is_board_stage_on,
                    "unlimited-ocr": settings.pdf.ocr_engine == "unlimited-ocr",
                    "d1-knowledge-filter": settings.frames.is_knowledge_filter_on
                    and settings.frames.knowledge_filter_model == "liquidai-d1-2B"}
    host_addresses = {"lmstudio": [_find_host_url(settings, "lmstudio")], "ollama": [_find_host_url(settings, "ollama")],
                      "mlx": [_resolve_base_url(settings, model) for model in called_models
                              if model.spec.startswith("openai-compatible:")]}
    runtimes = []
    for guide in RUNTIME_SPECS:
        addresses = [address for address in host_addresses.get(guide.key, []) if address]
        runtimes.append({
            "key": guide.key, "name": guide.name, "kind": guide.kind, "purpose": guide.purpose,
            "download_url": guide.download_url, "key_variable": guide.key_variable,
            "steps": {system: list(guide.steps[system]) for system in SYSTEMS}, "start_steps": list(guide.start_steps),
            "is_needed": tools_needed[guide.key] if guide.kind == TOOL_KIND else guide.key in used,
            "is_installed": _is_installed(guide.key, settings, api_keys),
            # a local host's server answers at every address a model uses; None for an API or a tool
            "is_running": (bool(addresses) and all(host_models.get(address) is not None for address in addresses))
            if guide.kind == HOST_KIND else None,
            # the models a running host has, which the wizard offers when a host is chosen; an embedding model writes
            # no text, so no phase can call it
            "available_models": sorted({model_id for address in addresses for model_id in host_models.get(address) or []
                                        if "embed" not in model_id}),
            # a tool that runs a model of its own (the scanned-page reader): that model, which the Models step lists
            # beside the phases' models with its own Download
            "model_id": settings.pdf.unlimited_ocr_model if guide.key == "unlimited-ocr" else "",
        })

    return runtimes


def _check_models(settings: config.Settings,
                  called: dict[str, list[str]],
                  host_models: dict[str, list[str] | None]) -> list[dict]:
    recommended = {model.spec: model for model in RECOMMENDED_MODELS}
    models = []
    for name, callers in called.items():
        entry = settings.find_configured_model(name)
        spec = entry.spec if entry else ""
        listed = host_models.get(_resolve_base_url(settings, entry)) if entry else None
        guide = recommended.get(spec)
        models.append({
            "name": name, "spec": spec, "used_by": callers, "is_configured": entry is not None,
            # whether the host has it; None while the host does not answer, and for an API, which the check cannot ask
            "is_on_host": None if listed is None else spec.partition(":")[2] in listed,
            "recommended_repository": guide.repository if guide else "",
            "is_downloaded": guide is not None and all(
                (model_setup_service.MODELS_DIR / guide.repository / file_name).is_file()
                or (model_setup_service.LMSTUDIO_MODELS_DIR / guide.repository / file_name).is_file()
                for file_name in guide.files),
        })

    return models


def _describe_download() -> dict:
    """Whether a models download runs now, and the end of its log."""

    pid = json.loads(DOWNLOAD_STATE_PATH.read_text(encoding="utf-8"))["pid"] if DOWNLOAD_STATE_PATH.is_file() else 0
    is_running = False
    if pid:
        try:
            os.kill(pid, 0)
            is_running = True
        except ProcessLookupError:
            # the download ended (or the machine restarted): its log is what is left of it
            is_running = False
    log = DOWNLOAD_LOG_PATH.read_text(encoding="utf-8", errors="replace") if DOWNLOAD_LOG_PATH.is_file() else ""
    # macOS's warning that two libraries of the environment both carry a video class says nothing about the download
    log = "".join(line for line in log.splitlines(keepends=True) if not line.startswith("objc["))

    return {"is_running": is_running, "log": log[-LOG_TAIL_CHARS:]}


def _describe_missing_provider(settings: config.Settings,
                               name: str,
                               host_models: dict[str, list[str] | None]) -> str:
    """Why the model `name` has no provider a run could call, in the words of the Settings page; "" when it has one."""

    entry = settings.find_configured_model(name)
    if entry is None:
        return f"no model is set for it: add models.{name} in Settings"
    provider, _, model_id = entry.spec.partition(":")
    guide_key = PROVIDER_GUIDE_KEYS.get(provider)
    if guide_key is None:
        return f"{provider!r} is not a model provider; known: {', '.join(PROVIDER_GUIDE_KEYS)}"
    guide = next(candidate for candidate in RUNTIME_SPECS if candidate.key == guide_key)
    if guide.kind == API_KIND:
        has_key = bool(os.environ.get(guide.key_variable)) or bool(entry.api_key)

        return "" if has_key else f"{guide.name} has no key: set {guide.key_variable}"
    address = _resolve_base_url(settings, entry)
    if not address:
        return f"no address for the {guide.name} server: set models.{name}.host to an entry of Hosts in Settings"
    listed = host_models.get(address)
    if listed is None:
        return f"the server at {address} does not answer"

    return "" if model_id in listed else f"the server at {address} does not have the model {model_id}"


def _find_missing_providers(settings: config.Settings,
                            called: dict[str, list[str]],
                            host_models: dict[str, list[str] | None]) -> list[dict]:
    """Each model a run calls that has no configured, reachable provider: its name, the phases that call it and why."""

    missing_list = []
    for name, callers in called.items():
        reason = _describe_missing_provider(settings, name, host_models)
        if reason:
            missing_list.append({"name": name, "used_by": callers, "reason": reason})

    return missing_list


def describe_container_refusal(action: str) -> str:
    """Why `action` (a machine setup) is refused while the server runs in a container."""

    return config.CONTAINER_SETUP_REFUSAL.format(action=action)


def list_missing_providers() -> list[dict]:
    """Each model the next run calls that has no configured, reachable provider, read from the settings as a job
    started now would read them."""

    settings = SETTINGS_READER()
    called = _list_called_models(settings)
    called_models = [model for model in map(settings.find_configured_model, called) if model is not None]

    return _find_missing_providers(settings, called, _list_models_of_hosts(settings, called_models))


def check_setup() -> dict:
    """`{"is_container", "has_models", "runtimes", "models", "download", "recommended", "missing_providers"}`: see the
    module's docstring. In a container no runtime is checked and nothing is recommended to install or download."""

    settings = SETTINGS_READER()
    called = _list_called_models(settings)
    called_models = [model for model in map(settings.find_configured_model, called) if model is not None]
    host_models = _list_models_of_hosts(settings, called_models)
    missing_providers = _find_missing_providers(settings, called, host_models)
    if config.IS_CONTAINER_MODE:
        return {"is_container": True, "has_models": bool(settings.models), "runtimes": [],
                "models": _check_models(settings, called, host_models), "download": {"is_running": False, "log": ""},
                "recommended": [], "missing_providers": missing_providers}

    return {"is_container": False, "has_models": bool(settings.models),
            "runtimes": _check_runtimes(settings, called_models, host_models),
            "models": _check_models(settings, called, host_models), "download": _describe_download(),
            "recommended": [{"repository": model.repository, "spec": model.spec, "used_by": model.used_by}
                            for model in RECOMMENDED_MODELS],
            "missing_providers": missing_providers}


def _save_env_value(variable: str,
                    secret: str) -> None:
    """`variable=secret` in the git-ignored `.env`, replacing a line of the same variable."""

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.is_file() else []
    kept = [line for line in lines if not line.startswith(f"{variable}=")]
    ENV_FILE.write_text("\n".join([*kept, f"{variable}={secret}"]) + "\n", encoding="utf-8")
    os.environ[variable] = secret


def configure_models(provider: str,
                     model_id: str,
                     base_url: str,
                     api_key: str) -> OperationResult:
    """The models every phase calls (`frame` and `document`) set to `<provider>:<model_id>` in both default settings
    files, with `base_url` as the address of the provider's entry of `hosts` when given; an API key goes into `.env` under
    the provider's variable. LM Studio with no
    model id takes the recommended model. Refused by name for a provider the program does not know, or a model id
    missing where nothing recommended stands in."""

    # STEP 1: the spec, from what the person chose
    guide = next((candidate for candidate in RUNTIME_SPECS if PROVIDER_GUIDE_KEYS.get(provider) == candidate.key), None)
    if guide is None:
        return OperationResult(2, [f"{provider!r} is not a model host; known: {', '.join(PROVIDER_GUIDE_KEYS)}"])
    if not model_id and provider == "lmstudio":
        model_id = RECOMMENDED_MODELS[0].spec.partition(":")[2]
    if not model_id:
        return OperationResult(2, [f"name the model {guide.name} serves"])
    if provider == "openai-compatible" and not base_url:
        return OperationResult(2, ["an OpenAI-compatible server needs its address (base URL)"])

    # STEP 2: written, each model by its own keys, so every other setting of it is kept
    # the address goes into the host named like the provider, which a model with no host of its own runs on
    changes = [(f"hosts.{provider}.url", base_url)] if base_url else []
    changes += [(f"models.{name}.spec", f"{provider}:{model_id}") for name in CONFIGURED_MODEL_NAMES]
    lines = []
    for key, new_value in changes:
        result = settings_service.save_setting(key, new_value)
        if result.exit_code != 0:
            return OperationResult(result.exit_code, [*lines, *result.output])
        lines.extend(result.output)

    # STEP 3: the key, kept out of the settings files
    if api_key and guide.key_variable:
        _save_env_value(guide.key_variable, api_key)
        lines.append(f"{guide.key_variable} saved in .env")

    return OperationResult(0, lines)


def start_models_download(token: str) -> int:
    """`main.py setup-models` in a process of its own session, its lines in `ui/setup-models.log`; the pid. The token,
    when given, reaches the child as `HF_TOKEN`, never on its command line, where any process list shows it."""

    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if token:
        environment["HF_TOKEN"] = token
    DOWNLOAD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DOWNLOAD_LOG_PATH.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(config.PLAYBOOK_ROOT / "main.py"), "setup-models"],
                                   cwd=config.PLAYBOOK_ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True, env=environment)
    DOWNLOAD_STATE_PATH.write_text(json.dumps({"pid": process.pid}), encoding="utf-8")

    return process.pid
