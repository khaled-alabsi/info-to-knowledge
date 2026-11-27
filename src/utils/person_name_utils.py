"""People named on a screen, from the words the text reader found on it (each `{"text", "box"}`). Pure functions.

Two steps read them: phase 4's check that no person's name stays readable on a placed cut, and phase 2's test that a
screen of call tiles, each labelled with its person's name, is a call window and no shared screen.
"""

from __future__ import annotations

import re

__all__ = ["HONORIFIC", "PERSON_NAME", "GUEST_MARK", "list_listed_names"]

# The word that introduces a person by name on a form or a record
HONORIFIC = re.compile(r"(?i)^(?:herr|frau|mr|mrs|ms|miss|dr|prof)\.?$")
# A name beside it: a capitalised word, hyphens and apostrophes allowed, no digits
PERSON_NAME = re.compile(r"^[^\W\d_][\w'’-]{1,30}$", re.UNICODE)
# A chat tool's mark of a guest account after a person's name, whole or cut short at the cut's edge
GUEST_MARK = re.compile(r"(?i)^\(ex(?:t\w*)?\)?$")


# How many words after "<Last>," are read for the first names and the guest mark: a call tile wrote a person with two
# first names, "<Last>, <First> <Second> (extern)", and a look two words on missed its mark (2026-10-10)
_NAME_WORDS_READ = 4


def _find_guest_mark(same_line: list[dict]) -> int | None:
    """Where a guest mark stands after one or more capitalised first names, or None."""

    for at, word in enumerate(same_line):
        if at and GUEST_MARK.match(word["text"]):
            return at
        if not PERSON_NAME.match(word["text"]) or not word["text"][:1].isupper():
            return None

    return None


def _is_in_list(words: list[dict],
                index: int) -> bool:
    """Whether the pair starting at `index` goes on a list of words with commas: the word before it on its line ends
    with one. "Kauf, Tausch, Sparplan" read as the pair "Tausch, Sparplan", and a slide of it with a photo was taken for
    a call tile (2026-10-10); no person's label stands inside such a list."""

    if index == 0:
        return False
    before, word = words[index - 1], words[index]

    return before["text"].endswith(",") and abs(before["box"][1] - word["box"][1]) <= word["box"][3] - word["box"][1]


def _join_boxes(words: list[dict]) -> list[int]:
    boxes = [word["box"] for word in words]

    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def list_listed_names(words: list[dict]) -> tuple[list[dict], list[dict]]:
    """Each "<Last>, <First>" pair, the way a chat title, a file list's author column or a call tile writes a person,
    as `{"text", "box"}` over its words: first the pairs marked as a person (an honorific after the comma, "<Last>,
    Dr.", or a guest mark after the pair, "(extern)", "(ext"), then the pairs with neither, which read the same as two
    nouns in a list ("Kauf, Tausch") and are a person only where the caller says so."""

    marked, unmarked = [], []
    for index, word in enumerate(words[:-1]):
        text = word["text"]
        if not text.endswith(",") or not PERSON_NAME.match(text[:-1]) or not text[:1].isupper():
            continue
        following = words[index + 1:index + 1 + _NAME_WORDS_READ]
        same_line = [after for after in following if abs(after["box"][1] - word["box"][1]) <= word["box"][3] - word["box"][1]]
        if not same_line:
            continue
        is_honorific = bool(HONORIFIC.match(same_line[0]["text"]))
        guest_at = _find_guest_mark(same_line)
        is_guest = guest_at is not None
        if is_honorific:
            named = [word, *same_line[:2]]
        elif is_guest:
            named = [word, *same_line[:guest_at + 1]]
        elif _is_in_list(words, index):
            continue
        elif PERSON_NAME.match(same_line[0]["text"]) and same_line[0]["text"][:1].isupper():
            named = [word, same_line[0]]
        else:
            continue
        person = {"text": " ".join(item["text"] for item in named), "box": _join_boxes(named)}
        (marked if is_honorific or is_guest else unmarked).append(person)

    return marked, unmarked
