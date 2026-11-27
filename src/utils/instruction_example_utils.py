"""The lines of the invented sample answer in phase 3's instructions (`frames_processing.md`), and whether a note copies one. Pure but
for reading that file once.

Two layers ask: phase 3's frame validator refuses a record that copies a line, and the merge drops an entry that copied
one before the check existed, so an invented question about a person who was never in the meeting never reaches the
document as the meeting's own.
"""

from __future__ import annotations

import functools
import re

from services import instruction_store_service

__all__ = ["EXAMPLE_FILE", "read_example_lines_of", "list_example_lines", "is_example_line", "list_placeholders",
           "find_placeholder"]

EXAMPLE_FILE = "instructions/_shared/frames-processing.md"  # phase 3's shared instructions, which hold the sample answer

# A placeholder of the instructions: `<what the speaker says ...>`, a phrase in angle brackets standing for what the
# worker writes. A shorter one (`<n>`, `<number>`) is a name in an ordinary sentence and is never compared
_PLACEHOLDER = re.compile(r"<([a-z][^<>\n]{9,})>")
# A line of the sample answer this short says nothing particular ("(none)", a tag list's word) and is not compared
EXAMPLE_LINE_MIN_CHARS = 25


def _normalise_line(text: str) -> str:
    return " ".join(text.strip().lstrip("-").strip().split()).casefold()


@functools.lru_cache(maxsize=4)
def read_example_lines_of(text: str) -> frozenset[str]:
    """The lines of the sample answer in the instruction text `text`, normalised; a text with no sample is refused."""

    if "~~~markdown" not in text:
        raise ValueError(f"{EXAMPLE_FILE} has no ~~~markdown block: the copied-example check reads its sample answer there")
    block = text.split("~~~markdown", 1)[1].split("~~~", 1)[0]

    normalised_lines = [_normalise_line(line) for line in block.splitlines()]

    return frozenset(line for line in normalised_lines if len(line) >= EXAMPLE_LINE_MIN_CHARS)


def list_example_lines() -> frozenset[str]:
    """The lines of frames_processing.md's sample answer as it is in force, normalised: case, spacing and a leading
    dash removed."""

    return read_example_lines_of(instruction_store_service.read_active(EXAMPLE_FILE))


def is_example_line(text: str) -> bool:
    """Is `text` a line of the sample answer, word for word?"""

    return _normalise_line(text) in list_example_lines()


@functools.lru_cache(maxsize=4)
def _read_placeholders_of(text: str) -> frozenset[str]:
    return frozenset(match.group(1).casefold() for match in _PLACEHOLDER.finditer(text))


def list_placeholders() -> frozenset[str]:
    """Every placeholder phrase of frames_processing.md as it is in force, folded, without its angle brackets."""

    return _read_placeholders_of(instruction_store_service.read_active(EXAMPLE_FILE))


def find_placeholder(text: str) -> str:
    """The first placeholder of the instructions `text` copies, in its angle brackets, or "" when it copies none."""

    for match in _PLACEHOLDER.finditer(text):
        if match.group(1).casefold() in list_placeholders():
            return match.group(0)

    return ""
