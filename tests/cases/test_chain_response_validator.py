"""The check on HOW a model call ended, read from the call log on disk. One case per code, and for
every code the ordinary response that merely resembles the broken one and must pass."""

import tempfile
from pathlib import Path

from support import harness

from services import call_log_service
from validators import chain_response_validator

COMPLETE_RESPONSE = {"outcome": "completed", "detail": "", "parsed": {"title": "a real record"}, "text": "",
            "thinking": "", "finishReason": "stop", "parsingError": ""}


def _write_call_log(**changes) -> Path:
    path = Path(tempfile.mkdtemp()) / "calls" / "item.json"
    call_log_service.write_call_log(path, {"response": {**COMPLETE_RESPONSE, **changes}})

    return path


def _list_codes(path: Path) -> set[str]:
    return harness.codes(chain_response_validator.validate_chain_response(path, "item"))


def test_a_complete_response_passes():
    harness.check("a parsed response that ended on its own raises no finding",
                  _list_codes(_write_call_log()) == set())


def test_a_long_thought_beside_a_parsed_answer_passes():
    """The lookalike of THINKING_ONLY: thinking is welcome, it is its being ALONE that is the defect."""

    harness.check("thinking beside a parsed answer is not a finding",
                  _list_codes(_write_call_log(thinking="x" * 4000)) == set())


def test_no_call_log_is_its_own_code():
    missing = Path(tempfile.mkdtemp()) / "calls" / "never-written.json"
    harness.check("a call that left no log is CALL_LOG_MISSING",
                  _list_codes(missing) == {"CALL_LOG_MISSING"})


def test_a_failed_call_is_reported_as_a_failed_call():
    path = _write_call_log(outcome="error", detail="ConnectError: host refused", parsed=None)
    harness.check("a call that raised is MODEL_CALL_FAILED", _list_codes(path) == {"MODEL_CALL_FAILED"})


def test_a_response_cut_at_the_output_limit_is_a_limit_not_an_answer():
    path = _write_call_log(finishReason="length", parsed=None, thinking="planning the whole record")
    harness.check("a cut response is RESPONSE_CUT_AT_LIMIT", _list_codes(path) == {"RESPONSE_CUT_AT_LIMIT"})

    # A response that was cut AFTER its answer parsed is still a cut: the record may be short of
    # fields the schema allows to be empty, and nothing else will ever say so.
    path = _write_call_log(finishReason="length")
    harness.check("a cut response is a finding even when something parsed",
                  _list_codes(path) == {"RESPONSE_CUT_AT_LIMIT"})


def test_a_reply_the_program_stopped_as_a_loop_is_named_a_loop():
    """Four loop stops in one run were refused as an empty critique, an empty fix and a cut "after None output tokens"
    (2026-10-13)."""

    path = _write_call_log(finishReason="thinking_looped", parsed=None, thinking="check it again " * 100, outputTokens=None)
    findings = chain_response_validator.validate_chain_response(path, "item").findings
    harness.check("a loop stop is RESPONSE_THINKING_LOOPED", [f.code for f in findings] == ["RESPONSE_THINKING_LOOPED"],
                  str(findings))
    harness.check("it counts the thinking, never None tokens", "1,500 characters" in findings[0].detail
                  and "None" not in findings[0].detail, findings[0].detail)


def test_thinking_with_no_answer_is_named_for_what_it_is():
    path = _write_call_log(parsed=None, thinking="a thousand characters of reasoning")
    harness.check("thinking and nothing else is RESPONSE_THINKING_ONLY",
                  _list_codes(path) == {"RESPONSE_THINKING_ONLY"})


def test_prose_where_a_structure_was_asked_for_is_no_structured_output():
    path = _write_call_log(parsed=None, text="Here is the record you asked for: ...",
                           parsingError="Invalid json output")
    harness.check("an unparsed answer is MODEL_NO_STRUCTURED_OUTPUT",
                  _list_codes(path) == {"MODEL_NO_STRUCTURED_OUTPUT"})


def test_every_finding_says_what_to_do():
    path = _write_call_log(finishReason="length", parsed=None)
    report = chain_response_validator.validate_chain_response(path, "item")
    harness.check("an answer cut at the length limit has a finding", bool(report.findings),
                  str([finding.as_dict() for finding in report.findings]))
    harness.check("every finding carries a fix", all(finding.fix for finding in report.findings),
                  str([finding.as_dict() for finding in report.findings]))


def test_an_answer_that_looped_with_no_thinking_is_named_by_its_answer_defect():
    # A critique's answer written from a compaction's summary, thinking nothing, looped in its text and was refused
    # "after 0 characters of thinking" (DEFECTS 343); it is now named by its answer's length
    path = _write_call_log(finishReason="thinking_looped", parsed=None, thinking="", text="Let's look at frame 4 again. " * 100,
                           outputTokens=None)
    detail = chain_response_validator.validate_chain_response(path, "item").findings[0].detail
    harness.check("the finding names the answer and its length, never thinking", "2,900 characters of its answer" in detail
                  and "thinking" not in detail, detail)

