"""A frame's notes as markdown: the model's answer read into fields, and fields written back as the same document.
Pure functions.

An answer is a run of level-1 documents, each with its `##` sections (`schemas/frame_record.py` names them):

    # Frame 12 · web application        one frame's notes; in the second pass, what one frame's notes gain
    # Batch                           a batch's account of it and how it felt (R15, R18)
    # Nothing to change                 the second pass found nothing to add (R19)

What stands before the first document is the model talking to itself, and is not read. A heading the documents do not
have is refused by name, never guessed at: its content would otherwise be lost in silence.

The notes are stored in the same shape (`services/record_file_service.py`), so the file a person opens is the answer
the checks judged, written back by the program with its secrets hidden and its diagram's nodes named.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from models.screen_outline_result import ScreenOutlineResult, ScreenPartItem
from schemas import frame_record
from utils import feedback_report_utils
from utils import mermaid_text_utils

__all__ = ["split_pointer_head", "fill_pointer_parts", "drop_answered_questions", "move_restated_answers_to_open", "read_pointed_frame", "mark_unsaid_screen_quotes", "normalise_speech", "read_feedback", "unwrap_code_entry", "THINKING_BLOCK", "parse_answer", "render_frame_document", "render_addition_document",
           "render_batch_document", "render_second_pass_document", "render_stored_notes", "render_notes_to_review", "render_frame_data",
           "render_batch_data", "is_screen_pointer", "read_screen_parts", "normalise_screen_indentation",
           "list_screen_quotes", "list_opening_and_recurring_text", "find_screen_title", "unwrap_answer_fence",
           "IGNORED_EDIT", "is_cut_off_question", "strip_line_numbers", "strip_reading_notes", "strip_bullet_signs", "unwrap_speaker_fact", "is_page_number_fact"]

_FENCE = "```"
# A thinking block a model writes into its answer, however it spells the tag's case
THINKING_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DOCUMENT = re.compile(r"^# (?P<title>.+?)\s*$")
_SECTION = re.compile(r"^## (?P<heading>.+?)\s*$")
# A frame's title written one level too deep, "## Frame 15 · slide", is its title: read as a section of the frame
# before, it threw away a second look's changes to both frames (2026-10-12)
_MISLEVELLED_FRAME_TITLE = re.compile(rf"^#{{2,3}}\s+(?={frame_record.FRAME_TITLE}\s+\d+\s*[·•:|\-–—])")
# `# Frame 12 · web application`; the separator is whatever dash, dot or colon the model typed.
_TITLE_WORDS = "|".join([frame_record.FRAME_TITLE, *frame_record.DOCUMENT_FRAME_TITLES.values()])
_FRAME_TITLE = re.compile(rf"^(?:{_TITLE_WORDS})\s+(?P<number>\d+)\s*(?:[·•:|\-–—]\s*(?P<kind>.*))?$",
                          re.IGNORECASE)
_BULLET = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+(?P<text>.*)$")
_DETAIL = re.compile(r"^\s+(?:[-*+]\s+)?(?P<label>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<value>.*)$")
_EMPTY_WORDS = {frame_record.EMPTY_MARK, "none", "-", "n/a", "nothing"}
# The empty mark in the worker's own words: "(none spoken).", "(nothing is spoken)", "nothing said"
# and "(None new; speech is brief acknowledgment)" or "(None from speech)", a line wholly in parentheses opening with the
# empty word, which became a fact phase 4 had to cite (lecture-9 chapter 9, 2026-10-21)
# Each turn of the circle taught it one wording; the cause-level rule is the last line: the empty word opening the
# section, followed by a separator or a bracket, is the empty mark with its reason, whatever the reason says
# ("(none) - The slide contains no new textual facts", "None (concluding introduction)"; over the public runs it names
# three such entries and no sentence, DEFECTS 645). "(none) →" opens a reading line with a screen side, read elsewhere
_EMPTY_PHRASE = re.compile(r"^\(?(?:none|nothing)(?: is)? (?:said|spoken|shown)\)?$|^\((?:none|nothing)\b[^()]*\)$"
                           r"|^(?:none|nothing) new$"
                           r"|^\(?(?:none|nothing)\)?\s*(?:[-–—:;,(\[]|$)")
_QUOTE = re.compile(r'"([^"]+)"|“([^”]+)”')
_TITLE_WORDS = re.compile(r"\b(?:title|heading|headline)\b", re.IGNORECASE)
_TITLE_WORD = re.compile(r"\btitle\b", re.IGNORECASE)
_OUTLINE_LINE = re.compile(r"^(?P<indent> *)[-*+] (?P<text>\S.*)$")
_OUTLINE_STEP = 2
_ANSWER_WORD = re.compile(r"[^\W_]{3,}")
# A word counts as the question's when its first five letters stand there ("reached" for "reaching")
_STEM_LETTERS = 5
# An answer that adds this many words or fewer beyond its question, its other words its question's, says nothing new:
# "Only Jonas." adds two words and none of its words is the question's
_LEAST_ADDED_WORDS = 2
_ASKED_WORD_SHARE = 0.6
# Words an answer names its source with, and the joining words between: none of them answers anything
_SOURCE_WORDS = {"frame", "frames", "transcript", "speech", "speaker", "said", "says", "states", "stated", "mentions",
                 "mentioned", "shown", "shows", "screen", "slide", "help", "text", "notes", "note", "while", "the",
                 "and", "that", "this", "with", "from", "via", "for", "are", "was", "were", "which", "also"}
# ... and with an empty mark after it ("same as frame 27: (none)", a repeat's line that blocked a batch, DEFECTS 280)
_SCREENSHOT_POINTER = re.compile(r"^(?:the\s+)?same\s+as\s+(?:on\s+|in\s+)?frame\s+\d+\W*(?:(?:none|n/a|nothing)\W*)?$",
                                 re.IGNORECASE)
_TRAILING_RULE = re.compile(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
# A line that is only a Markdown rule
_RULE_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
# An open question about speech that breaks off ("the sentence cuts off at 'for'", "the complete sentence starting with
# 'so the'"): a phrase of breaking off together
# with a word for the speech. Both are needed, so "Which step is cut off from the report?" stays a question.
_CUT_OFF = re.compile(r"\b(?:cut(?:s|ting)? off|breaks? off|broke off|trails? off|mid-sentence|incomplete|unfinished|"
                      r"is cut|left hanging|what follows|what comes (?:after|next)|how .* (?:ends|continues|conclude[sd]?|finish(?:es|ed)?)|"
                      r"(?:complete|full|whole) sentence (?:that )?(?:start|begin)\w*|rest of the (?:sentence|thought))\b",
                      re.IGNORECASE)
_SPEECH_WORDS = re.compile(r"\b(?:sentence|transcript|speech|spoken|words?|clause|phrase|thought|utterance)\b",
                           re.IGNORECASE)
# An editor's line number in an outline: "line 36", "lines 23 to 46", with the word before it that only places it
# ("at end of line 36", ": line 39", "moves from line 53 to line 54" leaves "moves"); a quoted or code text is never
# read for one
_LINE_NUMBER = re.compile(r"(?:\s+(?:of|on|at|in|from|to|into|onto|between|under|above|below|near|beside|and)\s+)?"
                          r"\b(?:code\s+)?lines?\s+\d+"
                          r"(?:\s*(?:to|-|–|through|and)\s*\d+)?\b", re.IGNORECASE)
_QUOTED_OR_CODE = re.compile(r'("[^"]*"|“[^”]*”|`[^`]*`)')
# What is left of an outline line that only said which lines show
_LINE_NUMBER_REST = re.compile(r"^(?:visible|shown|showing)?$", re.IGNORECASE)
# The sections an addition ADDS to the notes, under `added_<field>`; every other section replaces its field whole.
_LIST_SHAPES = (frame_record.LIST_SHAPE, frame_record.LABELLED_SHAPE, frame_record.TAGS_SHAPE)
# The fields an addition carries beside its sections, and the headings only an addition may write
_ADDITION_KEYS = ("frame", "what_changed", "screen_kind", "wrong_parts", "wrong_diagram_lines", "wrong_narration",
                  "edits")
_ADDITION_HEADINGS = (frame_record.WHAT_CHANGED_HEADING, frame_record.NOT_ON_FRAME_HEADING,
                      frame_record.WRONG_DIAGRAM_HEADING, frame_record.WRONG_NARRATION_HEADING,
                      frame_record.EDITS_HEADING)
# The opening texts are the ones the first this many frames' outlines quote
_OPENING_FRAMES = 2
# At most this many opening and this many recurring texts are listed
_QUOTES_LIMIT = 12
# A screen's quoted text listed as an opening or a recurring one is cut to this many characters
_QUOTE_CHARS = 120


def _clean_heading(text: str) -> str:
    return " ".join(text.replace("*", "").replace("`", "").strip().rstrip(":").split())


def _split_by(lines: list[str],
              pattern: re.Pattern) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines before the first heading `pattern` matches, and `(heading, body lines)` for each heading after, with
    a fenced block never cut: a `#` inside a diagram is the diagram's."""

    before: list[str] = []
    parts: list[tuple[str, list[str]]] = []
    is_fenced = False
    for index, line in enumerate(lines):
        match = None if is_fenced else pattern.match(line)
        # A fence that no later fence closes before the next heading is a stray mark, never an opening: one left after
        # a diagram's closed fence read every later section as code, and the frame was refused for headings it had
        # (2026-10-05)
        # A fence naming its language (```mermaid) always opens one; only a bare one can be a stray mark
        if line.strip().startswith(_FENCE) and (is_fenced or line.strip() != _FENCE
                                                or _is_closed_later(lines, index, pattern)):
            is_fenced = not is_fenced
        if match:
            parts.append((_clean_heading(match.group(1)), []))
        elif parts:
            parts[-1][1].append(line)
        else:
            before.append(line)

    return before, parts


def _is_closed_later(lines: list[str],
                     index: int,
                     pattern: re.Pattern) -> bool:
    """Whether a fence line follows the opening at `index` before the next line `pattern` reads as a heading."""

    for line in lines[index + 1:]:
        if line.strip().startswith(_FENCE):
            return True
        if pattern.match(line):
            return False

    return False


def _trim(lines: list[str]) -> list[str]:
    """The section's lines without blank lines, or a Markdown rule the model writes between frames, at either end."""

    kept = [line.rstrip() for line in lines]
    while kept and (not kept[0].strip() or _RULE_LINE.match(kept[0])):
        kept.pop(0)
    while kept and (not kept[-1].strip() or _RULE_LINE.match(kept[-1])):
        kept.pop()

    return kept


def _strip_bullet(line: str) -> str:
    """The line's text without its bullet of any kind (`-`, `*`, `+`, `1.`, `1)`), spaces stripped."""

    bullet = _BULLET.match(line)

    return (bullet.group("text") if bullet else line).strip()


def _is_empty(lines: list[str]) -> bool:
    # A model writes the empty mark as a bullet ("- (none)") as readily as bare, in every shape of section: read as an
    # entry it is one entry with every detail empty, which a check then refuses by name (2026-10-06: a batch of eight
    # frames blocked on an empty `importance` that was never written)
    text = " ".join(" ".join(lines).split()).casefold().rstrip(".")
    bullet = _BULLET.match(text)
    text = bullet.group("text").strip() if bullet else text

    return not lines or text in _EMPTY_WORDS or _EMPTY_PHRASE.match(text) is not None


def _read_list(lines: list[str]) -> list[str]:
    """One entry per top-level bullet, a wrapped line joined to the entry above it; a line with no bullet at all is an
    entry of its own."""

    items: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        bullet = _BULLET.match(line)
        if bullet and len(bullet.group("indent")) < _OUTLINE_STEP:
            items.append(bullet.group("text").strip())
        elif items and line[:1].isspace():
            items[-1] = f"{items[-1]} {line.strip()}"
        else:
            items.append(line.strip())

    return [unwrap_code_entry(_collapse_space_outside_code(item)) for item in items if item.strip()]


# A code span: a run of backticks, its text, the same run again
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_WHITE_RUN = re.compile(r"\s+")


def _collapse_space_outside_code(text: str) -> str:
    """`text` with each run of white space outside its code spans made one space, the spans kept as written: a fact
    quoting `def pay(a: int,   b: int)` to show spacing lost its spaces and read the same before and after."""

    parts, start = [], 0
    for span in _CODE_SPAN.finditer(text):
        parts += [_WHITE_RUN.sub(" ", text[start:span.start()]), span.group(0)]
        start = span.end()
    parts.append(_WHITE_RUN.sub(" ", text[start:]))

    return "".join(parts).strip()


# A take-away wrapped whole in one pair of backticks: no code, but it would print as a code span (lecture-1 frames 50-51)
_WRAPPED_TAKE_AWAY = re.compile(r"^`([^`]*→[^`]*)`$")


def unwrap_code_entry(entry: str) -> str:
    """`entry` without a backtick pair wrapping it whole when it is a take-away (`"<words>" → <part>: <meaning>`)."""

    wrapped = _WRAPPED_TAKE_AWAY.match(entry)

    return wrapped.group(1).strip() if wrapped else entry


def _read_diagram(lines: list[str]) -> str:
    """The Mermaid source inside the section's fence, or the section's text when it has none, for the check to judge."""

    source = mermaid_text_utils.strip_fence("\n".join(lines)).strip()

    # A flowchart of boxes and no arrow draws no flow: NIST's cover became six loose boxes in the published document.
    # A fence holding only the empty mark is empty: the check read "(none)" alone as the mark with a reason after it,
    # and a batch of four frames with no flow on screen was refused whole (release test 2, frames 18 to 21, DEFECTS 422)
    return "" if mermaid_text_utils.is_arrowless_flowchart(source) or _is_empty(source.splitlines()) else source


def _read_tags(lines: list[str]) -> list[str]:
    bullets = [line for line in lines if _BULLET.match(line)]
    # Tags written one per line with no comma are one tag a line, never one long tag
    if bullets:
        tags = _read_list(bullets)
    elif not any("," in line for line in lines):
        tags = [line.strip() for line in lines if line.strip()]
    else:
        tags = " ".join(lines).split(",")

    # A Markdown rule the model writes after the tags line ("..., persistence  ---") is no part of the last tag
    cleaned = [_TRAILING_RULE.sub("", tag).strip().strip("`\"'") for tag in tags]

    return [tag for tag in cleaned if tag]


def _build_labelled_entry(main: str,
                          labels: dict[str, str],
                          text: str) -> dict[str, str]:
    """A new entry of a labelled section: its main field from `text` without its marks, every detail empty."""

    return {main: text.strip().strip("<>`"), **{name: "" for name in labels.values()}}


def _clean_detail_value(text: str) -> str:
    """A detail's value with its spacing collapsed: wrapped in backticks or quote marks (`needed`), it is the value
    without them."""

    return " ".join(text.split()).strip("`\"'\u201c\u201d")


def _read_labelled(field: str,
                   lines: list[str],
                   errors: list[str]) -> list[dict[str, str]]:
    """Each top-level bullet an entry, its first line filling the entry's main field and each indented
    `- <label>: <value>` line one of its details."""

    main, labels = frame_record.LABELLED_SECTIONS[field]
    heading = frame_record.FRAME_SECTIONS[field][0]
    entries: list[dict[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        bullet = _BULLET.match(line)
        if bullet and len(bullet.group("indent")) < _OUTLINE_STEP:
            # A step's screenshot pointing to another frame ("same as frame 67") is that frame's suggestion, which
            # its notes already carry: read as an entry it had no importance and blocked a batch (DEFECTS 218)
            if field == "screenshots" and _SCREENSHOT_POINTER.match(bullet.group("text").strip()):
                continue
            entries.append(_build_labelled_entry(main, labels, bullet.group("text")))
            continue
        # A detail written flush left ("importance: needed") is the entry's detail, never an entry of its own
        flush_detail = _DETAIL.match(f" {line}") if line == line.lstrip() and entries else None
        if flush_detail and flush_detail.group("label").casefold() in labels:
            flush_label = labels[flush_detail.group("label").casefold()]
            entries[-1][flush_label] = _clean_detail_value(flush_detail.group("value"))
            continue
        # An entry written without its "- " (a bare address at the left edge) is still an entry, read as one
        if line == line.lstrip():
            entries.append(_build_labelled_entry(main, labels, line))
            continue
        detail = _DETAIL.match(line)
        label = detail.group("label").casefold() if detail else ""
        if not entries or label not in labels:
            errors.append(f'"## {heading}": the line "{line.strip()}" is not an entry\'s detail; write each '
                          f"entry as `- <{main}>`, then indented lines "
                          + ", ".join(f"`- {name}: ...`" for name in labels))
            continue
        entries[-1][labels[label]] = _clean_detail_value(detail.group("value"))

    return entries


# How the program's part of an outline opens ("- text the program read on the frame ..."), as a worker heads it
_PROGRAM_READING_HEADING = "text the program read"
# A top-level line opening with a two-word label and its colon: "- Take awies: ..."
_TWO_WORD_LABEL = re.compile(r"^(?P<dash>-\s*)(?P<label>\w+\s+\w+)\s*:")


def _fix_closing_label(line: str,
                         heading: str) -> str:
    """The take aways' closing line with its label spelled as the heading, when misspelled by a letter or two: a batch
    of eight frames wrote "Take awies:" on every one (Lecture 9, 2026-10-17), and the program reads the closing line
    by its label."""

    match = _TWO_WORD_LABEL.match(line)
    if match is None or match.group("label").casefold() == heading.casefold():
        return line
    likeness = difflib.SequenceMatcher(None, match.group("label").casefold(), heading.casefold()).ratio()

    return f"{match.group('dash')}{heading}:{line[match.end():]}" if likeness >= _HEADING_LIKENESS else line


def _is_part_above(line: str,
                   following: list[str]) -> bool:
    """Whether the next written line after `line` is indented deeper than it, a part inside it."""

    after = next((other for other in following if other.strip()), "")
    indent = len(line) - len(line.lstrip())

    return len(after) - len(after.lstrip()) > indent


def _read_section(field: str,
                  lines: list[str],
                  errors: list[str]) -> Any:
    """One section's body as its field's value, by the shape `schemas/frame_record.py` gives it."""

    heading, shape = frame_record.FRAME_SECTIONS[field]
    body = _trim(lines)
    # Only the take aways close with a line labelled with their name: under any other section a top-level line
    # "- Facts: ..." is a summary of it a second look added from a misread critique, dropped. A labelled line with
    # parts indented under it is a part of the outline, never a summary: the program's own "- screen: the text the
    # program read ..." over a frame's reader lines was dropped, its lines left with no part above, and the program
    # refused the outline it wrote (release test 2, frame 10, DEFECTS 418)
    if field != "take_aways":
        label = re.compile(rf"^-\s*{re.escape(heading)}\s*:", re.IGNORECASE)
        body = [line for index, line in enumerate(body)
                if not label.match(line) or _is_part_above(line, body[index + 1:])]
    else:
        body = [_fix_closing_label(line, heading) for line in body]
    if _is_empty(body):
        return _build_empty_value(shape)
    if shape == frame_record.LIST_SHAPE:
        return _read_list(body)
    if shape == frame_record.OUTLINE_SHAPE:
        outline = [line.replace("\t", " " * _OUTLINE_STEP) for line in body if line.strip()]
        # An outline with no dashed line at all, every line a part with its colon, is read with the dashes added: a
        # batch wrote all ten outlines so and every frame was refused for the form alone
        if not any(line.lstrip().startswith("- ") for line in outline) and all(":" in line for line in outline):
            outline = [f"{line[:len(line) - len(line.lstrip())]}- {line.lstrip()}" for line in outline]
        return "\n".join(outline)
    if shape == frame_record.DIAGRAM_SHAPE:
        return _read_diagram(body)
    if shape == frame_record.MARKDOWN_SHAPE:
        return "\n".join(body).strip()
    if shape == frame_record.TAGS_SHAPE:
        return _read_tags(body)
    if shape == frame_record.LABELLED_SHAPE:
        return _read_labelled(field, body, errors)
    if shape != frame_record.TEXT_SHAPE:
        raise ValueError(f"no reader for the section shape {shape!r} of {field}")

    return " ".join(" ".join(body).split())


# How close a misspelled heading must be to a known one to be read as it: "Take awies" is 0.80 from "Take aways",
# and no two of the real headings are closer than 0.71 to each other, so a misspelling never lands on the wrong one.
_HEADING_LIKENESS = 0.78


def _build_empty_value(shape: str) -> Any:
    """What a section of this shape holds when the answer left it out: a list for a list shape, else an empty text."""

    return [] if shape in _LIST_SHAPES else ""


def _map_headings() -> dict[str, str]:
    headings = {heading.casefold(): field for heading, field in frame_record.FORMER_HEADINGS.items()}
    headings.update({heading.casefold(): field for field, (heading, _) in frame_record.FRAME_SECTIONS.items()})

    return headings


# Heading -> field, read once
_HEADING_FIELDS = _map_headings()
# A remark in parentheses after a heading's name: "Screen (Revised)", "Edits (Prompt text correction)"
_HEADING_REMARK = re.compile(r"\s*\([^()]*\)\s*$")


def drop_heading_remark(heading: str) -> str:
    """A heading without the remark in parentheses after it, when what stands before the remark is a section's name: a
    repair wrote "## Screen (Revised)" and "## Edits (Prompt text correction)", and its batch's whole answer was refused
    for them, blocking 11 frames under zero repair (Lecture 7, frames 57-68, DEFECTS 559). A heading that is no section's
    name with its remark taken off stays as written, and is refused by name."""

    name = _HEADING_REMARK.sub("", heading).strip()
    known = {heading.casefold() for heading in (*_HEADING_FIELDS, *_ADDITION_HEADINGS,
                                                frame_record.FRAME_DATA_HEADING)}

    return name if name != heading and name.casefold() in known else heading


def is_cut_off_question(question: str) -> bool:
    """Whether an open question only says the speech broke off (a frame's last words, which the next frame's words
    continue): the instructions say such a sentence is no question, and models raised it all the same, three times in
    one run; the program drops it rather than ask a frame again for it."""

    return bool(_CUT_OFF.search(question) and _SPEECH_WORDS.search(question))


# A note the worker adds about its own reading of a text: "(misread by OCR ...)", "(Corrected from `...`)"
_READING_NOTE = re.compile(r"\s*\((?:misread|corrected from|correction|read as|ocr|the text reader|the reader|note typo|typo)\b[^)]*\)",
                           re.IGNORECASE)


def _strip_note_spans(line: str) -> str:
    """The line without each bracketed reading note, a note's own quoted or code text included."""

    while True:
        match = re.search(r"\s*\((?:misread|corrected from|correction|read as|ocr|the text reader|the reader|note typo|typo)\b",
                          line, re.IGNORECASE)
        if not match:
            return line
        depth, end = 0, len(line)
        for index in range(match.start(), len(line)):
            if line[index] == "(":
                depth += 1
            elif line[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        line = line[:match.start()] + line[end:]


# A take-away that opens with quoted words and an arrow: `"<words>" → <part>: <what it tells>`
_TAKE_AWAY_LINE = re.compile(r'^(?P<lead>\W*)"(?P<quote>[^"]+)"\s*(?:→|->)\s*(?P<rest>.*)$', re.DOTALL)
_NOT_WORD = re.compile(r"[^\w ]+")
# Fewer words than this are too short to tell where they were said ("the backoff")
TAKE_AWAY_MIN_QUOTE_WORDS = 4
# A take-away naming a part nobody mentions: `- the text "<words>" → not mentioned: <what it tells>`
_NOT_MENTIONED_LINE = re.compile(r'^(?P<lead>\W*)(?P<part>[^"→]*"(?P<quote>[^"]+)"[^→]*?)\s*(?:→|->)\s*not mentioned:\s*(?P<rest>.*)$',
                                 re.DOTALL)
# A spoken sentence says a screen text in other words when it shares this many of its content words
TAKE_AWAY_SHARED_WORDS = 2
TAKE_AWAY_SPOKEN_WORDS = 20
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# "44kb" is 44 and kb: a slide's "~44KB" and the speech's "44 kilobytes" share the number
_TOKEN = re.compile(r"[^\W\d_]+|\d+")
_COMMON_WORDS = frozenset("""the and for with that this are was were you our from into have has but not can will its all any
out use used how what when where which who why very must only then than they them there their your about also just
some more most such each been being does did done had here like make made over under onto upon well""".split())


def normalise_speech(text: str) -> str:
    """`text` case folded, its punctuation dropped and its spaces joined: how a quote is found in the speech."""

    return " ".join(_NOT_WORD.sub(" ", text.casefold()).split())


def _find_spoken_match(quote: str,
                       own_speech: str) -> str:
    """The stretch of the frame's speech that says what `quote` says in other words: the sentence sharing the most
    content words with it, at least TAKE_AWAY_SHARED_WORDS, whole when it fits TAKE_AWAY_SPOKEN_WORDS words, else
    that many from a little before the first shared word. Empty when no sentence shares enough."""

    wanted = {token for token in _TOKEN.findall(normalise_speech(quote))
              if (len(token) >= 3 or token.isdigit()) and token not in _COMMON_WORDS}
    best, best_shared = "", 0
    for sentence in _SENTENCE_END.split(own_speech):
        words = sentence.split()
        tokens = [set(_TOKEN.findall(normalise_speech(word))) for word in words]
        shared = len(wanted & set().union(*tokens)) if tokens else 0
        if shared > best_shared:
            first = next(index for index, held in enumerate(tokens) if held & wanted)
            start = 0 if len(words) <= TAKE_AWAY_SPOKEN_WORDS else max(0, first - 3)
            best, best_shared = " ".join(words[start:start + TAKE_AWAY_SPOKEN_WORDS]).strip(" ,;:"), shared

    return best if best_shared >= TAKE_AWAY_SHARED_WORDS else ""


def mark_unsaid_screen_quotes(take_aways: list[str],
                              own_speech: str,
                              screen: str | list[str]) -> list[str]:
    """`take_aways` with each line that quotes the frame's own screen text, unsaid over the frame word for word,
    tied to the speech that says it in other words (`- "<spoken>" → the text "<words>": <what it tells>`), or written as
    the part nobody mentions (`- the text "<words>" → not mentioned: <what it tells>`) when no sentence does. A worker
    reading a slide wrote its bullets where the speaker's words belong, on 4 of 10 frames of one batch and 30 of one
    lecture's 172, with the rule in its instructions; filed as not mentioned alone, 5 frames whose speaker said the
    bullet in other words were named as unmentioned (2026-10-15)."""

    own = normalise_speech(own_speech)
    shown = normalise_speech("\n".join(screen) if isinstance(screen, list) else str(screen or ""))
    filed = []
    for line in take_aways:
        unmentioned = _NOT_MENTIONED_LINE.match(str(line))
        spoken = _find_spoken_match(unmentioned.group("quote"), own_speech) if unmentioned else ""
        if spoken:
            filed.append(f'{unmentioned.group("lead")}"{spoken}" → {unmentioned.group("part")}: {unmentioned.group("rest")}')
            continue
        match = _TAKE_AWAY_LINE.match(str(line))
        quote = normalise_speech(match.group("quote")) if match else ""
        if match and len(quote.split()) >= TAKE_AWAY_MIN_QUOTE_WORDS and quote not in own and quote in shown:
            spoken = _find_spoken_match(match.group("quote"), own_speech)
            line = (f'{match.group("lead")}"{spoken}" → the text "{match.group("quote")}": {match.group("rest")}' if spoken
                    else f'{match.group("lead")}the text "{match.group("quote")}" → not mentioned: {match.group("rest")}')
        filed.append(line)

    return filed


_NOTE_ONLY_LINE = re.compile(r"^\s*[*_]*\(\s*note\b.*\)[*_]*\s*$", re.I)


def strip_reading_notes(screen: str) -> str:
    """A frame's outline without the worker's notes about how it read a text, outside quoted and code texts: a reader
    of the document takes "(misread by OCR)" or "(Corrected from `prnt`)" for part of the screen."""

    lines = []
    for line in screen.split("\n"):
        # A line of its own that is only a note, "*(Note: the prompt is ...)*", is no part of the screen (DEFECTS 282)
        if _NOTE_ONLY_LINE.match(line):
            continue
        parts = _QUOTED_OR_CODE.split(line)
        # The odd parts are the quoted and code texts, kept as written; a note's own quote splits it, so the note is
        # matched on the line with its quotes and code blanked out
        if not _READING_NOTE.search("".join(part if index % 2 == 0 else "x" for index, part in enumerate(parts))):
            lines.append(line)
            continue
        lines.append(_strip_note_spans(line).rstrip())

    return "\n".join(lines)


# A bullet sign written at the start of a quoted line, or of a list item's text: the slide's bullet is the part's
# kind, never its text
_QUOTED_BULLET = re.compile(r'(?:(?<=\s")|(?<=“))[*•·▪●◦]\s+')
# A dash is a bullet sign only on a bullet's or an item's own line: elsewhere a quote can begin with one ("- offset -")
_QUOTED_DASH = re.compile(r'^(?P<head> *[-*+] [\w -]*(?:bullet|item)\b[^"“]*["“])[-–—]\s+', re.M | re.I)
# Any bullet sign after an item's own marker, a dash too: a slide's hyphen bullets were written "- - extracted from Laser
# Scans", an empty item holding a nested one (38c3-openpv frames 46 and 49, DEFECTS 631); the third sign added to this
# rule one at a time, so it now takes every sign a slide draws as a bullet
_ITEM_BULLET = re.compile(r"^(?P<indent> *[-*+] )[-–—*•·▪●◦]\s+", re.M)


def strip_bullet_signs(text: str) -> str:
    """An outline or a markdown list without a bullet sign copied into its text: a worker quoted a slide's third bullet
    as "* <its words>", and its markdown list as "- * <its words>", which renders as a list inside the list; another
    quoted a sub-bullet as "- <its words>"."""

    return _ITEM_BULLET.sub(r"\g<indent>", _QUOTED_DASH.sub(r"\g<head>", _QUOTED_BULLET.sub("", text)))


_FENCE_LINE = re.compile(r"^\s*(```|~~~)")


def open_screen_fences(screen: str) -> str:
    """A frame's outline with each code fence inside it written as parts: every line the fence holds becomes one
    `- line "<text>"` part one level under the part before the fence (the first level when none is). Asked for an
    outline, a worker put a terminal's log and a code slide in fences under `## Screen`, and its batch was blocked under
    zero repair for lines that were not parts (froscon talk 3211, frames 57, 62, 63; DEFECTS 282); every line is kept."""

    kept: list[str] = []
    indent: str | None = None
    margin = 0
    for line in screen.split("\n"):
        if _FENCE_LINE.match(line):
            if indent is None:
                above = next((_OUTLINE_LINE.match(earlier) for earlier in reversed(kept) if _OUTLINE_LINE.match(earlier)), None)
                indent = above["indent"] + " " * _OUTLINE_STEP if above else ""
                # The fence's own indentation is the outline's, never the code's
                margin = len(line) - len(line.lstrip())
            else:
                indent = None
            continue
        if indent is None:
            kept.append(line)
        elif line.strip():
            text = line[margin:] if line[:margin].isspace() else line.lstrip()
            # A double quote inside the line would end its quote early
            kept.append(f'{indent}- line "{text.rstrip().replace(chr(34), chr(39))}"')

    return "\n".join(kept)


# A markdown copy written as outline lines, `- title "Die Software (4)"`, `- text "Bootloader 1KB"`: the screen's text
# in the outline's shape, which phase 4 copies into the document as it stands (froscon 3211 frames 60 and 64, 2 of 377
# public markdown copies, DEFECTS 295)
_OUTLINE_SHAPED_LINE = re.compile(r'^\s*-\s+(?P<kind>[a-z][\w -]{0,30}?)\s+"(?P<text>[^"]*)"\s*(?::.*)?$')
_OUTLINE_SHAPED_SHARE = 0.5
# An outline line among a copy's other lines, named by a part kind an outline uses, words after its quote allowed: a
# slide's title and bullets beside its table (4 of 9 lines) and a link "<address>" provides ..." line were printed in a
# chapter with their kinds and quotes (38c3-openpv frames 57 and 55, DEFECTS 634); over 1,190 public markdown lines these
# 5 were the only such lines
_NAMED_PART_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<kind>title|heading|subtitle|bullet|sub-bullet|item|link|label|text|'
                              r'line|button|caption|box)\s+"(?P<text>[^"]*)"(?P<rest>.*)$')


def _rewrite_named_part_line(line: str) -> str:
    match = _NAMED_PART_LINE.match(line)
    if match is None:
        return line
    if match["kind"] in ("title", "heading", "subtitle"):
        return f"{match['indent']}**{match['text']}**{match['rest']}"

    return f"{match['indent']}- {match['text']}{match['rest']}"


def rewrite_outline_shaped_markdown(markdown: str) -> str:
    """A markdown copy most of whose lines are outline lines, each written as the text it quotes: a title in bold, any
    other part a list item; else each line naming an outline part kind written so, the others as they stand."""

    lines = [line for line in markdown.split("\n") if line.strip()]
    shaped = [_OUTLINE_SHAPED_LINE.match(line) for line in lines]
    if sum(bool(match) for match in shaped) < max(2, _OUTLINE_SHAPED_SHARE * len(lines)):
        return "\n".join(_rewrite_named_part_line(line) for line in markdown.split("\n"))
    written = []
    for line, match in zip(lines, shaped):
        if match is None:
            written.append(line)
        elif match["kind"].strip() in ("title", "heading", "subtitle"):
            written.append(f"**{match['text']}**")
        else:
            written.append(f"- {match['text']}")

    return "\n".join(written)


# A fact or a take-away that only remarks on the notes ("The notes are complete.", "(No new facts.)"): over 5,510
# accepted facts and take-aways of the public runs 6 were such, a second look's and three first passes', every one a
# remark and none knowledge (DEFECTS 330)
_REMARK_ABOUT_NOTES = re.compile(
    r"^\W*(?:the |these |this frame'?s? |all )?(?:notes?|outline|record|existing (?:notes|facts))\b.{0,40}"
    r"\b(?:are|is|were|was|remain|already|cover|contain|complete|correct|accurate)\b"
    r"|^\W*(?:no (?:further |other |new )?(?:changes?|additions?|corrections?|facts?)\b|nothing (?:to add|missing|new)\b)",
    re.IGNORECASE)


# A fact that only reports the speaker saying its clause ("The presenter states that charge transport uses the sensor's
# geometry"): phase 4 wrote such facts into the document as sentences about the talk ("the presentation noted a limit
# of 60 units", DEFECTS 267). Over the public runs' 3,103 accepted facts 53 are such; the clause alone is the fact,
# unless it speaks of the speaker (my, we, their) and would lose its sense
_SPEAKER_SAYS = re.compile(
    r"^(?:the )?(?:presenter|speaker|lecturer|instructor)(?: [A-Z]\w+)? (?:also |further |then |briefly )?"
    r"(?:states|says|explains|notes|mentions|points out|emphasi[sz]es|highlights|clarifies|adds|argues|confirms|remarks|"
    r"observes|suggests|recommends|warns|advises|stresses|describes|reveals|admits|believes|thinks|claims) that (?P<clause>.+)$",
    re.IGNORECASE)
_SPEAKER_WORDS = re.compile(r"\b(?:I|me|my|mine|we|us|our|they|them|their|he|him|his|she|her)\b|\"", re.IGNORECASE)


# A fact that only tells a slide's page or slide number ("The page number updates to 115.") is form, never knowledge:
# over the public runs' 3,223 accepted facts 5 are such, all of release test 1 (DEFECTS 346), each of 12 words or fewer
_PAGE_NUMBER_FACT = re.compile(r"\b(?:page|slide) number\b", re.IGNORECASE)
_PAGE_NUMBER_FACT_WORDS = 12


def is_page_number_fact(fact: str) -> bool:
    """Whether `fact` only tells the slide's page or slide number."""

    return bool(_PAGE_NUMBER_FACT.search(fact)) and len(fact.split()) <= _PAGE_NUMBER_FACT_WORDS


# A fact whose subject is the person talking: phase 4 refuses every sentence with the speaker or the presenter as its
# subject, so such a fact never reaches the document (DEFECTS 644)
_SPEAKER_SUBJECT = re.compile(r"^\W*(?:the\s+)?(?:speaker|presenter|lecturer|moderator)s?\b", re.IGNORECASE)


def is_speaker_subject_fact(fact: str) -> bool:
    """Whether `fact` has the speaker, the presenter, the lecturer or the moderator as its subject."""

    return bool(_SPEAKER_SUBJECT.match(fact))


def unwrap_speaker_fact(entry: str) -> str:
    """`entry` as its clause when it only reports the speaker saying it; `entry` itself otherwise."""

    match = _SPEAKER_SAYS.match(entry.strip())
    if match is None or _SPEAKER_WORDS.search(match.group("clause")):
        return entry
    clause = match.group("clause").strip()

    return clause[0].upper() + clause[1:]


def is_remark_about_notes(entry: str) -> bool:
    """Whether a fact or a take-away only remarks on the notes themselves, with no knowledge in it."""

    return bool(_REMARK_ABOUT_NOTES.search(entry))


# Brackets a line number was taken out of
_EMPTY_BRACKETS = re.compile(r"\s+\(\s*\)")


def strip_line_numbers(screen: str) -> str:
    """A frame's outline without an editor's line numbers. The instructions say to quote code lines in order and
    never write "line 14:"; on a 360p editor the worker wrote them all the same, read differently on every pass, and
    each later step argued which was right. A line left saying only which lines show is dropped."""

    kept = []
    for line in screen.split("\n"):
        match = _OUTLINE_LINE.match(line)
        if not match:
            kept.append(line)
            continue
        parts = _QUOTED_OR_CODE.split(match["text"])
        # The odd parts are the quoted and code texts, kept as written
        # A label's brackets left empty go too: "signature (line 13): `fn main()`" kept "signature ():" (DEFECTS 355)
        text = "".join(part if index % 2 else _EMPTY_BRACKETS.sub("", _LINE_NUMBER.sub("", part))
                       for index, part in enumerate(parts))
        if text == match["text"]:
            kept.append(line)
            continue
        # The separators a label stood between: "block: lines 3 to 7, purple" and "`x = 1`: line 39"
        text = re.sub(r"\s*:\s*,\s*", ": ", re.sub(r"\s{2,}", " ", text))
        text = re.sub(r"\s*[:,]\s*$", "", text).strip().lstrip(":,").strip()
        if _LINE_NUMBER_REST.match(text):
            continue
        kept.append(f'{match["indent"]}- {text}')

    return "\n".join(kept)


def _is_known_heading(heading: str,
                      is_addition: bool) -> bool:
    """Whether a `## ` heading names a section of a frame's notes (or of a repair's addition), as `_read_frame` reads
    it: by name, misspelt by a letter or two, or as the program's own part of the outline."""

    name = heading.casefold()
    additions = {title.casefold() for title in _ADDITION_HEADINGS} if is_addition else set()

    return (name in _HEADING_FIELDS or name in additions or name == frame_record.FRAME_DATA_HEADING.casefold()
            or name.startswith(_PROGRAM_READING_HEADING)
            or bool(difflib.get_close_matches(name, [*_HEADING_FIELDS, *additions], n=1, cutoff=_HEADING_LIKENESS)))


def _join_markdown_subheadings(sections: list[tuple[str, list[str]]],
                               is_addition: bool) -> list[tuple[str, list[str]]]:
    """Each `## ` heading that names no section of the notes and stands inside an `## As markdown` section put back
    into it with its lines: the block is the screen's own markdown, a page's headings included. A README page's
    "## Astronaut Data" and "## Astronaut Analysis Script" refused frame 26 of 38c3 talk 797 (DEFECTS 481), as a
    `# ` title in the block once did (`_join_markdown_headings`). Anywhere else such a heading stays, refused by name."""

    markdown_heading = frame_record.FRAME_SECTIONS["as_markdown"][0].casefold()
    joined: list[tuple[str, list[str]]] = []
    for heading, lines in sections:
        if joined and joined[-1][0].casefold() == markdown_heading and not _is_known_heading(heading, is_addition):
            joined[-1] = (joined[-1][0], [*joined[-1][1], f"## {heading}", *lines])
        else:
            joined.append((heading, lines))

    return joined


def _read_frame(number: int,
                screen_kind: str,
                sections: list[tuple[str, list[str]]],
                is_addition: bool,
                errors: list[str]) -> dict[str, Any]:
    """One `# Frame N` document as a record, or, in the second pass, as the addition of that frame's notes."""

    headings = _HEADING_FIELDS
    sections = _join_markdown_subheadings(sections, is_addition)
    fields: dict[str, Any] = {}
    what_changed = ""
    wrong_parts: list[str] = []
    wrong_diagram_lines: list[str] = []
    wrong_narration: list[str] = []
    edits: list[dict[str, str]] = []
    ignored_headings: list[str] = []
    for heading, lines in sections:
        heading = drop_heading_remark(heading)
        if is_addition and heading.casefold() == frame_record.WHAT_CHANGED_HEADING.casefold():
            what_changed = " ".join(" ".join(_trim(lines)).split())
            continue
        if is_addition and heading.casefold() == frame_record.NOT_ON_FRAME_HEADING.casefold():
            wrong_parts = [] if _is_empty(_trim(lines)) else [
                part for part in (_strip_bullet(line).strip('"“”').strip() for line in _trim(lines)) if part]
            continue
        if is_addition and heading.casefold() == frame_record.WRONG_DIAGRAM_HEADING.casefold():
            wrong_diagram_lines = [] if _is_empty(_trim(lines)) else [
                _strip_bullet(line).strip("`").strip() for line in _trim(lines)
                if line.strip() and not line.strip().startswith("```")]
            continue
        if is_addition and heading.casefold() == frame_record.WRONG_NARRATION_HEADING.casefold():
            wrong_narration = [] if _is_empty(_trim(lines)) else [
                sentence for sentence in (_strip_bullet(line).strip() for line in _trim(lines)) if sentence]
            continue
        if is_addition and heading.casefold() == frame_record.EDITS_HEADING.casefold():
            # A text section named as the thing to replace is that section's new text, never a refused answer: "replace
            # Speech visual alignment: <text>" was written twice in a row, and the batch's repair was refused for it
            # (release test 3, DEFECTS 454; an outline's lines got ids for the same reason, DEFECTS 420)
            line_edits = []
            for line in _trim(lines):
                named = _EDIT_SECTION_LINE.match(_strip_bullet(line).strip())
                if named and named["heading"].casefold() in _TEXT_SECTION_FIELDS:
                    fields[_TEXT_SECTION_FIELDS[named["heading"].casefold()]] = " ".join(named["text"].split())
                else:
                    line_edits.append(line)
            edits = [] if _is_empty(line_edits) else _read_edits(line_edits, errors)
            continue
        if heading.casefold() == frame_record.FRAME_DATA_HEADING.casefold():
            continue
        field = headings.get(heading.casefold())
        # The program's own part of the outline, the text reader's lines it adds, written back as a heading of its own
        # with a line corrected: read into the outline, never refused (Lecture 9 frames-009-016-second-pass blocked
        # its batch on it, 2026-10-17)
        if field is None and heading.casefold().startswith(_PROGRAM_READING_HEADING):
            field = "screen"
        if field is None:
            # A heading misspelled by a letter or two is read as the one it misspells, never refused: a frame's whole
            # notes were asked again for "## Take awies".
            close = difflib.get_close_matches(heading.casefold(), list(headings), n=1, cutoff=_HEADING_LIKENESS)
            field = headings[close[0]] if close else None
        if field is None and is_addition:
            # A repair's section the reader cannot place is set aside and named on the console, never the reason its
            # batch's whole answer is refused, as an edit line in no edit's shape is (DEFECTS 559: two remarked
            # headings blocked 11 frames under zero repair)
            ignored_headings.append(heading)
            continue
        if field is None:
            known = [name for name, _ in frame_record.FRAME_SECTIONS.values()]
            known = [*_ADDITION_HEADINGS, *known] if is_addition else known
            errors.append(f'"## {heading}" is not a section of a frame\'s notes; the sections are '
                          + ", ".join(f'"## {name}"' for name in known))
            continue
        section = _read_section(field, lines, errors)
        held = fields.get(field)
        if not held:
            fields[field] = section
        elif isinstance(held, list):
            fields[field] = held + section
        else:
            fields[field] = f"{held}\n{section}".strip()
    if isinstance(fields.get("screen"), str):
        fields["screen"] = strip_bullet_signs(strip_reading_notes(strip_line_numbers(open_screen_fences(fields["screen"]))))
    if isinstance(fields.get("as_markdown"), str):
        fields["as_markdown"] = strip_bullet_signs(rewrite_outline_shaped_markdown(fields["as_markdown"]))
    # A question that only says the speech broke off is dropped: the next frame's words continue it
    if isinstance(fields.get("open_questions"), list):
        fields["open_questions"] = [question for question in fields["open_questions"] if not is_cut_off_question(question)]
    if not is_addition:
        return {"frame": number, "screen_kind": screen_kind,
                **{field: fields.get(field, _build_empty_value(shape))
                   for field, (_, shape) in frame_record.FRAME_SECTIONS.items()}}
    edits += [{"action": IGNORED_EDIT, "id": "", "text": f"## {heading}"} for heading in ignored_headings]
    addition: dict[str, Any] = dict(zip(_ADDITION_KEYS, (number, what_changed, screen_kind, wrong_parts,
                                                         wrong_diagram_lines, wrong_narration, edits),
                                                     strict=True))
    for field, (_, shape) in frame_record.FRAME_SECTIONS.items():
        name = f"added_{field}" if shape in _LIST_SHAPES else field
        addition[name] = fields.get(field, _build_empty_value(shape))

    return addition


# A line of the repair's `## Edits`: "- replace F158.2: <text>", "- remove T158.1"
# The action of an edit line in no edit's shape: kept with its text so the console can name it
IGNORED_EDIT = "ignored"
# The action that adds a part to the outline inside the part a line names: with only replace and remove, a repair that
# found a part missing could add it only by writing the whole outline again, and one that wrote "add under [O126.2]:
# line ..." had its corrections ignored (Lecture 7, DEFECTS 572)
ADD_UNDER_EDIT = "add under"
_EDIT_TEXT_ID_IN_FRONT = re.compile(r"^\s*\[[FTOQ]\d+\.\d+\]\s*")
# A line with the id and the new text and no action word is a replace: "- [O44.2] y-axis: vertical line on the left"
# (the worker listed corrected outline lines under its edits, and the whole answer was refused for it, DEFECTS 455);
# the id may stand in backticks, as a repair copies it from the outline it was shown (DEFECTS 572)
_EDIT_LINE = re.compile(r"^(?:(?P<action>replace|remove|add\s+under)\s+)?`?\[?(?P<id>[FTOQ]\d+\.\d+)\]?`?\s*:?\s*"
                        r"(?P<text>.*)$", re.IGNORECASE | re.DOTALL)
# ... and a section whose text the repair writes whole, named in place of an id: "- replace Speech visual alignment: <text>"
_EDIT_SECTION_LINE = re.compile(r"^replace\s+(?P<heading>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<text>\S.*)$", re.IGNORECASE | re.DOTALL)
_TEXT_SECTION_FIELDS = {heading.casefold(): field for field, (heading, shape) in frame_record.FRAME_SECTIONS.items()
                        if shape == frame_record.TEXT_SHAPE}
# The id the repair request shows in front of a fact, a take-away or an open question: the frame, then the line's place
# in its section. With no id on its open questions, a repair told to take one out wrote "remove O19.10", an outline
# line's id, was refused, and the question stayed beside the fact that answered it (38c3 talk 797, DEFECTS 480)
LINE_ID_PREFIXES = {"facts": "F", "take_aways": "T", "open_questions": "Q"}
# ... and in front of each line of the outline, the part the line writes: with no id there, a repair correcting one
# word of an outline wrote "replace screen: <line>" and was refused, the outline changing only when written whole
# again (release test 2's batch 8-13, DEFECTS 420)
SCREEN_LINE_PREFIX = "O"


# A fact whose subject is the slide, the screen or the talk ("The opening content slide is titled ...") tells the
# recording, not the subject: such facts reached published chapters as sentences about the slides (DEFECTS 267)
_SLIDE_SUBJECT = re.compile(r"^\W*(?:the|this|a)\s+(?:[\w-]+\s+){0,2}?(?:slides?|screens?|presentation|talk|frame|photo|"
                            r"photograph|window|picture)\b", re.IGNORECASE)


def list_slide_subject_facts(record: dict) -> list[tuple[str, str]]:
    """Each fact of `record` whose subject is the slide, the screen or the talk, with the id the repair edits it by."""

    frame = int(record.get("frame") or 0)

    return [(build_line_id("facts", frame, place), str(fact)) for place, fact in enumerate(record.get("facts") or [], start=1)
            if _SLIDE_SUBJECT.match(str(fact))]


# A sentence said over a frame that its notes carry little of: of its content words (5 letters or more, and numbers), a
# third or fewer stand in any of the frame's notes. A critique answered "Nothing to criticise" on four of twelve
# batches while the speech held facts no note had (DEFECTS 426); the measure flagged every frame the audit found short
# (23, 27, 31, 32, 42, 47, 53, 62, 64, 66, 67 of release test 2's talk) and 196 of 1,062 public frames
_SPEECH_WORD = re.compile(r"[A-Za-zÄÖÜäöüß_]{5,}|\d+(?:[.,]\d+)?")
_SPEECH_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SPEECH_SPEAKER = re.compile(r"^Speaker \w+:\s*", re.MULTILINE)
_THINLY_NOTED_MIN_WORDS = 6
_THINLY_NOTED_SHARE = 1 / 3
_THINLY_NOTED_LIMIT = 2
_NOTE_FIELDS = ("facts", "take_aways", "open_questions", "questions_asked")
_NOTE_TEXTS = ("what_happens", "speech_visual_alignment", "screen", "as_markdown")


def list_thinly_noted_sentences(record: dict,
                                speech: str) -> list[str]:
    """The sentences of `speech` (what was said over the frame) of `_THINLY_NOTED_MIN_WORDS` content words or more of
    which at most `_THINLY_NOTED_SHARE` stand in the frame's notes, the longest `_THINLY_NOTED_LIMIT`, in the order they
    were said: the facts the speech holds that no note states."""

    held = " ".join([" ".join(str(entry) for entry in record.get(field) or []) for field in _NOTE_FIELDS]
                    + [str(record.get(field) or "") for field in _NOTE_TEXTS])
    stems = {word.casefold()[:5] for word in _SPEECH_WORD.findall(held)}
    found = []
    for sentence in _SPEECH_SENTENCE_END.split(_SPEECH_SPEAKER.sub("", speech)):
        words = [word.casefold()[:5] for word in _SPEECH_WORD.findall(sentence)]
        if len(words) >= _THINLY_NOTED_MIN_WORDS and sum(word in stems for word in words) / len(words) <= _THINLY_NOTED_SHARE:
            found.append(sentence.strip())
    longest = sorted(found, key=len, reverse=True)[:_THINLY_NOTED_LIMIT]

    return [sentence for sentence in found if sentence in longest]


def build_line_id(field: str,
                  frame: int,
                  place: int) -> str:
    """The id of a frame's fact or take-away the repair names it by: "F158.2" is frame 158's second fact."""

    return f"{LINE_ID_PREFIXES[field]}{frame}.{place}"


def build_screen_line_id(frame: int,
                         place: int) -> str:
    """The id of a line of a frame's outline the repair names it by: "O158.3" is the third line of frame 158's
    outline, counted over its written lines."""

    return f"{SCREEN_LINE_PREFIX}{frame}.{place}"


def add_screen_line_ids(screen: str,
                        frame: int) -> str:
    """The outline with each part's id written after its dash: "  - [O158.3] title ..."."""

    lines, place = [], 0
    for line in screen.split("\n"):
        match = _OUTLINE_LINE.match(line)
        if match is None:
            lines.append(line)
            continue
        place += 1
        lines.append(f'{match.group("indent")}- [{build_screen_line_id(frame, place)}] {match.group("text")}')

    return "\n".join(lines)


def _read_edits(lines: list[str],
                errors: list[str]) -> list[dict[str, str]]:
    """The repair's edits, each an action, the id of the line it changes and, for a replace, the new text; a line in
    no edit's shape is refused by name."""

    edits = []
    for line in lines:
        text = _strip_bullet(line).strip()
        if not text:
            continue
        match = _EDIT_LINE.match(text)
        action = " ".join((match["action"] or "replace").casefold().split()) if match else ""
        if not match or (action != "remove" and not (match["text"] or "").strip()):
            # A line in no edit's shape is ignored and named on the console, never the reason a whole answer is
            # refused: the repair's other edits and additions stand. A refused answer cost three attempts and blocked a
            # batch for an edit line written without its action, and another for a section named in place of an id
            # (DEFECTS 420, 454, 455)
            edits.append({"action": IGNORED_EDIT, "id": "", "text": text})
            continue
        # The new text never starts with the line's id again: "replace T40.1: [T40.1] <text>" wrote it into the notes
        edits.append({"action": action, "id": match["id"].upper(),
                      "text": " ".join(_EDIT_TEXT_ID_IN_FRONT.sub("", match["text"] or "").split())})

    return edits


_FRAME_HEADINGS = frozenset(heading.casefold() for heading, _ in frame_record.FRAME_SECTIONS.values())


def _read_batch(sections: list[tuple[str, list[str]]],
                errors: list[str]) -> dict[str, Any]:
    known = {**frame_record.BATCH_SECTIONS, **frame_record.BATCH_FOLLOW_UP_SECTIONS}
    headings = ({heading.casefold(): field for field, heading in known.items()}
                | {alias.casefold(): field for alias, field in frame_record.BATCH_HEADING_ALIASES.items()})
    batch: dict[str, Any] = {field: "" for field in frame_record.BATCH_SECTIONS}
    for heading, lines in sections:
        if heading.casefold() == frame_record.BATCH_DATA_HEADING.casefold():
            continue
        field = headings.get(heading.casefold())
        # An empty "## Feedback" is the worker saying it has no report: "(none)" there refused a whole batch of ten
        # frames whose notes were whole (2026-10-05). A report with content is still its own `# Feedback` document
        if field is None and heading.casefold() == frame_record.FEEDBACK_TITLE.casefold() and _is_empty(_trim(lines)):
            continue
        # The open-questions step's worksheet of decided candidates: its place to settle each once (DEFECTS 641)
        if field is None and heading.casefold() == frame_record.CONSIDERED_HEADING.casefold():
            continue
        # A frame's own section under the batch ("## Tags"): the batch keeps nothing of it, and refusing it refused every
        # frame of a batch whose notes were whole (2026-10-05); a frame that lost its own section to it is refused for it
        if field is None and heading.casefold() in _FRAME_HEADINGS:
            continue
        if field is None:
            errors.append(f'"# {frame_record.BATCH_TITLE}": "## {heading}" is not one of its sections; they are '
                          + ", ".join(f'"## {name}"' for name in known.values()))
            continue
        body = _trim(lines)
        if field in frame_record.BATCH_FOLLOW_UP_SECTIONS:
            batch[field] = [] if _is_empty(body) else _read_batch_entries(body)
            if field == "open_questions":
                # Judged by the question alone: the reason a question stays open, after the arrow, may well say the
                # speech cuts off, and the question was then dropped and the answer refused for losing it
                batch[field] = [question for question in batch[field]
                                if not is_cut_off_question(str(question).split(frame_record.ANSWER_SEPARATOR)[0])]
        else:
            batch[field] = "" if _is_empty(body) else " ".join(" ".join(body).split())

    return batch


def _read_batch_entries(body: list[str]) -> list[str]:
    """The entries of a batch's list section, each a top-level bullet as a frame's list reads one (`-`, `*`, `+`,
    `1.`, `1)`); a line that continues an entry is joined to it. A list with no bullet at all is one entry per line."""

    if not any(_BULLET.match(line) for line in body):
        return [entry for entry in _read_list(body) if not _is_empty([entry])]
    entries: list[str] = []
    for line in body:
        bullet = _BULLET.match(line)
        if bullet and len(bullet.group("indent")) < _OUTLINE_STEP:
            entries.append(bullet.group("text").strip())
        elif line.strip() and entries:
            entries[-1] = f"{entries[-1]} {line.strip()}"

    # A model writes the empty mark as a bullet too ("- (none)"); it is no entry
    return [entry for entry in entries if not _is_empty([entry])]


def _render_section_body(field: str,
                         content: Any) -> list[str]:
    """One field's section body, in the shape its parser reads."""

    shape = frame_record.FRAME_SECTIONS[field][1]
    if shape == frame_record.LIST_SHAPE:
        return [f"- {' '.join(str(item).split())}" for item in content]
    if shape == frame_record.OUTLINE_SHAPE:
        return str(content).rstrip().splitlines()
    if shape == frame_record.DIAGRAM_SHAPE:
        return [f"{_FENCE}mermaid", mermaid_text_utils.strip_fence(str(content)).strip(), _FENCE]
    if shape == frame_record.MARKDOWN_SHAPE:
        return str(content).rstrip().splitlines()
    if shape == frame_record.TAGS_SHAPE:
        return [", ".join(str(tag).strip() for tag in content)]
    if shape == frame_record.LABELLED_SHAPE:
        main, labels = frame_record.LABELLED_SECTIONS[field]
        lines = []
        for entry in content:
            lines.append(f"- {' '.join(str(entry.get(main) or '').split())}")
            lines += [f"  - {label}: {' '.join(str(entry.get(name) or '').split())}"
                      for label, name in labels.items() if str(entry.get(name) or "").strip()]
        return lines
    if shape != frame_record.TEXT_SHAPE:
        raise ValueError(f"no writer for the section shape {shape!r} of {field}")

    return [" ".join(str(content).split())]


def _render_sections(fields: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for field, (heading, _) in frame_record.FRAME_SECTIONS.items():
        content = fields.get(field)
        if content and str(content).strip():
            lines += ["", f"## {heading}", *_render_section_body(field, content)]

    return lines


def _render_frame_title(number: Any,
                        screen_kind: str,
                        title: str = frame_record.FRAME_TITLE) -> str:
    kind = " ".join(str(screen_kind or "").split())

    return f"# {title} {number}" + (f" · {kind}" if kind else "")


def _refuse_unknown_keys(record: dict[str, Any],
                         known: set[str],
                         what: str) -> None:
    """A field this document has no section for would be dropped from the file in silence: a playbook bug, raised."""

    unknown = sorted(set(record) - known)
    if unknown:
        raise ValueError(f"{what} has no section for {', '.join(unknown)}")


# `- same as frame 15`, `as in Frame 15`: a screen written as a pointer to another frame instead of an outline.
_SCREEN_POINTER = re.compile(r"\b(?:same as|as in|as on|like)\s+frame\s*#?\s*(\d+)", re.IGNORECASE)


def _render_data_lines(pairs: list[tuple[str, Any]]) -> list[str]:
    """`- Label: value` for each value that holds something; a list joined with commas, a text on one line."""

    lines = []
    for label, value in pairs:
        if value is None or value == "" or value == []:
            continue
        shown = ", ".join(str(part) for part in value) if isinstance(value, (list, tuple)) else " ".join(str(value).split())
        lines.append(f"- {label}: {shown}")

    return lines


# An outline's first part naming the frame it is like and what differs: "same as frame 0, but with a panel open"
_POINTER_HEAD = re.compile(r"^\s*-\s*(?:the\s+screen\s+is\s+)?(?:the\s+)?same\s+as\s+frame\s*#?\s*(\d+)\b[,;:]?\s*"
                           r"(?:but|except|plus|and)?\s*(.*)$", re.IGNORECASE)


def split_pointer_head(screen: str) -> tuple[int, str, list[str]] | None:
    """An outline that opens on "same as frame N, but <difference>" and then outlines that difference: N, the
    difference's words, and the lines after the first; None for any other outline. Two board frames each opened a
    ticket's panel over frame 0's board and were outlined that way, refused for pointing (board recording, 2026-10-22)."""

    lines = str(screen or "").splitlines()
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None or '"' in lines[first]:
        return None
    match = _POINTER_HEAD.match(lines[first])
    rest = [line for line in lines[first + 1:] if line.strip()]
    if not match or not rest:
        return None

    return int(match.group(1)), match.group(2).strip(" .,;:"), rest


# One part of an outline described as another frame's: "- application header: same as Frame 1"
_POINTER_PART = re.compile(r"^(\s*)-\s*([^:\"]+?)\s*:\s*(?:the\s+)?same\s+as\s+(?:in\s+|on\s+)?frame\s*#?\s*(\d+)\s*\.?\s*$",
                           re.IGNORECASE)


# "(same as frame 3)" at the end of a part that still says what the part is, in its own words
# "(same as frame 24, showing the legend)": the pointer goes, the words after it stay
_POINTER_WITH_WORDS = re.compile(r"\((?:the\s+)?same\s+as\s+(?:in\s+|on\s+)?frame\s*#?\s*\d+\s*,\s*([^()]+)\)", re.IGNORECASE)
_POINTER_ASIDE = re.compile(r"(?<=\S)\s*\((?:the\s+)?same\s+as\s+(?:in\s+|on\s+)?frame\s*#?\s*\d+\)(?=\s*[.,;]?\s*$)",
                            re.IGNORECASE)


# The part's last clause, after a comma or a semicolon, naming another frame however it is worded ("identical to frame
# 23", "as on frame 4"), outside quotes: the part said what it is before it (39c3 Photon Science talk, frame 25, after
# "(same as Frame 22)" was taken out the run before; worded case by case, the pointer came back in other words)
_CLOSING_POINTER = re.compile(r"\s*[,;]\s*[^,;\"]*\bframes?\s*#?\s*\d+[^,;\"]*$", re.IGNORECASE)


def _drop_closing_pointer(line: str) -> str:
    """`line` without a closing clause naming another frame, when the part still says something before it."""

    stripped = _CLOSING_POINTER.sub("", line)

    return stripped if re.search(r"\w", stripped.split(":", 1)[-1]) else line


def drop_pointer_asides(screen: str) -> str:
    """`screen` with each part's closing "(same as frame N)" taken out, the part's own words kept: an outline wrote
    "left side: the functions ring (same as Frame 3)" of a frame showing the ring beside its steps, and was refused for
    pointing, though the part named what it is (pdf-figures-NIST.CSWP.29). A part that is only the pointer keeps it, for
    `fill_pointer_parts` and the check, unless its contents are listed under it."""

    lines = str(screen or "").splitlines()
    kept = []
    for index, line in enumerate(lines):
        # "(same as frame 24, showing the diagram)" keeps its own words; a part whose contents are listed under it
        # drops a pointer standing alone too: an outline wrote "diagram: (same as Frame 22)" above every label of the
        # diagram, quoted, and was refused for pointing (39c3 Photon Science talk)
        stripped = _drop_closing_pointer(_POINTER_ASIDE.sub("", _POINTER_WITH_WORDS.sub(r"(\1)", line)))
        depth = len(line) - len(line.lstrip())
        has_parts_under = index + 1 < len(lines) and len(lines[index + 1]) - len(lines[index + 1].lstrip()) > depth
        is_named = bool(re.search(r"\w", stripped.split(":", 1)[-1]))
        kept.append(stripped if is_named or (has_parts_under and stripped != line) else line)

    return "\n".join(kept)


def _read_part_name(line: str) -> str:
    return " ".join(line.strip().lstrip("-").split(":", 1)[0].split()).casefold()


def fill_pointer_parts(screen: str,
                       outlines: dict[int, str]) -> str:
    """`screen` with each part written "<part>: same as frame N" replaced by frame N's part of that name and the parts
    nested under it, at this part's depth, when `outlines` holds frame N's outline and it has such a part; any other
    line kept. A board frame's outline described its header as frame 1's, refused for pointing (board recording,
    2026-10-22)."""

    filled = []
    for line in str(screen or "").splitlines():
        match = _POINTER_PART.match(line)
        source = outlines.get(int(match.group(3))) if match else None
        lines = source.splitlines() if source else []
        start = next((index for index, other in enumerate(lines) if other.strip().startswith("-")
                      and _read_part_name(other) == " ".join(match.group(2).split()).casefold()), None) if lines else None
        if start is None:
            filled.append(line)
            continue
        depth = len(lines[start]) - len(lines[start].lstrip())
        end = next((index for index in range(start + 1, len(lines))
                    if len(lines[index]) - len(lines[index].lstrip()) <= depth), len(lines))
        filled += [match.group(1) + other[depth:] for other in lines[start:end]]

    return "\n".join(filled)


def read_pointed_frame(screen: str) -> int | None:
    """The frame number a `## Screen` that only points to another frame names, or None when it is an outline."""

    # Words inside quotes are the screen's own text ("Same as frame 5, but faster" is a slide's title), never a pointer
    unquoted = re.sub(r'"[^"]*"', '""', screen)
    if not is_screen_pointer(unquoted):
        return None
    match = next((found for line in unquoted.splitlines() if (found := _SCREEN_POINTER.search(line))), None)

    return int(match.group(1)) if match else None


def is_screen_pointer(screen: str) -> bool:
    """Whether a `## Screen` says only that the screen is the one of another frame."""

    lines = [line.strip(" -\t") for line in screen.splitlines() if line.strip()]

    return bool(lines) and len(lines) <= 2 and any(_SCREEN_POINTER.search(line) for line in lines)


def read_screen_parts(screen: str) -> ScreenOutlineResult:
    """The screen's outline as `{"depth", "text"}` parts, and every line that breaks the outline's shape: not a
    `- ` part, indented by other than two spaces a level, or deeper than one level below the part above it."""

    parts: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, line in enumerate(str(screen or "").splitlines(), start=1):
        if not line.strip():
            continue
        match = _OUTLINE_LINE.match(line.replace("\t", " " * _OUTLINE_STEP))
        if match is None:
            problems.append(f'line {number}, "{line.strip()}", is not a part: start it with "- "')
            continue
        indent = len(match.group("indent"))
        depth = indent // _OUTLINE_STEP
        above = parts[-1].depth if parts else -1
        if indent % _OUTLINE_STEP or depth > above + 1:
            problems.append(f'line {number}, "{match.group("text")}", is indented {indent} spaces: a part sits two '
                            f"spaces deeper than the part it is inside, and the first part is not indented")
            continue
        parts.append(ScreenPartItem(depth, match.group("text").strip()))

    return ScreenOutlineResult(parts, problems)


def normalise_screen_indentation(screen: str) -> str:
    """The outline with two spaces a level, whatever width the model indented by: each deeper indentation is one level
    deeper than the part above. A line that is not a `- ` part is left as it is, for the outline check to name."""

    widths: list[int] = []
    lines: list[str] = []
    rows = str(screen or "").splitlines()
    has_parts = any(row.lstrip(" \t").startswith("- ") for row in rows)
    for line in rows:
        expanded = line.replace("\t", " " * _OUTLINE_STEP)
        stripped = expanded.lstrip(" ")
        # A bare "(none)" under parts says nothing more: a step whose slide did not change wrote its step line, then
        # the mark, and the batch was refused for it (lecture-1 frame 104, 2026-10-13)
        if has_parts and stripped.strip().casefold() == frame_record.EMPTY_MARK:
            continue
        if not stripped.startswith("- "):
            lines.append(line)
            continue
        indent = len(expanded) - len(stripped)
        while widths and indent < widths[-1]:
            widths.pop()
        if not widths or indent > widths[-1]:
            widths.append(indent)
        lines.append(" " * (_OUTLINE_STEP * (len(widths) - 1)) + stripped)

    return "\n".join(lines)


def list_screen_quotes(screen: str) -> list[str]:
    """Every text the screen's outline quotes, in reading order."""

    return [first or second for first, second in _QUOTE.findall(str(screen or ""))]


def list_opening_and_recurring_text(screens: list[str]) -> tuple[list[str], list[str]]:
    """The texts the first frames' outlines quote, and the texts quoted on at least half of all frames (three at least):
    a title slide and a slide footer are where a deck names its author, and the agenda step that names the speakers saw
    neither (2026-10-01: a presenter named in every slide's footer, and `speakers: []`)."""

    quotes = [list(dict.fromkeys(list_screen_quotes(screen))) for screen in screens]
    opening = list(dict.fromkeys(quote for frame in quotes[:_OPENING_FRAMES] for quote in frame))
    counts: dict[str, int] = {}
    for frame in quotes:
        for quote in frame:
            counts[quote] = counts.get(quote, 0) + 1
    recurring = [quote for quote, count in counts.items() if count >= max(3, len(screens) / 2)]

    opening_quotes = [quote[:_QUOTE_CHARS] for quote in opening[:_QUOTES_LIMIT]]
    recurring_quotes = [quote[:_QUOTE_CHARS] for quote in recurring[:_QUOTES_LIMIT]]

    return opening_quotes, recurring_quotes


def find_screen_title(screen: str) -> str:
    """The quoted text of the first part the outline names a title, else of the first it names a heading or a
    headline; empty when no part is one. Never the first text on screen: that is a menu or a tab as often as it is a
    title. A title wins over an earlier heading: a wiki's side panel, outlined first with its own heading "Page tree",
    titled every page of a PDF (pdf-01)."""

    named = []
    for part in read_screen_parts(screen).parts:
        label = _QUOTE.sub('""', part.text).split(":", 1)[0]
        quotes = list_screen_quotes(part.text)
        if quotes and _TITLE_WORDS.search(label):
            named.append((label, quotes[0]))

    return next((quote for label, quote in named if _TITLE_WORD.search(label)), named[0][1] if named else "")


# A whole answer wrapped in one fence, as the instructions' own examples are: "```markdown" or "~~~markdown" ... "~~~"
_ANSWER_FENCE = re.compile(r"^(```|~~~)\s*(?:markdown|md)?\s*$", re.IGNORECASE)


def _find_last_answer_block(lines: list[str]) -> list[str] | None:
    """The body of the fenced block that ends `lines` when text stands before it and the block opens with a document's
    `# ` title line; None otherwise (a fence round the whole answer is the caller's case)."""

    if len(lines) < 3 or lines[-1].strip() not in ("```", "~~~"):
        return None
    # Read forward, so a fence line that closes an earlier code block is never taken for the opening: a second look's
    # last frame ended on its `## As markdown` code block, the bare fence closing the frame before's block was taken as
    # the opening, and frames 40 to 52 of the answer were lost (froscon talk 3211)
    opened_at = None
    for index, line in enumerate(lines[:-1]):
        mark = line.strip()[:3]
        if opened_at is None and mark in ("```", "~~~"):
            opened_at = index
        elif opened_at is not None and line.strip() == lines[opened_at].strip()[:3]:
            opened_at = None
    start = (opened_at if opened_at and (opening := _ANSWER_FENCE.match(lines[opened_at].strip()))
             and opening.group(1) == lines[-1].strip() else None)
    if start is None:
        return None
    body = _trim(lines[start + 1:-1])
    title = body[0].strip() if body else ""

    return body if title.startswith("# ") and _is_document_title(title[2:].strip(), is_addition=True) else None


def unwrap_answer_fence(lines: list[str]) -> list[str]:
    """The answer's lines without the fence around the whole of it. Left in, a "~~~" closing line is read as the last
    entry's text: an answers call was refused for a fourth question "(none) ~~~". A phase 4 chapter answer is unwrapped
    the same way."""

    kept = _trim(lines)
    # A reply that deliberates first and then gives its whole answer fenced at its end: that block is the answer. An
    # answers call written from its compacted thinking wrote a draft, "Wait, I must check ...", and its settled answer
    # in "~~~markdown"; read whole, the draft's lines and the fence were refused (Lecture 7 frames 95-105, DEFECTS 568)
    last_block = _find_last_answer_block(kept)
    if last_block is not None:
        return last_block
    opening = _ANSWER_FENCE.match(kept[0].strip()) if kept else None
    if not opening:
        # A closing fence with no opening one: an answer written from a compacted thinking ended its last question
        # with "~~~" (Lecture 9, frames 96-103). A fence that closes a code block of the answer is matched, so kept.
        last = kept[-1].strip() if kept else ""
        fences = sum(line.strip().startswith(last) for line in kept) if last in ("```", "~~~") else 0
        return kept[:-1] if fences % 2 else lines
    body = kept[1:]
    if body and body[-1].strip() == opening.group(1):
        body = body[:-1]

    return body


def _is_document_title(title: str,
                       is_addition: bool) -> bool:
    """Whether `# <title>` opens a document of an answer: a frame, the batch, a report, or a second look's nothing."""

    named = [frame_record.BATCH_TITLE, frame_record.FEEDBACK_TITLE]
    named += [frame_record.NOTHING_TO_CHANGE_TITLE] if is_addition else []
    return bool(_FRAME_TITLE.match(title)) or title.casefold() in [name.casefold() for name in named]


def _join_markdown_headings(documents: list[tuple[str, list[str]]],
                            is_addition: bool) -> tuple[None, list[tuple[str, list[str]]]]:
    """Each `# <title>` line that opens no document and stands inside an `## As markdown` section put back into it: the
    block is the screen's own markdown, a slide's title included ("# What to do now?"), and read as a document of its
    own it refused the frame (38c3-accessibility frame 42, 2026-10-12). Anywhere else such a line stays a document,
    and is refused by name."""

    markdown_heading = frame_record.FRAME_SECTIONS["as_markdown"][0].casefold()
    joined: list[tuple[str, list[str]]] = []
    for title, body in documents:
        sections = [line for line in joined[-1][1] if _SECTION.match(line)] if joined else []
        is_in_markdown = bool(sections) and _clean_heading(_SECTION.match(sections[-1]).group(1)).casefold() == markdown_heading
        if joined and is_in_markdown and not _is_document_title(title, is_addition):
            joined[-1] = (joined[-1][0], [*joined[-1][1], f"# {title}", *body])
        else:
            joined.append((title, body))

    return None, joined


def parse_answer(text: str,
                 is_addition: bool) -> dict[str, Any]:
    """The documents of one answer: `{"frames", "batch", "is_nothing_to_change", "errors"}`. `frames` holds each
    frame's record, or with `is_addition` its addition, for every frame that read cleanly; `batch` the
    `# Batch` document's fields, or None; `errors` every part that could not be read, as `{"frame", "text"}`, the
    frame None when the fault is the answer's rather than one frame's, the text in words the model can act on."""

    lines = [_MISLEVELLED_FRAME_TITLE.sub("# ", line)
             for line in unwrap_answer_fence(THINKING_BLOCK.sub("", str(text or "")).splitlines())]
    _, documents = _join_markdown_headings(_split_by(lines, _DOCUMENT)[1], is_addition)
    frames: list[dict[str, Any]] = []
    batch = None
    is_nothing_to_change = False
    errors: list[dict[str, Any]] = []
    for title, body in documents:
        _, sections = _split_by(body, _SECTION)
        frame_title = _FRAME_TITLE.match(title)
        if frame_title:
            number = int(frame_title.group("number"))
            # A frame written again is the worker's answer started over: its first copy is a draft (one began "wait, no"
            # inside its own text), so the last copy is read and the earlier one, records and errors, is dropped
            # (release test 2, frames 22 to 27, DEFECTS 423)
            frames = [record for record in frames if record["frame"] != number]
            errors = [error for error in errors if error["frame"] != number]
            frame_errors: list[str] = []
            record = _read_frame(number, " ".join((frame_title.group("kind") or "").split()), sections, is_addition,
                                 frame_errors)
            frames += [] if frame_errors else [record]
            errors += [{"frame": number, "text": f"frame {number}: {error}"} for error in frame_errors]
        elif title.casefold() == frame_record.BATCH_TITLE.casefold():
            batch_errors: list[str] = []
            batch = _read_batch(sections, batch_errors)
            errors += [{"frame": None, "text": error} for error in batch_errors]
        elif is_addition and title.casefold() == frame_record.NOTHING_TO_CHANGE_TITLE.casefold():
            is_nothing_to_change = True
        elif title.casefold() == frame_record.FEEDBACK_TITLE.casefold():
            continue                     # a report, read by `read_feedback`, never part of the notes
        else:
            allowed = [f'"# {frame_record.FRAME_TITLE} N · <screen kind>"', f'"# {frame_record.BATCH_TITLE}"']
            allowed += [f'"# {frame_record.NOTHING_TO_CHANGE_TITLE}"'] if is_addition else []
            errors.append({"frame": None, "text": f'"# {title}" is not a document of this answer; an answer is made '
                                                  f"of " + ", ".join(allowed)})
    if not documents:
        # A batch's follow-up answers under "# Batch", a frame's notes under "# Frame N": the answer is told both, since
        # the parser does not know which step asked
        errors.append({"frame": None, "text": f'the answer has no "# {frame_record.FRAME_TITLE} N" or '
                                              f'"# {frame_record.BATCH_TITLE}" heading: write each frame\'s notes under '
                                              f'"# {frame_record.FRAME_TITLE} N · <screen kind>", and the batch\'s '
                                              f'sections under "# {frame_record.BATCH_TITLE}", as the request asks, '
                                              f'each field under its "##" heading'})

    return {"frames": frames, "batch": batch, "is_nothing_to_change": is_nothing_to_change, "errors": errors}


def read_feedback(text: str) -> list[dict[str, str]]:
    """Every `# Feedback` document of an answer, as `{"kind", "summary", "detail"}` from its `## Kind`, `## Summary` and
    `## Detail` sections, read whether or not the rest of the answer could be. A report whose summary is empty or says
    only that there is nothing to report ("None") is left out."""

    lines = [_MISLEVELLED_FRAME_TITLE.sub("# ", line)
             for line in unwrap_answer_fence(THINKING_BLOCK.sub("", str(text or "")).splitlines())]
    _, documents = _split_by(lines, _DOCUMENT)
    reports = []
    for title, body in documents:
        if title.casefold() != frame_record.FEEDBACK_TITLE.casefold():
            continue
        _, sections = _split_by(body, _SECTION)
        fields = {}
        for heading, section in sections:
            # A value written on the heading line, `## Summary: <text>`, is that section's text: a report written so
            # was dropped unseen (lecture-1 frames 87-88, 2026-10-13)
            name, _, inline = heading.partition(":")
            fields[name.strip().casefold()] = "\n".join([inline.strip(), *_trim(section)]).strip()
        if not feedback_report_utils.is_empty_report(fields.get("summary", "")):
            reports.append({"kind": fields.get("kind", "").strip("`* ").lower() or "other", "summary": fields["summary"],
                            "detail": fields.get("detail", "")})

    return reports


def render_frame_document(record: dict[str, Any],
                          title: str = frame_record.FRAME_TITLE) -> str:
    """One frame's notes as the document the model writes them in, its heading naming it `title` (a PDF's page or
    figure is named so in its stored file). Raises on a field the document has no section for."""

    _refuse_unknown_keys(record, set(frame_record.FRAME_FIELDS), "a frame's notes")
    lines = [_render_frame_title(record.get("frame"), record.get("screen_kind") or "", title), *_render_sections(record)]

    return "\n".join(lines) + "\n"


def render_frame_data(item: dict[str, Any]) -> str:
    """The data the program keeps about a frame beside its notes, as the section `## Frame data`: its time, its frame number and
    file, its change score and boxes, the frames whose notes it was shown, and the words spoken before and over it."""

    pairs = [("Time", item.get("timestamp")), ("Frame number", item.get("frameNumber")), ("File", item.get("path")),
             ("Change score", item.get("score")), ("Crop box", item.get("box")), ("Screen box", item.get("screenBox")),
             ("Repeat of", item.get("repeatOf")), ("Step of", item.get("stepOf")), ("Notes built from", item.get("builtFrom")),
             ("Words before", item.get("transcriptBefore")), ("Words spoken", item.get("transcriptCurrent"))]

    return "\n".join([f"## {frame_record.FRAME_DATA_HEADING}", *_render_data_lines(pairs)]) + "\n"


def render_batch_data(entry: dict[str, Any]) -> str:
    """The data the program keeps about a batch beside its notes, as the section `## Batch data`."""

    pairs = [("Name", entry.get("slug")), ("Frames", entry.get("frames")), ("Start", entry.get("start")),
             ("End", entry.get("end")), ("Source", entry.get("source"))]

    return "\n".join([f"## {frame_record.BATCH_DATA_HEADING}", *_render_data_lines(pairs)]) + "\n"


def render_stored_notes(entry: dict[str, Any]) -> str:
    """A frame's accepted notes as another phase's agent reads them: the frame's document, from the entry the frames
    file keeps, whose own keys (its slug, its time, the frames it was shown) are the program's and not the notes'."""

    return render_frame_document({field: entry[field] for field in frame_record.FRAME_FIELDS if field in entry})


def render_notes_to_review(entry: dict[str, Any],
                           is_numbered: bool = False) -> str:
    """A frame's accepted notes as phase 3's own follow-ups read them: without the program's part of the outline (the
    text reader's lines it added), which is the program's reading, not the worker's to criticise or fix. Shown it, the
    critique called its misread lines mistakes and two fix passes rewrote them under headings of their own, each
    blocking its batch (Lecture 9, 2026-10-17); the program adds the lines again when the notes are written."""

    shown = {**entry, "screen": strip_program_reading(str(entry.get("screen") or ""))}
    # The repair is shown each fact, take-away and line of the outline with the id it edits it by (DEFECTS 357, 420)
    if is_numbered and "frame" in entry:
        shown.update({field: [f"[{build_line_id(field, int(entry['frame']), place)}] {line}"
                              for place, line in enumerate(entry.get(field) or [], start=1)]
                      for field in LINE_ID_PREFIXES})
        shown["screen"] = add_screen_line_ids(shown["screen"], int(entry["frame"]))

    return render_stored_notes(shown)


def strip_program_reading(screen: str) -> str:
    """`screen` without the part the program added from the text reader: its opening line and every line under it."""

    kept, part_indent = [], None
    for line in screen.split("\n"):
        indent = len(line) - len(line.lstrip())
        if part_indent is not None and (indent > part_indent or not line.strip()):
            continue
        part_indent = None
        if _strip_bullet(line).strip().casefold().startswith(_PROGRAM_READING_HEADING):
            part_indent = indent
            continue
        kept.append(line)

    return "\n".join(kept).rstrip()


def render_addition_document(addition: dict[str, Any]) -> str:
    """What one frame's notes gain in the second pass, as the document the model writes it in."""

    added = {f"added_{field}": field for field, (_, shape) in frame_record.FRAME_SECTIONS.items() if shape in _LIST_SHAPES}
    replaced = [field for field, (_, shape) in frame_record.FRAME_SECTIONS.items() if shape not in _LIST_SHAPES]
    _refuse_unknown_keys(addition, {*_ADDITION_KEYS, *added, *replaced}, "an addition")
    fields = {**{field: addition.get(field) for field in replaced},
              **{field: addition.get(name) for name, field in added.items()}}
    lines = [_render_frame_title(addition.get("frame"), addition.get("screen_kind") or "")]
    if str(addition.get("what_changed") or "").strip():
        lines += ["", f"## {frame_record.WHAT_CHANGED_HEADING}", " ".join(str(addition["what_changed"]).split())]
    if addition.get("wrong_parts"):
        lines += ["", f"## {frame_record.NOT_ON_FRAME_HEADING}", *(f'- "{part}"' for part in addition["wrong_parts"])]
    if addition.get("wrong_diagram_lines"):
        lines += ["", f"## {frame_record.WRONG_DIAGRAM_HEADING}", *(f"- {line}" for line in addition["wrong_diagram_lines"])]
    if addition.get("wrong_narration"):
        lines += ["", f"## {frame_record.WRONG_NARRATION_HEADING}", *(f"- {line}" for line in addition["wrong_narration"])]
    if addition.get("edits"):
        lines += ["", f"## {frame_record.EDITS_HEADING}",
                  *(f'- {edit["text"]}' if edit["action"] == IGNORED_EDIT else
                    f'- {edit["action"]} {edit["id"]}' + (f': {edit["text"]}' if edit["action"] == "replace" else "")
                    for edit in addition["edits"])]

    return "\n".join([*lines, *_render_sections(fields)]) + "\n"


def render_batch_document(batch: dict[str, Any]) -> str:
    """A batch's account and how its batch felt, under `# Batch`, then its open and answered questions when the
    batch carries them."""

    _refuse_unknown_keys(batch, {*frame_record.BATCH_SECTIONS, *frame_record.BATCH_FOLLOW_UP_SECTIONS}, "a batch")
    lines = [f"# {frame_record.BATCH_TITLE}"]
    for field, heading in frame_record.BATCH_SECTIONS.items():
        lines += ["", f"## {heading}", " ".join(str(batch.get(field) or "").split()) or frame_record.EMPTY_MARK]
    for field, heading in frame_record.BATCH_FOLLOW_UP_SECTIONS.items():
        if field in batch:
            entries = [" ".join(str(entry).split()) for entry in batch[field] or [] if str(entry).strip()]
            lines += ["", f"## {heading}", *([f"- {entry}" for entry in entries] or [frame_record.EMPTY_MARK])]

    return "\n".join(lines) + "\n"


def render_second_pass_document(answer: dict[str, Any]) -> str:
    """The second pass as it was answered: `# Nothing to change`, or each frame's addition, then the batch's new
    account when it wrote one."""

    _refuse_unknown_keys(answer, {"is_nothing_to_change", "additions", *frame_record.BATCH_SECTIONS}, "a second pass")
    documents = [render_addition_document(addition) for addition in answer.get("additions") or []]
    if answer.get("is_nothing_to_change") and not documents:
        documents = [f"# {frame_record.NOTHING_TO_CHANGE_TITLE}\n"]
    batch = {field: answer.get(field) or "" for field in frame_record.BATCH_SECTIONS}
    if any(str(text).strip() for text in batch.values()):
        documents.append(render_batch_document(batch))

    return "\n".join(documents)


_BLOCK_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_MARKDOWN_FENCE = re.compile(r"^(```|~~~)\s*(?:markdown|md)\s*\n(.*)\n\1\s*$", re.DOTALL | re.IGNORECASE)


def strip_markdown_fence(text: str) -> str:
    """An `## As markdown` block wrapped whole in a fence labelled markdown, unwrapped: such a fence holds no code, its
    content IS markdown, and printed as it stands it reaches the document as a code box of prose (38c3-accessibility
    frame 36: a slide of two paragraphs, 2026-10-12). A fence of a language, or none, is code and stays."""

    match = _MARKDOWN_FENCE.match(text.strip())
    if not match or "```" in match.group(2):
        return text.strip()
    return render_headings_as_bold(match.group(2).strip())


def render_headings_as_bold(text: str) -> str:
    """`text` with each heading line made a bold line: a block's own headings ("# <slide title>") would open a section
    of the document it is copied into (2026-10-13: a slide's "# SUCCEEDING in this CLASS" inside a block)."""

    return _BLOCK_HEADING.sub(lambda heading: f"**{heading.group(1).strip()}**", text)



def drop_answered_questions(still_open: list[str],
                            answered: list[str]) -> list[str]:
    """The questions left open, without one that also stands among the answered ones (`<question> → <answer>`): an
    answers call wrote its one question in both sections, and both reached the batch's notes (Lecture 9, frames
    96-103). Compared by the question's words, letter case and spacing aside."""

    asked = {_normalise_question(entry.split(frame_record.ANSWER_SEPARATOR.strip(), 1)[0]) for entry in answered}

    return [question for question in still_open if _normalise_question(question) not in asked]


def move_restated_answers_to_open(still_open: list[str],
                            answered: list[str]) -> tuple[list[str], list[str]]:
    """The questions left open and the answered ones, with each answer that adds at most two words beyond its own
    question and the words naming where it was found moved back among the open ones: asked why the speech said 60
    units where the screen read 1-63, an answer repeated both and said nothing of why (DEFECTS 214). An answer counts
    so when at most two of its words are new and 60% of them stand in its question; over the 30 answers of 15 public
    runs it names three, and each repeats its question."""

    reopened, kept = [], []
    for entry in answered:
        question, separator, answer = entry.partition(frame_record.ANSWER_SEPARATOR.strip())
        asked = {word.casefold()[:_STEM_LETTERS] for word in _ANSWER_WORD.findall(question)}
        words = [word for word in _ANSWER_WORD.findall(answer) if word.casefold() not in _SOURCE_WORDS
                 and not word.isdigit()]
        added = [word for word in words if word.casefold()[:_STEM_LETTERS] not in asked]
        is_restated = bool(separator and words) and len(added) <= _LEAST_ADDED_WORDS \
            and len(words) - len(added) >= _ASKED_WORD_SHARE * len(words)
        (reopened if is_restated else kept).append(question.strip() if is_restated else entry)

    return still_open + [question for question in reopened if question not in still_open], kept


def _normalise_question(text: str) -> str:
    return " ".join(str(text).casefold().split())
