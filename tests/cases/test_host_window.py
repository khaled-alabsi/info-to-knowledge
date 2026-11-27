"""The context window the model host actually loaded, read from the host. REQUIREMENTS.md R12.

LM Studio fixes the window when it loads a model, and says so in its own model listing; a window typed into the
settings can be wrong without anything noticing, and a prompt past it is cut silently. The fetch is the module's own
seam, swapped here for a fake.
"""

from support import harness

import config

from services import host_window_service

from cases import test_recording_in_run_folder as recording_tests

MODEL = "qwen-test"
OTHER_MODEL = "other-model"
LOADED_WINDOW = 163_840
LISTING = {"data": [{"id": OTHER_MODEL, "state": "not-loaded", "loaded_context_length": 8_192, "max_context_length": 8_192},
                    {"id": MODEL, "state": "loaded", "loaded_context_length": LOADED_WINDOW, "max_context_length": 262_144}]}
HOST_URL = "http://localhost:1234/v1"
PHASE_NUMBER = 4
# The loaded windows tried against the settings' own: one larger, one smaller
LARGER_BY = 19_200
SMALLER_BY = 32_768


def _run_with_fetch(fetch, call):
    previous = host_window_service.set_fetch_json(fetch)
    try:
        return call()
    finally:
        host_window_service.set_fetch_json(previous)


def _read_window(fetch, model: str = MODEL):
    """The window read_loaded_window gives for `model` with `fetch` as the host, or "raised X" when it raises, so a
    check in a loop still reports the other answers."""

    try:
        return _run_with_fetch(fetch, lambda: host_window_service.read_loaded_window(HOST_URL, model))
    except Exception as exc:  # noqa: BLE001 (the finding is that it raises)
        return f"raised {type(exc).__name__}"


def _refuse_connection(url: str):
    raise OSError("connection refused")


def _build_listing(loaded_window) -> dict:
    """The host's listing with MODEL loaded at `loaded_window`."""

    return {"data": [{"id": MODEL, "state": "loaded", "loaded_context_length": loaded_window}]}


def _resolve_window_with_loaded(loaded_window):
    listing = _build_listing(loaded_window)
    logger, handler = recording_tests._build_logger()
    role = config.ModelSettings(spec=f"lmstudio:{MODEL}")
    window = _run_with_fetch(lambda url: listing,
                             lambda: host_window_service.resolve_conversation_window(logger, role, PHASE_NUMBER))
    return window, handler.lines


def test_the_loaded_window_is_read_from_the_host_listing():
    seen = []
    window = _read_window(lambda url: seen.append(url) or LISTING)
    harness.check(f"the loaded window of {MODEL}", window == LOADED_WINDOW, str(window))
    harness.check("asked at the host's own listing, not the OpenAI path", seen == ["http://localhost:1234/api/v0/models"],
                  str(seen))


def test_an_unknown_or_unloaded_model_or_a_silent_host_gives_none():
    absent = _read_window(lambda url: LISTING, "absent")
    harness.check("a model not in the listing", absent is None, str(absent))
    unloaded = _read_window(lambda url: LISTING, OTHER_MODEL)
    harness.check("a model listed but not loaded", unloaded is None, str(unloaded))
    silent = _read_window(_refuse_connection)
    harness.check("a host that does not answer", silent is None, str(silent))


def test_the_conversation_keeps_within_the_window_lm_studio_loaded_larger_smaller_or_the_same():
    configured = config.settings.document.conversation_window_tokens
    larger_window, smaller_window = configured + LARGER_BY, configured - SMALLER_BY
    # Larger: the settings' smaller window put the size limit on the number the host reports for a cut prompt
    # (PROMPT_SUSPECTED_CUT), so a chapter was blocked on a host that cut nothing
    larger, larger_warnings = _resolve_window_with_loaded(larger_window)
    harness.check("a larger loaded window is used", larger == larger_window, str(larger))
    harness.check("a larger window is said once on the console, with its number",
                  len(larger_warnings) == 1 and f"{larger_window:,}" in larger_warnings[0], str(larger_warnings))
    # Smaller: a prompt past the real window is cut without an error
    smaller, smaller_warnings = _resolve_window_with_loaded(smaller_window)
    harness.check("a smaller loaded window is used", smaller == smaller_window, str(smaller))
    harness.check("a smaller window is said once on the console, with its number",
                  len(smaller_warnings) == 1 and f"{smaller_window:,}" in smaller_warnings[0], str(smaller_warnings))
    # The same: nothing to say
    same, same_warnings = _resolve_window_with_loaded(configured)
    harness.check("the same window is used", same == configured, str(same))
    harness.check("the same window says nothing", not same_warnings, str(same_warnings))


def test_a_host_answer_of_the_wrong_shape_is_no_window():
    for answer in ([], "loaded", {"data": [MODEL]}):
        window = _read_window(lambda url, answer=answer: answer)
        harness.check(f"{answer!r} gives no window", window is None, str(window))


def test_a_loaded_window_that_is_no_number_is_no_window():
    window = _read_window(lambda url: _build_listing("large"))
    harness.check("a window that is no number gives none", window is None, str(window))
