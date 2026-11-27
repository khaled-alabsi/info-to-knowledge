"""Everything one compaction needs to know about the follow-up it is compacting, gathered once where the loop calls it. Lives for one call."""

from __future__ import annotations

from dataclasses import dataclass

from models.follow_up_spec import FollowUpSpec
from models.write_conversation_state import WriteConversationState

__all__ = ["CompactionRequest"]


@dataclass(frozen=True)
class CompactionRequest:
    worker: str                                     # the worker's key: `executive_summary_draft`, `parts`, `draft_headings`, `chapter`, ...
    session: WriteConversationState
    spec: FollowUpSpec
    findings_text: str                              # the last findings, kept in the starting messages
    attempts: int                                   # attempts rejected so far in this follow-up
    calls: int                                      # tool calls made so far
    max_calls: int                                  # this follow-up's own limit, else the conversation's
    tokens: int                                     # the conversation's size now
    size_limit_tokens: int                          # the resolved limit (`resolve_size_limit`), not `limits.size_limit_tokens`
    tail_start: int                                 # where the last turns kept word for word begin; the summary covers what is before it
    tools_tokens: int = 0                           # what the tools cost in every prompt, counted with the starting messages
