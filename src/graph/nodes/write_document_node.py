"""Phase 4: layer 3, the file a person opens. REQUIREMENTS.md R3, R9, R11 and R12.

A long meeting is far more than one call can hold: an 87-minute one gives about 1,900 entries to write from. By
default (`document.writeMode: "conversation"`, R12) phase 4 is one session, one step, one worker: every piece is a
follow-up of one conversation and no separate call runs. In order: the executive summary draft, the agenda, a fresh
start, the draft headings of each chapter (one follow-up per agenda point, reading that point in the notes file), the
table of contents the PROGRAM builds from those drafts with no model call, one follow-up per chapter's text, and the
final title and summary.

With `document.writeMode: "calls"` the same document is written in separate calls, each through the chain attempt
loop with its own retry budget, attempt directories and validator:

  1. one PARTS call sees the meeting as a numbered list of moments and divides it into the meeting's own
     parts, in the order they happened, and names the agenda the presenter announced;
  2. one OUTLINE call per chunk of a part sorts that chunk's entries into topics, as data, told the
     topics earlier chunks made, so a subject that comes back continues its topic;
  3. one CONTENTS call sees every part's and every topic's title and writes the document's title;
  4. one SECTION call per topic part writes it, told what the topic's earlier parts already say, checked
     against every sentence the document already holds, and names the decisions and action items it states;
  5. one SUMMARY call, last, writes the executive summary and the agenda from the parts and the sections.

The document is written from the notes AND the speech: every frame's spoken lines are one entry, and so is each
span no frame read, so no separate step looks for what was said and never noted.

The PROGRAM decides which entries the document is written from, which part each falls in, where the
chunks are cut (never across a part), which topics are one and which short return is folded back where
its subject was first discussed, then assembles the document and writes the provenance file beside it
(`utils/document_plan_utils.py`). A chapter is one part of the meeting, in the meeting's order.

A blocked step stops the phase: nothing after it is asked, the phase is recorded as blocked, and `--resume` asks the
blocked step again.

On a resume, a part already on disk that still passes its validator is kept. A new run clears the
parts a previous run left, so a document never mixes parts written under two plans.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Callable

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph.graph_state import RunState
from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models.document_spec import DocumentChapterSpec, DocumentSpec, DocumentTopicSpec
from models.job_retry_context import JobRetryContext
from models.part_writing_context import PartWritingContext
from models.pipeline_errors import DescriptionsDoNotFit, MissingPhaseInput, PhaseBlocked
from models.run_context import RunContext
from models.run_paths import RunPaths
from models.timing_handles import TimingHandles
from models.write_conversation_state import WriteConversationState
from services import batch_summaries_service
from services import call_log_service
from services import chain_dispatch_service
from services import document_assembly_service
from services import document_conversation_service
from services import document_file_service
from services import dry_run_service
from services import fast_document_service
from services import fast_frame_note_service
from services import figure_match_service
from services import frame_records_service
from services import ingesting_profile_service
from services import integration_service
from services import pdf_conversion_service
from services import performance_log_service
from services import phase_file_service
from services import publication_service
from services import run_tags_service
from services import takeaway_document_service
from services import validation_service
from services import video_workflow_service
from services import write_conversation_service
from services import write_reading_service
from utils import chain_response_utils
from utils import document_plan_utils
from utils import document_text_utils
from utils import finding_text_utils
from utils import frame_markdown_utils
from utils import frame_slug_utils
from utils import garbled_name_utils
from utils import meeting_notes_utils
from utils import screenshot_suggestion_utils
from utils import section_markdown_utils
from utils import speech_markdown_utils
from utils import starting_messages_utils

PHASE = "finalize"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]

PARTS_KIND = "parts"
OUTLINE_KIND = "outline"
CONTENTS_KIND = "contents"
SECTION_KIND = "section"
# One whole chapter, every heading of it, in the conversation.
CHAPTER_KIND = "chapter"
SUMMARY_KIND = "summary"
DOCUMENT_KIND = "finalize"
TAKEAWAY_KIND = "takeaway"
PARTS_SLUG = document_file_service.PARTS_SLUG
CONTENTS_SLUG = document_file_service.CONTENTS_SLUG
SUMMARY_SLUG = document_file_service.SUMMARY_SLUG
# The title of a document with no topic, or whose working title came back empty.
UNARRANGED_TITLE = "What the recording established"
# The title call sees one entry of each topic, cut to this length.
EXAMPLE_CHARS = 160
# A section is told the other topics of its chapter, at most this many.
OTHER_TOPICS_LIMIT = 40
# The parts whose reply describes other parts, and so must be asked again when those change.
_INPUT_BOUND_KINDS = (CONTENTS_KIND, SUMMARY_KIND)
# The blocks a later chapter's request names as written already, never to write again (`earlierBlocks`)
_WRITTEN_BLOCK_KINDS = ("code", "list", "table")
# The executive summary's share of the document, REQUIREMENTS.md R11: at most a tenth.
SUMMARY_SHARE = 10
# REQUIREMENTS.md R12: the document written as one conversation of follow-ups, and its first follow-up.
CONVERSATION_MODE = "conversation"
EXECUTIVE_SUMMARY_DRAFT_KIND = "executive_summary_draft"
EXECUTIVE_SUMMARY_DRAFT_SLUG = document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG
# The reading of one agenda point before the table of contents, `chapter-02`.
DRAFT_HEADINGS_KIND = "draft_headings"
# How the console names a follow-up when it starts: what it is doing, as a verb. One asked many
# times names its place, `writing chapter 4 of 9`.
ONE_OFF_LABELS = {EXECUTIVE_SUMMARY_DRAFT_SLUG: "drafting the executive summary", PARTS_SLUG: "building the agenda",
                  SUMMARY_SLUG: "writing the final executive summary"}


def _mark_phase(ctx: RunContext,
                status: str,
                handles: TimingHandles) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def _list_blocked_reasons(ctx: RunContext) -> dict[str, str]:
    """slug -> why this run blocked that frame, from the store. Empty when this run blocked none."""

    return {slug: str(entry.get("blockedReason", "blocked")) for slug, entry in ctx.store.list_items().items()
            if entry.get("status") == "blocked"}


def _read_max_attempts(ctx: RunContext) -> int:
    return ctx.store.read_budget("maxAttempts", app_config.settings.budgets.max_attempts)


def _run_part(ctx: RunContext,
              job_kind: str,
              slug: str,
              written_path: Path,
              inputs: dict) -> bool:
    """One call of the phase through the chain attempt loop. True when it was accepted."""

    handles = performance_log_service.start_unit(ctx, PHASE, slug)
    job = JobRetryContext(phase=PHASE, job_kind=job_kind,
                          snapshot_targets=[written_path, call_log_service.build_call_log_path(ctx.paths, slug)],
                          inputs=inputs)
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=slug,
                                                               max_attempts=_read_max_attempts(ctx))
    if not outcome.is_accepted:
        ctx.log.warning(f"  {slug} blocked after {outcome.attempts} attempt(s): {outcome.summary}")
    performance_log_service.record_unit(ctx, handles, performance_log_service.PART_LEVEL,
                                        "accepted" if outcome.is_accepted else "blocked", [])

    return outcome.is_accepted


def _is_part_accepted_on_disk(ctx: RunContext,
                              job_kind: str,
                              inputs: dict) -> bool:
    """Does the part a previous life of THIS run wrote still pass its validator?"""

    return validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, job_kind, inputs=inputs)).ok


def _keep_or_ask_part(ctx: RunContext,
                      job_kind: str,
                      slug: str,
                      written_path: Path,
                      inputs: dict) -> bool:
    """The part kept from disk when it still validates, asked for otherwise. True when it is accepted. The title and the
    summary are also asked again when what they were written from changed: they still validate against new sections,
    and would describe the old ones."""

    is_input_bound = job_kind in _INPUT_BOUND_KINDS
    is_unchanged = not is_input_bound or document_plan_utils.is_inputs_hash_current(written_path, inputs)
    if is_unchanged and _is_part_accepted_on_disk(ctx, job_kind, inputs):
        return True
    is_accepted = _run_part(ctx, job_kind, slug, written_path, inputs)
    if is_accepted and is_input_bound:
        document_plan_utils.build_inputs_hash_path(written_path).write_text(
            document_plan_utils.compute_inputs_hash(inputs), encoding="utf-8")

    return is_accepted


def _read_recording_name(ctx: RunContext) -> str:
    """The recording's file name: it often holds the meeting's name, a person's name and the date (R13)."""

    return (ctx.paths.repository / ctx.paths.module).name


def _read_transcript_speakers(ctx: RunContext) -> list[str]:
    """The speaker names the meeting tool's own transcript carries, recorded by phase 1 (R14); empty when none."""

    transcript_number = output_contract.PHASE_NUMBERS["transcribe"]
    if not phase_file_service.has_phase_file(ctx.paths, transcript_number):
        return []

    return list(phase_file_service.read_phase_file(ctx.paths, transcript_number).get("providedTranscript", {})
                .get("speakers") or [])


def _read_screen_text(ctx: RunContext) -> dict[str, list[str]]:
    """What the first frames show and what most frames repeat (a title slide, a footer): where a deck names its author,
    for the agenda step to name the speakers from."""

    records = frame_records_service.read_frame_records(ctx.paths)
    opening, recurring = frame_markdown_utils.list_opening_and_recurring_text([str(record.get("screen") or "")
                                                                               for record in records])

    return {"opening": opening, "recurring": recurring}


def _read_document_headings(ctx: RunContext,
                            moments: list[dict] | None = None) -> list[dict]:
    """A PDF's own headings, `{"moment", "start", "level", "title"}`, in order, from its pages' text: the agenda follows
    them. A heading's moment is the moment of `moments` its line stands in, its page without them; a PDF read as its
    markdown has a moment per paragraph, and told the page number a heading stood on, its agenda started "Incidents" at
    the fourth paragraph, on page 1 (pdf-02, markdown mode). None for a recording."""

    if not phase_file_service.is_pdf_run(ctx.paths):
        return []
    transcript = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE])
    headings = []
    for segment in transcript.get("segments") or []:
        first = str(segment.get("text") or "").splitlines()[0] if segment.get("text") else ""
        marks = len(first) - len(first.lstrip("#"))
        if marks and first[marks:].startswith(" "):
            start = float(segment.get("start") or 0)
            standing = [moment["number"] for moment in moments or [] if float(moment["start"]) <= start]
            headings.append({"moment": standing[-1] if standing else int(segment.get("page") or 0), "start": start,
                             "level": marks, "title": " ".join(first[marks:].replace("*", "").split())})

    return headings


# A section's number before its title: "1 ", "2.3 ", "4) "
_LINK_ADDRESS = re.compile(r"\s*<https?://[^>]*>")
_SECTION_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+")


def _count_chapter_minutes(entries: list[dict],
                           part: dict,
                           following: float | None) -> float:
    """How long a chapter's talk runs, in minutes: from its start to the next chapter's, the last one to the last
    entry's time."""

    end = following if following is not None else max(
        (float(entry["timestamps"][-1]) for entry in entries if entry.get("timestamps")), default=0.0)

    return max(0.0, (float(end) - float(part.get("start") or 0)) / 60)


def _list_chapter_headings(headings: list[dict],
                           part: dict,
                           following: float | None) -> list[str] | None:
    """A PDF chapter's own headings, in order: those on its pages below the document's title, the chapter's own title
    left out; None for a recording and a document with no heading. Told only to keep the document's sub-headings, a
    worker made one heading of a chapter holding three (pdf-02)."""

    if not headings:
        return None
    top = min(int(heading["level"]) for heading in headings)
    # A deck's every slide title is a heading of one level: the document's title is then its first heading alone, and
    # the chapter's headings are the slide titles inside it (pdf-03 was told its chapter held none)
    is_one_level = all(int(heading["level"]) == top for heading in headings)
    # By time, not page: a PDF read as its markdown starts a chapter mid-page, at a paragraph
    first = float(part.get("start") or 0)
    titles = [heading["title"] for index, heading in enumerate(headings)
              if (index > 0 if is_one_level else int(heading["level"]) > top)
              and float(heading["start"]) >= first and (following is None or float(heading["start"]) < float(following))]

    # A numbered section ("1 Installing the CLI") is the chapter its agenda point titles without the number
    chapter_title = _SECTION_NUMBER.sub("", str(part.get("title") or "")).strip().casefold()

    # and a heading read with a link's address ("Membranes <https://…>") is its title without it
    return [title for title in titles
            if _SECTION_NUMBER.sub("", _LINK_ADDRESS.sub("", title)).strip().casefold() != chapter_title]


def _find_chapter_level(headings: list[dict]) -> int:
    """The heading level a PDF planned by its own headings has its chapters at: the top level, or the next one down
    when the top level holds only the document's title; 0 with no heading."""

    levels = sorted({int(heading["level"]) for heading in headings})
    if not levels:
        return 0
    is_title_alone = sum(1 for heading in headings if int(heading["level"]) == levels[0]) == 1

    return levels[1] if is_title_alone and len(levels) > 1 else levels[0]


def _list_chapter_level_headings(headings: list[dict]) -> list[dict]:
    """The headings a PDF planned by its own headings has its chapters at, one a moment, in order."""

    level = _find_chapter_level(headings)
    chosen: list[dict] = []
    for heading in headings:
        if int(heading["level"]) == level and (not chosen or heading["moment"] > chosen[-1]["moment"]):
            chosen.append(heading)

    return chosen


def _is_planned_by_document_headings(ctx: RunContext) -> bool:
    """Does phase 4 take this run's agenda and draft headings from the document's own headings, with no model reading
    the whole document? A PDF read as text only with two top-level headings or more: its
    first two follow-ups were handed every paragraph, 582,303 tokens of a 1,493-page wiki export against a window of
    183,040, and blocked before any call; each chapter was also read twice, for its headings and for its text."""

    if not phase_file_service.is_pdf_run(ctx.paths):
        return False
    medium = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"]).get("medium")
    if medium not in output_contract.PDF_TEXT_ONLY_MEDIUMS:
        return False

    headings = _read_document_headings(ctx)
    level = _find_chapter_level(headings)

    return sum(1 for heading in headings if int(heading["level"]) == level) >= 2


def _build_parts_from_headings(ctx: RunContext,
                               moments: list[dict]) -> tuple[list[dict], list[str], str]:
    """The agenda of a PDF planned by its own headings, built by the program and written as the agenda step writes it:
    one point per heading of the chapter level, the first point opening at the first moment so the text before the
    first heading is kept; no announced agenda; and the working title, the document's first heading."""

    headings = _read_document_headings(ctx, moments)
    chosen = _list_chapter_level_headings(headings)
    first_moment = int(moments[0]["number"])
    points = [{"title": heading["title"], "first_moment": first_moment if index == 0 else int(heading["moment"]),
               "covers_announced": []} for index, heading in enumerate(chosen)]
    document_file_service.write_parts(ctx.paths, {"announced_agenda": [], "points": points, "topic": "",
                                                  "event_name": "", "speakers": []})
    starts = {moment["number"]: moment["start"] for moment in moments}
    parts = [{"number": number, "title": point["title"], "firstMoment": point["first_moment"],
              "start": starts[point["first_moment"]], "coversAnnounced": []}
             for number, point in enumerate(points, start=1)]
    ctx.log.info(f"phase {PHASE_NUMBER}: the agenda from the document's {len(parts)} headings of level "
                 f"{_find_chapter_level(headings)}, no model call")

    return parts, [], headings[0]["title"]


def _build_draft_headings_from_document(ctx: RunContext,
                                        entries: list[dict],
                                        parts: list[dict]) -> list[dict]:
    """Each chapter's draft headings of a PDF planned by its own headings, built by the program and written as the
    headings step writes them: the chapter's headings of the level under its own, each starting at the first note at
    or after it. A heading past `document.headingEntries` notes is still written in parts by the chapter step."""

    headings = _read_document_headings(ctx)
    chapter_level = _find_chapter_level(headings)
    drafts = []
    for index, part in enumerate(parts):
        following = parts[index + 1]["start"] if index + 1 < len(parts) else None
        inside = [heading for heading in headings
                  if int(heading["level"]) > chapter_level and float(heading["start"]) >= float(part["start"])
                  and (following is None or float(heading["start"]) < float(following))]
        level = min((int(heading["level"]) for heading in inside), default=0)
        draft_headings = []
        for heading in inside:
            starting = [entry["id"] for entry in entries if float(entry["timestamps"][0]) >= float(heading["start"])]
            if int(heading["level"]) == level and starting:
                draft_headings.append({"title": heading["title"], "first_entry_id": starting[0], "what": ""})
        draft = {"purpose": "", "headings": draft_headings}
        document_file_service.write_draft_headings(ctx.paths, document_file_service.build_draft_headings_slug(part["number"]),
                                                   draft)
        drafts.append({"chapter": part["number"], **draft})
    ctx.log.info(f"phase {PHASE_NUMBER}: each chapter's headings from the document's own, "
                 f"{sum(len(draft['headings']) for draft in drafts)} in {len(drafts)} chapters, no model call")

    return drafts


def _build_parts_inputs(ctx: RunContext,
                        moments: list[dict]) -> dict:
    """What the agenda call is handed, for the call and for the dry run alike."""

    return {"slug": PARTS_SLUG, "moments": moments, "recordingName": _read_recording_name(ctx),
            "transcriptSpeakers": _read_transcript_speakers(ctx), "screenText": _read_screen_text(ctx),
            "batches": batch_summaries_service.read_batch_summaries(ctx.paths),
            "isPdf": phase_file_service.is_pdf_run(ctx.paths), "documentHeadings": _read_document_headings(ctx, moments)}


def _list_document_entries(integration: dict) -> list[dict]:
    """The entries the document is written from, in the order of their moments."""

    return sorted(document_plan_utils.select_document_entries(integration), key=lambda entry: entry["timestamps"][0])


def _write_parts(writing: PartWritingContext,
                 moments: list[dict]) -> tuple[list[dict], list[str]]:
    """The meeting's parts, `{"number", "title", "firstMoment", "start", "coversAnnounced"}`, and the agenda the
    presenter announced. Both empty when there is no moment."""

    ctx = writing.ctx

    if not moments:
        return [], []
    writing.write_part(PARTS_KIND, PARTS_SLUG, document_file_service.build_parts_path(ctx.paths),
                       _build_parts_inputs(ctx, moments))
    agenda_record = document_file_service.read_parts(ctx.paths)
    starts = {moment["number"]: moment["start"] for moment in moments}
    parts = [{"number": number, "title": str(part.get("title", "")).strip(), "firstMoment": part["first_moment"],
              "start": starts[part["first_moment"]], "coversAnnounced": list(part.get("covers_announced") or [])}
             for number, part in enumerate(agenda_record.get("points", []), start=1)]

    return parts, [str(item) for item in agenda_record.get("announced_agenda") or []]


def _build_outline_inputs(number: int,
                          chunk: list[dict],
                          contradictions: list[dict],
                          earlier_titles: list[str],
                          part_title: str) -> dict:
    first, last = chunk[0]["timestamps"][0], chunk[-1]["timestamps"][0]

    return {"slug": document_file_service.build_outline_slug(number), "entries": chunk, "partTitle": part_title,
            "contradictions": [contradiction for contradiction in contradictions
                               # one with no time of its own belongs to no chunk more than another, so every chunk sees it
                               if first <= contradiction.get("timestamp", first) <= last],
            "earlierTopics": earlier_titles}


def _split_outline_chunks(entries: list[dict],
                          parts: list[dict]) -> list[list[list[dict]]]:
    """The chunks the outlines sort, per part of the meeting: cut per part so none crosses one, then by size."""

    document = app_config.settings.document

    return [document_plan_utils.split_into_chunks(group, document.chunk_entries, document.chunk_characters)
            for group in document_plan_utils.split_by_parts(entries, [part["start"] for part in parts])]


def _write_outlines(writing: PartWritingContext,
                    entries: list[dict],
                    parts: list[dict],
                    contradictions: list[dict]) -> tuple[list[dict], list[int]]:
    """One outline per chunk, the chunks cut per part so none crosses one, and the part of each."""

    ctx = writing.ctx

    document = app_config.settings.document
    outlines: list[dict] = []
    outline_parts: list[int] = []
    for part_number, chunks in enumerate(_split_outline_chunks(entries, parts), start=1):
        part_title = parts[part_number - 1]["title"] if parts else ""
        for chunk in chunks:
            earlier, _ = document_plan_utils.merge_part_topics(outlines, outline_parts, document.fold_entries)
            inputs = _build_outline_inputs(len(outlines) + 1, chunk, contradictions,
                                           [topic["title"] for topic in earlier], part_title)
            slug = inputs["slug"]
            writing.write_part(OUTLINE_KIND, slug, document_file_service.build_outline_path(ctx.paths, slug), inputs)
            outlines.append(document_file_service.read_outline(ctx.paths, slug))
            outline_parts.append(part_number)

    return outlines, outline_parts


def _build_contents_inputs(topics: list[dict],
                           entries_by_id: dict[str, dict],
                           parts: list[dict]) -> dict:
    """Every part's title, and every topic's number, title, size and one of its entries: the title call never sees
    all the entries."""

    listed = []
    for topic in topics:
        ids = [entry_id for part in topic["parts"] for entry_id in part["entryIds"] if entry_id in entries_by_id]
        example = next((entries_by_id[entry_id] for entry_id in ids if entries_by_id[entry_id]["kind"] == "facts"),
                       entries_by_id[ids[0]] if ids else {"text": ""})
        text = " ".join(example["text"].split())
        listed.append({"number": topic["number"], "title": topic["title"], "entryCount": len(ids),
                       "example": text if len(text) <= EXAMPLE_CHARS else text[:EXAMPLE_CHARS] + " ..."})

    return {"slug": CONTENTS_SLUG, "topics": listed, "parts": _describe_agenda_points(parts)}


def _describe_agenda_points(parts: list[dict]) -> list[dict]:
    """What a call that needs the agenda is shown of each agenda point."""

    return [{"number": part["number"], "title": part["title"], "coversAnnounced": part["coversAnnounced"]}
            for part in parts]


def _render_written_parts(sections: list[dict]) -> str:
    """What a topic's accepted parts already say: each one as it was written, in its order, without its note ids."""

    texts = [section_markdown_utils.strip_entry_ids(section.get("markdown") or "").strip() for section in sections]

    return "\n\n".join(text for text in texts if text)


def _list_known_entries(entry_ids: list[str],
                        entries_by_id: dict[str, dict]) -> list[dict]:
    """The entries of `entry_ids` in their order, an id no entry holds left out."""

    return [entries_by_id[entry_id] for entry_id in entry_ids if entry_id in entries_by_id]


def _write_sections(writing: PartWritingContext,
                    title: str,
                    chapters: list[tuple[str, list[dict]]],
                    entries_by_id: dict[str, dict]) -> list[DocumentChapterSpec]:
    """One section per topic part, in document order, each told what its topic already says and checked against
    everything written before it."""

    ctx = writing.ctx

    written_slugs: list[str] = []
    document_chapters = []
    for chapter_title, chapter_topics in chapters:
        document_topics = []
        for topic in chapter_topics:
            sections: list[dict] = []
            for part_number, part in enumerate(topic["parts"], start=1):
                slug = document_file_service.build_section_slug(topic["number"], part_number)
                inputs = {"slug": slug, "documentTitle": title, "chapterTitle": chapter_title,
                          "topicTitle": topic["title"],
                          "otherTopics": [other["title"] for other in chapter_topics if other is not topic][:OTHER_TOPICS_LIMIT],
                          "earlierParts": _render_written_parts(sections), "topicEntryIds": part["entryIds"],
                          "entries": _list_known_entries(part["entryIds"], entries_by_id),
                          "earlierSlugs": list(written_slugs)}
                writing.write_part(SECTION_KIND, slug, document_file_service.build_section_path(ctx.paths, slug), inputs)
                sections.append(document_file_service.read_section(ctx.paths, slug))
                written_slugs.append(slug)
            document_topics.append(DocumentTopicSpec(title=topic["title"], sections=sections))
        document_chapters.append(DocumentChapterSpec(title=chapter_title, topics=document_topics))

    return document_chapters


def _list_own_frame(entry: dict | None) -> list[int]:
    """The frame a note belongs to: its first. A note whose text repeats on later frames (a web address left on screen)
    is one merged entry listing each of them, and their screenshots belong to their own headings, so a repeated
    web address cannot demand frames of other headings under one heading."""

    return ((entry or {}).get("frames") or [])[:1]


def _list_entry_frames(entry_ids: list[str],
                       entries_by_id: dict[str, dict]) -> list[int]:
    """The frames these entries come from, each its first frame, in order and once."""

    return list(dict.fromkeys(index for entry_id in entry_ids for index in _list_own_frame(entries_by_id.get(entry_id))))


def _list_screenshot_suggestions(entry_ids: list[str],
                                 entries_by_id: dict[str, dict],
                                 records: dict[int, dict]) -> list[str]:
    """The screenshots phase 3 suggested for the frames these entries come from: `frame-012 needed: why (which part)`."""

    lines = []
    for index in _list_entry_frames(entry_ids, entries_by_id):
        record = records.get(index) or {}
        lines += screenshot_suggestion_utils.render_suggestion_lines([record])

    return lines


def _describe_existing_cut(cut_id: str,
                           cut: dict,
                           left_out: dict[str, str]) -> str:
    """One cut already made, as a chapter's request names it: `cut-004 from frame-002`, with its warnings and the
    leave-out of its frame."""

    line = f"{cut_id} from {cut['frame']}"
    # A cut's warnings go with it: a partial table offered as "already made" was about to be placed, its warning that a
    # column was left out unseen
    if cut.get("warnings"):
        line += f" (its warnings: {'; '.join(cut['warnings'])})"
    # A frame already left out keeps its cut out: offered as "already made" with no word of the leave-out, a worker
    # placed the cut of a left-out frame four attempts running. It names the way out too: a leave-out the
    # judge refused kept its cut "not to place" against the rejection's "cut and place it"
    if cut["frame"] in left_out:
        line += (f" (not to place while {cut['frame']} stays left out, {left_out[cut['frame']]}; if a rejection says no "
                 f"placed frame shows {cut['frame']}, place this cut)")

    return line


def _list_existing_cuts(entry_ids: list[str],
                        entries_by_id: dict[str, dict],
                        cuts: dict[str, dict],
                        left_out: dict[str, str]) -> list[str]:
    """The cuts an earlier attempt or resume already made from the frames these entries come from: a chapter asked
    again places one of them rather than cutting the same frame once more on every resume."""

    frames = {frame_slug_utils.build_frame_slug(index) for index in _list_entry_frames(entry_ids, entries_by_id)}

    return [_describe_existing_cut(cut_id, cut, left_out) for cut_id, cut in sorted(cuts.items())
            if cut.get("frame") in frames and cut.get("file") and not cut.get("discarded")]


def _list_needed_screenshots(entry_ids: list[str],
                             entries_by_id: dict[str, dict],
                             records: dict[int, dict]) -> list[str]:
    frames = _list_entry_frames(entry_ids, entries_by_id)

    return screenshot_suggestion_utils.list_needed_slugs([records.get(index) or {} for index in frames])


def _list_section_slugs(topic: dict) -> list[str]:
    """The slug of each section a topic is written in, one per part, numbered from 1."""

    return [document_file_service.build_section_slug(topic["number"], number) for number in range(1, len(topic["parts"]) + 1)]


def _list_written_blocks(paths: RunPaths,
                         slugs: list[str]) -> list[str]:
    """The code, table and list blocks the sections of `slugs` hold, in order; a slug with no section is passed over."""

    return [block["text"] for slug in slugs if document_file_service.build_section_path(paths, slug).is_file()
            for block in document_file_service.read_section(paths, slug)["blocks"]
            if block["kind"] in _WRITTEN_BLOCK_KINDS]


def _count_notes(sections: list[dict]) -> int:
    """How many notes the sections hold together."""

    return sum(len(section["entryIds"]) for section in sections)


def _group_sections(sections: list[dict],
                    limit: int) -> list[list[dict]]:
    """A chapter's sections as the parts it is asked in: whole headings, in order, each part holding at most `limit`
    notes; a heading past the limit alone is a part of its own. One reply covered about 60 notes well and left 30 to
    90 of 239 uncovered on five tries."""

    headings: list[list[dict]] = []
    for section in sections:
        if headings and headings[-1][0]["topicId"] == section["topicId"]:
            headings[-1].append(section)
        else:
            headings.append([section])
    # A heading past the limit is split into its parts already: each part is then a unit of its own
    units = [unit for heading in headings for unit in
             ([[section] for section in heading] if _count_notes(heading) > limit else [heading])]
    parts: list[list[dict]] = []
    size = 0
    for unit in units:
        count = _count_notes(unit)
        if parts and size + count <= limit:
            parts[-1] += unit
            size += count
        else:
            parts.append(list(unit))
            size = count

    return parts


def _write_chapters(writing: PartWritingContext,
                    title: str,
                    chapters: list[tuple[str, list[dict]]],
                    entries_by_id: dict[str, dict]) -> list[DocumentChapterSpec]:
    """One follow-up per chapter, every heading of it in one reply, each heading's section
    checked against its own entries and against everything written before it."""

    ctx = writing.ctx
    written_slugs: list[str] = []
    document_chapters = []
    records = frame_records_service.map_records_by_index(ctx.paths)
    figure_frames = frame_records_service.list_figure_indexes(ctx.paths)
    # A name heard once and written as heard, where a screen of the recording spells it ("RepGL" for WebGL, DEFECTS 620)
    screen_spellings = garbled_name_utils.map_misheard_screen_names(
        " ".join(str(segment.get("text") or "") for segment in
                 (phase_file_service.read_phase_file(ctx.paths, 1).get("segments") or [])
                 if phase_file_service.has_phase_file(ctx.paths, 1)),
        [str(record.get("screen") or "") for record in records.values()])
    for chapter_number, (chapter_title, chapter_topics) in enumerate(chapters, start=1):
        # STEP 1: each section of the chapter with its entries, its frames and the cuts already made of them
        store = document_file_service.read_image_store(ctx.paths)
        cuts = store.get("cuts") or {}
        left_out = {str(record.get("frame") or ""): str(record.get("reason") or "").strip()
                    for record in store.get("leftOut") or [] if str(record.get("chapter") or "").startswith(
                        document_file_service.build_chapter_text_slug(chapter_number))}
        sections = [{"slug": document_file_service.build_section_slug(topic["number"], part_number), "heading": topic["title"],
                     "topicId": topic["id"], "entryIds": part["entryIds"], "subjects": topic.get("subjects") or [],
                     "requiredIds": document_plan_utils.list_required_ids(
                         _list_known_entries(part["entryIds"], entries_by_id), figure_frames),
                     "entries": _list_known_entries(part["entryIds"], entries_by_id),
                     "suggestions": _list_screenshot_suggestions(part["entryIds"], entries_by_id, records),
                     "neededFrames": _list_needed_screenshots(part["entryIds"], entries_by_id, records),
                     "existingCuts": _list_existing_cuts(part["entryIds"], entries_by_id, cuts, left_out)}
                    for topic in chapter_topics for part_number, part in enumerate(topic["parts"], start=1)]

        # STEP 2: the chapter in parts of whole headings, each part one follow-up, its twins named once earlier parts
        # are written
        chapter_needed: list[str] = []
        # The needed frames of the chapter before each section, to name its twins again once earlier parts are written
        needed_before: dict[str, list[str]] = {}
        for section in sections:
            needed_before[section["slug"]] = list(chapter_needed)
            chapter_needed += [frame_id for frame_id in section["neededFrames"] if frame_id not in chapter_needed]
        base_slug = document_file_service.build_chapter_text_slug(chapter_number)
        parts = _group_sections(sections, app_config.settings.document.chapter_entries)
        document_file_service.write_chapter_part_count(ctx.paths, chapter_number, len(parts))
        earlier = list(written_slugs)
        for part_number, part in enumerate(parts, start=1):
            # A chapter within the limit keeps its one follow-up and its slug, so an accepted attempt stays accepted
            slug = base_slug if len(parts) == 1 else document_file_service.build_chapter_text_part_slug(chapter_number, part_number)
            # A twin whose earlier frame an earlier part placed is named with its cut, read when this part is asked
            placed_frames = None
            if earlier:
                store_cuts = document_file_service.read_image_store(ctx.paths).get("cuts") or {}
                placed_frames = figure_match_service.map_placed_frames(ctx.paths, list(earlier), store_cuts)
            for section in part:
                section["placedMatches"] = (
                    figure_match_service.list_placed_matches(ctx.paths, section["neededFrames"], cuts, written_slugs)
                    + figure_match_service.list_twin_frames(ctx.paths, section["neededFrames"],
                                                            needed_before[section["slug"]], placed_frames))
            earlier_blocks = _list_written_blocks(ctx.paths, earlier)
            # A note repeating a block an earlier heading writes is no note to cite: asked to cite it without writing
            # it, a worker wrote the block again and cited another heading's note, three findings for one (DEFECTS 236)
            written_blocks = [section_markdown_utils.list_block_lines(str(text)) for text in earlier_blocks]
            for section in part:
                _, twins, _ = section_markdown_utils.split_twin_blocks(section["entries"], written_blocks)
                section["requiredIds"] = [entry_id for entry_id in section["requiredIds"] if entry_id not in twins]
            inputs = {"slug": slug, "documentTitle": title, "chapterTitle": chapter_title, "chapter": chapter_number,
                      "chapterCount": len(chapters), "sections": part, "earlierSlugs": list(earlier),
                      "earlierBlocks": earlier_blocks, "screenSpellings": screen_spellings,
                      "part": part_number, "partCount": len(parts),
                      "isPdf": phase_file_service.is_pdf_run(ctx.paths)}
            writing.write_part(CHAPTER_KIND, slug, document_file_service.build_chapter_submission_path(ctx.paths, slug), inputs)
            earlier += [section["slug"] for section in part]

        # STEP 3: the sections the follow-ups wrote, read back under their headings
        document_topics = []
        for topic in chapter_topics:
            topic_sections = []
            for section_slug in _list_section_slugs(topic):
                topic_sections.append(document_file_service.read_section(ctx.paths, section_slug))
                written_slugs.append(section_slug)
            document_topics.append(DocumentTopicSpec(title=topic["title"], sections=topic_sections))
        document_chapters.append(DocumentChapterSpec(title=chapter_title, topics=document_topics))

    return document_chapters


def _build_summary_inputs(title: str,
                          parts: list[dict],
                          chapters: list[DocumentChapterSpec],
                          outlines: list[dict],
                          entries_by_id: dict[str, dict]) -> dict:
    """What the summary call is handed: the parts, each section's first paragraph with the text of the notes it cites,
    the decisions, the action items and the open points, the ids it may cite, and its word limit. Handed the paragraph
    alone, the writer's rewording, three summaries stated what no note says ("all configuration changes executed
    successfully" for "none of them rolled back", DEFECTS 505)."""

    document = app_config.settings.document
    sections = [(chapter.title, topic.title, section) for chapter in chapters for topic in chapter.topics
                for section in topic.sections]
    claims = {field_name: list({" ".join(str(claim.get("text", "")).split()).casefold(): claim
                                for _, _, section in sections for claim in section.get(field_name) or []}.values())
              for field_name in ("decisions", "action_items")}
    cited = [entry_id for _, _, section in sections for block in section.get("blocks") or []
             for entry_id in block["entry_ids"]]
    words = sum(len(str(paragraph.get("text", "")).split()) for _, _, section in sections
                for paragraph in section.get("paragraphs", []))
    first_paragraphs = [(chapter_title, heading, (section.get("paragraphs") or [{}])[0])
                        for chapter_title, heading, section in sections]

    return {"slug": SUMMARY_SLUG, "title": title,
            "parts": _describe_agenda_points(parts),
            "sections": [{"chapter": chapter_title, "heading": heading,
                          "text": document_text_utils.remove_topic_links(str(paragraph.get("text", ""))),
                          "entryIds": paragraph.get("entry_ids", []),
                          "sources": [{"id": entry_id, "text": str(entries_by_id[entry_id].get("text") or "")}
                                      for entry_id in paragraph.get("entry_ids", []) if entry_id in entries_by_id]}
                         for chapter_title, heading, paragraph in first_paragraphs],
            "decisions": claims["decisions"], "actionItems": claims["action_items"],
            "openPoints": [line for outline in outlines for line in outline.get("not_established", [])]
                          + [line for _, _, section in sections for line in section.get("not_established", [])],
            "knownEntryIds": list(dict.fromkeys(cited)),
            "maxWords": max(document.summary_min_words, min(document.summary_max_words, words // SUMMARY_SHARE))}


def _write_summary(writing: PartWritingContext,
                   inputs: dict) -> tuple[list[dict], list[dict]]:
    """The executive summary's sentences and the agenda's lines, both empty when no section was written to
    summarise."""

    ctx = writing.ctx

    if not inputs["knownEntryIds"]:
        return [], []
    writing.write_part(SUMMARY_KIND, SUMMARY_SLUG, document_file_service.build_summary_path(ctx.paths), inputs)
    summary_record = document_file_service.read_summary(ctx.paths)

    return list(summary_record.get("summary") or []), list(summary_record.get("agenda") or [])


def _list_not_covered(announced: list[str],
                      parts: list[dict]) -> list[str]:
    """The announced agenda items no part covered, in the announced wording and order."""

    covered = {str(item).casefold().strip() for part in parts for item in part["coversAnnounced"]}

    return [item for item in announced if item.casefold().strip() not in covered]


def _build_chapters(parts: list[dict],
                    topics: list[dict]) -> list[tuple[str, list[dict]]]:
    """One chapter per part with its topics; one untitled chapter when the meeting has no part."""

    return ([(part["title"], [topic for topic in topics if topic["part"] == part["number"]]) for part in parts]
            if parts else [("", topics)])


def _build_plan_in_calls(ctx: RunContext,
                         integration: dict,
                         moments: list[dict]) -> DocumentSpec:
    """R8, R9 and R11 as calls: the parts, one outline per chunk of a part, the topics with a short return folded
    back, the title, one section per topic part, then the summary and the agenda."""

    # STEP 1: the agenda's parts, and the entries in the order of their moments
    document = app_config.settings.document
    writing = PartWritingContext(ctx=ctx, write_part=functools.partial(_write_or_stop, functools.partial(_keep_or_ask_part, ctx)))
    parts, announced = _write_parts(writing, moments)
    entries = _list_document_entries(integration)
    entries_by_id = {entry["id"]: entry for entry in entries}

    # STEP 2: one outline per chunk of a part, its topics with a short return folded back, then the title
    outlines, outline_parts = _write_outlines(writing, entries, parts, integration.get("contradictions", []))
    topics, folds = document_plan_utils.merge_part_topics(outlines, outline_parts, document.fold_entries)
    topics = document_plan_utils.split_long_parts(topics, document.section_entries)
    chapters = _build_chapters(parts, topics)
    title = UNARRANGED_TITLE
    # Through the writer that stops at a block, as every other call: a blocked title ends the phase here
    if topics:
        writing.write_part(CONTENTS_KIND, CONTENTS_SLUG, document_file_service.build_contents_path(ctx.paths),
                           _build_contents_inputs(topics, entries_by_id, parts))
        title = str(document_file_service.read_contents(ctx.paths).get("title", "")).strip()
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(topics)} topics in {len(chapters)} chapters, "
                 f"{sum(len(topic['parts']) for topic in topics)} sections to write, "
                 f"{len(folds)} short returns joined to an earlier topic")

    # STEP 3: one section per topic part, then the summary and the agenda, and the plan assembled from them
    document_chapters = _write_sections(writing, title, chapters, entries_by_id)
    summary, agenda = _write_summary(writing, _build_summary_inputs(title, parts, document_chapters, outlines,
                                                                    entries_by_id))
    plan = DocumentSpec(title=title, chapters=document_chapters,
                        set_aside_ids=[entry_id for outline in outlines for entry_id in outline.get("set_aside_entry_ids", [])],
                        not_established=[line for outline in outlines for line in outline.get("not_established", [])],
                        is_arranged=bool(parts), summary=summary, agenda=agenda,
                        not_covered=_list_not_covered(announced, parts), parts=parts, folds=folds)

    return plan


def _write_executive_summary_draft(writing: PartWritingContext,
                                   moments: list[dict]) -> str:
    """R12's first follow-up. Returns the working title the sections are written under until the final one."""

    ctx = writing.ctx
    inputs = {"slug": EXECUTIVE_SUMMARY_DRAFT_SLUG, "moments": moments, "maxWords": app_config.settings.document.summary_max_words,
              "batches": batch_summaries_service.read_batch_summaries(ctx.paths),
              "isPdf": phase_file_service.is_pdf_run(ctx.paths)}
    writing.write_part(EXECUTIVE_SUMMARY_DRAFT_KIND, EXECUTIVE_SUMMARY_DRAFT_SLUG,
                       document_file_service.build_executive_summary_draft_path(ctx.paths), inputs)
    working_title = str(document_file_service.read_executive_summary_draft(ctx.paths).get("working_title", "")).strip()

    return working_title or UNARRANGED_TITLE


def _map_link_headings(chapters: list[tuple[str, list[dict]]],
                       plan: DocumentSpec) -> dict[str, str]:
    """Each topic id and the heading the document prints its topic under."""

    headings = {}
    for (_, topics), printed in zip(chapters, document_assembly_service.list_printed_topic_headings(plan)):
        for topic, heading in zip(topics, printed):
            if topic.get("id"):
                headings[topic["id"]] = heading

    return headings


def _write_draft_headings(writing: PartWritingContext,
                          state: WriteConversationState,
                          entries: list[dict],
                          parts: list[dict]) -> list[dict]:
    """One follow-up per chapter (one agenda point) before the table of contents: the agent reads
    that chapter in the notes file, whole, in one read, and drafts its headings. Returns the accepted drafts,
    `{"chapter", **draft}`."""

    if not entries:
        return []
    drafts = []
    document_headings = _read_document_headings(writing.ctx)
    for index, part in enumerate(parts):
        slug = document_file_service.build_draft_headings_slug(part["number"])
        following = parts[index + 1]["start"] if index + 1 < len(parts) else None
        inputs = {"slug": slug, "chapter": part["number"], "chapterCount": len(parts), "title": part["title"],
                  "chunkCount": write_reading_service.count_chapter_chunks(state.reading, part["number"]),
                  "entries": entries,
                  "documentHeadings": _list_chapter_headings(document_headings, part, following),
                  "chapterMinutes": _count_chapter_minutes(entries, part, following),
                  "startIds": write_reading_service.list_chapter_ids(state.reading, part["number"])}
        writing.write_part(DRAFT_HEADINGS_KIND, slug,
                           document_file_service.build_draft_headings_path(writing.ctx.paths, slug), inputs)
        drafts.append({"chapter": part["number"], **document_plan_utils.move_late_heading_starts(
            document_plan_utils.keep_document_headings(document_file_service.read_draft_headings(writing.ctx.paths, slug),
                                                       inputs["documentHeadings"] or []), entries)})

    return drafts


def _write_table_of_contents(ctx: RunContext,
                             entries: list[dict],
                             parts: list[dict],
                             drafts: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """R12's whole table of contents, built by the program from the draft headings, no model asked:
    it orders them and fills in every entry. Returns the table, the agenda's points with the chapters' titles and
    starts, and one outline per chapter. Nothing is built when there is no entry to sort."""

    if not entries:
        return {}, parts, []
    groups = document_plan_utils.split_by_parts(entries, [part["start"] for part in parts])
    # An agenda point with no entry of its own (two points starting at one moment) has no chapter: each chapter is
    # paired with the point it was built for, never by position, and the chapters are numbered in order
    kept_parts = [part for part, group in zip(parts, groups) if group]
    for part, group in zip(parts, groups):
        if not group:
            ctx.log.warning(f"  agenda point {part['number']} ({part['title']}) has no note of its own: it gets no chapter")
    # The program builds it from the draft headings; the file keeps what was built, as the model's reply once did
    document_file_service.write_outline(ctx.paths, document_file_service.TABLE_OF_CONTENTS_SLUG,
                                        document_plan_utils.build_table_of_contents(
                                            parts, groups, drafts, phase_file_service.is_pdf_run(ctx.paths)))
    table = document_plan_utils.flatten_table_of_contents(
        document_file_service.read_outline(ctx.paths, document_file_service.TABLE_OF_CONTENTS_SLUG), entries)
    time_of = {entry["id"]: entry["timestamps"][0] for entry in entries}
    chapters = table.get("chapters") or []
    chapter_parts = [{**part, "number": number, "title": str(chapter.get("title") or "").strip() or part["title"],
                      "start": time_of.get(chapter.get("first_entry_id"), part["start"])}
                     for number, (part, chapter) in enumerate(zip(kept_parts, chapters), start=1)]

    return table, chapter_parts, document_plan_utils.group_topics_by_chapter(table)


def _has_earlier_life(ctx: RunContext) -> bool:
    """Did an earlier start of this phase ask the agenda? The calls path records its attempts in the run store, the conversation
    records each follow-up in `follow-ups.json`. Only a phase with no earlier life clears what it finds: asking only the store
    deleted the agenda, the table outline and the sections on every resume of the conversation path."""

    # A PDF planned by its own headings writes its agenda with no follow-up: its file is the earlier life
    return (ctx.store.count_attempts(PHASE, PARTS_SLUG) > 0
            or PARTS_SLUG in document_file_service.read_follow_up_records(ctx.paths)
            or (_is_planned_by_document_headings(ctx) and document_file_service.build_parts_path(ctx.paths).is_file()))


def _write_or_stop(write_part: Callable[[str, str, Path, dict], bool],
                   kind: str,
                   slug: str,
                   path: Path,
                   inputs: dict) -> None:
    """`write_part`, raising `PhaseBlocked` when the part is blocked, so nothing after it is asked."""

    if not write_part(kind, slug, path, inputs):
        raise PhaseBlocked(f"{slug} was blocked")


def _build_position_labels(kind: str,
                           slugs: list[str]) -> dict[str, str]:
    """Each follow-up of a kind asked many times, and how the console names it when it starts: `writing chapter 4 of
    9`."""

    return {slug: f"{kind} {number} of {len(slugs)}" for number, slug in enumerate(slugs, start=1)}


def _build_plan_in_conversation(ctx: RunContext,
                                integration: dict,
                                moments: list[dict]) -> tuple[DocumentSpec, dict[str, str]]:
    """R12, one session, one step, one worker: one conversation of the executive summary draft, the agenda, the draft
    headings of each chapter, the table of contents the program builds from them, one follow-up per chapter's text, and
    the final title, summary and agenda. Returns the plan and the heading of each topic id a section may link to."""

    # STEP 1: the entries and the transcript the conversation reads, and the conversation opened on them
    document = app_config.settings.document
    transcript = integration_service.list_transcript_lines(integration)
    entries = _list_document_entries(integration)
    entries_by_id = {entry["id"]: entry for entry in entries}
    state = document_conversation_service.open_conversation(
        ctx, entries, transcript, meeting_notes_utils.list_note_sections(integration), _read_max_attempts(ctx))
    writing = PartWritingContext(ctx=ctx, write_part=functools.partial(_write_or_stop, functools.partial(
        document_conversation_service.write_part_in_conversation, state)))

    # STEP 2: the executive summary draft and the agenda, then a fresh start for the headings; a PDF planned by its own
    # headings gets its agenda from them, with no draft and no model call
    # Each follow-up names its place when it starts
    state.positions.update(ONE_OFF_LABELS)
    is_planned_by_headings = _is_planned_by_document_headings(ctx)
    if is_planned_by_headings:
        parts, announced, title = _build_parts_from_headings(ctx, moments)
    else:
        title = _write_executive_summary_draft(writing, moments)
        parts, announced = _write_parts(writing, moments)
    document_conversation_service.record_agenda(state, parts)
    write_conversation_service.start_afresh(state)

    # STEP 3: each chapter's draft headings, and the table of contents the program builds from them; a PDF planned by
    # its own headings takes them from the document, so each chapter is read once, by its text's follow-up
    state.positions.update(_build_position_labels("building the headings of chapter",
                                                  [document_file_service.build_draft_headings_slug(part["number"]) for part in parts]))
    if is_planned_by_headings:
        drafts = _build_draft_headings_from_document(ctx, entries, parts)
    else:
        drafts = _write_draft_headings(writing, state, entries, parts)
    table, chapter_parts, step_outlines = _write_table_of_contents(ctx, entries, parts, drafts)
    # The chapters may start where the agenda's points did not: later reads by point follow the chapters
    document_conversation_service.record_agenda(state, chapter_parts)
    # A heading's id is its chapter and its place there, `T03.2`: the chapter outlines are numbered as the chapters are
    topics, folds = document_plan_utils.merge_part_topics(step_outlines, list(range(1, len(step_outlines) + 1)),
                                                           document.fold_entries)
    # A heading past `document.headingEntries` notes is written in parts under its one heading, each asked alone
    topics = document_plan_utils.split_long_parts(topics, document.heading_entries)
    chapters = _build_chapters(chapter_parts, topics)
    state.link_targets = {topic["id"]: topic["title"] for topic in topics}
    document_conversation_service.show_scaffold(state, [
        (chapter_title, [(topic["title"], _list_section_slugs(topic)) for topic in chapter_topics])
        for chapter_title, chapter_topics in chapters])

    # STEP 4: one follow-up per chapter, every heading of it
    state.positions.update(_build_position_labels("writing chapter", [document_file_service.build_chapter_text_slug(number)
                                                                     for number in range(1, len(chapters) + 1)]))
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(topics)} headings in {len(chapters)} chapters, "
                 f"{len(folds)} short returns joined to an earlier heading")
    document_chapters = _write_chapters(writing, title, chapters, entries_by_id)

    # STEP 5: the final title, summary and agenda, and the plan the program assembles the document from
    summary, agenda = _write_summary(writing, _build_summary_inputs(title, chapter_parts, document_chapters, [table],
                                                                    entries_by_id))
    # The final follow-up writes the title beside the summary, so the contents file holds a final title only after it
    if summary and document_file_service.build_contents_path(ctx.paths).is_file():
        title = str(document_file_service.read_contents(ctx.paths).get("title", "")).strip() or title
    plan = DocumentSpec(title=title, chapters=document_chapters,
                        set_aside_ids=list(table.get("set_aside_entry_ids", [])),
                        not_established=list(table.get("not_established", [])),
                        is_arranged=bool(chapter_parts), summary=summary, agenda=agenda,
                        not_covered=_list_not_covered(announced, chapter_parts), parts=chapter_parts, folds=folds)

    return plan, _map_link_headings(chapters, plan)


def _end_phase_failed(ctx: RunContext,
                      is_dry_run: bool) -> dict:
    """The phase failed. A dry run records nothing, its failure included: a resume would read it as a real attempt's."""

    if is_dry_run:
        return {"phases": {PHASE: "failed"}}

    return _mark_phase(ctx, "failed", performance_log_service.start_unit(ctx, PHASE, None))


def _write_takeaway(ctx: RunContext,
                    is_dry_run: bool) -> dict:
    """Phase 4 of a takeaway run: ONE call over every transcript line, its takeaway
    checked, then the document and the published folder written from it. No merge, no plan, no conversation."""

    # STEP 1: every transcript line with its id, time and speaker, numbered as the merge numbers them
    try:
        transcript = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"])
    except MissingPhaseInput as exc:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {exc}")

        return _end_phase_failed(ctx, is_dry_run)
    lines = speech_markdown_utils.list_numbered_lines(transcript)
    if not lines:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: the transcript holds no speech to take the facts from")

        return _end_phase_failed(ctx, is_dry_run)
    inputs = {"lines": lines, "tags": run_tags_service.read_tags(ctx.paths.output_root),
              "recording": _read_recording_name(ctx)}

    # STEP 2: on a dry run, render what the call WOULD be sent, and call no model
    if is_dry_run:
        messages = chain_dispatch_service.build_messages(
            attempt_request_builder.build_standalone_request(ctx, TAKEAWAY_KIND, inputs=inputs))
        system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
        dry_run_service.describe_dry_run(ctx, PHASE, output_contract.TAKEAWAY_SLUG, system_text, human_text)

        return {"phases": {PHASE: "complete"}}

    # STEP 3: the one call, kept from disk when a resume finds it still passing; a takeaway not accepted blocks the phase
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    ctx.log.info("")
    ctx.log.info(f"phase {PHASE_NUMBER}: a takeaway run, one call over {len(lines)} transcript lines")
    takeaway_path = takeaway_document_service.build_takeaway_path(ctx.paths)
    if not _keep_or_ask_part(ctx, TAKEAWAY_KIND, output_contract.TAKEAWAY_SLUG, takeaway_path, inputs):
        ctx.log.error(f"phase {PHASE_NUMBER} stopped: the takeaway was not accepted. Fix the cause, then `--resume`.")

        return _mark_phase(ctx, "blocked", handles)

    # STEP 4: the reader's document without the ids, and the published folder with what each line rests on
    document = takeaway_document_service.publish_takeaway(ctx.paths, lines)
    ctx.log.info(f"phase {PHASE_NUMBER}: published as {document.parent.name}/{document.name}")

    return _mark_phase(ctx, "complete", handles)


def _is_markdown_conversion(ctx: RunContext) -> bool:
    """Is the run a PDF's markdown conversion, by the medium phase 2 wrote, under its
    name now or before?"""

    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    if not phase_file_service.has_phase_file(ctx.paths, frames_number):
        return False
    medium = phase_file_service.read_phase_file(ctx.paths, frames_number).get("medium")

    return output_contract.RETIRED_MEDIUMS.get(medium, medium) == output_contract.PDF_MARKDOWN_CONVERSION_MEDIUM


def _write_converted_document(ctx: RunContext,
                              is_dry_run: bool) -> dict:
    """Phase 4 of a PDF's markdown conversion: the document written from phase 2's paragraphs, each figure where it
    stood, and published, with no model call."""

    if is_dry_run:
        ctx.log.info(f"phase {PHASE_NUMBER}: [dry-run] a markdown conversion; nothing to rehearse")

        return {"phases": {PHASE: "complete"}}
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    ctx.log.info("")
    try:
        document = pdf_conversion_service.publish_conversion(ctx.paths)
    except MissingPhaseInput as exc:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {exc}")

        return _mark_phase(ctx, "failed", handles)
    ctx.log.info(f"phase {PHASE_NUMBER}: the document converted to markdown with no model call, published as "
                 f"{document.parent.name}/{document.name}")

    return _mark_phase(ctx, "complete", handles)


def _write_fast_document(ctx: RunContext,
                         is_dry_run: bool) -> dict:
    """Phase 4 of a fast-processing run: ONE call of the document model over the
    frame descriptions phase 3 wrote, no check, then the document and the published folder."""

    if not fast_frame_note_service.build_descriptions_path(ctx.paths).is_file():
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {output_contract.FRAME_DESCRIPTIONS_FILE} is missing; phase 3 "
                      f"writes it")

        return _end_phase_failed(ctx, is_dry_run)
    if is_dry_run:
        system_text, human_text = chain_response_utils.render_prompt_as_text(
            fast_document_service.build_messages(ctx.paths, _read_recording_name(ctx)))
        dry_run_service.describe_dry_run(ctx, PHASE, output_contract.FAST_DOCUMENT_SLUG, system_text, human_text)

        return {"phases": {PHASE: "complete"}}
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    ctx.log.info("")
    # the call is a unit of its own, as a phase 3 session is: the phase's line sums its units' calls
    unit = performance_log_service.start_unit(ctx, PHASE, output_contract.FAST_DOCUMENT_SLUG,
                                              label="writing the document in one call")
    try:
        document = fast_document_service.write_document(ctx, _read_recording_name(ctx))
    except DescriptionsDoNotFit as refusal:
        ctx.log.error(f"phase {PHASE_NUMBER} stopped: {refusal}")
        performance_log_service.record_unit(ctx, unit, performance_log_service.PART_LEVEL, "blocked", [])

        return _mark_phase(ctx, "blocked", handles)
    performance_log_service.record_unit(ctx, unit, performance_log_service.PART_LEVEL, "written", [])
    ctx.log.info(f"phase {PHASE_NUMBER}: published as {document.parent.name}/{document.name}")

    return _mark_phase(ctx, "complete", handles)


def describe_writing_input(entries: list[dict],
                           integration: dict,
                           notes_tokens: int) -> str:
    """Phase 4's first console line: what the document is written from. An audio recording has no frame, so its
    moments are not counted as frames."""

    speech_lines = sum(1 for entry in integration.get("entries", []) if entry["kind"] == "speech")
    speech_entries = sum(1 for entry in entries if entry["kind"] == "speech")
    if not integration.get("frames"):
        # An audio recording has no frame notes: its entries are its paragraphs, and the sections phase 3 prepared
        prepared = len(entries) - speech_entries
        sections = f", {prepared} prepared sections" if prepared else ""

        return (f"phase {PHASE_NUMBER}: {output_contract.SUMMARY_FILE} from {speech_entries} speech paragraphs "
                f"({speech_lines} transcript lines){sections}, {notes_tokens:,} tokens")

    return (f"phase {PHASE_NUMBER}: {output_contract.SUMMARY_FILE} from {len(entries)} entries "
            f"({len(entries) - speech_entries} notes + {speech_entries} speech entries of {speech_lines} lines), "
            f"{len(integration['frames'])} frames, {notes_tokens:,} tokens")


def write_document_node(state: RunState,
                        config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]

    # STEP 1: a resume skips a phase that already completed. The store on disk decides, not state.
    if not state.get("is_dry_run") and ctx.store.get_phase(PHASE) == "complete":
        ctx.log.info(f"phase {PHASE_NUMBER}: already complete; not rewriting it")

        return {"phases": {PHASE: "complete"}}

    # STEP 1b: a takeaway run writes its document in one call over the transcript, and nothing below runs
    if ingesting_profile_service.is_one_call_profile(ctx.paths.output_root):
        return _write_takeaway(ctx, bool(state.get("is_dry_run")))

    # STEP 1b2: a PDF's markdown conversion is its own text with its figures, written by the program alone
    if _is_markdown_conversion(ctx):
        return _write_converted_document(ctx, bool(state.get("is_dry_run")))

    # STEP 1c: a fast-processing video run writes its document in one call over its frame descriptions
    if video_workflow_service.is_fast_processing(ctx.paths.output_root) and not phase_file_service.is_audio_run(ctx.paths):
        return _write_fast_document(ctx, bool(state.get("is_dry_run")))

    # STEP 2: merge layer 2 from the files phases 1 to 3 wrote, and refuse by name when one is not there
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    try:
        integration = integration_service.build_integration(ctx.paths, _list_blocked_reasons(ctx))
    except MissingPhaseInput as exc:
        if state.get("is_dry_run"):
            ctx.log.info(f"phase {PHASE_NUMBER}: [dry-run] {exc}; nothing to rehearse")

            return {"phases": {PHASE: "complete"}}
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {exc}")

        return _mark_phase(ctx, "failed", handles)

    # STEP 3: the program decides what the document is written from, and the moments the parts call sees; a dry run
    # renders what the parts call WOULD be sent, and calls no model
    entries = _list_document_entries(integration)
    document = app_config.settings.document
    moments = document_plan_utils.list_moments(integration, document.moment_speech_chars)
    if state.get("is_dry_run"):
        messages = chain_dispatch_service.build_messages(attempt_request_builder.build_standalone_request(
            ctx, PARTS_KIND, inputs=_build_parts_inputs(ctx, moments)))
        system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
        dry_run_service.describe_dry_run(ctx, PHASE, PARTS_SLUG, system_text, human_text)

        return {"phases": {PHASE: "complete"}}
    ctx.log.info("")
    # The notes are rendered from the merge, as the conversation reads them: phase 3's copy on disk is not needed here,
    # and a folder whose phase 3 ran before the file was named 03_notes.md has none
    notes_text = meeting_notes_utils.render_meeting_notes(_read_recording_name(ctx), integration)
    notes_tokens = starting_messages_utils.estimate_text_tokens(notes_text, document.chars_per_token)
    ctx.log.info(describe_writing_input(entries, integration, notes_tokens))

    # STEP 4: a new run clears the parts an earlier run left; a resumed one keeps what still validates
    if not _has_earlier_life(ctx):
        document_file_service.clear_document_parts(ctx.paths)

    # STEP 5: the document's pieces, asked in calls of their own or as follow-ups of one conversation (R12,
    # `document.writeMode`); either way the plan the program assembles
    link_headings: dict[str, str] = {}
    try:
        if document.write_mode == CONVERSATION_MODE:
            plan, link_headings = _build_plan_in_conversation(ctx, integration, moments)
        else:
            plan = _build_plan_in_calls(ctx, integration, moments)
    except PhaseBlocked as exc:
        ctx.log.error(f"phase {PHASE_NUMBER} stopped: {exc}, and nothing after it was asked. Fix the cause, then `--resume`.")

        return _mark_phase(ctx, "blocked", handles)

    # STEP 6: the program assembles the document and its provenance, turns a conversation's `[T02.1]` into links to
    # the topics' headings, then judges the document from disk
    written = document_assembly_service.write_document(ctx.paths, plan, integration)
    if link_headings:
        written.write_text(document_text_utils.render_heading_links(written.read_text(encoding="utf-8"), link_headings),
                           encoding="utf-8")
    report = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, DOCUMENT_KIND))
    if not report.ok:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {written.name} was rejected: {report.render_summary()} ({report.render_hint()})")

        return _mark_phase(ctx, "failed", handles)
    if report.notes:
        warnings = finding_text_utils.render_warnings_for_console(report.notes)
        ctx.log.info(f"phase {PHASE_NUMBER}: {written.name} accepted with a warning: {warnings}")
    counts = document_assembly_service.build_coverage(plan, integration)["counts"]
    ctx.log.info(f"phase {PHASE_NUMBER}: {written.name} written; entries: "
                 + ", ".join(f"{count} {status}" for status, count in sorted(counts.items())))

    # STEP 7: the program publishes the folder a reader opens, from the document and the images its chapters placed (R13)
    publication = publication_service.publish_document(ctx)
    if not publication.is_ok:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {publication.document} was rejected: {publication.report}")

        return _mark_phase(ctx, "failed", handles)
    ctx.log.info(f"phase {PHASE_NUMBER}: published as {publication.document.parent.name}/{publication.document.name} with "
                 f"{publication.image_count} image(s)")

    if not plan.parts:
        ctx.log.error(f"phase {PHASE_NUMBER} stopped: the agenda has no point, so the document has no chapter. Fix the "
                      f"cause, then `--resume`.")

        return _mark_phase(ctx, "blocked", handles)

    return _mark_phase(ctx, "complete", handles)
