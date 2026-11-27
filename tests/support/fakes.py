# <!-- sync: seed/support.fakes v1 -->
"""Scripted models for the test suite.

**Why this file exists rather than using a shipped fake.** `GenericFakeChatModel`,
`FakeMessagesListChatModel` and every other fake in
`langchain_core.language_models.fake_chat_models` inherit `bind_tools` from `BaseChatModel`, which
raises `NotImplementedError`. `create_agent` always binds its tools, so any agent test built on a
shipped fake fails inside the first model call - and, because a failed worker leaves nothing on disk,
the validator then rejects for "the artifact was not written" and the test PASSES for entirely the
wrong reason. That is the exact trap this suite has to avoid: a green test over a worker that never
ran.

`ScriptedChatModel` implements `bind_tools` and emits a scripted sequence, so a test can drive a real
agent loop - tool call, tool result, next message - deterministically and offline.
"""

from __future__ import annotations

import itertools
from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from utils import critique_markdown_utils, frame_markdown_utils

__all__ = ["ScriptedChatModel", "tool_call", "says", "cut_off", "writes_artifact", "frame_notes", "second_pass", "critique"]


_call_seq = itertools.count(1)
# The key of a scripted turn that makes the call fail with its text, as a host error does
HOST_ERROR_KEY = "scripted_host_error"


def tool_call(name: str, **args: Any) -> AIMessage:
    """An assistant turn that calls one tool."""

    # The id must be UNIQUE per call. `add_messages` merges messages by id, so a fixed id like
    # `c_read_file` makes every repeated call to the same tool collapse into one - which silently caps
    # any loop a test is trying to build, and makes a second call to one tool look already answered.

    return AIMessage(content="", tool_calls=[{"name": name, "args": args,
                                              "id": f"call_{next(_call_seq)}",
                                              "type": "tool_call"}])


def host_error(text: str) -> AIMessage:
    """A call the host answers with an error, `text` its message: the scripted model raises it instead of replying."""

    return AIMessage(content="", additional_kwargs={HOST_ERROR_KEY: text})


def says(text: str) -> AIMessage:
    """An assistant turn that just talks - which ends the agent loop."""

    return AIMessage(content=text)


def cut_off(thinking: str = "") -> AIMessage:
    """An assistant turn the provider ended at its output cap - thinking, and nothing after it."""

    # `finish_reason: length` is what an OpenAI-compatible host reports when `max_tokens` ran out. A
    # thinking model spends the cap on its reasoning first, so the answer and the tool call never came
    # - which is also exactly what a worker that chose to stop looks like.

    return AIMessage(content="", additional_kwargs={"reasoning_content": thinking},
                     response_metadata={"finish_reason": "length"})


def thinks_only(thinking: str) -> AIMessage:
    """An assistant turn that ended on its own inside its thinking: no answer, and a normal stop."""

    return AIMessage(content="", additional_kwargs={"reasoning_content": thinking},
                     response_metadata={"finish_reason": "stop"})


def writes_artifact(relpath: str, content: str) -> AIMessage:
    """The common case: the worker writes its artifact."""

    return tool_call("write_artifact", relpath=relpath, content=content)


# A batch's title by its first frame, one topic each, so the batches of one test never share a title
BATCH_TITLES = ("Refund split limits", "Token exchange at the gateway", "Order intake overview", "Ledger closing rules",
                "Reseller approval thresholds", "Invoice numbering scheme", "Warehouse stock sync", "Payment retry budget",
                "Tenant colour coding", "Audit log retention", "Partner onboarding checklist", "Currency rounding mode")


def _own_facts(entry: dict) -> dict:
    """`entry` with facts no other frame's notes hold: the tests give every frame one shared record, and a fact another
    accepted frame holds word for word is refused (`FRAME_FACTS_COPIED`). Frame 0 keeps the record's facts as written."""

    return {**entry, "facts": own_facts(int(entry.get("frame") or 0), list(entry.get("facts") or []))}


def own_facts(frame: int,
              facts: list[str]) -> list[str]:
    """The facts a fake answer writes for `frame`: frame 0's as given, any other frame's marked with its week."""

    return facts if not frame else [f"{str(fact).rstrip('.')} in week {frame + 1}" for fact in facts]


def frame_notes(frames: list[dict] | None = None,
                batch_summary: str = "",
                atmosphere: str = "",
                batch_title: str = "",
                **record: Any) -> AIMessage:
    """Phase 3's answer, in the markdown the frame model writes: one `# Frame N` document per record, then the batch's
    account when one is given. One frame passes its record's own fields; a group passes `frames`."""

    records = [_own_facts(entry) for entry in [*(frames or []), *([record] if record else [])]]
    documents = [frame_markdown_utils.render_frame_document(entry) for entry in records]
    if batch_summary or atmosphere:
        first = min([int(entry.get("frame") or 0) for entry in records] or [0])
        documents.append(frame_markdown_utils.render_batch_document(
            {"batch_title": batch_title or BATCH_TITLES[first % len(BATCH_TITLES)], "batch_summary": batch_summary,
             "atmosphere": atmosphere}))

    return AIMessage(content="\n".join(documents), response_metadata={"done_reason": "stop"})


def second_pass(is_nothing_to_change: bool = False,
                additions: list[dict] | None = None,
                batch_summary: str = "",
                atmosphere: str = "") -> AIMessage:
    """R19's second look, in its markdown. Named, so the scripted model knows the test answers that call itself."""

    text = frame_markdown_utils.render_second_pass_document({"is_nothing_to_change": is_nothing_to_change,
                                                            "additions": list(additions or []),
                                                            "batch_summary": batch_summary, "atmosphere": atmosphere})

    return AIMessage(content=text, name=SECOND_PASS_NAME, response_metadata={"done_reason": "stop"})


def critique(rules: list[dict] | None = None) -> AIMessage:
    """A batch's critique, in its markdown: the anti-pattern rules it reports. Named, so the scripted model knows the
    test answers that call itself. No rule is "# Nothing to criticise", which is a real answer."""

    text = critique_markdown_utils.render_critique_document({"is_nothing_to_criticise": not rules,
                                                             "rules": list(rules or [])})

    return AIMessage(content=text, name=CRITIQUE_NAME, response_metadata={"done_reason": "stop"})


def nothing_open() -> AIMessage:
    """A batch's open-questions follow-up with nothing left open: the batch ends after it, with no answers call."""

    return AIMessage(content="# Batch\n\n## Open questions\n(none)\n", response_metadata={"done_reason": "stop"})


# What a scripted answer to one of phase 3's self-answered follow-ups is named, and how its call is told apart: by
# the heading of its message. Both run after EVERY batch, so a test about the batch itself would otherwise have to
# script an answer for each; the fake answers them itself, and a test ABOUT one scripts its own answer, which wins.
SECOND_PASS_NAME = "second_pass"
SECOND_PASS_MARK = "## Read your own notes again"
CRITIQUE_NAME = "critique"
CRITIQUE_MARK = "## Criticise your own notes"
# name -> (the mark in its message, what the fake answers when the script holds none).
SELF_ANSWERED = {SECOND_PASS_NAME: (SECOND_PASS_MARK, lambda: second_pass(is_nothing_to_change=True)),
                 CRITIQUE_NAME: (CRITIQUE_MARK, lambda: critique())}


class ScriptedChatModel(BaseChatModel):
    """Replays `script` one entry per model call. The last entry repeats if the script runs out."""

    # `bind_tools` returns self and ignores the tools: the script already decides what is called, and
    # validating the tool schema is the framework's job, not this fake's.

    script: list[BaseMessage] = []
    calls: list[list[BaseMessage]] = []
    # The tools the call being made was bound to. R19's second pass runs after EVERY batch, so a test about the batch
    # itself would otherwise have to script an answer for it: the fake answers that one call itself, with "nothing to
    # change", and consumes no script entry. A test ABOUT the second pass scripts its own answer (`second_pass`),
    # which wins.
    bound_names: list[str] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self.bound_names = [getattr(tool, "__name__", "") or str(getattr(tool, "name", "")) for tool in tools]

        return self

    def _find_unscripted_follow_up(self,
                                   messages: list[BaseMessage]) -> str:
        """The name of the self-answered follow-up this call asks for and the script holds no answer for; empty when
        the call is something else, or the script answers it."""

        for name, (mark, _answer) in SELF_ANSWERED.items():
            if _holds_mark(messages, mark) and not any(getattr(message, "name", None) == name
                                                       for message in self.script):
                return name

        return ""

    def _generate(self,
                  messages: list[BaseMessage],
                  stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None,
                  **kwargs: Any) -> ChatResult:
        index = len(self.calls)
        self.calls.append(list(messages))
        unscripted = self._find_unscripted_follow_up(messages)
        if unscripted:
            return ChatResult(generations=[ChatGeneration(message=SELF_ANSWERED[unscripted][1]())])
        if not self.script:
            message: BaseMessage = says("")
        else:
            message = self.script[min(self._count_scripted_calls(), len(self.script) - 1)]
        if message.additional_kwargs.get(HOST_ERROR_KEY):
            raise RuntimeError(message.additional_kwargs[HOST_ERROR_KEY])

        return ChatResult(generations=[ChatGeneration(message=message)])

    def _count_scripted_calls(self) -> int:
        """How many calls before this one took an answer from the script: the follow-up calls the fake answered itself
        are not among them, so a batch's script does not slip by one per batch."""

        return sum(1 for call in self.calls[:-1] if not self._find_unscripted_follow_up(call))

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _holds_mark(messages: list[BaseMessage],
                mark: str) -> bool:
    return any(mark in str(part.get("text", "")) for message in messages
               if isinstance(message.content, list) for part in message.content if isinstance(part, dict))


def scripted(*script: BaseMessage) -> ScriptedChatModel:
    """A fresh model per test - `calls` accumulates, so instances are never shared."""

    return ScriptedChatModel(script=list(script), calls=[])
