"""A PDF read as a recording: read as pages, each page is a frame and its cleaned text is what
is "said" over it; read as text only, its blocks are the paragraphs and each figure is a frame of its own
(`find_figures`, `render_figures`). A page stands at its own minute of a timeline the document does not have
(`PAGE_SECONDS`), so every phase that reads seconds reads a page's position unchanged.

The text of a page comes from pymupdf4llm, which reads a page that holds its words only as pixels with the run's scan
engine (`OCR_ENGINES`). What is no knowledge is taken out before any model sees it, each removal written
with its rule (`01_cleanup.json`): a block at the same place on most pages and a line standing on most pages (a wiki's
breadcrumb and page tree, a print footer, a deck's footer), the export chrome of agent-context/pdf/chrome-lines.txt, a
picture's text of a few words (and every picture's text on a page whose pictures are figures), image placeholders,
the document's own table of contents and its back matter, and a link into the file itself kept as its text. A link to
a page outside the file keeps its address after its text, which pymupdf4llm drops.
"""

from __future__ import annotations

import collections
import difflib
import functools
import json
import re
import logging
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path

import cv2
import numpy
import pymupdf
import pymupdf4llm

import config
from models import output_contract
from services import frames_preparing_progress_service as progress_service
from services import unlimited_ocr_reader_service
from utils import json_store_utils
from utils import native_stderr_utils
from utils import unlimited_ocr_block_utils

__all__ = ["PAGE_SECONDS", "OCR_ENGINES", "TESSERACT_ENGINE",
           "UNLIMITED_OCR_ENGINE", "is_pdf", "count_pages",
           "read_chosen_ocr_engine", "write_chosen_ocr_engine", "is_scanned_page",
           "build_page_transcript", "render_pages", "read_page_image", "build_page_slices",
           "mark_repeated_pages", "find_figures", "render_figures", "split_figures_to_read", "read_figure_image"]

TESSERACT_ENGINE = "tesseract"
UNLIMITED_OCR_ENGINE = "unlimited-ocr"
# what reads a scanned page: pdf.ocrEngine, run --pdf-ocr-engine
OCR_ENGINES = (TESSERACT_ENGINE, UNLIMITED_OCR_ENGINE)
PICTURE_FIGURE = "picture"                     # a figure's `kind`: it holds a picture (a screenshot, a photo)
DRAWING_FIGURE = "drawing"                     # a figure's `kind`: lines and shapes alone (a chart, a diagram)
# which figures phase 3 reads (`pdf.figureReading`); the others are placed unread where they stood
READ_ALL_FIGURES = "read-all"
PLACE_ABOVE_LIMIT = "place-above-limit"
READ_DRAWINGS_ONLY = "read-drawings-only"
_OCR_CHROME_RULE = "Unlimited-OCR-2: a page's header, footer or page number"

PAGE_SECONDS = output_contract.PDF_PAGE_SECONDS  # a page's place on the document's timeline: page 3 is minute 2
_REPEATED_LINE_SHARE = 0.6                      # a line on this share of pages or more is chrome, never content
_SAME_LINE_RATIO = 0.8                          # two lines this alike are one line read twice (OCR misreads letters)
_MIN_PAGES_FOR_REPEATS = 3                      # a document this short repeats nothing worth a rule
_FRAME_WIDTH = 1600                             # a page as phase 3 sees it
_FULL_DPI = 200                                 # a page as the cut tool reads it
_DIGITS = re.compile(r"\d+")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_INTERNAL_LINK = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")
_BLANK_RUN = re.compile(r"\n{3,}")
# Bold marks and the HTML tags the reader writes into a line (an underline, a superscript an OCR read around a word: a
# scan reads chrome as lower-case junk, "te<sup>Like</sup>")
_MARKS = re.compile(r"\*+|</?(?:u|sup|sub|b|i|em|strong)>")
# A numbered section heading the reader marked as one ("## **5.1. Title**"), and a whole bold line numbered so
_NUMBERED_HEADING = re.compile(r"^(#+)\s*\**\s*\d+(?:\.\d+)*\.?\s+\S", re.M)
_BOLD_NUMBERED_LINE = re.compile(r"^\*\*\s*\d+(?:\.\d+)*\.?\s+[^*\n]{1,120}\*\*\s*$", re.M)
# The marks the reader writes around highlighted, underlined and struck text, whose words stay: a title reached phase
# 4 as "# <mark>…</mark>", and every link of a web page as "<u>…</u>" with a struck "~~:~~"
_HIGHLIGHT = re.compile(r"</?(?:mark|u)>|~~")
_CHROME_FILE = Path("pdf") / "chrome-lines.txt"
_CHROME_RULE = "export chrome: {}"              # the cleanup rule a chrome pattern's removal is written under
# A sentence ends at ".", "!" or "?" and a space: the reader joins an author line to the paragraph above it
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_PICTURE_TEXT = re.compile(r"<!-- Start of picture text -->(.*?)<!-- End of picture text -->", re.DOTALL)
# a picture whose text has this many words or fewer is a badge or a button
_DECORATION_WORDS = 3
_PLACE_POINTS = 4                               # two blocks within this many points of each other stand at one place
_POINTS_PER_INCH = 72                           # a PDF's unit: a page's size in points, its pixels at a DPI from them
# pixels at _FULL_DPI per point: the cut tool's box of a page or a figure
_FULL_DPI_SCALE = _FULL_DPI / _POINTS_PER_INCH
_FRAME_JPEG_QUALITY = 92                        # a page or figure written as a frame: the cut tool reads the page again
_TEXT_BLOCK = 0                                 # pymupdf's block type for text, beside 1 for an image
# pymupdf's render mode of text drawn with no ink: a searchable scan's layer
_INVISIBLE_TEXT = 3
_BOX_SLACK = 1                                  # a word or a path this close to a box's edge counts as inside it
_CAPTION_SIDE_SLACK = 4                         # a caption line may stand this far past its figure's side
_CAPTION_MATCH_CHARS = 40                       # a caption's opening this long finds the block that holds it
# a figure stands this far before the block it precedes, so it sorts first
_FIGURE_LEAD_SECONDS = 0.01
# the blocks of its page before a figure its notes are told of: what it is for
_FIGURE_BLOCKS_BEFORE = 2
# A figure: a picture or a drawing of _FIGURE_MIN_SHARE of the page or more and at
# least _FIGURE_MIN_SIDE points each way. A picture over _FIGURE_MAX_SHARE is a scanned page. A drawing needs
# _FIGURE_MIN_PATHS lines or shapes (a path's items) and words over less than _FIGURE_MAX_INK of it: a code block's
# background is 2 paths, a coloured box of text is 70% words, while a timeline chart drew 49 paths under 17% words
_FIGURE_MIN_SHARE = 0.01
_FIGURE_MAX_SHARE = 0.9
_FIGURE_MIN_SIDE = 40
_FIGURE_MIN_PATHS = 8
_FIGURE_MAX_INK = 0.5
# a drawing this much inside a table found on the page is the table's lines
_TABLE_SHARE = 0.5
# A drawing made of boxes alone with _BOXED_TEXT_SHARE of its words (by their area) inside its filled boxes is text set in boxes, never a
# figure: a table the table finder missed (a wiki export's rounded cells were 695 figures of one PDF) or a code block
# with a box behind each line number, while a chart's or a timeline's words stand at most 58% inside its boxes (DEFECTS 667)
_BOXED_TEXT_SHARE = 0.9
# and its filled boxes cover _BOXED_TEXT_COVER of it, as a table's touching cells do, where a flowchart's labelled
# nodes stand apart (a timeline's blocks covered 33% to 44%)
_BOXED_TEXT_COVER = 0.6
_FRAMED_PICTURE_SHARE = 0.5                    # a picture filling this much of a drawing it stands in is framed by it (a wiki's: 83%)
_ROUNDED_CORNER = 8                            # a curve no larger than this many points each way is a box's rounded corner
_BOX_ICON_SIDE = 16                            # a path no larger than this each way is an icon in a cell (a tick, a mark)
_BOX_MIN_AREA = 4                              # a filled shape smaller than this many square points is a dot, no box
# A picture is blank when everything off its one colour fits in a box of under _BLANK_PICTURE_SPREAD of it: a wiki's grey
# file placeholder (3.1%) or a white picture (0% to 3.2%), where every public figure needs 48% or more (DEFECTS 667)
_BLANK_PICTURE_SPREAD = 0.05
_BLANK_PICTURE_LEVELS = 12                     # a pixel this many grey levels off the picture's median is off its colour
_BLANK_PICTURE_DPI = 72
_HELD_PICTURE_SHARE = 0.5                       # a frame holds a picture when it covers more than this share of it
# a caption line starts this close under its figure (a chart's axis labels between)
_CAPTION_GAP = 48
# a drawing's label stands this close to its lines: an axis's months under it
_LABEL_GAP = 14
_PAGE_BREAK_SHARE = 0.06                        # a figure ending this near a page's bottom, or starting this near its top, may run over the break
_PAGE_BREAK_COLUMNS = 0.9                       # the parts of a figure over a page break share this much of the narrower's width
_BLANK_ROW_LEVEL = 245                          # a row of a figure's part this light all across its inside is page left white
_BLANK_ROW_SIDE = 0.03                          # the share of a part's width at each side its frame's lines may stand in
_LABEL_MAX_WORDS = 6                            # a line of this many words or more is running text, never a drawing's label
_JOIN_GAP = 2                                   # pictures this close are one, as an exporter stores a picture in strips
# a page-size picture with this few words written on it is a poster, not a scan
_POSTER_MAX_WORDS = 60
# A line that is an item of a list: a bullet or a number with a dot or a bracket
_LIST_ITEM = re.compile(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+\S")
# The heading of a document's own table of contents, its marks aside: "Contents", "Table of Contents"
_CONTENTS_HEADING = re.compile(r"^#+\s*[*_]*\s*(?:table\s+of\s+)?contents\s*[*_]*\s*$", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]*>")              # an underline or a superscript the reader wrote around a word
_LEADING_NUMBER = re.compile(r"^[\d.\s]+")      # a heading's section number, "7.1 "
_BLANK_LINE = re.compile(r"\n\s*\n")            # where one block of a page's text ends and the next begins
_HEADING_LINE = re.compile(r"^(#+)\s*[*_]*\s*(.*?)\s*[*_]*\s*$")
# A document's back matter: its sources and pointers out, which teach nothing of the subject and which a writer copied
# entry by entry. Kept, a Wikipedia article's References and Notes, 11 pages, held one chapter on dry checks for two
# hours (pdf-real-wikipedia-photosynthesis)
_BACK_MATTER_TITLES = re.compile(r"(?:references|bibliography|further reading|external links|see also|sources|"
                                 r"citations|works cited|literature|literatur|quellen|einzelnachweise|weblinks)",
                                 re.IGNORECASE)
# A notes section is back matter only when it is a numbered or bulleted list: a manual's notes can be its content
_NOTES_TITLES = re.compile(r"(?:notes|footnotes|endnotes|anmerkungen|fußnoten)", re.IGNORECASE)
_NUMBERED_OR_BULLET = re.compile(r"^\s*(?:\d{1,3}[.)]|[-*+\u2022^])\s+\S")
_BACK_MATTER_RULE = "the document's back matter (its sources and links out)"
# A numbered pointer into the back matter, "[42]", "[9,10]", "[3–5]", "[note 1]", never a link's text ("[1](…)") nor a
# named reference ("[RFC9110]"): once its list is taken out it points at nothing, and a writer copied one into a
# chapter (wiki run 2: 264 such markers in the article's text)
_NOTE_MARKER = re.compile(r"\[(?:note\s+)?\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\](?!\()", re.IGNORECASE)
# Two pages this close at 64 pixels wide (mean grey levels) look the same; their text must match as well
_SAME_PAGE_DIFFERENCE = 3.0
_COMPARE_WIDTH = 64
_CAPTION_LEAD = re.compile(r"^\s*(?:fig(?:ure)?\.?|table|chart|diagram|abb(?:ildung)?\.?)\s*\d", re.IGNORECASE)

_LOG = logging.getLogger(__name__)


def _record_removal(removed: list[dict],
                    page_number: int,
                    rule: str,
                    text: str) -> None:
    """One entry of `01_cleanup.json`: what was taken out of which page, and by which rule."""

    removed.append({"page": page_number, "rule": rule, "text": text})


def _normalise_line(line: str) -> str:
    """The line masked as lines are compared across pages: bold marks and spacing aside, case folded and every digit
    run as "#", so a page number does not tell two lines apart."""

    return _DIGITS.sub("#", " ".join(line.replace("*", "").split())).casefold()


@lru_cache(maxsize=1)
def _read_chrome_patterns() -> tuple[re.Pattern, ...]:
    """The export chrome's lines, as data in agent-context/pdf/chrome-lines.txt, read once a process: every page is
    cleaned with them."""

    lines = (config.AGENT_CONTEXT_DIR / _CHROME_FILE).read_text(encoding="utf-8").splitlines()

    return tuple(re.compile(line.strip(), re.IGNORECASE) for line in lines
                 if line.strip() and not line.strip().startswith("#"))


def _list_repeated_lines(pages: list[str]) -> list[str]:
    """The masked lines that stand on _REPEATED_LINE_SHARE of the pages or more, a line read with a letter or two
    different on another page (a scanned page's OCR) counting as the same line."""

    if len(pages) < _MIN_PAGES_FOR_REPEATS:
        return []
    page_lines = [{_normalise_line(line) for line in page.splitlines() if line.strip()} for page in pages]
    repeated = []
    for line in {line for lines in page_lines for line in lines}:
        pages_holding = sum(1 for lines in page_lines if line in lines or any(_is_same_line(line, other) for other in lines))
        if pages_holding >= _REPEATED_LINE_SHARE * len(pages):
            repeated.append(line)

    return repeated


def _is_same_line(first: str,
                  second: str) -> bool:
    return abs(len(first) - len(second)) <= len(first) * (1 - _SAME_LINE_RATIO) and (
        difflib.SequenceMatcher(a=first, b=second).ratio() >= _SAME_LINE_RATIO)


def _is_repeated(line: str,
                 repeated: list[str]) -> bool:
    masked = _normalise_line(line)

    return masked in repeated or any(_is_same_line(masked, other) for other in repeated)


def _list_outside_links(page: pymupdf.Page) -> list[tuple[str, str]]:
    """`(text, address)` of each link on the page to a page outside the file."""

    links = []
    for link in page.get_links():
        address = str(link.get("uri") or "")
        if address.startswith(("http://", "https://", "mailto:")):
            links.append((" ".join(page.get_textbox(link["from"]).split()), address))

    return links


def _add_link_addresses(text: str,
                        links: list[tuple[str, str]]) -> str:
    """The page's text with each link to an outside page written `text <address>` once, where its text first stands
    outside a heading: a heading's link kept its address in the heading, which the agenda and the headings carried."""

    for label, address in links:
        # The label as a word of its own: "here" inside "Somewhere" is not the link
        whole_label = re.compile(rf"(?<!\w){re.escape(label)}(?!\w)") if label else None
        if whole_label is None or address in text:
            continue
        for match in whole_label.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            if not text[line_start:].lstrip().startswith("#"):
                text = f"{text[:match.end()]} <{address}>{text[match.end():]}"
                break

    return text


def _read_picture_texts(text: str,
                        page_number: int,
                        removed: list[dict],
                        is_read_as_figures: bool = False) -> str:
    """The page's text with each picture's OCR text kept as plain lines, or taken out when it is a few words, the
    label of a badge or a button: a status lozenge's one word was read as a block of the page. On a page whose
    pictures are figures read as frames of their own (`is_read_as_figures`), every picture's text is taken out: the
    figure's notes read it from the picture, and as text a cover's artwork was a paragraph of misread letters and a
    logo's lettering a sentence run together (pdf-figures-NIST.CSWP.29)."""

    def _replace(match: re.Match) -> str:
        words = " ".join(match.group(1).replace("<br>", " ").split())
        if is_read_as_figures:
            _record_removal(removed, page_number, "a figure's text, read with the figure", words)
            return ""
        if len(words.split()) <= _DECORATION_WORDS:
            _record_removal(removed, page_number, "a picture's text of a few words", words)
            return ""
        return match.group(1).replace("<br>", "\n").strip()

    return _PICTURE_TEXT.sub(_replace, text)


def _find_chrome_pattern(text: str,
                         chrome: tuple[re.Pattern, ...]) -> str | None:
    """The chrome pattern `text` is whole, its marks and spacing aside; None when it is none, or empty. A heading keeps
    its `#`, so only a pattern written for a heading's form takes one: the document's own "# Attachments" heading is
    no attachments panel (DEFECTS 522)."""

    bare = " ".join(_MARKS.sub("", text).split())

    return next((pattern.pattern for pattern in chrome if pattern.fullmatch(bare)), None) if bare else None


def _drop_chrome_sentences(line: str,
                           chrome: tuple[re.Pattern, ...],
                           page_number: int,
                           removed: list[dict]) -> str:
    """The line with each sentence a chrome pattern matches whole taken out; a table row is left as it stands. The
    reader joined an author line to the paragraph above it, and a pattern read against the whole line missed it."""

    if line.lstrip().startswith("|"):
        return line
    sentences = _SENTENCE_BREAK.split(line)
    kept = []
    for sentence in sentences:
        pattern = _find_chrome_pattern(sentence, chrome)
        if pattern is None:
            kept.append(sentence)
        else:
            _record_removal(removed, page_number, _CHROME_RULE.format(pattern), sentence.strip())

    return " ".join(kept) if len(kept) < len(sentences) else line


# A heading line's marks, and in its text what only an equation holds: a reaction's arrow or a spaced plus.
_HEADING_MARKS = re.compile(r"^\s*#{1,6}\s+(.+)$")
_EQUATION_SIGN = re.compile(r"[→⇌⟶]| \+ ")


def _clean_page(text: str,
                repeated: list[str],
                page_number: int,
                removed: list[dict],
                is_read_as_figures: bool = False) -> str:
    """The page's text with every line of no knowledge taken out, each removal appended to `removed`; with
    `is_read_as_figures`, the page's pictures are read as figures, and their text is theirs."""

    text = _read_picture_texts(text, page_number, removed, is_read_as_figures)
    chrome = _read_chrome_patterns()
    kept = []
    for line in _HIGHLIGHT.sub("", text).splitlines():
        # A heading is never chrome: a wiki page's title stood in its page tree on every page as well. Nor is a table's
        # line: a table broken over pages repeats its header and its separator row on each, and with the separator
        # gone the rows were no table (pdf-02)
        is_heading_or_table = line.lstrip().startswith(("#", "|"))
        if line.strip() and not is_heading_or_table and _is_repeated(line, repeated):
            _record_removal(removed, page_number, "repeated on most pages", line.strip())
            continue
        pattern = _find_chrome_pattern(line, chrome)
        if pattern is not None:
            _record_removal(removed, page_number, _CHROME_RULE.format(pattern), line.strip())
            continue
        line = _drop_chrome_sentences(line, chrome, page_number, removed)
        # Each placeholder and each link is recorded alone: the rest of its line is kept
        for placeholder in _IMAGE.findall(line):
            _record_removal(removed, page_number, "image placeholder", placeholder)
        without_images = _IMAGE.sub("", line)
        for link in _INTERNAL_LINK.finditer(without_images):
            _record_removal(removed, page_number, "link inside the file kept as its text", link.group(0))
        plain = _INTERNAL_LINK.sub(r"\1", without_images)
        # A reaction or a sum set in a large font is read as a heading: Wikipedia's "2 glycolate + ATP → …" became a
        # section of the agenda. Over 29 PDF runs an arrow or a spaced plus marked that line alone
        equation = _HEADING_MARKS.match(plain)
        if equation and _EQUATION_SIGN.search(equation.group(1)):
            plain = equation.group(1).replace("**", "")
        kept.append(plain.rstrip())

    return _BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()


def _list_table_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _list_spilled_cells(header: str,
                        last_header: str) -> list[str] | None:
    """The text a row broken over pages left in the next page's repeated header, a cell each (empty where none), when
    every cell of `header` starts with the same cell of `last_header` and one holds more; None otherwise. The reader
    joined the end of the last row's cell to the header printed again: "...|**Cybersecurity Risk Management** The
    organization risk strategy is informed by ...|" (NIST CSWP 29, Table 2, DEFECTS 534)."""

    cells, earlier = _list_table_cells(header), _list_table_cells(last_header)
    if len(cells) != len(earlier) or not all(cell.startswith(cell_before) for cell, cell_before in zip(cells, earlier)):
        return None
    spilled = [cell[len(cell_before):].strip() for cell, cell_before in zip(cells, earlier)]

    return spilled if any(spilled) else None


def _restore_spilled_header(block: str,
                            last_header: str,
                            spilled: list[str]) -> str:
    """The table `block` with its header put back to `last_header` and the spilled text as its first row, under the
    separator."""

    lines = block.splitlines()
    row = "| " + " | ".join(spilled) + " |"

    return "\n".join([last_header, *lines[1:2], row, *lines[2:]])


def _mark_continued_tables(blocks: list[str],
                           last_header: str | None,
                           page_number: int) -> tuple[list[str], str | None]:
    """The page's blocks with a table that opens the page under the header of the last table before it marked as
    continuing that table (a table run over pages, its header printed again), and the header of the table that ends
    the page, None when another block ends it. Every cell's line break (`<br>`) is read as a space, so a header
    wrapped on one page matches it unwrapped on the next, and a body cell reads as one line."""

    marked = []
    for place, block in enumerate(blocks):
        # Any other block ends the table before it: only a table ending its page runs on to the next
        if not block.startswith("|"):
            marked.append(block)
            last_header = None
            continue
        block = block.replace("<br>", " ")
        header = block.splitlines()[0]
        spilled = _list_spilled_cells(header, last_header) if place == 0 and last_header else None
        if spilled:
            block = _restore_spilled_header(block, last_header, spilled)
            block = (f"(The table continues the one on page {page_number - 1}, the same columns; its first row ends "
                     f"that page's last row.)\n{block}")
            header = last_header
        elif place == 0 and header == last_header:
            block = f"(The table continues the one on page {page_number - 1}, the same columns.)\n{block}"
        last_header = header
        marked.append(block)
    # A page with no block keeps nothing running on: the next page starts after it
    if not blocks:
        last_header = None

    return marked, last_header


def _read_back_matter_level(block: str) -> int:
    """The heading level of a block opening a back-matter section, or 0 when it opens none."""

    lines = block.splitlines()
    match = _HEADING_LINE.match(lines[0]) if lines else None
    if not match:
        return 0
    unmarked = _HTML_TAG.sub("", match.group(2))
    title = _LEADING_NUMBER.sub("", unmarked).strip(" *_:")
    is_notes_list = bool(_NOTES_TITLES.fullmatch(title) and len(lines) > 1 and _NUMBERED_OR_BULLET.match(lines[1]))
    if _BACK_MATTER_TITLES.fullmatch(title) or is_notes_list:
        return len(match.group(1))

    return 0


def _drop_back_matter(blocks: list[str],
                      level: int,
                      page_number: int,
                      removed: list[dict]) -> tuple[list[str], int]:
    """The page's blocks with the document's back matter taken out: from a References, Further reading, External links
    or See also heading (a Notes heading opening a list) to the next heading of its level or higher that opens none,
    onto the next pages too; each removal appended to `removed`. The level of the back matter running on past the
    page's last block is returned for the next page, 0 when none does."""

    kept = []
    for block in blocks:
        heading = _HEADING_LINE.match(block.splitlines()[0]) if block else None
        opened = _read_back_matter_level(block)
        is_back_matter_opened = bool(opened) and (not level or opened <= level)
        is_back_matter_closed = bool(heading and level and not opened and len(heading.group(1)) <= level)
        if is_back_matter_opened:
            level = opened
        elif is_back_matter_closed:
            level = 0
        if level:
            _record_removal(removed, page_number, _BACK_MATTER_RULE, block.strip())
        else:
            kept.append(block)

    return kept, level


_PAGE_NUMBER_CELL = re.compile(r"\|\s*\d+\s*\|\s*$")   # a contents row ends on its page number: "| 3. Terms | 5 |"
_SEPARATOR_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# a contents line written as text: a title, then its page number, dot leaders allowed: "SUMMARY 1", "Scope ..... 3"
_CONTENTS_LINE = re.compile(r"^\s*[^|\s].*[A-Za-z].*?[\s.…]\d{1,4}\s*$")
_CONTENTS_LINE_MAX_WORDS = 12


def _is_contents_entries(block: str) -> bool:
    """Whether a block running on after the contents is more of them: a table whose every row after its header ends on
    a page number, or lines that are each a short title ending on one. A revision-history table opening the next page
    was dropped as contents; a scan's contents read one line a block ("SUMMARY 1") were kept (DEFECTS 494)."""

    lines = [line for line in block.splitlines() if line.strip()]
    if not lines:
        return False
    if lines[0].lstrip().startswith("|"):
        rows = [line for line in lines[1:] if not _SEPARATOR_ROW.match(line)] or lines[:1]

        return all(_PAGE_NUMBER_CELL.search(row) for row in rows)

    return all(_CONTENTS_LINE.match(line) and len(line.split()) <= _CONTENTS_LINE_MAX_WORDS for line in lines)


def _drop_contents_blocks(blocks: list[str],
                          is_in_contents: bool,
                          page_number: int,
                          removed: list[dict]) -> tuple[list[str], bool]:
    """The page's blocks with the document's own table of contents taken out: the block its heading opens and the table
    blocks that run on after it, onto the next page too; each removal appended to `removed`. Whether the contents run
    on past the page's last block is returned for the next page. Kept, RFC 9457's and a NACA report's contents became a
    chapter of headings and page numbers (pdf-real-rfc9457, pdf-real-nasa-scan-12p)."""

    kept = []
    for block in blocks:
        first = block.splitlines()[0] if block else ""
        is_contents = bool(_CONTENTS_HEADING.match(first)) or (is_in_contents and _is_contents_entries(block))
        if is_contents:
            _record_removal(removed, page_number, "the document's own table of contents", block.strip())
        else:
            kept.append(block)
        is_in_contents = is_contents

    return kept, is_in_contents


def _build_widened_box(box: pymupdf.Rect,
                       gap: float) -> pymupdf.Rect:
    return box + (-gap, -gap, gap, gap)


def _is_figure_size(box: pymupdf.Rect,
                    page: pymupdf.Page,
                    largest_share: float) -> bool:
    share = box.get_area() / page.rect.get_area()

    return _FIGURE_MIN_SHARE <= share <= largest_share and min(box.width, box.height) >= _FIGURE_MIN_SIDE


def _is_picture_in(picture: pymupdf.Rect,
                   box: pymupdf.Rect) -> bool:
    """Does the figure at `box` hold the picture, more than half of it inside?"""

    return (picture & box).get_area() > _HELD_PICTURE_SHARE * picture.get_area()


def _is_blank_picture(page: pymupdf.Page,
                      box: pymupdf.Rect) -> bool:
    """Is the picture at `box` blank: one colour, or one colour round a lone small icon (a file placeholder)?"""

    pixmap = page.get_pixmap(clip=box, dpi=_BLANK_PICTURE_DPI, colorspace=pymupdf.csGRAY)
    grey_array = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width).astype(int)
    off_array = numpy.abs(grey_array - numpy.median(grey_array)) > _BLANK_PICTURE_LEVELS
    if not off_array.any():
        return True
    rows, columns = numpy.nonzero(off_array)
    spread = (numpy.ptp(rows) + 1) * (numpy.ptp(columns) + 1) / off_array.size

    return spread < _BLANK_PICTURE_SPREAD


def _is_box_path(path: dict) -> bool:
    """Is this drawn path a box: rectangles, straight lines along the page's axes, and curves no larger than a rounded
    corner?"""

    for item in path["items"]:
        if item[0] == "l" and min(abs(item[1].x - item[2].x), abs(item[1].y - item[2].y)) > 0.5:
            return False
        if item[0] == "c":
            span = pymupdf.Rect(item[1], item[4]).normalize()
            if max(span.width, span.height) > _ROUNDED_CORNER:
                return False
        if item[0] not in ("re", "qu", "l", "c"):
            return False

    return True


def _is_boxed_text(paths: list[dict],
                   words: list[pymupdf.Rect],
                   box: pymupdf.Rect) -> bool:
    """Is this drawing text set in boxes (a table, a code block): its paths boxes or icons, its filled boxes covering
    most of it, and nearly all its words inside them?"""

    # 1. every path a box, or an icon small enough to stand in a cell
    is_icon_or_box = [_is_box_path(path) or max(path["rect"].width, path["rect"].height) <= _BOX_ICON_SIDE
                      for path in paths]
    if not all(is_icon_or_box):
        return False

    # 2. the filled boxes cover most of the drawing, as touching cells do
    filled = [pymupdf.Rect(path["rect"]) & box for path in paths
              if "f" in path["type"] and pymupdf.Rect(path["rect"]).get_area() > _BOX_MIN_AREA]
    covered = numpy.zeros((int(box.height) + 1, int(box.width) + 1), dtype=bool)
    for each in filled:
        covered[int(each.y0 - box.y0):int(each.y1 - box.y0), int(each.x0 - box.x0):int(each.x1 - box.x0)] = True
    if covered.mean() < _BOXED_TEXT_COVER:
        return False

    # 3. nearly all of its words, by their area, stand inside a filled box: the comma between two listed links is no label
    own = [word for word in words if word.intersects(box)]
    if not own:
        return False
    boxed = [word for word in own if any((word & each).get_area() > 0.5 * word.get_area() for each in filled)]
    boxed_area = sum(word.get_area() for word in boxed)
    own_area = sum(word.get_area() for word in own)

    return boxed_area >= _BOXED_TEXT_SHARE * own_area


def _list_drawn_figures(page: pymupdf.Page,
                        pictures: list[pymupdf.Rect],
                        blank_pictures: list[pymupdf.Rect],
                        words: list[pymupdf.Rect],
                        labels: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """The page's drawings that are figures, and the frames drawn round a picture (a wiki's picture and its caption in
    one box), which widen that picture; never a table, a code block or a blank picture's frame."""

    tables = [pymupdf.Rect(table.bbox) for table in page.find_tables().tables]
    paths = page.get_drawings()
    drawn = []
    for box in page.cluster_drawings():
        if not _is_figure_size(box, page, _FIGURE_MAX_SHARE):
            continue
        if any((box & table).get_area() > _TABLE_SHARE * box.get_area() for table in tables):
            continue
        inside = [path for path in paths if pymupdf.Rect(path["rect"]) in _build_widened_box(box, _BOX_SLACK)]
        held = [picture for picture in pictures if (box & picture).get_area() > _HELD_PICTURE_SHARE * picture.get_area()]
        # A table or a code block the table finder missed is no figure, nor the frame of a picture it holds: a table
        # holding one small screenshot was taken whole as the screenshot's frame. A picture filling its frame keeps it
        held_area = sum((box & picture).get_area() for picture in held)
        is_framed_picture = len(held) == 1 and held_area >= _FRAMED_PICTURE_SHARE * box.get_area()
        if not is_framed_picture and _is_boxed_text(paths=inside, words=words, box=box):
            continue
        # the frame of a blank picture is the blank's: a file placeholder's rounded box passed as a drawing alone
        if any((box & blank).get_area() > _HELD_PICTURE_SHARE * blank.get_area() for blank in blank_pictures):
            continue
        # A frame round one picture widens it with its caption; round several it is a table's lines or a grid of
        # headshots, and each picture stands alone (a directory's three headshots were one figure captioned by its names)
        if len(held) == 1:
            drawn.append(box)
            continue
        if held:
            continue
        ink = sum((word & box).get_area() for word in words) / box.get_area()
        if sum(len(path["items"]) for path in inside) >= _FIGURE_MIN_PATHS and ink < _FIGURE_MAX_INK:
            drawn.append(_add_labels(box, labels))

    return drawn


def _add_labels(box: pymupdf.Rect,
                words: list[pymupdf.Rect]) -> pymupdf.Rect:
    """A drawing's box widened to the words standing just outside its lines, its labels: a chart's months stood under
    its axis, outside the lines, and a cut of the lines would have lost them."""

    near = _build_widened_box(box, _LABEL_GAP)
    for word in words:
        if word.intersects(near) and word not in box:
            box = box | word

    return box


def _list_label_words(page_words: list[tuple]) -> list[pymupdf.Rect]:
    """The words that may be a drawing's labels: those of lines under `_LABEL_MAX_WORDS` words. A paragraph's first line
    stood 10 points under a timeline's lines and became part of its figure."""

    line_sizes = collections.Counter((word[5], word[6]) for word in page_words)

    return [pymupdf.Rect(word[:4]) for word in page_words if line_sizes[(word[5], word[6])] < _LABEL_MAX_WORDS]


def _join_overlapping(boxes: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    joined: list[pymupdf.Rect] = []
    for box in sorted(boxes, key=lambda each: (each.y0, each.x0)):
        overlapping = [each for each in joined if each.intersects(_build_widened_box(box, _JOIN_GAP))]
        for each in overlapping:
            joined.remove(each)
            box = box | each
        joined.append(box)

    return sorted(joined, key=lambda each: (each.y0, each.x0))


def _read_caption(page_words: list[tuple],
                  box: pymupdf.Rect,
                  pictures: list[pymupdf.Rect]) -> str:
    """The figure's caption among the page's words (pymupdf's `(x0, y0, x1, y1, text, block, line, word)`): the words
    inside its box outside the pictures it holds (a wiki writes the caption in the picture's frame), else a line under
    it opening "Figure 3" or "Table 2"."""

    # A drawing's own words are its labels, an axis or a box's name, never its caption
    held = [picture for picture in pictures if box.intersects(picture)]
    inside = [word for word in page_words if held and pymupdf.Rect(word[:4]) in _build_widened_box(box, _BOX_SLACK)
              and not any(pymupdf.Rect(word[:4]).intersects(picture) for picture in held)]
    if inside:
        return " ".join(str(text) for *_, text, _block, _line, _word in sorted(inside, key=lambda word: word[5:8]))
    # A line under the figure and within its width: two pictures side by side, each with its "Figure N" line, are one
    # text block, and each figure took both captions
    lines: dict[tuple, list] = {}
    for word in page_words:
        x0, y0, x1, _y1, _text, block, line, _word = word
        if (0 <= y0 - box.y1 <= _CAPTION_GAP and x0 >= box.x0 - _CAPTION_SIDE_SLACK
                and x1 <= box.x1 + _CAPTION_SIDE_SLACK):
            lines.setdefault((block, line), []).append(word)
    for line_words in sorted(lines.values(), key=lambda words: min(word[1] for word in words)):
        text = " ".join(str(word[4]) for word in sorted(line_words, key=lambda word: word[0]))
        if _CAPTION_LEAD.match(text):
            return text

    return ""


def _build_caption_key(text: str) -> str:
    """A caption or a block as a caption is found in it: marks and spacing aside and case folded, its digits kept, so
    "Figure 3" never finds the paragraph citing "Figure 1"."""

    return " ".join(text.replace("*", "").split()).casefold()


_CAPTION_RUN_WORDS = 3   # a run of a caption's words this long in a line is the caption, not a word it shares
_CAPTION_PREFIX_LETTERS = 4  # a caption word this long, read with letters run on after it, still matches
_CAPTION_SHARE = 0.8     # a line this much caption is the caption: the wiki's C4 caption, read with words doubled


def _list_caption_words(text: str) -> list[str]:
    """The words of a caption or a line as a caption is matched: link addresses left out, marks (an italic's
    underscores too) and punctuation stripped, case folded."""

    words = [re.sub(r"[\W_]", "", _HIGHLIGHT.sub("", word)).casefold()
             for word in text.split() if not word.startswith("<http")]

    return [word for word in words if word]


def _count_caption_words(line: list[str],
                         caption: list[str]) -> int:
    """How many of the line's words stand in runs of the caption's words, each run at least
    `_CAPTION_RUN_WORDS` long or the whole caption: a caption read with words doubled is still all caption."""

    covered = [False] * len(line)
    shortest = min(_CAPTION_RUN_WORDS, len(caption))
    for start in range(len(line)):
        for offset in range(len(caption)):
            length = 0
            while (start + length < len(line) and offset + length < len(caption)
                   and _is_same_caption_word(line[start + length], caption[offset + length])):
                length += 1
            if length >= shortest:
                covered[start:start + length] = [True] * length

    return sum(covered)


def _is_same_caption_word(word: str,
                           caption_word: str) -> bool:
    """Whether a line's word is the caption's: the same, or a caption word of `_CAPTION_PREFIX_LETTERS` letters or more
    with letters run on after it, as the reader wrote a caption's italic name ("affinegiomnium affineiomnium affine")."""

    return word == caption_word or (len(caption_word) >= _CAPTION_PREFIX_LETTERS and word.startswith(caption_word))


def _is_caption_line(line: str,
                     captions: list[list[str]]) -> bool:
    words = _list_caption_words(line)

    return bool(words) and any(_count_caption_words(words, caption) >= _CAPTION_SHARE * len(words)
                               for caption in captions if caption)


def _drop_caption_blocks(blocks: list[str],
                         figures: list[dict],
                         places: list[int],
                         page_number: int,
                         removed: list[dict]) -> tuple[list[str], list[int]]:
    """The page's blocks without the lines that are a figure's caption, and each figure's place moved with them: the
    caption is said over the figure's frame, and kept in the text as well, the wiki's caption paragraphs were notes a
    writer had to cite twice. A heading stays, and so does a sentence quoting a caption: NIST's text names its
    figures' titles."""

    captions = [_list_caption_words(str(figure.get("caption") or "")) for figure in figures]
    kept: list[str] = []
    dropped: list[int] = []
    for index, block in enumerate(blocks):
        lines = block.splitlines()
        caption_lines = [line for line in lines if not line.lstrip().startswith("#") and _is_caption_line(line, captions)]
        for line in caption_lines:
            _record_removal(removed, page_number, "a figure's caption, said over its figure", line.strip())
        left = [line for line in lines if line not in caption_lines]
        if left:
            kept.append("\n".join(left))
        else:
            dropped.append(index)

    # A figure stands before the same block as before, one place earlier for each block dropped ahead of it
    return kept, [place - sum(1 for index in dropped if index < place) for place in places]


def _list_page_blocks(cleaned: str,
                      page_number: int,
                      removed: list[dict],
                      carried: dict) -> tuple[list[str], dict]:
    """The page's cleaned text as its blocks, its contents and back matter taken out first and only then its tables
    marked where they run on, and what runs on to the next page: `carried`'s `last_header`, `is_in_contents` and
    `back_matter_level`, empty before the first page. Marked first, a contents table over three pages got the
    "continues" line on its third and no longer read as contents."""

    blocks, is_in_contents = _drop_contents_blocks(_split_blocks(cleaned), bool(carried.get("is_in_contents")),
                                                   page_number, removed)
    blocks, back_matter_level = _drop_back_matter(blocks, int(carried.get("back_matter_level") or 0), page_number,
                                                  removed)
    blocks, last_header = _mark_continued_tables(blocks, carried.get("last_header"), page_number)

    return blocks, {"last_header": last_header, "is_in_contents": is_in_contents, "back_matter_level": back_matter_level}


def _find_line_top(line_tops: list[tuple[float, str]],
                   heading_block: str) -> float | None:
    """Where on the page the heading that opens `heading_block` starts, from the page's own text; None when the page's
    text holds no line it starts with."""

    heading = _build_caption_key(_HEADING_LINE.sub(r"\2", heading_block.splitlines()[0]))[:_CAPTION_MATCH_CHARS]
    tops = [top for top, line in line_tops if heading and _build_caption_key(line).startswith(heading)]

    return tops[0] if tops else None


def _list_figure_places(figures: list[dict],
                        blocks: list[str],
                        page_height: float,
                        line_tops: list[tuple[float, str]] = ()) -> list[int]:
    """Where each figure stands among the page's blocks: before the block holding its caption, else at its height on
    the page; and after a heading it would stand before, when that heading starts above the figure's middle (a
    picture under its section's heading, or one the heading is drawn over). Placed before the heading, the life timeline
    of a Wikipedia article fell in the section above it, and a manual's screenshots in the chapter before (wiki run 2,
    05-manual); a cover picture with the title under it stays before."""

    places = []
    for figure in figures:
        caption = _build_caption_key(figure["caption"])[:_CAPTION_MATCH_CHARS]
        holding = [at for at, block in enumerate(blocks) if caption and caption in _build_caption_key(block)]
        place = holding[0] if holding else min(len(blocks), round(len(blocks) * figure["box"][1] / page_height))
        middle = (figure["box"][1] + figure["box"][3]) / 2
        while place < len(blocks) and blocks[place].lstrip().startswith("#"):
            top = _find_line_top(line_tops, blocks[place])
            if top is None or top >= middle:
                break
            place += 1
        places.append(place)

    return places


# A formula block as the readers write it
_FORMULA_MARK = "$$"
# The most words a connective line between two formulas holds
_CONNECTIVE_WORDS = 3


def _is_connective_block(block: str) -> bool:
    """Whether `block` is a connective line of a formula's text: one line of at most three words, lower case first and
    no sentence end ("where", "and hence")."""

    text = block.strip()

    return ("\n" not in text and 0 < len(text.split()) <= _CONNECTIVE_WORDS and text[:1].islower()
            and not text.endswith((".", ":", "?", "!")))


def _split_blocks(text: str) -> list[str]:
    """A page's text in blocks: paragraphs, lists, tables and headings, each a block of lines with no blank one, and a
    heading always the start of its own block. A table ending right on the next section's heading, with no blank line
    between, made one block of the last incident and the whole January section, which phase 4 then filed in the
    incidents' chapter (pdf-02)."""

    # Inside a code fence neither a blank line nor a "#" line ends a block: a shell comment is no heading, and a code
    # block cut at one came back as two paragraphs holding half a block each
    blocks: list[str] = []
    current: list[str] = []
    is_in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            is_in_fence = not is_in_fence
        elif not is_in_fence and not line.strip():
            blocks.append("\n".join(current))
            current = []
            continue
        elif not is_in_fence and line.lstrip().startswith("#") and current:
            blocks.append("\n".join(current))
            current = []
        current.append(line)
    blocks.append("\n".join(current))

    blocks = [block.strip() for block in blocks if block.strip()]
    # A heading alone in its block is joined to the block after it: read as markdown, a heading of its own became a
    # paragraph, set aside as "title slide text" under every chapter (pdf-02, both readers). So is a list item to the
    # list item or the lead-in ending in ":" before it: a form's "To reduce your risk:" and its three items were four
    # paragraphs, each a "brief reply" no section had to cite, and the summary cited one of them (pdf-real-fw9)
    joined: list[str] = []
    for block in blocks:
        if not joined:
            joined.append(block)
            continue
        is_after_lone_heading = joined[-1].startswith("#") and "\n" not in joined[-1] and not block.startswith("#")
        is_list_continuation = bool(_LIST_ITEM.match(block)) and (bool(_LIST_ITEM.match(joined[-1].splitlines()[-1]))
                                                                  or joined[-1].rstrip().endswith(":"))
        # A connective line between two formulas ("where", "and") is joined to the formula after it: alone it was a
        # paragraph of one word, an entry the writer had to cite (the NACA scan's chapter 13, DEFECTS 497)
        is_after_connective = _is_connective_block(joined[-1]) and block.startswith(_FORMULA_MARK)
        if is_after_lone_heading or is_list_continuation or is_after_connective:
            joined[-1] = f"{joined[-1]}\n{block}"
        else:
            joined.append(block)

    return joined


def _build_block_key(block: tuple) -> tuple[int, int, str]:
    """Where a text block stands and what it says, as a block standing on most pages is matched by."""

    return round(block[0] / _PLACE_POINTS), round(block[1] / _PLACE_POINTS), _normalise_line(str(block[4]))


def _remove_repeated_blocks(document: pymupdf.Document,
                            removed: list[dict]) -> None:
    """Blank out of the open (in-memory) document every text block standing at the same place with the same text on
    _REPEATED_LINE_SHARE of its pages or more: a wiki's page tree beside the content, a print header and footer, a row
    of buttons. Read with the page, a side panel's lines were mixed into the content's and cut a sentence in half."""

    if document.page_count < _MIN_PAGES_FOR_REPEATS:
        return
    page_blocks = [[block for block in page.get_text("blocks") if block[6] == _TEXT_BLOCK and str(block[4]).strip()]
                   for page in document]
    keys = [{_build_block_key(block) for block in blocks} for blocks in page_blocks]
    counts = Counter(key for page_keys in keys for key in page_keys)
    repeated = {key for key, count in counts.items() if count >= _REPEATED_LINE_SHARE * document.page_count}
    for number, (page, blocks) in enumerate(zip(document, page_blocks)):
        for block in blocks:
            if _build_block_key(block) in repeated:
                page.add_redact_annot(pymupdf.Rect(block[:4]))
                _record_removal(removed, number + 1, "a block at the same place on most pages",
                                " ".join(str(block[4]).split()))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def is_scanned_page(page: pymupdf.Page) -> bool:
    """Whether `page` is a scan: a picture fills it, and no word on it is drawn in ink. A searchable scan's text layer
    is invisible (an old OCR's, garbled on a scanned NASA report); a poster or a page drawn over a
    background picture writes its words visibly."""

    page_area = page.rect.get_area()
    is_filled = any((pymupdf.Rect(info["bbox"]) & page.rect).get_area() / page_area > _FIGURE_MAX_SHARE
                    for info in page.get_image_info())
    if not is_filled:
        return False

    return not any(span.get("chars") for span in page.get_texttrace() if span.get("type") != _INVISIBLE_TEXT)


def _read_scanned_pages(document: pymupdf.Document,
                        page_texts: list[str],
                        page_figures: list[list[dict]],
                        removed: list[dict],
                        is_figure_kept: bool,
                        indexes: list[int],
                        on_scanned_page: Callable[[int, int], None] | None = None) -> list[int]:
    """Each scanned page of the open `document` among `indexes` (from 0) read by Unlimited-OCR-2 in place of
    Tesseract's text in `page_texts`:
    its header, footer and page number appended to `removed`, and its pictures, when figures are kept, as its figures
    in `page_figures` (the PDF's own figure finder sees no figure on a scan). `on_scanned_page(read, total)` is told
    before the first page and after each one. The page numbers read, from 1."""

    scanned = [number for number in indexes if is_scanned_page(document[number])]
    if not scanned:
        return []
    if on_scanned_page:
        on_scanned_page(0, len(scanned))
    with tempfile.TemporaryDirectory(prefix="i2k-scans-") as folder:
        images = []
        for number in scanned:
            page = document[number]
            zoom = _FRAME_WIDTH / page.rect.width
            image = Path(folder) / f"page-{number + 1:04d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(image)
            images.append(image)
        readings = unlimited_ocr_reader_service.read_pages(images, on_scanned_page)
    for number, reading in zip(scanned, readings):
        markdown, chrome, figures = unlimited_ocr_block_utils.read_page(reading)
        page_texts[number] = markdown
        removed += [{"page": number + 1, "rule": _OCR_CHROME_RULE, "text": text} for text in chrome]
        if not is_figure_kept:
            continue
        rect = document[number].rect
        scale = [rect.width / unlimited_ocr_block_utils.GRID, rect.height / unlimited_ocr_block_utils.GRID] * 2
        origin = [rect.x0, rect.y0] * 2
        page_figures[number] = [{"box": [round(start + value * factor, 1)
                                         for start, value, factor in zip(origin, figure["box"], scale)],
                                 "caption": figure["caption"]} for figure in figures]

    return [number + 1 for number in scanned]


def _count_pages(on_page: Callable[..., None],
                 walks: dict[str, int],
                 pages: list) -> Iterator:
    """`pages` handed on one by one, `on_page(read, total, step=...)` told after each: put in place of pymupdf4llm's
    progress bar, which it makes once for each walk over the pages. Its first walk parses them and its second writes
    their markdown, each counted as its own step: counted once, a 6,761-page PDF stood at 6,761 of 6,761 for hours
    while the second walk ran."""

    walks["count"] += 1
    step = progress_service.PARSING if walks["count"] == 1 else progress_service.MARKDOWN
    total = len(pages)
    for read, page in enumerate(pages, start=1):
        yield page
        on_page(read, total, step=step)


def _read_page_texts(document: pymupdf.Document,
                     on_page: Callable[..., None] | None,
                     indexes: list[int]) -> list[str]:
    """Each page's markdown as pymupdf4llm reads the open, blanked document, one entry a page of the document, "" for a
    page not in `indexes` (from 0), which it does not read. `on_page(read, total, step=...)` is told as each page is
    parsed and then as its markdown is written (from 6 pages, where pymupdf4llm counts them)."""

    # pymupdf4llm counts its pages through its layout module's progress bar; a counter put in its place while it
    # reads tells phase 1's bar how far it is, a 60 MB PDF having read for minutes with no count. Read one page at
    # a time instead, 4 of 7 test PDFs came out otherwise (its headers and footers are found over the whole PDF)
    bar_owner = getattr(pymupdf4llm.helpers, "document_layout", None)
    counted = on_page is not None and bar_owner is not None and hasattr(bar_owner, "ProgressBar")
    previous_bar = bar_owner.ProgressBar if counted else None
    if counted:
        bar_owner.ProgressBar = functools.partial(_count_pages, on_page, {"count": 0})
    try:
        # Tesseract, which reads each picture's words, prints its own complaints past Python's error stream
        with native_stderr_utils.hide_native_stderr():
            chunks = pymupdf4llm.to_markdown(document, pages=indexes, page_chunks=True, show_progress=counted)
    finally:
        if counted:
            bar_owner.ProgressBar = previous_bar
    texts = [""] * document.page_count
    for index, chunk in zip(indexes, chunks):
        texts[index] = str(chunk.get("text") or "")

    return texts


def _save_frame(pixmap: pymupdf.Pixmap,
                frames_dir: Path,
                index: int) -> tuple[str, str]:
    """The pixmap written as frame `index` in `frames_dir`; its slug and its path in the run folder."""

    slug = f"frame-{index:03d}"
    pixmap.save(str(frames_dir / f"{slug}.jpg"), jpg_quality=_FRAME_JPEG_QUALITY)

    return slug, f"{frames_dir.name}/{slug}.jpg"


def _save_frame_image(image: numpy.ndarray,
                      frames_dir: Path,
                      index: int) -> tuple[str, str]:
    """The BGR image written as frame `index` in `frames_dir`, as `_save_frame` writes a pixmap."""

    slug = f"frame-{index:03d}"
    cv2.imwrite(str(frames_dir / f"{slug}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, _FRAME_JPEG_QUALITY])

    return slug, f"{frames_dir.name}/{slug}.jpg"


def _build_frame_item(slug: str,
                      path: str,
                      index: int,
                      page_number: int,
                      timestamp: float,
                      box: list[int]) -> dict:
    """A page's or a figure's item of phase 2's frame list, `box` its place on the page at _FULL_DPI, which the cut tool
    reads the page again at: frame number is the page's index, as the cut tool opens it."""

    return {"slug": slug, "index": index, "frameNumber": page_number - 1, "timestamp": timestamp, "path": path,
            "score": 1.0, "box": box, "screenBox": list(box), "repeatOf": None, "stepOf": None, "page": page_number}


def _is_same_page(text: str,
                  image: numpy.ndarray | None,
                  earlier_text: str,
                  earlier_image: numpy.ndarray | None) -> bool:
    """Whether a page repeats an earlier one: the same text, and the same look at `_COMPARE_WIDTH` pixels wide."""

    if not text or text != earlier_text or image is None or earlier_image is None or image.shape != earlier_image.shape:
        return False

    return float(numpy.abs(earlier_image - image).mean()) <= _SAME_PAGE_DIFFERENCE


def _read_small_page(path: Path) -> numpy.ndarray | None:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height = max(1, round(image.shape[0] * _COMPARE_WIDTH / image.shape[1]))

    return cv2.resize(image, (_COMPARE_WIDTH, height), interpolation=cv2.INTER_AREA).astype(numpy.float32)


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def count_pages(pdf: Path) -> int:
    with pymupdf.open(pdf) as document:
        return document.page_count


def find_figures(page: pymupdf.Page) -> list[dict]:
    """The page's figures, top to bottom: `{"box": [x0, y0, x1, y1] in points, "caption"}`. A picture, or a drawing of
    lines and shapes, with the frame and caption drawn round it; never a scanned page, a table's lines, a code block's
    background or a box of text, whose words the page's text holds already."""

    page_words = page.get_text("words")
    words = [pymupdf.Rect(word[:4]) for word in page_words]
    # A picture filling the page is a scan, unless the page writes a few visible words on it: a poster. A searchable
    # scan's text layer is invisible ink (render mode 3) over the picture, 26 words on a report's title page
    has_visible_text = any(span.get("chars") for span in page.get_texttrace() if span.get("type") != _INVISIBLE_TEXT)
    largest = 1.0 if has_visible_text and len(words) < _POSTER_MAX_WORDS else _FIGURE_MAX_SHARE
    all_pictures = [pymupdf.Rect(info["bbox"]) & page.rect for info in page.get_image_info()]
    sized_pictures = [picture for picture in all_pictures if _is_figure_size(picture, page, largest)]
    # A blank picture is no figure: a wiki's grey file placeholder was 10 figures of one PDF (DEFECTS 667)
    blank_pictures = [picture for picture in sized_pictures if _is_blank_picture(page=page, box=picture)]
    pictures = [picture for picture in sized_pictures if picture not in blank_pictures]
    drawn = _list_drawn_figures(page=page,
                                pictures=pictures,
                                blank_pictures=blank_pictures,
                                words=words,
                                labels=_list_label_words(page_words))
    figures = _join_overlapping([*pictures, *drawn])

    # A figure holding a picture is a picture (a screenshot, a photo); one of lines and shapes alone is a drawing (a
    # chart, a diagram), which `pdf.figureReading` "read-drawings-only" reads and places the pictures unread
    return [{"box": [round(value, 1) for value in box], "caption": _read_caption(page_words, box, pictures),
             "kind": PICTURE_FIGURE if any(_is_picture_in(picture=picture, box=box) for picture in pictures)
             else DRAWING_FIGURE}
            for box in figures]


def read_chosen_ocr_engine(folder: Path) -> str:
    """The engine the run in `folder` reads its PDF's scanned pages with: the one written for it, else
    `pdf.ocrEngine`."""

    path = folder / output_contract.RUN_PDF_OCR_ENGINE_FILE
    if not path.is_file():
        return config.settings.pdf.ocr_engine
    # A damaged choice reads as the settings' engine, as no choice does, and says so: phase 1 still reads the PDF
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _LOG.warning("%s/%s could not be read, scans are read with pdf.ocrEngine: %s", folder.name, path.name, error)
        return config.settings.pdf.ocr_engine
    chosen = recorded.get("ocrEngine") if isinstance(recorded, dict) else None
    if chosen not in OCR_ENGINES:
        _LOG.warning("%s/%s names no engine of %s, scans are read with pdf.ocrEngine", folder.name, path.name,
                     ", ".join(OCR_ENGINES))
        return config.settings.pdf.ocr_engine

    return chosen


def write_chosen_ocr_engine(folder: Path,
                            engine: str) -> None:
    """The run folder's engine for scanned pages set to `engine`; an engine not in the list is refused by name."""

    if engine not in OCR_ENGINES:
        raise ValueError(f"no OCR engine {engine!r}: the engines are " + ", ".join(OCR_ENGINES))
    json_store_utils.write_json(folder / output_contract.RUN_PDF_OCR_ENGINE_FILE, {"ocrEngine": engine})


def _mark_numbered_headings(texts: list[str]) -> list[str]:
    """The pages' text with each whole bold line numbered as a section ("**5.2. Title**") marked as a heading, the
    mark the document's other numbered headings carry, when it has any: the reader marked a framework's every numbered
    section but one, and the agenda never named it. A document with no numbered heading keeps its bold numbered lines,
    a manual's steps."""

    marks = collections.Counter(match.group(1) for text in texts for match in _NUMBERED_HEADING.finditer(text))
    if not marks:
        return texts
    mark = marks.most_common(1)[0][0]

    return [_BOLD_NUMBERED_LINE.sub(lambda match: f"{mark} {match.group(0)}", text) for text in texts]


def _ignore_pages(read: int,
                  total: int,
                  step: str) -> None:
    """The page count told to no one, for a caller that follows none."""


def _drop_note_markers(segments: list[dict],
                       removed: list[dict]) -> None:
    """Each segment's numbered pointers into the back matter taken out, in place, when the back matter itself was:
    each page's markers one entry of `01_cleanup.json`. A code block keeps its brackets."""

    if not any(entry["rule"] == _BACK_MATTER_RULE for entry in removed):
        return
    for segment in segments:
        if segment["text"].lstrip().startswith("```"):
            continue
        markers = _NOTE_MARKER.findall(segment["text"])
        if markers:
            # with the space before it, so "the cycle [12] uses" reads "the cycle uses"
            segment["text"] = re.sub(r" ?" + _NOTE_MARKER.pattern, "", segment["text"], flags=re.IGNORECASE)
            _record_removal(removed, segment["page"], "pointers to the back matter taken out", " ".join(markers))


def build_page_transcript(pdf: Path,
                          is_read_as_pages: bool = True,
                          ocr_engine: str = TESSERACT_ENGINE,
                          on_pages: Callable[..., None] | None = None,
                          page_indexes: list[int] | None = None) -> tuple[dict, list[dict]]:
    """The document as phase 1's transcript, read by pymupdf4llm, one segment per block of a page's cleaned text, at the
    page's minute, and every removal `{"page", "rule", "text"}`. With `page_indexes` (from 0), only those pages are
    read, each keeping its own number and minute; the lines and blocks repeated on most pages are still found over the
    whole document, so a few pages keep no footer. Read as pages as images, a page's text is what
    its frame shows; read as text only (`is_read_as_pages` false), the figures are listed apart. A scanned page is read
    by `ocr_engine` (`OCR_ENGINES`), and `on_pages(read, total, step=...)` follows the reading page by page, step by
    step in `progress_service.PDF_STAGES`' order: each page's text layer, its figures, pymupdf4llm's parsing and its
    markdown (6 pages or more), the scanned pages Unlimited-OCR-2 reads, each page's text cleaned."""

    if ocr_engine not in OCR_ENGINES:
        raise ValueError(f"no OCR engine {ocr_engine!r}: the engines are " + ", ".join(OCR_ENGINES))

    # STEP 1: from the open document, the links, the repeated lines, the blanked repeated blocks, the figures and the
    # pages' text. Links and the lines repeated on most pages are read before the blanking, which takes the links of
    # the blanked blocks with it and leaves a deck's footer on the one slide where it stood in another block
    removed: list[dict] = []
    tell = on_pages or _ignore_pages
    with pymupdf.open(pdf) as document:
        total = document.page_count
        indexes = list(range(total)) if page_indexes is None else page_indexes
        # Every page's text layer, for the lines repeated on most pages: counted over a few pages alone, a footer is no
        # longer on most of them
        raw_texts = []
        for number, page in enumerate(document, start=1):
            raw_texts.append(page.get_text())
            tell(number, total, step=progress_service.TEXT_LAYER)
        links = {index: _list_outside_links(document[index]) for index in indexes}
        repeated = _list_repeated_lines(raw_texts)
        _remove_repeated_blocks(document, removed)
        # Read as text only, the pictures and drawings are lost unless kept apart: each becomes a frame of its own in
        # phase 2; read as pages as images, the page's frame shows them. Found
        # before the text is read, which changes the open document: a drawn chart was gone after it. Each line's top
        # on the page is kept too, so a figure is placed on the right side of a heading beside it
        page_figures: list[list[dict]] = [[] for _ in range(total)]
        page_line_tops: list[list] = [[] for _ in range(total)]
        page_heights = [page.rect.height for page in document]
        for read, index in enumerate(indexes, start=1):
            page = document[index]
            page_figures[index] = [] if is_read_as_pages else find_figures(page)
            page_line_tops[index] = [(block[1], line) for block in page.get_text("blocks")
                                     for line in block[4].splitlines()[:1]]
            tell(read, len(indexes), step=progress_service.FIGURES)
        page_texts = _read_page_texts(document=document, on_page=on_pages, indexes=indexes)
        # A scanned page read by Unlimited-OCR-2 in place of the Tesseract text pymupdf4llm gave it: on the test
        # scans it kept 99% of the words where Tesseract kept 26% to 97%
        is_ocr_engine_read = ocr_engine == UNLIMITED_OCR_ENGINE
        ocr_pages = (_read_scanned_pages(document, page_texts, page_figures, removed, not is_read_as_pages, indexes,
                                         functools.partial(tell, step=progress_service.SCANNED_PAGES))
                     if is_ocr_engine_read else [])

    # STEP 2: each page's text cleaned and cut into blocks, its contents and back matter taken out, then its tables
    # marked where they run on
    texts = _mark_numbered_headings([_add_link_addresses(page_texts[index], links[index]) for index in indexes])
    # Counted again on the reader's text as well as the raw text layer: the reader writes a line otherwise (its marks,
    # its link addresses, a line it joins), and a footer that repeats in one form may not repeat in the other
    repeated += _list_repeated_lines(texts)
    segments: list[dict] = []
    figures: list[dict] = []
    carried: dict = {}
    for read, (number, text) in enumerate(zip(indexes, texts), start=1):
        page_number, page_start = number + 1, number * PAGE_SECONDS
        cleaned = _clean_page(text, repeated, page_number, removed, bool(page_figures[number]))
        blocks, carried = _list_page_blocks(cleaned, page_number, removed, carried)
        tell(read, len(texts), step=progress_service.PAGE_CLEANING)

        # STEP 3: the page's blocks and figures placed on its minute, each figure just before the block it stands
        # before, so the merge puts it there; one before the page's first block keeps the page's start, so it stays on
        # its page, and the merge's order of kinds puts it first there
        places = _list_figure_places(page_figures[number], blocks, page_heights[number], page_line_tops[number])
        blocks, places = _drop_caption_blocks(blocks, page_figures[number], places, page_number, removed)
        step = PAGE_SECONDS / max(len(blocks), 1)
        figures += [{**figure, "page": page_number,
                     "start": round(max(page_start + place * step - _FIGURE_LEAD_SECONDS, page_start), 2)}
                    for figure, place in zip(page_figures[number], places)]
        segments += [{"text": block, "start": round(page_start + place * step, 2),
                      "end": round(page_start + (place + 1) * step, 2), "speaker": None, "words": [],
                      "page": page_number} for place, block in enumerate(blocks)]

    # STEP 4: the pointers into the back matter, which the whole document had to be read to know was there; a figure the
    # page break cut in two made one again
    _drop_note_markers(segments, removed)
    figures = _join_page_break_figures(figures, page_heights)
    # The blocks repeated on most pages were blanked on every page; only the pages read say what was removed from them
    read_numbers = {index + 1 for index in indexes}
    removed = [entry for entry in removed if entry.get("page") in read_numbers]

    # STEP 5: the transcript, its figures beside its segments
    transcript = {"language": None, "languageProbability": None, "engine": "pymupdf4llm", "model": "",
                  "text": "\n\n".join(segment["text"] for segment in segments), "segments": segments}
    if figures:
        transcript["figures"] = figures
    if ocr_pages:
        transcript["ocrEngine"] = ocr_engine
        transcript["ocrPages"] = ocr_pages

    return transcript, removed


def _join_page_break_figures(figures: list[dict],
                             page_heights: list[float]) -> list[dict]:
    """`figures` with each one the page break cut in two made one: its first part's page and place, `parts` listing each
    page's box, the caption of its last part. A wiki's timeline ran from a page's foot to the next page's head, and was
    two frames, the first with the page's white under its bars."""

    joined: list[dict] = []
    for figure in figures:
        if joined and _is_continued_by(joined[-1], figure, page_heights):
            upper = joined[-1]
            parts = upper.get("parts") or [{"page": upper["page"], "box": upper["box"]}]
            joined[-1] = {**upper, "parts": [*parts, {"page": figure["page"], "box": figure["box"]}],
                          "caption": figure["caption"]}
            continue
        joined.append(figure)

    return joined


def _is_continued_by(upper: dict,
                     lower: dict,
                     page_heights: list[float]) -> bool:
    """Whether `lower`, the first figure of its page, goes on from `upper`, the last of the page before: `upper` has no
    caption yet and ends at its page's foot, `lower` starts at its page's head, and both stand in the same columns."""

    last = (upper.get("parts") or [upper])[-1]
    if lower["page"] != last["page"] + 1 or upper.get("caption"):
        return False
    upper_box, lower_box = last["box"], lower["box"]
    is_at_foot = upper_box[3] >= page_heights[last["page"] - 1] * (1 - _PAGE_BREAK_SHARE)
    is_at_head = lower_box[1] <= page_heights[lower["page"] - 1] * _PAGE_BREAK_SHARE
    shared = min(upper_box[2], lower_box[2]) - max(upper_box[0], lower_box[0])
    narrower = min(upper_box[2] - upper_box[0], lower_box[2] - lower_box[0])

    return is_at_foot and is_at_head and shared >= _PAGE_BREAK_COLUMNS * narrower


def _render_parts(document: pymupdf.Document,
                  parts: list[dict],
                  zoom: float) -> numpy.ndarray:
    """A figure over page breaks as one BGR image: each part, `{"page", "box"}` in points, rendered at `zoom`, the white
    the page break left under a part and over the next taken off, the parts stacked, the narrower padded white."""

    images = []
    for index, part in enumerate(parts):
        page = document[part["page"] - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(part["box"]) & page.rect, alpha=False)
        image = cv2.cvtColor(numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, 3),
                             cv2.COLOR_RGB2BGR)
        side = round(image.shape[1] * _BLANK_ROW_SIDE)
        inked = numpy.flatnonzero((image[:, side:image.shape[1] - side] < _BLANK_ROW_LEVEL).any(axis=(1, 2)))
        top = int(inked[0]) if index and inked.size else 0
        bottom = int(inked[-1]) + 1 if index < len(parts) - 1 and inked.size else image.shape[0]
        images.append(image[top:bottom])
    width = max(image.shape[1] for image in images)

    return numpy.vstack([cv2.copyMakeBorder(image, 0, 0, 0, width - image.shape[1], cv2.BORDER_CONSTANT,
                                            value=(255, 255, 255)) for image in images])


def read_figure_image(pdf: Path,
                      parts: list[dict]) -> numpy.ndarray | None:
    """A figure over page breaks as the cut tool reads it, at _FULL_DPI: its frame item's `parts`, each box in pixels at
    _FULL_DPI, stacked as its frame was. None when the file lacks a part's page."""

    with pymupdf.open(pdf) as document:
        if not all(1 <= part["page"] <= document.page_count for part in parts):
            return None
        return _render_parts(document, [{"page": part["page"], "box": [value / _FULL_DPI_SCALE for value in part["box"]]}
                                        for part in parts], _FULL_DPI_SCALE)


def read_page_image(pdf: Path,
                    page_index: int,
                    dpi: int = _FULL_DPI) -> numpy.ndarray | None:
    """The page as a BGR array at `dpi`, or None when the file has no such page."""

    with pymupdf.open(pdf) as document:
        if not 0 <= page_index < document.page_count:
            return None
        pixmap = document[page_index].get_pixmap(dpi=dpi, alpha=False)
    rgb = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, 3)

    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def render_pages(pdf: Path,
                 frames_dir: Path,
                 page_indexes: list[int] | None = None) -> dict:
    """Every page, or the pages `page_indexes` names (from 0), written as `frames/frame-NNN.jpg`, _FRAME_WIDTH wide, in
    the shape the video's frame extraction returns: `{"mode", "video": {"durationS", "fps"}, "items"}`, each item's
    `box` the page at _FULL_DPI, which the cut tool reads. Frames are numbered from 0 as written; each keeps its page's
    own number and minute."""

    frames_dir.mkdir(parents=True, exist_ok=True)
    items = []
    with pymupdf.open(pdf) as document:
        indexes = list(range(document.page_count)) if page_indexes is None else page_indexes
        for frame_index, number in enumerate(indexes):
            page = document[number]
            zoom = _FRAME_WIDTH / page.rect.width
            slug, path = _save_frame(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False), frames_dir,
                                     frame_index)
            box = [0, 0, round(page.rect.width * _FULL_DPI_SCALE), round(page.rect.height * _FULL_DPI_SCALE)]
            items.append(_build_frame_item(slug, path, frame_index, number + 1, number * PAGE_SECONDS, box))
    end_s = (indexes[-1] + 1) * PAGE_SECONDS if indexes else 0

    return {"mode": "pdf", "video": {"durationS": end_s, "fps": 0.0}, "items": items}


def render_figures(pdf: Path,
                   transcript: dict,
                   frames_dir: Path) -> list[dict]:
    """Each figure phase 1 found, read as text only, written as `frames/frame-NNN.jpg` cut from its page at _FULL_DPI
    (at most _FRAME_WIDTH wide), as phase 2's items: `box` the figure on the page at _FULL_DPI, which the cut tool reads
    from the page again, `transcriptCurrent` its caption and `transcriptBefore` the last `_FIGURE_BLOCKS_BEFORE` blocks of
    its page before it, so its notes are read in their place."""

    figures = transcript.get("figures") or []
    if not figures:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    segments = transcript.get("segments", [])
    items = []
    with pymupdf.open(pdf) as document:
        for index, figure in enumerate(figures):
            page = document[figure["page"] - 1]
            clip = pymupdf.Rect(figure["box"]) & page.rect
            parts = figure.get("parts") or []
            zoom = min(_FULL_DPI_SCALE, _FRAME_WIDTH / max(max([clip.width, *(part["box"][2] - part["box"][0] for part in parts)]), 1))
            if parts:
                slug, path = _save_frame_image(_render_parts(document, parts, zoom), frames_dir, index)
            else:
                slug, path = _save_frame(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False),
                                         frames_dir, index)
            before = [segment["text"] for segment in segments
                      if segment.get("page") == figure["page"] and segment["start"] < figure["start"]]
            item = _build_frame_item(slug, path, index, figure["page"], figure["start"],
                                     [round(value * _FULL_DPI_SCALE) for value in clip])
            # A figure over a page break keeps each page's part, which the cut tool reads and stacks as the frame is
            if parts:
                item["parts"] = [{"page": part["page"], "box": [round(value * _FULL_DPI_SCALE) for value in part["box"]]}
                                 for part in parts]
            items.append({**item, "medium": output_contract.PDF_FIGURE_MEDIUM, "caption": figure["caption"],
                          "transcriptBefore": "\n\n".join(before[-_FIGURE_BLOCKS_BEFORE:]), "transcriptCurrent": figure["caption"],
                          # a scan's figures, from Unlimited-OCR-2's picture boxes, and a phase 1 run before kinds
                          # were kept name none
                          "kind": figure.get("kind") or ""})

    return items


def split_figures_to_read(items: list[dict],
                          figure_reading: str,
                          read_limit: int) -> tuple[list[dict], list[dict]]:
    """The figure frames phase 3 reads, and those placed unread where they stood, by `pdf.figureReading`: every one
    read, every one placed when there are more than `read_limit`, or only the drawings read."""

    if figure_reading == READ_ALL_FIGURES or (figure_reading == PLACE_ABOVE_LIMIT and len(items) <= read_limit):
        return items, []
    if figure_reading == PLACE_ABOVE_LIMIT:
        return [], items
    if figure_reading == READ_DRAWINGS_ONLY:
        drawings = [item for item in items if item.get("kind") == DRAWING_FIGURE]
        pictures = [item for item in items if item.get("kind") != DRAWING_FIGURE]
        return drawings, pictures
    raise ValueError(f"pdf.figureReading {figure_reading!r} is none of {READ_ALL_FIGURES}, {PLACE_ABOVE_LIMIT}, "
                     f"{READ_DRAWINGS_ONLY}")


def mark_repeated_pages(items: list[dict],
                        frames_dir: Path) -> list[dict]:
    """`items` with each page that holds an earlier page's text, and looks the same, marked `repeatOf` the first such
    page, so phase 3 reads it as a screen shown again: a document's closing page printed three times was refused
    twice for facts the first one held word for word, a repeat's right answer being "nothing new" (pdf-06)."""

    seen: list[tuple[str, numpy.ndarray | None, str]] = []
    marked = []
    for item in items:
        text = " ".join(str(item.get("transcriptCurrent") or "").split())
        image = _read_small_page(frames_dir / Path(item["path"]).name)
        original = next((slug for earlier, small, slug in seen if _is_same_page(text, image, earlier, small)), None)
        marked.append({**item, "repeatOf": original})
        if original is None:
            seen.append((text, image, item["slug"]))

    return marked


def build_page_slices(segments: list[dict],
                      page_numbers: list[int]) -> list[dict]:
    """Each page's text, of the pages `page_numbers` names (from 1, in order), as what is said over its frame,
    `{"before", "current"}`: `current` exactly its own blocks, their lines and tables kept, `before` the page before it.
    Sliced as speech, a table became one line and a page's words ran on into the next page's heading, as a sentence
    said across a frame's end does."""

    pages = ["\n\n".join(segment["text"] for segment in segments if segment.get("page") == number)
             for number in page_numbers]

    return [{"before": pages[index - 1] if index else "", "current": text} for index, text in enumerate(pages)]
