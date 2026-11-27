"""Every step of phase 4 or phase 5 of a run folder, for the detail view's tables: its stage, its status, its model
calls, their time and tokens, and each rejection with why. Read from the run's own logs (every `logs/<run id>/calls.jsonl`
and the phase's `logs/conversation.jsonl`), never from a console, so a server restart or a resume loses none of it. The
phase 3 twin is `batch_history_service`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from services import call_log_service
from services import conversation_log_service
from services import document_file_service
from utils import json_lines_utils

__all__ = ["list_document_steps", "list_review_steps"]

# How much of a finding's words the table shows
_FINDING_CHARS = 300


_STAGE_CONTENTS = "table of contents"
_STAGE_CHAPTERS = "chapters"
_STAGE_FINAL = "final summary"


def _describe_step(slug: str) -> tuple[str, str]:
    """`(stage, step)`: the stage the page groups the step under, and the step's name as a person reads it."""

    match = document_file_service.CHAPTER_NAME_PATTERN.match(slug)
    if match and match.group(2):
        part = document_file_service.read_chapter_text_part_number(slug)
        return _STAGE_CHAPTERS, f"chapter {int(match.group(1))} text" + (f", part {part}" if part else "")
    if match:
        return _STAGE_CONTENTS, f"chapter {int(match.group(1))} headings"
    if slug == document_file_service.SUMMARY_SLUG:
        return _STAGE_FINAL, "final summary and title"

    names = {document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG: "executive summary draft",
             document_file_service.PARTS_SLUG: "agenda"}

    return _STAGE_CONTENTS, names.get(slug, slug.replace("-", " "))


_STAGE_REVIEW = "review"


def _compute_call_totals(folder: Path,
                         phase: str = output_contract.FINALIZE_PHASE) -> dict[str, dict[str, float]]:
    """Each follow-up's calls of `phase` over every run and resume of the folder: count, seconds and tokens."""

    sums: dict[str, dict[str, float]] = {}
    for run_dir in sorted((folder / output_contract.RUNS_DIR_NAME).glob("*/")):
        for row in call_log_service.read_call_metrics(run_dir):
            if row.get("phase") != phase:
                continue
            total = sums.setdefault(str(row.get("slug")), {"calls": 0, "seconds": 0.0, "promptTokens": 0, "outputTokens": 0})
            total["calls"] += 1
            total["seconds"] += float(row.get("durationS") or 0)
            total["promptTokens"] += int(row.get("promptTokens") or 0)
            total["outputTokens"] += int(row.get("outputTokens") or 0)

    return sums


def _is_rejected_submission(row: dict[str, Any],
                            result: str) -> bool:
    """Only a submission is rejected: a read whose found text opens with the word is no rejection."""

    return bool(row.get("followUp") and str(row.get("tool") or "").startswith("submit_")
                and conversation_log_service.is_rejection(result))


def _list_rejections(folder: Path,
                     log_name: str = output_contract.WRITE_CONVERSATION_LOG) -> dict[str, list[dict[str, Any]]]:
    """Each follow-up's rejected attempts, in order, each with its findings' codes and words."""

    rejections: dict[str, list[dict[str, Any]]] = {}
    for row in json_lines_utils.read_json_lines(folder / log_name):
        result = str(row.get("result") or "")
        if not _is_rejected_submission(row, result):
            continue
        findings = [{"code": code, "text": text.strip()[:_FINDING_CHARS]}
                    for code, text in conversation_log_service.list_rejection_findings(result)]
        rejections.setdefault(str(row["followUp"]), []).append({"findings": findings})

    return rejections


def list_document_steps(folder: Path) -> list[dict[str, Any]]:
    """Phase 4's steps in the order they are asked, each `{"slug", "stage", "step", "status", "calls", "seconds",
    "prompt_tokens", "output_tokens", "rejections"}`; a step not asked yet is `waiting` with no call."""

    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    steps = document_file_service.list_follow_up_steps(paths)
    sums, rejections = _compute_call_totals(folder), _list_rejections(folder)
    rows = []
    for slug, status in steps:
        stage, step = _describe_step(slug)
        total = sums.get(slug, {"calls": 0, "seconds": 0.0, "promptTokens": 0, "outputTokens": 0})
        rows.append({"slug": slug, "stage": stage, "step": step, "status": status, "calls": int(total["calls"]),
                     "seconds": round(total["seconds"], 1), "prompt_tokens": int(total["promptTokens"]),
                     "output_tokens": int(total["outputTokens"]), "rejections": rejections.get(slug, [])})

    return rows


def _list_asked_follow_ups(folder: Path) -> list[str]:
    """Phase 5's follow-ups whose request was written, in the order they were asked. A request is written when its
    follow-up starts, before its first turn ends."""

    requests_dir = folder / output_contract.REVIEW_DIR_NAME / "logs" / document_file_service.REQUESTS_DIR_NAME
    requests = sorted(requests_dir.glob("*.md"), key=lambda path: path.stat().st_mtime) if requests_dir.is_dir() else []

    return [path.stem for path in requests]


def _list_review_follow_ups(folder: Path) -> list[tuple[str, str]]:
    """Phase 5's follow-ups in the order they ran, each `accepted` once a submission of it passed, `rejected` while
    none has, and `waiting` when it was asked and no turn of it has ended yet: a turn reaches the conversation only when
    it ends, and the page showed "No step has been asked yet" through a first step's 25 minutes (DEFECTS 401)."""

    statuses: dict[str, str] = {}
    for row in json_lines_utils.read_json_lines(folder / output_contract.REVIEW_CONVERSATION_LOG):
        follow_up = str(row.get("followUp") or "")
        if not follow_up:
            continue
        statuses.setdefault(follow_up, "rejected")
        result = str(row.get("result") or "")
        if str(row.get("tool") or "").startswith("submit_") and not conversation_log_service.is_rejection(result):
            statuses[follow_up] = "accepted"
    for follow_up in _list_asked_follow_ups(folder):
        statuses.setdefault(follow_up, "waiting")

    return list(statuses.items())


def list_review_steps(folder: Path) -> list[dict[str, Any]]:
    """Phase 5's steps, as `list_document_steps` writes phase 4's: each follow-up of the review's conversation with its
    calls, time, tokens and rejections."""

    sums = _compute_call_totals(folder, output_contract.REVIEW_PHASE)
    rejections = _list_rejections(folder, output_contract.REVIEW_CONVERSATION_LOG)
    rows = []
    for slug, status in _list_review_follow_ups(folder):
        total = sums.get(slug, {"calls": 0, "seconds": 0.0, "promptTokens": 0, "outputTokens": 0})
        rows.append({"slug": slug, "stage": _STAGE_REVIEW, "step": slug.replace("-", " "), "status": status,
                     "calls": int(total["calls"]), "seconds": round(total["seconds"], 1),
                     "prompt_tokens": int(total["promptTokens"]), "output_tokens": int(total["outputTokens"]),
                     "rejections": rejections.get(slug, [])})

    return rows
