"""The document assembled from phase 4's files: `04_document.md` and `04_document.provenance.json`.

The PROGRAM assembles the document. The sections are the agent's markdown, each block naming the merged entries it
rests on, and this module turns them into two files:

  04_document.md               what a person opens: the subject, with no timestamp, no frame number
                                and no description of the recording
  04_document.provenance.json  every paragraph with its entries, their timestamps and their frames

The document opens with the executive summary, the agenda and the table of contents (REQUIREMENTS.md R11), has one
chapter per agenda point in the meeting's order (R9), and closes with one section: the open points always, and the
decisions and the action items when the sections state any (`_render_closing`). The open points are what the
sections left open, plus the program's own lines when part of the frames could not be analysed or the meeting could
not be divided into its parts. Their wording avoids every phrase the document may not contain, because the validator
reads the whole file, this module's own sentences included.

A diagram that another one in the document contains line for line is an earlier version of it: it is
kept in the provenance, marked `superseded`, and not drawn.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import config as app_config
from models import output_contract
from models.document_heading_spec import DocumentHeadingSpec
from models.document_spec import DocumentChapterSpec, DocumentSpec, DocumentTopicSpec
from models.run_paths import RunPaths
from services import ingesting_profile_service
from utils import document_heading_utils
from utils import document_plan_utils
from utils import document_text_utils
from utils import json_store_utils
from utils import mermaid_text_utils
from utils import section_markdown_utils

__all__ = ["list_printed_topic_headings", "list_held_screen_ids", "build_coverage", "write_document"]

# An open point that names terms the meeting never explained: its quoted terms join one line, never one item each
_UNEXPLAINED_TERM = re.compile(r'"([^"]+)"')
_UNEXPLAINED = re.compile(r"\b(?:not (?:fully )?explained|never explained|(?:definition|meaning) [^.?]*\b(?:unclear|not "
                          r"explained)|what does .+ (?:refer to|mean))", re.IGNORECASE)
_UNEXPLAINED_LINE = "Terms the meeting used and never explained: {terms}."

_SOME_FRAMES_FAILED = ("Part of the visual material could not be analysed, so what was visible only there is "
                         "missing from this document.")
_ALL_FRAMES_FAILED = ("None of the visual material could be analysed. Everything in this document rests on what was "
                        "spoken.")
_NOT_ARRANGED = "The meeting could not be divided into its parts, so the topics follow the order in which they came up."


def _is_unexplained_terms_line(line: str) -> bool:
    return bool(_UNEXPLAINED.search(line) and _UNEXPLAINED_TERM.search(line))


def _list_located_sections(plan: DocumentSpec) -> list[tuple[DocumentChapterSpec, DocumentTopicSpec, dict[str, Any]]]:
    """Every section of the plan with the chapter and the topic it stands in, in document order."""

    return [(chapter, topic, section) for chapter in plan.chapters for topic in chapter.topics
            for section in topic.sections]


def _list_sections(plan: DocumentSpec) -> list[dict[str, Any]]:
    return [section for _, _, section in _list_located_sections(plan)]


_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)


# A batch's question names the frames it is about, "(Frame 1)", "(Frames 3-4)", "mentioned in Frame 2", "in the Frame 19
# facts and transcript", which the reader of the document never sees: a published document's open points held three, and
# its banned-word check failed phase 4 after every chapter was accepted (the Matrix talk)
_FRAME_POINTER = re.compile(r"\s*\((?:frames?|page)\s[\d\s,–-]+(?:and\s\d+)?\)", re.IGNORECASE)
_FRAME_SLIDE = re.compile(r"\bthe\s+frames?\s+\d+\s+(slide|screen)\b", re.IGNORECASE)
_FRAME_PLACE = re.compile(r",?\s+(?:as\s+)?(?:(?:mentioned|listed|shown|seen|named|written)\s+)?(?:in|on)\s+(?:the\s+)?"
                          r"frames?\s+\d+(?:\s*(?:,|and|-|–)\s*\d+)*"
                          r"(?:\s+(?:facts|notes|transcript|screen|slide)(?:\s+and\s+(?:facts|notes|transcript))?)?",
                          re.IGNORECASE)


# The words of an open point that name what it is about: four letters or more, without the question's own words
_POINT_WORD = re.compile(r"[a-z][a-z0-9-]{3,}")
_QUESTION_WORDS = frozenset({"what", "which", "when", "where", "does", "that", "this", "with", "from", "have", "there",
                             "their", "about", "each", "specific", "exact", "exactly", "whether", "they", "used"})
_QUOTED_TERM = re.compile(r'"([^"]+)"')
# An open line holding this share of a question's words holds its point
_HELD_POINT_SHARE = 0.5


def _is_point_held(question: str,
                   lines: list[str]) -> bool:
    """Whether one of `lines` is about the point `question` asks: it names a term the question quotes, or holds half
    the question's words (two at least)."""

    words = {word for word in _POINT_WORD.findall(question.casefold()) if word not in _QUESTION_WORDS}
    terms = [term.casefold() for term in _QUOTED_TERM.findall(question)]
    for line in lines:
        folded = line.casefold()
        held = {word for word in words if word in set(_POINT_WORD.findall(folded))}
        if any(term in folded for term in terms) or (len(held) >= 2 and len(held) >= _HELD_POINT_SHARE * len(words)):
            return True

    return False


def _drop_frame_pointers(question: str) -> str:
    """A batch's question with every pointer to the frames it is about taken out: "the Frame 19 slide" becomes "the slide",
    "mentioned in Frame 2" and "(Frames 3-4)" go."""

    question = _FRAME_SLIDE.sub(r"the \1", question)

    return _FRAME_PLACE.sub("", _FRAME_POINTER.sub("", question)).strip()


# A bracket inside a batch's question, with the space before it
_BRACKET = re.compile(r"\s*\([^()]*\)")


def _drop_source_brackets(question: str) -> str:
    """A batch's question about the recording, with its brackets taken out when the rest is about the subject: "Is the
    range 1-60 (as stated by the presenter) or 1-63 (as shown in the help text)?" named who said each value, and the
    closing left the whole question out (froscon talk 3211's address range, DEFECTS 303)."""

    if not document_text_utils.find_narrated_sentences(question):
        return question
    unbracketed = _BRACKET.sub("", question).strip()

    return unbracketed if not document_text_utils.find_narrated_sentences(unbracketed) else question


def _is_about_subject(question: str) -> bool:
    """Whether a batch's question asks about the subject, as the document's own checks judge a sentence: one that tells
    what the recording, its transcript or a speaker did, or names a frame, is about the recording and is left out, on
    the console. Phase 3's instruction already asks for a question a reader with no frames understands, and one batch
    still wrote "In Frame 100, the transcript quotes Speaker F asking …": the published document then failed phase 4's
    last check after every chapter and the summary were accepted (the Photon talk)."""

    found = document_text_utils.find_narrated_sentences(question) or document_text_utils.find_banned_phrases(question)
    if found:
        _LOG.info(f"  open points: a batch's question about the recording is left out: {question!r}")

    return not found


def _render_closing_lines(plan: DocumentSpec,
                          integration: dict[str, Any],
                          headings: DocumentHeadingSpec) -> list[str]:
    """The open points, each once, in the closing's order, or the one line that says nothing is open."""

    # STEP 1: what the plan and the sections left open, and what each batch of phase 3 asked and its answers step could
    # not answer: a PDF's batch left four questions open that no section marked, and the document said nothing was
    # left open (pdf-01)
    lines = [*plan.not_established, *(line for section in _list_sections(plan) for line in section.get("not_established", []))]
    # A batch's question a section already marked open is the same point asked again: pdf-04's open points listed
    # "The identity of "Keyhole" is not explained" and "What is the identity of "Keyhole"?", four points twice
    questions = [_drop_source_brackets(_drop_frame_pointers(str(question))) for batch in integration.get("batchSummaries") or []
                 for question in batch.get("openQuestions") or []]
    lines += [question for question in questions if not _is_point_held(question, lines) and _is_about_subject(question)]

    # STEP 2: the program's own lines, for frames not analysed and a meeting not divided
    counts = integration.get("counts", {})
    if counts.get("frames") and counts.get("framesAccepted") == 0:
        lines.append(_ALL_FRAMES_FAILED)
    elif integration.get("gaps"):
        lines.append(_SOME_FRAMES_FAILED)
    if not plan.is_arranged:
        lines.append(_NOT_ARRANGED)

    # STEP 3: each once; a question with its answer after it is no longer open so it leaves the list
    unique = list(dict.fromkeys(line.strip() for line in lines if line.strip()))
    still_open = [line for line in unique if not document_text_utils.is_answered_question(line)]

    # STEP 4: the terms the meeting never explained, joined into one line at the top
    terms = [term for line in still_open if _is_unexplained_terms_line(line) for term in _UNEXPLAINED_TERM.findall(line)]
    grouped = [line for line in still_open if not _is_unexplained_terms_line(line)]
    if terms:
        grouped.insert(0, _UNEXPLAINED_LINE.format(terms=", ".join(f'"{term}"' for term in dict.fromkeys(terms))))

    return grouped or [headings.nothing_open]


def _build_one_line_text(text: Any) -> str:
    """`text` on one line, every run of white space one space."""

    return " ".join(str(text).split())


def _read_claim_text(claim: dict[str, Any]) -> str:
    """The sentence a claim states, without the white space round it."""

    return str(claim.get("text", "")).strip()


def _list_claims(plan: DocumentSpec,
                 field_name: str) -> list[dict[str, Any]]:
    """Every decision or action item the sections state, each once, in document order."""

    claims: dict[str, dict[str, Any]] = {}
    for section in _list_sections(plan):
        for claim in section.get(field_name) or []:
            claims.setdefault(_build_one_line_text(claim.get("text", "")).casefold(), claim)

    return [claim for key, claim in claims.items() if key]


def _list_cited(entries: dict[str, dict[str, Any]],
                entry_ids: list[str]) -> list[dict[str, Any]]:
    """The entries a block cites, from the merge. An id the merge does not hold is listed bare, with no time or frame,
    so the provenance still names it."""

    return [entries.get(entry_id, {"id": entry_id}) for entry_id in entry_ids]


def _is_topic_heading_printed(chapter: DocumentChapterSpec) -> bool:
    """A topic gets its own heading unless it is the one topic of a titled chapter, whose heading stands for it."""

    return not chapter.title.strip() or len(chapter.topics) > 1


def _render_image(image: dict[str, Any]) -> list[str]:
    """A frame a chapter placed: its cut, then its caption. Publishing, at the end of phase 4,
    reads the pair back as a placed figure (`publication_service`), numbers it and moves its file into the published
    folder."""

    described = _build_one_line_text(image.get("description", ""))

    return [f"![{_build_one_line_text(image.get('alt_text', ''))}]({image.get('cutFile', '')})", "",
            f"*{_build_one_line_text(image.get('caption', ''))}*", "",
            *([f"> **What the figure shows:** {described}", ""] if described else [])]


# A marked block keeps its mark in the document, where it was said; the closing lists it again.
_CLAIM_MARKS = {"decision": "Decision", "action": "Action", "open": "Open"}


def _render_block(block: dict[str, Any],
                  section: dict[str, Any]) -> list[str]:
    """One block of a section other than a diagram, as the reader reads it: the text without its note ids."""

    if block["kind"] == "image":
        image = next((candidate for candidate in section.get("images") or [] if candidate["cut"] == block["cut"]), {})

        return _render_image(image)
    if block["kind"] in ("subheading", "heading"):
        return [f"#### {block['text']}", ""]
    if block["kind"] in _CLAIM_MARKS:
        return [f"> **{_CLAIM_MARKS[block['kind']]}:** {block['text']}", ""]
    if block["kind"] == "set_aside":
        return []
    if block["kind"] == "code":
        return ["```", block["text"], "```", ""]

    return [block["text"].strip(), ""]


def _render_section_blocks(section: dict[str, Any],
                           superseded: set[int],
                           position: int,
                           titles: tuple[str, ...] = ()) -> tuple[list[str], int]:
    """The section's blocks in the order its writer laid them out, each without its note ids, and the position of the
    next diagram in the document: a diagram a later one holds whole (`superseded`) is not drawn, nor a subheading
    saying again one of the `titles` above it (its chapter's, its heading's): a PDF's writer made the page's heading
    a subheading under the chapter of the same name, "#### Incidents" under "## Incidents" (pdf-02)."""

    above = {" ".join(title.split()).casefold() for title in titles if title.strip()}
    lines: list[str] = []
    for block in section.get("blocks") or []:
        if block["kind"] in ("subheading", "heading") and " ".join(str(block["text"]).split()).casefold() in above:
            continue
        if block["kind"] != "diagram":
            lines += _render_block(block, section)
            continue
        if position not in superseded:
            lines += ["```mermaid", mermaid_text_utils.strip_fence(block["text"]), "```", ""]
        position += 1

    return lines, position


def _render_chapters(plan: DocumentSpec,
                     superseded: set[int]) -> tuple[list[str], list[tuple[int | None, str]]]:
    """The chapters' markdown, and every heading it holds in document order as `(chapter index, text)`, a topic's
    heading carrying None."""

    lines: list[str] = []
    printed_headings: list[tuple[int | None, str]] = []
    position = 0
    for index, chapter in enumerate(plan.chapters):
        topic_level = "##"
        if chapter.title.strip() and chapter.topics:
            lines += [f"## {chapter.title.strip()}", ""]
            printed_headings.append((index, chapter.title.strip()))
            topic_level = "###"
        for topic in chapter.topics:
            if _is_topic_heading_printed(chapter):
                heading = topic.title
                lines += [f"{topic_level} {heading}", ""]
                printed_headings.append((None, heading))
            for section in topic.sections:
                section_lines, position = _render_section_blocks(section, superseded, position,
                                                                 (chapter.title, topic.title))
                lines += section_lines

    return lines, printed_headings


def _list_top_headings(plan: DocumentSpec,
                       headings: DocumentHeadingSpec,
                       has_contents: bool) -> list[str]:
    """The headings printed before the chapters, in order: the title, then the executive summary, the agenda and the
    contents when they print (`_render_summary`, `_render_agenda` and `_render_contents` print nothing without them).
    The chapters' anchors are worked out after these, so this list must agree with those three."""

    return ([plan.title.strip()] + ([headings.executive_summary] if plan.summary else [])
            + ([headings.agenda] if plan.agenda else []) + ([headings.contents] if has_contents else []))


def _render_summary(plan: DocumentSpec,
                    headings: DocumentHeadingSpec) -> list[str]:
    """The executive summary: its first sentence, the main conclusion, as a paragraph, and the rest as a list."""

    if not plan.summary:
        return []
    rest = [f"- {_read_claim_text(claim)}" for claim in plan.summary[1:]]

    return [f"## {headings.executive_summary}", "", _read_claim_text(plan.summary[0]), "",
            *rest, *([""] if rest else [])]


def _render_agenda(plan: DocumentSpec,
                   chapter_anchors: dict[int, str],
                   headings: DocumentHeadingSpec) -> list[str]:
    """One numbered line per part, linked to its chapter, then what was announced and never reached."""

    if not plan.agenda:
        return []
    lines = [f"## {headings.agenda}", ""]
    for number, line in enumerate(plan.agenda, start=1):
        index = int(line.get("part_number", 0)) - 1
        title = plan.chapters[index].title.strip() if 0 <= index < len(plan.chapters) else ""
        label = f"[{title}](#{chapter_anchors[index]})" if title and index in chapter_anchors else title
        lines.append(f"{number}. {label}: {str(line.get('text', '')).strip()}")
    if plan.not_covered:
        lines += ["", f"{headings.announced_not_covered}: {'; '.join(item.strip() for item in plan.not_covered)}."]

    return [*lines, ""]


def _render_contents(printed_headings: list[tuple[int | None, str]],
                     anchors: list[str],
                     title: str) -> list[str]:
    """The table of contents as a list a reader clicks: each chapter, and under it each heading,
    a link to the heading it names. Built by the program from the headings the document prints, so no link can miss;
    asked for only when a chapter heading is printed."""

    lines = [f"## {title}", ""]
    for (index, text), anchor in zip(printed_headings, anchors):
        lines.append(f"{'' if index is not None else '  '}- [{text}](#{anchor})")

    return [*lines, ""]


def _render_closing(plan: DocumentSpec,
                    integration: dict[str, Any],
                    headings: DocumentHeadingSpec) -> list[str]:
    """The one closing section: the decisions and the action items when the meeting stated any, each under its own
    heading, and the open points always. A list with nothing in it is left out, and the title names what remains."""

    decisions = [f"- {_read_claim_text(claim)}" for claim in _list_claims(plan, "decisions")]
    actions = [f"- {_read_claim_text(claim)}" for claim in _list_claims(plan, "action_items")]
    lists = [(heading, items) for heading, items in ((headings.decisions, decisions), (headings.action_items, actions))
             if items]
    open_points = [f"- {line}" for line in _render_closing_lines(plan, integration, headings)]
    if not lists:
        return [f"## {headings.open_points}", "", *open_points]
    if decisions and actions:
        title = headings.closing_all
    else:
        title = headings.closing_decisions if decisions else headings.closing_actions
    lines = [f"## {title}", ""]
    for heading, items in lists:
        lines += [f"### {heading}", "", *items, ""]

    return [*lines, f"### {headings.open_points}", "", *open_points]


def list_printed_topic_headings(plan: DocumentSpec) -> list[list[str]]:
    """For each chapter, the heading each of its topics is printed under: its own, or its chapter's (R12's links)."""

    return [[topic.title if _is_topic_heading_printed(chapter) else chapter.title.strip()
             for topic in chapter.topics] for chapter in plan.chapters]


def list_held_screen_ids(entries: list[dict[str, Any]],
                         cited: set[str],
                         written_blocks: list[str]) -> set[str]:
    """The markdown notes no section cites whose block one of `written_blocks` holds: a screen shown twice, its block
    written once from the other note."""

    written = [section_markdown_utils.list_block_lines(text) for text in written_blocks]

    return {entry["id"] for entry in entries if entry.get("kind") == "markdown" and entry["id"] not in cited
            and section_markdown_utils.is_block_held(
                section_markdown_utils.list_block_lines(str(entry.get("text") or "")), written)}


def build_coverage(plan: DocumentSpec,
                   integration: dict[str, Any]) -> dict[str, Any]:
    """The coverage table (REQUIREMENTS.md R8): what became of every layer-2 entry, the count of each status, and the
    same count over the transcript's segments alone."""

    sections = _list_sections(plan)
    cited = {entry_id for section in sections for block in section.get("blocks") or [] for entry_id in block["entry_ids"]}
    cited |= {entry_id for claim in plan.summary for entry_id in claim.get("entry_ids", [])}
    # A screen's block an earlier section already writes is no note to cite (DEFECTS 236): the chapter request took it
    # off the section's list, and counted missing here it named two blocks Lecture 7's document holds (DEFECTS 606)
    held = list_held_screen_ids(integration.get("entries", []), cited, [
        str(block.get("text") or "") for section in sections for block in section.get("blocks") or []])
    statuses = document_plan_utils.list_entry_statuses(integration, cited, set(plan.set_aside_ids), held)
    speech = {entry["id"] for entry in integration.get("entries", []) if entry["kind"] == "speech"}

    return {"entries": statuses, "counts": dict(Counter(statuses.values())),
            "segments": dict(Counter(status for entry_id, status in statuses.items() if entry_id in speech))}


def _build_cited_claims(entries: dict[str, dict[str, Any]],
                        claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each claim as its text, unstripped, with the entries it cites."""

    return [{"text": claim.get("text", ""), "entries": _list_cited(entries, claim.get("entry_ids", []))}
            for claim in claims]


def _build_provenance(plan: DocumentSpec,
                      integration: dict[str, Any],
                      drawn: list[tuple[DocumentChapterSpec, DocumentTopicSpec, dict[str, Any]]],
                      superseded: set[int]) -> dict[str, Any]:
    """`04_document.provenance.json`: every paragraph, diagram, decision and action item with the entries it rests on,
    their timestamps and frames, and the coverage table."""

    entries = {entry["id"]: entry for entry in integration.get("entries", [])}
    # A span of unread speech is cited under its first line's id, and its provenance names every line in it
    entries.update({entry["id"]: entry for entry in document_plan_utils.select_document_entries(integration)})

    return {"schemaVersion": 3, "title": plan.title, "parts": plan.parts, "folds": plan.folds,
            "summary": _build_cited_claims(entries, plan.summary),
            "agenda": plan.agenda, "announcedNotCovered": plan.not_covered,
            "chapters": [{"title": chapter.title, "topics": [topic.title for topic in chapter.topics]}
                         for chapter in plan.chapters],
            "setAside": _list_cited(entries, plan.set_aside_ids),
            "paragraphs": [{"chapter": chapter.title, "heading": topic.title,
                            "text": paragraph.get("text", ""),
                            "entries": _list_cited(entries, paragraph.get("entry_ids", []))}
                           for chapter, topic, section in _list_located_sections(plan)
                           for paragraph in section.get("paragraphs", [])],
            "diagrams": [{"chapter": chapter.title, "heading": topic.title,
                          "mermaid": mermaid_text_utils.strip_fence(str(diagram.get("mermaid", ""))),
                          "superseded": position in superseded,
                          "entries": _list_cited(entries, diagram.get("entry_ids", []))}
                         for position, (chapter, topic, diagram) in enumerate(drawn)],
            "decisions": _build_cited_claims(entries, _list_claims(plan, "decisions")),
            "actionItems": _build_cited_claims(entries, _list_claims(plan, "action_items")),
            "coverage": build_coverage(plan, integration),
            "contradictions": integration.get("contradictions", []), "gaps": integration.get("gaps", []),
            "unreadSpeech": integration.get("unreadSpeech", [])}


def write_document(paths: RunPaths,
                   plan: DocumentSpec,
                   integration: dict[str, Any]) -> Path:
    """Assemble and write the document and its provenance file. Returns the document's path."""

    # STEP 1: which diagrams an extension later in the document already holds
    drawn = [(chapter, topic, diagram) for chapter, topic, section in _list_located_sections(plan)
             for diagram in section.get("diagrams") or []]
    superseded = document_plan_utils.list_superseded_diagrams(
        [mermaid_text_utils.strip_fence(str(diagram.get("mermaid", ""))) for _, _, diagram in drawn])

    # STEP 2: the markdown, which is about the subject and nothing else: the title, the executive summary, the agenda
    # linked to the chapters, the chapters in the meeting's order, and the closing
    chapter_lines, chapter_headings = _render_chapters(plan, superseded)
    has_contents = any(index is not None for index, _ in chapter_headings)
    # In the document's language: English, or the recording's own for a profile written in it
    headings = document_heading_utils.select_headings(ingesting_profile_service.read_document_language(paths.output_root))
    top_headings = _list_top_headings(plan, headings, has_contents)
    anchors = document_text_utils.build_heading_anchors(top_headings + [text for _, text in chapter_headings])
    chapter_heading_anchors = anchors[len(top_headings):]
    chapter_anchors = {index: anchor for (index, _), anchor in zip(chapter_headings, chapter_heading_anchors)
                       if index is not None}
    lines = [f"# {plan.title.strip()}", "", *_render_summary(plan, headings), *_render_agenda(plan, chapter_anchors, headings),
             *(_render_contents(chapter_headings, chapter_heading_anchors, headings.contents) if has_contents else []),
             *chapter_lines, *_render_closing(plan, integration, headings)]
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # STEP 3: the provenance, which holds every timestamp and frame number the markdown may not
    json_store_utils.write_json(paths.output_root / output_contract.DRAFT_PROVENANCE_FILE,
                                _build_provenance(plan, integration, drawn, superseded))

    return paths.summary
