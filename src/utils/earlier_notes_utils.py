"""The lines of earlier frames' notes that use a rare word of an open question, for a batch's answers step (DEFECTS 268).

A batch's answers step read its own batch's notes, words and frames only, and kept "What is tref?" open while an earlier
batch's slide named it ("tref (reference to the base table)", froscon talk 3143 frame 39); phase 4 put it among the
document's open points. A word of the question is looked up when it stands in the notes of at most a few earlier frames:
a word every frame uses ("database", "vector") answers nothing.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[^\W_][\w\-]{2,}")
# A question's word is looked up when at most this many earlier frames' notes hold it
MOST_FRAMES_A_WORD = 3
# The most lines handed for one question: the answers step reads them beside its own batch
MOST_LINES_A_QUESTION = 8
_FIELDS = ("facts", "take_aways")


def _list_lines(record: dict) -> list[str]:
    lines = [str(entry) for field in _FIELDS for entry in record.get(field) or []]
    lines += [line.strip().lstrip("- ").strip() for line in str(record.get("screen") or "").splitlines() if '"' in line]

    return [line for line in lines if line]


# A word this long or longer is matched without a final "s", so a plural meets its singular: a question about "MCPs"
# was never handed the earlier line explaining "MCP", and stayed open for the document (Lecture 7, DEFECTS 581)
_PLURAL_LEAST_LETTERS = 4


def _list_words(text: str) -> set[str]:
    words = {word.casefold() for word in _WORD.findall(text)}

    return {word[:-1] if len(word) >= _PLURAL_LEAST_LETTERS and word.endswith("s") else word for word in words}


def list_earlier_lines(questions: list[str],
                       earlier: list[dict]) -> list[tuple[int, str]]:
    """Each (frame number, line) of the `earlier` records' notes holding a rare word of one of the `questions`, in frame
    order, each line once, at most MOST_LINES_A_QUESTION a question."""

    lines = [(int(record.get("index", 0)), line) for record in earlier for line in _list_lines(record)]
    frames_by_word: dict[str, set[int]] = {}
    for frame, line in lines:
        for word in _list_words(line):
            frames_by_word.setdefault(word, set()).add(frame)
    found: list[tuple[int, str]] = []
    for question in questions:
        rare = {word for word in _list_words(question) if 0 < len(frames_by_word.get(word, ())) <= MOST_FRAMES_A_WORD}
        # The lines sharing the most of the question's rare words first: one sharing a single common-enough word is the
        # weakest lead. Among lines sharing as many, each frame's best line comes before any frame's next: taken in
        # frame order, one frame's five lines and two frames' lines on "answer" filled the eight places, and frame 42,
        # whose notes say the index must fit in RAM, was never handed to the review (froscon talk 3143)
        matching = sorted(((frame, line) for frame, line in lines if rare & _list_words(line)),
                          key=lambda entry: -len(rare & _list_words(entry[1])))
        taken: dict[int, int] = {}
        ranked: list[tuple[int, int, tuple[int, str]]] = []
        for frame, line in matching:
            ranked.append((-len(rare & _list_words(line)), taken.get(frame, 0), (frame, line)))
            taken[frame] = taken.get(frame, 0) + 1
        matching = [entry for _shared, _turn, entry in sorted(ranked, key=lambda each: each[:2])]
        for entry in matching[:MOST_LINES_A_QUESTION]:
            if entry not in found:
                found.append(entry)

    return sorted(found)
