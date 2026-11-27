"""The worker of a phase, live, in every workflow: what it thinks now, what it did,
and what the person wrote to it. Read from what the run already writes, so nothing here slows a run: each call's
`thinking-live.md`, and phases 4 and 5's conversation log. Phase 7's parts have their own panel."""

from __future__ import annotations

import json
from pathlib import Path

import config as app_config
from models import output_contract
from services import conversation_log_service
from services import fast_turn_service
from services import video_workflow_service
from services import worker_message_service

__all__ = ["describe_phase_worker", "describe_fast_session", "queue_phase_message", "PHASES_WITH_QUESTIONS"]

_THINKING_FILE = "thinking-live.md"
_THINKING_SHOWN = 30_000
_ARGS_SHOWN = 300
_RESULT_SHOWN = 600
_STEPS_SHOWN = 200
# A worker that wrote within this time is writing; else the host reads its prompt, or the program runs its tools.
_WRITING_WITHIN_S = 60
_CONVERSATION_LOGS = {4: output_contract.WRITE_CONVERSATION_LOG, 5: output_contract.REVIEW_CONVERSATION_LOG}
# Phases that hold a conversation, where a worker may answer in words: a call step's reply is read in a set shape, so a
# question there would come back as a refused answer
PHASES_WITH_QUESTIONS = frozenset(_CONVERSATION_LOGS)
# The phases a fast-processing run's workers keep turns for (`fast_turn_service`), in place of the quality workflow's logs
_FAST_PHASES = (3, 4)


def _read_phase_name(number: int) -> str:
    return next((name for name, phase in output_contract.PHASE_NUMBERS.items() if phase == number), "")


def _find_thinking_file(output_root: Path,
                        number: int) -> Path | None:
    """The newest thinking of the phase: beside its conversation log, or in the attempt folder of its newest call."""

    if number in _CONVERSATION_LOGS:
        path = (output_root / _CONVERSATION_LOGS[number]).with_name(_THINKING_FILE)
        return path if path.is_file() else None
    logs = output_root / output_contract.RUNS_DIR_NAME
    found = list(logs.glob(f"*/{_read_phase_name(number)}/**/{_THINKING_FILE}")) if logs.is_dir() else []

    return max(found, key=lambda path: path.stat().st_mtime, default=None)


def _read_step(path: Path | None,
               number: int) -> str:
    """The step the thinking belongs to: the attempt's folder names it."""

    if path is None or number in _CONVERSATION_LOGS:
        return ""

    return path.parent.parent.name


def _list_conversation_steps(output_root: Path,
                             number: int) -> list[dict]:
    """Each tool call of phase 4's or 5's conversation, with what the program answered, newest last."""

    steps: list[dict] = []
    for row in conversation_log_service.read_rows(output_root / _CONVERSATION_LOGS[number]):
        for call in row.get("calls") or []:
            steps.append({"at": row.get("at"), "call": len(steps), "tool": str(call.get("name") or ""),
                          "summary": str(row.get("followUp") or ""),
                          "args": json.dumps(call.get("args") or {}, ensure_ascii=False)[:_ARGS_SHOWN], "result": ""})
        if "tool" in row:
            step = next((step for step in steps if step["tool"] == row["tool"] and not step["result"]), None)
            if step is not None:
                step["result"] = str(row.get("result") or "")[:_RESULT_SHOWN]

    return steps[-_STEPS_SHOWN:]


def _list_call_steps(output_root: Path,
                     number: int) -> list[dict]:
    """Each call of a phase with no conversation, oldest first, by the attempt folders the run left."""

    logs = output_root / output_contract.RUNS_DIR_NAME
    found = list(logs.glob(f"*/{_read_phase_name(number)}/**/attempt-*")) if logs.is_dir() else []
    found.sort(key=lambda path: path.stat().st_mtime)

    return [{"at": path.stat().st_mtime, "call": index, "tool": path.parent.name, "summary": path.name.replace("-", " "),
             "args": "", "result": ""} for index, path in enumerate(found[-_STEPS_SHOWN:])]


def _describe_doing(path: Path | None,
                    now: float) -> dict:
    if path is None:
        return {"what": "", "since": None}
    written_at = path.stat().st_mtime
    if now - written_at <= _WRITING_WITHIN_S:
        return {"what": "thinking or writing", "since": written_at}

    return {"what": "reading its prompt or running its tools", "since": written_at}


def _describe_speed(path: Path | None,
                    text: str) -> dict:
    """Estimated from the characters written and the file's own times: these calls keep no token count while they run."""

    idle = {"tokens_per_second": None, "is_estimated": True, "prompt_tokens": 0, "is_writing": False}
    if path is None or not text:
        return idle
    stat = path.stat()
    started = getattr(stat, "st_birthtime", None)
    if started is None or stat.st_mtime <= started:
        return idle
    tokens = len(text) / app_config.settings.document_reshaping.chars_per_token

    return {**idle, "tokens_per_second": round(tokens / (stat.st_mtime - started), 1), "is_writing": True}


def queue_phase_message(output_root: Path,
                        number: int,
                        message: dict) -> None:
    """Keep `message` for the worker of phase `number`."""

    path = worker_message_service.build_phase_messages_path(output_root, _read_phase_name(number))
    worker_message_service.queue_message(path, message)


def _describe_fast_turns(output_root: Path,
                         number: int,
                         turns: list[dict]) -> dict:
    """A fast-processing worker's panel from its kept turns: phase 3 takes the person's messages before each frame,
    phase 4's one call takes none, and neither answers in words."""

    messages_path = worker_message_service.build_phase_messages_path(output_root, _read_phase_name(number))
    steps = [step for turn in turns for step in turn.get("steps") or []]

    return {"number": number, "step": "", "steps": steps, "turns": turns, "thinking": "", "thinking_at": None,
            "speed": {"tokens_per_second": None, "is_estimated": True, "prompt_tokens": 0, "is_writing": False},
            "doing": {"what": "", "since": None}, "messages": worker_message_service.list_messages(messages_path),
            "can_ask": False, "can_send": number == 3}


def describe_fast_session(output_root: Path,
                          slug: str) -> dict:
    """One phase 3 session of a fast-processing run, for the panel its row in the Frames table opens."""

    return _describe_fast_turns(output_root, 3, fast_turn_service.list_turns(output_root, slug))


def describe_phase_worker(output_root: Path,
                          number: int,
                          now: float) -> dict:
    """What the worker of phase `number` did and does now, for the panel its card opens."""

    # both fast workflows keep their workers' turns the same way (`fast_turn_service`)
    if number in _FAST_PHASES and video_workflow_service.has_no_critic(output_root):
        return _describe_fast_turns(output_root, number, fast_turn_service.list_phase_turns(output_root, number))
    thinking = _find_thinking_file(output_root, number)
    text = thinking.read_text(encoding="utf-8", errors="replace") if thinking else ""
    is_conversation = number in _CONVERSATION_LOGS
    steps = _list_conversation_steps(output_root, number) if is_conversation else _list_call_steps(output_root, number)
    messages_path = worker_message_service.build_phase_messages_path(output_root, _read_phase_name(number))

    return {"number": number, "step": _read_step(thinking, number), "steps": steps, "thinking": text[-_THINKING_SHOWN:],
            "thinking_at": thinking.stat().st_mtime if thinking else None, "speed": _describe_speed(thinking, text),
            "doing": _describe_doing(thinking, now), "messages": worker_message_service.list_messages(messages_path),
            "can_ask": number in PHASES_WITH_QUESTIONS}
