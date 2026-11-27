"""What one section is judged against: the ids it may cite, the ids it must cite, and which speech entry holds each
transcript line it may name."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SectionJudgingContext"]


@dataclass(frozen=True)
class SectionJudgingContext:
    entry_ids: list[str]                                        # the ids it may cite; any other is foreign
    required_ids: list[str] | None = None                       # the ids it must cite; all of `entry_ids` when None
    line_owners: dict[str, str] = field(default_factory=dict)   # transcript line id -> the speech entry holding it
    speech_texts: list[str] = field(default_factory=list)       # what was said in its entries, which no sentence copies
    screen_texts: list[str] = field(default_factory=list)       # its markdown notes: the screen's text, copied as it stands
    decision_ids: list[str] = field(default_factory=list)       # notes that state a decision, cited by a marked decision
    kept_ids: list[str] = field(default_factory=list)           # web addresses, action items, questions: never set aside
    code_blocks: dict[str, list[str]] = field(default_factory=dict)  # entry id -> the code blocks its text holds
    # every note's text, facts included: what the notes state a number with (a fact's "thought for 2s", DEFECTS 590)
    note_texts: list[str] = field(default_factory=list)
