"""The names the speech writes one way and the screens another, too far apart for the program to respell (DEFECTS 339).

The speech model wrote a talk's own subject, the library "ratatui" its slides show, as "Ratatouille" 60 times, and
eight frames' notes carried the garbled name; the program respells a heard name only one or two letters off a screen
word (`item_validator.use_screen_spelling_of_heard_names`). A heard word said twenty times or more and on no screen,
beside a word a screen quotes and the speech says a fifth as often at most, that starts with the same three letters, is no inflection of it
(neither starts the other) and is alike by 0.7 or more, while the screen word is no dictionary word, is named to the
worker as possibly that screen word, for it to judge; nothing is respelled. Said three times or more, over the public
runs it named about 30 pairs of inflections and other words beside two garblings; said twenty times or more, it names
"Ratatouille" for "ratatui" alone.
"""

from __future__ import annotations

import collections
import difflib
import functools
import re
from pathlib import Path

__all__ = ["list_garbled_names", "map_misheard_screen_names"]

# DEFECTS 620 and 647: a name heard once or twice, written as heard ("RepGL" for the WebGL a later slide shows, "HydroBus"
# for the Hydrabus a screen shows). Over the public runs, a heard name-like word (a capital or a digit) in no word list
# and on no screen, beside the one screen word two letters of edits off at most that is written in mixed case and in no
# word list, named 3 pairs, each a misheard name; with capitals-only screen words and no German list, 2 German words
# more by chance
_NAME_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_MOST_EDITS = 2
_GERMAN_WORDS = Path(__file__).resolve().parents[2] / "word-lists" / "deu.txt"

_WORD = re.compile(r"[^\W\d_]+")
# Only what a screen shows: an outline's own words ("centre", "left") are no screen text
_QUOTE = re.compile(r'"([^"]+)"')
_LEAST_HEARD_LETTERS = 6
_LEAST_SHOWN_LETTERS = 5
# A name said this often is what the talk is about, where a garbling costs most: over the public runs, the words the
# other tests name are heard 3 to 31 times, inflections and other words up to 11, the one garbled subject 31
_LEAST_HEARD = 20
_LEAST_SCREENS = 1
# The screen word may be heard too, a fifth as often at most: "ratatui" twice against "Ratatouille" 31 times
_MOST_HEARD_SHARE = 0.2
_SAME_START = 3
_LEAST_LIKENESS = 0.7
_DICTIONARY = Path("/usr/share/dict/words")


@functools.lru_cache(maxsize=1)
def _read_dictionary() -> frozenset[str]:
    """The system's word list, lower case; empty where it is not installed, and then nothing is named."""

    try:
        return frozenset(line.strip().casefold() for line in _DICTIONARY.read_text(encoding="utf-8").splitlines())
    except OSError:
        return frozenset()


@functools.lru_cache(maxsize=1)
def _read_listed_words() -> frozenset[str]:
    """The system's word list and the playbook's German one, lower case."""

    try:
        german = frozenset(line.strip().casefold()
                           for line in _GERMAN_WORDS.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        german = frozenset()

    return _read_dictionary() | german


def _count_edits(first: str,
                 second: str) -> int:
    """The letters to change, add or remove to turn `first` into `second`."""

    row = list(range(len(second) + 1))
    for position, letter in enumerate(first, start=1):
        diagonal, row[0] = row[0], position
        for other_position, other_letter in enumerate(second, start=1):
            diagonal, row[other_position] = row[other_position], min(
                row[other_position] + 1, row[other_position - 1] + 1, diagonal + (letter != other_letter))

    return row[-1]


def _is_name_like(word: str) -> bool:
    return word[0].isupper() or any(letter.isdigit() for letter in word)


def map_misheard_screen_names(transcript: str,
                              screens: list[str]) -> dict[str, str]:
    """Each heard word of `transcript` that may be a name `screens` spell otherwise, mapped to that spelling: a word with
    a capital or a digit, in no word list and on none of `screens`, with one screen word nearest it, two edits off at
    most, written in mixed case and in no word list. Empty with no word list."""

    listed = _read_listed_words()
    if not listed:
        return {}
    shown = {word.casefold(): word for screen in screens for word in _NAME_WORD.findall(screen)}
    names = {key: word for key, word in shown.items() if key not in listed
             and any(letter.isupper() for letter in word) and not word.isupper()}
    misheard: dict[str, str] = {}
    for word in dict.fromkeys(_NAME_WORD.findall(transcript)):
        key = word.casefold()
        if key in listed or key.rstrip("s") in listed or key in shown or not _is_name_like(word):
            continue
        near = sorted((_count_edits(key, name), name) for name in names if abs(len(name) - len(key)) <= _MOST_EDITS)
        if near and near[0][0] <= _MOST_EDITS and (len(near) == 1 or near[1][0] > near[0][0]):
            misheard[word] = names[near[0][1]]

    return misheard


def list_garbled_names(speech: str,
                       transcript: str,
                       screens: list[str]) -> list[tuple[str, str]]:
    """Each word of `speech` that may be a screen word garbled, with that word: said `_LEAST_HEARD` times or more in
    `transcript` and on none of `screens`, beside a word `screens` quote and `transcript` says a fifth as often at most."""

    dictionary = _read_dictionary()
    if not dictionary:
        return []
    heard = collections.Counter(word.casefold() for word in _WORD.findall(transcript))
    shown_on = collections.Counter(word for screen in screens for word in {
        token.casefold() for quote in _QUOTE.findall(screen) for token in _WORD.findall(quote)})
    names = []
    for word in dict.fromkeys(word.casefold() for word in _WORD.findall(speech)):
        if len(word) < _LEAST_HEARD_LETTERS or heard[word] < _LEAST_HEARD or shown_on[word]:
            continue
        match = next((shown for shown, count in shown_on.items()
                      if count >= _LEAST_SCREENS and len(shown) >= _LEAST_SHOWN_LETTERS
                      and heard[shown] <= _MOST_HEARD_SHARE * heard[word]
                      and shown not in dictionary and shown[:_SAME_START] == word[:_SAME_START]
                      and not shown.startswith(word) and not word.startswith(shown)
                      and difflib.SequenceMatcher(None, word, shown).ratio() >= _LEAST_LIKENESS), None)
        if match:
            names.append((word, match))

    return names
