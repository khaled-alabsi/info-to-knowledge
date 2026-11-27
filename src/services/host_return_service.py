"""Waiting for a model host that lost its model while a call ran, so the call is asked again instead of spending its
step's attempts. REQUIREMENTS.md R12.

A machine restarted under a run (froscon 3147, release test 1) ended the second pass's call with "terminated", and its
two retries failed within seconds on a host with no model loaded: the step spent its three attempts and stopped phase 3
on the default settings. An LM Studio restarted under a run blocked a critique the same way (38c3-accessibility).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import config as app_config
from services import chat_model_service
from services import host_window_service

__all__ = ["wait_for_lost_model", "set_clock", "set_test_call"]

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)

# How long a run waits for its model after loading it again, and how often it asks the host: a minute, the owner's
# ("waiting 30 minutes is too much"), after which the call fails and the queue's own retry takes over.
WAIT_LIMIT_S = 60
CHECK_EVERY_S = 15

# The program's own load of a model that does not work: one call at a time, since loaded for four at once the 35B model
# answered every call "Compute error" while listed ready; a load of it took about a minute.
_LOAD_PARALLEL = 1
_LOAD_TIMEOUT_S = 600

# The test call: one token of a two-word prompt, which a model that works answers in a second or two.
_TEST_CALL_TIMEOUT_S = 60

# The seams the tests swap: the clock and the pause between two checks.
_now: Callable[[], float] = time.monotonic
_pause: Callable[[float], None] = time.sleep


def set_clock(now: Callable[[], float],
              pause: Callable[[float], None]) -> tuple[Callable[[], float], Callable[[float], None]]:
    """Swap the clock and the pause, for a test; returns the ones it replaced."""

    global _now, _pause
    previous, _now, _pause = (_now, _pause), now, pause

    return previous


def _read_test_call_error(base_url: str,
                          model: str) -> str:
    """Empty when `model` answers a call of one token; else what the host answered instead (its error's message, such
    as LM Studio's "Compute error."), or why it did not answer at all."""

    body = {"model": model, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 1,
            "ttl": chat_model_service.LMSTUDIO_LOAD_SECONDS}
    request = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TEST_CALL_TIMEOUT_S) as response:  # noqa: S310 (a local host the settings name)
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return _read_error_message(error.read().decode("utf-8", errors="replace")) or f"HTTP {error.code}"
    except (OSError, ValueError) as error:
        return str(error) or type(error).__name__
    if isinstance(answer, dict) and answer.get("choices"):
        return ""

    return _read_error_message(json.dumps(answer)) or "an answer with no reply"


def _read_error_message(body: str) -> str:
    """The message of an OpenAI-style error body; LM Studio nests its engine's error as JSON inside it."""

    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return body.strip()[:200]
    message = error.get("message") if isinstance(error, dict) else error
    inner = str(message or "")
    start = inner.find("{")
    if start >= 0:
        try:
            return str(json.loads(inner[start:]).get("message") or inner)[:200]
        except (ValueError, AttributeError):
            pass

    return inner[:200]


# The seam the tests swap: the one place this module calls the model.
_ask: Callable[[str, str], str] = _read_test_call_error


def set_test_call(ask: Callable[[str, str], str]) -> Callable[[str, str], str]:
    """Swap the test call, for a test; returns the one it replaced."""

    global _ask
    previous, _ask = _ask, ask

    return previous


def _call_host(url: str,
               body: dict | None,
               timeout_s: float) -> dict:
    """LM Studio's answer to a GET (no body) or a POST of `body`, as JSON."""

    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 (a local host the settings name)
        return json.loads(response.read().decode("utf-8"))


def _reload_model(base_url: str,
                  model: str,
                  window: int) -> str:
    """Unloads every copy of `model` the LM Studio host holds, then loads it with `window` tokens of context and one
    call at a time. Empty when the load answered; else what went wrong."""

    root = f"{base_url.rstrip('/').removesuffix('/v1')}/api/v1/models"
    try:
        listing = _call_host(root, None, _TEST_CALL_TIMEOUT_S)
        for entry in listing.get("models") or []:
            if isinstance(entry, dict) and entry.get("key") == model:
                for instance in entry.get("loaded_instances") or []:
                    _call_host(f"{root}/unload", {"instance_id": instance.get("id")}, _TEST_CALL_TIMEOUT_S)
        _call_host(f"{root}/load", {"model": model, "context_length": window, "parallel": _LOAD_PARALLEL},
                   _LOAD_TIMEOUT_S)
    except urllib.error.HTTPError as error:
        return _read_error_message(error.read().decode("utf-8", errors="replace")) or f"HTTP {error.code}"
    except (OSError, ValueError, AttributeError) as error:
        return str(error) or type(error).__name__

    return ""


# The seam the tests swap: the one place this module loads a model.
_load: Callable[[str, str, int], str] = _reload_model


def set_model_loader(load: Callable[[str, str, int], str]) -> Callable[[str, str, int], str]:
    """Swap the model load, for a test; returns the one it replaced."""

    global _load
    previous, _load = _load, load

    return previous


def _read_model_problem(base_url: str,
                        model: str) -> str:
    """Empty when the host lists `model` loaded and it answers; else what is wrong, in words for the person. The
    listing is read first: a call naming a model not loaded makes LM Studio load it, with a window nobody chose."""

    if host_window_service.read_loaded_window(base_url, model) is None:
        return f"{model} is not loaded on the model host"
    error = _ask(base_url, model)

    return f"{model} answers every call with an error: {error}" if error else ""


def wait_for_lost_model(phase: str,
                        on_wait: Callable[[str], None] | None = None) -> bool:
    """True when the LM Studio host of `phase`'s model does not list it loaded after a call raised, or lists it and it
    fails even a call of one token, and the model, loaded again here (`_reload_model`), works within `WAIT_LIMIT_S`: the
    call is then asked again. False at once when the model works (the call failed for another reason) or is on another
    host, which the program never loads, and False when the limit passes, so the queue's own retry takes over. A load
    LM Studio broke answered every call with an engine error, and the queue ran 12 jobs into it in seconds. `on_wait` is
    told what is wrong before the wait starts: a model listed READY that answered "Compute error" to every call kept a
    part waiting 23 minutes while the page said it was writing."""

    role = app_config.settings.resolve_model(app_config.settings.resolve_phase_model_name(phase))
    provider, model = chat_model_service.split_spec(role.spec)
    if provider != "lmstudio":
        return False
    base_url = chat_model_service.resolve_host_url(role)
    problem = _read_model_problem(base_url, model)
    if not problem:
        return False

    if on_wait is not None:
        on_wait(problem)
    window = role.num_ctx or 0
    _LOG.warning(f"the model host has no working model ({problem}): loading {model} again with a window of "
                 f"{window:,} tokens and one call at a time")
    load_error = _load(base_url, model, window) if window else "no window is configured for it"
    if load_error:
        _LOG.warning(f"the model host could not load {model}: {load_error}")
    _LOG.warning(f"checking every {CHECK_EVERY_S} s for {WAIT_LIMIT_S} s; the call is asked again as soon as "
                 f"{model} works")
    deadline = _now() + WAIT_LIMIT_S
    while _now() < deadline:
        _pause(CHECK_EVERY_S)
        if not _read_model_problem(base_url, model):
            _LOG.warning(f"the model host has {model} working again: asking the call again")
            return True
    _LOG.warning(f"the model host has no working {model} after {WAIT_LIMIT_S} s")

    return False
