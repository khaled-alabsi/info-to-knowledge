"""What the conversation of phase 4 or 5 restarts from when it is compacted: the least a follow-up needs, rebuilt by
the program from disk. REQUIREMENTS.md R12. The fields are in the order the starting messages are sent, from the part
that changes least to the part that changes most, so a host that reuses an unchanged prefix reuses as much as it can."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["StartingMessagesSpec"]


@dataclass(frozen=True)
class StartingMessagesSpec:
    system_text: str                                # the instruction, sent once and kept by every compaction
    plan_text: str                                  # working title, executive summary draft, agenda; the moments before it
    notes: list[str]                                # decisions the agent recorded for later steps
    done_lines: list[str]                           # one line per accepted or blocked follow-up
    topic_parts_text: str                           # the current topic's accepted parts, whole
    follow_up_text: str                             # the follow-up the worker is working on
    findings_text: str                              # the last rejection's findings and attempt; empty on a first try
