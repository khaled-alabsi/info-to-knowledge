# <!-- sync: seed/services.attempt_log_service v2 -->
"""One directory per attempt, written as the attempt runs - and the same directory shape for an
interactive session, so one loader reopens either.

Written as it happens, not at the end: an in-process worker cannot be killed from outside, and an OOM
takes the whole interpreter down rather than one attempt. If the record is only flushed on success,
a hard crash leaves nothing explaining where it was. That holds for the RELOADABLE record too: a
`session/session.json` written once at the end is absent on exactly the attempt worth reopening -
the one that was killed - so it is flushed on the same per-step hook as the transcript.

`invocation.json` plus `system-prompt.md` is what makes "why did this attempt behave differently"
answerable a week later, and with `session/session.json` they are what makes the worker resumable.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, messages_from_dict, messages_to_dict

from agent_client import finish_reason_utils
from utils import chat_transcript_utils
from utils import json_store_utils
from utils import message_content_utils

__all__ = ["AttemptLog", "claim_attempt_dir", "load_session", "read_session_payload",
           "INVOCATION_FILE", "SYSTEM_PROMPT_FILE", "HUMAN_MESSAGE_FILE", "VALIDATION_FILE", "DECISION_FILE", "SESSION_DIR",
           "SESSION_FILE", "IMAGES_FILE"]

# The files that make a directory a reopenable session, whichever command wrote it.
INVOCATION_FILE = "invocation.json"
SYSTEM_PROMPT_FILE = "system-prompt.md"
HUMAN_MESSAGE_FILE = "human-message.md"
VALIDATION_FILE = "validation.json"
DECISION_FILE = "decision.json"
SESSION_DIR = "session"
SESSION_FILE = "session.json"
# Beside `transcript.md` (what the worker sees now): every turn the attempt ever had.
FULL_TRANSCRIPT_FILE = "transcript.full.md"
# A call that sends frames the model names by their place in the call: the frame each image is, in order
IMAGES_FILE = "images.json"


@dataclass
class AttemptLog:
    directory: Path
    # EVERY TURN THIS ATTEMPT EVER HAD, in the order it first appeared, for `transcript.full.md`.
    # `transcript.md` is what the worker can see NOW - after a compaction that is a summary plus the
    # last turns, and everything before them is gone from it. This is the other half: a file that
    # never loses a turn. Messages keep their ids through a compaction (the kept tail is the same
    # objects), so a turn is appended here the first time its id is seen and never again - the
    # summary is a new id and lands where the compaction happened.
    full_history: list[BaseMessage] = field(default_factory=list)
    full_history_ids: set[str] = field(default_factory=set)

    @property
    def input_snapshot(self) -> Path:
        return self.directory / "input-snapshot"

    @property
    def output_snapshot(self) -> Path:
        return self.directory / "output-snapshot"

    @property
    def session_dir(self) -> Path:
        """`<attempt>/session/` - the reloadable record, and the session's own history."""

        return self.directory / SESSION_DIR

    def write_invocation(self,
                         payload: dict[str, Any]) -> None:
        self._write_json(INVOCATION_FILE, payload)

    def write_system_prompt(self,
                            system_prompt: str) -> None:
        """The system prompt exactly as sent - what a reopened session is rebuilt from."""

        (self.directory / SYSTEM_PROMPT_FILE).write_text(system_prompt, encoding="utf-8")

    def write_human_message(self,
                            human_message: str) -> None:
        """The opening human message. A session that grows turn by turn writes none."""

        (self.directory / HUMAN_MESSAGE_FILE).write_text(human_message, encoding="utf-8")

    def write_session(self,
                      messages: list[BaseMessage],
                      was_pruned: bool = False,
                      was_compacted: bool = False) -> None:
        """The session, LOSSLESSLY, as `session/session.json` - written to be loaded back."""

        # `messages.jsonl` is a VIEW: type, text, tool calls, reasoning - everything a person needs and
        # not enough to reconstruct the conversation. Tool-call ids, `additional_kwargs`,
        # `response_metadata` and the exact class of each message are all dropped, and a provider
        # rejects a transcript whose tool results no longer match their calls.
        #
        # `messages_to_dict` is the round-trip pair of `messages_from_dict`, so this file can be loaded
        # back into an agent and continued with a new prompt - which is the whole point of keeping it.
        #
        # ONE filename, whatever was done to the history. It is rewritten after every step, and the
        # flags can flip mid-attempt, so a name that carried them would leave a stale sibling behind.
        # The flags travel in the payload instead, and the loader reports them: a pruned or compacted
        # session is not the conversation the worker had - bodies are stubs, older turns one summary.

        payload = {"schemaVersion": 1, "attempt": self.directory.name,
                   "wasPruned": was_pruned, "wasCompacted": was_compacted,
                   "messages": messages_to_dict(messages)}
        json_store_utils.write_json(self.session_dir / SESSION_FILE, payload, default=str)

    def write_messages(self,
                       messages: list[BaseMessage]) -> None:
        """The message list, in the forms it is read in: `messages.jsonl`, `transcript.md` and `transcript.full.md`, and
        each new message recorded in `full_history`."""

        # One method writes both so they cannot drift - a call site that recorded the evidence and
        # forgot the readable copy would leave a transcript that silently stops mid-attempt, which
        # reads exactly like a hung worker.

        for message in messages:
            self._add_to_full_history(message)
        rows = [_build_message_row(message) for message in messages]
        label = "/".join(self.directory.parts[-3:])
        (self.directory / "messages.jsonl").write_text(_render_json_lines(rows), encoding="utf-8")
        (self.directory / "transcript.md").write_text(chat_transcript_utils.render_transcript(messages, label),
                                                      encoding="utf-8")
        (self.directory / FULL_TRANSCRIPT_FILE).write_text(
            chat_transcript_utils.render_transcript(self.full_history, label, is_full_history=True), encoding="utf-8")

    def _add_to_full_history(self,
                             message: BaseMessage) -> None:
        # A message with no id cannot be told apart from itself next step, so it is kept on first
        # sight and would repeat if it came round again - the state reducer stamps every message it
        # merges, so that is a fake in a test, not a run.
        message_id = getattr(message, "id", None)
        if message_id and message_id in self.full_history_ids:
            return
        if message_id:
            self.full_history_ids.add(message_id)
        self.full_history.append(message)

    def write_tool_calls(self,
                         log: list[dict[str, Any]]) -> None:
        (self.directory / "tool-calls.jsonl").write_text(_render_json_lines(log), encoding="utf-8")

    def write_validation(self,
                         payload: dict[str, Any]) -> None:
        self._write_json(VALIDATION_FILE, payload)

    def write_decision(self,
                       payload: dict[str, Any]) -> None:
        self._write_json(DECISION_FILE, payload)

    def capture_output(self,
                       targets: list[Path]) -> None:
        """What this attempt actually produced, saved BEFORE any restore deletes it."""

        self.output_snapshot.mkdir(parents=True, exist_ok=True)
        for path in targets:
            if path.is_file():
                shutil.copy2(path, self.output_snapshot / path.name)

    def _write_json(self,
                    name: str,
                    payload: dict[str, Any]) -> None:
        json_store_utils.write_json(self.directory / name, payload, default=str)


def _build_message_row(message: BaseMessage) -> dict[str, Any]:
    """One message as a row of `messages.jsonl`."""

    row = {
        "type": message.__class__.__name__,
        "content": message_content_utils.read_message_text(message.content),
        "tool_calls": getattr(message, "tool_calls", None) or [],
    }
    # Thinking is logged BESIDE the content, never instead of it: a right reply reached
    # by a wrong route is exactly what a reader needs both halves to see. Present only when
    # the provider separated it at all - a host that leaves `<think>` tags inline leaves
    # them in `content`, where they are logged anyway.
    reasoning = message_content_utils.read_reasoning(message)
    if reasoning:
        row["reasoning"] = reasoning
    # A cut turn and a silent turn have the same content - none - and only this tells a
    # reader which one they are looking at.
    if finish_reason_utils.is_reply_cut_at_output_limit(message):
        row["cutAtOutputCap"] = True

    return row


def _render_json_lines(rows: list[dict[str, Any]]) -> str:
    """One JSON object per line, a line end after each; nothing for no row."""

    return "".join(json.dumps(row, default=str) + "\n" for row in rows)


def _drop_unanswered_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The history up to its last COMPLETE exchange."""

    # A session flushed after every step can end on an assistant turn whose tool calls were never
    # answered - the process was killed while the tool ran. A provider rejects a transcript with a
    # dangling call, so the turn is dropped and the operator's next message follows the last tool
    # result the worker actually saw. Nothing else is touched.

    kept = list(messages)
    while kept:
        last = kept[-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            answered = {m.tool_call_id for m in kept if isinstance(m, ToolMessage)}
            if all(call.get("id") in answered for call in last.tool_calls):
                break
            kept.pop()
            continue
        if isinstance(last, ToolMessage):
            calls = {call.get("id") for m in kept if isinstance(m, AIMessage) for call in m.tool_calls}
            if last.tool_call_id in calls:
                break
            kept.pop()
            continue
        break

    return kept


def read_session_payload(directory: Path) -> dict[str, Any]:
    """`session/session.json` as written, or an empty dict when the directory holds none."""

    path = directory / SESSION_DIR / SESSION_FILE
    if not path.is_file():
        return {}

    return json_store_utils.read_json(path)


def load_session(directory: Path) -> list[BaseMessage]:
    """The conversation a session directory recorded, ready to be continued."""

    payload = read_session_payload(directory)
    if not payload:
        return []

    return _drop_unanswered_tool_calls(messages_from_dict(payload.get("messages", [])))


def claim_attempt_dir(run_dir: Path,
                      phase: str,
                      slug: str | None,
                      attempt: int) -> AttemptLog:
    """`<run>/<phase>/<slug>/attempt-N/`, never overwriting an existing one."""

    # A re-entry takes the NEXT NAME IN THE SERIES - `attempt-N.1`, then `attempt-N.2` - so the
    # highest suffix is always the newest and nothing already written ever moves. The loop only reaches
    # attempt numbers it has not recorded, so an occupied directory is from an interrupted run and is
    # the only log of what that run did; renaming it aside broke that, because the reader cannot tell
    # from the names which of the survivors ran last.

    base = run_dir / phase / slug if slug else run_dir / phase
    directory = base / f"attempt-{attempt}"
    reentry = 0
    while directory.exists():
        reentry += 1
        directory = base / f"attempt-{attempt}.{reentry}"
    directory.mkdir(parents=True, exist_ok=True)

    return AttemptLog(directory)
