"""A frame's outline read again against the frame by Apple's text recognizer: the labels it quotes that no reader finds
there (DEFECTS 278), and the lines it quotes with numbers other than the frame shows (DEFECTS 284).

A batch's outline listed parts its frame does not show, taken from the speech or a neighbouring frame (a connector's
name on a schematic step, a chip named while the speaker scrolled), and the second pass kept them when the critique
named them. No reader settles it alone: on froscon talk 3211's schematics Tesseract missed 11 of 15 real labels, and
Apple's text recognizer, read at the frame's size and twice that, missed the invented "J5A1", "J5B" and "J5C" of frame
16 and four real pin names of frame 21. So the critique and the second pass are told these labels and look for each on
the image; the program drops nothing.

A terminal's table of 23 rows on a 524-pixel frame (froscon talk 3211, frame 62) was outlined with the minimum and
maximum of rows 5 to 8 taken from other rows. The recognizer, read at about 1,500 pixels wide, read every number of
rows 1 to 11 right while it garbled their letters ("RWG" for "AVG"), so lines are matched by their numbers alone, and
the worker is told each line whose numbers differ.
"""

from __future__ import annotations

import difflib
import functools
import math
import re
from pathlib import Path

import cv2

from services import apple_text_service

# A label: a name of three characters or more holding a digit or two capitals ("J5A1", "DIGI-IO", "AD620"), the
# kind of part an outline takes from the speech; a plain word is left to the outline's text check
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.]{2,}")
_QUOTE = re.compile(r'"([^"]+)"')
_LEAST_CAPITALS = 2
# A label counts as read when a read word is this close to it: the recognizer misreads a letter of a small label
_READ_MATCH_RATIO = 0.75
# The second reading's scale: small labels the first missed were read at twice the size
_ENLARGED_SCALE = 2
# Past this many, the reader missed the frame's labels wholesale and the list would only be noise
_MOST_LISTED = 8


def _is_label(token: str) -> bool:
    # A number alone is the number check's (list_misread_numbers)
    if not any(letter.isalpha() for letter in token):
        return False

    return any(letter.isdigit() for letter in token) or sum(letter.isupper() for letter in token) >= _LEAST_CAPITALS


def list_quoted_labels(screen: str) -> list[str]:
    """The labels the outline quotes, in the order it quotes them, each once."""

    labels: list[str] = []
    for quote in _QUOTE.findall(screen):
        for token in _LABEL.findall(quote):
            token = token.rstrip(".-")
            if _is_label(token) and token not in labels:
                labels.append(token)

    return labels


def _read_tokens(image) -> set[str]:
    read: set[str] = set()
    for scale in (1, _ENLARGED_SCALE):
        scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        read |= {token.casefold() for word in apple_text_service.find_words_in_image(scaled)
                 for token in _LABEL.findall(word["text"])}

    return read


def list_unread_labels(image_path: Path,
                       screen: str) -> list[str]:
    """The labels the outline quotes that the reader finds nowhere on the frame. Empty when the reader cannot run, the
    frame is not on disk, or more than _MOST_LISTED are unread."""

    labels = list_quoted_labels(screen)
    if not labels or not apple_text_service.is_available():
        return []
    image = cv2.imread(str(image_path))
    if image is None:
        return []
    read = _read_tokens(image)
    if not read:
        return []
    unread = [label for label in labels
              if not _is_read(label, read) and not _is_in_read_line(label, screen, read)]

    return unread if len(unread) <= _MOST_LISTED else []


def _is_read(word: str,
             read: set[str]) -> bool:
    return bool(difflib.get_close_matches(word.casefold(), read, n=1, cutoff=_READ_MATCH_RATIO))


# A label quoted inside a line whose other words the reader reads was missed by the reader, never taken from elsewhere:
# froscon talk 3211's frame 29, 524 pixels wide, read "RFM" and "FM69:" for "RFM69CW chip revision 2 OK", and the
# critique was told the module was not on the frame
_READ_LINE_SHARE = 0.5


def _is_in_read_line(label: str,
                     screen: str,
                     read: set[str]) -> bool:
    for quote in _QUOTE.findall(screen):
        if label not in _LABEL.findall(quote) and label + "." not in quote:
            continue
        others = [word for word in _LABEL.findall(quote) if word != label]
        if others and sum(_is_read(word, read) for word in others) >= _READ_LINE_SHARE * len(others):
            return True

    return False


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# The width a frame is read at for its numbers: on a 524-pixel frame twice the size read 3 of 23 rows, three times 11
_NUMBER_READ_WIDTH = 1500
# A line is compared only with at least this many numbers: a line of one number has no row to match it by
_LEAST_NUMBERS = 3
# Past this many, the lines are no table the reader read whole
_MOST_MISREAD = 12


def _read_lines(image) -> list[str]:
    scale = max(1, math.ceil(_NUMBER_READ_WIDTH / image.shape[1]))
    scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    by_line: dict[int, list[dict]] = {}
    for word in apple_text_service.find_words_in_image(scaled):
        by_line.setdefault(word["line"], []).append(word)

    return [" ".join(word["text"] for word in sorted(words, key=lambda word: word["box"][0])) for words in by_line.values()]


# The missed lines and the differing words read one frame for one message: they share one reading of it
@functools.lru_cache(maxsize=64)
def _read_lines_at(image_path: str) -> tuple[str, ...]:
    image = cv2.imread(image_path)

    return () if image is None else tuple(_read_lines(image))


def list_read_lines(image_path: Path) -> list[str]:
    """The lines of text Apple's recognizer reads on the frame, each its words in order; empty when the recognizer
    cannot run or the frame is not on disk."""

    if not apple_text_service.is_available() or not image_path.is_file():
        return []
    image = cv2.imread(str(image_path))

    return [] if image is None else _read_lines(image)


# How alike a quoted and a read line must be, letters garbled and all ("RWG" for "AVG"): lines of two commands sharing
# a first number read 0.3 to 0.5 alike, rows of one table 0.8
_LINE_LIKENESS = 0.6


def _find_differing_line(quote: str,
                         numbers: list[str],
                         read: list[tuple[str, list[str]]]) -> str | None:
    """The one read line of the same row (its first number the same, as many numbers, and alike) whose numbers differ,
    each differing number as long as the quote's: a number read a digit short is the reader's misreading."""

    rows = [(line, found) for line, found in read if len(found) == len(numbers) and found[0] == numbers[0]
            and difflib.SequenceMatcher(None, quote.casefold(), line.casefold()).ratio() >= _LINE_LIKENESS]
    if len(rows) != 1 or rows[0][1] == numbers:
        return None
    if any(len(left) != len(right) for left, right in zip(numbers, rows[0][1]) if left != right):
        return None

    return rows[0][0]


def list_misread_numbers(image_path: Path,
                         screen: str) -> list[tuple[str, str]]:
    """Each line the outline quotes with numbers other than the reader reads on the same row of the frame, as (the
    quote, the read line). Empty when the reader cannot run, the frame is not on disk, or more than _MOST_MISREAD
    differ."""

    quotes = [quote for quote in _QUOTE.findall(screen) if len(_NUMBER.findall(quote)) >= _LEAST_NUMBERS]
    if not quotes or not apple_text_service.is_available():
        return []
    image = cv2.imread(str(image_path))
    if image is None:
        return []
    read = [(line, _NUMBER.findall(line)) for line in _read_lines(image)]
    if not read:
        return []
    differing = [(quote, line) for quote in quotes
                 for line in [_find_differing_line(quote, _NUMBER.findall(quote), read)] if line is not None]

    return differing if len(differing) <= _MOST_MISREAD else []


# The lines the first reader missed: a line of the recognizer whose words of two letters or more, over half of them,
# the first reader's lines lack. A file manager's panes of short names on a 545-pixel frame (froscon talk 3147, frame
# 30: "CD", "COLORS", "SSAVERS") gave Tesseract no line at any scale, and the outline quoted none of them (DEFECTS 311)
_LEAST_LETTERS = 2
_MOST_KNOWN_SHARE = 0.5
# Past this many, a list of misread lines would bury the frame's message
_MOST_MISSED_LINES = 40
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.]+")


def is_read(image_path: Path) -> bool:
    """Whether Apple's text recognizer read at least one line on the frame. A frame of text it reads nothing on was not
    read: the recognizer failed (under a full swap it answers each call with no reading), and "no line missed" said
    nothing (38c3-fault-injection's frame 14 went as text, its reading short of a quoted word, DEFECTS 643)."""

    return apple_text_service.is_available() and bool(_read_lines_at(str(image_path)))


def list_missed_lines(image_path: Path,
                      listed: list[str]) -> list[str]:
    """The lines Apple's text recognizer reads on the frame, at about 1,500 pixels wide, that the first reader's
    `listed` lines leave out: over half their words of two letters or more are in none of them. Empty when the
    recognizer cannot run or the frame is not on disk; at most `_MOST_MISSED_LINES`."""

    if not apple_text_service.is_available():
        return []
    known = {word.casefold() for line in listed for word in _WORD.findall(line)}
    missed = []
    for line in _read_lines_at(str(image_path)):
        words = [word.casefold() for word in _WORD.findall(line)
                 if len(word) >= _LEAST_LETTERS and any(letter.isalpha() for letter in word)]
        # a line of no word with a letter (an editor's line number, a slide's page number) is no text to quote
        if words and sum(word in known for word in words) <= _MOST_KNOWN_SHARE * len(words):
            missed.append(line)

    return missed[:_MOST_MISSED_LINES]


# The reader circle (AGENTS.md, "the text reader's misread written into the notes as the screen's text"): the frame's
# message listed one reader's line as what the screen shows, and its misreads reached the notes ("Read 42 lines" for 12,
# DEFECTS 579; a bar for "I", 336; a table's border, 575). Each listed line is matched to the recognizer's most alike
# line, and the words the two read otherwise are named. Over 38c3-openpv's 124 listed lines, 25 read differently: 13 by
# marks alone (a bullet, a quote mark, an arrow, a capital), 5 by where a line was cut, 7 by words, Apple right on all 7
# ("chne Gewahr" for "ohne Gewähr", "PY" for "PV", "Moller" for "Möller"), measured once, offline
_WORD_LIKENESS = 0.5
_MOST_DIFFERING = 12
_MARKS = "\"'“”‘’`.,;:!?()[]{}|•·-–—→←*"


def _strip_marks(text: str) -> str:
    return " ".join(word.strip(_MARKS) for word in text.split() if word.strip(_MARKS)).casefold()


def _is_misread_pair(listed: str,
                     read: str) -> bool:
    """Whether two spans the readers put at one place of a line are one text read two ways: other than by marks, case or
    a space, alike, and not a part of the line one reader left out (more words on one side, the other's at its end)."""

    first, second = _strip_marks(listed), _strip_marks(read)
    if not first or not second or first.replace(" ", "") == second.replace(" ", ""):
        return False
    if len(first.split()) != len(second.split()) and (first.endswith(second) or second.endswith(first)
                                                      or first.startswith(second) or second.startswith(first)):
        return False

    return difflib.SequenceMatcher(None, first, second).ratio() >= _WORD_LIKENESS


def list_differing_words(image_path: Path,
                         listed: list[str]) -> list[tuple[str, str]]:
    """Each span of the first reader's `listed` lines that Apple's recognizer reads otherwise on the frame, as (the
    listed span, the recognizer's), at most `_MOST_DIFFERING`. Empty when the recognizer cannot run or the frame is not
    on disk."""

    if not listed or not apple_text_service.is_available():
        return []
    read = _read_lines_at(str(image_path))
    pairs = []
    for line in listed:
        best = max(read, key=lambda other: difflib.SequenceMatcher(None, line.casefold(), other.casefold()).ratio(),
                   default="")
        if not best or difflib.SequenceMatcher(None, line.casefold(), best.casefold()).ratio() < _LINE_LIKENESS:
            continue
        first, second = line.split(), best.split()
        for kind, a0, a1, b0, b1 in difflib.SequenceMatcher(None, first, second).get_opcodes():
            pair = (" ".join(first[a0:a1]), " ".join(second[b0:b1]))
            if kind == "replace" and _is_misread_pair(*pair) and pair not in pairs:
                pairs.append(pair)

    return pairs[:_MOST_DIFFERING]


# DEFECTS 333: on a 545-pixel terminal no reader reads a table's rows or a panel's fields well enough to check an
# outline's text against, and outlines left a third of the rows and half the fields out; the recognizer, read about
# 1,500 pixels wide, still finds every line in its place, its letters garbled. So the lines are counted by column: lines
# whose left edges stand within _COLUMN_SPREAD of the width, one row per height, a column broken where the gap between
# two rows passes _COLUMN_BREAK times its usual spacing, kept from _LEAST_COLUMN_LINES rows
_COLUMN_SPREAD = 0.015
_COLUMN_BREAK = 1.6
_LEAST_COLUMN_LINES = 5
_MOST_COLUMNS = 6


def _list_line_starts(image) -> list[tuple[float, float, float]]:
    """Each line the recognizer reads: its left edge, its middle and its height, as shares of the frame."""

    scale = max(1, math.ceil(_NUMBER_READ_WIDTH / image.shape[1]))
    scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    height, width = scaled.shape[:2]
    by_line: dict[int, list[dict]] = {}
    for word in apple_text_service.find_words_in_image(scaled):
        by_line.setdefault(word["line"], []).append(word)
    starts = []
    for words in by_line.values():
        left = min(word["box"][0] for word in words)
        top, bottom = min(word["box"][1] for word in words), max(word["box"][3] for word in words)
        starts.append((left / width, (top + bottom) / 2 / height, (bottom - top) / height))

    return starts


def _split_column(rows: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """`rows` (middle, height), sorted by middle, as runs with no gap past `_COLUMN_BREAK` times their usual spacing."""

    merged: list[tuple[float, float]] = []
    for middle, height in rows:
        if merged and middle - merged[-1][0] < merged[-1][1] / 2:
            continue
        merged.append((middle, height))
    gaps = sorted(second[0] - first[0] for first, second in zip(merged, merged[1:]))
    if not gaps:
        return [merged]
    usual = gaps[len(gaps) // 2]
    runs = [[merged[0]]]
    for first, second in zip(merged, merged[1:]):
        if second[0] - first[0] > _COLUMN_BREAK * usual:
            runs.append([])
        runs[-1].append(second)

    return runs


def list_text_columns(image_path: Path) -> list[tuple[float, float, float, int]]:
    """The columns of text lines the recognizer finds on the frame, the most lines first: each column's left edge, its
    first and last line's middle as shares of the frame, and its count of lines. Empty when the recognizer cannot run
    or the frame is not on disk."""

    if not apple_text_service.is_available():
        return []
    image = cv2.imread(str(image_path))
    if image is None:
        return []
    groups: list[list[tuple[float, float, float]]] = []
    for start in sorted(_list_line_starts(image)):
        if groups and start[0] - groups[-1][0][0] <= _COLUMN_SPREAD:
            groups[-1].append(start)
        else:
            groups.append([start])
    columns = []
    for group in groups:
        for run in _split_column(sorted((middle, height) for _, middle, height in group)):
            if len(run) >= _LEAST_COLUMN_LINES:
                columns.append((group[0][0], run[0][0], run[-1][0], len(run)))

    return sorted(columns, key=lambda column: -column[3])[:_MOST_COLUMNS]
