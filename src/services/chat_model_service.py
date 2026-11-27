# <!-- sync: seed/services.chat_model_service v3 -->
"""The only place a chat model is constructed.

Nothing else imports a provider class. Once `num_ctx`, a temperature and a structured-output method
are duplicated across files, "swap the model" becomes a search-and-replace with a tail of missed call
sites, and the ones you miss are the ones that fail on item 40 of 60.

Every host is reached with the standard OpenAI-compatible requests (`/v1/chat/completions`, `/v1/models`), at the address
of the entry of `hosts` the model names (`models.frame.host`), else of the entry named like its spec's provider.
"""

from __future__ import annotations

import os

from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

import config
from services import endpoint_client
from services.openai_compatible_client import OpenAICompatibleChatModel
from models.chain_call_spec import NO_THINKING
from models.endpoint_errors import EndpointError
from models import structured_method_vocabulary
from models.endpoint_spec import EndpointSpec
from models.run_errors import ModelUnavailable

__all__ = ["get_model", "get_named_model", "is_phase_model_spec", "bind_no_thinking", "split_spec",
           "resolve_structured_method", "resolve_host_url", "describe_phase_models", "probe_hosts",
           "build_chat_endpoint"]

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
_STRUCTURED_DEFAULT = structured_method_vocabulary.STRUCTURED_METHOD_BY_PROVIDER

# Providers the program once built a client of its own for: each is now served behind an OpenAI-compatible address
_RETIRED_PROVIDERS = ("ollama", "anthropic")

# Which sampling settings each provider takes beyond `top_p`, and how: as a field of its client, or (an
# OpenAI-compatible host) in the request body, where the host reads what the OpenAI schema lacks. A key outside a
# provider's two rows is refused by name, never dropped: a setting written and not sent looks exactly like one that
# made no difference.
_SAMPLING_FIELDS = {
    "lmstudio": ("presence_penalty", "frequency_penalty", "seed"),
    "openai-compatible": ("presence_penalty", "frequency_penalty", "seed"),
    "openai": ("presence_penalty", "frequency_penalty", "seed"),
}
_SAMPLING_BODY = {
    "lmstudio": ("top_k", "min_p", "repeat_penalty"),
    # `presence_context_size`: how many recent tokens mlx_vlm.server's presence penalty looks back over, 20 when not
    # sent, too short to see a repeated line of thought (a second look repeated 11 lines 38 times)
    "openai-compatible": ("top_k", "min_p", "repeat_penalty", "presence_context_size"),
}


def split_spec(spec: str) -> tuple[str, str]:
    """`provider:model` -> (provider, model). A model name may itself contain colons: `openai-compatible:qwen3.6:35b` is
    ("openai-compatible", "qwen3.6:35b")."""

    provider, _, model = spec.partition(":")
    if not provider or not model:
        raise ModelUnavailable(
            f"model spec {spec!r} is not `provider:model` - e.g. `openai-compatible:qwen3.6-35b-a3b`"
        )

    return provider, model


def resolve_host_url(cfg: config.ModelSettings) -> str | None:
    """The address a model's host answers at: the entry of `hosts` it names, else the one named like its spec's provider
    (`lmstudio`). None for an `openai-compatible` model that names no host: that host has no default to guess."""

    split_spec(cfg.spec)
    host = config.settings.find_model_host(cfg)

    return host.url if host else None


def build_chat_endpoint(base_url: str,
                        timeout_s: int | None = None) -> EndpointSpec:
    """Give me the server spec for the chat host at `base_url`, with the key its entry of `hosts` names and the request
    timeout `timeout_s` (`DEFAULT_CHAT_TIMEOUT_S` when None)."""

    host = config.settings.find_host_by_url(base_url)

    return EndpointSpec(base_url=base_url, api_key_env=host.api_key_env if host else "",
                        timeout_s=timeout_s or config.DEFAULT_CHAT_TIMEOUT_S)


def resolve_structured_method(phase: str) -> str:
    """The structured-output method to pass to `with_structured_output` for this phase's model. Never left to the library
    default on a local model."""

    return _pick_structured_method(_resolve_phase_settings(phase))


def _split_known_spec(cfg: config.ModelSettings) -> tuple[str, str]:
    """`(provider, model)` of the model's spec; a provider this module cannot build is refused by name, with the ones it
    can."""

    provider, model = split_spec(cfg.spec)
    if provider in _RETIRED_PROVIDERS:
        raise ModelUnavailable(f"provider {provider!r} in {cfg.spec!r} is no longer built by the program: serve it behind an "
                               f"OpenAI-compatible address and name the model `openai-compatible:{model}` with its "
                               f"`host`, an entry of `hosts`")
    if provider not in _STRUCTURED_DEFAULT:
        raise ModelUnavailable(f"unknown provider {provider!r} in {cfg.spec!r}. Known: "
                               f"{', '.join(sorted(_STRUCTURED_DEFAULT))}")

    return provider, model


def _pick_structured_method(cfg: config.ModelSettings) -> str:
    """The structured-output method of one model: its own setting, else its provider's default."""

    provider, _ = _split_known_spec(cfg)

    return cfg.structured_method or _STRUCTURED_DEFAULT[provider]


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


def _build_openai_compatible(cfg: config.ModelSettings,
                             provider: str,
                             model: str,
                             sampling: dict[str, Any]) -> BaseChatModel:
    base = resolve_host_url(cfg)
    if not base:
        raise ModelUnavailable(
            f"provider {provider!r} needs a host - name an entry of `hosts` in the model's `host`"
        )

    # `api_key` is required even when the host ignores it: the client refuses to construct
    # without one. `base_url`/`api_key` are aliases of openai_api_base/openai_api_key - a
    # model_fields dump does not show them, but they work.
    # `timeout` is the request timeout, under the field name THIS client uses (the others set the same floor under
    # their own).
    # NOT plain `ChatOpenAI`: it targets the official OpenAI schema and drops every field a
    # third-party host adds, `reasoning_content` among them - so a thinking model's thinking
    # never reaches `messages.jsonl`. `cfg.reasoning` is not passed
    # here; a local host decides for itself whether it separates thinking, and this client logs
    # it whenever it does. `stream_usage`: a streamed markdown call (`chain_runner`) still reports its token
    # counts, which the host sends only when asked for them.
    host = config.settings.find_model_host(cfg)
    key_env = host.api_key_env if host else ""
    key = cfg.api_key or (os.environ.get(key_env) if key_env else None) or "not-needed"
    timeout_s = cfg.request_timeout_s if cfg.request_timeout_s is not None else config.DEFAULT_CHAT_TIMEOUT_S

    return OpenAICompatibleChatModel(model=model, base_url=base,
                                     api_key=key,
                                     temperature=cfg.temperature, max_tokens=cfg.max_tokens,
                                     timeout=timeout_s, stream_usage=True, **sampling)


def _build_openai(cfg: config.ModelSettings,
                  model: str,
                  sampling: dict[str, Any]) -> BaseChatModel:
    if not cfg.api_key and not os.environ.get("OPENAI_API_KEY"):
        raise ModelUnavailable("provider 'openai' needs an api_key or OPENAI_API_KEY")

    return ChatOpenAI(model=model, api_key=cfg.api_key, temperature=cfg.temperature,
                      max_tokens=cfg.max_tokens, timeout=cfg.request_timeout_s, **sampling)


def _build(cfg: config.ModelSettings) -> BaseChatModel:
    """The chat client for `cfg`. No host is asked how long to keep a model loaded: the server decides."""

    provider, model = _split_known_spec(cfg)
    sampling = _build_sampling_kwargs(provider, cfg)
    if provider in ("lmstudio", "openai-compatible"):
        return _build_openai_compatible(cfg, provider, model, sampling)
    if provider == "openai":
        return _build_openai(cfg, model, sampling)

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
def get_named_model(name: str) -> BaseChatModel:
    """The configured model under `name` in `models`, built once, for a step that names its model in its own setting
    (the redundancy filter's `frames.redundancyModel`). Raises `ModelUnavailable` for a name `models` does not hold."""

    if name not in config.settings.models:
        raise ModelUnavailable(f"{name!r} is not a configured model; configured: "
                               f"{', '.join(sorted(config.settings.models)) or 'none'}")

    return _build(config.settings.models[name])


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
        if provider in ("lmstudio", "openai-compatible") and base:
            endpoints[base] = provider
        elif provider == "openai-compatible":
            missing.append({"host": "", "provider": provider, "reachable": False,
                            "detail": f"phase {phase!r}: provider {provider!r} needs a host"})

    results = missing
    for base, provider in sorted(endpoints.items()):
        endpoint = build_chat_endpoint(base_url=base, timeout_s=_PROBE_TIMEOUT_S)
        try:
            available = endpoint_client.list_served_models(endpoint=endpoint)
            results.append({"host": base, "provider": provider, "reachable": True,
                            "models": available})
        except EndpointError as exc:  # nobody answered, or the answer was refused or not a model list
            results.append({"host": base, "provider": provider, "reachable": False,
                            "detail": f"{base} did not answer: {exc}"})

    return results
