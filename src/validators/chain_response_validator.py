"""How one model call ENDED, judged from the call log on disk. A pure function over bytes.

This runs before any check on the record, and a finding here is an early return: a call that was
cut short, or that produced thinking and no answer, has no record worth reading. Whatever record
an EARLIER attempt left on disk must not be judged as this attempt's.

Every code is a different cause with a different fix, which is why "the answer was empty" is not
one code. An empty answer after a `length` finish is an output budget too small for thinking plus
answer. An empty answer after a `stop` finish with thinking on disk is a model that never got to
the answer. An answer in prose is a model that ignored the structure it was asked for.
"""

from __future__ import annotations

from pathlib import Path

from agent_client import finish_reason_utils
from models import call_outcome_vocabulary
from models.validation_report import ValidationReport
from services import call_log_service

__all__ = ["validate_chain_response"]


def validate_chain_response(call_log_path: Path,
                            subject: str) -> ValidationReport:
    report = ValidationReport(subject=subject)

    # --- existence: always first, and always an early return ---------------------------------
    report.mark_ran("CALL_LOG_MISSING")
    if not call_log_path.is_file() or not call_log_path.stat().st_size:
        report.add("CALL_LOG_MISSING", f"{subject}: no model call was logged for this attempt",
                   fix="this is a playbook bug: the call node must write the call log before validation")

        return report

    response = call_log_service.read_call_log(call_log_path).get("response", {})

    report.mark_ran("MODEL_CALL_FAILED")
    if response.get("outcome") != call_outcome_vocabulary.CALL_COMPLETED:
        report.add("MODEL_CALL_FAILED", f"{subject}: the model call raised: {response.get('detail', '')}",
                   fix="check that the model host is running and that the role's model is installed")

        return report

    # A loop the program stopped is named as one: reported as a cut "after None output tokens", it read as a limit
    # to raise, and the follow-ups' own findings called it an empty answer (lecture-1, 2026-10-13: 4 of 4 rejections)
    report.mark_ran("RESPONSE_THINKING_LOOPED")
    if response.get("finishReason") == finish_reason_utils.THINKING_LOOPED:
        # A call that thinks nothing (an answer written from a compaction's summary) loops in its answer: named so, never
        # as "0 characters of thinking" (DEFECTS 343, release test 1's critique of frames 101 to 103)
        thinking, text = str(response.get("thinking") or ""), str(response.get("text") or "")
        looped = (f"{len(thinking):,} characters of thinking, which repeated the same passage" if thinking.strip()
                  else f"{len(text):,} characters of its answer, which repeated the same passage")
        report.add("RESPONSE_THINKING_LOOPED", f"{subject}: the program stopped the reply after {looped}",
                   fix="the next attempt opens with what that thinking settled: answer from it, and write the answer "
                       "once a point is decided instead of checking it again")

        return report
    report.mark_ran("RESPONSE_CUT_AT_LIMIT")
    if finish_reason_utils.is_finish_reason_cut_at_output_limit(response.get("finishReason")):
        report.add("RESPONSE_CUT_AT_LIMIT",
                   f"{subject}: the host ended the reply at its output limit after "
                   f"{response.get('outputTokens')} output tokens",
                   fix="raise the role's output limit: it bounds thinking plus answer, and thinking is spent first")

        return report

    report.mark_ran("RESPONSE_THINKING_ONLY")
    report.mark_ran("MODEL_NO_STRUCTURED_OUTPUT")
    if response.get("parsed") is None:
        if response.get("thinking") and not response.get("text"):
            report.add("RESPONSE_THINKING_ONLY",
                       f"{subject}: the reply holds {len(response['thinking'])} characters of thinking "
                       "and no answer",
                       fix="answer with the structure you were asked for, not only with reasoning")
        else:
            report.add("MODEL_NO_STRUCTURED_OUTPUT",
                       f"{subject}: the reply could not be read as the answer asked for "
                       f"({response.get('parsingError') or 'nothing was returned'})",
                       fix="answer in the shape your instructions give, once, with every part they ask for")

    return report
