"""The words said over a frame that only the next frame's screen shows (DEFECTS 132).

A demo's words come before the change they describe: the presenter says what they are about to type, and the frame is
taken while the screen still shows the step before. On Lecture 9's regex tester, of 25 new screen words said near
their screen, 15 were said over the frame before and 10 over their own; the notes of 9 of 17 steps gave a frame the
value its speech announced. A word said over a frame, missing from its screen and new on the next one, is that
announcement, and the frame's message names it.
"""

from __future__ import annotations

import re

__all__ = ["list_announced_words", "is_announcing"]

# A word short or common enough to stand on any screen tells nothing: one of five letters or more, or a value holding a
# digit (a number typed, "99" for "95")
_MIN_LETTERS = 5
_COMMON = frozenset("about above actually after again always because before being below between could doing during "
                    "every first going great might never other right should since still their there these thing "
                    "think those three through today under until using where which while would yeah".split())
# Named only when it says enough: two such words, or one value
_MIN_WORDS = 2
_NOT_WORD = re.compile(r"[^\w]+")


def _list_tokens(text: str) -> set[str]:
    return {_NOT_WORD.sub("", word.casefold()) for word in text.split()} - {""}


def _is_telling(word: str) -> bool:
    return any(character.isdigit() for character in word) or (len(word) >= _MIN_LETTERS and word not in _COMMON)


def list_announced_words(said: str,
                         screen_words: list[str],
                         next_screen_words: list[str]) -> list[str]:
    """The words of `said` (what was said over the frame) missing from `screen_words` (its screen's) and on
    `next_screen_words` (the next frame's), sorted; empty unless they are MIN_WORDS words or one value."""

    screen = _list_tokens(" ".join(screen_words))
    upcoming = _list_tokens(" ".join(next_screen_words)) - screen
    announced = sorted(word for word in _list_tokens(said) & upcoming if _is_telling(word))
    has_value = any(any(character.isdigit() for character in word) for word in announced)

    return announced if len(announced) >= _MIN_WORDS or has_value else []


def is_announcing(quote: str,
                  screen: str,
                  earlier_screens: list[str]) -> bool:
    """Whether `quote`, said over an earlier frame, announces this frame's screen: a telling word of it stands on
    `screen` (this frame's outline) and on none of `earlier_screens` (the outlines of the frames it was said over). The
    same contract as `list_announced_words`, read from the frame the words are about: "there's a nice tool ... called
    Reuse" said over the frame before the REUSE bullet appeared (38c3-797 frame 50)."""

    shown = _list_tokens(screen) - _list_tokens(" ".join(earlier_screens))

    return any(_is_telling(word) for word in _list_tokens(quote) & shown)
