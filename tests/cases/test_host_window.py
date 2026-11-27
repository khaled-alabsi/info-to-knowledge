"""The context window the model server reports for a model, read from its standard model list. REQUIREMENTS.md R12.

A server fixes the window when it loads a model, and may say so in the model's entry at `GET /v1/models`; a window typed
into the settings can be wrong without anything noticing, and a prompt past it is cut silently. The server here is the
stand-in server on port 0, with the listing each case needs.
"""

import json

from support import harness
from support import fake_model_server

import config

from services import host_window_service

from cases import test_recording_in_run_folder as recording_tests

MODEL = "qwen-test"
OTHER_MODEL = "other-model"
LOADED_WINDOW = 163_840
# The windows tried against the settings' own: one larger, one smaller
LARGER_BY = 19_200
SMALLER_BY = 32_768
PHASE_NUMBER = 4


def _build_listing(window_entry: dict) -> bytes:
    """The server's model list: OTHER_MODEL with a small window, and MODEL with the fields of `window_entry`."""

    entry_list = [{"id": OTHER_MODEL, "object": "model", "loaded_context_length": 8_192},
                  {"id": MODEL, "object": "model", **window_entry}]

    return json.dumps({"object": "list", "data": entry_list}).encode("utf-8")


def _serve_listing(listing: bytes) -> fake_model_server.FakeModelServer:
    server = fake_model_server.FakeModelServer()
    server.set_reply("/v1/models", 200, listing)

    return server


def _read_window(server: fake_model_server.FakeModelServer,
                 model: str = MODEL):
    """The window read_loaded_window gives for `model` on `server`, or "raised X" when it raises, so a check in a loop
    still reports the other answers."""

    with fake_model_server.serving(server):
        try:
            return host_window_service.read_loaded_window(server.build_url(), model)
        except Exception as exc:  # noqa: BLE001 (the finding is that it raises)
            return f"raised {type(exc).__name__}"


def _resolve_window_with_loaded(loaded_window):
    server = _serve_listing(_build_listing({"loaded_context_length": loaded_window}))
    logger, handler = recording_tests._build_logger()
    role = config.ModelSettings(spec=f"openai-compatible:{MODEL}", base_url=server.build_url())
    with fake_model_server.serving(server):
        window = host_window_service.resolve_conversation_window(logger, role, PHASE_NUMBER)

    return window, handler.lines


def test_the_loaded_window_is_read_from_the_standard_model_list():
    server = _serve_listing(_build_listing({"loaded_context_length": LOADED_WINDOW}))
    window = _read_window(server)
    harness.check(f"the loaded window of {MODEL}", window == LOADED_WINDOW, str(window))
    harness.check("asked at the standard model list only",
                  [(request.method, request.path) for request in server.request_list] == [("GET", "/v1/models")],
                  str([(request.method, request.path) for request in server.request_list]))


def test_a_window_is_read_under_each_name_a_server_gives_it():
    for field in ("loaded_context_length", "max_model_len", "context_length"):
        window = _read_window(_serve_listing(_build_listing({field: LOADED_WINDOW})))
        harness.check(f"a window under '{field}' is read", window == LOADED_WINDOW, str(window))


def test_an_unknown_model_or_one_with_no_window_or_a_silent_server_gives_none():
    absent = _read_window(_serve_listing(_build_listing({})), "absent")
    harness.check("a model not in the list", absent is None, str(absent))
    plain = _read_window(_serve_listing(_build_listing({})))
    harness.check("a model listed with no window, as the standard reply has it", plain is None, str(plain))
    # A port nothing listens on: the stand-in server is built, bound and closed again before the call
    closed = fake_model_server.FakeModelServer()
    closed_url = closed.build_url()
    closed.server_close()
    silent = host_window_service.read_loaded_window(closed_url, MODEL)
    harness.check("a server that does not answer", silent is None, str(silent))


def test_the_conversation_keeps_within_the_window_the_server_loaded_larger_smaller_or_the_same():
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


def test_a_server_that_reports_no_window_leaves_the_configured_one_with_a_warning():
    server = _serve_listing(_build_listing({}))
    logger, handler = recording_tests._build_logger()
    role = config.ModelSettings(spec=f"openai-compatible:{MODEL}", base_url=server.build_url())
    with fake_model_server.serving(server):
        window = host_window_service.resolve_conversation_window(logger, role, PHASE_NUMBER)
    harness.check("the configured window is used", window == config.settings.document.conversation_window_tokens, str(window))
    harness.check("the console says the server reported none", len(handler.lines) == 1 and "did not report" in handler.lines[0],
                  str(handler.lines))


def test_a_server_answer_of_the_wrong_shape_is_no_window():
    for body in (b"[]", b'"loaded"', b'{"data": ["qwen-test"]}', b"not json"):
        window = _read_window(_serve_listing(body))
        harness.check(f"{body!r} gives no window", window is None, str(window))


def test_a_loaded_window_that_is_no_number_is_no_window():
    window = _read_window(_serve_listing(_build_listing({"loaded_context_length": "large"})))
    harness.check("a window that is no number gives none", window is None, str(window))
