"""The context window a model host actually loaded, read from the host itself. REQUIREMENTS.md R12.

A server fixes a model's window when it loads it. The standard `GET /v1/models` reply holds none, but a server may add
it to the model's entry (LM Studio, vLLM). A window typed into the settings can be wrong unnoticed: a prompt past the
real window is cut at its start without an error.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import config as app_config
from models.endpoint_errors import EndpointError
from services import chat_model_service
from services import endpoint_client

__all__ = ["set_window_reader", "read_loaded_window", "resolve_conversation_window", "resolve_max_output_tokens"]

_TIMEOUT_S = 5

# What reads the window instead of the server, for a test: the suite reaches no model host, and the local settings may name
# one that is running
_window_reader: Callable[[str, str], int | None] | None = None


def set_window_reader(reader: Callable[[str, str], int | None] | None) -> Callable[[str, str], int | None] | None:
    """Swap what reads the loaded window, for a test; returns the one it replaced."""

    global _window_reader
    previous, _window_reader = _window_reader, reader

    return previous


def read_loaded_window(base_url: str,
                       model: str) -> int | None:
    """The window `model` is loaded with on the server at `base_url`; None when the server does not answer, does not list
    the model or reports no window for it."""

    if _window_reader is not None:
        return _window_reader(base_url, model)
    endpoint = chat_model_service.build_chat_endpoint(base_url=base_url, timeout_s=_TIMEOUT_S)
    try:
        return endpoint_client.read_reported_window(endpoint=endpoint, model=model)
    except EndpointError:
        # The server may be down or answer with no model list: no window is known, and the caller falls back and says so
        return None


def resolve_max_output_tokens(role: app_config.ModelSettings | None) -> int:
    """The most tokens one reply of the role may take (`ModelSettings.resolve_max_output_tokens`); 0 when no role. The
    conversation's size limit stays under the window less this."""

    return role.resolve_max_output_tokens() if role else 0


def resolve_conversation_window(log: logging.Logger,
                                role: app_config.ModelSettings | None,
                                phase_number: int) -> int:
    """The window a conversation keeps within (phase 4's, R12; phase 5's, R16): the one the server says it loaded, when
    it says one, else the configured one. Smaller, a prompt past the real window is cut without an error; larger, the
    configured window put the size limit on the very number that reads as a cut, and a chapter blocked as cut on a host
    that had cut nothing."""

    configured = app_config.settings.document.conversation_window_tokens
    provider, _, model = (role.spec if role else "").partition(":")
    host_url = chat_model_service.resolve_host_url(role) if role else None
    if provider not in ("lmstudio", "openai-compatible") or not host_url:
        return configured
    loaded = read_loaded_window(host_url, model)
    if loaded is None:
        # Not an error: the server may be down, or report no window, and the typed window is the best there is
        log.warning(f"phase {phase_number}: the server did not report a loaded window for {model}; using the "
                    f"configured {configured:,}")
        return configured
    if loaded != configured:
        log.warning(f"phase {phase_number}: the server loaded {model} with a {loaded:,}-token window, not the "
                    f"configured {configured:,}; the conversation keeps within {loaded:,}")

    return loaded
