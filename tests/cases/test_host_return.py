"""Waiting for a model host that lost its model while a call ran. REQUIREMENTS.md R12.

The host's listing and the clock are the module's seams, swapped here for fakes, so no check waits.
"""

import contextlib
from collections.abc import Callable, Iterator

from support import harness

import config

from services import chat_model_service
from services import host_return_service
from services import host_window_service

PHASE = "frames_processing"
WINDOW = 183_040


def _model() -> str:
    role = config.settings.resolve_model(config.settings.resolve_phase_model_name(PHASE))

    return chat_model_service.split_spec(role.spec)[1]


def _listing(is_loaded: bool) -> dict:
    return {"data": [{"id": _model(), "state": "loaded" if is_loaded else "not-loaded",
                      "loaded_context_length": WINDOW}]}


def _wait_with(states: list[bool],
               test_calls: list[bool] | None = None) -> tuple[bool, int]:
    """What wait_for_lost_model answers when the host lists the model loaded or not, one state per check, the last
    state kept after the list; and how many pauses it took."""

    checks, pauses, clock = iter(states), [], [0.0]

    def fetch(url):
        nonlocal last
        last = next(checks, last)
        return _listing(last)

    def pause(seconds):
        pauses.append(seconds)
        clock[0] += seconds

    answers = iter(test_calls or [])
    last = states[0]
    with _fake_host(fetch, lambda base_url, model: "" if next(answers, True) else "Compute error.", pause,
                    now=lambda: clock[0]):
        return host_return_service.wait_for_lost_model(PHASE), len(pauses)


# Every load the wait asked for, as (model, window): the tests never reach a real host
LOADS: list[tuple[str, int]] = []


def _record_load(base_url: str,
                 model: str,
                 window: int) -> str:
    LOADS.append((model, window))

    return ""


def test_a_call_lost_with_its_model_is_asked_again_once_the_model_is_back_defect():
    # A machine restarted under release test 1 ended the second pass's call, and its two retries failed in seconds on a
    # host with no model loaded: three attempts spent and phase 3 stopped. Now the run waits for the model to be loaded.
    is_back, pauses = _wait_with([False, False, False, True])
    harness.check("the call is asked again once the host lists the model loaded", is_back is True, str(is_back))
    harness.check("the host is asked again after each pause", pauses == 3, str(pauses))


def test_a_model_listed_loaded_that_fails_every_call_is_waited_for_defect():
    # After LM Studio restarted, its loaded model answered every call with an engine error while listed loaded, so the
    # job ended in seconds and the queue ran 12 more into it. Now a test call says so, and the job waits for a working model.
    is_back, pauses = _wait_with([True], test_calls=[False, False, True])
    harness.check("the call is asked again once the model answers", is_back is True, str(is_back))
    harness.check("the test call is made again after each pause", pauses == 2, str(pauses))


def test_the_wait_tells_its_caller_why_the_model_does_not_work_defect():
    # Defect: LM Studio listed its model READY while every call failed with "Compute error"; the run waited 30 minutes
    # and the page said the model was writing. Fix: the wait hands its caller the host's own error before it waits
    reasons = []
    answers = iter(["Compute error.", ""])
    with _fake_host(lambda url: _listing(True), lambda base_url, model: next(answers), lambda seconds: None):
        is_back = host_return_service.wait_for_lost_model(PHASE, on_wait=reasons.append)
    harness.check("the call is asked again once the model answers", is_back is True, str(is_back))
    harness.check("the caller is told the host's error once", len(reasons) == 1 and "Compute error" in reasons[0],
                  str(reasons))


def test_the_wait_says_a_model_the_host_does_not_list_is_not_loaded_defect():
    # Defect: as above, a wait with no reason on the page. Fix: a model missing from the host's list is named not loaded
    reasons = []
    is_back, _ = _wait_with_reasons([False, True], reasons)
    harness.check("the caller is told the model is not loaded", is_back is True and len(reasons) == 1
                  and "not loaded" in reasons[0], str(reasons))


def _wait_with_reasons(states: list[bool],
                       reasons: list[str]) -> tuple[bool, int]:
    checks, pauses = iter(states), []
    with _fake_host(lambda url: _listing(next(checks, states[-1])), lambda base_url, model: "", pauses.append):
        return host_return_service.wait_for_lost_model(PHASE, on_wait=reasons.append), len(pauses)


@contextlib.contextmanager
def _fake_host(fetch: Callable[[str], dict],
               ask: Callable[[str, str], str],
               pause: Callable[[float], None],
               now: Callable[[], float] = lambda: 0.0) -> Iterator[None]:
    """The host's listing, its test call, the clock and the pause between checks swapped for fakes, and every load
    recorded in LOADS, never sent."""

    previous_fetch = host_window_service.set_fetch_json(fetch)
    previous_clock = host_return_service.set_clock(now, pause)
    previous_ask = host_return_service.set_test_call(ask)
    previous_load = host_return_service.set_model_loader(_record_load)
    try:
        yield
    finally:
        host_window_service.set_fetch_json(previous_fetch)
        host_return_service.set_clock(*previous_clock)
        host_return_service.set_test_call(previous_ask)
        host_return_service.set_model_loader(previous_load)


def test_a_model_that_does_not_work_is_loaded_again_by_the_program_defect():
    # Defect: a run waited for someone to load its model again by hand. Fix: the wait itself unloads and loads the
    # LM Studio model, at its configured window and one call at a time, then checks it
    LOADS.clear()
    is_back, _ = _wait_with([True], test_calls=[False, True])
    window = config.settings.resolve_model(config.settings.resolve_phase_model_name(PHASE)).num_ctx
    harness.check("the model is loaded once, at its configured window, and the call asked again",
                  is_back is True and LOADS == [(_model(), window)], str(LOADS))


def test_a_call_failing_with_its_model_loaded_waits_for_nothing():
    is_back, pauses = _wait_with([True])
    harness.check("a host still listing the model gives the failure back at once", is_back is False, str(is_back))
    harness.check("and no pause is taken", pauses == 0, str(pauses))


def test_a_model_never_loaded_again_ends_the_wait_at_its_limit():
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


def test_a_host_that_does_not_answer_is_waited_for():
    answers = iter([OSError("refused"), OSError("refused"), _listing(True)])

    def fetch(url):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with _fake_host(fetch, lambda base_url, model: "", lambda seconds: None):
        is_back = host_return_service.wait_for_lost_model(PHASE)
    harness.check("a host down, then up with the model, is waited for", is_back is True, str(is_back))
