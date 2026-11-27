"""What the person writes to a worker while it works, in every workflow.

The server appends each message to a file of its own, so the page never touches a job's conversation. The job takes what
waits before its next call and keeps, in a state file beside it, how many it took and the answer to each question."""

from __future__ import annotations

import json
import time
from pathlib import Path

from models import output_contract
from utils import json_lines_utils

__all__ = ["build_phase_messages_path", "queue_message", "take_waiting", "list_waiting", "record_reply", "list_messages",
           "render_user_text", "render_system_text", "is_system_message"]

_SYSTEM_ROLE = "system"
_MESSAGE_LEAD = "The person running this pass writes to you while you work:"
_MESSAGE_ASK = "Take it into account and go on with your work."
_QUESTION_ASK = "Answer it in words in this reply, with no tool call. Your next call goes on with your work."
_SYSTEM_MESSAGE_LEAD = "The person running this pass adds this to your instructions while you work:"
_MESSAGES_DIR = "worker-messages"


def build_phase_messages_path(output_root: Path,
                              phase: str) -> Path:
    """The messages the person wrote to the worker of `phase`, whichever step it is on."""

    return output_root / output_contract.RUNS_DIR_NAME / _MESSAGES_DIR / f"{phase}.jsonl"


def _build_state_path(path: Path) -> Path:
    return path.with_suffix(".state.json")


def _read_state(path: Path) -> dict:
    state_path = _build_state_path(path)
    if not state_path.is_file():
        return {"sent": 0, "answers": {}, "open": []}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except ValueError:
        return {"sent": 0, "answers": {}, "open": []}


def _write_state(path: Path,
                 state: dict) -> None:
    # replaced whole: the server reads it while the job writes it
    state_path = _build_state_path(path)
    written = state_path.with_suffix(".tmp")
    written.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    written.replace(state_path)


def queue_message(path: Path,
                  message: dict) -> None:
    """Keep `message` ({"text", "role", "is_question"}) for the job to take before its next call."""

    json_lines_utils.append_json_line(path, {"at": time.time(), **message})


def list_waiting(path: Path) -> list[dict]:
    """The messages the worker has not got yet, oldest first."""

    return json_lines_utils.read_json_lines(path)[_read_state(path)["sent"]:]


def take_waiting(path: Path) -> list[dict]:
    """The messages waiting, recorded as sent."""

    state = _read_state(path)
    waiting = json_lines_utils.read_json_lines(path)[state["sent"]:]
    if not waiting:
        return []
    questions = [state["sent"] + index for index, message in enumerate(waiting) if message.get("is_question")]
    state["open"] = [*state["open"], *questions]
    # when the worker took each one, so its live panel marks the turn it reached (the owner: "the turn should have some
    # visiual marker, that show that a user message is recived in the turn")
    taken_at = time.time()
    taken_dict = {str(state["sent"] + index): taken_at for index in range(len(waiting))}
    state["sent_at"] = {**state.get("sent_at", {}), **taken_dict}
    state["sent"] += len(waiting)
    _write_state(path, state)

    return waiting


def record_reply(path: Path,
                 text: str) -> None:
    """The worker's words `text` are the answer to the oldest question it was sent and has not answered."""

    state = _read_state(path)
    if state["open"] and text.strip():
        state["answers"][str(state["open"].pop(0))] = text
        _write_state(path, state)


def list_messages(path: Path) -> list[dict]:
    """Each message, whether the worker got it and when (`sent_at`, none for one taken before that was kept), and its
    answer when it was a question and has one."""

    state = _read_state(path)
    sent_at = state.get("sent_at", {})

    return [{**message, "is_sent": index < state["sent"], "sent_at": sent_at.get(str(index)),
             "answer": state["answers"].get(str(index), "")}
            for index, message in enumerate(json_lines_utils.read_json_lines(path))]


def render_user_text(message: dict) -> str:
    """The text a message reaches the worker with as the conversation's last message, so the host reads only it."""

    ask = _QUESTION_ASK if message.get("is_question") else _MESSAGE_ASK

    return f"{_MESSAGE_LEAD}\n\n{message.get('text') or ''}\n\n{ask}"


def render_system_text(instructions: object,
                       message: dict) -> str:
    """The first message with a system message added: the one place every model takes one, so the host reads the whole
    prompt again."""

    return f"{instructions}\n\n{_SYSTEM_MESSAGE_LEAD}\n\n{message.get('text') or ''}"


def is_system_message(message: dict) -> bool:
    """Whether `message` was sent as system, which joins the worker's instructions."""

    return message.get("role") == _SYSTEM_ROLE
