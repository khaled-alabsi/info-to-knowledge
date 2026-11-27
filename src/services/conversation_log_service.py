"""What a follow-up of a conversation did, read back from the conversation's log (`logs/conversation.jsonl`): the
evidence a check uses instead of what the agent says it did. Shared by phase 4's writing and phase 5's review."""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any

from schemas import review_actions
from schemas import write_actions
from utils import json_lines_utils

# A block from rejected attempts names each finding's code once per finding: "1 attempt rejected: CODE, CODE, OTHER".
# Logs written before the wording changed say "answers" for "attempts", and are still read.
_REJECTED_CODES = re.compile(r"^\d+ (?:answers?|attempts?) rejected: (.+)$")
# A block's one-line summary is cut here: a refused tool's reason runs on for paragraphs
_SUMMARY_CHARS = 200

# The tools that read: the reading tools and the note of phase 4 and of phase 5 (never `check_chapter` or
# `report_problem`). Both lists also hold the frame tools (look, cut, undo_cut, mask), which read nothing of the notes,
# so they are left out.
_READING_TOOLS = frozenset(name for name in (*write_actions.READ_TOOLS, *review_actions.READ_TOOLS)
                           if name not in write_actions.FRAME_TOOLS)

__all__ = ["REJECTED_LEAD", "PROGRAM_READS_KEY", "read_rows", "render_rejected_reason", "render_findings", "render_rejection",
           "is_rejection", "list_rejection_findings", "list_follow_up_calls", "list_follow_up_reads",
           "count_follow_up_outcomes"]

# The first word of a rejected attempt's result, which a later life of the run looks for in the log to learn why it
# blocked, and the page's table counts by
REJECTED_LEAD = "Rejected."
# A row's reads the program made for the follow-up and handed it in a result, `{"name", "args"}` each: they count as
# the follow-up's reads, and never as calls the model made
PROGRAM_READS_KEY = "programReads"
# A finding in a rejected attempt's result, one line: `- CODE: detail Fix: ...`
_FINDING = re.compile(r"^- ([A-Z][A-Z0-9_]+): (.*)$", re.MULTILINE)


def read_rows(log_path: Path) -> list[dict[str, Any]]:
    """Every row of a conversation log, in order. No log is no rows; a line that does not read is passed over, wherever
    it stands: a run killed while it wrote leaves its last row half written, and the rows after a broken one still
    count."""

    return json_lines_utils.read_json_lines(log_path)


def list_follow_up_calls(log_path: Path,
                         slug: str) -> list[dict[str, Any]]:
    """Every tool call one follow-up made, `{"name", "args"}`, in order, the reads the program made for it included. No
    log is no calls."""

    calls = []
    for row in read_rows(log_path):
        if row.get("followUp") == slug:
            calls += [{"name": str(call.get("name", "")), "args": dict(call.get("args") or {})}
                      for call in _list_row_calls(row)]

    return calls


def _list_row_calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(row.get("calls") or []), *(row.get(PROGRAM_READS_KEY) or [])]


def list_reads_up_to(log_path: Path,
                     slug: str) -> list[str]:
    """Every read the conversation made up to and through follow-up `slug`, in order: what is in the conversation when
    that follow-up answers."""

    rows = read_rows(log_path)
    last = max((index for index, row in enumerate(rows) if row.get("followUp") == slug), default=-1)

    return [f"{call.get('name', '')} {json.dumps(dict(call.get('args') or {}), ensure_ascii=False)}"
            for row in rows[:last + 1] for call in _list_row_calls(row) if call.get("name") in _READING_TOOLS]


def list_follow_up_reads(log_path: Path,
                         slug: str) -> list[str]:
    """Every read one follow-up made, as its name and arguments, in order: what it searched, taken from what it did
    rather than from what it says it did."""

    return [f"{call['name']} {json.dumps(call['args'], ensure_ascii=False)}"
            for call in list_follow_up_calls(log_path, slug) if call["name"] in _READING_TOOLS]


def render_findings(findings: list[dict[str, str]]) -> str:
    """Each finding on a line, `- CODE: detail Fix: how`; `list_rejection_findings` reads it back."""

    return "\n".join(f"- {finding['code']}: {finding['detail']}" + (f" Fix: {finding['fix']}" if finding.get("fix") else "")
                     for finding in findings)


def render_rejection(findings: list[dict[str, str]]) -> str:
    """A rejected attempt's result as the conversation log keeps it: `REJECTED_LEAD`, then each finding."""

    return f"{REJECTED_LEAD}\n{render_findings(findings)}"


def is_rejection(result: str) -> bool:
    """Whether a logged result is a rejected attempt's."""

    return result.startswith(REJECTED_LEAD)


def list_rejection_findings(result: str) -> list[tuple[str, str]]:
    """The `(code, detail)` of each finding of a rejected attempt's result, in order."""

    return _FINDING.findall(result)


def render_rejected_reason(attempts: int,
                           codes: list[str]) -> str:
    """The reason a step blocked on rejections: how many attempts were rejected and each finding's code, in the form
    `_summarise_block` reads back."""

    return f"{attempts} {'attempt' if attempts == 1 else 'attempts'} rejected: " + ", ".join(codes)


def _summarise_block(reason: str) -> str:
    """One line for a block: its rejected codes each once with how often (`CODE ×2`), or its reason's first line."""

    first = reason.strip().splitlines()[0] if reason.strip() else ""
    codes = _REJECTED_CODES.match(first)
    if codes:
        counted = collections.Counter(code.strip() for code in codes.group(1).split(","))
        return "rejected: " + ", ".join(f"{code} ×{count}" if count > 1 else code for code, count in counted.items())

    return first if len(first) <= _SUMMARY_CHARS else first[:_SUMMARY_CHARS - 1] + "…"


def count_follow_up_outcomes(log_path: Path) -> list[dict[str, Any]]:
    """Each follow-up of the conversation in the order it was first asked, with how often it blocked, was accepted and
    was taken back by `i2k reset-chapter`, and each block's reason, its one-line summary and its time (`at`, empty on a row logged before rows
    carried one). No log is no follow-ups."""

    steps: dict[str, dict[str, Any]] = {}
    for row in read_rows(log_path):
        if not row.get("followUp"):
            continue
        step = steps.setdefault(row["followUp"], {"step": row["followUp"], "blocked": 0, "accepted": 0, "reset": 0,
                                                  "blocks": []})
        if row.get("reset"):
            step["reset"] += 1
        elif "blocked" in row:
            step["blocked"] += 1
            step["blocks"].append({"reason": str(row["blocked"]), "summary": _summarise_block(str(row["blocked"])),
                                   "at": str(row.get("at") or "")})
        elif "accepted" in row:
            step["accepted"] += 1

    return list(steps.values())
