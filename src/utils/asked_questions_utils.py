"""The questions someone other than the frame's main speaker asks in what is said over it (DEFECTS 538).

A talk's question round is said over its closing slide: on 38c3-797 frame 63 an attendee asked about documentation
besides Markdown and the speaker answered, and the notes recorded no question asked, though the instruction asks for
each with its answer. Over 746 public frames with speakers, 5 hold another speaker's question with none recorded, about
half of them real questions; the rest a filler "Yes?" or a rhetorical one, so the program names them and the worker
judges, never a refusal.
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = ["list_asked_questions"]

_TURN = re.compile(r"(Speaker [A-Z]+):\s*(.*?)(?=Speaker [A-Z]+:|$)", re.S)
_SENTENCE = re.compile(r"[^.!?]*\?")
# A question of fewer words is a filler or a call ("Yes?", "Huh?"), never one a reader needs
_MIN_WORDS = 4


def list_asked_questions(speech: str) -> list[str]:
    """The questions of `speech` (a frame's `transcriptCurrent`, its turns written "Speaker X: ...") asked by a speaker
    other than the one who says the most, each of _MIN_WORDS words or more, in the order said."""

    turns = _TURN.findall(speech)
    said = Counter()
    for speaker, text in turns:
        said[speaker] += len(text.split())
    main = said.most_common(1)[0][0] if said else ""

    return [sentence.strip() for speaker, text in turns if speaker != main
            for sentence in _SENTENCE.findall(text) if len(sentence.split()) >= _MIN_WORDS]
