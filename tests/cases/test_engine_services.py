"""The engine services the generated suite left untested, and this project's small carriers.

REQUIREMENTS.md R6: no module ships without a test. The retry service is tested with its `sleep`
replaced, so the pauses it ASKS for are asserted and none is waited out.
"""

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage

from support import fixtures as fx
from support import harness

from models.attempt_request import AttemptRequest
from models.scene_result import SceneResult
from prompts import document_messages
from schemas import document_contents
from schemas import document_outline
from schemas import document_section
from services import cli_log_service
from services import model_retry_service
from services.ignore_list_service import IgnoreList

ENTRIES = [{"id": "E001", "kind": "speech", "text": "89 Prozent wollen billigeren Sprit."},
           {"id": "E002", "kind": "facts", "text": "89 %"}]


def _build_request(job_kind: str,
                   inputs: dict,
                   findings: list[dict] | None = None) -> AttemptRequest:
    paths = fx.make_paths()

    return AttemptRequest(job_kind=job_kind, attempt=1, repository=paths.repository, module=paths.module,
                          paths=paths, ignore=IgnoreList(), inputs=inputs, findings=findings or [])


def test_the_console_log_saves_what_the_runs_own_logger_writes():
    run_dir = Path(tempfile.mkdtemp())
    logger = logging.getLogger("test-console-log")
    logger.setLevel(logging.INFO)
    handler = cli_log_service.open_cli_log(run_dir, logger, now=datetime(2026, 9, 20, 12, 0, 0))
    logger.info("phase 1: transcribing")
    cli_log_service.close_cli_log(handler, logger=logger)
    logger.info("written after the file was closed")
    saved = Path(handler.baseFilename).read_text(encoding="utf-8")
    harness.check("a line logged while the file is open is saved", "phase 1: transcribing" in saved, saved)
    harness.check("a line logged after it closed is not", "after the file was closed" not in saved, saved)
    second = cli_log_service.build_cli_log_path(run_dir, datetime(2026, 9, 20, 12, 0, 0))
    harness.check("a second start in the same minute is a resume", second.name == "12-00-resume.log", str(second))
    second.write_text("", encoding="utf-8")
    third = cli_log_service.build_cli_log_path(run_dir, datetime(2026, 9, 20, 12, 0, 30))
    harness.check("a third start in that minute gets its own file", third.name == "12-00-resume-2.log", str(third))


def test_an_error_logged_while_the_console_log_is_open_is_saved_with_its_traceback():
    """A run stopped on an error nothing caught, and its console log ended mid-run with no word of why (2026-10-04)."""

    logger = logging.getLogger("test-console-log-error")
    logger.setLevel(logging.INFO)
    handler = cli_log_service.open_cli_log(Path(tempfile.mkdtemp()), logger)
    try:
        raise ValueError("a setting the host refuses")
    except ValueError:
        logger.exception("the run stopped on an error")
    cli_log_service.close_cli_log(handler, logger=logger)
    saved = Path(handler.baseFilename).read_text(encoding="utf-8")
    harness.check("the line, the traceback and the error are in the file", "the run stopped on an error" in saved
                  and "Traceback" in saved and "ValueError: a setting the host refuses" in saved, saved)


def test_a_dropped_connection_is_retried_and_the_pauses_are_the_ones_asked_for():
    pauses: list[float] = []
    calls = []

    def fail_twice_then_answer():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Server disconnected without sending a response")

        return "answered"

    settings = model_retry_service.RetrySettings(attempts=3, backoff_s=5.0, sleep=pauses.append)
    harness.check("the third try's answer is returned",
                  model_retry_service.call_with_transport_retry(fail_twice_then_answer, settings) == "answered")
    harness.check("it paused twice, doubling, and waited for neither", pauses == [5.0, 10.0], str(pauses))


def _refuse_the_request():
    raise ValueError("model does not support tools")


def test_an_error_that_is_the_models_own_is_raised_at_once():
    pauses: list[float] = []

    try:
        model_retry_service.call_with_transport_retry(
            _refuse_the_request, model_retry_service.RetrySettings(attempts=3, sleep=pauses.append))
    except ValueError:
        harness.check("an answer from the host is never retried", pauses == [], str(pauses))
    else:
        harness.check("the model's own error is raised", False)


def test_a_host_that_is_not_running_gets_the_long_wait():
    harness.check("a refused connection reads as a host that is down",
                  model_retry_service.looks_like_host_down(ConnectionError("[Errno 61] Connection refused")))
    harness.check("a dropped connection does not",
                  not model_retry_service.looks_like_host_down(RuntimeError("Server disconnected")))
    wrapped = RuntimeError("Connection error.")
    wrapped.__cause__ = ConnectionError("[Errno 61] Connection refused")
    harness.check("a client error that wraps a refused socket is a host down, so a restarting host is waited out",
                  model_retry_service.looks_like_host_down(wrapped))


def test_a_stopped_ollama_and_a_refused_httpx_connection_are_a_host_that_is_down_and_get_retried():
    class ConnectError(Exception):
        """What httpx raises when nothing answers."""

    stopped_ollama = ConnectionError("Failed to connect to Ollama. Please check that Ollama is downloaded, running and "
                                     "accessible. https://ollama.com/download")
    refused = ConnectError("All connection attempts failed")
    for label, error in (("Ollama's own message", stopped_ollama), ("httpx's connect error", refused)):
        harness.check(f"{label} is a failure of the transport",
                      model_retry_service.looks_like_transport_failure(error))
        harness.check(f"{label} is a host that is down", model_retry_service.looks_like_host_down(error))


def test_a_message_with_the_letters_eof_inside_a_word_is_not_a_dropped_connection():
    harness.check("an answer that says 'thereof' is the model's own",
                  not model_retry_service.looks_like_transport_failure(ValueError("not accepted: the rest thereof")))
    harness.check("an unexpected EOF still is a dropped connection",
                  model_retry_service.looks_like_transport_failure(EOFError("unexpected EOF while reading")))


def test_the_outline_message_hands_over_every_entry_and_every_contradiction():
    request = _build_request("outline", {"entries": ENTRIES, "contradictions": [{"text": "grey, not green"}]})
    text = document_messages.build_outline_messages(request)[1].content[0]["text"]
    harness.check("every entry is a line with its id and kind", "E001 [speech]" in text and "E002 [facts] 89 %" in text,
                  text)
    harness.check("the count is stated, so nothing can be dropped unnoticed", "2 entries" in text, text)
    harness.check("a contradiction between frames is handed over as not established", "grey, not green" in text, text)


def test_the_section_message_names_its_topic_the_others_and_what_was_rejected():
    finding = {"code": "DOCUMENT_BANNED_PHRASE", "detail": "the text holds 'video shows'", "scope": "target",
               "fix": "state the fact"}
    request = _build_request("section", {"documentTitle": "Fuel decides the vote", "topicTitle": "Fuel",
                                         "otherTopics": ["Turnout"], "entries": ENTRIES}, findings=[finding])
    messages = document_messages.build_section_messages(request)
    text = messages[1].content[0]["text"]
    harness.check("the system text forbids describing the recording", "meta-narration" in messages[0].content,
                  messages[0].content[:200])
    harness.check("and states the banned phrases from the contract", '"video shows"' in messages[0].content)
    harness.check("the topic and the other topics are named", "Fuel" in text and "Turnout" in text, text)
    harness.check("a retry is told what was rejected", "DOCUMENT_BANNED_PHRASE" in text, text)
    harness.check("and what was wrong, in the validator's own words", "the text holds 'video shows'" in text, text)
    harness.check("the message is text only", isinstance(messages[1], HumanMessage) and len(messages[1].content) == 1)


def test_the_document_schemas_name_their_sources():
    harness.check("an outline places entries and sets entries aside by name; the title is the contents' job",
                  {"topics", "set_aside_entry_ids", "not_established"}
                  == set(document_outline.DocumentOutline.model_fields))
    harness.check("the contents only title the document: the chapters are the meeting's own parts (R9)",
                  set(document_contents.DocumentContents.model_fields) == {"title"})
    harness.check("a section is the markdown its writer lays out, each block naming its entries in brackets",
                  set(document_section.DocumentSection.model_fields) == {"markdown"})


def test_a_scene_knows_its_length_and_its_middle_frame():
    scene = SceneResult(start_frame=100, end_frame=140)
    harness.check("a scene's length and representative frame", (scene.length, scene.representative_frame) == (40, 120))
