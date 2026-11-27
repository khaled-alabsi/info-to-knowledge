"""The numbers the conversation of phase 4 or 5 stays within. REQUIREMENTS.md R12. Read from the settings once per phase."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ConversationLimitsSpec"]


@dataclass(frozen=True)
class ConversationLimitsSpec:
    size_limit_tokens: int                           # the configured limit; it compacts past `resolve_size_limit` of it, which the window can lower
    window_tokens: int                               # the loaded model's context window
    max_output_tokens: int                           # the reply's limit, thinking included, kept free in the window
    chars_per_token: float                           # the estimate's ratio for text
    frame_tokens: int                                # the estimate's fixed cost of one frame
    max_calls: int                                   # tool calls one follow-up may make, reads and submits together
    max_attempts: int                                # rejected attempts one follow-up may make before it is blocked
    should_ask_why_after_rejection: bool = False     # one turn offering only `report_problem` before a block
    can_dry_run_chapters: bool = False               # `check_chapter`, and the check of an attempt sent with none before it
    should_stop_on_frame_tool_refusal: bool = False  # a refused frame tool blocks the follow-up
