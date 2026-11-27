"""Waiting for a model host that lost its model while a call ran, so the call is asked again instead of spending its
step's attempts. REQUIREMENTS.md R12.

A machine restarted under a run (froscon 3147, release test 1) ended the second pass's call with "terminated", and its
two retries failed within seconds on a host with no model loaded: the step spent its three attempts and stopped phase 3
on the default settings. A model server restarted under a run blocked a critique the same way (38c3-accessibility). The
program only asks the server (`GET /v1/models`, one call of one token); it never loads or unloads a model: the server
brings its model back by its own settings.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

import config as app_config
from models.endpoint_errors import EndpointError
from services import chat_model_service
from services import endpoint_client

__all__ = ["wait_for_lost_model", "set_clock", "set_test_call"]

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)

# How long a run waits for its model to come back, and how often it asks the host: a minute, the owner's ("waiting 30
# minutes is too much"), after which the call fails and the queue's own retry takes over.
WAIT_LIMIT_S = 60
CHECK_EVERY_S = 15

# The test call: one token of a two-word prompt, which a model that works answers in a second or two.
_TEST_CALL_TIMEOUT_S = 60
# How long the listing of a server's models may take
_LISTING_TIMEOUT_S = 5

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

    body = {"model": model, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 1}
    endpoint = chat_model_service.build_chat_endpoint(base_url=base_url, timeout_s=_TEST_CALL_TIMEOUT_S)
    try:
        endpoint_client.request_chat_completion(endpoint=endpoint, body=body)
    except EndpointError as error:
        return _read_error_message(error.detail) or (f"HTTP {error.status}" if error.status else "no answer")

    return ""


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


def _read_model_problem(base_url: str,
                        model: str) -> str:
    """Empty when the server lists `model` and it answers a call; else what is wrong, in words for the person. The
    listing is read first: a call naming a model the server does not list is refused at once, with no hint why."""

    endpoint = chat_model_service.build_chat_endpoint(base_url=base_url, timeout_s=_LISTING_TIMEOUT_S)
    try:
        listed = endpoint_client.list_served_models(endpoint=endpoint)
    except EndpointError as error:
        return f"the model server does not answer: {error}"
    if model not in listed:
        return f"{model} is not listed by the model server"
    error_text = _ask(base_url, model)

    return f"{model} answers every call with an error: {error_text}" if error_text else ""


def wait_for_lost_model(phase: str,
                        on_wait: Callable[[str], None] | None = None) -> bool:
    """True when the model server of `phase`'s model does not list the model after a call raised, or lists it and it
    fails even a call of one token, and the model works again within `WAIT_LIMIT_S`: the call is then asked again. False
    at once when the model works (the call failed for another reason) or the phase's model has no server address, and
    False when the limit passes, so the queue's own retry takes over. The program does not load the model: the server
    does, by its own settings. `on_wait` is told what is wrong before the wait starts: a model listed READY that
    answered "Compute error" to every call kept a part waiting 23 minutes while the page said it was writing."""

    role = app_config.settings.resolve_model(app_config.settings.resolve_phase_model_name(phase))
    provider, model = chat_model_service.split_spec(role.spec)
    base_url = chat_model_service.resolve_host_url(role)
    if provider not in ("lmstudio", "openai-compatible") or not base_url:
        return False
    problem = _read_model_problem(base_url, model)
    if not problem:
        return False

    if on_wait is not None:
        on_wait(problem)
    _LOG.warning(f"the model server has no working model ({problem}): checking every {CHECK_EVERY_S} s for "
                 f"{WAIT_LIMIT_S} s; the call is asked again as soon as {model} works")
    deadline = _now() + WAIT_LIMIT_S
    while _now() < deadline:
        _pause(CHECK_EVERY_S)
        if not _read_model_problem(base_url, model):
            _LOG.warning(f"the model server has {model} working again: asking the call again")
            return True
    _LOG.warning(f"the model server has no working {model} after {WAIT_LIMIT_S} s")

    return False
