"""Waiting for a model host that lost its model while a call ran. REQUIREMENTS.md R12.

The server's model list is the stand-in server on port 0; the clock, the pause between checks and the one-token test call
are the module's seams, swapped here for fakes, so no check waits. A pause is where the stand-in server's list moves to
the next state, so the list one check sees is the one the test meant for it.
"""

import contextlib
import json
from collections.abc import Callable, Iterator

from support import harness
from support import fake_model_server

import config

from services import chat_model_service
from services import host_return_service

PHASE = "frames_processing"


def _model() -> str:
    role = config.settings.resolve_model(config.settings.resolve_phase_model_name(PHASE))

    return chat_model_service.split_spec(role.spec)[1]


def _set_listing(server: fake_model_server.FakeModelServer,
                 is_listed: bool) -> None:
    """The server's model list with the phase's model in it, or empty."""

    entry_list = [{"id": _model(), "object": "model"}] if is_listed else []
    server.set_reply("/v1/models", 200, json.dumps({"object": "list", "data": entry_list}).encode("utf-8"))


@contextlib.contextmanager
def _fake_host(states: list[bool],
               ask: Callable[[str, str], str],
               pauses: list[float],
               now_ref: list[float] | None = None) -> Iterator[fake_model_server.FakeModelServer]:
    """The stand-in server listing the model per `states` (one state per check, the last kept), the phase's model pointed
    at it, and the clock, the pause and the test call swapped for fakes. Every pause is added to `pauses`."""

    server = fake_model_server.FakeModelServer()
    clock = now_ref if now_ref is not None else [0.0]
    state_iter = iter(states[1:])
    _set_listing(server, states[0])

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        clock[0] += seconds
        _set_listing(server, next(state_iter, states[-1]))

    model_name = config.settings.resolve_phase_model_name(PHASE)
    saved_models = dict(config.settings.models)
    saved_phase_models = dict(config.settings.phase_models)
    config.settings.models[model_name] = config.ModelSettings(spec=f"openai-compatible:{_model()}", base_url=server.build_url())
    previous_clock = host_return_service.set_clock(lambda: clock[0], pause)
    previous_ask = host_return_service.set_test_call(ask)
    try:
        with fake_model_server.serving(server):
            yield server
    finally:
        host_return_service.set_clock(*previous_clock)
        host_return_service.set_test_call(previous_ask)
        config.settings.models.clear()
        config.settings.models.update(saved_models)
        config.settings.phase_models.clear()
        config.settings.phase_models.update(saved_phase_models)


def _wait_with(states: list[bool],
               test_calls: list[bool] | None = None,
               reasons: list[str] | None = None) -> tuple[bool, int]:
    """What wait_for_lost_model answers when the server lists the model or not, one state per check, the last state kept
    after the list; and how many pauses it took."""

    pauses: list[float] = []
    answers = iter(test_calls or [])
    on_wait = reasons.append if reasons is not None else None
    with _fake_host(states, lambda base_url, model: "" if next(answers, True) else "Compute error.", pauses):
        return host_return_service.wait_for_lost_model(PHASE, on_wait=on_wait), len(pauses)


def test_a_call_lost_with_its_model_is_asked_again_once_the_model_is_back_defect():
    # A machine restarted under release test 1 ended the second pass's call, and its two retries failed in seconds on a
    # host with no model loaded: three attempts spent and phase 3 stopped. Now the run waits for the model to be listed.
    is_back, pauses = _wait_with([False, False, False, True])
    harness.check("the call is asked again once the server lists the model", is_back is True, str(is_back))
    harness.check("the server is asked again after each pause", pauses == 3, str(pauses))


def test_a_model_listed_that_fails_every_call_is_waited_for_defect():
    # After LM Studio restarted, its loaded model answered every call with an engine error while listed, so the job
    # ended in seconds and the queue ran 12 more into it. Now a test call says so, and the job waits for a working model.
    is_back, pauses = _wait_with([True], test_calls=[False, False, True])
    harness.check("the call is asked again once the model answers", is_back is True, str(is_back))
    harness.check("the test call is made again after each pause", pauses == 2, str(pauses))


def test_the_wait_tells_its_caller_why_the_model_does_not_work_defect():
    # Defect: LM Studio listed its model READY while every call failed with "Compute error"; the run waited 30 minutes
    # and the page said the model was writing. Fix: the wait hands its caller the host's own error before it waits
    reasons: list[str] = []
    is_back, _ = _wait_with([True], test_calls=[False, True], reasons=reasons)
    harness.check("the call is asked again once the model answers", is_back is True, str(is_back))
    harness.check("the caller is told the host's error once", len(reasons) == 1 and "Compute error" in reasons[0],
                  str(reasons))


def test_the_wait_says_a_model_the_server_does_not_list_is_not_listed_defect():
    # Defect: as above, a wait with no reason on the page. Fix: a model missing from the server's list is named
    reasons: list[str] = []
    is_back, _ = _wait_with([False, True], reasons=reasons)
    harness.check("the caller is told the server does not list the model",
                  is_back is True and len(reasons) == 1 and "not listed" in reasons[0], str(reasons))


def test_the_program_never_loads_or_unloads_a_model_defect():
    # Defect: the wait called LM Studio's own load and unload requests (`/api/v1/models/...`), which no other server has,
    # so a run on any other server could not wait for its model. Fix: the wait only lists models and sends one-token calls
    pauses: list[float] = []
    with _fake_host([True], lambda base_url, model: "", pauses) as server:
        host_return_service.wait_for_lost_model(PHASE)
        paths = [request.path for request in server.request_list]
    harness.check("only the standard model list was asked", paths == ["/v1/models"], str(paths))


def test_a_call_failing_with_its_model_listed_waits_for_nothing():
    is_back, pauses = _wait_with([True])
    harness.check("a server still listing the model gives the failure back at once", is_back is False, str(is_back))
    harness.check("and no pause is taken", pauses == 0, str(pauses))


def test_a_model_never_back_ends_the_wait_at_its_limit():
    is_back, pauses = _wait_with([False])
    limit = host_return_service.WAIT_LIMIT_S // host_return_service.CHECK_EVERY_S
    harness.check("a model not back within the limit gives the failure back", is_back is False, str(is_back))
    harness.check("the wait ends at its limit", pauses == limit, f"{pauses} pauses, limit {limit}")


def test_a_model_not_back_ends_the_wait_after_one_minute_defect():
    # Defect: a model unloaded under a run held the job 30 minutes before it failed, too long to the owner. Fix: the
    # host is checked every 15 s for one minute, then the call fails and the queue's own retry takes over
    is_back, pauses = _wait_with([False])
    harness.check("the wait gives up after one minute of checks every 15 s",
                  is_back is False and pauses * host_return_service.CHECK_EVERY_S == 60, f"{pauses} pauses")


def test_a_server_that_does_not_answer_is_waited_for():
    pauses: list[float] = []
    reasons: list[str] = []
    with _fake_host([True], lambda base_url, model: "", pauses) as server:
        # the first check meets a server that answers HTTP 503; the next, after a pause, the model list
        server.set_reply("/v1/models", 503, b'{"error": {"message": "loading"}}')
        previous = host_return_service.set_clock(lambda: 0.0, lambda seconds: (pauses.append(seconds), _set_listing(server, True)))
        try:
            is_back = host_return_service.wait_for_lost_model(PHASE, on_wait=reasons.append)
        finally:
            host_return_service.set_clock(*previous)
    harness.check("a server down, then up with the model, is waited for", is_back is True, str(is_back))
    harness.check("the caller is told the server does not answer", len(reasons) == 1 and "does not answer" in reasons[0],
                  str(reasons))
