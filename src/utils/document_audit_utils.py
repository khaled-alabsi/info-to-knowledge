"""What the program finds wrong in a finished document and repairs with no model, before phase 5's critique: figures it
removes, code spans left open, sentences said twice, and diagrams Mermaid cannot draw. Pure functions over markdown.

A review that only asked what a reader cannot know passed a document with two figures of one view, a figure almost all
masks, a code span that ran on for a paragraph and a diagram Mermaid could not draw (2026-10-01).
"""

from __future__ import annotations

import re
from typing import Any

from utils import document_heading_utils
from models.text_repair_result import TextRepairResult
from utils import document_text_utils
from utils import mermaid_text_utils

__all__ = ["list_figures", "remove_figures", "find_text_defects", "repair_text"]

# A published figure's file, by its number: `resources/figure-03.png`
_FIGURE_FILE_PATTERN = r"resources/figure-(\d+)\.png"
_FIGURE_LINE = re.compile(rf"^!\[[^\]]*\]\(({_FIGURE_FILE_PATTERN})\)\s*$")
_CAPTION_LINE = re.compile(rf"^{re.escape(document_text_utils.FIGURE_CAPTION_START)}(\d+)\.")
_FIGURE_FILE = re.compile(_FIGURE_FILE_PATTERN)
_HEADING = re.compile(r"^#{1,6} (.+?)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# A sentence this short said twice is a phrase, not a repeat
_MIN_REPEATED_SENTENCE_WORDS = 6
# The sections outside the check for a sentence said twice (CONTEXT.md): what they say is said again in the chapters
_BREAK = re.compile(r"<br\s*/?>")
_NOT_PROSE = re.compile(rf"^\s*(?:!|{re.escape(document_text_utils.FIGURE_CAPTION_START.rstrip())}|#|-|>|\||\d+\. )")


def _render_figure_file(number: int) -> str:
    return f"resources/figure-{number:02d}.png"


def _list_blocks(body: str) -> list[tuple[str, str, bool]]:
    """Each paragraph or fenced block of `body`, with the heading it stands under and whether it is fenced: the parts of
    `_list_blocks_with_gaps` without the headings and blank lines, so the finder and the repair cut one document alike."""

    return [(heading, block, is_fenced) for heading, block, is_fenced in _list_blocks_with_gaps(body)
            if is_fenced is not None]


def _is_open_code_span(block: str) -> bool:
    """Whether a paragraph leaves a code span open, which turns the rest of it into code; a figure line and a caption
    hold backticks of their own."""

    return bool(block.count("`") % 2) and not block.lstrip().startswith(("!", document_text_utils.FIGURE_CAPTION_START))


def _is_mermaid_block(block: str) -> bool:
    return block.lstrip("`").startswith("mermaid")


def _read_sentence_key(sentence: str) -> str:
    """What two copies of one sentence share: its words, lower case, one space apart; "" for a sentence too short."""

    key = " ".join(sentence.lower().split())

    return key if len(key.split()) >= _MIN_REPEATED_SENTENCE_WORDS else ""


def _read_prose_text(block: str) -> str:
    """A paragraph as its sentences are read and repaired: without its backticks when it leaves a code span open."""

    return block.replace("`", "") if _is_open_code_span(block) else block


def _list_prose_lines(heading: str,
                      block: str) -> list[list[str] | None]:
    """Each line of a paragraph as the finder and the repair read its sentences: its sentences, or None for a line no
    sentence pass may touch (a quote, a table row, a list item, a figure, a caption) and for every line of a section
    outside the repeat check. Read with an open code span's backticks removed, as the repair writes it."""

    lines = _read_prose_text(block).split("\n")
    if heading in document_heading_utils.list_repeat_exempt_headings():
        return [None for _ in lines]

    return [None if _is_not_prose(line) or not line.strip() else _SENTENCE_END.split(line.strip()) for line in lines]


def _is_not_prose(block: str) -> bool:
    """Is `block` a figure, a caption, a heading, a list, a quote or a table, which no sentence pass may join? A numbered
    list read as a paragraph was joined into one line (2026-10-01)."""

    return bool(_NOT_PROSE.match(block))


def _repair_diagram_line(line: str) -> list[str]:
    """`line` with an open label quote closed before the bracket that ends the label, and split where `<br/>` outside a
    quoted label joins two statements."""

    if line.count('"') % 2:
        line = re.sub(r'(\[\(?\{?)"([^"\]\)\}]*)([\]\)\}])', r'\1"\2"\3', line, count=1)
    indent = line[:len(line) - len(line.lstrip())]
    parts, current, is_quoted, index = [], "", False, 0
    while index < len(line):
        if line[index] == '"':
            is_quoted = not is_quoted
        match = _BREAK.match(line, index) if not is_quoted else None
        if match:
            parts.append(current)
            current, index = "", match.end()
            continue
        current += line[index]
        index += 1
    parts.append(current)

    return [parts[0].rstrip()] + [indent + part.strip() for part in parts[1:] if part.strip()]


def _list_blocks_with_gaps(body: str) -> list[tuple[str, str, bool | None]]:
    """`body` cut into its parts in order, so they join back into it: each paragraph or fenced block with the heading it
    stands under and whether it is fenced, and each heading or blank line as itself with None."""

    parts: list[tuple[str, str, bool | None]] = []
    heading, current, is_fenced = "", [], False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            if not is_fenced and current:
                parts.append((heading, "\n".join(current), False))
                current = []
            current.append(line)
            if is_fenced:
                parts.append((heading, "\n".join(current), True))
                current = []
            is_fenced = not is_fenced
            continue
        if is_fenced:
            current.append(line)
            continue
        title = _HEADING.match(line)
        if title or not line.strip():
            if current:
                parts.append((heading, "\n".join(current), False))
                current = []
            heading = title.group(1) if title else heading
            parts.append((heading, line, None))
            continue
        current.append(line)
    if current:
        parts.append((heading, "\n".join(current), is_fenced))

    return parts


def list_figures(body: str) -> list[tuple[int, str]]:
    """Each figure the document places, as its number and its file, in the order placed."""

    return [(int(match.group(2)), match.group(1)) for line in body.splitlines()
            for match in [_FIGURE_LINE.match(line.strip())] if match]


def remove_figures(body: str,
                   numbers: set[int]) -> str:
    """`body` without the figures `numbers` names: each one's image line, its caption line and the blank line after."""

    if not numbers:
        return body
    lines = body.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        figure = _FIGURE_LINE.match(lines[index].strip())
        if figure and int(figure.group(2)) in numbers:
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            caption = _CAPTION_LINE.match(lines[index].strip()) if index < len(lines) else None
            if caption and int(caption.group(1)) == int(figure.group(2)):
                index += 1
            while index < len(lines) and not lines[index].strip() and kept and not kept[-1].strip():
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    # The figures after a removed one are numbered on, in captions, in the text and in their image links: left as they
    # were, the text jumped from Figure 2 to Figure 4 and the critique spent a todo on it, and a caption that showed
    # another number's file read as a contradiction (2026-10-02). Publishing renames the files to match.
    remaining = [number for number, _file in list_figures("\n".join(kept))]
    renumbered = {old: new for new, old in enumerate(remaining, start=1)}
    text = document_text_utils.FIGURE_MENTION.sub(
        lambda match: document_text_utils.render_figure_mention(renumbered.get(int(match.group(1)), int(match.group(1)))),
                               "\n".join(kept))
    text = _FIGURE_FILE.sub(lambda match: _render_figure_file(renumbered.get(int(match.group(1)), int(match.group(1)))),
                            text)

    return text + ("\n" if body.endswith("\n") else "")


def find_text_defects(body: str) -> list[dict[str, Any]]:
    """The defects a reader sees in the text: a code span left open, which turns the rest of its paragraph into code; a
    diagram Mermaid cannot draw; a sentence said twice. Each with the section it is in, the text it is about, what is
    wrong, and whether a review edit can reach it (a diagram sits in a code block, which no edit reaches)."""

    # STEP 1: each block's own defects
    defects = []
    blocks = _list_blocks(body)
    for heading, block, is_fenced in blocks:
        if is_fenced:
            problems = mermaid_text_utils.find_syntax_problems(block) if _is_mermaid_block(block) else []
            defects += [{"section": heading, "quote": problem, "problem": "Mermaid cannot draw this diagram",
                         "isEditable": False} for problem in problems]
        elif _is_open_code_span(block):
            defects.append({"section": heading, "quote": block.strip(), "isEditable": True,
                            "problem": "a code span is left open: the rest of the paragraph prints as code"})

    # STEP 2: the sentences said twice, read as the repair reads them, so the finder names what the repair will do
    seen: set[str] = set()
    for heading, block, is_fenced in blocks:
        if is_fenced:
            continue
        for sentences in _list_prose_lines(heading, block):
            for sentence in sentences or []:
                key = _read_sentence_key(sentence)
                if not key:
                    continue
                if key in seen:
                    defects.append({"section": heading, "quote": sentence.strip(), "isEditable": True,
                                    "problem": "the sentence stands twice in the document"})
                seen.add(key)

    return defects


# How much of a removed sentence the review report quotes
_QUOTED_CHARS = 80


def _shorten_quote(sentence: str) -> str:
    """The sentence whole, or its start up to the last whole word within _QUOTED_CHARS and " …": the report quoted a
    removed sentence cut mid-word, with nothing to say it went on (2026-10-10)."""

    flat = " ".join(sentence.split())
    if len(flat) <= _QUOTED_CHARS:
        return flat

    return flat[:_QUOTED_CHARS].rsplit(" ", 1)[0] + " …"


def repair_text(body: str) -> TextRepairResult:
    """`body` with the text defects repaired, and one line per repair: a paragraph whose code span was left open loses
    its backticks, a later copy of a sentence goes, and a diagram line is repaired as `_repair_diagram_line` says."""

    lines, repairs, seen = [], [], set()
    for heading, block, is_fenced in _list_blocks_with_gaps(body):
        if is_fenced is None:
            lines.append(block)
            continue
        if is_fenced:
            if _is_mermaid_block(block) and mermaid_text_utils.find_syntax_problems(block):
                fixed = [repaired for line in block.splitlines() for repaired in _repair_diagram_line(line)]
                block = "\n".join(fixed)
                repairs.append(f'"{heading}": a diagram Mermaid could not draw was repaired')
            lines.append(block)
            continue
        prose_lines = _list_prose_lines(heading, block)
        if _is_open_code_span(block):
            block = _read_prose_text(block)
            repairs.append(f'"{heading}": a code span left open; the paragraph\'s backticks were removed')
        # Line by line: a quote line or a table row right under a sentence stays on its own line
        kept_lines = []
        for line, sentences in zip(block.split("\n"), prose_lines):
            if sentences is None:
                kept_lines.append(line)
                continue
            kept = []
            for sentence in sentences:
                key = _read_sentence_key(sentence)
                if key and key in seen:
                    repairs.append(f'"{heading}": a sentence said earlier was removed: "{_shorten_quote(sentence)}"')
                    continue
                if key:
                    seen.add(key)
                kept.append(sentence)
            if kept:
                kept_lines.append(" ".join(kept))
        lines.append("\n".join(kept_lines))

    return TextRepairResult(text="\n".join(lines) + ("\n" if body.endswith("\n") else ""), repairs=repairs)


