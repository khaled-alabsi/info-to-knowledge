"""What a run uses for a setting that has no value of its own. A model with no `host` runs on the host named like its
provider, a model with no `structuredMethod` uses its provider's, a `language` left empty is found from the recording:
the Settings page shows that instead of "(not set)", from the two fields `add_run_values` puts on such a row."""

from __future__ import annotations

import config
from models import structured_method_vocabulary
from models.setting_run_value_result import SettingRunValueResult

__all__ = ["add_run_values", "describe_run_value"]

# The label the settings service gives the empty entry of a list of choices, which this module relabels
_EMPTY_OPTION_LABEL = "(empty)"
_MODEL_FIELD_COUNT = 3

# What happens in place of a value, for the settings whose empty value a run turns into no value at all
_NOTE_BY_KEY = {
    "audio.language": "found from the recording",
    "audio.diarization.minSpeakers": "no lower limit: found from the recording",
    "audio.diarization.maxSpeakers": "no upper limit: found from the recording",
    "prompt.userPrompt": "nothing is added to a frame's message",
    "prompt.transcriptBeforeSeconds": "all the speech before the frame is sent",
    "prompt.previousFramesLimit": "the notes of every earlier frame are sent",
    "pdf.cleanupWishes": "no extra wishes are added",
    "pdf.rewriteWishes": "no extra wishes are added",
    "pdf.knowledgeWishes": "no extra wishes are added",
    "machine": "no machine name is shown beside the worker set",
    "providers.chat.host": "scanned PDF pages are read by the reader installed on this machine",
    "providers.speech.host": "speech to text runs on this machine",
    "providers.diarization.host": "speaker turns are found on this machine",
    "providers.embeddings.host": "frame vectors are made on this machine",
    "providers.decision.host": "the decision model runs on this machine",
    "providers.diarization.model": "the host answers with its own model",
    "providers.embeddings.model": "the host answers with its own model",
    "providers.decision.model": "the host answers with its own model",
}


def _find_host_name(settings: config.Settings,
                    model: config.ModelSettings) -> str:
    """The entry of `hosts` a model runs on, "" when it has none."""

    name = model.host or model.spec.partition(":")[0]

    return name if name in settings.hosts else ""


def _describe_model_host(settings: config.Settings,
                         model: config.ModelSettings) -> SettingRunValueResult:
    host_name = _find_host_name(settings=settings, model=model)
    if host_name:
        return SettingRunValueResult(used_value=host_name, note="from the model's provider")
    if model.spec.partition(":")[0] == "openai":
        return SettingRunValueResult(used_value=None, note="the model is called at OpenAI's own service")

    return SettingRunValueResult(used_value=None, note="a run refuses this model until a host is chosen")


def _describe_model_key(settings: config.Settings,
                        model: config.ModelSettings) -> SettingRunValueResult:
    if model.spec.partition(":")[0] == "openai":
        return SettingRunValueResult(used_value="OPENAI_API_KEY", note="the key's environment variable, read for OpenAI")
    host_name = _find_host_name(settings=settings, model=model)
    key_variable = settings.hosts[host_name].api_key_env if host_name else ""
    if key_variable:
        return SettingRunValueResult(used_value=key_variable, note="the key's environment variable, named by its host")

    return SettingRunValueResult(used_value=None, note="no key is needed: a placeholder is sent")


def _describe_model_output_limit(model: config.ModelSettings) -> SettingRunValueResult:
    if model.num_predict:
        return SettingRunValueResult(used_value=model.num_predict, note="from the model's numPredict")

    return SettingRunValueResult(used_value=None, note="no limit is sent: the host decides")


def _describe_model_method(model: config.ModelSettings) -> SettingRunValueResult:
    method = structured_method_vocabulary.STRUCTURED_METHOD_BY_PROVIDER.get(model.spec.partition(":")[0])
    if method:
        return SettingRunValueResult(used_value=method, note="from the model's provider")

    return SettingRunValueResult(used_value=None, note="a run refuses this model: its provider is not one it can call")


def _describe_model_field(settings: config.Settings,
                          model_name: str,
                          field_key: str) -> SettingRunValueResult | None:
    """What a run uses for a model's empty `host`, `apiKey`, `structuredMethod`, `framesPerCall`, `topP`, `maxTokens` or
    `requestTimeoutS`; None for another field."""

    model = settings.resolve_model(model_name)
    if field_key == "host":
        return _describe_model_host(settings=settings, model=model)
    if field_key == "apiKey":
        return _describe_model_key(settings=settings, model=model)
    if field_key == "structuredMethod":
        return _describe_model_method(model=model)
    if field_key == "framesPerCall":
        return SettingRunValueResult(used_value=settings.frames_processing.frames_per_call,
                                     note="from the frames processing setting")
    if field_key == "topP":
        return SettingRunValueResult(used_value=None, note="not sent: the model host uses its own value")
    if field_key == "maxTokens":
        return _describe_model_output_limit(model=model)
    if field_key == "requestTimeoutS":
        return SettingRunValueResult(used_value=config.DEFAULT_CHAT_TIMEOUT_S, note="the program's own limit")

    return None


def _find_run_value(settings: config.Settings,
                    path: list[str]) -> SettingRunValueResult | None:
    """What a run uses for the empty setting at `path`; None when the program knows nothing to say for it."""

    if path[0] == "models" and len(path) == _MODEL_FIELD_COUNT:
        return _describe_model_field(settings=settings, model_name=path[1], field_key=path[2])
    if path[0] == "phaseModels" and path[1:2] and path[1] in config.PHASE_DEFAULT_MODELS:
        return SettingRunValueResult(used_value=config.PHASE_DEFAULT_MODELS[path[1]], note="the phase's own model")
    key = ".".join(path)
    if key == "frames.similarityModel":
        return SettingRunValueResult(used_value=settings.frames.redundancy_model, note="the redundancy filter's model")
    if key == "providers.speech.model":
        return SettingRunValueResult(used_value=settings.audio.whisper_model,
                                     note="the speech model chosen in the audio settings")
    if key in _NOTE_BY_KEY:
        return SettingRunValueResult(used_value=None, note=_NOTE_BY_KEY[key])

    return None


def describe_run_value(run_value: SettingRunValueResult) -> str:
    """Give me the value a run uses with where it comes from, as "lmstudio (from the model's provider)", or only what
    happens when a run uses no value."""

    if run_value.used_value is None:
        return run_value.note

    return f"{run_value.used_value} ({run_value.note})"


def add_run_values(rows: list[dict],
                   settings: config.Settings) -> list[dict]:
    """Each row whose own value is empty with `resolved` (what a run uses, None when it uses no value) and
    `resolved_note` (where that comes from, or what happens instead); the empty entry of such a row's list of choices
    says it too, as "default: lmstudio (from the model's provider)"."""

    for row in rows:
        if row["value"] is not None and row["value"] != "":
            continue
        run_value = _find_run_value(settings=settings, path=row["path"])
        if run_value is None:
            continue
        row["resolved"] = run_value.used_value
        row["resolved_note"] = run_value.note
        for option in row.get("choice_options") or []:
            if option["value"] == "" and option["label"] == _EMPTY_OPTION_LABEL:
                option["label"] = f"default: {describe_run_value(run_value=run_value)}"
                option["hint"] = ""

    return rows
