"""The context window a model host actually loaded, read from the host itself. REQUIREMENTS.md R12.

LM Studio fixes a model's window when it loads it, and lists it at `/api/v0/models` as `loaded_context_length`. The
OpenAI-compatible path (`/v1`) never says it, and a window typed into the settings can be wrong unnoticed: a prompt
past the real window is cut at its start without an error.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

import config as app_config
from services import chat_model_service

__all__ = ["read_loaded_window", "resolve_conversation_window", "resolve_max_output_tokens", "set_fetch_json"]

_TIMEOUT_S = 5


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as response:  # noqa: S310 (a local host the settings name)
        return json.loads(response.read().decode("utf-8"))


# The seam the tests swap: the one place this module reaches the network.
_fetch: Callable[[str], Any] = _fetch_json


def set_fetch_json(fetch: Callable[[str], Any]) -> Callable[[str], Any]:
    """Swap the fetch, for a test; returns the one it replaced."""

    global _fetch
    previous, _fetch = _fetch, fetch

    return previous


def read_loaded_window(base_url: str,
                       model: str) -> int | None:
    """The window `model` is loaded with on the LM Studio host at `base_url`; None when the host does not answer or
    the model is not loaded."""

    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        listing = _fetch(f"{root}/api/v0/models")
    except (OSError, ValueError):
        # The host may be down or answer with no JSON: no window is known, and the caller falls back and says so
        return None
    if not isinstance(listing, dict):
        return None
    loaded = [entry for entry in listing.get("data") or [] if isinstance(entry, dict)
              and entry.get("id") == model and entry.get("state") == "loaded"]

    if not loaded:
        return None
    length = loaded[0].get("loaded_context_length")

    return length if isinstance(length, int) and length > 0 else None


def resolve_max_output_tokens(role: app_config.ModelSettings | None) -> int:
    """The most tokens one reply of the role may take (`ModelSettings.resolve_max_output_tokens`); 0 when no role. The
    conversation's size limit stays under the window less this."""

    return role.resolve_max_output_tokens() if role else 0


def resolve_conversation_window(log: logging.Logger,
                                role: app_config.ModelSettings | None,
                                phase_number: int) -> int:
    """The window a conversation keeps within (phase 4's, R12; phase 5's, R16): the one LM Studio says it loaded, when
    it says one, else the configured one. Smaller, a prompt past the real window is cut without an error; larger, the
    configured window put the size limit on the very number that reads as a cut, and a chapter blocked as cut on a host
    that had cut nothing."""

    configured = app_config.settings.document.conversation_window_tokens
    provider, _, model = (role.spec if role else "").partition(":")
    if provider != "lmstudio":
        return configured
    loaded = read_loaded_window(chat_model_service.resolve_host_url(role), model)
    if loaded is None:
        # Not an error: the host may be down or the model not loaded yet, and the typed window is the best there is
        log.warning(f"phase {phase_number}: LM Studio did not report a loaded window for {model}; using the "
                    f"configured {configured:,}")
        return configured
    if loaded != configured:
        log.warning(f"phase {phase_number}: LM Studio loaded {model} with a {loaded:,}-token window, not the "
                    f"configured {configured:,}; the conversation keeps within {loaded:,}")

    return loaded
