"""Every phase 3 batch and follow-up of a run folder, for the Frames card of the detail view: its time, its tokens,
its result, and each rejection with why. Read from the run's own logs (`logs/<run id>/performance.jsonl` and each
attempt's `validation.json`), never from a console, so a server restart or a resume loses none of it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models import output_contract
from services import attempt_log_service
from services import batch_summaries_service
from services import performance_log_service
from utils import json_store_utils

__all__ = ["describe_critique_result", "list_batches"]

# The phase whose batches are listed, as the performance log names it
_PHASE = "frames_processing"
# A unit's slug: its frames (`frames-083-086`, `frame-012`) and the follow-up it is, if any (`-critique`), with the round
# of a repair after the first (`-repair-2`)
_SLUG = re.compile(r"^frames?-(\d+)(?:-(\d+))?(?:-([a-z-]+?)(?:-\d+)?)?$")
# A fast-processing run's session (`fast-notes-000-004`): its frames described one by one, with no check
_FAST_SESSION_SLUG = re.compile(r"^fast-notes-(\d+)-(\d+)$")
_FAST_NOTES = "frame notes"
# The follow-up a slug ends with -> the step's name as the console prints it
_READING, _CRITIQUE, _FIX, _OPEN_QUESTIONS, _ANSWERS, _ALONE, _REPEATED = (
    "reading", "critique", "fix from critique", "open questions", "answers", "batch account", "asked again")
_STEP_NAMES = {"": _READING, batch_summaries_service.CRITIQUE_SUFFIX: _CRITIQUE,
               batch_summaries_service.SECOND_PASS_SUFFIX: _FIX,
               batch_summaries_service.OPEN_QUESTIONS_SUFFIX: _OPEN_QUESTIONS,
               batch_summaries_service.ANSWERS_SUFFIX: _ANSWERS, "summary": "account", "alone": _ALONE,
               "repair": _REPEATED}


# A critique's result as runs before the wording changed recorded it: "3 rules on 1 of 2 frames", "nothing to criticise"
_OLD_CRITIQUE_RESULT = re.compile(r"^(\d+) rules? on (\d+) of (\d+) frames$")
# The results the other steps record, as `process_frames_node` words them
_READING_RESULT = re.compile(r"^(\d+) of (\d+) accepted$")
_FIX_RESULT = re.compile(r"^(\d+) of (\d+) frames gained notes(?:, (\d+) dropped)?$")
_OPEN_QUESTIONS_RESULT = re.compile(r"^(\d+) found$")
_ANSWERS_RESULT = re.compile(r"^(\d+) answered, (\d+) stay open$")
# The frames a reading result names as refused: `(frame 5)`, `(frames 5, 7)`
_REFUSED_FRAMES = re.compile(r"\((frames? [\d, ]+)\)")


def _describe_slug(slug: str) -> tuple[str, str]:
    """`(frames, step)`: the frame span ("83–86", or one number) and the step's name."""

    session = _FAST_SESSION_SLUG.match(slug)
    if session:
        return f"{int(session.group(1))}–{int(session.group(2))}", _FAST_NOTES
    match = _SLUG.match(slug)
    if not match:
        return "", slug
    first, last, follow_up = int(match.group(1)), match.group(2), match.group(3) or ""
    frames = f"{first}–{int(last)}" if last and int(last) != first else str(first)

    return frames, _STEP_NAMES.get(follow_up, follow_up.replace("-", " "))


def _read_attempt_order(folder_name: str) -> tuple[int, int]:
    """An attempt folder's number and, for an attempt asked again after a resume, its re-entry: `attempt-2` is (2, 0),
    `attempt-1.2` is (1, 2) (`attempt_log_service`). Read as one whole number, `1.1` broke the page's Frames card."""

    attempt, _, reentry = folder_name.split("-")[-1].partition(".")

    return int(attempt), int(reentry or 0)


def _read_rejections(run_dir: Path,
                     slug: str) -> list[dict[str, Any]]:
    """Each rejected attempt of the unit, by attempt number, with the code and the words of each finding."""

    rejections = []
    for path in sorted((run_dir / _PHASE / slug).glob(f"attempt-*/{attempt_log_service.VALIDATION_FILE}"),
                       key=lambda path: _read_attempt_order(path.parent.name)):
        try:
            report = json_store_utils.read_json(path)
        except (OSError, json.JSONDecodeError):
            # A report a kill cut in half is no rejection to show: the attempt never finished
            continue
        if report.get("ok") is False:
            rejections.append({"attempt": _read_attempt_order(path.parent.name)[0],
                               "findings": [{"code": str(finding.get("code") or ""), "detail": str(finding.get("detail") or "")}
                                            for finding in report.get("findings") or []]})

    return rejections


def _format_plural(count: int,
               word: str) -> str:
    """`word`, with an `s` unless `count` is 1."""

    return word if count == 1 else f"{word}s"


def _format_count(count: int,
                  word: str) -> str:
    return f"{count} {_format_plural(count, word)}"


def _describe_reading_result(status: str,
                             is_repair: bool) -> str:
    """The result of reading, as a sentence: `2 of 3 accepted → retried together (frame 5)` becomes "notes of 2 of 3
    frames accepted; frame 5 refused, asked again"."""

    head, _, tail = status.partition(" → ")
    match = _READING_RESULT.match(head)
    if not match:
        return status
    text = f"{'asked again: ' if is_repair else ''}notes of {match.group(1)} of {match.group(2)} frames accepted"
    refused = _REFUSED_FRAMES.search(tail)
    if refused and tail.startswith("blocked"):
        text += f"; {refused.group(1)} refused, the run stopped there"
    elif refused and tail.startswith("retried"):
        text += f"; {refused.group(1)} refused, asked again"

    return text


def _describe_status(step: str,
                     status: str) -> str:
    """A step's result as one plain sentence, whatever words the run recorded it in."""

    if status in ("blocked", "critique blocked"):
        return "rejected on every attempt: the run stopped there"
    match = _OLD_CRITIQUE_RESULT.match(status)
    if match:
        return describe_critique_result(*(int(group) for group in match.groups()))
    if status == "nothing to criticise":
        return describe_critique_result(0, 0, 0)
    if step in (_READING, _REPEATED):
        return _describe_reading_result(status, step == _REPEATED)
    if step == _FIX:
        match = _FIX_RESULT.match(status)
        if match:
            text = f"notes added to {match.group(1)} of {match.group(2)} frames"
            refused = int(match.group(3) or 0)
            return text + (f"; {_format_count(refused, 'addition')} refused by a check" if refused else "")
        return "nothing to add" if status == "nothing to change" else status
    if step == _OPEN_QUESTIONS:
        match = _OPEN_QUESTIONS_RESULT.match(status)
        if match:
            count = int(match.group(1))
            return f"{_format_count(count, 'open question')} found" if count else "no open question found"
    if step == _ANSWERS:
        match = _ANSWERS_RESULT.match(status)
        if match:
            return f"{_format_count(int(match.group(1)), 'question')} answered, {match.group(2)} still open"
    if step == _ALONE and status == "summary accepted":
        return "the batch's account, asked alone, accepted"

    return status


def _describe_row(run_dir: Path,
                  row: dict[str, Any]) -> dict[str, Any]:
    slug = str(row.get("slug") or "")
    frames, step = _describe_slug(slug)

    return {"slug": slug, "frames": frames, "step": step, "started_at": row.get("startedAt") or "",
            "duration_s": row.get("durationS") or 0,
            "status": _describe_status(step, str(row.get("status") or "")),
            "attempts": row.get("attempts") or 0,
            "prompt_tokens": row.get("promptTokens") or 0, "output_tokens": row.get("outputTokens") or 0,
            "rejections": _read_rejections(run_dir, slug)}


def describe_critique_result(mistakes: int,
                             frames_with_mistakes: int,
                             frames_in_batch: int) -> str:
    """What a batch's critique found, as the console and the batch table say it: "3 mistakes found, in 1 of 2 frames"."""

    if not mistakes:
        return "no mistake found"

    return (f"{_format_count(mistakes, 'mistake')} found, in {frames_with_mistakes} of "
            f"{frames_in_batch} {_format_plural(frames_in_batch, 'frame')}")


def list_batches(folder: Path) -> list[dict[str, Any]]:
    """Every phase 3 batch and follow-up the run folder's logs record, in the order they started, over every run and
    resume of the folder."""

    rows: list[tuple[float, dict[str, Any]]] = []
    for run_dir in sorted((folder / output_contract.RUNS_DIR_NAME).glob("*/")):
        for row in performance_log_service.read_rows(run_dir):
            if row.get("level") == "batch" and row.get("phase") == _PHASE:
                rows.append((float(row.get("startedAtS") or 0), _describe_row(run_dir, row)))

    return [batch for _, batch in sorted(rows, key=lambda pair: pair[0])]
