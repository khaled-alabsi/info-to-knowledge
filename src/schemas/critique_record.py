"""The vocabulary of a batch's critique: the anti-pattern rules it reports, and nothing about the meeting.

A critique is an ARTIFACT, not an edit: the call that writes it reports what went wrong and changes no
frame. Each finding is a rule stated for any meeting - what the anti-pattern is, how to avoid it - with the frames of
this batch it was seen on beside it. That is what makes the file safe to carry into
`agent-context/instructions/frames_processing.md`, and what lets phase 6 group one run's rules and count them.

The repair that follows reads these rules and returns the frame-addition shape of `schemas/frame_record.py`; this
module describes only the report.
"""

from __future__ import annotations

__all__ = ["CHECKED_TITLE", "CRITIQUE_TITLE", "NOTHING_TO_CRITICISE_TITLE", "NONE_FOUND", "RULE_LINES", "SEEN_ON_LABEL",
           "WHERE_LABEL"]

# The section, before the rules, holding one line per mistake the instruction lists, each answered once: the frames it
# was seen on, or "none". Without a place for each answer, looped critiques walked the list again and again (12% of
# critique attempts, against 0% to 2% of every other step), never knowing they were done
CHECKED_TITLE = "Checked"
NONE_FOUND = "none"

# The document's own level-1 heading, and what a reply with no finding says instead of one.
CRITIQUE_TITLE = "Critique"
NOTHING_TO_CRITICISE_TITLE = "Nothing to criticise"

# A rule is one `## <name>` section holding these lines, each by its label. `field -> the label that opens its line`.
# Lines, not sections, because a reader of the run's whole rule set reads dozens of rules.
RULE_LINES = {
    "what_it_is": "What it is",
    "how_to_avoid": "How to avoid it",
}
# The line that names the frames of this batch the anti-pattern was seen on: the only place a frame number belongs,
# since the rule itself must hold for any meeting.
SEEN_ON_LABEL = "Seen on"
# One line per frame the rule was seen on, naming the note it means in this batch's own words: handed to the fix,
# which otherwise compared line after line to find it and looped. Kept out of the rule's own lines, so the
# rule stays one any meeting can follow.
WHERE_LABEL = "Where"
