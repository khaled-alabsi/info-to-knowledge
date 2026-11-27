"""A section the writing agent wrote as markdown, read back into blocks. Pure functions.

The agent lays its section out itself: paragraphs, lists, tables, diagrams and
frames in the order the text needs, `####` subheadings, and each decision, action item and open point marked where
it was said (`> **Decision:** ...`). Every block ends with the ids of the notes it rests on, in brackets, at the end of
its last line or on the line under it. The program reads each block's kind, text and ids back out, and strips the ids
before a reader sees the section. The ids in brackets are found by `document_text_utils`, the one place that knows
their pattern, so a topic link `[T08.3]` and a web link are never read as ids.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Callable

from utils import document_text_utils
from utils import frame_markdown_utils

__all__ = ["parse_section", "strip_entry_ids", "rewrite_diagrams", "build_section_record", "split_chapter_markdown",
           "replace_cut_ids", "end_lead_ins_into_nothing", "drop_unfinished_fences", "is_chapter_markdown",
           "list_heading_ids", "clean_chapter_markdown", "open_slide_fences", "keep_terminal_fences",
           "write_note_headings_as_bold", "CODE_MARK",
           "list_block_lines", "is_block_held", "BLOCK_KINDS", "split_draft_blocks", "apply_block_edits",
           "render_numbered_blocks"]

_FENCE = "```"
_MERMAID = "mermaid"
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
# `> **Decision:** The team keeps the proxy.` The mark's word, lower case, is the block's kind.
_CLAIM_KINDS = ("Decision", "Action", "Open")
_CLAIM = re.compile(rf"^>\s*\*\*({'|'.join(_CLAIM_KINDS)}):\*\*\s*(.*)$", re.DOTALL)
# `> **Set aside:** they only say what was clicked. [F3.2, F6.3]` covers notes that carry no knowledge, as an outline's
# set-aside list does; no reader sees it (2026-09-29: a chapter blocked three times over notes that only said what the
# presenter clicked)
_SET_ASIDE_KIND = "Set aside"
_SET_ASIDE = re.compile(rf"^>\s*\*\*{_SET_ASIDE_KIND}:\*\*\s*(.*)$", re.DOTALL)
# `> **What the figure shows:** a login page, white background. Left, the form "Sign in" ...` under a frame line: the
# outline a reader who cannot see the frame reads instead of it, and so does an agent reading the document as text.
_FIGURE_DESCRIPTION = re.compile(rf"^{document_text_utils.FIGURE_DESCRIPTION_START}\s*(.*)$", re.DOTALL)
# `![what it shows](cut-03 "what the reader takes from it")`
# The caption runs to the line's last `")`, so a quote inside it, escaped or not, is part of it (2026-10-12: a caption
# quoting a value as `\"right\"` read as no frame line, and the frame it placed as never placed)
_IMAGE = re.compile(r'^!\[(?P<alt>[^\]]*)\]\((?P<cut>[^\s)]+)(?:\s+"(?P<caption>.*)")?\)$')
# A look's box ids in brackets, "(C1)" or "(T2, T3)": the worker's handles on a frame, which a figure's description
# and caption carried to the reader (NIST's "labeled "NIST Cybersecurity Framework" (C1)")
_BOX_IDS = re.compile(r"\s*\((?:[CTPG]\d+)(?:,\s*[CTPG]\d+)*\)")
# and in any text block a bracketed list or range of them, "[C2-C5]" (the wiki's Z scheme paragraph); code keeps its own
_BRACKETED_BOX_IDS = re.compile(r"\s*\[(?:[CTPG]\d+(?:\s*[-–]\s*[CTPG]\d+)?)(?:,\s*[CTPG]\d+(?:\s*[-–]\s*[CTPG]\d+)?)*\]")
_BLANK_RUNS = re.compile(r"\n{3,}")
# A horizontal rule ("---", "***", "___") is layout, not a statement: one was refused as a block naming no entry id
_RULE_LINE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
# A heading's own line in a chapter answer, as the follow-up lists it: `### Refund limits (T02.1)`
_HEADING_LINE = re.compile(rf"^###\s+.*\((?P<heading_id>{document_text_utils.TOPIC_ID_PATTERN})\)\s*$")
# What a worker writes before a frame line's cut id and is no part of it: `./cut-03`
_CUT_PREFIX = r"[./]*"
_NAMES_ENTRY = re.compile(rf"\[[^\]]*\b{document_text_utils.ENTRY_ID_PATTERN}\b")
# The kinds a reader reads as prose, which every check of a paragraph applies to.
_PROSE_KINDS = ("paragraph", "list", "table", "code")
# Every marked line: a claim, a set-aside line, a figure's description
_MARKED_LINE = re.compile(rf"^>\s*(?:\*\*(?:{'|'.join((*_CLAIM_KINDS, _SET_ASIDE_KIND))}):\*\*"
                          rf"|{re.escape(document_text_utils.FIGURE_DESCRIPTION_LABEL)})")
# Entry ids in parentheses, "(S0110)." for "[S0110].", at a line's end or inside a paragraph: chapter answers were
# refused as unsourced and for ids in the text for it, two resumes (2026-10-06); parentheses holding other words are
# never touched
_PARENTHESISED_IDS = re.compile(rf"\((\s*{document_text_utils.ENTRY_ID_PATTERN}"
                                rf"(?:\s*[,–-]\s*{document_text_utils.ENTRY_ID_PATTERN})*\s*)\)")
# "on screen" the writer added to a sentence of its own; a quoted label or a diagram keeps it
# A chapter's own heading line, `### Title (T<chapter>.<heading>)`, and any heading of level 1 to 3, which a chapter
# holds no other of
_LISTED_HEADING = re.compile(r"^### .*\((T\d+\.\d+)\)\s*$")
_TOP_HEADING = re.compile(r"^#{1,3} ")


def read_chapter_draft(text: str,
                       listed_ids: set[str] | None = None) -> str:
    """The chapter a reply wrote as its text, read by the chapter's contract: from its first listed heading, each listed
    heading once, each with its text up to the next line outside a fence the contract forbids there: a heading of level
    1 to 3 that is no new listed heading, or a line opening with closing markup ("</parameter>"), which markdown never
    writes. A looped reply ran on past its chapter in one more way each run (its call's closing tags and thinking,
    DEFECTS 527; the chapter written again whole, 531), and cutting by each shape went round in a circle. With
    `listed_ids`, the chapter's own topic ids, a heading of another chapter ends it too: chapter 6 wrote chapter 7's two
    headings, then repeated them, and was checked as 4 sections for 2 headings (DEFECTS 636)."""

    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if _LISTED_HEADING.match(line.strip())), None)
    if start is None:
        return text
    seen, is_fenced, kept = set(), False, []
    for line in lines[start:]:
        stripped = line.strip()
        # Another chapter's heading ends it even inside a fence: the reply wrapped the whole chapter in a markdown
        # fence, so after the wrapper's close every later line counted as fenced (DEFECTS 636)
        other = _LISTED_HEADING.match(stripped)
        if other and listed_ids is not None and other.group(1) not in listed_ids:
            break
        if stripped.startswith(("```", "~~~")):
            is_fenced = not is_fenced
        elif not is_fenced:
            listed = _LISTED_HEADING.match(stripped)
            if (listed and listed.group(1) in seen) or (_TOP_HEADING.match(stripped) and not listed) or stripped.startswith("</"):
                break
            if listed:
                seen.add(listed.group(1))
        kept.append(line)

    return "\n".join(kept).rstrip()


def _drop_from_closing_markup(markdown: str) -> str:
    """`markdown` up to its first line outside a fence that opens with closing markup ("</"), which markdown never
    writes; the whole of it when it has none."""

    lines = markdown.split("\n")
    is_fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            is_fenced = not is_fenced
        elif not is_fenced and stripped.startswith("</"):
            return "\n".join(lines[:index]).rstrip()

    return markdown
# A fence the writer put round markdown: no language, or markdown itself
_MARKDOWN_FENCES = ("", "markdown", "md")
# A line that reads as prose: a sentence's end, a list item or a bullet, a table row, a bold line
_PROSE_LINE = re.compile(r"[.!?:,;)\"']\s*$|^\s*(?:[-*+•]|\d+\.)\s|^\s*\||^\s*\*\*.*\*\*\s*$")
# A fence named plain text holds a terminal's output as often as a slide's sentences: it is read as markdown only when
# every frame its ids cite is a slide (`open_slide_fences`)
_TEXT_FENCES = ("text", "txt", "plaintext")
_FRAME_OF_ID = re.compile(r"\bF(\d+)\.\d+")
# A line that makes a fenced block code: a brace, semicolon, assignment or angle bracket, a shell prompt, an indent
# of four spaces or a tab, or a line that is one call, `round(3.9)` (a slide's console lines of calls alone were opened
# as markdown and ran together into one paragraph)
# What share of a block's lines one earlier block must hold for it to repeat that block: the chapter request names such
# a block "cite only", and DOCUMENT_SAID_EARLIER refuses it written again; below it the block is written as it stands
HELD_SHARE = 0.6
# A line this alike an earlier block's line is that line read again
HELD_LINE_LIKENESS = 0.9
BLOCK_KINDS = ("code", "table", "list")
_BLOCK_LINE_MARK = re.compile(r"^\s*(?:[>#]+\s*|[-*+]\s+|\d+[.)]\s+)+")
# A ";" before a space and a word is a sentence's (pdf-06: "about the webhook test; Mara will do it by Friday" read
# a page's sentences as code, and they reached the writer as a block repeating the page's facts), and a winking ";)"
# ending a slide's line is no code (DEFECTS 658: a talk's "Conclusions" list was kept as a code box for it)
CODE_MARK = re.compile(r"[{}=<>]|;(?!\s+[A-Za-z]|-?[()]\s*$)|^\s*\$\s|^(?: {4}|\t)|^\s*[A-Za-z_][\w.]*\(.*\)\s*$")
# A line that makes a block more than sentences: code, a list item, a table row or a fence
_STRUCTURED_LINE = re.compile(rf"{CODE_MARK.pattern}|^\s*(?:[-*+]\s|\d+[.)]\s|\||```|~~~)")
_ON_SCREEN = re.compile(r"[ \t]+on (?:the )?screen\b", re.IGNORECASE)
# "on screen" right after a form of be or remain is the sentence's own end, never an aside to take out: "… is not on
# the screen; this is …" became "… is not; this is …" (DEFECTS 367); the narration check judges such a sentence
_ON_SCREEN_PREDICATE = re.compile(r"\b(?:is|are|was|were|be|been|appears?|stays?|remains?)(?:[ \t]+(?:not|only|still|also))?$",
                                  re.IGNORECASE)
_QUOTED_SPAN = re.compile(r'("[^"]*")')
# A markdown link wrapped whole in a code span: the reader gets the link's markdown as code, never a link (DEFECTS 226)
_CODED_LINK = re.compile(r"`\s*(\[[^\]`]+\]\((?:https?://|www\.)[^)\s`]+\))\s*`")


def _is_fence_line(line: str) -> bool:
    """Does `line` open a fence? Only at the left edge: an indented fence under a list item is the item's text."""

    return line.startswith(_FENCE)


def _is_mermaid_fence(line: str) -> bool:
    """Does `line` open a Mermaid fence, "```mermaid" or "``` mermaid"?"""

    return _is_fence_line(line) and line[len(_FENCE):].strip() == _MERMAID


def _is_closing_fence(line: str) -> bool:
    """Does `line` close an open fence? A bare fence, indented or not."""

    return line.strip() == _FENCE


def _remove_on_screen(markdown: str) -> str:
    """`markdown` with "on screen" taken out of the writer's own sentences, never inside a fence or a quoted span, and a
    link wrapped whole in a code span unwrapped."""

    lines = markdown.split("\n")
    is_fenced = False
    for index, line in enumerate(lines):
        if is_fenced:
            is_fenced = not _is_closing_fence(line)
            continue
        if _is_fence_line(line):
            is_fenced = True
            continue
        line = _CODED_LINK.sub(r"\1", line)
        # Split on a capturing group, the quoted spans stand at the odd places and are kept as written
        pieces = _QUOTED_SPAN.split(line)
        lines[index] = "".join(piece if number % 2 else _ON_SCREEN.sub(
            lambda match: match.group(0) if _ON_SCREEN_PREDICATE.search(match.string[:match.start()]) else "", piece)
                               for number, piece in enumerate(pieces))

    return "\n".join(lines)


def _find_closing_fence(lines: list[str],
                        opening: int) -> int | None:
    """The index of the line that closes the fence opened at `opening`; None when it is never closed."""

    return next((number for number in range(opening + 1, len(lines)) if _is_closing_fence(lines[number])), None)


def _append_text_block(raw: list[dict],
                       run: list[str]) -> list[str]:
    """Appends `run` to `raw` as a text block when it holds a line; returns the new, empty run."""

    if run:
        raw.append({"kind": "text", "lines": run})

    return []


def _add_entry_ids(block: dict,
                   entry_ids: list[str]) -> None:
    """`block` citing `entry_ids` too, each once, in the order first cited."""

    block["entry_ids"] = list(dict.fromkeys([*block["entry_ids"], *entry_ids]))


def _move_ids_to_lead_ins(blocks: list[dict]) -> None:
    """A prose block with no ids before a block with ids takes those ids when it leads into it, and a code block those
    of the code block right after it."""

    # A lead-in ending in a colon ("The response holds:") takes the ids of the block it introduces (2026-09-30: a
    # lead-in before a code block was read as a block with no ids, and the chapter was rejected). A prose block right
    # before a frame, with the frame's ids under the frame, says what the frame shows and takes its ids: the worker kept
    # writing the ids once, under the frame, and five chapter answers were refused for the paragraph above it
    # (2026-10-05). Read from the last block back, so a lead-in, its list and the frame are one.
    lead_ins: set[int] = set()
    for index in reversed(range(len(blocks) - 1)):
        lead, following = blocks[index], blocks[index + 1]
        is_uncited_prose = lead["kind"] in _PROSE_KINDS and not lead["entry_ids"]
        # A code block right before another (code, then the output it gives) is one piece with it: the worker wrote
        # their ids once, after the second fence, and was refused for the first (Lecture 9 chapter 2, three checks)
        # A formula is such a piece too: four formulas in a row, with lead-ins between them, were cited once, after the
        # last, and each formula was refused (DEFECTS 496)
        is_paired_code = ((lead["kind"] == "code" or is_formula(lead))
                          and (following["kind"] == "code" or is_formula(following)))
        # A paragraph right before a code block, a table or a list says what that block shows, as one before a frame
        # does: the worker wrote the ids once, after the block, and three paragraphs of one chapter were refused for
        # it (lecture-9 chapter 9, 2026-10-21)
        # A Mermaid diagram too: "A diagram illustrates the process of photorespiration …" was refused (the wiki's
        # chapter 9)
        is_block_intro = lead["kind"] == "paragraph" and (following["kind"] in BLOCK_KINDS or following["kind"] == "diagram"
                                                          or is_formula(following))
        # A list right before a table is one slide's block written as it stands (a bullet, then its table): the writer
        # wrote the note's ids once, after the table, and was refused for the bullet, check after check (pdf-03)
        is_list_before_table = lead["kind"] == "list" and following["kind"] == "table"
        # A table and the footnote written under it ("¹ The numbers ... [ids]") are one piece: the writer cited the
        # table's notes on its footnote and the table was refused (DEFECTS 498)
        is_table_before_footnote = (lead["kind"] == "table" and following["kind"] == "paragraph"
                                    and str(following["text"]).lstrip()[:1] in _FOOTNOTE_MARKS)
        is_lead_in = (lead["text"].rstrip().endswith(":") or following["kind"] == "image" or index + 1 in lead_ins
                      or is_paired_code or is_block_intro or is_list_before_table or is_table_before_footnote)
        if is_uncited_prose and following["entry_ids"] and is_lead_in:
            lead["entry_ids"] = list(following["entry_ids"])
            lead_ins.add(index)
    # The other way round: a lead-in ending in a colon that names its ids says what the block under it is, and the
    # writer cited that block's note once, on the lead-in (NIST chapter 1's check refused the table under it)
    for lead, following in zip(blocks, blocks[1:]):
        if (lead["kind"] == "paragraph" and lead["entry_ids"] and lead["text"].rstrip().endswith(":")
                and following["kind"] in BLOCK_KINDS and not following["entry_ids"]):
            following["entry_ids"] = list(lead["entry_ids"])


def _list_cited_texts(blocks: list[dict],
                      kind: str) -> list[dict]:
    """`{"text", "entry_ids"}` of every block of `kind`."""

    return [{"text": block["text"], "entry_ids": block["entry_ids"]} for block in blocks if block["kind"] == kind]


def _is_ids_line(line: str) -> bool:
    return bool(line.strip()) and not document_text_utils.remove_bracketed_entry_ids(line).strip()


def _is_fenced_markdown(opening: str,
                        lines: list[str]) -> bool:
    """Does the fence opened by `opening` hold markdown, not code: no language or markdown named, a line in it, and no
    line with a mark of code?"""

    # A code block inside it, fenced with tildes, is the markdown's own code block (DEFECTS 650): only the lines outside
    # one are read
    outside = [line for line, is_outside in zip(lines, _mark_outside_tilde_fences(lines)) if is_outside]

    return (opening[len(_FENCE):].strip().casefold() in _MARKDOWN_FENCES and any(line.strip() for line in outside)
            and not any(CODE_MARK.search(line) for line in outside) and _is_prose(outside))


def _mark_outside_tilde_fences(lines: list[str]) -> list[bool]:
    """For each of `lines`, whether it stands outside a fence written with tildes, its fence lines counted inside."""

    marks, is_inside = [], False
    for line in lines:
        is_fence = line.strip().startswith("~~~")
        marks.append(not is_inside and not is_fence)
        is_inside ^= is_fence

    return marks


def _is_prose(lines: list[str]) -> bool:
    """Do half of `lines` or more read as prose: a sentence's end, a list item or bullet, a table row, a bold line? A
    file's lines read as none of these: a `.gitignore`'s "# " comments and paths were opened as markdown and its
    comments shown as headings (DEFECTS 385). Of 56 public fences opened as markdown, it keeps 11 as code, each a
    process table, a file listing, a terminal's log, a game's screen or a demo window."""

    kept = [line for line in lines if line.strip()]

    return 2 * len([line for line in kept if _PROSE_LINE.search(line)]) >= len(kept)


def _is_plain_line(line: str) -> bool:
    """Is `line` a line of text, no list, table, heading, bold line or code?"""

    return bool(line.strip()) and not _STRUCTURED_LINE.search(line) and not line.lstrip().startswith(("#", "**"))


def _keep_line_breaks(lines: list[str]) -> list[str]:
    """A slide's lines with a hard break (a trailing backslash) after each plain line that another plain line follows:
    read as one paragraph, a slide's title and items ran on as one line, "Contents Motivation Hardware …"."""

    return [f"{line.rstrip()}\\" if _is_plain_line(line) and position + 1 < len(lines) and _is_plain_line(lines[position + 1])
            else line for position, line in enumerate(lines)]


def _open_fence(lines: list[str],
                index: int,
                closing: int) -> list[str]:
    """The lines of the fence opened at `index` and closed at `closing`, read as the markdown they hold: headings as
    bold lines, a slide's plain lines kept on their own, the ids after the fence closing each of their blocks."""

    held = lines[index + 1:closing]
    outside = _mark_outside_tilde_fences(held)
    # The lines of a tilde-fenced code block inside it stay as written, no heading made bold, no line break added
    inner, run = [], []
    for line, is_outside in zip(held, outside):
        if is_outside:
            run.append(line)
            continue
        if run:
            inner += _keep_line_breaks(frame_markdown_utils.render_headings_as_bold("\n".join(run)).split("\n"))
            run = []
        # Opened, the code block is nested no more: its fence goes back to backticks, the fence the reader splits on
        inner.append(_FENCE + line.strip()[3:] if line.strip().startswith("~~~") else line)
    if run:
        inner += _keep_line_breaks(frame_markdown_utils.render_headings_as_bold("\n".join(run)).split("\n"))
    # A lead-in written right above the fence ("The agenda is as follows:") stays its own paragraph: joined to the
    # opened lines with no blank line between, a reader got "follows: Inhalt" on one line (DEFECTS 407)
    apart = [""] if index and lines[index - 1].strip() else []

    return apart + _add_fence_ids(inner, _find_ids_after(lines, closing + 1))


def _find_ids_after(lines: list[str],
                    start: int) -> str:
    """The ids line a fence is followed by, past blank lines; "" when the next text is no ids line."""

    following = next((line for line in lines[start:] if line.strip()), "")

    return following.strip() if _is_ids_line(following) else ""


def _add_fence_ids(inner: list[str],
                     ids_line: str) -> list[str]:
    """A fence's lines with `ids_line` closing each of their blocks but the last, which the ids line itself closes: the
    writer cited the slide once, after the fence, and every paragraph and list read out of it was refused as naming no
    id (lecture-1 chapter 6, 2026-10-14)."""

    if not ids_line:
        return inner
    carried: list[str] = []
    for position, line in enumerate(inner):
        is_block_end = (bool(line.strip()) and any(rest.strip() for rest in inner[position + 1:])
                        and (position + 1 >= len(inner) or not inner[position + 1].strip()))
        carried.append(line)
        if is_block_end:
            carried.append(ids_line)

    return carried


_FORMULA_MARK = "$$"
# The marks a table's footnote opens with: a superscript digit or an asterisk
_FOOTNOTE_MARKS = "¹²³⁴⁵⁶⁷⁸⁹⁰*†"


def _find_formula_end(lines: list[str],
                      index: int) -> int | None:
    """The index of the line that closes the formula opening at `lines[index]`: itself for `$$ ... $$` on one line, the
    next line that is `$$` alone for a `$$` line; None when no formula opens there."""

    line = lines[index].strip()
    if len(line) > 2 * len(_FORMULA_MARK) and line.startswith(_FORMULA_MARK) and line.endswith(_FORMULA_MARK):
        return index
    if line != _FORMULA_MARK:
        return None

    return next((later for later in range(index + 1, len(lines)) if lines[later].strip() == _FORMULA_MARK), None)


def is_formula(block: dict) -> bool:
    """Whether `block` is a formula written between `$$` marks."""

    text = str(block.get("text") or "").strip()

    return text.startswith(_FORMULA_MARK) and text.endswith(_FORMULA_MARK) and len(text) > 2 * len(_FORMULA_MARK)


def _split_raw_blocks(markdown: str) -> list[dict]:
    """The section cut into raw blocks, each with its `kind`: `{"kind": "text", "lines"}`, `{"kind": "fence", "fence",
    "lines", "is_unclosed"}` for a fenced block, and `{"kind": "ids_line", "ids_line"}` for a line holding only ids. A
    heading is always a block of its own."""

    raw: list[dict] = []
    lines = markdown.splitlines()
    index = 0
    run: list[str] = []
    while index < len(lines):
        line = lines[index]
        if _is_fence_line(line):
            run = _append_text_block(raw, run)
            closing = _find_closing_fence(lines, index)
            end = len(lines) if closing is None else closing
            # A slide's sentences or list fenced with no language reached the reader as a code box of prose (the
            # owner, 2026-10-13: eleven in one document, after three request lines asked for no such box): a fence
            # holding no code is read as the markdown it holds
            if closing is not None and _is_fenced_markdown(line, lines[index + 1:end]):
                lines[index:end + 1] = _open_fence(lines, index, end)
                continue
            raw.append({"kind": "fence", "fence": line[len(_FENCE):].strip(), "lines": lines[index + 1:end],
                        "is_unclosed": closing is None})
            index = end + 1
            continue
        # A formula, `$$` to `$$` or `$$ ... $$` on one line, is a block of its own, as a fence is: written with no
        # blank line around it, four formulas and their lead-ins ran into blocks no ids could be placed on (DEFECTS 496)
        formula_end = _find_formula_end(lines, index)
        if formula_end is not None:
            run = _append_text_block(raw, run)
            raw.append({"kind": "text", "lines": lines[index:formula_end + 1]})
            index = formula_end + 1
            continue
        # A marked line, `> **Set aside:** ...` or `> **Decision:** ...`, opens a block of its own even with no blank
        # line above it (2026-09-29: a set-aside line written under a paragraph was read as part of the paragraph)
        if _MARKED_LINE.match(line):
            run = _append_text_block(raw, run)
        # A frame line is always a block of its own, with or without blank lines around it; an ids line under it is
        # its ids (2026-09-29: a frame line written under a paragraph was read as the paragraph's text)
        if _IMAGE.match(document_text_utils.remove_bracketed_entry_ids(line).strip()):
            run = _append_text_block(raw, run)
            raw.append({"kind": "text", "lines": [line]})
            index += 1
            continue
        if _HEADING.match(line):
            run = _append_text_block(raw, run)
            raw.append({"kind": "text", "lines": [line]})
        elif not line.strip():
            run = _append_text_block(raw, run)
        elif _is_ids_line(line) and not run:
            raw.append({"kind": "ids_line", "ids_line": line})
        else:
            # A marked line is one line: a sentence written under it with no blank line between ran into it, and the
            # closing list printed that sentence as an action item (lecture-1, 2026-10-15); an ids line under it, or a
            # line of the same quote, is still its own
            if run and _MARKED_LINE.match(run[0]) and not line.lstrip().startswith(">") and not _is_ids_line(line):
                run = _append_text_block(raw, run)
            run.append(line)
        index += 1
    _append_text_block(raw, run)

    return raw


def _read_text_block(lines: list[str]) -> dict:
    """One block of text: its kind from its first line, its text with the ids taken out, and those ids."""

    heading = _HEADING.match(lines[0])
    if heading:
        level = len(heading.group(1))

        return {"kind": "subheading" if level >= 4 else "heading", "level": level, "text": heading.group(2),
                "entry_ids": []}
    moved = document_text_utils.move_bracketed_entry_ids({"text": _BRACKETED_BOX_IDS.sub("", "\n".join(lines))})
    text, entry_ids = moved["text"], moved["entry_ids"]
    claim = _CLAIM.match(text)
    image = _IMAGE.match(text)
    set_aside = _SET_ASIDE.match(text)
    description = _FIGURE_DESCRIPTION.match(text)
    if description:
        block = {"kind": "figure_description", "text": _BOX_IDS.sub("", description.group(1)).strip()}
    elif set_aside:
        block = {"kind": "set_aside", "text": set_aside.group(1).strip()}
    elif claim:
        block = {"kind": claim.group(1).lower(), "text": claim.group(2).strip()}
    elif image:
        # A leading slash or dot is not part of the cut's id, and an empty alt text is the caption (the owner,
        # 2026-09-29)
        caption = _BOX_IDS.sub("", (image.group("caption") or "").replace('\\"', '"'))
        block = {"kind": "image", "text": text, "cut": re.sub(rf"^{_CUT_PREFIX}", "", image.group("cut")),
                 "alt": image.group("alt").strip() or caption, "caption": caption, "description": ""}
    elif text.startswith("|"):
        block = {"kind": "table", "text": text}
    elif _LIST_ITEM.match(text):
        block = {"kind": "list", "text": text}
    else:
        block = {"kind": "paragraph", "text": text}

    return {**block, "entry_ids": entry_ids}


# A lead-in announces the block under it: one ending in a colon with nothing such under it reads unfinished (DEFECTS 381)
_TRAILING_IDS = re.compile(r"(\s*\[[^\]]*\])+\s*$")


def _list_written_blocks(markdown: str) -> list[tuple[str, str]]:
    """The section's blocks as written, `(kind, text)`: `fence` for a fenced block whatever it holds, `text` for a run of
    other lines up to a blank line; a line holding only ids is no block."""

    blocks: list[tuple[str, str]] = []
    run: list[str] = []
    fence: list[str] | None = None
    for line in markdown.splitlines():
        if fence is not None:
            fence.append(line)
            if line.strip().startswith("```"):
                blocks.append(("fence", "\n".join(fence)))
                fence = None
            continue
        if line.strip().startswith("```"):
            if run:
                blocks.append(("text", "\n".join(run)))
                run = []
            fence = [line]
        elif not line.strip():
            if run:
                blocks.append(("text", "\n".join(run)))
                run = []
        else:
            run.append(line)
    if run:
        blocks.append(("text", "\n".join(run)))
    if fence is not None:
        blocks.append(("fence", "\n".join(fence)))

    return [(kind, text) for kind, text in blocks if not (kind == "text" and _TRAILING_IDS.fullmatch(text.strip()))]


def _list_lead_in_blocks(markdown: str) -> list[str]:
    """Each paragraph of the section, as written, that ends in a colon (its ids after it or not) with no code block,
    diagram, table, list, figure or quoted text right under it: a sentence announcing what never follows. Over the
    public runs' 68 such paragraphs, 7 led into nothing (an Open line, the hidden set-aside line, another paragraph, the
    end). A quote that is no marked line (`> Hi! …`, a comment the screen shows) is a block it leads into."""

    blocks = _list_written_blocks(markdown)
    found = []
    for position, (kind, text) in enumerate(blocks):
        text = text.strip()
        if kind != "text" or text.startswith((">", "#", "|", "![")) or _LIST_ITEM.match(text):
            continue
        if not _TRAILING_IDS.sub("", text).rstrip().endswith(":"):
            continue
        following_kind, following = blocks[position + 1] if position + 1 < len(blocks) else ("", "")
        following = following.lstrip()
        is_quote = following.startswith(">") and not _MARKED_LINE.match(following)
        if following_kind == "fence" or is_quote or following.startswith(("|", "![")) or _LIST_ITEM.match(following):
            continue
        found.append(text)

    return found


def list_lead_ins_into_nothing(markdown: str) -> list[str]:
    """The end of each paragraph that ends in a colon with nothing under it (`_list_lead_in_blocks`)."""

    return [_TRAILING_IDS.sub("", text).rstrip()[-80:] for text in _list_lead_in_blocks(markdown)]


def drop_unfinished_fences(markdown: str) -> str:
    """`markdown` without each fence of markdown whose one line ends in a colon: a slide's unfinished sentence ("Electron
    apps are:", its rest a picture) stood as a block of its own (DEFECTS 382). The ids after it stay, and go with the
    block before it."""

    lines = markdown.splitlines()
    index, is_dropped = 0, False
    while index < len(lines):
        closing = _find_closing_fence(lines, index) if _is_fence_line(lines[index]) else None
        if closing is None:
            index += 1
            continue
        inner = [line for line in lines[index + 1:closing] if line.strip()]
        if (len(inner) == 1 and inner[0].rstrip().endswith(":")
                and _is_fenced_markdown(lines[index], lines[index + 1:closing])):
            del lines[index:closing + 1]
            is_dropped = True
            continue
        index = closing + 1

    return "\n".join(lines) if is_dropped else markdown


def end_lead_ins_into_nothing(markdown: str) -> str:
    """`markdown` with the colon of each paragraph that leads into nothing made a full stop, its ids kept after it: a
    sentence announcing a block that was never written is a whole sentence once it ends. Refused instead, the chapter
    was asked again for a stop, four accepted parts of one talk on a resume (DEFECTS 381, 394)."""

    for text in _list_lead_in_blocks(markdown):
        bare = _TRAILING_IDS.sub("", text).rstrip()
        markdown = markdown.replace(text, bare[:-1].rstrip() + "." + text[len(bare):], 1)

    return markdown


def parse_section(markdown: str) -> list[dict]:
    """Every block of the section, in the order written: `{"kind", "text", "entry_ids"}`, plus `level` for a heading,
    `cut`, `alt`, `caption` and `description` for an image, and `is_unclosed` for a fenced block. Kinds: paragraph,
    list, table, diagram (a Mermaid fence; its text is the source), code (any other fence), image, subheading (`####`
    and below), heading (`###` and above, which belong to the table of contents), decision, action, open, set_aside,
    and never a figure_description: one under a frame goes with it, one with no frame above it is dropped. A line of ids alone belongs to the block above it."""

    blocks: list[dict] = []
    for raw in _split_raw_blocks(markdown):
        if raw["kind"] == "ids_line":
            cited = document_text_utils.move_bracketed_entry_ids({"text": raw["ids_line"]})["entry_ids"]
            if blocks:
                _add_entry_ids(blocks[-1], cited)
            continue
        if raw["kind"] == "fence":
            blocks.append({"kind": "diagram" if raw["fence"] == _MERMAID else "code", "text": "\n".join(raw["lines"]),
                           "entry_ids": [], "is_unclosed": raw["is_unclosed"]})
            continue
        block = _read_text_block(raw["lines"])
        # A figure's description belongs to the frame above it, never to the reader's prose: it is rendered under the
        # caption and carries the frame's own ids, so no check reads it as a claim of its own.
        if block["kind"] == "figure_description" and blocks and blocks[-1]["kind"] == "image":
            blocks[-1]["description"] = block["text"]
            # The ids line under the description is the figure's: it is written under the whole figure, and the
            # description carries no claim of its own.
            _add_entry_ids(blocks[-1], block["entry_ids"])
            continue
        # A description with no figure above it describes nothing a reader sees: a frame left out kept its box, the
        # document printed it as a bare sentence about "the slide", and the whole document was refused (2026-10-13).
        # It is dropped, its ids kept on the block before it.
        if block["kind"] == "figure_description":
            if blocks:
                _add_entry_ids(blocks[-1], block["entry_ids"])
            continue
        blocks.append(block)

    return blocks


def strip_entry_ids(markdown: str) -> str:
    """The section as a reader sees it: every note id in brackets taken out, and a line that held only ids dropped."""

    lines = [document_text_utils.remove_bracketed_entry_ids(line) for line in markdown.splitlines()
             if not _is_ids_line(line) and not _SET_ASIDE.match(line.strip())]
    text = _BLANK_RUNS.sub("\n\n", "\n".join(lines).strip()) + "\n"

    return text


def rewrite_diagrams(markdown: str,
                     rewrite: Callable[[str], str]) -> str:
    """`markdown` with the source of every Mermaid fence replaced by `rewrite(source)`, where it stands."""

    lines = markdown.split("\n")
    rewritten: list[str] = []
    index = 0
    while index < len(lines):
        rewritten.append(lines[index])
        if not _is_mermaid_fence(lines[index]):
            index += 1
            continue
        closing = _find_closing_fence(lines, index)
        closing = len(lines) if closing is None else closing
        rewritten += rewrite("\n".join(lines[index + 1:closing])).split("\n")
        index = closing

    return "\n".join(rewritten)


def build_section_record(markdown: str) -> dict[str, Any]:
    """The section as the program reads it: the markdown, its blocks in order (what the document is rendered from),
    and, derived from those blocks, the lists the checks and the closing read: `paragraphs` (every prose block),
    `diagrams`, `decisions`, `action_items`, `not_established` and `images`. The markdown is the only thing stored."""

    # STEP 1: the blocks, each lead-in given the ids of the block it leads into, a colon into nothing ended first
    markdown = end_lead_ins_into_nothing(drop_unfinished_fences(markdown))
    blocks = parse_section(markdown)
    _move_ids_to_lead_ins(blocks)

    # STEP 2: the lists the checks and the closing read, derived from the blocks
    record = {
        "markdown": markdown, "blocks": blocks,
        "paragraphs": [{"text": block["text"], "entry_ids": block["entry_ids"], "kind": block["kind"]} for block in blocks
                       if block["kind"] in _PROSE_KINDS and not _RULE_LINE.match(block["text"])],
        "diagrams": [{"mermaid": block["text"], "entry_ids": block["entry_ids"]} for block in blocks
                     if block["kind"] == "diagram"],
        "decisions": _list_cited_texts(blocks, "decision"),
        "action_items": _list_cited_texts(blocks, "action"),
        "not_established": [block["text"] for block in blocks if block["kind"] == "open"],
        "images": [{"cut": block["cut"], "alt_text": block["alt"], "caption": block["caption"],
                    "description": block.get("description", ""),
                    "entry_ids": block["entry_ids"]} for block in blocks if block["kind"] == "image"]}

    return record


def replace_cut_ids(markdown: str,
                    cut_files: dict[str, str]) -> str:
    """`markdown` with each frame line's cut id, `![..](cut-03 "..")`, replaced by the path of its file in `cut_files`,
    so the frame shows where the text is read. A cut `cut_files` does not hold is left as written."""

    return re.sub(rf"(!\[[^\]]*\]\(){_CUT_PREFIX}({document_text_utils.CUT_ID_PATTERN})(?=[\s)])",
                  lambda match: match.group(1) + cut_files.get(match.group(2), match.group(2)), markdown)


def is_chapter_markdown(text: str) -> bool:
    """Does `text` hold a chapter's answer: at least one heading's own line, `### Refund limits (T02.1)`?"""

    return any(_HEADING_LINE.match(line.strip()) for line in text.splitlines())


def list_heading_ids(markdown: str) -> list[str]:
    """The heading id of each heading's own line in a chapter's answer, in order: `### Refund limits
    (T<chapter>.<heading>)`
    gives its id. The split goes by position, so this is how an answer that wrote another part's heading is told."""

    return [match.group("heading_id") for line in markdown.splitlines() if (match := _HEADING_LINE.match(line.strip()))]


_INLINE_MARK = re.compile(rf"(?<=\S)[ \t]+(>[ \t]*\*\*(?:{'|'.join((*_CLAIM_KINDS, _SET_ASIDE_KIND))}):\*\*)")


def _drop_stray_closing_fence(lines: list[str]) -> list[str]:
    """`lines` without a lone "```" last line that closes nothing: an answer ended in one, left from wrapping it with no
    opening line, and was refused twice as a fence never closed, with an empty unsourced block (2026-10-12)."""

    last = max((index for index, line in enumerate(lines) if line.strip()), default=None)
    fences = [line for line in lines if _is_fence_line(line)]
    if last is None or lines[last].strip() not in ("```", "~~~") or len(fences) % 2 == 0:
        return lines

    return lines[:last] + lines[last + 1:]


def _drop_fence_after_table(lines: list[str]) -> list[str]:
    """`lines` without each lone "```" that comes right after a table's row, outside an open fence, with an ids line under
    it: it closes a block never opened, and opened as a fence it took the ids, the next block and the prose after them
    for code, five drafts running of one chapter (DEFECTS 594). Over 804 public chapter drafts, all 7 such lines stood
    right before an ids line."""

    kept: list[str] = []
    is_fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        is_stray = (not is_fenced and stripped == _FENCE and bool(kept) and kept[-1].lstrip().startswith("|")
                    and _is_ids_line(document_text_utils.write_short_ranges_in_full(following)))
        if is_stray:
            continue
        if stripped.startswith(("```", "~~~")):
            is_fenced = not is_fenced
        kept.append(line)

    return kept


def _is_cited_sentence_line(line: str) -> bool:
    """Is `line` a plain line that cites its own notes and ends a sentence: "Shard: one of the eight databases [S0028]."?"""

    bare = document_text_utils.remove_bracketed_entry_ids(line).rstrip()

    return (_is_plain_line(line) and not line.lstrip().startswith((">", "![")) and bare != line.rstrip()
            and bare.endswith((".", "!", "?")))


def _split_sentence_lines(markdown: str) -> str:
    """`markdown` with each paragraph of two lines or more, every one a cited sentence, written as one paragraph a line:
    a glossary's three definitions, each its own line and ids, ran together on the page as one line (DEFECTS 503).
    Lines inside a fence are never touched."""

    lines = markdown.splitlines()
    split: list[str] = []
    index = 0
    while index < len(lines):
        closing = _find_closing_fence(lines, index) if _is_fence_line(lines[index]) else None
        if closing is not None:
            split += lines[index:closing + 1]
            index = closing + 1
            continue
        end = index
        while end < len(lines) and lines[end].strip() and not _is_fence_line(lines[end]):
            end += 1
        run = lines[index:end] or [lines[index]]
        is_split = len(run) >= 2 and all(_is_cited_sentence_line(line) for line in run)
        split += [part for line in run for part in (line, "")][:-1] if is_split else run
        index = max(end, index + 1)

    return "\n".join(split)


def clean_chapter_markdown(markdown: str) -> str:
    """A chapter's answer as the program reads it: an answer fence around the whole taken off, "on screen" taken out
    of the writer's own sentences, a link wrapped in a code span unwrapped, and entry ids in parentheses written in
    brackets. Cleaning twice changes nothing."""

    # An answer wrapped whole in "```markdown" gave its first paragraph the fence line and its last heading an empty
    # block, both refused as unsourced (2026-10-02); taken off first, so the clean-ups below read the answer itself
    markdown = "\n".join(_drop_fence_after_table(
        _drop_stray_closing_fence(frame_markdown_utils.unwrap_answer_fence(markdown.splitlines()))))
    # "on screen" added to a sentence is taken out here, where the sentence reads right without it: five chapter answers
    # blocked on it, each a whole resume (2026-09-30). A deterministic fix beats a retry; "frame" is left to the check
    markdown = _remove_on_screen(markdown)
    # A marked line written on after a paragraph's last sentence, on the same line, is put on a line of its own: read as
    # the paragraph's text, a set-aside line was published to readers (2026-10-12)
    markdown = _INLINE_MARK.sub("\n\n\\1", markdown)
    # The answer ends at a line outside a fence that opens with closing markup, "</parameter>", which markdown never
    # writes: the model's own call tags, read as a block of the text, refused a chapter as citing no note at its end
    # (DEFECTS 518), and again with "<br><br>" after them (DEFECTS 592); the chapter's contract, as for a looped reply
    markdown = _drop_from_closing_markup(markdown)
    markdown = _PARENTHESISED_IDS.sub(r"[\1]", markdown)

    return _split_sentence_lines(markdown)


def open_slide_fences(markdown: str,
                      slide_frames: set[int]) -> str:
    """`markdown` with each fence named plain text whose ids line cites slide frames alone opened as a fence with no
    language, which the section is then read through as the markdown it holds unless a line in it is code. A fence
    citing a terminal's frame stays as written: its output is read in a code box."""

    lines = markdown.splitlines()
    opened: dict[int, int] = {}
    for index, line in enumerate(lines):
        if not (_is_fence_line(line) and line.strip()[len(_FENCE):].strip().casefold() in _TEXT_FENCES):
            continue
        closing = _find_closing_fence(lines, index)
        if closing is None:
            continue
        frames = {int(number) for number in _FRAME_OF_ID.findall(_find_ids_after(lines, closing + 1))}
        if frames and frames <= slide_frames and not any(CODE_MARK.search(each) for each in lines[index + 1:closing]):
            # Opened here, where the frames are known slides: a slide's plain lines need not read as prose
            opened[index] = closing
    for index, closing in sorted(opened.items(), reverse=True):
        lines[index:closing + 1] = _open_fence(lines, index, closing)

    return "\n".join(lines)


def write_note_headings_as_bold(markdown: str,
                       note_texts: list[str]) -> str:
    """`markdown` with each heading line that is a heading of one of `note_texts` (a screen's markdown) made a bold line:
    a slide's own title copied with its block is no part of the document's outline. Chapter 5 of 38c3-fault-injection
    wrote the slides' "### Reversing", "Reversing auth logic" and "Reversing more auth logic" as its `####` subheadings
    (DEFECTS 655), as a block's headings inside a fence already are (`frame_markdown_utils.render_headings_as_bold`).
    A listed heading of the chapter stays as it is."""

    titles = {match.group(2).strip().casefold() for text in note_texts for match in
              (_HEADING.match(line.strip()) for line in str(text).splitlines()) if match}
    if not titles:
        return markdown
    lines = markdown.split("\n")
    is_fenced = False
    for index, line in enumerate(lines):
        if _is_fence_line(line.strip()):
            is_fenced = not is_fenced
            continue
        match = _HEADING.match(line.strip())
        if (not is_fenced and match and not _LISTED_HEADING.match(line.strip())
                and match.group(2).strip().casefold() in titles):
            lines[index] = f"**{match.group(2).strip()}**"

    return "\n".join(lines)


def keep_terminal_fences(markdown: str,
                         terminal_frames: set[int]) -> str:
    """`markdown` with each fence named markdown, or no language, whose ids line cites terminal frames alone renamed a
    plain-text fence, which the section reader keeps as code: a terminal's permission dialog fenced as markdown read as
    prose by its numbered options and sentences, was opened, and each of its lines reached the reader as a hard-broken
    paragraph (DEFECTS 609). The frames' kind is their notes' own, the counterpart of `open_slide_fences`."""

    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _is_fence_line(line):
            index += 1
            continue
        closing = _find_closing_fence(lines, index)
        if closing is None:
            break
        # Only an opening fence is renamed, and the walk goes on after its closing one: a closing "```" read as an
        # opening fence was renamed "```text" when the next block cited a terminal, and the block never closed
        # (DEFECTS 654: chapter 7's terminal blocks, refused as an open fence five checks running)
        frames = {int(number) for number in _FRAME_OF_ID.findall(_find_ids_after(lines, closing + 1))}
        if (line.strip()[len(_FENCE):].strip().casefold() in _MARKDOWN_FENCES and frames
                and frames <= terminal_frames):
            lines[index] = f"{line[:len(line) - len(line.lstrip())]}{_FENCE}text"
        index = closing + 1

    return "\n".join(lines)


def split_chapter_markdown(markdown: str) -> list[str]:
    """A chapter's one markdown as the text of each heading, in order: it is cut at every line that is a heading's own
    line (`### Refund limits (T02.1)`), and what stands before the first such line goes to the first heading when it
    names an entry id, and is dropped as the worker's own words when it names none. With no such line the whole text
    is one part, which the chapter's count then refuses by number. The answer is cleaned first
    (`clean_chapter_markdown`)."""

    # STEP 1: clean the answer
    markdown = clean_chapter_markdown(markdown)

    # STEP 2: cut at each heading's own line
    parts: list[list[str]] = []
    before: list[str] = []
    for line in markdown.splitlines():
        if _HEADING_LINE.match(line.strip()):
            parts.append([])
        elif parts:
            parts[-1].append(line)
        else:
            before.append(line)
    if not parts:
        return [markdown.strip()] if markdown.strip() else []

    # STEP 3: before the first heading line: text naming an entry id is the first heading's opening; text naming none
    # is the worker talking to itself ("To fix the rejection, I ..."), not read, as phase 3 reads no words before its
    # first document (2026-10-05: two chapter answers refused for it)
    if _NAMES_ENTRY.search("\n".join(before)):
        parts[0] = [*before, *parts[0]]

    return ["\n".join(part).strip() for part in parts]


def list_block_lines(text: str) -> set[str]:
    """A markdown block's lines as plain words: the list mark, list number and heading mark that start a line off, case
    and spacing evened, a fence line left out (a note holds its fences, a written block's record does not); a figure
    inside the line stays."""

    return {" ".join(_BLOCK_LINE_MARK.sub("", line).casefold().split()) for line in text.splitlines()
            if _BLOCK_LINE_MARK.sub("", line).strip() and not line.lstrip().startswith(("```", "~~~"))}


def is_block_held(lines: set[str],
                  earlier_blocks: list[set[str]]) -> bool:
    """Whether one earlier block holds HELD_SHARE of a block's lines, so the block repeats it. A line two earlier blocks
    or more hold is a template's fixed line (a permission dialog's "Do you want to proceed?", "1. Yes", "3. No") and is
    left out of the share: a dialog for another command was refused as a repeat of an earlier dialog, the lines it
    "adds" being its whole command (DEFECTS 595). A block made of such lines alone is judged whole, as before."""

    content = {line for line in lines if sum(1 for earlier in earlier_blocks if _count_held_lines({line}, earlier)) < 2}
    lines = content or lines

    return bool(lines) and any(_count_held_lines(lines, earlier) >= HELD_SHARE * len(lines) for earlier in earlier_blocks)


def list_unheld_lines(lines: list[str],
                      earlier_blocks: list[set[str]]) -> list[str]:
    """The lines of a block, in its order, that the earlier block holding most of it does not hold, a line read one
    letter off counting as held: what a repeated table or list adds (DEFECTS 388)."""

    holder = max(earlier_blocks, key=lambda earlier: _count_held_lines(set(lines), earlier), default=set())

    return [line for line in lines if line not in holder and not any(
        difflib.SequenceMatcher(None, line, other).ratio() >= HELD_LINE_LIKENESS for other in holder)]


def _count_held_lines(lines: set[str],
                      earlier: set[str]) -> int:
    """How many of `lines` an earlier block holds, a line read one letter off counting (SequenceMatcher at
    HELD_LINE_LIKENESS): one slide's list read twice, "Temperatursmessung" and "Temperaturmessung", was written twice
    in one heading (DEFECTS 243)."""

    exact = lines & earlier

    return len(exact) + sum(1 for line in lines - exact if any(
        difflib.SequenceMatcher(None, line, other).ratio() >= HELD_LINE_LIKENESS for other in earlier - exact))


def split_twin_blocks(entries: list[dict],
                      written_blocks: list[set[str]]) -> tuple[list[str], list[str], list[str]]:
    """The markdown notes of `entries` sorted: blocks to write, blocks an earlier one in `written_blocks` already holds
    (twins), and slides' sentences (prose); `written_blocks` grows by each block to write, so a later twin of it is one
    too. The request names each group, and the twins are no note a section has to cite (DEFECTS 236)."""

    blocks, twins, prose = [], [], []
    for entry in entries:
        if entry.get("kind") != "markdown":
            continue
        if is_prose_block(str(entry.get("text") or "")):
            prose.append(entry["id"])
            continue
        lines = list_block_lines(str(entry.get("text") or ""))
        is_twin = is_block_held(lines, written_blocks)
        (twins if is_twin else blocks).append(entry["id"])
        written_blocks += [] if is_twin else [lines]

    return blocks, twins, prose


def is_prose_block(text: str) -> bool:
    """Whether a markdown note holds only sentences or a title: no list item, table row, fence, command or code."""

    lines = [line for line in text.splitlines() if line.strip()]

    return bool(lines) and not any(_STRUCTURED_LINE.search(line) for line in lines)


# A table's separator row: "|---|---|", with or without colons and spaces
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


def count_table_rows(text: str) -> int:
    """The rows of every markdown table in `text`, each table's header and separator rows left out; a table written on
    one line (a speech paragraph's) counted by its "| |" row breaks."""

    lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) == 1:
        # A table a speech paragraph holds on one line: its rows break at "| |"; an empty cell of a table written on
        # lines of its own is never split
        lines = [line for line in lines[0].replace("| |", "|\n|").splitlines() if line.strip()]
    separators = [index for index, line in enumerate(lines) if _TABLE_SEPARATOR.match(line)]

    return max(len(lines) - 2 * len(separators), 0) if separators else 0


# The edits `edit_chapter_draft` takes, and how much of a block's first line its numbered list shows (DEFECTS 584)
_EDIT_ACTIONS = ("replace", "remove", "add_after", "cite")
_ONE_ENTRY_ID = re.compile(rf"{document_text_utils.ENTRY_ID_PATTERN}")
_BLOCK_PREVIEW_CHARS = 60


def split_draft_blocks(markdown: str) -> list[str]:
    """A chapter draft's blocks as written, in order, the units `edit_chapter_draft` names by number: a run of lines up
    to a blank line, a heading line alone, and a fenced block whole with the ids line written after it. Joined by blank
    lines they read as the draft."""

    lines = markdown.splitlines()
    blocks: list[str] = []
    run: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_fence_line(line):
            if run:
                blocks.append("\n".join(run))
                run = []
            closing = _find_closing_fence(lines, index)
            end = len(lines) - 1 if closing is None else closing
            block = lines[index:end + 1]
            following = end + 1
            while following < len(lines) and not lines[following].strip():
                following += 1
            # The ids written under a fence are the fence's: an edit of the code keeps them, a remove takes them out; a
            # range with a typographic dash, `[F79.1–F79.4]`, is such a line too (DEFECTS 591)
            if following < len(lines) and _is_ids_line(document_text_utils.write_short_ranges_in_full(lines[following])):
                block.append(lines[following])
                end = following
            blocks.append("\n".join(block))
            index = end + 1
            continue
        if _HEADING.match(line):
            if run:
                blocks.append("\n".join(run))
                run = []
            blocks.append(line)
        elif not line.strip():
            if run:
                blocks.append("\n".join(run))
                run = []
        else:
            run.append(line)
        index += 1
    if run:
        blocks.append("\n".join(run))

    return blocks


def _add_cited_ids(block: str,
                   ids: list[str]) -> str:
    """`block` with the `ids` it does not cite yet added in one bracket at the end of its last line, or as an ids line
    under a fenced block: adding ids by replacing the block, a worker wrote it again from its first line and lost ids
    it already cited (DEFECTS 605)."""

    cited = set(document_text_utils.list_cited_ids(block))
    new = [entry_id for entry_id in dict.fromkeys(ids) if entry_id not in cited]
    if not new:
        return block
    lines = block.splitlines()
    if _is_fence_line(lines[-1]):
        return f"{block}\n[{', '.join(new)}]"

    return f"{block} [{', '.join(new)}]"


def _find_code_written_again(text: str,
                             block: str) -> list[str]:
    """A kept code block's first line when `text` holds a code block of the same lines, else nothing: a code block is one
    sentence, as the chapter check reads it. Compared line by line, two terminal outputs opening with the same prompt and
    banner ("> python sploit.py", "Trying to connect ...") refused every edit of chapter 7 that wrote one of them
    (DEFECTS 656); a longer output starting with a shorter one's lines is another output."""

    code = _list_code_lines(block)
    if not code:
        return []
    written, current = [], None
    for line in text.splitlines():
        if _is_fence_line(line.strip()):
            if current is None:
                current = []
            else:
                written.append([each for each in current if each])
                current = None
        elif current is not None:
            current.append(line.strip())

    return [code[0]] if code in written else []


def _list_code_lines(block: str) -> list[str]:
    """The lines inside a fenced block, stripped, blank ones and its fences and ids line left out."""

    return [line.strip() for line in block.splitlines()[1:] if line.strip() and not _is_fence_line(line.strip())
            and not _is_ids_line(line)]


def _list_kept_sentences_written_again(blocks: list[str],
                                       changed: dict[int, str | None],
                                       written: list[tuple[int, str]]) -> list[str]:
    """Why each written block is refused: it holds a sentence of a block the edits leave as it is. To merge two blocks,
    a worker replaced one with both texts and kept the other, and the next check refused the repeated sentences, twice
    in two chapters (DEFECTS 604)."""

    errors = []
    for position, text in written:
        for number, block in enumerate(blocks, start=1):
            said = [] if number in changed or _HEADING.match(block) else (
                _find_code_written_again(text, block) if _is_fence_line(block.splitlines()[0].strip())
                else document_text_utils.find_sentences_said_earlier(text, block))
            if said:
                errors.append(f"edit {position}: it writes again what block {number} says ('{said[0][:80]}'), and "
                              f"block {number} stays; remove block {number} in the same edits, or leave its text out")

    return errors


def apply_block_edits(markdown: str,
                      edits: list[dict]) -> tuple[str, list[str]]:
    """`markdown` with `edits` applied, each `{"action", "block", "markdown"}` naming a block of `split_draft_blocks` by
    its number from 1 (0 with add_after: before the first block), a cite `{"action", "block", "ids"}`, and the reasons
    any edit could not be applied; with a reason, the draft is returned as it was, so a wrong number never changes a
    block it did not mean. A block written that holds a sentence of a block the edits keep is refused by name."""

    blocks = split_draft_blocks(markdown)
    errors: list[str] = []
    changed: dict[int, str | None] = {}
    added: dict[int, list[str]] = {}
    written: list[tuple[int, str]] = []
    for position, edit in enumerate(edits, start=1):
        action, number = str(edit.get("action") or ""), edit.get("block")
        text = str(edit.get("markdown") or "").strip()
        lowest = 0 if action == "add_after" else 1
        if action not in _EDIT_ACTIONS:
            errors.append(f"edit {position}: the action is {action!r}; it is one of {', '.join(_EDIT_ACTIONS)}")
        elif not isinstance(number, int) or not lowest <= number <= len(blocks):
            errors.append(f"edit {position}: there is no block {number}; the draft has blocks 1 to {len(blocks)}")
        elif action == "cite":
            ids = [str(entry_id).strip() for entry_id in edit.get("ids") or []]
            unread = [entry_id for entry_id in ids if not _ONE_ENTRY_ID.fullmatch(entry_id)]
            if not ids or unread:
                errors.append(f"edit {position}: cite needs the note ids to add, each like F12.3 or S0415"
                              + (f"; not ids: {', '.join(unread)}" if unread else ""))
            elif _HEADING.match(blocks[number - 1]):
                errors.append(f"edit {position}: block {number} is a heading, which cites nothing; cite the ids in a "
                              "block under it")
            elif number in changed:
                errors.append(f"edit {position}: block {number} is already changed by an earlier edit")
            else:
                changed[number] = _add_cited_ids(blocks[number - 1], ids)
        elif action != "remove" and not text:
            errors.append(f"edit {position}: {action} needs the block's markdown")
        elif action == "add_after":
            added.setdefault(number, []).append(text)
            written.append((position, text))
        elif number in changed:
            errors.append(f"edit {position}: block {number} is already changed by an earlier edit")
        else:
            changed[number] = None if action == "remove" else text
            written += [] if action == "remove" else [(position, text)]
    errors += [] if errors else _list_kept_sentences_written_again(blocks, changed, written)
    if errors:
        return markdown, errors
    kept = list(added.get(0, []))
    for number, block in enumerate(blocks, start=1):
        replacement = changed.get(number, block)
        if replacement is not None:
            kept.append(replacement)
        kept += added.get(number, [])

    return "\n\n".join(kept), []


# A finding's quote of the draft, in single or double quotes, at least 8 characters
_QUOTED_IN_FINDING = re.compile(r"""'([^'\n]{8,})'|"([^"\n]{8,})\"""")


def name_quoted_blocks(detail: str,
                       markdown: str) -> str:
    """`detail` with each text it quotes from the draft followed by the number of the block holding it, "(block 19)":
    a finding quoted the call tags at the end of a set-aside line, which the numbered list shows only by its first
    line, and the worker looked for them in another block (DEFECTS 593). Unchanged when no quote is found in a block."""

    blocks = split_draft_blocks(markdown)

    def _name(match: re.Match) -> str:
        # The quote's longest piece between its line breaks and the "…" that ends a cut quote
        pieces = re.split(r"\\n|\n|…", match.group(1) or match.group(2))
        quoted = max((piece.strip() for piece in pieces), key=len)
        number = next((number for number, block in enumerate(blocks, start=1) if quoted.casefold() in block.casefold()),
                      None) if len(quoted) >= 8 else None

        return f"{match.group(0)} (block {number})" if number else match.group(0)

    return _QUOTED_IN_FINDING.sub(_name, detail)


def render_numbered_blocks(markdown: str) -> str:
    """One line per block of the draft, `block N: "<its first line>" [<the ids it cites>]`, the numbers
    `edit_chapter_draft` takes. The ids are listed because a code block shows only its fence line: a worker replacing
    five code blocks wrote new ids lines under them and dropped the ids they had (DEFECTS 597)."""

    lines = []
    for number, block in enumerate(split_draft_blocks(markdown), start=1):
        first = block.splitlines()[0].strip()
        preview = first if len(first) <= _BLOCK_PREVIEW_CHARS else first[:_BLOCK_PREVIEW_CHARS].rstrip() + " …"
        cited = document_text_utils.list_cited_ids(block)
        lines.append(f'block {number}: "{preview}"' + (f" [{', '.join(cited)}]" if cited else "")
                     + _describe_open_fence(block))

    return "\n".join(lines)


def _describe_open_fence(block: str) -> str:
    """For a code block left open, what it swallowed: its fence never closed, it runs to the draft's end, and the
    blocks written after it have no numbers. A writer edited blocks 14 to 23 of a draft listed as 12, six edits
    refused in a row (DEFECTS 653). Empty for any other block."""

    block_lines = block.splitlines()
    fences = [line for line in block_lines if _is_fence_line(line.strip())]
    if not _is_fence_line(block_lines[0].strip()) or len(fences) % 2 == 0:
        return ""
    headings = [line.strip() for line in block_lines if _LISTED_HEADING.match(line.strip())]

    return (f" (a code block never closed: it runs to the draft's end, {len(block_lines)} lines"
            + (f", holding {'; '.join(headings)}" if headings else "")
            + "; replace it with its code closed by a line of three backticks, then the text you wrote after it)")

