"""Batch history: the Frames card of the detail view opens a table of every phase 3 batch and follow-up, with its time,
tokens, outcome and each refused attempt with why, read from the run's own logs so a server restart loses none of it."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from services import batch_history_service
from services import run_status_service
from services import web_api_service


def _row(slug: str,
         started_s: float,
         **fields) -> dict:
    return {"level": "batch", "session": "2026-01-01T10:00:00", "phase": "frames_processing", "slug": slug,
            "startedAt": "2026-01-01T10:00:00", "startedAtS": started_s, "durationS": 120.5, "status": "2 of 2 accepted",
            "attempts": 1, "frames": 2, "promptTokens": 26000, "outputTokens": 5000, **fields}


def _build_run_folder() -> Path:
    """A run folder whose log holds a batch, its critique (refused once, then accepted) and a phase 4 row."""

    folder = Path(tempfile.mkdtemp()) / "output" / "Gateway-review"
    run_dir = folder / "logs" / "2026-01-01_10-00"
    run_dir.mkdir(parents=True)
    rows = [_row("frames-001-002-critique", 300.0, attempts=2, status="3 rules on 2 of 2 frames"),
            _row("frames-001-002", 100.0),
            {"level": "batch", "phase": "finalize", "slug": "outline-03", "startedAtS": 400.0, "durationS": 9.0},
            {"level": "session", "session": "2026-01-01T10:00:00"}]
    (run_dir / "performance.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n{cut", encoding="utf-8")
    attempt = run_dir / "frames_processing" / "frames-001-002-critique" / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "validation.json").write_text(json.dumps({"ok": False, "findings": [
        {"code": "CRITIQUE_RULE_NOT_GENERIC", "detail": "a rule quotes a screen", "fix": "name its kind"}]}), encoding="utf-8")
    accepted = attempt.parent / "attempt-2"
    accepted.mkdir()
    (accepted / "validation.json").write_text(json.dumps({"ok": True, "findings": []}), encoding="utf-8")
    reentered = attempt.parent / "attempt-1.1"
    reentered.mkdir()
    (reentered / "validation.json").write_text(json.dumps({"ok": False, "findings": [
        {"code": "CRITIQUE_EMPTY", "detail": "no rule"}]}), encoding="utf-8")

    return folder


def test_every_phase_3_batch_is_listed_in_order_with_its_cost_and_each_refusal_with_why():
    batches = batch_history_service.list_batches(_build_run_folder())
    harness.check("only phase 3's rows, in the order they started",
                  [batch["slug"] for batch in batches] == ["frames-001-002", "frames-001-002-critique"], str(batches))
    reading, critique = batches
    harness.check("a batch names its frames, its step, its time, its tokens and its result",
                  reading["frames"] == "1–2" and reading["step"] == "reading" and reading["duration_s"] == 120.5
                  and reading["prompt_tokens"] == 26000 and reading["output_tokens"] == 5000
                  and reading["status"] == "notes of 2 of 2 frames accepted" and reading["rejections"] == [], str(reading))
    harness.check("a follow-up is named by its step, with how many answers it took and why each refused one was refused",
                  critique["step"] == "critique" and critique["attempts"] == 2 and critique["rejections"]
                  == [{"attempt": 1, "findings": [{"code": "CRITIQUE_RULE_NOT_GENERIC", "detail": "a rule quotes a screen"}]},
                      {"attempt": 1, "findings": [{"code": "CRITIQUE_EMPTY", "detail": "no rule"}]}],
                  str(critique))
    harness.check("a batch's result is one plain sentence", reading["status"] == "notes of 2 of 2 frames accepted",
                  reading["status"])
    for step, recorded, shown in (
            ("reading", "2 of 3 accepted → blocked, no repair (frame 5)",
             "notes of 2 of 3 frames accepted; frame 5 refused, the run stopped there"),
            ("reading", "1 of 3 accepted → retried together (frames 4, 6)",
             "notes of 1 of 3 frames accepted; frames 4, 6 refused, asked again"),
            ("fix from critique", "1 of 2 frames gained notes, 1 dropped",
             "notes added to 1 of 2 frames; 1 addition refused by a check"),
            ("open questions", "0 found", "no open question found"),
            ("answers", "1 answered, 2 stay open", "1 question answered, 2 still open"),
            ("critique", "critique blocked", "rejected on every attempt: the run stopped there")):
        harness.check(f"{step}: {recorded!r} reads plainly", batch_history_service._describe_status(step, recorded) == shown,
                      batch_history_service._describe_status(step, recorded))
    harness.check("a critique of a one-frame batch does not say 1 of 1 frames",
                  batch_history_service.describe_critique_result(2, 1, 1) == "2 mistakes found, in 1 of 1 frame",
                  batch_history_service.describe_critique_result(2, 1, 1))
    harness.check("a critique's result recorded in the old words reads in today's",
                  critique["status"] == "3 mistakes found, in 2 of 2 frames", critique["status"])


def test_the_page_reads_them_from_the_run_folder():
    folder = _build_run_folder()
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = folder.parent
    try:
        answer = TestClient(web_api_service.build_app()).get("/api/runs/output/Gateway-review/batches")
        harness.check("the route lists the batches", answer.status_code == 200 and len(answer.json()) == 2, answer.text[:300])
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)


def test_a_repair_round_after_the_first_is_read_like_the_first():
    first = batch_history_service._describe_slug("frames-000-006-repair")
    later = batch_history_service._describe_slug("frames-000-006-repair-2")
    harness.check("round 2 names its frames and its step, as round 1 does", later == first and later[0] == "0\u20136",
                  f"{first} / {later}")


def test_a_fast_session_names_its_frames_and_its_step_defect():
    # The defect: a fast run's table showed an empty Frames column and "fast-notes-000-004" as the step. The fix: a
    # session's slug names its frames, and its step is "frame notes".
    harness.check("fast-notes-000-004 is frames 0\u20134, frame notes",
                  batch_history_service._describe_slug("fast-notes-000-004") == ("0\u20134", "frame notes"))
