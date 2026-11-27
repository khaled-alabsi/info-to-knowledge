# <!-- sync: seed/services.chat_model_service v3 -->
"""The only place a chat model is constructed.

Nothing else imports a provider class. Once `num_ctx`, a temperature and a structured-output method
are duplicated across files, "swap the model" becomes a search-and-replace with a tail of missed call
sites, and the ones you miss are the ones that fail on item 40 of 60.
"""

from __future__ import annotations

import json
import os
import urllib.request

from functools import lru_cache
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

import config
from services import model_retry_service
from services.retrying_chat_client import RetryingOllamaChatModel
from services.openai_compatible_client import OpenAICompatibleChatModel
from models.chain_call_spec import NO_THINKING
from models.run_errors import ModelUnavailable

__all__ = ["get_model", "get_named_model", "is_phase_model_spec", "bind_no_thinking", "split_spec",
           "resolve_structured_method", "resolve_host_url", "describe_phase_models", "probe_hosts", "LMSTUDIO_LOAD_SECONDS"]

# A local host drops sockets on the longest generations: a request that never returned is asked again this many times,
# this many seconds apart (see `_build_ollama`)
_REQUEST_RETRIES = 3
_REQUEST_RETRY_BACKOFF_S = 5.0
# What Anthropic's API requires when a model entry sets no `max_tokens`
_ANTHROPIC_MAX_TOKENS = 8192
# How long `probe_hosts` waits for a host to answer
_PROBE_TIMEOUT_S = 5

# The model card's sampling for a call with no thinking (Qwen3.6: temperature 0.7, top_p 0.8; top_k 20, min_p 0 and
# presence_penalty 1.5 are the same in both modes). Sent with the thinking set (1.0, 0.95), a call with thinking off
# deliberated in its answer and wrote a frame twice, or ran into a list of words; with this set, 6 of 6 replays of
# one such call parsed in 14 to 36 s
_NO_THINKING_SAMPLING = {"temperature": 0.7, "top_p": 0.8}


def bind_no_thinking(chat_model: Any,
                     **kwargs: Any) -> Any:
    """`chat_model` asked to think nothing (`reasoning_effort: "none"`, which LM Studio honours) with the sampling made
    for that, plus `kwargs`."""

    return chat_model.bind(reasoning_effort=NO_THINKING, **_NO_THINKING_SAMPLING, **kwargs)


# Which structured-output method a provider can actually honour: measured, not assumed
_STRUCTURED_DEFAULT = {
    "ollama": "function_calling",
    "lmstudio": "function_calling",
    "openai-compatible": "function_calling",
    "openai": "json_schema",
    "anthropic": "json_schema",
}

# Which sampling settings each provider takes beyond `top_p`, and how: as a field of its client, or (an
# OpenAI-compatible host) in the request body, where the host reads what the OpenAI schema lacks. A key outside a
# provider's two rows is refused by name, never dropped: a setting written and not sent looks exactly like one that
# made no difference.
_SAMPLING_FIELDS = {
    "ollama": ("top_k", "repeat_penalty", "seed"),
    "lmstudio": ("presence_penalty", "frequency_penalty", "seed"),
    "openai-compatible": ("presence_penalty", "frequency_penalty", "seed"),
    "openai": ("presence_penalty", "frequency_penalty", "seed"),
    "anthropic": ("top_k",),
}
_SAMPLING_BODY = {
    "lmstudio": ("top_k", "min_p", "repeat_penalty"),
    # `presence_context_size`: how many recent tokens mlx_vlm.server's presence penalty looks back over, 20 when not
    # sent, too short to see a repeated line of thought (a second look repeated 11 lines 38 times)
    "openai-compatible": ("top_k", "min_p", "repeat_penalty", "presence_context_size"),
}


def split_spec(spec: str) -> tuple[str, str]:
    """`provider:model` -> (provider, model). A model name may itself contain colons: `ollama:qwen3.6:35b-a3b-nvfp4` is
    ("ollama", "qwen3.6:35b-a3b-nvfp4")."""

    provider, _, model = spec.partition(":")
    if not provider or not model:
        raise ModelUnavailable(
            f"model spec {spec!r} is not `provider:model` - e.g. `ollama:qwen3.6:35b-a3b-nvfp4`"
        )

    return provider, model


def resolve_host_url(cfg: config.ModelSettings) -> str | None:
    """The address a model's host answers at: its own `base_url`, else the default of an Ollama or LM Studio host. None
    for an `openai-compatible` model with no `base_url`: that host has no default to guess."""

    provider, _ = split_spec(cfg.spec)
    defaults = {"ollama": config.settings.ollama_base_url, "lmstudio": config.settings.lmstudio_base_url}

    return cfg.base_url or defaults.get(provider)


def resolve_structured_method(phase: str) -> str:
    """The structured-output method to pass to `with_structured_output` for this phase's model. Never left to the library
    default on a local model."""

    return _pick_structured_method(_resolve_phase_settings(phase))


def _split_known_spec(cfg: config.ModelSettings) -> tuple[str, str]:
    """`(provider, model)` of the model's spec; a provider this module cannot build is refused by name, with the ones it
    can."""

    provider, model = split_spec(cfg.spec)
    if provider not in _STRUCTURED_DEFAULT:
        raise ModelUnavailable(f"unknown provider {provider!r} in {cfg.spec!r}. Known: "
                               f"{', '.join(sorted(_STRUCTURED_DEFAULT))}")

    return provider, model


def _pick_structured_method(cfg: config.ModelSettings) -> str:
    """The structured-output method of one model: its own setting, else its provider's default."""

    provider, _ = _split_known_spec(cfg)

    return cfg.structured_method or _STRUCTURED_DEFAULT[provider]


# How long LM Studio keeps a model it loads for a request, idle: by default an hour, and a model loaded for a run that
# had been unloaded (for a second reader's memory) was unloaded mid-request an hour on, and blocked phase 4 (pdf-05)
LMSTUDIO_LOAD_SECONDS = 86_400


def _build_sampling_kwargs(provider: str,
                           cfg: config.ModelSettings) -> dict[str, Any]:
    """The client arguments that carry a model's sampling: `top_p`, the provider's own fields, and for an
    OpenAI-compatible host the rest in `extra_body`. Raises `ModelUnavailable` on a key the provider cannot take."""

    fields = _SAMPLING_FIELDS.get(provider, ())
    body_keys = _SAMPLING_BODY.get(provider, ())
    refused = sorted(set(cfg.sampling) - set(fields) - set(body_keys))
    if refused:
        raise ModelUnavailable(f"sampling {', '.join(refused)} cannot be sent to provider {provider!r}: it takes "
                               f"{', '.join(fields + body_keys) or 'none beyond top_p'}")
    kwargs: dict[str, Any] = {key: setting for key, setting in cfg.sampling.items() if key in fields}
    if cfg.top_p is not None:
        kwargs["top_p"] = cfg.top_p
    body = {key: setting for key, setting in cfg.sampling.items() if key in body_keys}
    if body:
        kwargs["extra_body"] = body

    return kwargs


def _build_ollama(cfg: config.ModelSettings,
                  model: str,
                  sampling: dict[str, Any]) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": resolve_host_url(cfg),
        "temperature": cfg.temperature,
    }
    if cfg.num_ctx is not None:
        kwargs["num_ctx"] = cfg.num_ctx
    if cfg.num_predict is not None:
        kwargs["num_predict"] = cfg.num_predict
    if cfg.reasoning is not None:
        kwargs["reasoning"] = cfg.reasoning
    # The request timeout is the liveness floor: without it a wedged host hangs the attempt for the life of the process
    if cfg.request_timeout_s is not None:
        kwargs["client_kwargs"] = {"timeout": cfg.request_timeout_s}
    kwargs.update(sampling)

    # A LOCAL HOST DROPS SOCKETS on the longest generations - the ones that cost the most to
    # redo. Retrying the REQUEST is safe because a model call has no side effect of its own: the
    # tools it asks for run after the reply comes back, so a request that never returned changed
    # nothing. Retrying the ATTEMPT instead throws away every read, write and line of reasoning
    # the worker had built. Measured twice in one day, once 27 minutes in.
    return RetryingOllamaChatModel(
        **kwargs,
        retry_settings=model_retry_service.RetrySettings(attempts=_REQUEST_RETRIES, backoff_s=_REQUEST_RETRY_BACKOFF_S))


def _build_openai_compatible(cfg: config.ModelSettings,
                             provider: str,
                             model: str,
                             sampling: dict[str, Any]) -> BaseChatModel:
    base = resolve_host_url(cfg)
    if not base:
        raise ModelUnavailable(
            f"provider {provider!r} needs a base_url - set it in playbook.local.json"
        )

    # `api_key` is required even when the host ignores it: the client refuses to construct
    # without one. `base_url`/`api_key` are aliases of openai_api_base/openai_api_key - a
    # model_fields dump does not show them, but they work.
    # `timeout` is the request timeout, under the field name THIS client uses (the others set the same floor under
    # their own).
    # NOT plain `ChatOpenAI`: it targets the official OpenAI schema and drops every field a
    # third-party host adds, `reasoning_content` among them - so a thinking model's thinking
    # never reaches `messages.jsonl`. `cfg.reasoning` is an Ollama-only switch and is not passed
    # here; a local host decides for itself whether it separates thinking, and this client logs
    # it whenever it does. `stream_usage`: a streamed markdown call (`chain_runner`) still reports its token
    # counts, which the host sends only when asked for them.
    return OpenAICompatibleChatModel(model=model, base_url=base,
                                     api_key=cfg.api_key or "not-needed",
                                     temperature=cfg.temperature, max_tokens=cfg.max_tokens,
                                     timeout=cfg.request_timeout_s, stream_usage=True, **sampling)


def _build_openai(cfg: config.ModelSettings,
                  model: str,
                  sampling: dict[str, Any]) -> BaseChatModel:
    if not cfg.api_key and not os.environ.get("OPENAI_API_KEY"):
        raise ModelUnavailable("provider 'openai' needs an api_key or OPENAI_API_KEY")

    return ChatOpenAI(model=model, api_key=cfg.api_key, temperature=cfg.temperature,
                      max_tokens=cfg.max_tokens, timeout=cfg.request_timeout_s, **sampling)


def _build_anthropic(cfg: config.ModelSettings,
                     model: str,
                     sampling: dict[str, Any]) -> BaseChatModel:
    return ChatAnthropic(model=model, temperature=cfg.temperature, timeout=cfg.request_timeout_s,
                         max_tokens=cfg.max_tokens or _ANTHROPIC_MAX_TOKENS, **sampling)


def _build(cfg: config.ModelSettings,
           load_seconds: int = LMSTUDIO_LOAD_SECONDS) -> BaseChatModel:
    provider, model = _split_known_spec(cfg)
    sampling = _build_sampling_kwargs(provider, cfg)
    if provider == "lmstudio":
        sampling["extra_body"] = {**sampling.get("extra_body", {}), "ttl": load_seconds}
    if provider == "ollama":
        return _build_ollama(cfg, model, sampling)
    if provider in ("lmstudio", "openai-compatible"):
        return _build_openai_compatible(cfg, provider, model, sampling)
    if provider == "openai":
        return _build_openai(cfg, model, sampling)
    if provider == "anthropic":
        return _build_anthropic(cfg, model, sampling)

    raise ModelUnavailable(f"provider {provider!r} is known but this module builds no client for it")


def _resolve_phase_model_name(phase: str) -> str:
    """The name of the configured model this phase calls; a `phaseModels` entry naming a model `models` does not hold is
    `ModelUnavailable`, never a raw `ValueError`."""

    try:
        return config.settings.resolve_phase_model_name(phase)
    except ValueError as exc:
        raise ModelUnavailable(str(exc)) from exc


def _resolve_phase_settings(phase: str) -> config.ModelSettings:
    """The settings of the model this phase calls, from `_resolve_phase_model_name`."""

    return config.settings.resolve_model(_resolve_phase_model_name(phase))


@lru_cache(maxsize=None)
def get_model(phase: str) -> BaseChatModel:
    """The model this phase calls, built once. Raises `ModelUnavailable`; never falls back silently."""

    try:
        config.check_phase_calls_model(phase)
    except ValueError as exc:
        raise ModelUnavailable(str(exc)) from exc

    return _build(_resolve_phase_settings(phase))


@lru_cache(maxsize=None)
def get_named_model(name: str,
                    load_seconds: int = LMSTUDIO_LOAD_SECONDS) -> BaseChatModel:
    """The configured model under `name` in `models`, built once, for a step that names its model in its own setting
    (the redundancy filter's `frames.redundancyModel`); LM Studio keeps it loaded `load_seconds` idle. Raises
    `ModelUnavailable` for a name `models` does not hold."""

    if name not in config.settings.models:
        raise ModelUnavailable(f"{name!r} is not a configured model; configured: "
                               f"{', '.join(sorted(config.settings.models)) or 'none'}")

    return _build(config.settings.models[name], load_seconds)


def is_phase_model_spec(name: str,
                        phase: str) -> bool:
    """Whether the configured model `name` is the model `phase` calls, by its spec (two names may hold one model)."""

    return name in config.settings.models and config.settings.models[name].spec == _resolve_phase_settings(phase).spec


def describe_phase_models() -> list[dict[str, Any]]:
    """What `models` prints: each phase that calls a model, the configured model it uses, and that model's
    effective settings. `explicit` says whether the name has its own entry in `models`."""

    rows = []
    for phase in config.PHASE_DEFAULT_MODELS:
        name = _resolve_phase_model_name(phase)
        cfg = config.settings.resolve_model(name)
        provider, model = split_spec(cfg.spec)
        rows.append({
            "phase": phase,
            "name": name,
            "provider": provider,
            "model": model,
            "explicit": name in config.settings.models,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "sampling": dict(cfg.sampling),
            "num_ctx": cfg.num_ctx,
            "structured_method": _pick_structured_method(cfg),
        })

    return rows


def probe_hosts() -> list[dict[str, Any]]:
    """What `probe` prints: is each configured host reachable, and if not, why. An unreachable optional backend is the
    normal state, so it is reported as a sentence naming the host - never as a stack trace."""

    endpoints: dict[str, str] = {}
    missing: list[dict] = []
    for phase in config.PHASE_DEFAULT_MODELS:
        cfg = _resolve_phase_settings(phase)
        provider, _ = split_spec(cfg.spec)
        base = resolve_host_url(cfg)
        if provider in ("ollama", "lmstudio", "openai-compatible") and base:
            endpoints[base] = provider
        elif provider == "openai-compatible":
            missing.append({"host": "", "provider": provider, "reachable": False,
                            "detail": f"phase {phase!r}: provider {provider!r} needs a base_url"})

    results = missing
    for base, provider in sorted(endpoints.items()):
        url = f"{base.rstrip('/')}/api/tags" if provider == "ollama" else f"{base.rstrip('/')}/models"
        try:
            with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if provider == "ollama":
                available = [m["model"] for m in payload.get("models", [])]
            else:
                available = [m["id"] for m in payload.get("data", [])]
            results.append({"host": base, "provider": provider, "reachable": True,
                            "models": available})
        except OSError as exc:  # a refused connection, a failed lookup and a timeout
            results.append({"host": base, "provider": provider, "reachable": False,
                            "detail": f"{base} did not answer: {exc}"})
        except (ValueError, KeyError) as exc:
            results.append({"host": base, "provider": provider, "reachable": False,
                            "detail": f"{base} answered but not in the expected shape: {exc}"})

    return results
