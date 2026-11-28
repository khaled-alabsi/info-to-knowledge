"""Validate phase 4's writing: the meeting's parts, one chunk's outline, the draft headings, the title, one section part
with its placed and needed frames, the executive summary with the agenda, and the finished document. Pure functions
over disk.

REQUIREMENTS.md R3 asks for things a program can check and things it cannot. Checked here: every
entry of a chunk is placed exactly once, every topic is placed in exactly one chapter, every
paragraph names entries that exist and belong to its topic, no entry is dropped, no section says
again what an earlier one said, and the text holds no clock time, no banned phrase and no sentence
said twice. On trust, stated in the instructions: that a paragraph says what its entries
say, and that related claims sit together.

As everywhere in a chain pipeline, how the model call ENDED is judged first, and a finding there is
an early return.
"""

from __future__ import annotations

import collections
import datetime
import difflib
import json
import re

import cv2
import numpy

from utils import document_heading_utils
from models import output_contract
from models.frame_dispositions_result import FrameDispositionsResult
from models.reading_errors import ReadArgumentError
from models.run_paths import RunPaths
from models.section_judging_context import SectionJudgingContext
from models.text_check_context import TextCheckContext
from models.validation_report import ValidationReport
from services import call_log_service
from services import conversation_log_service
from services import integration_service
from services import phase_file_service
from services import document_file_service
from services import figure_match_service
from services import frame_records_service
from services import text_detection_service
from utils import person_name_utils
from models import pipeline_errors
from utils import document_plan_utils
from utils import frame_region_utils
from utils import document_text_utils
from utils import mermaid_text_utils
from utils import reading_tool_utils
from utils import section_markdown_utils
from validators import chain_response_validator
from validators import document_folder_validator

__all__ = ["describe_open_needed_frames", "validate_parts", "validate_outline", "validate_contents", "validate_section",
           "validate_summary", "validate_executive_summary_draft", "validate_section_links", "validate_document",
           "validate_draft_headings", "validate_chapter_count", "validate_section_images", "validate_table_of_contents",
           "validate_needed_screenshots", "judge_section", "check_text",
           "MOST_PERSONAL_TO_MASK"]

# REQUIREMENTS.md R12: the executive summary draft is three to eight sentences.
_DRAFT_SENTENCES = (3, 8)
# A title opening with one of these says what the document does, not what the meeting establishes: a run's title was
# "Clarifies the branching strategy, ..." though the instructions ask for a claim, never a label
_TITLE_ACT_VERBS = ("clarifies", "explains", "describes", "outlines", "covers", "discusses", "presents", "summarizes",
                    "summarises", "introduces", "details", "reviews", "shows", "demonstrates", "defines", "walks")

# What a mask over a secret covers: one word of 8 or more characters mixing letters and digits, or the asterisks a
# screen shows in a hidden value's place. Labels with counts ("Client secrets (3)") and dates are neither (2026-09-30:
# a mask over the tab labels was counted as a mask on the secret, and the secret stayed readable)
_MASKED_VALUE = re.compile(r"(?<!\S)(?=\S*[A-Za-z])(?=\S*\d)\S{8,}|\*{4,}")
# A degree or a certainty a summary adds to its notes, each word's stem looked for in the notes it cites: over 118
# claims of public runs' summaries these name 5, each a claim its notes do not make; "must", "all" and "never" named as
# many right paraphrases and are left out (temp/defect-505/measure.py, DEFECTS 505)
_DEGREE_STEMS = {"strict": "strict", "guarantee": "guarant", "definitive": "definitiv", "ensure": "ensur",
                 "entire": "entire", "complete": "complete"}
_DEGREE_WORD = re.compile(r"\b(strict|guarantee|definitive|ensure|entire|complete)(?:ly|s|d|ing)?\b", re.IGNORECASE)
# A person's e-mail address is `document_text_utils.PERSONAL_EMAIL`, which phase 4's choice of the notes never set
# aside reads too (DEFECTS 542)
# Any address, for finding the ones a PDF prints itself
_ANY_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A line that only marks lines left out of a block, or a diff's empty line ("+", "-"): no content a block adds
# (DEFECTS 587)
_ELISION_LINE = re.compile(r"(?:\.\.\.|…|\[\.\.\.\]|\[…\]|[+-])")
# An address a narrow column cut short: "<first>.<last>@se..." in a mail client's account tree was read with no host
_CUT_SHORT_EMAIL = re.compile(r"(?<![\w.+/:@-])[\w.+-]+@[\w.-]*(?:\.{2,}|\u2026)")
# A person's account written out: a quoted value after a username's label, or after "as" in a sign-in line
# (2026-10-06: a chapter wrote out the presenter's sign-in account from a note on the login page)
# Only a value in quotes counts: a value in code marks is a field, claim or command name, never one person's account
# (a chapter describing a token's claims, `acr` and `session_state` after "user id", was refused, DEFECTS 704)
_ACCOUNT_NAME = re.compile(r"(?i)\b(?:user\s?(?:name|id)|login\s(?:name|id)|account\sname|(?:logged|signed|logs|signs)\s+in\b[^.\n]{0,40}?\bas)"
                           r"\b[^.\n\"“`]{0,40}?[\"“]([^\"”`\s<>]{3,40})[\"”]")
# A bank's own word for an account kind follows "account" in quotes too ("the main account ("Girokonto")"), so after
# the bare word only a value shaped like an id counts: a digit, an @, a dot or an underscore in it (2026-10-08: a
# product name was refused as a person's account name)
_BARE_ACCOUNT = re.compile(r"(?i)\baccount\b[^.\n\"“`]{0,40}?[\"“]([^\"”`\s<>]{3,40})[\"”]")
_ACCOUNT_ID_SHAPE = re.compile(r"[\d@._]")
# A person's name quoted as a field's value: "the saved advisor name, \"<first> <last>\"". Two capitalised words, each
# with lower-case letters, so a company's "<word> AG" is no person (2026-10-08: a presenter's full name, saved in a demo
# form, was copied into a chapter; the fifth name copied that day, none behind an honorific)
# A name in brackets after "name" too ("the advisor's full name (<first> <last>)")
_QUOTED_PERSON_NAME = re.compile(r"\b(?i:name)\b[^.\n\"“(]{0,40}?[\"“(]([A-ZÄÖÜ][a-zäöüß'’-]+(?:\s[A-ZÄÖÜ][a-zäöüß'’-]+)+)[\"”)]")
# A placeholder the text names instead of a person's account
_PLACEHOLDER_ACCOUNT = re.compile(r"(?i)^(?:your|my|the|a|an|example|test|demo|user|admin)\b|user$")
# A customer's own record written out: the number of a customer, a contract or a policy, with its label beside it
# (2026-10-07: a chapter wrote a demo customer's number and name out of a form, and only e-mail addresses were checked)
_RECORD_NUMBER = re.compile(r"(?i)\b(?:kunden|vertrags|policen|versicherungs)?\s?"
                            r"(?:kundennummer|vertragsnummer|policennummer|beraternummer|customer\s?(?:number|id)|advisor\s?(?:number|id)|"
                            r"contract\s?(?:number|id)|policy\s?(?:number|id)|client\s?(?:number|id))"
                            r"[^.\n]{0,20}?\b(\d[\d .\-/]{5,})")
_LEAST_RECORD_DIGITS = 6
# One record's reference written out: the opaque id a system gives a customer's order or case, under its label
# (2026-10-07: a chapter copied a demo order's 36-character reference out of a table, and only labelled numbers were
# checked, so a reference of letters and digits passed)
_RECORD_REFERENCE = re.compile(r"(?i)\b(?:referenz(?:nummer)?|reference(?:\s?(?:number|id))?|vorgangsnummer|vorgang|"
                               r"auftragsnummer|auftrag|order\s?(?:number|id|reference)|transaction\s?(?:number|id)|case\s?(?:number|id))"
                               r"\b[^.\n\"“`]{0,40}?[\"“`]([^\"”`\s<>]{12,60})[\"”`]")
# A reference of letters only, or digits only, is a word or a number, not an opaque id
_OPAQUE_REFERENCE = re.compile(r"(?=[^\W\d_]*\d)(?=\d*[^\W\d_])[^\W_]{12,60}$")
# A record number as it stands on a screen: eight digits or more in one run, no separator inside. An amount carries a
# comma or a dot, a year four digits, a version a dot; a customer's number is a bare run (2026-10-07: a cut of a form
# carried "4415502703 | <a person's name>" with "Warnings: none", and nothing on a cut looked for a number at all)
_RECORD_DIGITS = re.compile(r"^\d{8,20}$")
# Past this many, the cut is a list of people's records: masked row by row it shows black bars and spends a call a
# row (2026-10-08: a table of test users held 101 names, and a worker masking one row per call ran at a 40-call budget)
MOST_PERSONAL_TO_MASK = 10
_OPEN_QUOTE = "\"“„«'‘"
_CLOSE_QUOTE = "\"”«»'’"
# The word each marked line opens with, by its block kind
_CLAIM_MARKERS = {"decision": "Decision", "action": "Action", "open": "Open", "set_aside": "Set aside"}
# An image whose line holds text before it
_INLINE_IMAGE = re.compile(r"^[^\n]*\S[^\n]*?!\[[^\]]*\]\((cut-\d+)", re.MULTILINE)
# A cut is a fragment of another when this share of its words, at least this many, are in the other: a table's first
# rows measured 95%, a cut adding three sections to a placed one 80%. Two diagrams with the same labels read 100%, so
# the test runs only when the shapes keep the pixels from being compared.
_PART_SHARE = 0.9
_PART_MIN_WORDS = 10
# A block named in a finding is quoted up to this many characters
_QUOTED_TEXT_CHARS = 60
# The document's contents section, a list of links to the headings
_CONTENTS_SECTION = re.compile(rf"^## (?:{'|'.join(map(re.escape, document_heading_utils.list_every('contents')))})\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
# A frame's note id, `F12.3`, and a frame's name, `frame-012`: the frame number in each
_FRAME_OF_NOTE = re.compile(r"^F(\d+)\.")
_FRAME_NUMBER = re.compile(r"^frame-(\d+)$")
# How one placed cut repeats another: the same figure, or a part of it
_SAME_FIGURE = "same"
_PART_OF = "part"
# A figure's description is the figure for whoever cannot see it: shorter than this it names a part or two and stops.
_MIN_DESCRIPTION_WORDS = 25
# A leave_out reason that claims a placed frame already shows the frame
_SHOWN_ELSEWHERE = re.compile(r"\b(?:placed|already shown|shown (?:already|elsewhere|by)|same (?:visual|table|slide|diagram|"
                              r"dashboard|screen|content|information|view)|identical|duplicate|redundant|another frame)\b",
                              re.IGNORECASE)
# ... unless it says the part is written in the text: "redundant" beside "written as a markdown code block" names a
# block the section writes, not a placed frame, and was checked against the placed cuts (Lecture 9 chapter 3)
_WRITTEN_IN_TEXT = re.compile(r"\b(?:written|stated|quoted|typed)\s+(?:as|in)\b|\bmarkdown\b|\bcode block\b", re.IGNORECASE)


def _list_markdown_notes(paths: RunPaths) -> dict[str, str]:
    """The notes phase 3 wrote as a screen's code, table or list in markdown, which the chapter request asks written as
    they stand, `{id: text}`; empty when the notes cannot be merged."""

    try:
        entries = integration_service.build_integration(paths).get("entries", [])
    except (OSError, ValueError, KeyError, pipeline_errors.MissingPhaseInput):
        return {}

    return {entry["id"]: str(entry.get("text") or "") for entry in entries if entry.get("kind") == "markdown"}


# A line of a list: a bullet, or a number with a dot or a bracket
_SOURCE_LIST_LINE = re.compile(r"^\s*(?:[-*+\u2022]|\d{1,3}[.)])\s+\S", re.MULTILINE)


def _list_source_list_ids(paths: RunPaths) -> set[str]:
    """The speech entries whose own text is a list of two items or more (a text-only PDF's paragraph): a list the
    writer copies from them is the source's, whose items may repeat as the source repeats them. A reference list citing
    one work twice was refused as sentences said more than once, four checks in a row (pdf-real-wikipedia-photosynthesis)."""

    try:
        entries = integration_service.build_integration(paths).get("entries", [])
    except (OSError, ValueError, KeyError, pipeline_errors.MissingPhaseInput):
        return set()

    return {entry["id"] for entry in entries if entry.get("kind") == "speech"
            and len(_SOURCE_LIST_LINE.findall(str(entry.get("text") or ""))) >= 2}


def _list_measured_lines(paragraph: dict,
                         markdown_notes: dict[str, str]) -> set[str]:
    """The lines a screen block is measured by against earlier blocks: those of the markdown notes it cites, as the
    chapter request measures them, or its own when it cites none. A writer wrote part of a note the request asked
    written as it stands, and the part, more held than the whole, was refused (lecture-9 chapter 10, 2026-10-21)."""

    cited = [markdown_notes[entry_id] for entry_id in paragraph.get("entry_ids") or [] if entry_id in markdown_notes]
    text = "\n".join(cited) if cited else paragraph.get("text", "")

    return section_markdown_utils.list_block_lines(text)


_CODE_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")


def check_code_words(report: ValidationReport,
                     slug: str,
                     text: str,
                     note_words: frozenset[str]) -> None:
    """A code block is copied as the frame spells it, so each identifier of six characters or more in a fenced block stands
    in the notes or in the section's own sentences. The writer printed "pgench_accounts_3" for the screen's
    "pgbench_accounts_3" in five lines of one chapter and every check passed it; over the public runs' published
    sections 17 of about 2,500 such words were absent from the notes, 15 of them this chapter's (DEFECTS 440)."""

    report.mark_ran("DOCUMENT_CODE_WORD_NOT_IN_NOTES")
    if not note_words:
        return
    # Fences paired line by line: a closing fence is never read as the next block's opening one
    # as CommonMark pairs them: a fence opens on a line of three backticks or more with or without a language, and only a
    # line of backticks alone closes it ("```sql" inside an open block is a line of that block)
    code_lines, prose_lines, is_in_fence = [], [], False
    for line in text.splitlines():
        stripped = line.strip()
        if not is_in_fence and stripped.startswith("```"):
            is_in_fence = True
        elif is_in_fence and stripped.startswith("```") and not stripped.strip("`"):
            is_in_fence = False
        elif is_in_fence:
            code_lines.append(line)
        else:
            prose_lines.append(line)
    known = note_words | set(_CODE_WORD.findall("\n".join(prose_lines)))
    unknown = sorted(set(_CODE_WORD.findall("\n".join(code_lines))) - known)
    if not unknown:
        return
    listed = []
    for word in unknown:
        near = difflib.get_close_matches(word, note_words, n=1, cutoff=0.8)
        listed.append(f"'{word}'" + (f" (the notes spell '{near[0]}')" if near else ""))
    report.add("DOCUMENT_CODE_WORD_NOT_IN_NOTES", f"{slug}: a code block holds word(s) no note spells: {', '.join(listed)}",
               fix="a code block is copied as the frame spells it: write each identifier as the notes spell it, or take out "
                   "a line the notes do not hold")


def check_code_fences(report: ValidationReport,
                      slug: str,
                      text: str) -> None:
    """A code fence that opens while another is still open: the first block was never closed, so the document prints the
    second block's fence line as text inside the first. Chapter 5 of release test 2 wrote "```markdown", a slide's lines
    and "```sql" with no closing line between, in two sections; over the public runs' 293 published sections no other
    section did (DEFECTS 441)."""

    report.mark_ran("DOCUMENT_CODE_FENCE_NOT_CLOSED")
    is_in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if not is_in_fence and stripped.startswith("```"):
            is_in_fence = True
        elif is_in_fence and stripped.startswith("```") and not stripped.strip("`"):
            is_in_fence = False
        elif is_in_fence and stripped.startswith("```"):
            report.add("DOCUMENT_CODE_FENCE_NOT_CLOSED", f"{slug}: a code block opens with `{stripped}` while the block before "
                       "it is still open", fix="close each block with a line of three backticks alone before the next one "
                                              "opens, and write a block's own lines between its two fences")
            return


def _is_screen_block(paragraph: dict,
                     markdown_ids: set[str]) -> bool:
    """A code block or a table, or a list that cites a note holding a screen's markdown: a block copied from the screen,
    whose lines another screen's block may share. A list of the writer's own sentences is prose."""

    kind = paragraph.get("kind")

    return kind in ("code", "table") or (kind == "list" and bool(set(paragraph.get("entry_ids") or []) & markdown_ids))


def _list_entry_ids(paths: RunPaths) -> list[str]:
    """Every entry the document may cite: the merged notes'."""

    return [entry["id"] for entry in integration_service.build_integration(paths).get("entries", [])]


def _read_outline_or_refuse(paths: RunPaths,
                            slug: str) -> tuple[ValidationReport, dict | None]:
    """How the call ended, then the outline it wrote: the report, and the outline, or None when there is none."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report, None
    report.mark_ran("DOCUMENT_OUTLINE_MISSING")
    if not document_file_service.build_outline_path(paths, slug).is_file():
        report.add("DOCUMENT_OUTLINE_MISSING", f"{slug}: no outline was written",
                   fix="answer through the function you were given")

        return report, None

    return report, document_file_service.read_outline(paths, slug)


def _judge_topics(report: ValidationReport,
                  slug: str,
                  outline: dict,
                  known: list[str],
                  kept_ids: list[str]) -> None:
    """The checks every outline shares, on its topics as the program filled them in."""

    topics = [{"title": topic.get("title", ""), "first_entry_id": str(topic.get("first_entry_id") or ""),
               "entry_ids": list(topic.get("entry_ids", []))} for topic in outline.get("topics", [])]
    starts = [topic["first_entry_id"] for topic in topics]
    placed = [entry_id for topic in topics for entry_id in topic["entry_ids"]] + list(outline.get("set_aside_entry_ids", []))

    report.mark_ran("DOCUMENT_UNKNOWN_SOURCE")
    unknown = sorted(set(placed + starts) - set(known))
    if unknown:
        report.add("DOCUMENT_UNKNOWN_SOURCE", f"the outline names id(s) that do not exist: {', '.join(unknown)}",
                   fix="use only the entry ids you were given")

    report.mark_ran("DOCUMENT_ENTRY_TWICE")
    twice = sorted(entry_id for entry_id, count in collections.Counter(placed).items() if count > 1)
    if twice:
        report.add("DOCUMENT_ENTRY_TWICE", f"placed more than once: {', '.join(twice)}",
                   fix="name each entry in `set_aside_entry_ids` once")

    report.mark_ran("DOCUMENT_SET_ASIDE_KEPT_KIND")
    kept = set(kept_ids)
    dropped = [entry_id for entry_id in outline.get("set_aside_entry_ids", []) if entry_id in kept]
    if dropped:
        report.add("DOCUMENT_SET_ASIDE_KEPT_KIND", f"set aside, though a reader must find them: {', '.join(dropped)}",
                   fix="put every `url`, `action_item` and `question` entry in the topic it belongs to")

    report.mark_ran("DOCUMENT_ENTRY_UNCOVERED")
    placed_ids = set(placed)
    uncovered = [entry_id for entry_id in known if entry_id not in placed_ids]
    if uncovered:
        report.add("DOCUMENT_ENTRY_UNCOVERED", f"{len(uncovered)} entry id(s) placed nowhere: {', '.join(uncovered[:20])}",
                   fix="start the first topic at the first entry; set an entry aside only if it is a greeting, filler, or only "
                       "what a screen displayed")

    # The titles and the open points reach the document as they are: they obey what its prose obeys, here, where
    # a retry can still fix them, and not only in the finished document, where nothing can.
    check_text(report, slug, "\n".join([topic["title"] for topic in topics] + list(outline.get("not_established", []))))

    report.mark_ran("DOCUMENT_NO_TOPICS")
    if known and not any(topic["entry_ids"] for topic in topics):
        report.add("DOCUMENT_NO_TOPICS", "the outline has no topic with entries",
                   fix="sort the entries into topics; setting everything aside drops the whole video")


def _check_blank_cuts(paths: RunPaths,
                      slug: str,
                      section: dict,
                      report: ValidationReport) -> None:
    """No placed cut is an empty area: a cut of empty editor space was placed as the figure of a code block
    (2026-10-02)."""

    report.mark_ran("DOCUMENT_IMAGE_BLANK")
    cuts = document_file_service.list_cuts_in_use(document_file_service.read_image_store(paths))
    for image in section.get("images") or []:
        cut_path = paths.output_root / str(image.get("cutFile") or "")
        grey = cv2.imread(str(cut_path), cv2.IMREAD_GRAYSCALE) if image.get("cutFile") and cut_path.is_file() else None
        if grey is not None and float(grey.std()) < frame_region_utils.BLANK_SPREAD:
            # The frame's other cuts still in use are named: a worker cut a frame a third time while a good cut of it
            # stood unplaced (2026-10-02)
            frame = (cuts.get(image.get("cut")) or {}).get("frame")
            others = [cut_id for cut_id, cut in cuts.items() if cut_id != image.get("cut") and cut.get("frame") == frame]
            such_as = f", such as {', '.join(others)} of the same frame" if others else ""
            report.add("DOCUMENT_IMAGE_BLANK", f"{slug}: {image.get('cut')} shows an empty area: no text or drawing on it",
                       fix=f"look at {image.get('cut')}; place a cut that holds the part{such_as}, or cut the frame "
                           f"again around it")


def _find_personal_values(text: str) -> list[str]:
    bare = [value for value in _BARE_ACCOUNT.findall(text) if _ACCOUNT_ID_SHAPE.search(value)]
    bare += _QUOTED_PERSON_NAME.findall(text)
    names = [name for name in _ACCOUNT_NAME.findall(text) + bare if not _PLACEHOLDER_ACCOUNT.search(name)]
    references = [reference for reference in _RECORD_REFERENCE.findall(text)
                  if _OPAQUE_REFERENCE.match(reference)]

    # A record number holds six digits or more: "customer number selection (2.6.22)" names a document's section, and
    # was refused as a customer's number
    numbers = [number.strip() for number in _RECORD_NUMBER.findall(text)
               if sum(character.isdigit() for character in number) >= _LEAST_RECORD_DIGITS]

    return names + references + numbers


def _is_record_number(text: str) -> bool:
    """Is this a bare run of digits a record number: eight or more, and not a date written year, month, day? A folder
    named by its date ("20260722 <name>") was masked as a customer's number."""

    if not _RECORD_DIGITS.match(text):
        return False
    if len(text) == 8:
        try:
            datetime.date(int(text[:4]), int(text[4:6]), int(text[6:]))
            return not 1990 <= int(text[:4]) <= 2100
        except ValueError:
            return True

    return True


# A word of hexadecimal digits holding a letter and a digit, as a memory dump's addresses are ("000094e8")
_HEX_WORD = re.compile(r"^(?=[0-9a-fA-F]*[a-fA-F])(?=[a-fA-F]*\d)[0-9a-fA-F]{6,}$")
_LEAST_HEX_WORDS = 2


def _is_hex_listing(words: list[dict]) -> bool:
    """Does the cut show hexadecimal numbers, two or more? Its runs of digits are then such numbers, never a person's
    record: a memory dump's address "000094e9", read "00009429", was taken for one and masked (DEFECTS 652)."""

    return sum(1 for word in words if _HEX_WORD.match(word["text"].strip())) >= _LEAST_HEX_WORDS


def _find_named_people(words: list[dict]) -> list[dict]:
    """Each person named after an honorific on a cut, as one box over the honorific and the name beside it: a cut of a
    form carried a customer's name under 'Zu versichernde Person erfassen' and only e-mail addresses were looked for
    (2026-10-07)."""

    found = []
    for index, word in enumerate(words):
        if not person_name_utils.HONORIFIC.match(word["text"]):
            continue
        box = list(word["box"])
        height = box[3] - box[1]
        named = []
        for following in words[index + 1:index + 3]:
            after = following["box"]
            # The same line, and no further right than a word's own height: a name stands beside its honorific
            if abs(after[1] - box[1]) > height or not 0 <= after[0] - box[2] <= height * 2:
                break
            if not person_name_utils.PERSON_NAME.match(following["text"]) or following["text"][:1].islower():
                break
            named.append(following["text"])
            box = [min(box[0], after[0]), min(box[1], after[1]), max(box[2], after[2]), max(box[3], after[3])]
        if named:
            found.append({"text": f"{word['text']} {' '.join(named)}", "box": box})

    return found


def _find_listed_people(words: list[dict]) -> list[dict]:
    """Each person listed as "<Last>, <First>", the way a chat title or a file list's author column writes them, as one
    box over the words: two cuts held such names unmasked, and only names behind an honorific or in quotes were looked
    for. A comma between two capitalised words is also how a list of German nouns reads, so a pair counts only when an
    honorific follows the comma ("<Last>, Dr.") or a mark of a guest account follows the pair ("(extern)", "(ext").
    A pair with neither counts when it starts in the column of one that has one: a file list's author column held
    "<Last>, <First>" rows beside "<Last>, Dr. <First>" rows, and only the latter were found."""

    marked, unmarked = person_name_utils.list_listed_names(words)
    # The same column: left edges within a line's height of each other
    in_column = [person for person in unmarked
                 if any(abs(person["box"][0] - other["box"][0]) <= other["box"][3] - other["box"][1] for other in marked)]

    return marked + in_column


def _find_quoted_people(words: list[dict]) -> list[dict]:
    """Each person named in quotes on a cut, `"<First> <Last>"`, as one box over the words: a warning panel quoted a
    person's name, and only names behind an honorific were looked for (2026-10-08)."""

    found = []
    for index, word in enumerate(words[:-1]):
        text = word["text"]
        if not text or text[0] not in _OPEN_QUOTE or not person_name_utils.PERSON_NAME.match(text[1:]) or not text[1:2].isupper():
            continue
        for end in range(index + 1, min(index + 3, len(words))):
            last = words[end]["text"]
            stripped = last.rstrip(".,;:")
            if not stripped or stripped[-1] not in _CLOSE_QUOTE:
                if not person_name_utils.PERSON_NAME.match(last) or not last[:1].isupper():
                    break
                continue
            core = stripped[:-1]
            if person_name_utils.PERSON_NAME.match(core) and core[:1].isupper() and any(ch.islower() for ch in core):
                boxes = [item["box"] for item in words[index:end + 1]]
                found.append({"text": " ".join(item["text"] for item in words[index:end + 1]),
                              "box": [min(box[0] for box in boxes), min(box[1] for box in boxes),
                                      max(box[2] for box in boxes), max(box[3] for box in boxes)]})
            break

    return found


def _check_personal_values_masked(paths: RunPaths,
                                  slug: str,
                                  section: dict,
                                  report: ValidationReport) -> None:
    """No e-mail address and no person's name stays readable on a placed cut: write.md asks for a person's details
    masked, and a cut showing a person's address was accepted with no check looking (2026-09-30)."""

    report.mark_ran("DOCUMENT_IMAGE_PERSONAL_UNMASKED")
    cuts = document_file_service.read_image_store(paths).get("cuts") or {}
    for image in section.get("images") or []:
        cut = cuts.get(image.get("cut")) or {}
        if not cut.get("file"):
            continue
        # Read with no confidence floor: a missed address leaks, a false one costs a mask
        words = text_detection_service.find_words(paths.output_root / str(cut["file"]), min_confidence=0)
        personal_values = ([word for word in words if document_text_utils.PERSONAL_EMAIL.search(word["text"]) or _CUT_SHORT_EMAIL.search(word["text"])]
                           + [word for word in words if _is_record_number(word["text"].strip())
                              and not _is_hex_listing(words)]
                           + _find_named_people(words) + _find_quoted_people(words) + _find_listed_people(words))
        if len(personal_values) > MOST_PERSONAL_TO_MASK:
            report.add("DOCUMENT_IMAGE_PERSONAL_UNMASKED",
                       f"{slug}: {image.get('cut')} shows {len(personal_values)} e-mail address(es), person's name(s) "
                       f"or record number(s): it is a list of people's records",
                       fix=f"take {image.get('cut')} out of the answer, `undo_cut` it and `leave_out` "
                           f"{cut.get('frame')}, and name in the text which columns the list holds and what it is "
                           f"for, never a row's values: masked row by row it shows the reader black bars")
        elif personal_values:
            # The rectangle is given: an address read below the boxes' confidence floor is in no box `look` lists
            # Each with the words read there: told only "an e-mail address or a person's name" at a rectangle that held
            # an eight-digit advisor number, a worker looked for a name the cut did not hold (2026-10-08)
            rectangles = ", ".join(f"{word['box']} {word['text']!r}" for word in personal_values)
            report.add("DOCUMENT_IMAGE_PERSONAL_UNMASKED",
                       f"{slug}: {image.get('cut')} shows {len(personal_values)} e-mail address(es), person's name(s) or "
                       f"record number(s) a reader can read",
                       fix=f"`mask` {image.get('cut')} at each of these rectangles, in the cut's own pixels, the words "
                           f"read there beside each: {rectangles}; then place the cut")


def _check_mostly_masked(paths: RunPaths,
                         slug: str,
                         section: dict,
                         report: ValidationReport) -> None:
    """No placed cut is mostly masks: figures masked over a quarter and more were placed and showed the reader nothing
    (2026-10-01)."""

    report.mark_ran("DOCUMENT_IMAGE_MOSTLY_MASKED")
    cuts = document_file_service.read_image_store(paths).get("cuts") or {}
    for image in section.get("images") or []:
        cut = cuts.get(image.get("cut")) or {}
        areas = [area["rect"] for area in cut.get("maskedAreas") or []]
        frame = cv2.imread(str(paths.output_root / str(cut.get("file") or ""))) if areas and cut.get("file") else None
        if frame is None:
            continue
        share = frame_region_utils.compute_masked_share(frame.shape[1], frame.shape[0], areas)
        if share > frame_region_utils.MOST_MASKED_SHARE:
            report.add("DOCUMENT_IMAGE_MOSTLY_MASKED", f"{slug}: masks hide {share:.0%} of {image.get('cut')}",
                       fix="cut the frame again around the part that holds no secret and place that cut, or `leave_out` "
                           "the frame and state in words what it showed")


def _check_text_cut_off(paths: RunPaths,
                        slug: str,
                        section: dict,
                        report: ValidationReport) -> None:
    """No placed cut has an edge through a line of text: a slide's decision line lost its last words that way, and a
    cut made before `cut` refused such an edge was still offered to place (2026-10-01)."""

    report.mark_ran("DOCUMENT_IMAGE_TEXT_CUT_OFF")
    store = document_file_service.read_image_store(paths)
    cuts, looks = store.get("cuts") or {}, store.get("looks") or {}
    for image in section.get("images") or []:
        cut = cuts.get(image.get("cut")) or {}
        look = looks.get(str(cut.get("frame") or "")) or {}
        rect = cut.get("lookRect")
        crossed = frame_region_utils.list_crossed_words(rect, look.get("words") or []) if rect else []
        if crossed:
            whole = frame_region_utils.expand_to_whole_blocks(rect, look["words"], look["width"], look["height"])
            report.add("DOCUMENT_IMAGE_TEXT_CUT_OFF",
                       f"{slug}: the edge of {image.get('cut')} runs through text ({', '.join(repr(word) for word in crossed[:6])})",
                       fix=f"`cut` {cut.get('frame')} again as {whole} so those lines are whole, and place that cut instead")
            continue
        # The cut tool's own measures of an edge through content, which it warned of and the writer placed past: the
        # right half of a photo, its left edge through 18 marks, went in captioned "a full photograph" (DEFECTS 360)
        sides = [side for side, _ in frame_region_utils.list_lines_cut_at_sides(rect, look.get("words") or [])]
        if sides:
            report.add("DOCUMENT_IMAGE_TEXT_CUT_OFF",
                       f"{slug}: the {', '.join(dict.fromkeys(sides))} edge of {image.get('cut')} runs through a line "
                       f"of text: the cut holds part of it",
                       fix=f"`look` at {image.get('cut')}, then `cut` {cut.get('frame')} again with that edge past what it "
                           f"cuts through if it belongs to the part, or before it if not, and place that cut instead")
            continue
        # Marks an edge crosses are a console note, never a refusal (hard fix): on a dense form every edge a writer tried
        # crossed 10 to 39 marks, and a chapter part spent its 40 calls cutting and undoing one frame (DEFECTS 705), the
        # fifth block of this measure (360, 699)
        gray = _read_frame_gray(paths, str(cut.get("frame") or "")) if rect else None
        marked = [side for side, _ in (frame_region_utils.list_marked_edges(gray, rect) if gray is not None else [])]
        if marked:
            report.note("DOCUMENT_IMAGE_TEXT_CUT_OFF",
                        f"{slug}: the {', '.join(dict.fromkeys(marked))} edge of {image.get('cut')} crosses marks "
                        f"(lines, symbols or letters the text reader may not read); placed as it is")


def _read_frame_gray(paths: RunPaths,
                     frame_slug: str):
    """The stored frame `frame_slug` in grey, as the cut tool looked at it; None when the frames file or the image is
    missing."""

    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    items = phase_file_service.read_phase_file(paths, frames_number).get("items", []) \
        if phase_file_service.has_phase_file(paths, frames_number) else []
    path = next((str(item.get("path") or "") for item in items if item.get("slug") == frame_slug), "")

    return cv2.imread(str(paths.output_root / path), cv2.IMREAD_GRAYSCALE) if path else None


def _compare_cuts(paths: RunPaths,
                  cuts: dict,
                  cut_id: str,
                  other_id: str) -> str | None:
    """Whether cut `cut_id` shows what cut `other_id` shows: _SAME_FIGURE when the two read alike and differ in under
    REPEATED_SAME_WORDS_SHARE of their content pixels, _PART_OF when, of other shapes, the one is a part of the other,
    None otherwise or when either cannot be read. The repeated-figure check and the needed-frame check both decide by it."""

    image = _read_cut_image(paths, cuts, cut_id)
    other = _read_cut_image(paths, cuts, other_id)
    if image is None or other is None:
        return None
    difference = frame_region_utils.compute_content_difference(image, other)
    if difference is not None:
        is_same = (difference < frame_region_utils.REPEATED_SAME_WORDS_SHARE
                   and _is_same_wording(paths, cuts, cut_id, other_id))
        if is_same:
            return _SAME_FIGURE
        # One table cut from two frames scrolled a row apart differed in 71% of its pixels and was placed twice
        # (2026-10-10): moved up or down against each other, their rows line up
        scrolled = frame_region_utils.compute_scrolled_difference(image, other)
        is_scrolled = (scrolled is not None and scrolled < frame_region_utils.REPEATED_SAME_WORDS_SHARE
                       and _is_same_wording(paths, cuts, cut_id, other_id))
        return _PART_OF if is_scrolled else None

    # A schematic's relay cut and a cut of its whole frame read too few words for the word test (DEFECTS 225)
    is_part = frame_region_utils.is_contained(image, other) or _is_part_of(paths, cuts, cut_id, other_id)

    return _PART_OF if is_part else None


def _read_slug_order(slug: str) -> tuple[int, ...]:
    """A section's place in the document, from the numbers in its slug: `heading-001-10` comes after `heading-001-2`,
    which a comparison of the names as text puts the other way round."""

    return tuple(int(number) for number in re.findall(r"\d+", slug))


def _check_repeated_figures(paths: RunPaths,
                            slug: str,
                            section: dict,
                            report: ValidationReport) -> None:
    """No placed cut shows what another placed cut shows: two cuts of one view a second apart were placed as two
    figures with captions claiming a difference neither showed (2026-10-01)."""

    report.mark_ran("DOCUMENT_IMAGE_REPEATED")
    cuts = document_file_service.read_image_store(paths).get("cuts") or {}
    sections_dir = document_file_service.build_sections_dir(paths)
    # Only a section before this one placed a figure earlier: a later section may be a blocked answer's, never
    # accepted, and an accepted part judged again on a resume was refused against it (2026-10-05)
    others = [(other.stem, image["cut"]) for other in sorted(sections_dir.glob("*.md"))
              if _read_slug_order(other.stem) < _read_slug_order(slug)
              for image in document_file_service.read_section(paths, other.stem).get("images") or []]
    placed: list[tuple[str, str]] = []
    for image in section.get("images") or []:
        cut_id = str(image.get("cut") or "")
        for where, earlier_id in [*others, *placed]:
            likeness = _compare_cuts(paths, cuts, cut_id, earlier_id) if earlier_id != cut_id else None
            is_part = likeness == _PART_OF
            if likeness is not None:
                report.add("DOCUMENT_IMAGE_REPEATED", f"{slug}: {cut_id} shows what {earlier_id} ({where}) already shows",
                           fix=_render_repeat_fix(paths, cuts, cut_id, earlier_id, is_part=is_part,
                                                  is_earlier_kept=where != slug))
                break
        placed.append((slug, cut_id))


def _render_repeat_fix(paths: RunPaths,
                       cuts: dict,
                       cut_id: str,
                       earlier_id: str,
                       is_part: bool,
                       is_earlier_kept: bool = False) -> str:
    """What to do about two placed cuts of one figure. When one is a fragment of the other, the fragment (the one with
    fewer words) is named to leave out and the whole to keep, the same in both mirrored findings: told only "place one
    of them", a worker left out the whole table and kept its first rows (2026-10-04). An earlier cut in a section
    written before stays, since this answer cannot change it: the cut of this answer is the one to leave out."""

    if is_earlier_kept:
        return (f"{earlier_id} is placed already under another heading and stays: `leave_out` the frame of {cut_id} "
                f"with the reason that another placed frame shows it, or cut only the part of it that {earlier_id} "
                f"does not show")
    if is_part:
        # The part is the smaller cut by area: by words, a schematic's whole frame, which reads almost none, was named
        # the part of its own relay cut, and the jumpers only the whole frame showed would have been left out (DEFECTS 235)
        image, earlier = _read_cut_image(paths, cuts, cut_id), _read_cut_image(paths, cuts, earlier_id)
        if image is not None and earlier is not None:
            is_cut_smaller = image.shape[0] * image.shape[1] <= earlier.shape[0] * earlier.shape[1]
        else:
            is_cut_smaller = (len(_list_cut_words(paths, cuts, cut_id) or set())
                              <= len(_list_cut_words(paths, cuts, earlier_id) or set()))
        part, whole = (cut_id, earlier_id) if is_cut_smaller else (earlier_id, cut_id)
        return (f"{part} is part of {whole}: keep {whole} and `leave_out` the frame of {part} with the reason that "
                f"another placed frame shows it")

    return (f"place one of them only: `leave_out` the frame of {cut_id} with the reason that another placed frame shows "
            f"it, or cut the part of it that differs")


def _read_frame_dispositions(store: dict,
                             chapter_slug: str) -> FrameDispositionsResult:
    """What the chapter's follow-up recorded for its frames: left out, left out as shown by a placed frame, and
    replaced by a diagram. What the worker is told and what the judge accepts are read here, once."""

    records = [record for record in store.get("leftOut") or [] if record.get("chapter") == chapter_slug]
    # the latest reason given for a frame decides: an earlier "shown by" was kept after the worker gave another reason
    latest_reasons = {str(record.get("frame") or ""): str(record.get("reason") or "") for record in records}
    left_out = set(latest_reasons)
    shown_elsewhere = {frame for frame, reason in latest_reasons.items()
                       if _SHOWN_ELSEWHERE.search(reason) and not _WRITTEN_IN_TEXT.search(reason)}
    replaced = {str(record.get("frame") or "") for record in store.get("diagramReplacements") or []
                if record.get("chapter") == chapter_slug}

    return FrameDispositionsResult(left_out, shown_elsewhere, replaced)


def _list_cut_words(paths: RunPaths,
                    cuts: dict,
                    cut_id: str) -> set[str] | None:
    """The words the text reader finds on the cut; None when the reader failed on it, never the same as no words."""

    file = str((cuts.get(cut_id) or {}).get("file") or "")

    return text_detection_service.read_figure_words(paths.output_root / file) if file else set()


def _is_same_wording(paths: RunPaths,
                     cuts: dict,
                     first_id: str,
                     second_id: str) -> bool:
    """Whether two cuts read alike: at least half the words either holds are in both, or neither holds words. Two
    slides of one deck with the same table layout and other words in every cell matched pixel for pixel at the size the
    figures are compared, and were refused as one figure placed twice (2026-10-04)."""

    return text_detection_service.is_same_wording(_list_cut_words(paths, cuts, first_id),
                                                  _list_cut_words(paths, cuts, second_id))


def _is_part_of(paths: RunPaths,
                cuts: dict,
                part_id: str,
                whole_id: str) -> bool:
    """Whether one cut is a fragment of the other: the words of the one with fewer, at least _PART_MIN_WORDS, are nearly
    all (_PART_SHARE) in the other. A table's first rows, cut from a later frame, were placed beside the whole table
    already placed; their shapes differed, so their pixels were never compared (2026-10-04)."""

    first, second = _list_cut_words(paths, cuts, part_id), _list_cut_words(paths, cuts, whole_id)
    if first is None or second is None:
        return False
    fewer, more = (first, second) if len(first) <= len(second) else (second, first)

    return (len(fewer) >= _PART_MIN_WORDS
            and text_detection_service.count_shared_words(fewer, more) >= _PART_SHARE * len(fewer))


def _read_cut_image(paths: RunPaths,
                    cuts: dict,
                    cut_id: str) -> numpy.ndarray | None:
    file = str((cuts.get(cut_id) or {}).get("file") or "")

    return cv2.imread(str(paths.output_root / file)) if file else None


def _describe_diagram(number: int,
                      diagram: dict) -> str:
    """A section's diagram as a finding names it: its place and its first line."""

    first_line = (str(diagram.get("mermaid") or "").strip().splitlines() or [""])[0]

    return f"diagram {number} ({_shorten_to_first_words(first_line)!r})"


def _read_sections(paths: RunPaths,
                   slugs: list[str]) -> list[dict]:
    """The sections of `slugs` that were written, in order; a slug with no file is passed over."""

    return [document_file_service.read_section(paths, slug) for slug in slugs
            if document_file_service.build_section_path(paths, slug).is_file()]


def _shorten_to_first_words(text: str) -> str:
    """The first words of `text`, to name a block in a finding."""

    return text if len(text) <= _QUOTED_TEXT_CHARS else text[:_QUOTED_TEXT_CHARS].rsplit(" ", 1)[0] + " …"


def _is_hidden_value(text: str) -> bool:
    """A mask covers a value worth hiding: a secret-shaped word, a screen's asterisks or an e-mail address."""

    return bool(_MASKED_VALUE.search(text) or _ANY_EMAIL.search(text))


def _check_secrets_masked(paths: RunPaths,
                          slug: str,
                          section: dict,
                          report: ValidationReport) -> None:
    """A frame phase 3 describes as showing a secret is placed only with a mask on its cut. A client secret too small for
    the text reader was placed unmasked (2026-09-29), so phase 3's own note on the frame is the evidence."""

    report.mark_ran("DOCUMENT_IMAGE_SECRET_UNMASKED")
    images = section.get("images") or []
    if not images:
        return
    cuts = document_file_service.read_image_store(paths).get("cuts") or {}
    notes = {record["slug"]: " ".join(f"{shot.get('part', '')} {shot.get('why', '')}" for shot in record.get("screenshots") or [])
             for record in frame_records_service.read_frame_records(paths)}
    for image in images:
        cut = cuts.get(image.get("cut")) or {}
        note = notes.get(str(cut.get("frame") or ""), "")
        areas = cut.get("maskedAreas") or []
        masked_rects = [area["rect"] for area in areas]
        # Read with no confidence floor: a token's line read at low confidence passed unnamed (2026-09-30)
        cut_words = text_detection_service.find_words(paths.output_root / str(cut.get("file") or ""), min_confidence=0)
        # A label whose value a mask right after it hides shows no secret (2026-10-06: 'Bearer' was named beside its
        # masked token, and the one line truly left was lost among the names) ... or after a scheme word:
        # 'Authorization: Bearer <masked>' reopened an accepted chapter (2026-10-06)
        left = [word["text"] for index, word in enumerate(cut_words)
                if not (document_text_utils.is_credential_label(word["text"])
                        and frame_region_utils.is_label_value_masked(cut_words, index, masked_rects,
                                                                     document_text_utils.is_auth_scheme))]
        readable = document_text_utils.find_readable_secrets(left)
        # A note that names only a token is weak: a cut of a page about tokens needs a mask only when a value shows on it
        # (2026-09-30: a documentation section on "token exchange changes" was held to need a mask it could not have).
        # The cut's own text counts with no note at all (2026-10-02: a settings file's passwords, noted as "the values")
        # Only a credential's key with its value counts with no note: a long value alone matched paths and record ids
        shows_credential_key = bool(document_text_utils.find_credential_keys(left))
        is_secret_noted = document_text_utils.is_secret_shown(note) and (
            document_text_utils.is_secret_named_strongly(note) or bool(cut.get("masks")) or bool(readable))
        if not shows_credential_key and not is_secret_noted:
            continue
        # A mask over a credential's label alone leaves its value readable with nothing to show it (2026-09-30)
        labels = [area["words"] for area in areas
                  if document_text_utils.is_label_without_value(str(area.get("words") or "").split())]
        if labels:
            report.add("DOCUMENT_IMAGE_SECRET_UNMASKED",
                       f"{slug}: {image.get('cut')} is cut from {cut.get('frame')}, which shows a secret, and "
                       f"its masks hide the label {', '.join(repr(text) for text in labels)} but not the value after it",
                       # By words, the label and its value in one mask: a box on a settings file holds most of the
                       # file, and a mask over it blacks out the figure (2026-10-02)
                       fix=f"`cut` {cut.get('frame')} again, `mask` each credential on the new cut by its `words`, the label "
                           f"and the value after it in one mask, and place the new cut instead of {image.get('cut')}")
            continue
        # A credential line masked only as far as its box leaves the value's end and its wrapped line readable, where the
        # reader often cannot read them (2026-09-30)
        wide = [area["rect"] for area in areas if area.get("reachesEdge")]
        short = [area["words"] for area in areas if not area.get("reachesEdge")
                 and document_text_utils.has_credential_label(str(area.get("words") or "").split())
                 and not any(rect[1] <= (area["rect"][1] + area["rect"][3]) / 2 <= rect[3] for rect in wide)]
        if short:
            report.add("DOCUMENT_IMAGE_SECRET_UNMASKED",
                       f"{slug}: {image.get('cut')} is cut from {cut.get('frame')}, which shows a secret, and "
                       f"the mask over {', '.join(repr(_shorten_to_first_words(text)) for text in short)} stops before the value ends",
                       fix=f"`mask` the same box again on {image.get('cut')}, by its box id: a mask on a credential's line "
                           f"now runs to the cut's edge and over its wrapped line. Or `cut` {cut.get('frame')} again, `mask` "
                           f"each credential's line by its box id, and place the new cut instead of {image.get('cut')}")
            continue
        # A secret still readable beside a mask keeps the cut refused, whatever the mask covers
        if cut.get("masks") and readable:
            report.add("DOCUMENT_IMAGE_SECRET_UNMASKED",
                       f"{slug}: {image.get('cut')} is cut from {cut.get('frame')}, which shows a secret, and "
                       f"the text reader still reads {', '.join(repr(text) for text in readable[:6])} on it",
                       fix=f"look at {image.get('cut')} and `mask` each of those by its `words`, or by the box id holding it: "
                           f"a label such as 'authorization:' or 'cookie:' is masked with the value after it, and a long "
                           f"value in full. Then place the cut")
            continue
        # The text reader finding no value on the cut proves nothing: it read none on a cut whose secret was plainly
        # readable (2026-09-30), so a cut is cleared only by a mask over a value. Only a mask recorded with its rectangle, which the tool refuses outside the cut, is counted
        covered = [area["words"] for area in areas]
        # A credential line masked to the cut's edge covers its value, whatever the reader read of it (2026-09-30: an
        # authorization value whose read start had no digit was called "words that hold no value")
        # An e-mail address is a value too (worker report: a mask over an address was called "words that hold no value")
        if any(_is_hidden_value(text=text) for text in covered) or any(area.get("reachesEdge") for area in areas):
            continue
        # Text the reader could not read is counted when the mask covered dark text and no word of it was read
        # (2026-09-30: the reader read no word of a secret plainly visible on a cut)
        if any(area.get("coveredText") and not area["words"].strip() for area in areas):
            continue
        # A mask over words that hold no value hides no secret (2026-09-30: the only mask on a secret's cut covered the
        # page's description line), a mask whose words were never recorded cannot be told from one, and a mask over no
        # word at all proves nothing (2026-09-30: three masks guessed onto blank space left the secret readable)
        masked_texts = [text for text in covered if text.strip()]
        if not cut.get("masks"):
            why = "it has no mask"
        elif not covered:
            why = "the words under its mask were not recorded"
        elif not masked_texts:
            why = "its masks cover no word the text reader could read, so nothing shows they hide the value"
        else:
            why = f"its masks cover only words that hold no value ({'; '.join(repr(_shorten_to_first_words(text)) for text in masked_texts)})"
        # A masked cut on which the reader finds no value left is cut again, said as the whole fix (2026-09-30: told it
        # as the last condition of a long fix, a worker opened such a cut four times and never cut the frame again)
        if cut.get("masks") and not any(_is_hidden_value(text=text) for text in left):
            report.add("DOCUMENT_IMAGE_SECRET_UNMASKED",
                       f"{slug}: {image.get('cut')} is cut from {cut.get('frame')}, which shows a secret, and {why}",
                       fix=f"No value is left on {image.get('cut')} to mask: its earlier masks cover it. `cut` "
                           f"{cut.get('frame')} again, `mask` the new cut by its value's `words`, and place the new cut "
                           f"instead of {image.get('cut')}")
            continue
        # The keys are named, and a value after one is masked in any form (2026-10-02: a client secret in GUID form was
        # told to be "a record's id, no secret")
        keys = document_text_utils.find_credential_keys(left)
        report.add("DOCUMENT_IMAGE_SECRET_UNMASKED",
                   f"{slug}: {image.get('cut')} is cut from {cut.get('frame')}, which shows a secret, and {why}",
                   fix=(f"the value after each of {', '.join(repr(key) for key in keys[:8])} is a secret, whatever its form. "
                        if keys else "") +
                       f"look at {image.get('cut')}, find the secret's value (a long string of letters, digits and signs; "
                       f"an id in GUID form, <8 hex>-<4 hex>-<4 hex>-<4 hex>-<12 hex>, names a record and is no secret "
                       f"unless it stands after a key that names a password, a secret or a key), "
                       f"`mask` it by its `words` as `look` lists them (or by the box id holding them), check the cut shown back, then place it. Where the "
                       f"screen shows the value only as asterisks, mask the asterisks the same way: the check counts a mask "
                       f"only over a word the text reader found. When earlier masks already cover the value, so no word of it is left, "
                       f"cut {cut.get('frame')} again and mask the new cut")


def _is_frame_cut_shown(paths: RunPaths,
                        cuts: dict,
                        frame: str,
                        placed_cuts: list[str]) -> bool:
    """Whether a cut made from this frame shows what a placed cut shows, by the comparison the repeated-figure check
    makes: a box shown again at another height is missed by comparing one rectangle of both frames (2026-10-05). A cut
    of the frame placed in the same answer counts too, against the placed cuts of other frames: skipping it, the
    repeat check said "leave this frame out, cut-NN shows it" and this check said nothing shows it (2026-10-05)."""

    # A cut a chapter reset set aside is still a picture of its frame: the reset of a later chapter set aside the cuts
    # an accepted chapter's leave-out rested on, and the resume refused that chapter (2026-10-05). An undone cut is the
    # worker's own wrong cut and proves nothing
    own = [cut_id for cut_id, cut in cuts.items()
           if cut.get("frame") == frame and cut.get("discarded") in (None, output_contract.RESET_DISCARD_REASON)]
    others = [cut_id for cut_id in placed_cuts if (cuts.get(cut_id) or {}).get("frame") != frame]

    return any(_compare_cuts(paths, cuts, cut_id, placed_id) is not None for cut_id in own for placed_id in others)


def _find_repeated_line_marker(section: dict,
                               said: str) -> str:
    """The marker of the decision, action or open line of `section` that `said` repeats, or "" for prose; a marker
    written inside a paragraph counts too."""

    written = re.match(r">?\s*\*\*(\w[\w ]*):\*\*", said.strip(), re.IGNORECASE)
    if written:
        return written.group(1).capitalize()
    wanted = " ".join(said.lower().split())
    marked = [*(("Decision", item.get("text")) for item in section.get("decisions") or []),
              *(("Action", item.get("text")) for item in section.get("action_items") or []),
              *(("Open", text) for text in section.get("not_established") or [])]

    return next((marker for marker, text in marked if wanted and wanted in " ".join(str(text or "").lower().split())), "")


def _list_said_texts(section: dict) -> list[str]:
    """What a section says that the document prints: its prose and its decision, action and open lines."""

    return ([paragraph.get("text", "") for paragraph in section.get("paragraphs", [])]
            + [str(item.get("text") or "") for item in [*(section.get("decisions") or []), *(section.get("action_items") or [])]]
            + [str(text) for text in section.get("not_established") or []])


def _read_final_title(paths: RunPaths) -> str:
    """The title the final answer gave, read from its call log; empty when the call gave none."""

    path = call_log_service.build_call_log_path(paths, document_file_service.SUMMARY_SLUG)
    if not path.is_file():
        return ""
    parsed = (call_log_service.read_call_log(path).get("response") or {}).get("parsed") or {}

    return str(parsed.get("title") or "").strip()


def _list_unsourced_degree_words(paths: RunPaths,
                                 claims: list[dict]) -> list[str]:
    """Each summary claim holding a degree or certainty word (`_DEGREE_WORD`) whose stem none of its cited notes holds,
    with those words."""

    if not any(_DEGREE_WORD.search(str(claim.get("text") or "")) for claim in claims):
        return []
    entries = integration_service.build_integration(paths).get("entries", [])
    owners = document_plan_utils.map_line_owners(entries)
    texts = {str(entry["id"]): str(entry.get("text") or "").lower() for entry in entries}
    added = []
    for claim in claims:
        source = " ".join(texts.get(owners.get(entry_id, entry_id), "") for entry_id in claim.get("entry_ids") or [])
        words = sorted({match.group(0).lower() for match in _DEGREE_WORD.finditer(str(claim.get("text") or ""))
                        if not re.search(rf"\b{_DEGREE_STEMS[match.group(1).lower()]}", source)})
        if words:
            added.append(f"{_shorten_to_first_words(str(claim.get('text') or ''))!r} says {', '.join(words)}")

    return added


def _check_title_states_claim(report: ValidationReport,
                              title: str) -> None:
    """Refuse a title that opens by saying what the document does ("Explains the refund flow")."""

    report.mark_ran("DOCUMENT_TITLE_NOT_CLAIM")
    words = title.split()
    if words and words[0].strip(".,:;").casefold() in _TITLE_ACT_VERBS:
        report.add("DOCUMENT_TITLE_NOT_CLAIM", f"the title opens with \"{words[0]}\", saying what the document does: {title!r}",
                   fix="write the title as the main thing the meeting establishes, a claim with its own subject: "
                       "\"Refunds are split into at most three parts from May\", never \"Explains the refund process\"")


def validate_outline(paths: RunPaths,
                     slug: str,
                     entry_ids: list[str],
                     kept_ids: list[str] | None = None) -> ValidationReport:
    """One chunk's outline, judged against the ids of that chunk only. `kept_ids` may never be set aside: the
    web addresses, action items and questions of the chunk (REQUIREMENTS.md R8)."""

    report, outline = _read_outline_or_refuse(paths, slug)
    if outline is not None:
        _judge_topics(report, slug, outline, list(entry_ids), list(kept_ids or []))

    return report


def validate_table_of_contents(paths: RunPaths,
                               slug: str,
                               entries: list[dict],
                               kept_ids: list[str],
                               chapter_count: int) -> ValidationReport:
    """R12's whole table of contents, stored as the agent decided it: its chapter and heading starts are judged as
    given (a transcript line id counts as the speech entry holding it), then its headings after the program orders and
    fills them in, as everything later uses them, and one chapter per agenda point."""

    report, stored = _read_outline_or_refuse(paths, slug)
    if stored is None:
        return report
    owners = document_plan_utils.map_line_owners(entries)
    given = ([str(chapter.get("first_entry_id") or "") for chapter in stored.get("chapters") or []]
             + [str(heading.get("first_entry_id") or "") for chapter in stored.get("chapters") or []
                for heading in chapter.get("headings") or []])
    unknown_starts = sorted({start for start in given if start not in owners})
    table = document_plan_utils.flatten_table_of_contents(stored, entries)
    _judge_topics(report, slug, table, [entry["id"] for entry in entries], kept_ids)
    if unknown_starts:
        report.add("DOCUMENT_UNKNOWN_SOURCE", f"the table of contents starts at id(s) that do not exist: "
                                               f"{', '.join(unknown_starts)}", fix="use only the ids you were given")

    report.mark_ran("DOCUMENT_TOC_CHAPTERS")
    chapters = table.get("chapters") or []
    if len(chapters) != chapter_count:
        report.add("DOCUMENT_TOC_CHAPTERS", f"{len(chapters)} chapter(s) with a known start for {chapter_count} agenda "
                                             f"point(s)",
                   fix=f"write one chapter per agenda point, {chapter_count} in all, each starting at an entry id you "
                       f"were given")
    check_text(report, slug, "\n".join(str(chapter.get("title", "")) for chapter in chapters))

    return report


def validate_contents(paths: RunPaths) -> ValidationReport:
    """The title call: a title, and one that obeys what the prose obeys. REQUIREMENTS.md R9: the chapters are the
    meeting's parts, so nothing here is arranged."""

    report = chain_response_validator.validate_chain_response(
        call_log_service.build_call_log_path(paths, document_file_service.CONTENTS_SLUG), document_file_service.CONTENTS_SLUG)
    if not report.ok:
        return report

    report.mark_ran("DOCUMENT_CONTENTS_MISSING")
    if not document_file_service.build_contents_path(paths).is_file():
        report.add("DOCUMENT_CONTENTS_MISSING", "no title was written",
                   fix="answer through the function you were given")

        return report
    title = str(document_file_service.read_contents(paths).get("title", "")).strip()

    report.mark_ran("DOCUMENT_TITLE_MISSING")
    if not title:
        report.add("DOCUMENT_TITLE_MISSING", "the title is empty",
                   fix="write one line stating the main thing the video establishes")
    _check_title_states_claim(report, title)
    check_text(report, document_file_service.CONTENTS_SLUG, title)

    return report


def validate_parts(paths: RunPaths,
                   moment_count: int) -> ValidationReport:
    """The parts call: parts that open the meeting at moment 1 and follow it in order, covering only announced items,
    with titles that obey what the prose obeys. REQUIREMENTS.md R9."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, document_file_service.PARTS_SLUG),
                                                              document_file_service.PARTS_SLUG)
    if not report.ok:
        return report

    report.mark_ran("DOCUMENT_PARTS_MISSING")
    if not document_file_service.build_parts_path(paths).is_file():
        report.add("DOCUMENT_PARTS_MISSING", "no agenda was written", fix="answer through the function you were given")

        return report
    answer = document_file_service.read_parts(paths)
    parts = answer.get("points") or []
    announced = [str(item) for item in answer.get("announced_agenda") or []]

    report.mark_ran("DOCUMENT_PARTS_EMPTY")
    if not parts:
        report.add("DOCUMENT_PARTS_EMPTY", "the answer holds no agenda point",
                   fix="divide the meeting into at least one agenda point, starting at moment 1")

        return report

    report.mark_ran("DOCUMENT_PARTS_ORDER")
    firsts = [part.get("first_moment") for part in parts]
    is_ordered = (all(isinstance(first, int) for first in firsts) and firsts[0] == 1
                  and all(earlier < later for earlier, later in zip(firsts, firsts[1:])) and firsts[-1] <= moment_count)
    if not is_ordered:
        report.add("DOCUMENT_PARTS_ORDER", f"the agenda points start at moments {firsts}",
                   fix=f"start the first point at 1, each later point at a later moment, none past {moment_count}")

    report.mark_ran("DOCUMENT_PARTS_ANNOUNCED")
    known = {item.casefold().strip() for item in announced}
    foreign = [item for part in parts for item in part.get("covers_announced") or []
               if str(item).casefold().strip() not in known]
    if foreign:
        report.add("DOCUMENT_PARTS_ANNOUNCED", f"covered item(s) that were never announced: {', '.join(map(repr, foreign))}",
                   fix="name in `covers_announced` only items of `announced_agenda`, in the same wording")
    check_text(report, document_file_service.PARTS_SLUG, "\n".join([str(part.get("title", "")) for part in parts] + announced), repeat_text="")

    return report


def validate_section(paths: RunPaths,
                     slug: str,
                     topic_entry_ids: list[str],
                     earlier_slugs: list[str]) -> ValidationReport:
    """One section part written by its own call: how the call ended, then the section as `judge_section` judges it."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report
    judged = judge_section(paths, slug, SectionJudgingContext(entry_ids=topic_entry_ids), earlier_slugs)
    report.findings += judged.findings
    report.checks_run += judged.checks_run

    return report


def _list_role_names(paths: RunPaths) -> set[str]:
    """The full names the frames' notes give a person's role (DEFECTS 292)."""

    return document_text_utils.list_role_names(
        [" ".join(str(value) for value in record.values() if isinstance(value, (str, list)))
         for record in frame_records_service.read_frame_records(paths)])


def judge_section(paths: RunPaths,
                  slug: str,
                  scope: SectionJudgingContext,
                  earlier_slugs: list[str]) -> ValidationReport:
    """One written section, judged against its topic part's ids and against every section written before it.
    `scope`: the ids it may cite, the ids it must cite, and which speech entry holds each line it may name. The call that wrote it is judged by the
    caller: a section's own call, or a whole chapter's."""

    report = ValidationReport(subject=slug)

    report.mark_ran("DOCUMENT_SECTION_MISSING")
    if not document_file_service.build_section_path(paths, slug).is_file():
        report.add("DOCUMENT_SECTION_MISSING", f"{slug}: no section was written",
                   fix="answer through the function you were given")

        return report
    section = document_file_service.read_section(paths, slug)
    paragraphs = section.get("paragraphs", [])

    report.mark_ran("DOCUMENT_SECTION_EMPTY")
    if not [block for block in section.get("blocks") or [] if block["kind"] not in ("subheading", "heading")]:
        report.add("DOCUMENT_SECTION_EMPTY", f"{slug}: the section says nothing",
                   fix="write at least one paragraph from the entries you were handed")

        return report

    report.mark_ran("DOCUMENT_BRACKET_UNREAD")
    # The fix names where a heading's link goes: '[T03.8, F37.2]' at a block's end blocked a chapter (2026-10-06)
    unread = [bracket for block in section.get("blocks") or [] for bracket in document_text_utils.find_unread_brackets(block["text"])]
    if unread:
        items = [item for bracket in unread for item in document_text_utils.describe_unread_items(bracket)]
        report.add("DOCUMENT_BRACKET_UNREAD", f"{slug}: these brackets could not be read as note ids: {', '.join(unread)}"
                   + (f" ({'; '.join(items)})" if items else ""),
                   fix="a bracket at the end of a block holds note ids only: a frame's note as F<frame>.<note>, "
                       "a transcript line as S<line>, or a range of one frame's notes, F<frame>.<first note>-F<frame>.<last note>. A whole "
                       "frame, F<frame>, is no note id: name its notes. A link to another heading, [T03.2], stands in its "
                       "own brackets inside the sentence that uses its point, never among the note ids")

        return report

    report.mark_ran("DOCUMENT_TRANSCRIPT_COPIED")
    copied = document_text_utils.find_copied_speech(" ".join(paragraph.get("text") or "" for paragraph in paragraphs),
                                                    list(scope.speech_texts), screen_texts=list(scope.screen_texts))
    if copied:
        report.add("DOCUMENT_TRANSCRIPT_COPIED", f"{slug}: {len(copied)} sentence(s) copy the speech word for word: "
                   + "; ".join(repr(_shorten_to_first_words(sentence)) for sentence in copied),
                   fix="write what the speech establishes, in your own words and without its filler; a line that "
                       "establishes nothing goes on the `> **Set aside:** <why> [ids]` line")

    report.mark_ran("DOCUMENT_BLOCK_VALUE_CHANGED")
    changed = document_text_utils.find_changed_value_lines(
        # The fences as written: one holding no code is read as the paragraphs it holds, as a boot log was
        [*document_text_utils.list_code_blocks(str(section.get("markdown") or "")),
         *(str(block["text"]) for block in section.get("blocks") or [] if block["kind"] == "table")],
        list(scope.screen_texts))
    if changed:
        report.add("DOCUMENT_BLOCK_VALUE_CHANGED", f"{slug}: these block lines give other numbers than the notes' screen text: "
                   + "; ".join(f"{line!r} where the notes write {held!r}" for line, held in changed),
                   fix="copy each line of a code block or a table as the notes' screen text writes it, its numbers "
                       "included")

    # A count of a table's rows above the rows it holds: "82 incidents" over 80, or "60 tenants" over a table that kept
    # 53 of them (DEFECTS 519; over 1,123 accepted sections it named these 3 and nothing else)
    report.mark_ran("DOCUMENT_COUNT_ABOVE_ROWS")
    above = document_text_utils.find_counts_above_table_rows(section.get("blocks") or [],
                                                             "\n".join(scope.speech_texts + scope.screen_texts))
    if above:
        report.add("DOCUMENT_COUNT_ABOVE_ROWS", f"{slug}: these counts are larger than the rows of the table they count: "
                   + "; ".join(above),
                   fix="count the table's rows and write that number, or write every row the notes hold into the table")

    # A time unit the notes never give a number: "60 seconds" for `--retry-budget 60`, which counts retries (DEFECTS 516;
    # over 137 accepted sections of the invented PDFs' runs it named this one and "Retry-After: 60" written "60 seconds").
    # Every note counts as giving it, a fact too: a fact's "thought for 2s" was refused as a unit no note gives (DEFECTS 590)
    report.mark_ran("DOCUMENT_UNIT_UNSOURCED")
    units = document_text_utils.find_units_unstated(
        "\n".join(str(block.get("text") or "") for block in section.get("blocks") or []),
        "\n".join(scope.speech_texts + scope.screen_texts + scope.note_texts))
    if units:
        report.add("DOCUMENT_UNIT_UNSOURCED", f"{slug}: these numbers carry a unit their notes never give them: "
                   + "; ".join(units),
                   fix="write the number as the notes give it, with their unit or none; a unit the notes do not state is "
                       "not added")

    report.mark_ran("DOCUMENT_PARAGRAPH_UNSOURCED")
    unsourced = [paragraph.get("text") or "" for paragraph in paragraphs if not paragraph.get("entry_ids")]
    if unsourced:
        # Each block is quoted, so the worker knows which one (2026-09-30: a worker told only "a paragraph" looked for it
        # among its paragraphs, and the block was two lines of its own that no instruction asked for)
        quoted = "; ".join(repr(_shorten_to_first_words(text)) for text in unsourced)
        report.add("DOCUMENT_PARAGRAPH_UNSOURCED", f"{slug}: these blocks name no entry id: {quoted}",
                   fix="end every paragraph, list and table with the ids of the entries it rests on, in brackets, "
                       "such as [F<frame>.<note>, S<line>]; a code block's ids stand alone on the line right after its closing fence; "
                       # A worker kept a lead-in sentence over a figure and was refused for it twice: the fix names
                       # the way to keep one
                       "a sentence that sums up the blocks after it ends with the ids of the entries it sums up; a block "
                       "that rests on no entry does not belong in the text")
    # The checks after it still run: under one attempt a step was refused three times, each refusal naming the one
    # finding this check hid the rest behind (2026-10-06)

    blocks = section.get("blocks") or []
    report.mark_ran("DOCUMENT_HEADING_LEVEL")
    headings = [block["text"] for block in blocks if block["kind"] == "heading"]
    if headings:
        report.add("DOCUMENT_HEADING_LEVEL", f"{slug}: the section holds a heading of level 3 or higher: "
                   + "; ".join(repr(_shorten_to_first_words(text)) for text in headings),
                   fix="the table of contents owns the headings; write a subheading inside a section with ####")

    diagrams = section.get("diagrams") or []
    report.mark_ran("DOCUMENT_DIAGRAM_NOT_CLOSED")
    if any(block.get("is_unclosed") for block in blocks):
        report.add("DOCUMENT_DIAGRAM_NOT_CLOSED", f"{slug}: a ``` fence is never closed, so it swallows what follows",
                   fix="close every diagram with a line of three backticks, ```")

        return report

    report.mark_ran("DOCUMENT_DIAGRAM_UNSOURCED")
    unsourced_diagrams = [_describe_diagram(number, diagram) for number, diagram in enumerate(diagrams, start=1)
                          if not diagram.get("entry_ids")]
    if unsourced_diagrams:
        report.add("DOCUMENT_DIAGRAM_UNSOURCED", f"{slug}: {', '.join(unsourced_diagrams)} name(s) no entry id",
                   fix="write the ids of the `diagram` entries it is drawn from on the line under the diagram, in "
                       "brackets")

        return report

    report.mark_ran("DOCUMENT_DIAGRAM_NOT_MERMAID")
    not_mermaid = [_describe_diagram(number, diagram) for number, diagram in enumerate(diagrams, start=1)
                   if not mermaid_text_utils.is_mermaid(str(diagram.get("mermaid", "")))]
    if not_mermaid:
        report.add("DOCUMENT_DIAGRAM_NOT_MERMAID", f"{slug}: {', '.join(not_mermaid)} is not Mermaid source",
                   fix="start every diagram with its Mermaid type, such as `flowchart LR` or `sequenceDiagram`")

    report.mark_ran("DOCUMENT_DIAGRAM_SYNTAX")
    problems = [problem for diagram in diagrams
                for problem in mermaid_text_utils.find_syntax_problems(str(diagram.get("mermaid", "")))]
    if problems:
        report.add("DOCUMENT_DIAGRAM_SYNTAX", f"{slug}: Mermaid cannot draw the diagram: {'; '.join(problems)}",
                   fix="write one statement per line, close every quote and bracket on the line it opens, and use "
                       "`<br/>` only inside a quoted label")

    claims = [block for block in blocks if block["kind"] in _CLAIM_MARKERS]
    report.mark_ran("DOCUMENT_CLAIM_UNSOURCED")
    unsourced = [claim for claim in claims if not claim.get("entry_ids")]
    if unsourced:
        # The line is quoted with its marker: a set-aside line was called "a decision, an action item or an open point",
        # and the worker, who had written none, could not find it (2026-10-02)
        named = "; ".join(f"'> **{_CLAIM_MARKERS[claim['kind']]}:** {_shorten_to_first_words(str(claim.get('text') or ''))}'"
                          for claim in unsourced)
        # What is wrong is said as it is: a line that ended "…: F20.3, F21.4" was told it named no entry id, and the
        # worker, which had named ten, rewrote the sentence twice without ever moving them into brackets (2026-10-07)
        outside = [claim for claim in unsourced
                   if document_text_utils.find_entry_ids(str(claim.get("text") or ""))]
        wrong = ("these marked lines name their entry ids outside the brackets" if len(outside) == len(unsourced)
                 else "these marked lines name no entry id in brackets")
        report.add("DOCUMENT_CLAIM_UNSOURCED", f"{slug}: {wrong}: {named}",
                   fix="end every marked line (decision, action item, open point, set aside) with the ids of the entries "
                       "it rests on, in square brackets: [F<frame>.<note>, S<line>]")

    # write.md: "Never set aside a web address, an action item or a question" had no check, and a chapter put its web
    # address notes on the set-aside line (2026-10-08)
    report.mark_ran("DOCUMENT_KEPT_NOTE_SET_ASIDE")
    kept = set(scope.kept_ids)
    set_aside = [entry_id for block in blocks if block["kind"] == "set_aside" for entry_id in block["entry_ids"]
                 if entry_id in kept]
    if set_aside:
        report.add("DOCUMENT_KEPT_NOTE_SET_ASIDE", f"{slug}: the set-aside line holds {', '.join(set_aside)}, a web "
                   f"address, an action item or a question, which is never set aside",
                   fix="move each into the brackets of the block it belongs to; a web address already written in the "
                       "document is cited by its id there, with no line of its own")

    # An open point is a question nobody answered: one written with its answer after it is left out of the closing's
    # open points, and the document then said "Nothing was left open." under an open line (lecture-9 chapter 10,
    # 2026-10-21, which phase 5's review raised as a contradiction and blocked on)
    report.mark_ran("DOCUMENT_OPEN_ANSWERED")
    answered = [text for text in section.get("not_established") or [] if document_text_utils.is_answered_question(str(text))]
    if answered:
        report.add("DOCUMENT_OPEN_ANSWERED", f"{slug}: the open line gives its answer: {answered[0]!r}",
                   fix="a question the meeting answered is no open point: write it as a sentence, \"Asked whether "
                       "<question>, the team answered that <answer>\", its ids in the brackets, and delete the "
                       "`> **Open:**` line; only a question nobody answered stays one")

    # The checks go on past an unsourced line: stopping there hid a wrong id beside it until the next answer, a whole
    # blocked step later (2026-10-02)
    # A transcript line the section names is the speech entry that holds it (`line_owners`, from the request's entries)
    cited = [scope.line_owners.get(entry_id, entry_id) for block in blocks for entry_id in block["entry_ids"]]
    report.mark_ran("DOCUMENT_UNKNOWN_SOURCE")
    foreign = sorted(set(cited) - set(scope.entry_ids))
    if foreign:
        # A note number past a frame's last, F16.2 where frame 16 has only F16.1, was cited again after this finding
        # named it only as "not this topic's" (DEFECTS 224): the frame's own notes here are named beside it
        siblings = {entry_id: [own for own in scope.entry_ids if own.split(".")[0] == entry_id.split(".")[0]]
                    for entry_id in foreign if entry_id.startswith("F")}
        named = ", ".join(f"{entry_id} (this topic holds {', '.join(siblings[entry_id])} of that frame, and no "
                          f"{entry_id}: replace it with one of those, or drop it)" if siblings.get(entry_id) else entry_id
                          for entry_id in foreign)
        report.add("DOCUMENT_UNKNOWN_SOURCE", f"{slug}: cites id(s) that are not this topic's: {named}",
                   fix=f"use only these ids: {', '.join(scope.entry_ids)}")

    # A note that states a decision is cited by a marked decision, so the closing section lists it (2026-10-01: two
    # decisions the slides labelled were written as prose, and the closing section said none was stated)
    report.mark_ran("DOCUMENT_DECISION_UNMARKED")
    decision_cited_ids = {scope.line_owners.get(entry_id, entry_id) for block in blocks if block["kind"] == "decision"
                          for entry_id in block["entry_ids"]}
    unmarked = [entry_id for entry_id in scope.decision_ids if entry_id in cited and entry_id not in decision_cited_ids]
    if unmarked:
        report.add("DOCUMENT_DECISION_UNMARKED", f"{slug}: {', '.join(unmarked)} state(s) a decision no `> **Decision:**` "
                                                 "line cites",
                   fix="write each decision those notes state as its own line where it is said, `> **Decision:** <what "
                       "was decided> [ids]`; the closing section lists only marked decisions")

    # A paragraph holding a code block is cited by the block written out: a specification's two central examples were cited
    # at the end of a sentence pointing to their section, and never written anywhere (pdf-real-rfc9457)
    report.mark_ran("DOCUMENT_CODE_LEFT_OUT")
    written = "\n".join(block["text"] for block in section.get("blocks") or [])
    left_out = [entry_id for entry_id in dict.fromkeys(cited) for code in scope.code_blocks.get(entry_id, [])
                if not document_text_utils.is_code_block_held(code, written)]
    if left_out:
        report.add("DOCUMENT_CODE_LEFT_OUT", f"{slug}: {', '.join(dict.fromkeys(left_out))} hold(s) a code block the "
                                             "section cites and does not write",
                   fix="write each of those code blocks in a fence as it stands, every line, its ids after it; or "
                       "leave the id out of this section and cite it where its block is written")
    report.mark_ran("DOCUMENT_ENTRY_UNCOVERED")
    unused = [entry_id for entry_id in (scope.entry_ids if scope.required_ids is None else scope.required_ids) if entry_id not in cited]
    if unused:
        report.add("DOCUMENT_ENTRY_UNCOVERED", f"{slug}: no block of the section uses {', '.join(unused)}",
                   fix="keep every block you wrote, word for word, and add what each of those entries establishes, "
                       "naming its id in the brackets that end the block; an entry whose fact an earlier section, an "
                       "earlier part of this heading or a table you wrote already holds is never written again: its id "
                       "alone goes in the brackets of the block it belongs to; a note about the screen itself (where a "
                       "button or a field stands, what a page holds, what a console shows or waits for) needs only its id in the brackets of the block "
                       "about that page or step, never a sentence of its own; only an entry that says nothing about the "
                       # write.md's own list; told a shorter one, a worker doubted a note that the meeting moved on to
                       # its next part could be set aside, and put it under another heading (2026-10-04)
                       "subject (that the speech was cut off or nothing was said, a guess at a misheard word, who is "
                       "presenting, that the presenter switched windows or the meeting moved on) goes on that heading's "
                       "own `> **Set aside:** <why> [ids]` line, its id in the brackets")

    # Each paragraph apart, as the document renders it: run into the code block before it, "The presentation structure
    # outlines …" was no sentence's start, passed every chapter check and failed the finished document (DEFECTS 244)
    text = "\n\n".join(paragraph.get("text", "") for paragraph in paragraphs)
    marked_texts = [block["text"] for block in blocks if block["kind"] in ("subheading", "decision", "action", "open")]
    # The description is checked with the caption: it is text the reader is given, and a draft put a customer's number
    # and name in it while the same words were refused in the prose (2026-10-07)
    # Each as a block of its own, as the document renders them: run into one sentence, a caption opening "the slide
    # details …" stood behind its alt text, passed every chapter check and failed the finished document (2026-10-08)
    # The description keeps its label, as rendered: it says what the figure holds, so "the slide …" opens it rightly
    captions = [f"{image['alt_text']}\n\n{image['caption']}\n\n"
                f"{document_text_utils.FIGURE_DESCRIPTION_MARK} {image.get('description') or ''}"
                for image in section.get("images") or []]
    # A figure's description quotes its screen, as write.md asks, so a heading the prose quotes too is no claim said
    # twice: the document's own repeat check reads it without descriptions, and this one does the same (2026-10-08: a
    # legal heading in a paragraph and in its figure's description was refused here and passed there)
    spoken = [f"{image['alt_text']}\n\n{image['caption']}" for image in section.get("images") or []]
    # Where a figure's own text holds a sentence, the finding says so: refused for its caption six checks in a row, a
    # writer rewrote its prose each time looking for the sentence there (lecture-1 chapter 6, 2026-10-14)
    places = {part: f"the {name} of {image.get('cut') or 'a figure'}" for image in section.get("images") or []
              for name, part in (("alt text", image.get("alt_text")), ("caption", image.get("caption")),
                                 ("description", image.get("description"))) if str(part or "").strip()}
    # A code block is read between its fences, as the document prints it, so it is one sentence: its own lines a
    # workflow file repeats in each job are no claim said twice (lecture-9 chapter 7, 2026-10-21: fixed for the document
    # alone, the section's record lost the fences and the chapter check refused the block four checks in a row)
    # The same for a screen's table or list: two lists of one web page share its side panel's lines, each written as it
    # stands as the request asks, and the shared lines were refused (lecture-9 chapter 9, 2026-10-21)
    markdown_notes = _list_markdown_notes(paths)
    markdown_ids = set(markdown_notes)
    # A list copied from a source paragraph is the source's text, its repeats the source's: left out of the count (a
    # fenced block of reference entries was still cut at each full stop)
    source_list_ids = _list_source_list_ids(paths)
    repeat_prose = "\n".join("" if paragraph.get("kind") == "list"
                              and set(paragraph.get("entry_ids") or []) & source_list_ids
                              else f"```\n{paragraph.get('text', '')}\n```" if _is_screen_block(paragraph, markdown_ids)
                              else paragraph.get("text", "") for paragraph in paragraphs)
    check_text(report, slug, _hide_published_addresses(paths, "\n\n".join([text, *marked_texts, *captions])),
               "\n\n".join([repeat_prose, *marked_texts, *spoken]),
               TextCheckContext(places, [str(paragraph.get("text") or "") for paragraph in paragraphs
                                       if _is_screen_block(paragraph, markdown_ids)],
                                document_folder_validator.list_note_times(paths)))
    check_code_fences(report, slug, text)
    check_code_words(report, slug, text, document_folder_validator.list_note_words(paths))
    report.mark_ran("DOCUMENT_PERSON_FULL_NAME")
    full_names = sorted(name for name in _list_role_names(paths) if name in text)
    if full_names:
        report.add("DOCUMENT_PERSON_FULL_NAME", f"{slug}: the text names a person in full: {', '.join(full_names)}",
                   fix="name the person by their role (\"the presenter\", \"the speaker\"); a first name alone only for "
                       "someone who speaks in the recording")

    report.mark_ran("DOCUMENT_SAID_EARLIER")
    # Marked lines too, as the whole document's DOCUMENT_REPEATED_SENTENCE counts them: one open point written under two
    # headings of a chapter passed every section and failed the finished document, with no answer left to fix it
    # (2026-10-06)
    earlier_sections = _read_sections(paths, earlier_slugs)
    # Each block apart, as the document prints it: joined by one line break, a code block ran on into the paragraph
    # after it as one sentence, and a code block written under two headings was never found said earlier (2026-10-14)
    earlier_text = "\n\n".join(text_part for earlier in earlier_sections for text_part in _list_said_texts(earlier))
    # A code, table or list block is said earlier only as the chapter request counts it, when one earlier block holds
    # 60% of its lines: the request asked a file extended by two functions written as it stands, and this check refused
    # it for the one function an earlier chapter's block held (lecture-9 chapter 7, 2026-10-21)
    earlier_blocks = [section_markdown_utils.list_block_lines(paragraph.get("text", ""))
                      for earlier in earlier_sections for paragraph in earlier.get("paragraphs") or []
                      if paragraph.get("kind") in section_markdown_utils.BLOCK_KINDS]
    new_blocks = [paragraph.get("text", "") for paragraph in section.get("paragraphs") or []
                  if _is_screen_block(paragraph, markdown_ids)
                  and not section_markdown_utils.is_block_held(_list_measured_lines(paragraph, markdown_notes), earlier_blocks)]
    # A block an earlier one holds is named as one block with what it adds: judged row by row, a table repeating an
    # earlier one but for its new row was refused by one old row five checks running, and the writer never saw that
    # the table was the repeat (DEFECTS 388)
    held_blocks = [paragraph.get("text", "") for paragraph in section.get("paragraphs") or []
                   if _is_screen_block(paragraph, markdown_ids) and paragraph.get("text", "") not in new_blocks]
    for held in held_blocks:
        own_lines = [line for line in (str(held).splitlines()) if line.strip()]
        ordered = [line for raw in own_lines for line in section_markdown_utils.list_block_lines(raw)]
        added = list_added_block_lines(ordered, earlier_blocks)
        first = next((line.strip() for line in own_lines if line.strip() and not line.strip().startswith("```")), "")[:60]
        report.add("DOCUMENT_SAID_EARLIER", f"{slug}: the block beginning {first!r} repeats a block an earlier section "
                   f"already holds" + (f"; it adds only: {'; '.join(repr(line[:60]) for line in added[:5])}" if added
                                       else ", and adds nothing"),
                   fix=("write only the lines it adds, as a block of their own with the block's ids, and say in a "
                        "sentence that they join the earlier one" if added else
                        "drop the block and put its ids in the brackets of the block before it: the earlier section keeps it"))
    said_texts = [text_part for text_part in _list_said_texts(section) if text_part not in new_blocks + held_blocks]
    said = document_text_utils.find_sentences_said_earlier("\n\n".join(said_texts), earlier_text)
    if said:
        # A marked line said again is deleted whole: told to "drop the sentence", a worker kept a repeated open point
        # three times and only moved its ids (2026-10-08)
        marker = _find_repeated_line_marker(section, said[0])
        report.add("DOCUMENT_SAID_EARLIER", f"{slug}: an earlier section already says: {said[0]!r}",
                   fix=(f"delete the whole `> **{marker}:**` line from this section, and add its ids to the brackets "
                        f"that end the block before it: the earlier section keeps it"
                        if marker else
                        "say only what the document does not say yet: state what is new, or drop the sentence and put "
                        "its ids in the brackets of the block it belongs to, so its entries stay covered"))

    # A slide's points written as a paragraph and again as the slide's list, in one section: write.md says a fact a list
    # holds is never written again as a sentence, and 26 of 43 lists of the public runs' sections broke it (DEFECTS 637)
    report.mark_ran("DOCUMENT_LIST_REPEATS_PROSE")
    for list_text, paragraph_text in document_text_utils.find_lists_restating_prose(section.get("paragraphs") or []):
        report.add("DOCUMENT_LIST_REPEATS_PROSE",
                   f"{slug}: the list beginning {list_text.splitlines()[0].strip()[:60]!r} says again what the paragraph "
                   f"beginning {paragraph_text.strip()[:60]!r} says",
                   fix="keep one of the two: drop the list and add its ids to the paragraph's brackets, or drop the "
                       "paragraph's sentences the list holds and add their ids to the list's brackets")

    # A cut is one figure with one place: a later part of a heading placed the cut an earlier part had placed, and the
    # document showed the same figure twice (2026-10-03)
    report.mark_ran("DOCUMENT_IMAGE_PLACED_TWICE")
    placed_earlier = {str(image.get("cut") or "") for earlier in earlier_sections for image in earlier.get("images") or []}
    own_list = [str(image.get("cut") or "") for image in section.get("images") or [] if image.get("cut")]
    own_cuts = set(own_list)
    # Twice in this section too: the hard figures PDF's crew photo stood as its Figure 1 and its Figure 8
    twice_here = {cut_id for cut_id in own_cuts if own_list.count(cut_id) > 1}
    again = sorted((own_cuts & placed_earlier) | twice_here)
    if again:
        report.add("DOCUMENT_IMAGE_PLACED_TWICE", f"{slug}: {', '.join(again)} already stand(s) in an earlier section or earlier in this one",
                   fix="drop that image line: a cut is placed once, where it was first placed; cite its notes in the "
                       "brackets of the block they belong to")

    # A cut stands under a heading whose notes its frame holds: a worker placed a cut of one heading's frame under
    # another heading, captioned as that heading's screen (2026-10-04)
    report.mark_ran("DOCUMENT_IMAGE_OFF_HEADING")
    own_frames = {int(match.group(1)) for entry_id in scope.entry_ids if (match := _FRAME_OF_NOTE.match(entry_id))}
    for image in section.get("images") or []:
        frame = str(image.get("frame") or "")
        number = _FRAME_NUMBER.fullmatch(frame)
        if number is not None and int(number.group(1)) not in own_frames:
            report.add("DOCUMENT_IMAGE_OFF_HEADING",
                       f"{slug}: {image.get('cut')} is cut from {frame}, which holds none of this heading's notes",
                       fix=f"place {image.get('cut')} under the heading whose notes {frame} holds, and here a cut of a "
                           f"frame this heading's notes come from")

    return report


def validate_section_images(paths: RunPaths,
                            slug: str) -> ValidationReport:
    """The frames a section placed: each is a cut that exists, with a caption and a description, that hides every
    secret and person's details, is not mostly masked, cuts no line of text, repeats no placed figure and is not
    blank."""

    report = ValidationReport(subject=slug)
    report.mark_ran("DOCUMENT_IMAGE_INVALID")
    report.mark_ran("DOCUMENT_IMAGE_NOT_DESCRIBED")
    path = document_file_service.build_section_path(paths, slug)
    section = document_file_service.read_section(paths, slug) if path.is_file() else {}
    cuts = document_file_service.read_image_store(paths).get("cuts") or {}
    for image in section.get("images") or []:
        problems = []
        if not image.get("cutFile") or not (paths.output_root / str(image["cutFile"])).is_file():
            problems.append(f"no cut {image.get('cut')!r} was made")
        if (cuts.get(str(image.get("cut"))) or {}).get("discarded"):
            problems.append(f"{image.get('cut')} was discarded: {cuts[str(image['cut'])]['discarded']}")
        if not str(image.get("caption") or "").strip():
            problems.append("its caption is empty")
        described = str(image.get("description") or "").split()
        if len(described) < _MIN_DESCRIPTION_WORDS:
            report.add("DOCUMENT_IMAGE_NOT_DESCRIBED",
                       f"{slug}: the figure of {image.get('cut')} is described in {len(described)} word(s), "
                       f"fewer than {_MIN_DESCRIPTION_WORDS}",
                       fix="under the figure's caption write a line `> **What the figure shows:** <the figure "
                           "outlined>`: its parts where they sit, every text on it quoted exactly, in reading order, "
                           "as one paragraph. It is what a reader who cannot see the figure, and an agent reading the "
                           "document as text, read in its place, so it names what the figure shows, never why it "
                           "matters, which is the caption's job.")
        if problems:
            report.add("DOCUMENT_IMAGE_INVALID", f"{slug}: an image is not usable: {'; '.join(problems)}",
                       fix="cut the frame with `cut` first, then write it where the text needs it as "
                           "![alt text](cut-NN \"caption\"): a caption that states what the reader should take from it. "
                           # Cuts are numbered in order, so the cut made after this refusal can take the very id it
                           # names, and a worker reasoned at length over the two (2026-10-06)
                           "The cut tool numbers cuts in order: a cut you make now may get the id named here, and it "
                           "is then the one to place")
    _check_secrets_masked(paths, slug, section, report)
    _check_personal_values_masked(paths, slug, section, report)
    _check_mostly_masked(paths, slug, section, report)
    _check_text_cut_off(paths, slug, section, report)
    _check_repeated_figures(paths, slug, section, report)
    _check_blank_cuts(paths, slug, section, report)

    return report


def list_added_block_lines(lines: list[str],
                           earlier_blocks: list[list[str]]) -> list[str]:
    """The lines of a block that no earlier block holds, an elision line ("...") left out: it is no content, and told a
    repeated diff "adds only: '...'", a writer was about to write a code block holding "..." alone (Lecture 7's
    chapter 1, DEFECTS 587)."""

    return [line for line in section_markdown_utils.list_unheld_lines(lines, earlier_blocks)
            if not _ELISION_LINE.fullmatch(line.strip())]


def describe_open_needed_frames(paths: RunPaths,
                                sections: list[dict],
                                follow_up: str,
                                store: dict) -> str:
    """One line for the worker after a frame tool: where each needed frame of this follow-up stands, from the program's
    records: left out, replaced, placed under a heading another follow-up wrote, a cut made that its answer must place,
    or nothing yet. A worker tracking ten frames over several turns from memory dropped a cut's line from its answer and
    never called a `leave_out` it had planned (2026-10-04). Empty when the follow-up needs no frame."""

    needed = list(dict.fromkeys(frame for section in sections for frame in section.get("neededFrames") or []))
    if not needed:
        return ""
    cuts = store.get("cuts") or {}
    own = {str(section.get("slug") or "") for section in sections}
    sections_dir = document_file_service.build_sections_dir(paths)
    others = [path.stem for path in sections_dir.glob("*.md") if path.stem not in own] if sections_dir.is_dir() else []
    placed = {str((cuts.get(image.get("cut")) or {}).get("frame") or "")
              for other in others for image in document_file_service.read_section(paths, other).get("images") or []}
    # A leave_out as shown elsewhere holds only while a placed cut shows the frame (2026-10-06: told just "left out", a
    # worker placed no such cut and the leave_out was refused a second time)
    dispositions = _read_frame_dispositions(store, follow_up)
    states = []
    for frame in needed:
        made = [cut_id for cut_id, cut in cuts.items() if cut.get("frame") == frame and not cut.get("discarded")]
        if frame in dispositions.shown_elsewhere:
            state = ("left out as shown by a placed frame: it holds while a placed cut of another frame shows the same; "
                     "if a rejection says no placed frame shows it, place a cut of this frame")
        elif frame in dispositions.left_out and made:
            # A cut made of a frame left out is named: told only "left out" beside the cut it had just made, a worker
            # could not tell whether to place it (2026-10-06)
            state = (f"left out, and {', '.join(made)} made of it: place one as its own image line (a placed cut shows "
                     f"the frame), or `undo_cut` it to keep the frame out")
        elif frame in dispositions.left_out:
            state = "left out"
        elif frame in dispositions.replaced:
            state = "replaced by a diagram"
        elif frame in placed:
            state = "placed under another heading"
        elif made:
            state = f"{', '.join(made)} made: put one in your answer as its own image line"
        elif frame_records_service.is_figure_placed_only_as_cut(paths, frame):
            # A PDF figure is cut whole with no look first: its look says only that
            state = "nothing yet: `cut` it with no box and no rectangle to place it whole (a figure is never left out)"
        elif frame_records_service.read_frame_medium(paths, frame) == output_contract.PDF_FIGURE_MEDIUM:
            # A figure its notes hold as a markdown block is carried by that block (DEFECTS 517)
            state = ("nothing yet: its notes hold it as a markdown block; write that block and `leave_out` the figure "
                     "with that reason, or `cut` it whole and leave the block out")
        else:
            state = "nothing yet: look and cut it, or `leave_out` it"
        states.append(f"{frame} {state}")

    return "Needed frames of this follow-up: " + "; ".join(states) + "."


def validate_needed_screenshots(paths: RunPaths,
                                slug: str,
                                needed_frames: list[str],
                                chapter_slug: str) -> ValidationReport:
    """Every screenshot phase 3 marked needed is placed or has an explicit, valid disposition for this chapter."""

    report = ValidationReport(subject=slug)
    report.mark_ran("DOCUMENT_NEEDED_FRAME_UNACCOUNTED")
    report.mark_ran("DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID")
    section_path = document_file_service.build_section_path(paths, slug)
    section = document_file_service.read_section(paths, slug) if section_path.is_file() else {}
    store = document_file_service.read_image_store(paths)
    cuts = store.get("cuts") or {}
    # Placed under ANY heading written so far, this one included: a frame whose notes span two headings is shown
    # once, and a check per heading asked for it again under the second (a repeated figure is a defect of its own).
    sections_dir = document_file_service.build_sections_dir(paths)
    written = [path.stem for path in sections_dir.glob("*.md")] if sections_dir.is_dir() else []
    images = [image for other in written for image in document_file_service.read_section(paths, other).get("images") or []]
    # `written` holds this heading's own section once it is written, so its images are counted once
    all_images = images
    placed_cuts = [str(image.get("cut") or "") for image in all_images]
    placed = {str((cuts.get(cut_id) or {}).get("frame") or "") for cut_id in placed_cuts}
    dispositions = _read_frame_dispositions(store, chapter_slug)
    replacements, left_out = dispositions.replaced, dispositions.left_out
    needed = set(needed_frames)
    missing = sorted(needed - placed - replacements - left_out)
    # An image written inside a sentence is not read as placed; it is named (2026-09-30: a worker wrote two images
    # inside sentences and was told only which frames were missing)
    inline = sorted(set(_INLINE_IMAGE.findall(section_path.read_text(encoding="utf-8")))) if section_path.is_file() else []
    inline_text = (f" ({', '.join(inline)} written inside a sentence, which is not read as placed: write "
                   f"`![alt](cut-NN \"caption\")` on its own line)" if inline else "")
    if missing:
        # A missing frame whose own cut repeats a placed cut is named, with the leave_out it needs: told only "place its
        # cut", a worker placed the cut that repeated one already placed and was refused for the repeat (2026-10-05)
        shown = [frame for frame in missing if _is_frame_cut_shown(paths, cuts, frame, placed_cuts)]
        shown_text = (f"; {', '.join(shown)} already shown: a cut of it repeats a placed cut, so `leave_out` it with the "
                      f"reason that a placed frame shows it, and do not place its cut" if shown else "")
        # A cut already made of a missing frame is named: told only the frame, a worker that had left the cut's line out of
        # a rewrite believed the line was there, twice in one chapter (2026-10-12)
        made = [f"{cut_id} made of {frame} is not in your answer: write `![alt]({cut_id} \"caption\")` on its own line"
                for frame in missing for cut_id, cut in cuts.items()
                if cut.get("frame") == frame and not cut.get("discarded") and cut_id not in placed_cuts]
        made_text = f" ({'; '.join(made)})" if made else ""
        # A PDF's figure is only ever placed as its cut: told it could be replaced or left out, the wiki's writer tried
        # both on the Calvin cycle, a turn each
        is_figures = all(frame_records_service.is_figure_placed_only_as_cut(paths, frame) for frame in missing)
        # A frame named in the writer's own text, on its set-aside line most often, is accounted for by nothing: told
        # only to call `leave_out`, five drafts of one run's chapters wrote the frame on a set-aside line instead
        # (release test 2, DEFECTS 439)
        section_text = section_path.read_text(encoding="utf-8") if section_path.is_file() else ""
        named = [frame for frame in missing if frame in section_text]
        named_text = (f"; {', '.join(named)} is named in your text, which records nothing: call the `leave_out` tool for it "
                      f"(a part you wrote as a markdown block is left out with that reason) and take the line out" if named else "")
        fix = ("`cut` each with no box and no rectangle and place the cut: a figure of the document is never replaced or "
               "left out" if is_figures else
               f"look at each frame, then place its cut, call `replace_with_diagram` for a complete Mermaid replacement, "
               f"or call `leave_out` with an allowed reason{shown_text}{named_text}")
        report.add("DOCUMENT_NEEDED_FRAME_UNACCOUNTED", f"{slug}: needed frame(s) not accounted for: {', '.join(missing)}{made_text}{inline_text}",
                   fix=fix)
    # A frame left out as shown by a placed frame must be shown by one: frames of other dashboard sections were left out
    # as "the same dashboard", which none of the placed cuts showed (2026-10-04)
    report.mark_ran("DOCUMENT_LEAVE_OUT_UNFOUNDED")
    claimed = sorted(dispositions.shown_elsewhere & needed)
    placed_frames = sorted(frame for frame in placed if frame)
    # A frame placed by its own cut is shown, whatever its leave-out says (2026-10-06: a leave-out refused once stayed on
    # record, the worker placed the frame's own cut, and this check refused it again)
    unfounded = [frame for frame in claimed
                 if frame not in placed
                 and not figure_match_service.list_placed_matches(paths, [frame], cuts, written)
                 and not figure_match_service.list_twin_frames(paths, [frame], [other for other in placed_frames
                                                                                if other != frame])
                 and not _is_frame_cut_shown(paths, cuts, frame, placed_cuts)]
    if unfounded:
        report.add("DOCUMENT_LEAVE_OUT_UNFOUNDED",
                   f"{slug}: left out as shown by a placed frame, but no placed frame shows it: {', '.join(unfounded)}",
                   fix="cut and place each of them; leave a frame out as shown elsewhere only when a placed frame shows it")
    # A replacing diagram counts under any heading written so far, as a placed cut does: a frame suggested under two
    # headings is placed once, and a worker who drew it under the second, citing the frame's diagram note, was refused
    # under the first (2026-10-06)
    diagrams = [diagram for other in {slug, *written} if document_file_service.build_section_path(paths, other).is_file()
                for diagram in document_file_service.read_section(paths, other).get("diagrams") or []]
    drawn = {frame for frame in needed & replacements
             if any((note := _FRAME_OF_NOTE.match(str(entry_id))) and (number := _FRAME_NUMBER.fullmatch(frame))
                    and int(note.group(1)) == int(number.group(1))
                    for diagram in diagrams for entry_id in diagram.get("entry_ids") or [])}
    invalid = sorted((needed & replacements) - drawn if not section.get("diagrams") else set())
    if invalid:
        report.add("DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID",
                   f"{slug}: frame(s) recorded as replaced but the section has no Mermaid diagram: {', '.join(invalid)}",
                   fix="write the complete Mermaid diagram in this heading, or place the screenshot instead")

    return report


def validate_chapter_count(paths: RunPaths,
                           slug: str,
                           heading_lines: list[str]) -> ValidationReport:
    """A whole chapter's call: how it ended, then one section for every heading it was handed."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report
    report.mark_ran("DOCUMENT_CHAPTER_SECTIONS")
    path = document_file_service.build_chapter_submission_path(paths, slug)
    answer = document_file_service.read_chapter_submission(paths, slug) if path.is_file() else {}
    written = int(answer.get("sections") or 0)
    # The split goes by position: an answer that rewrote an earlier part's heading was judged as this part's
    # (2026-09-30), so an answer none of whose heading ids was asked for is told so. One long heading can be listed
    # twice under one id, so a single id off the list is left to the count
    heading_count = len(heading_lines)
    asked = section_markdown_utils.list_heading_ids("\n".join(heading_lines))
    written_ids = answer.get("headingIds") or []
    wrong = written_ids if asked and written_ids and not set(written_ids) & set(asked) else []
    if wrong:
        report.add("DOCUMENT_CHAPTER_SECTIONS", f"the answer writes heading(s) {', '.join(wrong)}, which this follow-up does "
                   f"not ask for; it asks for {', '.join(asked)}",
                   fix="answer this follow-up only: write the headings it lists, each with the text its own notes give; "
                       "an earlier part is finished and is never written again")
    elif written != heading_count and len(written_ids) > len(set(written_ids)):
        # An answer that looped holds the chapter several times over: told only "6 section(s) for 1 heading(s)", the
        # writer looked for headings it had not written (the wiki's chapter 6)
        repeated = [heading_id for heading_id, count in collections.Counter(written_ids).items() if count > 1]
        report.add("DOCUMENT_CHAPTER_SECTIONS",
                   "; ".join(f"the heading ({heading_id}) stands {written_ids.count(heading_id)} times" for heading_id in repeated)
                   + ": the answer writes the same text more than once",
                   fix="write each heading's line once, with its text under it once; the answer is the chapter written one time")
    elif written != heading_count:
        # The lines themselves are given: told only "1 section(s) for 3 heading(s)", a worker whose second and third
        # heading lines lacked their ids looked for blank lines (2026-09-30)
        lines = "".join(f"\n{line}" for line in heading_lines)
        report.add("DOCUMENT_CHAPTER_SECTIONS", f"{written} section(s) for {heading_count} heading(s)",
                   fix=f"write, for each of the {heading_count} headings in the order listed, the heading's own line exactly as listed, `### Title (T<chapter>.<heading>)`, "
                       f"its id in brackets included, and its text under it" + (f"; the lines are:{lines}" if lines else ""))

    return report


def validate_summary(paths: RunPaths,
                     part_count: int,
                     known_entry_ids: list[str],
                     max_words: int) -> ValidationReport:
    """The executive summary and the agenda: sentences that cite what the sections rest on, within the word limit,
    and one agenda line per part in the parts' order. REQUIREMENTS.md R11."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, document_file_service.SUMMARY_SLUG),
                                                              document_file_service.SUMMARY_SLUG)
    if not report.ok:
        return report

    report.mark_ran("DOCUMENT_SUMMARY_MISSING")
    if not document_file_service.build_summary_path(paths).is_file():
        report.add("DOCUMENT_SUMMARY_MISSING", "no summary was written", fix="answer through the function you were given")

        return report
    answer = document_file_service.read_summary(paths)
    claims = answer.get("summary") or []
    agenda = answer.get("agenda") or []

    report.mark_ran("DOCUMENT_SUMMARY_EMPTY")
    if not claims:
        report.add("DOCUMENT_SUMMARY_EMPTY", "the executive summary holds no sentence",
                   fix="open with the meeting's main conclusion, then its most important points")

        return report

    # The summary's first sentence was the title word for word, so a reader read the same line twice (2026-10-01)
    report.mark_ran("DOCUMENT_SUMMARY_REPEATS_TITLE")
    title = _read_final_title(paths)
    first = " ".join(str(claims[0].get("text") or "").split()).strip(" .").casefold()
    if title and first == " ".join(title.split()).strip(" .").casefold():
        report.add("DOCUMENT_SUMMARY_REPEATS_TITLE", "the executive summary opens with the title, word for word",
                   fix="open the summary with the meeting's conclusion in other words than the title: what was decided "
                       "or shown, and why it matters")
    _check_title_states_claim(report, title)

    # A summary states each fact as its notes do: three runs' summaries added a degree or a certainty none of their notes
    # gave ("a strict five-step workflow", "guarantee that SPDX tags are applied", "ensuring cybersecurity planning
    # aligns"), DEFECTS 505
    report.mark_ran("DOCUMENT_SUMMARY_DEGREE_UNSOURCED")
    added = _list_unsourced_degree_words(paths, claims)
    if added:
        report.add("DOCUMENT_SUMMARY_DEGREE_UNSOURCED", "summary sentence(s) add a degree or a certainty none of their "
                   "notes gives: " + "; ".join(added),
                   fix="state each fact as its notes state it: drop the word, or use the notes' own word")

    report.mark_ran("DOCUMENT_SUMMARY_UNSOURCED")
    unsourced = [str(claim.get("text") or "") for claim in claims if not claim.get("entry_ids")]
    if unsourced:
        # The sentence named, and the counting sentence's ids explained: told only "a summary sentence", a writer could
        # not tell which, and its closing sentence counting the open points had no ids to give (2026-10-03)
        report.add("DOCUMENT_SUMMARY_UNSOURCED", f"{len(unsourced)} summary sentence(s) name no entry id: "
                   + "; ".join(repr(_shorten_to_first_words(text)) for text in unsourced),
                   fix="give every sentence the ids of the entries it rests on; a sentence counting the rest (the open "
                       "points, the other topics) names the ids of the points it counts")

    report.mark_ran("DOCUMENT_UNKNOWN_SOURCE")
    unknown = sorted({entry_id for claim in claims for entry_id in claim["entry_ids"]} - set(known_entry_ids))
    if unknown:
        report.add("DOCUMENT_UNKNOWN_SOURCE", f"the summary cites id(s) no section rests on: {', '.join(unknown)}",
                   fix="cite only ids the sections you were handed name")

    report.mark_ran("DOCUMENT_SUMMARY_TOO_LONG")
    words = sum(len(str(claim.get("text", "")).split()) for claim in claims)
    if words > max_words:
        report.add("DOCUMENT_SUMMARY_TOO_LONG", f"the summary has {words} words, more than {max_words}",
                   fix=f"keep the {max_words} words for the most important points; the closing lists the rest")

    report.mark_ran("DOCUMENT_AGENDA_PARTS")
    numbers = [line.get("part_number") for line in agenda]
    if numbers != list(range(1, part_count + 1)):
        report.add("DOCUMENT_AGENDA_PARTS", f"the agenda's lines are for points {numbers}",
                   fix=f"write one line for each agenda point, 1 to {part_count}, in order" if part_count
                   else "leave the agenda empty: there are no agenda points")
    check_text(report, document_file_service.SUMMARY_SLUG, _hide_published_addresses(
        paths, "\n".join([str(claim.get("text", "")) for claim in claims] + [str(line.get("text", "")) for line in agenda])))

    return report


def validate_executive_summary_draft(paths: RunPaths,
                                     max_words: int) -> ValidationReport:
    """The conversation's first answer: a working title and three to eight sentences within the summary's word limit,
    obeying what every text of the document obeys. REQUIREMENTS.md R12. It cites nothing: nothing it is written from
    has an id."""

    report = chain_response_validator.validate_chain_response(
        call_log_service.build_call_log_path(paths, document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG), document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG)
    if not report.ok:
        return report

    report.mark_ran("DOCUMENT_SUMMARY_MISSING")
    if not document_file_service.build_executive_summary_draft_path(paths).is_file():
        report.add("DOCUMENT_SUMMARY_MISSING", "no executive summary draft was written",
                   fix="answer through the function you were given")

        return report
    draft = document_file_service.read_executive_summary_draft(paths)
    title = str(draft.get("working_title", "")).strip()
    sentences = [str(sentence).strip() for sentence in draft.get("sentences") or [] if str(sentence).strip()]

    report.mark_ran("DOCUMENT_TITLE_MISSING")
    if not title:
        report.add("DOCUMENT_TITLE_MISSING", "the working title is empty",
                   fix="write one line stating the main thing the meeting establishes")
    _check_title_states_claim(report, title)

    report.mark_ran("EXECUTIVE_SUMMARY_DRAFT_LENGTH")
    words = sum(len(sentence.split()) for sentence in sentences)
    fewest, most = _DRAFT_SENTENCES
    if not fewest <= len(sentences) <= most or words > max_words:
        report.add("EXECUTIVE_SUMMARY_DRAFT_LENGTH", f"the draft has {len(sentences)} sentence(s) and {words} words",
                   fix=f"write {fewest} to {most} sentences, at most {max_words} words in all")
    # The title may open the sentences in other words, as a summary opens with its conclusion: repetition is checked
    # among the sentences only.
    check_text(report, document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG,
               _hide_published_addresses(paths, "\n".join([title, *sentences])), repeat_text="\n".join(sentences))

    return report


def validate_section_links(paths: RunPaths,
                           slug: str,
                           topic_ids: list[str]) -> ValidationReport:
    """R12: every `[T08.3]` a section writes names a topic of the document, so it can become a link."""

    report = ValidationReport(subject=slug)
    section = document_file_service.read_section(paths, slug) if document_file_service.build_section_path(
        paths, slug).is_file() else {}
    linked = [topic_id for paragraph in section.get("paragraphs") or []
              for topic_id in document_text_utils.find_topic_links(str(paragraph.get("text", "")))]
    report.mark_ran("SECTION_LINK_UNKNOWN")
    unknown = sorted(set(linked) - set(topic_ids))
    if unknown:
        report.add("SECTION_LINK_UNKNOWN", f"{slug} links to topic id(s) the document does not have: {', '.join(unknown)}",
                   fix="link only to an id the follow-up lists, or state the point without a link")

    return report


def validate_document(paths: RunPaths) -> ValidationReport:
    report = ValidationReport(subject=output_contract.SUMMARY_FILE)

    # --- existence: always first, and always an early return ---------------------------------
    report.mark_ran("DOCUMENT_MISSING")
    if not paths.summary.is_file() or not paths.summary.stat().st_size:
        report.add("DOCUMENT_MISSING", f"{paths.summary.name} was not written",
                   fix="this is a playbook bug: the program assembles this file")

        return report
    text = paths.summary.read_text(encoding="utf-8")

    report.mark_ran("DOCUMENT_NO_CLOSING_SECTION")
    lines = text.split("\n")
    if not any(f"## {heading}" in lines for heading in document_heading_utils.list_closing_headings()):
        report.add("DOCUMENT_NO_CLOSING_SECTION", f"the closing section, `## {output_contract.OPEN_POINTS_HEADING}` or "
                   f"`## {output_contract.CLOSING_HEADING_ALL}` with its lists, is missing",
                   fix="this is a playbook bug: the program writes that section")

    prose = mermaid_text_utils.remove_blocks(text)
    # Each list one sentence, as the folder's own check reads it (lecture-9, 2026-10-21: two screen lists of one web page
    # passed their chapter and failed here on a side panel line both hold)
    # A time the notes hold is their content here too, as in each section's check: a process table's CPU times ("0:09.96")
    # passed every chapter and failed the document (DEFECTS 398)
    # A repeat is a note here, never a refusal: no model call is left to fix the finished document, and a chapter that
    # repeated a screen's list as its own failed the whole recording after every chapter had passed (a hard fix)
    check_text(report, paths.summary.name, _hide_published_addresses(paths, prose), "",
               TextCheckContext(note_times=document_folder_validator.list_note_times(paths)))
    repeat_text = document_text_utils.join_list_blocks(document_folder_validator.build_repeat_text(text))
    repeated = document_text_utils.find_repeated_sentences(repeat_text)
    if repeated:
        report.note("DOCUMENT_REPEATED_SENTENCE", f"{paths.summary.name}: {len(repeated)} sentence(s) said more than once: "
                    + "; ".join(repr(_shorten_to_first_words(sentence)) for sentence in repeated),
                    fix="say each thing once")

    # --- every paragraph traces to entries that exist ------------------------------------------
    report.mark_ran("DOCUMENT_UNKNOWN_SOURCE")
    report.mark_ran("DOCUMENT_PROVENANCE_MISSING")
    provenance_file = paths.output_root / output_contract.DRAFT_PROVENANCE_FILE
    if provenance_file.is_file():
        provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
        known = set(_list_entry_ids(paths))
        unknown = sorted({entry.get("id", "?")
                          for part in [*provenance.get("paragraphs", []), *provenance.get("diagrams", [])]
                          for entry in part.get("entries", [])} - known)
        if unknown:
            report.add("DOCUMENT_UNKNOWN_SOURCE", f"the provenance names id(s) that do not exist: {', '.join(unknown)}",
                       fix="this is a playbook bug: every section is validated against its topic first")
    else:
        report.add("DOCUMENT_PROVENANCE_MISSING", f"{provenance_file.name} was not written beside the document",
                   fix="this is a playbook bug: the program writes both files together")

    return report


def _name_place(sentence: str,
                places: dict[str, str] | None) -> str:
    """", in the caption of cut-NN" when a figure's own text holds `sentence`; "" when the prose does."""

    folded = " ".join(sentence.split()).casefold()
    place = next((name for part, name in (places or {}).items() if folded and folded in " ".join(part.split()).casefold()),
                 "")

    return f", in {place}" if place else ""


def _hide_published_addresses(paths: RunPaths,
                              text: str) -> str:
    """`text` with each e-mail address a PDF prints in its own text put out of the personal-data check's sight: a
    published document's addresses were published on purpose, as a social handle is (a framework's contact office was
    refused as a person's address). A meeting's screen is no publication, so its addresses stay checked."""

    if not phase_file_service.is_pdf_run(paths):
        return text
    transcript = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"])
    source = "\n".join(str(segment.get("text", "")) for segment in transcript.get("segments", []))
    published = {address.casefold() for address in _ANY_EMAIL.findall(source)}

    return _ANY_EMAIL.sub(lambda match: "the document's address" if match.group(0).casefold() in published
                          else match.group(0), text)


def check_text(report: ValidationReport,
               subject: str,
               text: str,
               repeat_text: str | None = None,
               parts: TextCheckContext | None = None) -> None:
    """The six things no text phase 4 writes may hold. `repeat_text` is the part of it a claim may not be said twice in,
    all of `text` when None; an empty string checks no repetition, for lists that restate the prose on purpose."""

    report.mark_ran("DOCUMENT_TIMESTAMP")
    # A time a note holds is the note's content: the writer is never shown a position in the recording (DEFECTS 379)
    note_times = parts.note_times if parts else frozenset()
    timestamps = [time for time in document_text_utils.find_timestamps(text) if time not in note_times]
    if timestamps:
        report.add("DOCUMENT_TIMESTAMP", f"{subject}: the text holds a clock time: {', '.join(timestamps)}",
                   # Told only "without saying when in the recording", a worker rewrote a meeting's own deadline
                   # "0:30" as "00:30" and was refused again: the fix names both cases
                   fix="a position in the recording is left out: state the fact without saying when it appeared; a "
                       "time of day the meeting names is kept with AM, PM, o'clock or its time zone: 'a cut-off at <hh:mm> AM'")
    # A person's e-mail address stays out of the text as it stays masked on the cuts (2026-09-30: a chapter wrote out
    # the signed-in user's address while the cut of the same page masked it)
    report.mark_ran("DOCUMENT_PERSONAL_IN_TEXT")
    personal_values = document_text_utils.PERSONAL_EMAIL.findall(text) + _find_personal_values(text)
    if personal_values:
        # Each one is named, never counted: told only that its text held two of them, a worker read its whole draft
        # twice and found the wrong one first (2026-10-07). The value goes to the worker that wrote it and nowhere else
        report.add("DOCUMENT_PERSONAL_IN_TEXT",
                   f"{subject}: the text holds the e-mail address(es), account name(s) or record reference(s) of one "
                   f"person: {', '.join(repr(found) for found in dict.fromkeys(personal_values))}",
                   fix="name the person by their role (\"the signed-in user\", \"a test account\") instead of their "
                       "address, and name a record by what it is (\"the order's reference\") instead of copying its "
                       "number or id")
    report.mark_ran("DOCUMENT_BANNED_PHRASE")
    # A block copied from the screen as it stands keeps the screen's words: a slide's list "Network protocol with
    # Frame serial numbers" was asked for as it stands and refused for "frame" (froscon 3211 chapter 2, DEFECTS 293)
    parts = parts or TextCheckContext()
    places = parts.places
    prose = text
    for block in parts.screen_blocks:
        prose = prose.replace(block, "")
    phrases = document_text_utils.find_banned_phrases(prose)
    if phrases:
        where = "; ".join(repr(snippet) for snippet in document_text_utils.find_banned_snippets(prose)[:4])
        report.add("DOCUMENT_BANNED_PHRASE",
                   f"{subject}: the text holds {', '.join(repr(phrase) for phrase in phrases)}"
                   + (f", in: {where}" if where else ""),
                   fix="state the fact about the subject, and never describe the recording; a sentence that only "
                       "describes the recording is dropped, and its entry goes on the `> **Set aside:** <why> [ids]` line; "
                       "the word is banned in its plural and in any use, a verb included: choose another word; the subject's own "
                       "term (a stack or data frame) and a label quoted from a tool's screen in backticks or double "
                       "quotes may stay")
    report.mark_ran("DOCUMENT_RECORDING_NARRATED")
    # The table of contents only repeats the headings, which are checked where they are written and skipped here as
    # heading lines; read as one run of list items it became one sentence, and a heading accepted chapters before
    # failed the finished document (2026-10-04)
    narrated = document_text_utils.find_narrated_sentences(_CONTENTS_SECTION.sub("", text))
    if narrated:
        report.add("DOCUMENT_RECORDING_NARRATED",
                   f"{subject}: {len(narrated)} sentence(s) tell what happened in the recording, not what it teaches: "
                   + "; ".join(f"{_shorten_to_first_words(sentence)!r} (the words {document_text_utils.find_narration_words(sentence)!r}"
                               f"{_name_place(sentence, places)})"
                               # Every one: told "4 sentence(s)" and shown three, a worker searched for the fourth
                               # (2026-10-08)
                               for sentence in narrated),
                   fix="state what the moment establishes about the subject; a sentence whose subject is the talk (\"the lecture covers X\") states what X teaches, in the words of its notes; one that only says what the talk covers, how long it runs or what it is called is dropped and its note goes on the set-aside line, never reworded as a claim about something else (\"the documentation covers\"); who pointed, hovered, scrolled, joked or "
                       "paused, and what the transcript cut off or misheard, is no knowledge: drop the sentence; its "
                       "entry goes on the `> **Set aside:** <why> [ids]` line, a speech visual alignment note into the "
                       "brackets of the block it points at; an open point that only says a step is not shown, written or "
                       "covered yet, or that the speech broke off, goes on the set-aside line too, never reworded into a "
                       "question or a claim the section does not make (\"the syntax is undefined\" beside the syntax); "
                       "and a web address, an action item or a question, which is "
                       "never set aside: one about the subject keeps its `> **Action:**` or `> **Open:**` line, written "
                       "as the task or the question and not as what happened; one about the recording itself (a screen "
                       "to share, a file to open) goes into the brackets of the block it belongs to with no sentence "
                       "of its own")
    report.mark_ran("DOCUMENT_ENTRY_ID_IN_TEXT")
    # A heading's id in a sentence too ("the structure shown in T01.2"): in brackets it is a link, bare a reader reads
    # "T01.2", and it passed every check (Lecture 7's chapter 1, DEFECTS 588)
    entry_ids = document_text_utils.find_entry_ids(text) + document_text_utils.find_bare_topic_ids(text)
    if entry_ids:
        report.add("DOCUMENT_ENTRY_ID_IN_TEXT", f"{subject}: the text holds entry id(s) {', '.join(entry_ids)}",
                   fix="name ids only in the brackets that end a block, such as [F<frame>.<note>, S<line>], never inside a sentence")
    report.mark_ran("DOCUMENT_EMPTY_LIST_ITEM")
    empty_items = document_text_utils.find_empty_list_items(text)
    if empty_items:
        report.add("DOCUMENT_EMPTY_LIST_ITEM", f"{subject}: {len(empty_items)} list item(s) hold no text: "
                   + ", ".join(repr(item) for item in empty_items),
                   fix="write each item's text after its mark, as its note or paragraph gives it, or leave the item out")
    report.mark_ran("DOCUMENT_REPEATED_SENTENCE")
    repeated = document_text_utils.find_repeated_sentences(text if repeat_text is None else repeat_text)
    if repeated:
        report.add("DOCUMENT_REPEATED_SENTENCE", f"{subject}: {len(repeated)} sentence(s) said more than once: "
                   + "; ".join(repr(_shorten_to_first_words(sentence)) for sentence in repeated),
                   fix="say each thing once")


def validate_draft_headings(paths: RunPaths,
                            slug: str,
                            chapter: int,
                            chunk_count: int,
                            entries: list[dict]) -> ValidationReport:
    """The draft headings of one chapter, found before the table of contents (the owner, 2026-09-28): every chunk of the
    chapter's notes read with `read_notes`, a purpose, and at least one heading, each starting at an entry the notes
    show (a transcript line id counts as the speech entry holding it)."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report

    report.mark_ran("DOCUMENT_CHAPTER_NOT_READ")
    calls = conversation_log_service.list_follow_up_calls(paths.output_root / output_contract.WRITE_CONVERSATION_LOG, slug)
    read = set()
    for call in calls:
        if call["name"] != "read_notes" or reading_tool_utils.read_chapter_number(call["args"].get("chapter")) != chapter:
            continue
        # Read as the tool read it: a chunk the tool refused ("two", 2.5) was never served
        try:
            read.add(reading_tool_utils.read_whole_number(call["args"], "chunk", 1))
        except ReadArgumentError:
            continue
    missing = [chunk for chunk in range(1, chunk_count + 1) if chunk not in read]
    if missing:
        report.add("DOCUMENT_CHAPTER_NOT_READ", f"chunk(s) {', '.join(str(chunk) for chunk in missing)} of chapter "
                                                 f"{chapter} were never read",
                   fix=f"read each of them with `read_notes`, chapter {chapter}, before you answer")

    report.mark_ran("DOCUMENT_DRAFT_HEADINGS_MISSING")
    if not document_file_service.build_draft_headings_path(paths, slug).is_file():
        report.add("DOCUMENT_DRAFT_HEADINGS_MISSING", "no draft headings were written",
                   fix="answer through the function you were given")

        return report
    draft = document_file_service.read_draft_headings(paths, slug)
    headings = draft.get("headings") or []

    report.mark_ran("DOCUMENT_DRAFT_HEADINGS_EMPTY")
    if not str(draft.get("purpose") or "").strip() or not headings:
        report.add("DOCUMENT_DRAFT_HEADINGS_EMPTY", "the answer has no purpose or no heading",
                   fix="write what the presenter set out to do in this chapter, and at least one heading")

    report.mark_ran("DOCUMENT_UNKNOWN_SOURCE")
    owners = document_plan_utils.map_start_owners(entries)
    unknown = sorted({str(heading.get("first_entry_id") or "") for heading in headings} - set(owners))
    if unknown:
        report.add("DOCUMENT_UNKNOWN_SOURCE", f"no entry or transcript line has the id {', '.join(unknown)}",
                   fix="start each heading at an id the notes show")
    # Each title is judged as a sentence of its own: joined by line breaks alone, two titles were quoted back as one
    # sentence, and the worker could not tell which one the finding meant
    check_text(report, slug, ".\n".join(str(heading.get("title", "")).strip().rstrip(".") for heading in headings) + ".",
               repeat_text="")

    return report
