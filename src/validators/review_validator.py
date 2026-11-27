"""Validate phase 5's two answers (the review), REQUIREMENTS.md R16. Pure functions over bytes on disk.

The todo list: at most `max_todos`, each saying what is missing, why it matters and where to look, and each quoting a
sentence that stands word for word in the section it names, in the document as the review has it now. A resolution:
a resolved one names one sentence to replace or to follow and it is in its section, every source it cites exists,
every new sentence cites one and obeys what the document's text obeys, the edit leaves every figure, caption and
diagram as it was, and says nothing the document already says; a resolved one that edits nothing says in its
searches where the document already does what the todo asks; an unresolved one lists the searches it made and words
its question for a reader. Every search that names a frame read it in this todo, every question obeys what the
document's text obeys, and an unsupported todo's deletion removes its own sentence only. The program checks
validity, never whether a todo is worth a reader's time.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from prompts import system_prompt_builder
from schemas import review_actions
from services import document_file_service
from services import frame_records_service
from services import document_folder_service
from services import call_log_service
from services import integration_service
from services import phase_file_service
from services import review_file_service
from utils import document_edit_utils
from utils import screenshot_suggestion_utils
from utils import document_text_utils
from validators import chain_response_validator
from validators import document_folder_validator
from validators import document_validator

__all__ = ["validate_review_todos", "validate_resolution", "find_todo_defect", "is_stated_by_its_notes",
           "drop_counted_note_ids", "drop_unknown_sources_beside_known"]

_TODO_FIELDS = ("section", "quote", "missing", "why_it_matters", "where")
# A search naming a frame it read: `read_frame_record frame 14`, `look_at_frame 31`
# Where a quote leaves words out
_ELISION = re.compile(r"…|\.\.\.")
# Where one sentence of a replaced text ends and the next begins
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
# An open question's end: its first question mark
_QUESTION_END = re.compile(r"^(.*?\?)(?=\s|$)", re.DOTALL)


def build_open_question(text: str) -> str:
    """The question a reader is left with, as the document's open points hold it: the text up to its first question
    mark, when it has one. A reviewer wrote "What are …? The transcript only mentions them as …", and the second sentence,
    about the recording, refused the answer and blocked the run; the open point is the question alone."""

    collapsed = _collapse_spaces(text or "")
    match = _QUESTION_END.match(collapsed)

    return match.group(1) if match else collapsed


def list_question_problems(question: str) -> list[str]:
    """What the document's text checks find in an open question, empty when it may stand in the open points. A question
    they find something in is left out of the open points, never the reason its answer is refused: a reviewer that could
    not resolve a todo asked whether a word of its notes was misheard, naming the notes by id and "on the slide", and the
    run blocked on its question after the earlier answers had been refused for what they wrote (DEFECTS 709)."""

    report = ValidationReport(subject="question")
    document_validator.check_text(report, "question", question, repeat_text="")

    return [finding.code for finding in report.findings]


# A review has no `> **Set aside:**` line, so the chapter's fix for a sentence about the recording misled a reviewer that
# had concluded "unresolved" and wrote "the transcript does not mention ..." as a new sentence (2026-10-03)
_REVIEW_FIXES = {"DOCUMENT_RECORDING_NARRATED": "drop that sentence: add only what the sources state about the subject. "
                                                "What the sources do not say is asked in `question`, as one question: keep "
                                                "the supported sentences with status 'resolved', or give none with status 'unresolved'"}

# How much of a section a refused edit target is shown, its lines joined into one
_SECTION_SHOWN_CHARS = 600


def _collapse_spaces(text: object) -> str:
    """`text` on one line: every run of spaces and line breaks one space."""

    return " ".join(str(text).split())


def _check_searches_made(report: ValidationReport,
                         paths: RunPaths,
                         slug: str,
                         answer: dict[str, Any]) -> None:
    """Every frame a search says was read was read in this conversation, by this follow-up or one before it: a resolution
    cited 'read_frame_record frame 14: confirms …' as its source and had made no read at all (2026-10-08). A frame the
    todo before it read is in the conversation too: held to its own todo's turns, a resolution naming the frames read
    for the todo before was refused and blocked the review (pdf-02-report-v2)."""

    report.mark_ran("RESOLUTION_SEARCH_NOT_MADE")
    read_frames = review_file_service.list_read_frames(paths, slug)
    # Frame numbers compared as numbers: `frame-003` names frame 3
    unmade = [str(search) for search in answer.get("searches") or []
              if any(int(number) not in read_frames for number in review_file_service.NAMED_FRAMES.findall(str(search)))]
    if unmade:
        report.add("RESOLUTION_SEARCH_NOT_MADE", f"{slug}: a search names a frame this review never read: {unmade[0]!r}"
                   f" (frames read so far: {', '.join(str(number) for number in sorted(read_frames)) or 'none'})",
                   fix="list only the reads you made: read that frame first, or drop the search")


def _read_answer(paths: RunPaths,
                 slug: str) -> tuple[ValidationReport, dict[str, Any] | None]:
    """How the call ended, then the answer the program wrote from it; None when either is not there."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report, None
    report.mark_ran("REVIEW_ANSWER_MISSING")
    if not review_file_service.build_answer_path(paths, slug).is_file():
        report.add("REVIEW_ANSWER_MISSING", f"{slug}: no answer was written",
                   fix="this is a playbook bug: the program writes the answer the submit tool carried")

        return report, None

    return report, review_file_service.read_answer(paths, slug)


def _read_document(paths: RunPaths) -> str:
    """The document as the review has it now, without its front matter."""

    _front_matter, body = document_edit_utils.split_front_matter(
        review_file_service.build_working_document_path(paths).read_text(encoding="utf-8"))

    return body


def _read_known_sources(paths: RunPaths) -> tuple[set[str], set[str], set[int]]:
    """The ids of the transcript's lines, the ids of every entry, and the frames' numbers."""

    integration = integration_service.build_integration(paths)
    entries = list(integration.get("entries", []))
    frames_preparing_phase = output_contract.PHASE_NUMBERS["frames_preparing"]
    items = (phase_file_service.read_phase_file(paths, frames_preparing_phase).get("items", [])
             if phase_file_service.has_phase_file(paths, frames_preparing_phase) else [])
    speech_ids = {entry["id"] for entry in entries if entry.get("kind") == "speech"}

    return speech_ids, {entry["id"] for entry in entries}, {int(item["index"]) for item in items}


# A note id that continues a count of one frame's notes, one past the id before it: a resolution listed "F10.9",
# "F10.10", ... on to "F10.1000", hundreds of notes frame 10 does not have, and was refused for them (froscon 3211,
# DEFECTS 297)
_NOTE_ID = re.compile(r"^F(\d+)\.(\d+)$")


def drop_counted_note_ids(answer: dict,
                          paths: RunPaths) -> dict:
    """The resolution with each unknown note id dropped that only counts on from the id before it in its sentence's
    list (same frame, next number); any other unknown id stays, for the check to name."""

    known = _read_known_sources(paths)[1]
    sentences = []
    for sentence in answer.get("sentences") or []:
        kept, previous = [], None
        for entry_id in sentence.get("entry_ids") or []:
            match, last = _NOTE_ID.match(str(entry_id)), _NOTE_ID.match(str(previous or ""))
            counted = (match and last and match.group(1) == last.group(1)
                       and int(match.group(2)) == int(last.group(2)) + 1)
            if entry_id in known or not counted:
                kept.append(entry_id)
            previous = entry_id
        sentences.append({**sentence, "entry_ids": kept} if "entry_ids" in sentence else sentence)

    return {**answer, "sentences": sentences} if "sentences" in answer else answer


def drop_unknown_sources_beside_known(answer: dict,
                                      paths: RunPaths) -> dict:
    """The resolution with each unknown source dropped from a sentence that keeps a known one: the sentence still
    rests on its known sources, and the checks of its numbers and terms read those. A sentence with no known source
    keeps its ids, for RESOLUTION_SOURCE_UNKNOWN to name (DEFECTS 723: "F27.27" beside real notes of frame 27)."""

    speech_ids, entry_ids, frames = _read_known_sources(paths)
    sentences = []
    for sentence in answer.get("sentences") or []:
        known = {"transcript_ids": [source for source in sentence.get("transcript_ids") or [] if source in speech_ids],
                 "entry_ids": [source for source in sentence.get("entry_ids") or [] if source in entry_ids],
                 "frames": [frame for frame in sentence.get("frames") or [] if frame in frames]}
        if any(known.values()):
            sentence = {**sentence, **{key: value for key, value in known.items() if key in sentence}}
        sentences.append(sentence)

    return {**answer, "sentences": sentences} if "sentences" in answer else answer


# A number as written in a sentence: digits, with a thousands or decimal mark between groups
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
# The frame a note's id names: F13.1 is frame 13's
_CITED_FRAME = re.compile(r"^F(\d+)\.\d+$")
# A name written in capitals, digits allowed after its letters ("ZED", "QA02", "ZEB-3"): an environment, a system or a
# product, which a resolution must not take from anywhere but its sources
_CAPS_TERM = re.compile(r"(?<![\w-])[A-Z]{2,}(?:-?\d+)?(?![\w-])")


def _read_cited_texts(paths: RunPaths,
                      sentence: dict[str, Any]) -> list[str]:
    """The texts a new sentence cites: its entries and lines, every note of a frame it names, and the outline of each
    frame it cites by number or by a note."""

    cited = set(sentence.get("entry_ids") or []) | set(sentence.get("transcript_ids") or [])
    frames = {f"F{frame}." for frame in sentence.get("frames") or []}
    texts = [str(entry.get("text") or "") for entry in integration_service.build_integration(paths).get("entries", [])
             if entry["id"] in cited or any(str(entry["id"]).startswith(prefix) for prefix in frames)]
    # A frame cited, by its number or by one of its notes, is cited with its outline, which `read_frame_record` shows the
    # reviewer: "Read 12 lines" stood in frames 13 and 14's outlines alone, and their correction was refused (DEFECTS 612)
    cited_frames = {int(frame) for frame in sentence.get("frames") or [] if str(frame).isdigit()} | {
        int(match.group(1)) for entry_id in cited if (match := _CITED_FRAME.match(str(entry_id)))}
    if cited_frames:
        records = frame_records_service.map_records_by_index(paths)
        texts += [str(records.get(frame, {}).get("screen") or "") for frame in cited_frames]

    return texts


def _list_unsourced_numbers(paths: RunPaths,
                            sentence: dict[str, Any],
                            replaced: str) -> list[str]:
    """The numbers a new sentence states that none of its sources, nor the text it replaces, holds: an accepted
    resolution put a count from another note of the frame into the document, citing two notes that gave a different
    count (2026-10-10). A number is compared without its thousands marks."""

    texts = _read_cited_texts(paths, sentence)
    held = {_strip_number_marks(number) for text in [*texts, replaced] for number in _NUMBER.findall(text)}

    # A figure's number is the program's, never a source's: "shown in Figure 2" was refused as an unsourced 2 (pdf-05)
    text = document_text_utils.FIGURE_MENTION.sub("", str(sentence.get("text") or ""))

    return [number for number in _NUMBER.findall(text) if _strip_number_marks(number) not in held]


def _read_term_letters(term: str) -> str:
    """A capitals name's letters alone, so "ZEB3", "ZEB-3" and "ZEB 03" are one name."""

    return re.sub(r"[^A-Z]", "", term.upper())


def _list_unsourced_terms(paths: RunPaths,
                          sentence: dict[str, Any],
                          replaced: str) -> list[str]:
    """The capitals names a new sentence states that none of its sources, nor the text it replaces, holds, a name the
    term mapping says the transcript writes as a word its sources hold counting as held: an accepted resolution swapped
    an environment the speakers named twice for another one from a list, citing a line that held only the first."""

    # A cited frame's outline counts, as for numbers: a chart's legend labels stood in frame 11's outline alone, and a
    # figure's description naming them was refused twice (DEFECTS 710)
    texts = _read_cited_texts(paths, sentence)
    held = {_read_term_letters(word) for text in [*texts, replaced] for word in re.findall(r"[A-Za-z]+\d*", text)}
    held |= {_read_term_letters(term) for word, term in system_prompt_builder.read_transcript_spellings().items()
             if any(re.search(rf"(?<![\w-]){re.escape(word)}(?![\w])", text, re.IGNORECASE) for text in texts)}

    return [term for term in _CAPS_TERM.findall(str(sentence.get("text") or "")) if _read_term_letters(term) not in held]


def _strip_number_marks(number: str) -> str:
    return number.replace(",", "").replace(".", "")


def _list_unknown_sources(sentences: list[dict[str, Any]],
                          known: tuple[set[str], set[str], set[int]]) -> list[str]:
    speech_ids, entry_ids, frames = known
    unknown = []
    for sentence in sentences:
        unknown += [f"{source} is not a transcript line of this meeting"
                    for source in sentence.get("transcript_ids") or [] if source not in speech_ids]
        unknown += [f"{source} is not an entry of this meeting" for source in sentence.get("entry_ids") or []
                    if source not in entry_ids]
        unknown += [f"{frame} is not a frame of this meeting" for frame in sentence.get("frames") or []
                    if frame not in frames]

    return list(dict.fromkeys(unknown))


def _describe_section(document: str,
                      section: str) -> str:
    headings = document_edit_utils.list_headings(document)
    wanted = document_edit_utils.normalise_heading(section)
    if any(document_edit_utils.is_named(heading, wanted) for heading in headings):
        return f'section "{section}"'

    return f'section "{section}", which the document does not have (its headings: {"; ".join(headings)})'


def _check_unresolved(report: ValidationReport,
                      paths: RunPaths,
                      slug: str,
                      answer: dict[str, Any]) -> None:
    report.mark_ran("RESOLUTION_NOTES_UNREAD")
    reads = [read.split(" ")[0] for read in review_file_service.list_follow_up_reads(paths, slug)]
    if not any(read in review_actions.NOTES_READS for read in reads):
        report.add("RESOLUTION_NOTES_UNREAD", f"{slug}: \"unresolved\" after reading no frame's notes in this "
                   f"follow-up ({', '.join(reads) or 'no read at all'}); what was shown on screen is only in them",
                   fix="read the moments of the time the todo names with `read_moments`, or `find` one distinctive word, "
                       "then answer")
    report.mark_ran("RESOLUTION_NO_SEARCH")
    if not any(str(search).strip() for search in answer.get("searches") or []):
        report.add("RESOLUTION_NO_SEARCH", f'{slug}: "unresolved" lists no search',
                   fix="say what you looked for and where")
    question = build_open_question(answer.get("question") or "")
    report.mark_ran("RESOLUTION_NO_QUESTION")
    if not question:
        report.add("RESOLUTION_NO_QUESTION", f'{slug}: "unresolved" words no question for the reader',
                   fix="write the question a reader of the document is left with, in one sentence")


def _check_edit_target(report: ValidationReport,
                       slug: str,
                       answer: dict[str, Any],
                       document: str) -> tuple[int, int] | None:
    """Where the edit's sentence stands; None, with the finding, when it names none, both, or one that is not there."""

    section, replaces, after = (str(answer.get(key) or "") for key in ("section", "replaces", "after"))
    replaces = replaces if replaces.strip() else ""
    report.mark_ran("RESOLUTION_TARGET_MISSING")
    if bool(replaces) == bool(after.strip()):
        report.add("RESOLUTION_TARGET_MISSING", f"{slug}: the edit names {'both' if replaces else 'neither'} "
                   "a sentence to replace and a sentence to follow",
                   fix="give `replaces` to change a sentence, or `after` to add after one, never both")

        return None
    span = document_edit_utils.find_quote(document, section, replaces or after)
    if span is None:
        # The section as it reads now: an earlier todo's answer had already replaced the sentence a later todo was
        # about ("Nothing was left open." became the open point), and the worker, told only "not in the section",
        # copied the old sentence again and reported the check wrong
        current = document_edit_utils.read_sections(document, section)
        now = (f'; the section now reads: "{" ".join(current[0].split())[:_SECTION_SHOWN_CHARS]}"' if current else "")
        report.add("RESOLUTION_TARGET_MISSING", f"{slug}: the sentence to {'replace' if replaces else 'follow'} is not "
                   f"in {_describe_section(document, section)}{now}",
                   fix="copy it exactly as the document has it now, under the heading it stands under; an earlier todo's "
                       "edit may have changed it, and when the section already does what this todo asks, answer "
                       "\"resolved\" with no edit and no sentence and say in `searches` where it does")

    return span


_SECTION_IDS = re.compile(r"\[((?:[FST]\d+(?:\.\d+)?(?:,\s*)?)+)\]")
_SECTION_FRAME_ID = re.compile(r"F(\d+)")


def list_replaced_frames(paths: RunPaths,
                         replaced_text: str) -> set[int]:
    """The frames the block of the section that holds `replaced_text` rests on (the frame numbers of the entry ids in
    brackets that end the block, as the writer cited them); empty when no section holds it. A review resolution replaced
    a sentence of the version 17 section on frames 60 and 61 and never read frame 77, the one the sentence rested on
    (release test 2, DEFECTS 443)."""

    key = re.sub(r"\W+", " ", str(replaced_text).casefold()).strip()[:80]
    sections_dir = document_file_service.build_sections_dir(paths)
    if not key:
        return set()
    for path in sorted(sections_dir.glob("*.md")) if sections_dir.is_dir() else []:
        for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
            if key in re.sub(r"\W+", " ", _SECTION_IDS.sub("", block).casefold()):
                return {int(number) for ids in _SECTION_IDS.findall(block) for number in _SECTION_FRAME_ID.findall(ids)}
    # The executive summary is no section file: its sentences carry their entry ids in the summary's own file (a resolution
    # replaced a summary sentence citing frame 8's notes for a claim of the Q&A, release test 3, DEFECTS 466)
    if document_file_service.build_summary_path(paths).is_file():
        for sentence in document_file_service.read_summary(paths).get("summary") or []:
            if key in re.sub(r"\W+", " ", str(sentence.get("text", "")).casefold()):
                return {int(number) for entry in sentence.get("entry_ids") or [] for number in _SECTION_FRAME_ID.findall(str(entry))}

    return set()


def _check_replaced_source_read(report: ValidationReport,
                                paths: RunPaths,
                                slug: str,
                                replaced_text: str,
                                new: list[dict[str, Any]]) -> None:
    """A sentence that replaces another cites at least one frame the replaced sentence rests on: told only to cite
    sources that exist, a resolution cited the frames of the other section's slide and rewrote a version claim wrongly. Over
    the public runs 4 of 41 such resolutions cited none (DEFECTS 443)."""

    report.mark_ran("RESOLUTION_REPLACED_SOURCE_NOT_READ")
    if not replaced_text.strip() or not new:
        return
    rests_on = list_replaced_frames(paths, replaced_text)
    cited = {int(frame) for sentence in new for frame in sentence.get("frames") or [] if str(frame).isdigit()}
    cited |= {int(number) for sentence in new for entry in sentence.get("entry_ids") or []
              for number in _SECTION_FRAME_ID.findall(str(entry))}
    if rests_on and cited and not rests_on & cited:
        report.add("RESOLUTION_REPLACED_SOURCE_NOT_READ",
                   f"{slug}: the sentence you replace rests on frame(s) {', '.join(str(frame) for frame in sorted(rests_on))}, "
                   f"and your new sentence cites none of them",
                   fix="read the notes or the frame of the sentence you replace and cite it for what you write, or leave "
                       "the sentence as it is")


def _check_resolved(report: ValidationReport,
                    paths: RunPaths,
                    slug: str,
                    answer: dict[str, Any]) -> None:
    # STEP 1: something to add, and a place in the document to put it
    sentences = [sentence for sentence in answer.get("sentences") or [] if str(sentence.get("text") or "").strip()]
    report.mark_ran("RESOLUTION_NO_SENTENCE")
    # A resolution that names a sentence to replace and adds none deletes it: the fix of a sentence about the recording
    # is "nothing", and the check refused that answer (2026-10-02)
    # One that edits nothing records that the document already does what the todo asks, said in `searches`: a todo the
    # program's own repair had settled had no answer to give (2026-10-02)
    is_edit = bool(str(answer.get("replaces") or "").strip() or str(answer.get("after") or "").strip())
    if not sentences and not is_edit and any(str(search).strip() for search in answer.get("searches") or []):
        return
    if not sentences and not str(answer.get("replaces") or "").strip():
        report.add("RESOLUTION_NO_SENTENCE", f'{slug}: "resolved" adds no sentence and deletes none',
                   fix="write the sentences the sources support; to delete a sentence, copy it into `replaces` and add "
                       "none; when the document already does what the todo asks, give no edit and say in `searches` "
                       "where it does; or mark the todo unresolved")

        return
    document = _read_document(paths)
    span = _check_edit_target(report, slug, answer, document)
    if span is None:
        return

    # STEP 2: every new sentence rests on sources that exist; a sentence the edit only carries over needs none
    # Only a whole sentence of the replaced text is carried over: a part of one says something else
    replaced = {_collapse_spaces(part) for part in _SENTENCE_BREAK.split(document[span[0]:span[1]])} \
        if str(answer.get("replaces") or "").strip() else set()
    new = [sentence for sentence in sentences if _collapse_spaces(sentence["text"]) not in replaced]
    report.mark_ran("RESOLUTION_NO_SOURCE")
    for sentence in new:
        if not any(sentence.get(key) for key in ("transcript_ids", "frames", "entry_ids")):
            report.add("RESOLUTION_NO_SOURCE", f'{slug}: the new sentence "{sentence["text"]}" cites no source',
                       fix="cite the transcript lines, frames or entries that say it, or mark the todo unresolved")
    report.mark_ran("RESOLUTION_SOURCE_UNKNOWN")
    unknown = _list_unknown_sources(sentences, _read_known_sources(paths))
    if unknown:
        report.add("RESOLUTION_SOURCE_UNKNOWN", f"{slug}: {'; '.join(unknown)}", fix="cite ids the tools returned")
    report.mark_ran("RESOLUTION_NUMBER_UNSOURCED")
    replaced_text = document[span[0]:span[1]] if str(answer.get("replaces") or "").strip() else ""
    _check_replaced_source_read(report, paths, slug, replaced_text, new)
    # A sentence with no source at all is RESOLUTION_NO_SOURCE alone: its number or term is unsourced only because
    # nothing is cited, and told also that its term "none of its sources says", a worker hunted for another word for
    # one its transcript said (lecture-9 review, 2026-10-21)
    new = [sentence for sentence in new if any(sentence.get(key) for key in ("transcript_ids", "frames", "entry_ids"))]
    for sentence in new:
        numbers = _list_unsourced_numbers(paths, sentence, replaced_text)
        if numbers:
            report.add("RESOLUTION_NUMBER_UNSOURCED",
                       f'{slug}: the new sentence "{sentence["text"]}" states {", ".join(numbers)}, which none of its '
                       f"sources holds",
                       # A range's two ends stood on two pages, and the sentence cited the page of one end (pdf-02)
                       fix="cite the entry or transcript line that states each number, or leave the number out; a "
                           "range or a list (the codes from A-100 to A-180) cites the notes of each of its ends, which "
                           "can stand on different frames")
    report.mark_ran("RESOLUTION_TERM_UNSOURCED")
    for sentence in new:
        terms = _list_unsourced_terms(paths, sentence, replaced_text)
        if terms:
            report.add("RESOLUTION_TERM_UNSOURCED",
                       f'{slug}: the new sentence "{sentence["text"]}" names {", ".join(terms)}, which none of its '
                       f"sources says",
                       fix="write the name its sources say; when they say a word the term list maps to a team term, "
                           "that term; when you cannot tell which name was meant, keep the sentence as it is and "
                           "answer \"unresolved\" with the question")

    # STEP 3: the edited document keeps its figures and diagrams, and its new text obeys the document's rules
    edited = document_edit_utils.apply_edit(document, answer)
    report.mark_ran("RESOLUTION_FIGURE_TOUCHED")
    # A figure named by its number in a new sentence is told apart: told the edit "removes or changes a figure", a
    # reviewer that had only added "shown in Figure 2" could not tell what it had done (pdf-05)
    kept = set(document_text_utils.FIGURE_MENTION.findall(replaced_text))
    named = [mention for mention in document_text_utils.FIGURE_MENTION.findall(" ".join(str(sentence["text"])
                                                                                        for sentence in new))
             if mention not in kept]
    if named:
        report.add("RESOLUTION_FIGURE_TOUCHED",
                   f"{slug}: a new sentence names Figure {', '.join(dict.fromkeys(named))}: a figure's number is the "
                   "program's, and a new sentence never points to a figure",
                   fix="write what the sentence says about the subject, without naming the figure")
    elif document_edit_utils.list_figure_marks(edited) != document_edit_utils.list_figure_marks(document):
        report.add("RESOLUTION_FIGURE_TOUCHED",
                   f"{slug}: the edit removes or changes a figure, its caption, a sentence that points to it, or a "
                   "diagram",
                   fix="leave figures and diagrams as they are")
    document_validator.check_text(report, slug, "\n".join(str(sentence["text"]) for sentence in new), repeat_text="")
    report.mark_ran("DOCUMENT_REPEATED_SENTENCE")
    earlier = set(document_folder_validator.find_repeated_document_sentences(document))
    repeated = [sentence for sentence in document_folder_validator.find_repeated_document_sentences(edited)
                if sentence not in earlier]
    if repeated:
        report.add("DOCUMENT_REPEATED_SENTENCE", f"{slug}: the document already says {repeated[0]!r}",
                   fix="say each thing once: add only what the document does not hold")


def _is_within_quote(text: str,
                     quote: str) -> bool:
    """Whether `text` holds no more than `quote` names: a part of it, or, for a quote with words left out ("…"), the
    whole sentence from its first part to its last, every part in order. Both folded and stripped of a full stop."""

    parts = [part.strip(" .") for part in _ELISION.split(quote) if part.strip(" .")]
    if len(parts) < 2:
        return text in quote
    position = 0
    for part in parts:
        position = text.find(part, position)
        if position < 0:
            return False
        position += len(part)

    return text.startswith(parts[0]) and text.endswith(parts[-1])


def _check_deletion_scope(report: ValidationReport,
                          slug: str,
                          answer: dict[str, Any],
                          todo_kind: str,
                          todo_quote: str) -> None:
    """An unsupported todo's deletion removes its own sentence and nothing more: two deletions of one run took a
    supported sentence along (the meeting's date, a lesson learned) because `replaces` held it too (2026-10-03)."""

    report.mark_ran("RESOLUTION_DELETES_MORE")
    # Code marks are left out of both: the todo quoted a sentence without them and the answer copied it from the
    # document with them, refused as deleting more (2026-10-12)
    replaces = _collapse_spaces((answer.get("replaces") or "").replace("`", "")).casefold().strip(" .")
    quote = _collapse_spaces(todo_quote.replace("`", "")).casefold().strip(" .")
    is_deletion = bool(replaces) and not any(str(sentence.get("text") or "").strip()
                                             for sentence in answer.get("sentences") or [])
    if todo_kind == "unsupported" and is_deletion and quote and not _is_within_quote(replaces, quote):
        report.add("RESOLUTION_DELETES_MORE", f"{slug}: `replaces` holds more than the todo's sentence {todo_quote!r}",
                   fix="copy into `replaces` only the sentence the todo names; the sentences beside it stay")


# A word or a number of a sentence, for what a gap's fix keeps: three letters or more, or any digits
_KEPT_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|\d")
# A gap's fix keeps at least this share of the words of the sentence it replaces: a fix that lost a manual's expiry
# rule kept 1 of 11, one that named who set a rule ("Jonas requires the backup job to run …" for "A requirement is
# established that the backup job must run …") 12 of 16, refused at 0.8 (pdf-06)
_KEPT_SHARE = 0.5
# A sentence is what its notes state when they hold this share of its words
_STATED_SHARE = 0.8
# A replaced line states something to keep from this many words of three letters or more (a number counted too)
_STATED_WORDS = 4


def _list_kept_words(text: str) -> set[str]:
    return {word for word in _KEPT_WORD.findall(text.casefold()) if len(word) >= 3 or word.isdigit()}


def is_stated_by_its_notes(paths: RunPaths,
                            sentence: str) -> bool:
    """Whether the notes the document wrote `sentence` from hold its words, so it is what a source states."""

    sources = set(document_file_service.find_quote_sources(paths, sentence))
    words = _list_kept_words(sentence)
    if not sources or not words:
        return False
    held = _list_kept_words(" ".join(str(entry.get("text") or "")
                                     for entry in integration_service.build_integration(paths).get("entries", [])
                                     if entry["id"] in sources))

    return len(words - held) <= (1 - _STATED_SHARE) * len(words)


def _check_text_kept(report: ValidationReport,
                     paths: RunPaths,
                     slug: str,
                     answer: dict[str, Any],
                     todo_kind: str) -> None:
    """A gap or an open question is closed by adding what is missing: its fix keeps the sentence it replaces. A fix
    replaced "Sandbox keys expire after 14 days; live keys never expire but can be revoked" with a renewal step no
    source states, and the expiry rules left the document (pdf-05). So is an `unsupported` todo whose sentence its own
    notes state: a fix replaced "Error 401 means the token expired; the recovery step is to sign in again", which its
    page's notes said word for word, with a second cause of the error alone (pdf-05)."""

    report.mark_ran("RESOLUTION_DROPS_TEXT")
    replaces = str(answer.get("replaces") or "")
    replaced = _list_kept_words(replaces)
    written = " ".join(str(sentence.get("text") or "") for sentence in answer.get("sentences") or []).casefold()
    # A line of under four words deleted outright drops no statement: a bare open point ("Open: cookie cutter") asks
    # nothing, and its gap's fix deleting it was refused and blocked phase 5 (38c3 talk 797, DEFECTS 550)
    if not replaced or (len(replaced) < _STATED_WORDS and not answer.get("sentences")):
        return
    # An unsupported sentence may still be deleted: only one rewritten is held to it. So is an open question's: its
    # sentence says what is not known, and the answer the sources give replaces it ("no answer was provided" was kept
    # against a question answered at 58:08, DEFECTS 614), unless its notes state that sentence
    if todo_kind != "gap" and not (todo_kind in ("unsupported", "open_question") and answer.get("sentences")
                                   and is_stated_by_its_notes(paths, replaces)):
        return
    lost = sorted(word for word in replaced if word not in written)
    if len(lost) > (1 - _KEPT_SHARE) * len(replaced):
        report.add("RESOLUTION_DROPS_TEXT", f"{slug}: the fix replaces a sentence and leaves out what it said: "
                   + ", ".join(lost[:12]),
                   fix=("a gap is closed by adding what is missing: keep what the sentence you replace says in your "
                        "new sentences and add to it, or leave `replaces` empty and put the new sentence in `after`"
                        if todo_kind in ("gap", "open_question") else
                        "the sentence you replace is what its sources state: keep what it says in your new sentences "
                        "and add to it, or leave `replaces` empty and put the new sentence in `after`"))


def _apply_review_fixes(report: ValidationReport) -> None:
    for finding in report.findings:
        if finding.code in _REVIEW_FIXES:
            finding.fix = _REVIEW_FIXES[finding.code]


# A line of the document is the quote's nearest sentence when it holds at least this share of the quote's words, in order
_NEAREST_SHARE = 0.5
# Words shown either side of where a quote and its nearest sentence part
_DIFFERENCE_CONTEXT = 2


def _describe_nearest_difference(document: str,
                                 quote: str) -> str:
    """Where a quote parts from the document line it nearly copies, as "; its nearest sentence has 'a b' where yours has
    'a c'", or "" when no line holds half of it: a quote with one word changed was sent three times, its worker told
    each time only that the document does not hold it (2026-10-10)."""

    quote_words = quote.split()
    best = None
    for line in document.splitlines():
        shared = sum(block.size for block in difflib.SequenceMatcher(None, quote_words, line.split(),
                                                                     autojunk=False).get_matching_blocks())
        if shared >= _NEAREST_SHARE * len(quote_words) and (best is None or shared > best[0]):
            best = (shared, line)
    if best is None:
        return ""
    line_words = best[1].split()
    for tag, start, end, line_start, line_end in difflib.SequenceMatcher(None, quote_words, line_words,
                                                                         autojunk=False).get_opcodes():
        # The line's other sentences, before or after the quoted one, are no difference
        if tag == "equal" or (tag == "insert" and start in (0, len(quote_words))):
            continue
        before = max(0, start - _DIFFERENCE_CONTEXT)
        line_before = max(0, line_start - _DIFFERENCE_CONTEXT)
        yours = " ".join(quote_words[before:end + _DIFFERENCE_CONTEXT - 1])
        theirs = " ".join(line_words[line_before:line_end + _DIFFERENCE_CONTEXT - 1])
        return f"; its nearest sentence has {theirs!r} where yours has {yours!r}"

    return ""


def find_todo_defect(document: str,
                     todo: dict[str, Any],
                     todo_id: str) -> tuple[str, str, str] | None:
    """What is wrong with one todo of the critique, as (code, detail, fix); None when nothing is. The rejection says
    where the sentence really stands, or that the document does not hold it: found on the first real run, where a
    worker told only "not in this section" sent a sentence of the speech three times over."""

    empty = [field_name for field_name in _TODO_FIELDS if not str(todo.get(field_name) or "").strip()]
    if empty:
        # A contradiction stands in two blocks, and a todo has room for one: a reviewer named both only in its text and
        # left the block and the sentence empty (38c3-openpv, DEFECTS 638)
        return ("REVIEW_TODO_INCOMPLETE", f"{todo_id} leaves {', '.join(empty)} empty",
                "give every todo its block number, its section, its sentence, what is missing, why it matters and where "
                "the answer may be; a contradiction gives the block and the sentence of its first side, and names the "
                "other side's block in what is missing")
    if document_edit_utils.find_quote(document, todo["section"], todo["quote"]) is not None:
        return None
    heading = document_edit_utils.find_quote_heading(document, todo["quote"])
    # A text the document holds only inside a fenced block (a code block, a slide's list printed as it stands) is no
    # sentence to quote or edit: told "the document does not hold it anywhere", a reviewer was sent to look for a sentence
    # that stands in the document (release test 2, DEFECTS 442)
    in_fence = heading is None and str(todo["quote"]).strip() in document
    where = (f'it stands under "{heading}"' if heading is not None
             else "that text stands inside a fenced block (a code block, or a slide's list printed as it stands), which a "
                  "todo cannot quote: quote a sentence of the document's own text instead" if in_fence
             else "the document does not hold this sentence anywhere: quote the document itself, never the "
                  "transcript or the notes" + _describe_nearest_difference(document, todo["quote"]))

    return ("REVIEW_QUOTE_NOT_FOUND",
            f"{todo_id}'s sentence is not in {_describe_section(document, todo['section'])} word for word; {where}",
            "copy the sentence exactly as the document has it, under the heading it stands under")


def validate_review_todos(paths: RunPaths,
                          slug: str,
                          max_todos: int) -> ValidationReport:
    report, answer = _read_answer(paths, slug)
    if answer is None:
        return report
    todos = list(answer.get("todos") or [])
    document = _read_document(paths)

    report.mark_ran("REVIEW_TOO_MANY")
    if len(todos) > max_todos:
        report.add("REVIEW_TOO_MANY", f"{len(todos)} todos, at most {max_todos}",
                   fix=f"keep the {max_todos} a reader loses most without")
    report.mark_ran("REVIEW_TODO_INCOMPLETE")
    report.mark_ran("REVIEW_QUOTE_NOT_FOUND")
    for number, todo in enumerate(todos, start=1):
        defect = find_todo_defect(document, todo, review_file_service.build_todo_id(number))
        if defect is not None:
            code, detail, fix = defect
            report.add(code, detail, fix=fix)

    return report


# A markdown block that can stand in place of a picture of code, a table or a list
_BLOCK_START = re.compile(r"^(```|~~~|\||-\s|\*\s|\d+\.\s)")


def _check_figure_action(report: ValidationReport,
                         paths: RunPaths,
                         slug: str,
                         answer: dict[str, Any],
                         todo_kind: str) -> None:
    """A figure taken out or replaced by a markdown block: only for a figure todo, only a figure line the document holds,
    and a replacement that is one markdown block read off frames it names (the owner, 2026-10-12)."""

    report.mark_ran("RESOLUTION_FIGURE_ACTION")
    line = str(answer.get("replaces") or "").strip()
    block = str(answer.get("markdown_block") or "").strip()
    if todo_kind != "figure":
        report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: a figure is removed or replaced only for a `figure` todo",
                   fix="leave `figure_action` empty and edit sentences, or mark the todo unresolved")
        return
    if not line.startswith("![") or document_edit_utils.replace_figure(_read_document(paths), line, "") is None:
        report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: `replaces` is not a figure line of the document",
                   fix="copy the figure's line, `![...](...)`, exactly as the document has it")
        return
    # A figure phase 3 marked needed stays: its legend unreadable at the recording's own size, a chart of a day's
    # temperatures was removed, though no text carries its curves (DEFECTS 247)
    if answer.get("figure_action") == review_actions.FIGURE_REMOVE:
        frame = document_folder_service.find_figure_frame(paths, line)
        record = next((record for record in frame_records_service.read_frame_records(paths)
                       if record.get("slug") == frame), None) if frame else None
        if record is not None and screenshot_suggestion_utils.list_needed_slugs([record]):
            report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: the figure is of {frame}, which the frame's notes mark "
                                                    f"needed: a reader cannot do without it",
                       fix="answer `keep`, and write what its small text says in the section's sentences when the "
                           "figure cannot show it more clearly; or `recut` a larger part of the frame")
    if answer.get("figure_action") == review_actions.FIGURE_RECUT:
        cut = (document_file_service.read_image_store(paths).get("cuts") or {}).get(str(answer.get("cut") or ""))
        if cut is None or not (paths.output_root / str(cut.get("file") or "")).is_file():
            report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: `cut` names no cut you made",
                       fix="`cut` the whole part from the figure's frame, look at it, and give its id, `cut-<n>`")
    if answer.get("figure_action") == review_actions.FIGURE_REPLACE:
        is_fenced = block.startswith(("```", "~~~")) and block.rstrip().endswith(("```", "~~~")) and "\n" in block
        if not block or not _BLOCK_START.match(block) or (block.startswith(("```", "~~~")) and not is_fenced):
            report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: `markdown_block` is not one markdown block",
                       fix="give the figure's content as a fenced code block, a markdown table or a list, read off "
                           "its frame; or choose `remove` or `keep`")
        if not answer.get("block_frames"):
            report.add("RESOLUTION_FIGURE_ACTION", f"{slug}: `block_frames` names no frame the block is read from",
                       fix="name the frames you read the block off with `look_at_frame`")


def validate_resolution(paths: RunPaths,
                        slug: str,
                        todo_kind: str = "",
                        todo_quote: str = "") -> ValidationReport:
    report, answer = _read_answer(paths, slug)
    if answer is None:
        return report
    _check_searches_made(report, paths, slug, answer)
    # The question goes into the document's open points only when it obeys what the document's text obeys; one that does
    # not is left out there (`list_question_problems`), never refused here (DEFECTS 709)
    if answer.get("status") == "unresolved":
        _check_unresolved(report, paths, slug, answer)
    elif str(answer.get("figure_action") or "") in (review_actions.FIGURE_REMOVE, review_actions.FIGURE_REPLACE,
                                                    review_actions.FIGURE_RECUT):
        _check_figure_action(report, paths, slug, answer, todo_kind)
    else:
        _check_resolved(report, paths, slug, answer)
        _check_deletion_scope(report, slug, answer, todo_kind, todo_quote)
        _check_text_kept(report, paths, slug, answer, todo_kind)
    _apply_review_fixes(report)

    return report
