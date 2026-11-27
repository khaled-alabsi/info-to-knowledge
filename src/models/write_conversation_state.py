"""Phase 4's conversation while it runs (REQUIREMENTS.md R12), or phase 5's (R16): the model, the messages sent so far,
the reading tools' state, the starting messages it restarts from when compacted, and the host's last count of the
prompt. Lives for one phase; everything a resume needs is on disk, never only here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from models import tool_set_vocabulary
from models.conversation_limits_spec import ConversationLimitsSpec
from models.review_reading_context import ReviewReadingContext
from models.run_context import RunContext
from models.starting_messages_spec import StartingMessagesSpec
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState

__all__ = ["WriteConversationState"]


@dataclass
class WriteConversationState:
    ctx: RunContext
    model: BaseChatModel
    reading: WriteReadingContext | ReviewReadingContext
    reading_state: WriteReadingState
    starting_messages: StartingMessagesSpec                    # the standing parts; each follow-up fills in its own
    limits: ConversationLimitsSpec
    # whose tools it offers: phase 4's or phase 5's
    tool_set: str = tool_set_vocabulary.WRITE_TOOL_SET
    # whether the recording has frames: an audio recording's conversation is offered no frame tool
    has_frames: bool = True
    messages: list[BaseMessage] = field(default_factory=list)
    reported_tokens: int = 0                                    # the host's count of the last call's prompt; 0 when unknown
    reported_messages: int = 0                                  # how many of `messages` that count covers
    next_message: int = 1                                       # the number the next appended message's id gets
    turns_start: int = 0                                        # where this follow-up's own turns begin in `messages`; a compaction keeps none before it
    link_targets: dict[str, str] = field(default_factory=dict)  # topic id -> title, once the outlines are written
    positions: dict[str, str] = field(default_factory=dict)     # slug -> its console name: "writing chapter 4 of 9", "agenda"
    # The scaffold once the program has built the table of contents: (chapter title, [(heading title, [section slugs])])
    scaffold: list[tuple[str, list[tuple[str, list[str]]]]] = field(default_factory=list)
    # slug -> every note id an earlier `check_chapter` draft of that follow-up cited: a rewrite dropped notes its earlier
    # draft covered, and covered them again only after three more checks
    checked_ids: dict[str, set[str]] = field(default_factory=dict)
    # slug -> the markdown of that follow-up's last checked draft, which `edit_chapter_draft` changes block by block:
    # a part rewritten whole for each finding lost what its draft before had right (DEFECTS 584)
    checked_drafts: dict[str, str] = field(default_factory=dict)
    conversation_log_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.conversation_log_path = self.ctx.paths.output_root / tool_set_vocabulary.CONVERSATION_LOG_RELPATHS[self.tool_set]
