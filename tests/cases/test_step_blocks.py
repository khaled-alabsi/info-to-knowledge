"""How often each step of phases 4 and 5 blocked, was accepted and was reset, read from the conversation logs: the
counter, the `blocks` command's lines and the time each log row now carries."""

from __future__ import annotations

import json
import tempfile
import types
from pathlib import Path

from support import harness

from cli import blocks_command
from models import output_contract
from services import conversation_log_service
from services import run_status_service
from services import write_conversation_service


def _write_log(path: Path,
               rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + "not json\n", encoding="utf-8")


def _build_run_folder() -> Path:
    """A run folder whose phase 4 log holds a step blocked twice, reset, then accepted, a step accepted at once, and a
    phase 5 log with one block."""

    folder = Path(tempfile.mkdtemp())
    _write_log(folder / output_contract.WRITE_CONVERSATION_LOG, [
        {"followUp": "chapter-01-text", "sent": ["m1"], "calls": []},
        {"followUp": "chapter-01-text", "blocked": "1 answers rejected: CODE_A, CODE_A, CODE_B",
         "at": "2026-10-01T09:15:00+02:00"},
        {"followUp": "chapter-01-text", "blocked": "the host failed\nwith a second line"},
        {"followUp": "chapter-01-text", "reset": True},
        {"followUp": "chapter-01-text", "accepted": 1},
        {"followUp": "chapter-02-text", "accepted": 1}])
    _write_log(folder / output_contract.REVIEW_CONVERSATION_LOG, [{"followUp": "critique", "blocked": "1 answer rejected: CODE_C"}])

    return folder


def test_each_step_is_counted_in_the_order_it_was_asked_with_its_blocks_summed_up():
    folder = _build_run_folder()
    steps = conversation_log_service.count_follow_up_outcomes(folder / output_contract.WRITE_CONVERSATION_LOG)
    first = steps[0]
    harness.check("two steps, in the order asked", [step["step"] for step in steps] == ["chapter-01-text", "chapter-02-text"],
                  str(steps))
    harness.check("blocked twice, reset once, accepted once",
                  (first["blocked"], first["reset"], first["accepted"]) == (2, 1, 1), str(first))
    harness.check("rejected codes counted once each, a reason's first line kept, its time or none",
                  [(block["summary"], block["at"]) for block in first["blocks"]]
                  == [("rejected: CODE_A ×2, CODE_B", "2026-10-01T09:15:00+02:00"), ("the host failed", "")],
                  str(first["blocks"]))
    harness.check("no log is no steps",
                  conversation_log_service.count_follow_up_outcomes(folder / "missing.jsonl") == [], "")


def test_the_run_status_lists_phase_4_then_phase_5_steps():
    steps = run_status_service.list_step_outcomes(_build_run_folder())
    harness.check("phase numbers beside each step",
                  [(step["phase"], step["step"]) for step in steps]
                  == [(4, "chapter-01-text"), (4, "chapter-02-text"), (5, "critique")], str(steps))


def test_the_command_prints_each_troubled_step_with_its_blocks_and_counts_the_rest():
    lines = blocks_command.render_step_outcomes(run_status_service.list_step_outcomes(_build_run_folder()))
    harness.check("phase 4's total and its blocked step", lines[0] == "phase 4: 2 block(s) over 2 step(s)"
                  and "blocked 2  accepted 1  reset 1" in lines[1], "\n".join(lines))
    harness.check("each block with its time, or 'no time'", "2026-10-01 09:15  rejected: CODE_A ×2, CODE_B" in lines[2]
                  and lines[3].strip().startswith("no time"), "\n".join(lines))
    harness.check("the step with no block is only counted", "  1 other step(s) with no block" in lines, "\n".join(lines))
    harness.check("phase 5 follows", "phase 5: 1 block(s) over 1 step(s)" in lines, "\n".join(lines))


def test_a_conversation_log_row_carries_its_time():
    path = Path(tempfile.mkdtemp()) / "logs" / "conversation.jsonl"
    write_conversation_service._append_conversation_log_row(types.SimpleNamespace(conversation_log_path=path),
                                                   {"followUp": "chapter-01-text", "blocked": "the host failed"})
    row = json.loads(path.read_text(encoding="utf-8"))
    harness.check("the row keeps its fields and gains a time", row["blocked"] == "the host failed"
                  and len(row["at"]) >= 19 and row["at"][10] == "T", str(row))


def test_the_reason_a_step_blocked_on_is_read_back_by_the_block_summary():
    reason = conversation_log_service.render_rejected_reason(3, ["CODE_A", "CODE_A", "CODE_B"])
    harness.check("the summary reads the codes back, each once with how often",
                  conversation_log_service._summarise_block(reason) == "rejected: CODE_A ×2, CODE_B",
                  conversation_log_service._summarise_block(reason))
    harness.check("one attempt reads in the singular",
                  conversation_log_service.render_rejected_reason(1, ["CODE_C"]) == "1 attempt rejected: CODE_C")
