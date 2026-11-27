"""The review's changes to a finished document, as text. REQUIREMENTS.md R16. Pure functions over a string.

The worker names where an edit goes by a section's heading and a sentence copied from that section; the program finds
the sentence word for word (only the spacing between words may differ, so a line break inside a quote is no
difference) and puts the new sentences in its place or right after it, in the same paragraph. A sentence inside a
fenced block (a diagram) is never found, and neither is a heading, so an edit cannot change either. The open points
the review adds go into the closing section's list, and one line at the end says what the review found.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Any

from utils import document_heading_utils
from models import output_contract
from schemas import review_actions
from utils import document_text_utils

__all__ = ["BLOCK_MARK", "list_document_blocks", "render_numbered_document", "read_block", "remove_block_marks",
           "normalise_heading", "is_named", "split_front_matter", "list_headings", "read_sections", "find_quote", "find_quote_heading",
           "render_new_text", "apply_edit", "list_figure_marks", "add_open_questions", "add_review_line"]

_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
_FENCE = "```"
# The front matter phase 5 opens the published document with (R13), up to the blank line after its closing `---`.
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n\n?", re.DOTALL)
# What a figure is made of: its frame's line, its caption's line, the description a reader who cannot see it reads in
# its place, and every sentence that points to it by number.
_FIGURE_LINE = re.compile(rf"^!\[[^\]]*\]\([^)]*\)\s*$|^{re.escape(document_text_utils.FIGURE_CAPTION_START)}\d+\."
                          rf"|^{document_text_utils.FIGURE_DESCRIPTION_START}")
_FIGURE_DESCRIPTION = re.compile(rf"^{document_text_utils.FIGURE_DESCRIPTION_START}")
# An ellipsis, as three dots or as one character ("AcmeSync sends the orders from …"); at a quote's end, its start,
# or inside it ("A … B", whose parts stand in that order in one paragraph of the section)
_ELLIPSIS = r"(?:\.\.\.|…)"
_ELLIPSIS_END = re.compile(rf"\s*{_ELLIPSIS}\s*$")
_ELLIPSIS_START = re.compile(rf"^\s*{_ELLIPSIS}\s*")
_ELLIPSIS_INSIDE = re.compile(rf"\s*{_ELLIPSIS}\s*")
_SENTENCE_STOP = re.compile(r"[.!?](?=\s|$)")
# The marks a reader does not read as words: emphasis and code marks, and straight or curly quotes. A worker copying a
# sentence leaves them out, so a quote is compared with the document's reading text: measured on the second real run,
# where an emphasised word and a quoted name came back without their marks, word for word otherwise.
_UNREAD_MARKS = frozenset("*_`'\"\u2018\u2019\u201c\u201d")
# A markdown link's target, "(#qa-session)" after "[Q&A session]": program text a reader never reads. A reviewer quoting the
# executive summary's last sentence, which the program ends with a link, copied the link's text and not its target, and
# its first critique was rejected (REVIEW_QUOTE_NOT_FOUND, release test 3, DEFECTS 465)
_LINK_TARGET = re.compile(r"(?<=\])\([^()\s]*\)")
# A section's number before its title: "8 ", "2.3 ", "4) ". A reviewer named "Revoking a key" for the heading
# "8 Revoking a key" of a manual, and its edit was refused as aimed at a section the document lacks (pdf-05).
_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+")


# The line that numbers a block of the document the review reads, and how it is read back (DEFECTS 610)
BLOCK_MARK = "{{block {number}}}"
_BLOCK_MARK_LINE = re.compile(r"^\{block \d+\}$")


def list_document_blocks(markdown: str) -> list[tuple[str, str]]:
    """Each block of the document's own text, in order, numbered from 1 by its place in the list: (the heading it
    stands under, its text). A block is a run of lines up to a blank line, outside fenced blocks and headings, which
    are never quoted or edited. A reviewer named its sentence by copying it from an 80,000-character document, and the
    finder was widened for each way a copy differs (AGENTS.md's circle); a number names it whole."""

    blocks: list[tuple[str, str]] = []
    heading, run, is_fenced = "", [], False
    for _offset, line in _list_lines(markdown):
        is_fence_line = line.strip().startswith(_FENCE)
        is_fenced = is_fenced != is_fence_line
        match = None if is_fenced or is_fence_line else _HEADING.match(line)
        if is_fenced or is_fence_line or match or not line.strip():
            if run:
                blocks.append((heading, "\n".join(run)))
                run = []
            heading = match.group(2).strip() if match else heading
            continue
        run.append(line)
    if run:
        blocks.append((heading, "\n".join(run)))

    return blocks


def render_numbered_document(markdown: str) -> str:
    """`markdown` with a `{block N}` line before each block of `list_document_blocks`, the numbers a review point and
    a resolution name."""

    numbered = iter(range(1, len(list_document_blocks(markdown)) + 1))
    lines, run_started, is_fenced = [], False, False
    for _offset, line in _list_lines(markdown):
        is_fence_line = line.strip().startswith(_FENCE)
        is_fenced = is_fenced != is_fence_line
        if is_fenced or is_fence_line or _HEADING.match(line) or not line.strip():
            run_started = False
        elif not run_started:
            lines.append(BLOCK_MARK.format(number=next(numbered)))
            run_started = True
        lines.append(line)

    return "\n".join(lines)


def read_block(markdown: str,
               number: object) -> tuple[str, str] | None:
    """Block `number` of `list_document_blocks`, (its heading, its text), or None when the number names none."""

    blocks = list_document_blocks(markdown)

    return blocks[number - 1] if isinstance(number, int) and 1 <= number <= len(blocks) else None


def remove_block_marks(text: str) -> str:
    """`text` without the `{block N}` lines a worker copied along with a sentence."""

    return "\n".join(line for line in text.split("\n") if not _BLOCK_MARK_LINE.match(line.strip()))


def _list_lines(markdown: str) -> list[tuple[int, str]]:
    """Every line with the offset it starts at."""

    lines, offset = [], 0
    for line in markdown.split("\n"):
        lines.append((offset, line))
        offset += len(line) + 1

    return lines


def _list_sections(markdown: str) -> list[tuple[str, str, int, int, int]]:
    """Every heading outside a fenced block, as (its text, its line, where its line starts, where its body starts,
    where its body ends), in document order. A body runs to the next heading of the same or a higher level, so a
    section holds its subsections, as a reader reads it: found on the first real run, where a sentence under
    "### Decisions" was quoted, rightly, as being in the "## Decisions, action items and open points" section."""

    headings: list[tuple[str, str, int, int, int]] = []
    is_fenced = False
    for offset, line in _list_lines(markdown):
        if line.strip().startswith(_FENCE):
            is_fenced = not is_fenced
            continue
        match = None if is_fenced else _HEADING.match(line)
        if match:
            headings.append((match.group(2).strip(), line, offset, offset + len(line) + 1, len(match.group(1))))
    sections = []
    for position, (name, line, start, body, level) in enumerate(headings):
        end = next((later[2] for later in headings[position + 1:] if later[4] <= level), len(markdown))
        sections.append((name, line, start, body, end))

    return sections


def _list_fenced_spans(markdown: str) -> list[tuple[int, int]]:
    spans, start = [], None
    for offset, line in _list_lines(markdown):
        if line.strip().startswith(_FENCE):
            if start is None:
                start = offset
            else:
                spans.append((start, offset + len(line)))
                start = None

    return spans


def _read_without_marks(markdown: str,
                        start: int,
                        end: int) -> tuple[str, list[int]]:
    """The text between `start` and `end` as a reader reads it, without the unread marks and the targets of links, and
    the offset in `markdown` of each character it keeps."""

    targets = {position for match in _LINK_TARGET.finditer(markdown, start, end) for position in range(match.start(), match.end())}
    offsets = [position for position in range(start, end) if markdown[position] not in _UNREAD_MARKS and position not in targets]

    return "".join(markdown[position] for position in offsets), offsets


def _list_heading_spans(markdown: str) -> list[tuple[int, int]]:
    """The (start, end) offsets of every heading line of `markdown`, read as `_list_sections` reads the headings."""

    return [(start, body - 1) for _name, _line, start, body, _end in _list_sections(markdown)]


def _find_outside_fences(markdown: str,
                         quote: str,
                         start: int,
                         end: int) -> tuple[int, int] | None:
    """Where `quote` first stands between `start` and `end`, word for word as a reader reads both, outside a fenced
    block and off every heading line. The span takes in a mark that touches its first or last word, so an edit leaves no
    stray mark behind."""

    words = "".join(character for character in _LINK_TARGET.sub("", quote) if character not in _UNREAD_MARKS).split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words))
    reading, offsets = _read_without_marks(markdown, start, end)
    # A heading is no text an edit may change, a subsection's heading inside the section included
    closed = _list_fenced_spans(markdown) + _list_heading_spans(markdown)
    for match in pattern.finditer(reading):
        span_start, span_end = offsets[match.start()], offsets[match.end() - 1] + 1
        while span_start > start and markdown[span_start - 1] in _UNREAD_MARKS:
            span_start -= 1
        while span_end < end and markdown[span_end] in _UNREAD_MARKS:
            span_end += 1
        target = _LINK_TARGET.match(markdown, span_end, end)
        if target is not None:
            span_end = target.end()
        if not any(closed_start < span_end and span_start < closed_end for closed_start, closed_end in closed):
            return span_start, span_end

    return None


def _find_elided(markdown: str,
                 wanted: str,
                 quote: str) -> tuple[int, int] | None:
    """Where a quote with words left out in its middle stands: from its first part to its last, each part found after
    the one before within one paragraph of the section. A critique quoted two sentences joined by "…" and blocked on
    it, though the passage was plain to find. None when the quote has no inner ellipsis or a part is missing."""

    parts = [part for part in _ELLIPSIS_INSIDE.split(quote.strip()) if part]
    if len(parts) < 2 or len(parts[0].split()) < 4:
        return None
    for name, _line, _start, body, end in _list_sections(markdown):
        if name != wanted:
            continue
        first = _find_outside_fences(markdown, parts[0], body, end)
        if first is None:
            continue
        cursor, last = first[1], first[1]
        # Each later part is found as the first is: as a reader reads it, marks and spacing aside, outside a fence
        for part in parts[1:]:
            span = _find_outside_fences(markdown, part, cursor, end)
            if span is None or "\n\n" in markdown[cursor:span[0]]:
                last = None
                break
            cursor = last = span[1]
        if last is not None:
            return first[0], last

    return None


def _find_in_sections(markdown: str,
                      wanted: str,
                      quote: str) -> tuple[int, int, int, int] | None:
    """Where `quote` first stands outside a fence in a section headed `wanted`: its (start, end) and the section's
    body start and end; None when no such section holds it."""

    for name, _line, _start, body, end in _list_sections(markdown):
        span = _find_outside_fences(markdown, quote, body, end) if is_named(name, wanted) else None
        if span is not None:
            return span[0], span[1], body, end

    return None


def normalise_heading(heading: str) -> str:
    return " ".join(heading.strip().lstrip("#").split())


def is_named(name: str,
             wanted: str) -> bool:
    """Whether the heading `name` is the section `wanted` names: the same text, or the text after the heading's own
    section number when `wanted` gives none ("Revoking a key" for "8 Revoking a key")."""

    return name == wanted or (not _SECTION_NUMBER.match(wanted) and _SECTION_NUMBER.sub("", name, count=1) == wanted)


def split_front_matter(markdown: str) -> tuple[str, str]:
    """The front matter the published document opens with (empty when it has none), and the rest."""

    match = _FRONT_MATTER.match(markdown)
    front = match.group(0) if match else ""

    return front, markdown[len(front):]


def list_headings(markdown: str) -> list[str]:
    return [heading for heading, _line, _start, _body, _end in _list_sections(markdown)]


def read_sections(markdown: str,
                  heading: str) -> list[str]:
    """Each section headed `heading`, its heading line included, in document order; none when no heading matches."""

    wanted = normalise_heading(heading)

    return [markdown[start:end].rstrip() for name, _line, start, _body, end in _list_sections(markdown)
            if is_named(name, wanted)]


def find_quote(markdown: str,
               heading: str,
               quote: str) -> tuple[int, int] | None:
    """Where `quote` stands, word for word, in the first section headed `heading` that holds it outside a fenced
    block, as (start, end) offsets; a quote ending in an ellipsis stands for the whole sentence it starts. None when
    no such section holds it."""

    # STEP 1: the quote as it is
    wanted = normalise_heading(heading)
    found = _find_in_sections(markdown, wanted, quote)
    if found is not None:
        return found[0], found[1]

    # STEP 2: a clause quoted from inside a sentence, its first letter made a capital ("Currently, it reports …" of "…;
    # currently, it reports …"): the words stand in the document, and a critique blocked on them (2026-10-05)
    if quote[:1].isalpha():
        found = _find_in_sections(markdown, wanted, quote[0].swapcase() + quote[1:])
        if found is not None:
            return found[0], found[1]

    # STEP 3: words left out in the middle, "A … B": the parts in that order in one paragraph
    prefix = _ELLIPSIS_END.sub("", quote)
    span = _find_elided(markdown, wanted, prefix)
    if span is not None:
        return span

    # STEP 4: a quote opened with an ellipsis is the end of the sentence it finishes: a critique quoted a long sentence
    # from its middle with "..." before it, and blocked on it (2026-10-04)
    core = _ELLIPSIS_START.sub("", prefix)
    if core != prefix and len(core.split()) >= 4:
        found = _find_in_sections(markdown, wanted, core)
        if found is not None:
            span_start, span_end, body, end = found
            stops = [stop.end() for stop in _SENTENCE_STOP.finditer(markdown, body, span_start)]
            begin = stops[-1] if stops else body
            while begin < span_start and markdown[begin].isspace():
                begin += 1
            stop = _SENTENCE_STOP.search(markdown, span_end, end) if prefix != quote else None

            return begin, stop.end() if stop else span_end

    # STEP 5: a quote cut short with an ellipsis is the one sentence of the section it starts: a worker copied the
    # start of a long sentence and "…", and the critique blocked on it (2026-10-01)
    if prefix == quote or len(prefix.split()) < 4:
        return _find_one_word_off(markdown, wanted, quote)
    found = _find_in_sections(markdown, wanted, prefix)
    if found is None:
        return None
    span_start, span_end, _body, end = found
    stop = _SENTENCE_STOP.search(markdown, span_end, end)

    return span_start, stop.end() if stop else span_end


# A quote this long or longer may differ from its sentence by one word: shorter, one word is too much of it
_ONE_WORD_OFF_LEAST_WORDS = 8


def _find_one_word_off(markdown: str,
                       wanted: str,
                       quote: str) -> tuple[int, int] | None:
    """The one sentence of the section `wanted` that `quote` copies with one word added, left out or changed: a
    critique wrote "converts it to a 5 V" for the sentence's "converts it to 5 V" and blocked (froscon 3211, DEFECTS
    296). None when no sentence, or more than one, is that near."""

    words = quote.split()
    if len(words) < _ONE_WORD_OFF_LEAST_WORDS:
        return None
    found: list[tuple[int, int]] = []
    for name, _line, _start, body, end in _list_sections(markdown):
        if not is_named(name, wanted):
            continue
        begin = body
        for stop in [*_SENTENCE_STOP.finditer(markdown, body, end), None]:
            finish = stop.end() if stop else end
            sentence = markdown[begin:finish]
            lead = len(sentence) - len(sentence.lstrip())
            if _count_word_edits(words, sentence.split()) == 1:
                found.append((begin + lead, finish))
            begin = finish

    return found[0] if len(found) == 1 else None


def _count_word_edits(words: list[str],
                      other: list[str]) -> int:
    """The fewest words added, left out or changed that make `words` into `other`, counted no further than two."""

    if abs(len(words) - len(other)) > 1:
        return 2
    opcodes = difflib.SequenceMatcher(None, words, other, autojunk=False).get_opcodes()

    return min(2, sum(max(end - start, other_end - other_start) for tag, start, end, other_start, other_end in opcodes
                      if tag != "equal"))


def find_quote_heading(markdown: str,
                       quote: str) -> str | None:
    """The heading `quote` stands under, word for word and outside a fenced block: the innermost section that holds
    it. None when the document does not hold it anywhere."""

    span = _find_outside_fences(markdown, quote, 0, len(markdown))
    if span is None:
        return None
    holding = [name for name, _line, _start, body, end in _list_sections(markdown) if body <= span[0] < end]

    return holding[-1] if holding else None


def render_new_text(sentences: list[dict[str, Any]]) -> str:
    """The new sentences as the document prints them: one line, spaces collapsed, the secrets of a web address
    hidden as everywhere else in the document."""

    texts = [" ".join(str(sentence.get("text") or "").split()) for sentence in sentences]

    return document_text_utils.hide_secret_query_values(" ".join(text for text in texts if text))


_WHAT_FIGURE_SHOWS = "> **What the figure shows:**"


def replace_figure(markdown: str,
                   figure_line: str,
                   block: str) -> str | None:
    """`markdown` with the figure whose line is `figure_line` taken out whole (its line, its caption and its "What the
    figure shows" box) and `block` in its place, or nothing when `block` is empty; None when the line is not there. A
    figure that pictured code or a table is replaced by that block (the owner, 2026-10-12)."""

    lines = markdown.split("\n")
    start = next((index for index, line in enumerate(lines) if line.strip() == figure_line.strip()), None)
    if start is None or not figure_line.strip():
        return None
    end = start + 1
    for is_part in (lambda line: line.startswith(document_text_utils.FIGURE_CAPTION_START),
                    lambda line: line.startswith(_WHAT_FIGURE_SHOWS)):
        probe = end
        while probe < len(lines) and not lines[probe].strip():
            probe += 1
        if probe < len(lines) and is_part(lines[probe].strip()):
            end = probe + 1
            # A box written over several lines goes on while its lines start with ">"
            while end < len(lines) and lines[end].startswith(">"):
                end += 1
    inserted = block.strip().split("\n") if block.strip() else []
    kept = lines[:start] + inserted + lines[end:]

    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept))


def apply_edit(markdown: str,
               edit: dict[str, Any]) -> str:
    """`markdown` with `edit` made: its new sentences in place of `edit["replaces"]`, or right after `edit["after"]`,
    in the section `edit["section"]`; a figure's removal or replacement by a markdown block (`figure_action`). Raises
    `ValueError` when the sentence or figure it names is not there."""

    action = str(edit.get("figure_action") or "")
    # A recut keeps the figure's line: its image file is swapped, the text stays
    if action in (review_actions.FIGURE_RECUT, review_actions.FIGURE_KEEP):
        return markdown
    if action in (review_actions.FIGURE_REMOVE, review_actions.FIGURE_REPLACE):
        edited = replace_figure(markdown, str(edit.get("replaces") or ""),
                                str(edit.get("markdown_block") or "") if action == review_actions.FIGURE_REPLACE else "")
        if edited is None:
            raise ValueError(f"the figure line {edit.get('replaces')!r} is not in the document")
        return edited

    # A `replaces` of spaces names no sentence: the edit goes after `after`
    replaces = str(edit.get("replaces") or "") if str(edit.get("replaces") or "").strip() else ""
    target = replaces or str(edit.get("after") or "")
    if not target.strip() and not edit.get("sentences"):
        return markdown
    span = find_quote(markdown, str(edit.get("section") or ""), target)
    if span is None:
        raise ValueError(f"the sentence {target!r} is not in the section {edit.get('section')!r}")
    new_text = render_new_text(list(edit.get("sentences") or []))
    start, end = span
    if not replaces:
        return markdown[:end] + " " + new_text + markdown[end:] if new_text else markdown
    if new_text:
        return markdown[:start] + new_text + markdown[end:]
    # A deleted sentence leaves no double space where it stood, and one space between two words it stood between
    is_between_words = markdown[end:end + 1] not in ("", "\n") and markdown[:start][-1:] not in ("", "\n")

    return markdown[:start].rstrip(" ") + (" " if is_between_words else "") + markdown[end:].lstrip(" ")


def list_figure_marks(markdown: str) -> Counter:
    """Everything an edit may not change about the figures and diagrams: each frame's line, each caption's line, that
    each figure keeps a description, each mention of a figure by its number, and each fenced block whole. A
    description's words are the writer's account of the figure, and a wrong one is corrected like any sentence: Lecture
    7's said "Read 42 lines" where the figure shows 12, and the fix was refused as touching the figure (DEFECTS 611)."""

    lines = [document_text_utils.FIGURE_DESCRIPTION_MARK if _FIGURE_DESCRIPTION.match(line) else line
             for line in markdown.split("\n") if _FIGURE_LINE.match(line)]
    fences = [markdown[start:end] for start, end in _list_fenced_spans(markdown)]

    return Counter([*lines, *(match.group(0) for match in document_text_utils.FIGURE_MENTION.finditer(markdown)), *fences])


# An open point a review question asks again: its words, function words left out and plurals folded, stand in the
# question, all of them for a point of one or two words, this share for a longer one. A point of one word is only a
# term written with a capital or in quotes ("What is Gematik?"), never a common word. The Closing Ceremony listed five
# points twice: "What is Gematik?" beside the review's "What are Gematik and GPG …?"
_ASKED_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_ASKED_FUNCTION_WORDS = frozenset({"what", "which", "who", "when", "where", "why", "how", "does", "did", "the", "and",
                                   "are", "for", "not", "was", "were", "has", "have", "that", "this", "with", "from",
                                   "there", "their", "about", "each", "specific", "exact", "exactly", "whether", "they",
                                   "used", "only", "stated", "named", "unclear", "detailed", "yet", "beyond", "its",
                                   "refers", "refer", "mentioned", "explained", "defined", "is", "an", "or", "of", "in"})
_ASKED_SHARE = 0.6
_ASKED_FEW_WORDS = 2


def _build_word_root(word: str) -> str:
    folded = word.casefold()
    if folded.endswith("ies") and len(folded) > 5:
        return folded[:-3] + "y"
    if folded.endswith(("ses", "xes", "zes", "ches", "shes")):
        return folded[:-2]
    if folded.endswith("s") and not folded.endswith("ss") and len(folded) > 4:
        return folded[:-1]

    return folded


def _list_asked_words(text: str) -> list[str]:
    return [word for word in _ASKED_WORD.findall(text) if word.casefold() not in _ASKED_FUNCTION_WORDS]


def is_point_asked_again(point: str,
                         question: str) -> bool:
    """Whether the open point `point` is what `question` asks: its words stand in the question (see above)."""

    words = _list_asked_words(point)
    asked = {_build_word_root(word) for word in _list_asked_words(question)}
    held = [word for word in words if _build_word_root(word) in asked]
    if not words:
        return False
    if len(words) == 1:
        return bool(held) and (words[0][0].isupper() or f'"{words[0]}' in point or f"'{words[0]}" in point)
    if len(words) <= _ASKED_FEW_WORDS:
        return len(held) == len(words)

    return len(held) >= _ASKED_SHARE * len(words)


def add_open_questions(markdown: str,
                       questions: list[str],
                       replacing: str = "") -> str:
    """`markdown` with each question an item of the closing section's open points, in the one list with the open
    points the writing left. It replaces the line saying nothing was left open, and the open point `replacing` (the
    one a review todo was about) when the list holds it; a document without that list gets one."""

    texts = [" ".join(document_text_utils.hide_secret_query_values(question).split()) for question in questions]
    items = [f"- {text}" for text in texts if text]
    lines = markdown.split("\n")
    if not items:
        return markdown
    # The open points' heading in whichever language the document is written in (DEFECTS.md 62)
    heading = next((line for title in document_heading_utils.list_every("open_points") for line in (f"### {title}", f"## {title}")
                    if line in lines), None)
    if heading is None:
        return (markdown.rstrip("\n") + "\n\n" + f"## {output_contract.OPEN_POINTS_HEADING}" + "\n\n"
                + "\n".join(items) + "\n")
    first = lines.index(heading) + 1
    while first < len(lines) and not lines[first].strip():
        first += 1
    last = first
    while last < len(lines) and lines[last].startswith("- "):
        last += 1
    # The todo was about one of these open points, and its question is that point asked for a reader: printed beside
    # the old line, the document listed one point twice (pdf-02)
    old = f"- {' '.join(replacing.split())}" if replacing.strip() else ""
    if old and old in lines[first:last]:
        at = first + lines[first:last].index(old)
        lines[at:at + 1] = items

        return "\n".join(lines)
    # The question asks again open points the list already holds (a batch's question, a section's line): it takes the
    # place of the first, and the others go
    asked = [index for index in range(first, last) if any(is_point_asked_again(lines[index][2:], text) for text in texts)]
    if asked:
        lines[asked[0]:asked[0] + 1] = items
        shift = len(items) - 1
        for index in reversed(asked[1:]):
            del lines[index + shift]

        return "\n".join(lines)
    if len(lines[first:last]) == 1 and lines[first][2:] in document_heading_utils.list_every("nothing_open"):
        lines[first:last] = items
    else:
        lines[last:last] = items if last > first else [*items, ""]

    return "\n".join(lines)


def add_review_line(markdown: str,
                    counts: dict[str, int]) -> str:
    """`markdown` closed by one line saying how many points the review found, fixed and left open, linking its
    report beside the document."""

    found = counts["todos"]
    parts = [f"{found} point{'s' if found != 1 else ''} found", f"{counts['resolved']} fixed from the sources",
             f"{counts['unresolved']} left open"]
    if counts["blocked"]:
        parts.append(f"{counts['blocked']} not settled")
    line = (f"*Reviewed after writing: {', '.join(parts)}. See [the review]({output_contract.REVIEW_REPORT_FILE}).*")

    return markdown.rstrip("\n") + "\n\n" + line + "\n"
