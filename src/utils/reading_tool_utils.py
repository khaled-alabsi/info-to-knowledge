"""What the reading tools of phase 4 (R12) and phase 5 (R16) share: a read returned a chunk at a time, a word found
in a line, and the notes an agent keeps within their size. Pure functions."""

from __future__ import annotations

import re

from models.reading_errors import ReadArgumentError
from utils import clock_utils


__all__ = ["select_chunk", "update_notes", "read_chapter_number",
           "read_whole_number", "is_word_in", "list_partial_lines", "explain_no_line"]


_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
_DIGITS_OF = {word: str(number) for number, word in enumerate(_NUMBER_WORDS)}
_WORD_OF = {digits: word for word, digits in _DIGITS_OF.items()}


def _parse_whole_number(key: str,
                        sent: object) -> int:
    """`sent`, the model's `key`, as the whole number it names, written as a number or as text ("12"). Raises
    ReadArgumentError for anything else."""

    # A decimal number such as 2.5 would be cut to 2, and True would be 1: the tool would act on a number the model
    # did not send
    refusal = f"`{key}` must be a whole number, such as 12; it was {sent!r}"
    if isinstance(sent, bool) or (isinstance(sent, float) and not sent.is_integer()):
        raise ReadArgumentError(refusal)
    try:
        return int(sent)
    except (TypeError, ValueError) as exc:
        raise ReadArgumentError(refusal) from exc


def _split_chunks(blocks: list[str],
                  read_chars: int) -> list[list[str]]:
    """`blocks` cut into chunks of at most `read_chars` characters, never less than one block a chunk."""

    chunks: list[list[str]] = [[]]
    for block in blocks:
        if chunks[-1] and sum(len(existing) + 2 for existing in chunks[-1]) + len(block) > read_chars:
            chunks.append([])
        chunks[-1].append(block)

    return chunks


def _is_at_word_start(word: str,
                      folded_text: str) -> bool:
    # A word matches where a word starts, so "token" finds "tokens"; a number matches whole. Matched anywhere, "figure"
    # found "configured" and "3" found "ABC3" and "figure-13", and those hits buried the line asked for (2026-10-02)
    end = r"(?![\w])" if word in _DIGITS_OF else r"(?!\d)" if word.isdigit() else ""
    start = r"(?<![\w])"

    return re.search(rf"{start}{re.escape(word)}{end}", folded_text) is not None


def read_whole_number(args: dict,
                      key: str,
                      default: int | None) -> int:
    """The whole number the model sent as `key`, written as a number or as text ("12"); `default` when it sent none.
    Raises ReadArgumentError, which the tool answers in words, for a value that is no number or a missing one with no
    default."""

    sent = args.get(key)
    if sent in (None, ""):
        if default is None:
            raise ReadArgumentError(f"`{key}` is missing: give it, a whole number such as 12")
        return default

    return _parse_whole_number(key, sent)


def read_chapter_number(chapter: object) -> object:
    """A chapter given as text ("1") as the number it names: a host's tool parser sent every chapter as text, and no
    section, and no read, matched the number it was compared with (2026-10-03, every chapter read empty). Anything
    but a string of digits comes back unchanged."""

    if isinstance(chapter, str) and chapter.strip().isdigit():
        return int(chapter.strip())

    return chapter


def select_chunk(blocks: list[str],
                 read_chars: int,
                 chunk: int) -> str:
    """Chunk `chunk` of `blocks`, each chunk at most `read_chars` characters but never less than one block."""

    if not blocks:
        return "Nothing matches."
    chunks = _split_chunks(blocks, read_chars)
    if not 1 <= chunk <= len(chunks):
        return f"There is no chunk {chunk}; there are {len(chunks)}."
    more = f"\n\n(chunk {chunk} of {len(chunks)}; ask for chunk {chunk + 1} for more)" if chunk < len(chunks) else ""

    return "\n\n".join(chunks[chunk - 1]) + more


def is_word_in(word: str,
               folded_text: str) -> bool:
    """Whether `word` (folded) stands in `folded_text`, a number found as a word or as digits: a search for "ten
    developers" missed "10 people" in an automatic transcript, which writes most numbers as digits (2026-10-01)."""

    other = _DIGITS_OF.get(word) or _WORD_OF.get(word)

    return _is_at_word_start(word, folded_text) or (other is not None and _is_at_word_start(other, folded_text))


def list_partial_lines(lines: list[str],
                       words: list[str],
                       texts: list[str] | None = None) -> list[str]:
    """The lines holding at least two of `words`, the most first; none for fewer than three words. A worker searching
    "latency delay fail error retry" meant them as alternatives, and no line holds them all (2026-09-30). `texts`,
    when given, is what each line says, searched instead of the rendered line: searched with its id and its time,
    "May 25 August 23" matched lines spoken at 1:23:25."""

    if len(words) < 3:
        return []
    searched = texts if texts is not None else lines
    counted = [(sum(is_word_in(word, text.casefold()) for word in words), line) for line, text in zip(lines, searched)]

    return [line for count, line in sorted(counted, key=lambda pair: -pair[0]) if count >= 2]


def explain_no_line(words: list[str],
                    last_time: float | None = None,
                    language: str = "") -> str:
    """The reply to a read that found no line: with several words, why, and what to ask instead; with the time the
    transcript ends, where the recording stops. A worker asked for eleven words at once, and for an hour past the
    recording's end, and read "Nothing matches." each time (2026-09-30)."""

    reasons = []
    # Two words found nowhere together are each searched alone: told to "ask for one or two words", a reviewer that had
    # asked for two read advice it had already followed (pdf-05)
    if len(words) == 2:
        reasons.append("No line holds both words: search each one alone; to read every line between two times, call "
                       "`read_transcript` with a time range and no words.")
    elif len(words) > 2:
        reasons.append(f"No line holds all {len(words)} words: a line matches only when it holds every word given. Ask "
                       f"for one or two words at a time; to read every line between two times, call `read_transcript` with a time "
                       f"range and no words.")
    if last_time is not None:
        reasons.append(f"The transcript runs from 00:00 to {clock_utils.render_clock(last_time)}.")
    # A reviewer searched a German transcript in English words three times in a row (2026-10-03)
    if language:
        reasons.append(f"The transcript is in the language the speakers spoke ({language}): search with words in that "
                       f"language, never with a translation.")

    return " ".join(reasons) or "Nothing matches."


def update_notes(notes: list[str],
                 text: str,
                 replaces: object,
                 notes_chars: int) -> tuple[list[str] | None, str]:
    """The notes with `text` added, in place of note `replaces` when it names one, and the sentence the agent reads
    back. None instead of the notes when the note is refused: an unknown number, or notes past `notes_chars`.
    `replaces` is the model's own argument: a number written as text ("2") is that number."""

    number: int | None = None
    if replaces not in (None, ""):
        try:
            number = _parse_whole_number("replaces", replaces)
        except ReadArgumentError as error:
            return None, f"{error}: give the number of a note, such as 2."
    revised = list(notes)
    note = " ".join(text.split())
    if number is None:
        revised.append(note)
    elif not 1 <= number <= len(revised):
        return None, f"There is no note {number}; there are {len(revised)}."
    else:
        # In its place, so every later note keeps the number the agent knows it by
        revised[number - 1] = note
    if sum(len(existing) for existing in revised) > notes_chars:
        listed = "\n".join(f"{index}. {existing}" for index, existing in enumerate(notes, start=1))
        return None, (f"This note does not fit: all notes together may hold {notes_chars} characters. Give "
                      f"`replaces` the number of a note it replaces, or shorten it. Your notes:\n{listed}")

    return revised, f"Noted ({len(revised)} note(s)); it stays when the conversation is compacted."
