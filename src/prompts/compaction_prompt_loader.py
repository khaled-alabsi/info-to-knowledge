"""Each worker's compaction texts, read from `agent-context/compaction/` at every call, so an edit reaches the next compaction.

`conversation/<worker>/summary.md` holds two placeholders the caller fills, `{allowance}` (tokens) and `{messages}` (the
history as text). `conversation/<worker>/handoff.md` holds `{summary}`, the check placeholders
`compaction_service.check_summary` fills for that worker and the measured values `compaction_service` computes for that
worker. The table of contents has no prompt: the program builds it, no follow-up asks for it.

`cut-thinking/<step>.md` is phase 3's, read by `thinking_compaction_service`, and holds `{allowance}` and `{thinking}`;
`cut-thinking/handoff.md` and `finish.md` hold `{summary}`, `finish-mistakes.md` holds `{mistakes}`.
"""

from __future__ import annotations

from prompts import system_prompt_builder

__all__ = ["WORKER_OF_SUBMIT_TOOL", "CUT_THINKING_STEPS", "COMPACTION_DIR", "read_summary_prompt", "read_handoff",
           "read_tail_line", "read_write_shorter", "read_cut_thinking_prompt", "read_cut_thinking_handoff",
           "read_cut_thinking_finish", "read_cut_thinking_finish_mistakes"]

# The worker of a follow-up is named by its submit tool.
WORKER_OF_SUBMIT_TOOL = {"submit_executive_summary_draft": "executive_summary_draft", "submit_agenda": "parts",
                         "submit_draft_headings": "draft_headings", "submit_chapter": "chapter", "submit_final": "summary",
                         "submit_review": "review_critique", "submit_resolution": "review_resolution"}
# Phase 3's follow-ups whose reply, cut while still thinking, is compacted: each has its own prompt
CUT_THINKING_STEPS = ("critique", "second_pass", "open_questions", "answer_questions")
# Where the compaction texts live under `agent-context/`
COMPACTION_DIR = "compaction"
CONVERSATION_DIR = f"{COMPACTION_DIR}/conversation"  # phases 4 and 5: one folder per worker
CUT_THINKING_DIR = f"{COMPACTION_DIR}/cut-thinking"  # phase 3: a reply cut at its output limit while still thinking


def read_summary_prompt(worker: str) -> str:
    """The prompt that asks `worker`'s model to summarise its own conversation."""

    return system_prompt_builder.read_context_text(f"{CONVERSATION_DIR}/{worker}/summary.md") + "\n"


def read_handoff(worker: str) -> str:
    """The message `worker`'s conversation gets right after it was compacted."""

    return system_prompt_builder.read_context_text(f"{CONVERSATION_DIR}/{worker}/handoff.md") + "\n"


def read_tail_line() -> str:
    """The line added under a handoff when the last turns were kept: the summary covers only what came before them."""

    return system_prompt_builder.read_context_text(f"{CONVERSATION_DIR}/tail-line.md")


def read_write_shorter() -> str:
    """The request to write a summary again under its allowance, holding `{first_tokens}`, `{allowance}` and `{summary}`."""

    return system_prompt_builder.read_context_text(f"{CONVERSATION_DIR}/write-shorter.md")


def read_cut_thinking_prompt(step: str) -> str:
    """The prompt that asks phase 3's `step` to write down what its cut thinking had settled."""

    return system_prompt_builder.read_context_text(f"{CUT_THINKING_DIR}/{step}.md") + "\n"


def read_cut_thinking_handoff() -> str:
    """What the next attempt of a step whose reply was cut while thinking opens with, around `{summary}`."""

    # The blank line before it sets it off from the request text it follows
    return "\n" + system_prompt_builder.read_context_text(f"{CUT_THINKING_DIR}/handoff.md") + "\n"


def read_cut_thinking_finish() -> str:
    """The request to write the answer from the cut thinking's summary alone, in the same attempt."""

    return system_prompt_builder.read_context_text(f"{CUT_THINKING_DIR}/finish.md") + "\n"


def read_cut_thinking_finish_mistakes() -> str:
    """The request to write a critique's rules from the mistakes its cut thinking found, and from nothing else."""

    return system_prompt_builder.read_context_text(f"{CUT_THINKING_DIR}/finish-mistakes.md") + "\n"
