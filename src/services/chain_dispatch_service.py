"""Hand ONE unit of work to ONE model call, and report what the call did.

The chain loop's counterpart of the agent dispatch. A job kind names three things, each in its own
table: the builder that turns the request into messages, the schema the answer must parse into (or, for phase 3's
kinds, the reader of the markdown the model answers in), and the writer that tidies the parsed answer (secrets hidden,
diagram nodes named, ranges written out) and puts it on disk. A job stays data, a kind and its inputs, and the
behaviour is looked up here, so what was asked can be logged, compared across attempts and diffed.

Nothing here decides whether the work was done. A validator answers that, from the call log and the
record on disk, after this returns.

An unknown kind RAISES in every lookup. A job wired to a row that does not exist would otherwise
send the wrong prompt or write nothing, and both read as a bad model rather than as bad wiring.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

import config as app_config
from agent_client import finish_reason_utils
from chains import chain_runner
from models import call_outcome_vocabulary
from models.attempt_request import AttemptRequest
from models.chain_call_result import ChainCallResult
from models.chain_call_spec import NO_THINKING, ChainCallSpec
from models.chain_reply_result import ChainReplyResult, MarkdownReader
from models.run_paths import RunPaths
from prompts import critique_collection_messages
from prompts import document_messages
from prompts import preprocessing_messages
from prompts import speech_structuring_messages
from prompts import takeaway_messages
from prompts import frames_processing_messages
from schemas import frame_record
from schemas import document_contents
from schemas import document_outline
from schemas import document_parts
from schemas import document_section
from schemas import document_summary
from services import batch_summaries_service
from services import chat_model_service
from services import critique_collection_service
from services import document_file_service
from services import frame_records_service
from services import host_return_service
from services import phase_file_service
from services import prepared_file_service
from services import speech_file_service
from services import takeaway_document_service
from services import screen_typo_check_service
from services import tag_support_service
from services import web_address_check_service
from services import record_file_service
from services import review_file_service
from utils import chain_response_utils
from utils import critique_markdown_utils
from utils import prepared_markdown_utils
from utils import speech_markdown_utils
from utils import takeaway_markdown_utils
from utils import frame_markdown_utils
from utils import document_plan_utils
from utils import document_text_utils
from utils import mermaid_text_utils
from utils import message_content_utils
from utils import section_markdown_utils
from validators import item_validator
from validators import review_validator

__all__ = ["PHASE3_FOLLOW_UPS", "WINDOW_SIZED_KINDS", "select_reasoning_effort", "estimate_prompt_tokens_by_characters",
           "compute_window_room", "MESSAGE_BUILDERS", "SCHEMAS", "MARKDOWN_READERS", "RECORD_WRITERS",
           "build_messages", "invoke_chain", "write_record"]


def _read_slug(request: AttemptRequest) -> str:
    """The slug a request's answer is written under; empty when the request names none."""

    return str(request.inputs.get("slug") or "")


def _name_diagram_nodes(record: dict[str, Any]) -> dict[str, Any]:
    """The record with its diagram's common mistakes mended, so the next frame extends a diagram Mermaid draws."""

    return {**record, "diagram": mermaid_text_utils.repair_diagram(str(record.get("diagram") or ""))}


def _clean_frame_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = _name_diagram_nodes(document_text_utils.hide_secrets_in_record(record))
    # An outline indented four spaces a level is the same outline: folded to two where it enters, so it is never rejected
    # for its width (three frames of one run were, twice each).
    if str(cleaned.get("screen") or "").strip():
        cleaned = {**cleaned, "screen": frame_markdown_utils.normalise_screen_indentation(cleaned["screen"])}

    return cleaned


def _finish_frame_record(request: AttemptRequest,
                         slug: str,
                         parsed: dict[str, Any]) -> dict[str, Any]:
    """A frame's record as the program stores it: cleaned, then the pointer to another frame's screen filled (the frame's own reader lines instead when that frame's
    outline does not fit it), then a fact only telling a question asked, then a take-away quoting another frame's
    words, then a take-away's quote no one said unquoted and its side of the screen taken from the outline, then a fact only remarking on the notes dropped, then each web address neither the frame nor the speech shows dropped, and each open question claiming a misspelling the frame does not show, then each name the speech misheard
    written as the screen spells it, then the lines the outline left out
    added from the text reader, and last a fact an accepted frame holds word for word dropped while one of its own
    remains. The pointer is filled first, so the lines added last are the ones the frame truly lacks."""

    written = item_validator.drop_false_step_claims(request.paths, slug,
                                                    item_validator.replace_corrected_claims(_clean_frame_record(parsed)))
    pointed = item_validator.replace_unfitting_pointed_outline(
        request.paths, slug, written, record_file_service.fill_screen_pointer(request.paths, slug, written))
    figured = item_validator.drop_figure_page_facts(request.paths, slug, item_validator.set_text_screenshots_to_helps(
        item_validator.drop_figure_diagram(request.paths, slug, pointed)))
    figured = item_validator.drop_figure_markdown(request.paths, slug, figured)
    figured = item_validator.drop_figure_page_outline_lines(request.paths, slug, figured)
    figured = item_validator.drop_figure_unsourced_standing_words(request.paths, slug, figured)
    filled = item_validator.drop_figure_look_facts(request.paths, slug, figured)
    dropped = item_validator.drop_misplaced_take_aways(request.paths, slug, item_validator.drop_structure_facts(
        item_validator.drop_table_row_facts(item_validator.drop_question_facts(item_validator.use_outline_spelling_in_facts(
            filled)))))
    unquoted = item_validator.drop_rhetorical_questions(item_validator.drop_page_number_facts(item_validator.unwrap_speaker_facts(item_validator.drop_remarks_about_notes(request.paths, slug, item_validator.use_outline_side_in_take_aways(
        item_validator.drop_quote_marks_of_unsaid_words(request.paths, slug, dropped))))))
    own = item_validator.add_missing_page_sentences(request.paths, slug,
                                                    item_validator.add_step_line(request.paths, slug, unquoted))

    seen = tag_support_service.drop_unsupported_new_tags(request.paths, slug, screen_typo_check_service.drop_unread_typo_claims(
        request.paths, slug, web_address_check_service.drop_unseen_web_addresses(request.paths, slug, own)))

    # Copies are judged last, on the outline as it is stored: judged first, a copy the outline's quotes excused was
    # refused once a later step took its line out (froscon 3147 frame 151)
    return item_validator.drop_copied_facts(request.paths, slug, item_validator.add_unquoted_screen_lines(
        request.paths, slug, item_validator.drop_build_repeated_lines(
            request.paths, slug, item_validator.drop_neighbour_screen_lines(
                request.paths, slug, item_validator.use_screen_spelling_of_heard_names(request.paths, slug, seen)))))


def _write_frame_record(request: AttemptRequest,
                        parsed: dict[str, Any]) -> Path:
    slug = _read_slug(request)

    return record_file_service.write_frame_record(request.paths, slug, _finish_frame_record(request, slug, parsed))


def _write_group_records(request: AttemptRequest,
                         parsed: dict[str, Any]) -> Path:
    """Each frame of the group under its own number, and the batch's account in its own record. A frame the answer left
    out, or wrote in a shape that could not be read, has its record removed, so the validator finds it missing rather than judging a record an earlier attempt
    left; why it could not be read stays in the call log's answer, under `unreadable`."""

    answered = {record.get("frame"): record for record in parsed.get("frames") or [] if isinstance(record, dict)}
    items = request.inputs["items"]
    for item in items:
        record = answered.get(item["index"])
        if record is None:
            record_file_service.build_record_path(request.paths, item["slug"]).unlink(missing_ok=True)
        else:
            record_file_service.write_frame_record(request.paths, item["slug"],
                                                   _finish_frame_record(request, item["slug"], record))
    _write_batch_summary(request, parsed)

    return record_file_service.build_record_path(request.paths, items[0]["slug"])


def _write_batch_summary(request: AttemptRequest,
                         parsed: dict[str, Any]) -> Path:
    """R15 and R18: the batch's account and how it felt, in their own record beside the frames', for the program to
    judge. Both fields travel together, because one batch has one mood."""

    return record_file_service.write_batch_record(
        request.paths, batch_summaries_service.build_summary_slug(_read_slug(request)),
        {field: str(parsed.get(field) or "") for field in frame_record.BATCH_SECTIONS})


def _write_batch_follow_up(request: AttemptRequest,
                           parsed: dict[str, Any]) -> Path:
    """The batch's open questions, or the answers found for them, in a record of their own under the call's slug: the
    node reads it and puts what it keeps into the batch's file."""

    return record_file_service.write_batch_record(
        request.paths, _read_slug(request),
        {field: [str(entry) for entry in parsed.get(field) or []] for field in frame_record.BATCH_FOLLOW_UP_SECTIONS})


def _write_critique_collection(request: AttemptRequest,
                               parsed: dict[str, Any]) -> Path:
    """Phase 6's rule set: the counts the PROGRAM worked out, with the wording that came back for each group. Groups a
    rule names together are merged into one, a group the model dropped is left out, and a rule naming no group of the
    request is dropped with it."""

    groups, worded = critique_collection_service.merge_worded_groups(request.inputs.get("groups") or [],
                                                                     list(parsed.get("rules") or []))

    return critique_collection_service.write_rule_set(request.paths, groups, worded)


def _write_speech(request: AttemptRequest,
                  parsed: dict[str, Any]) -> Path:
    """Phase 2's paragraphs of an audio recording, written as the file's own markdown: each paragraph ending where the
    next begins, and each inside one speaker turn named by that turn's speaker."""

    return speech_file_service.write_speech(
        request.paths, speech_markdown_utils.build_settled_paragraphs(parsed, request.inputs.get("lines") or []))


def _write_takeaway(request: AttemptRequest,
                    parsed: dict[str, Any]) -> Path:
    """Phase 4's takeaway of a takeaway run, written with its ids as the program's copy the check reads."""

    return takeaway_document_service.write_takeaway(request.paths, parsed)


def _write_prepared(request: AttemptRequest,
                    parsed: dict[str, Any]) -> Path:
    """One preparation call's items of an audio recording, written as the file's own markdown under `03_prepared/`."""

    step = request.inputs["step"]

    return prepared_file_service.write_prepared(request.paths, step["id"], step["title"], request.inputs.get("item_number"),
                                                parsed)


def _write_critique(request: AttemptRequest,
                    parsed: dict[str, Any]) -> Path:
    """The batch's critique, whole, in its own record beside the frames': the anti-pattern rules it reported. An
    ARTIFACT - no frame's notes are touched by it - which the repair reads, the monitor validates and phase 6
    collects. A quote of the screen's own words and a frame number in a rule's own lines are hidden before it
    is written (`critique_markdown_utils.hide_screen_quotes`, `hide_screen_keys`, `hide_frame_numbers`)."""

    screens = [str((entry or {}).get("screen") or "") for entry in (request.inputs.get("batch") or {}).get("accepted") or []]

    return record_file_service.write_critique_record(
        request.paths, batch_summaries_service.build_critique_slug(_read_slug(request)),
        {"is_nothing_to_criticise": bool(parsed.get("is_nothing_to_criticise")),
         "rules": [critique_markdown_utils.hide_frame_numbers(critique_markdown_utils.hide_screen_keys(
                       critique_markdown_utils.hide_screen_quotes(rule, screens), screens))
                   for rule in parsed.get("rules") or []]})


def _write_second_pass(request: AttemptRequest,
                       parsed: dict[str, Any]) -> Path:
    """R19: the batch's second look, whole, in its own record beside the frames' for the program to judge and apply.
    Written as it came back: the node decides which addition survives its guards."""

    return record_file_service.write_second_pass_record(
        request.paths, batch_summaries_service.build_second_pass_slug(_read_slug(request)),
        {"is_nothing_to_change": bool(parsed.get("is_nothing_to_change")),
         "additions": [document_text_utils.hide_secrets_in_record(addition) for addition in parsed.get("additions") or []],
         **{field: str(parsed.get(field) or "") for field in frame_record.BATCH_SECTIONS}})


def _render_parse_errors(errors: list[dict[str, Any]]) -> str:
    return "; ".join(error["text"] for error in errors)


def _read_batch_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    batch = parsed.get("batch") or {}

    fields = {field: str(batch.get(field) or "") for field in frame_record.BATCH_SECTIONS}
    # The follow-ups' lists travel only when the answer had those sections, so a batch's own answer reads as it did
    for field in frame_record.BATCH_FOLLOW_UP_SECTIONS:
        if field in batch:
            fields[field] = [str(entry) for entry in batch[field]]

    return fields


def _read_frame_answer(text: str) -> tuple[dict[str, Any] | None, str]:
    """One frame's `# Frame N` document, or why the answer is not exactly that."""

    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    if parsed["errors"]:
        return None, _render_parse_errors(parsed["errors"])
    if len(parsed["frames"]) != 1:
        return None, f'the answer holds {len(parsed["frames"])} "# Frame N" documents; write one, for this frame'

    return parsed["frames"][0], ""


def _read_group_answer(text: str) -> tuple[dict[str, Any] | None, str]:
    """Every frame that read cleanly, the batch's account, and why each other frame could not be read. The answer
    as a whole fails only for a fault of its own: no document at all, a document that is none of an answer's, or a
    `# Batch` that could not be read, since its account is the whole batch's."""

    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    answer_errors = [error for error in parsed["errors"] if error["frame"] is None]
    if answer_errors:
        return None, _render_parse_errors(answer_errors)
    reasons_by_frame: dict[str, list[str]] = {}
    for error in parsed["errors"]:
        reasons_by_frame.setdefault(str(error["frame"]), []).append(error["text"])
    unreadable = {frame: "; ".join(reasons) for frame, reasons in reasons_by_frame.items()}
    answer = {"frames": parsed["frames"], **_read_batch_fields(parsed), "unreadable": unreadable}

    return answer, _render_parse_errors(parsed["errors"])


def _read_batch_answer(text: str) -> tuple[dict[str, Any] | None, str]:
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    if parsed["errors"]:
        return None, _render_parse_errors(parsed["errors"])

    return _read_batch_fields(parsed), ""


def _read_second_pass_answer(text: str) -> tuple[dict[str, Any] | None, str]:
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    if parsed["errors"]:
        return None, _render_parse_errors(parsed["errors"])

    return {"is_nothing_to_change": parsed["is_nothing_to_change"], "additions": parsed["frames"],
            **_read_batch_fields(parsed)}, ""


def _write_outline(request: AttemptRequest,
                   parsed: dict[str, Any]) -> Path:
    """One batch's outline or R12's table of contents: the answer names where each topic starts, and the program
    fills in every entry up to the next start before the file is written and judged."""

    return document_file_service.write_outline(request.paths, _read_slug(request),
                                                document_plan_utils.expand_topic_starts(
                                                    parsed, list(request.inputs.get("entries") or [])))


def _write_table_of_contents(request: AttemptRequest,
                             parsed: dict[str, Any]) -> Path:
    """R12's whole table of contents, kept as the agent decided it: chapters and their headings, each named by where it
    starts, the entries set aside and the open points. Ordering and filling in are the program's, done in memory
    wherever they are needed (`document_plan_utils.flatten_table_of_contents`), never stored."""

    return document_file_service.write_outline(request.paths, _read_slug(request),
                                                {"chapters": [{"title": chapter.get("title", ""),
                                                               "first_entry_id": chapter.get("first_entry_id", ""),
                                                               "headings": list(chapter.get("topics") or [])}
                                                              for chapter in parsed.get("chapters") or []],
                                                 "set_aside_entry_ids": list(parsed.get("set_aside_entry_ids") or []),
                                                 "not_established": list(parsed.get("not_established") or [])})


def _write_draft_headings(request: AttemptRequest,
                          parsed: dict[str, Any]) -> Path:
    return document_file_service.write_draft_headings(request.paths, _read_slug(request), parsed)


def _write_parts(request: AttemptRequest,
                 parsed: dict[str, Any]) -> Path:
    """The agenda as answered, its first point moved to moment 1 when it starts later: what comes before the first
    point the worker named (a sound check, a greeting) can only belong to it. An agenda was refused, and its run
    blocked, for starting at moment 2 after a moment of "can you see my screen"."""

    points = [dict(point) for point in parsed.get("points") or []]
    if points and isinstance(points[0].get("first_moment"), int) and points[0]["first_moment"] > 1:
        points[0]["first_moment"] = 1
        parsed = {**parsed, "points": points}

    return document_file_service.write_parts(request.paths, parsed)


def _expand_cited_ids(cited: list[Any],
                      known: list[str]) -> list[str]:
    """The ids a claim cites with each cross-frame range written out, each id once, in the order first cited."""

    return list(dict.fromkeys(
        entry_id for entry in cited
        for entry_id in document_text_utils.expand_cross_frame_ranges(str(entry), known).split(document_text_utils.ID_SEPARATOR)))


_TOPIC_ID = re.compile(document_text_utils.TOPIC_ID_PATTERN)
# A stop the removed bracket stood before: "transformation [F40.1]." is "transformation ." once the bracket is out
_STRAY_STOP = re.compile(r"\s+([.,;:])(?=\s|$)")


def _expand_ranges(text: str,
                   known: list[str]) -> str:
    return document_text_utils.expand_cross_frame_ranges(document_text_utils.expand_line_ranges(text, known), known)


def _clean_agenda_line(line: dict[str, Any],
                       known: list[str]) -> dict[str, Any]:
    """One agenda line with the bracketed ids its model wrote into the text taken out: a line cites nothing, and a
    writer that ended every block of fifty chapters with its ids ended each agenda line with them too."""

    text = document_text_utils.remove_bracketed_entry_ids(_expand_ranges(str(line.get("text") or ""), known))

    return {**line, "text": _STRAY_STOP.sub(r"\1", " ".join(text.split()))}


def _expand_claim(claim: dict[str, Any],
                  known: list[str]) -> dict[str, Any]:
    """One summary sentence with its cross-frame ranges written out, in its text and in its `entry_ids`, the bracketed
    ids moved into `entry_ids` and its secrets hidden."""

    expanded = {**claim, "text": _expand_ranges(str(claim.get("text") or ""), known)}
    moved = document_text_utils.hide_claim_secrets(document_text_utils.move_bracketed_entry_ids(expanded))

    cited = _expand_cited_ids(moved.get("entry_ids") or [], known)
    # A topic id is a link, never a source: copied from a section's text beside the notes it rests on, it is dropped
    sources = [entry_id for entry_id in cited if not _TOPIC_ID.fullmatch(entry_id)]

    return {**moved, "entry_ids": sources or cited}


def _write_summary(request: AttemptRequest,
                   parsed: dict[str, Any]) -> Path:
    # The summary's sentences cite their sources in `entry_ids`, as a section's paragraphs do. A range across frames,
    # `[F12.1-F15.3]`, is written out first from the ids the sections cite, in document order, as a chapter's is: read
    # as it stood, it was one unknown id, and a summary that cited a chapter's span was refused for it.
    known = [str(entry_id) for entry_id in request.inputs.get("knownEntryIds") or []]
    claims = [_expand_claim(claim, known) for claim in parsed.get("summary") or []]
    agenda = [_clean_agenda_line(line, known) for line in parsed.get("agenda") or []]

    return document_file_service.write_summary(request.paths, {**parsed, "summary": claims, "agenda": agenda})


def _write_executive_summary_draft(request: AttemptRequest,
                                   parsed: dict[str, Any]) -> Path:
    return document_file_service.write_executive_summary_draft(request.paths, parsed)


def _write_review_answer(request: AttemptRequest,
                         parsed: dict[str, Any]) -> Path:
    """REQUIREMENTS.md R16: phase 5's todo list, or one todo's resolution, as submitted, for its validator to judge."""

    # A resolution's counted run of note ids is a loop of the model's, never sources (DEFECTS 297)
    if parsed.get("sentences"):
        parsed = review_validator.drop_counted_note_ids(parsed, request.paths)
        # and an id no note, line or frame has, beside one that has, is a slip the sentence's real sources outlive
        # (DEFECTS 723)
        parsed = review_validator.drop_unknown_sources_beside_known(parsed, request.paths)

    return review_file_service.write_answer(request.paths, _read_slug(request), parsed)


def _write_contents(request: AttemptRequest,
                    parsed: dict[str, Any]) -> Path:
    return document_file_service.write_contents(request.paths, parsed)


def _clean_section(markdown: str) -> str:
    """A section as the program stores it: every diagram's bare Mermaid nodes named, and the secrets of every web
    address hidden (REQUIREMENTS.md R8). Its note ids stay: they are the provenance, and the reader never sees them."""

    cleaned = section_markdown_utils.rewrite_diagrams(markdown, mermaid_text_utils.repair_diagram)
    # Short ranges are written in full first: the id rewrites after it read the ids a range hides
    cleaned = document_text_utils.write_short_ranges_in_full(cleaned)
    cleaned = document_text_utils.rewrite_marked_line_ids_in_brackets(cleaned)
    cleaned = document_text_utils.split_ids_after_fence(cleaned)

    return document_text_utils.hide_secret_query_values(cleaned)


def _write_section(request: AttemptRequest,
                   parsed: dict[str, Any]) -> Path:
    return document_file_service.write_section(request.paths, _read_slug(request),
                                                _clean_section(str(parsed.get("markdown") or "")))


def _order_texts_by_heading(written: list[str],
                            written_ids: list[str],
                            asked_ids: list[str]) -> list[str]:
    """The texts in the order the headings were asked. An answer that wrote its headings in another order was filed by
    position, each text under the other heading; when every heading is named once and they are the headings asked for,
    each text goes to the heading its own line names. Otherwise the texts stay as written."""

    is_every_heading_named = len(written_ids) == len(written)
    is_each_named_once = len(set(written_ids)) == len(written_ids)
    if not (is_every_heading_named and is_each_named_once and sorted(written_ids) == sorted(asked_ids)):
        return written
    by_id = dict(zip(written_ids, written))

    return [by_id[topic_id] for topic_id in asked_ids]


def _list_slide_frames(paths: RunPaths) -> set[int]:
    """The index of every frame whose notes name its screen a slide."""

    return _list_frames_of_kind(paths, "slide")


def _list_frames_of_kind(paths: RunPaths,
                         kind: str) -> set[int]:
    """The index of every frame whose notes' screen kind holds `kind` ("slide", "terminal")."""

    return {index for index, record in frame_records_service.map_records_by_index(paths).items()
            if kind in str(record.get("screen_kind") or "").casefold()}


def _write_chapter(request: AttemptRequest,
                   parsed: dict[str, Any]) -> Path:
    """One chapter's answer: each section stored under its heading's slug, and the whole answer beside them, which the
    chapter's check counts. A text goes to the heading its own line names when every heading is named once; otherwise
    to the heading in its place in the follow-up's order. A frame names its cut by id in the markdown; the program finds
    the cut's file in the frame store when it reads the section."""

    # Cleaned once, so the texts and the heading ids are read from the same answer
    markdown = section_markdown_utils.clean_chapter_markdown(str(parsed.get("markdown") or ""))
    # A slide's sentences fenced as plain text reached the reader as code boxes (froscon talk 3211, DEFECTS 249)
    markdown = section_markdown_utils.open_slide_fences(markdown, _list_slide_frames(request.paths))
    # A terminal's output fenced as markdown reached the reader as hard-broken paragraphs (DEFECTS 609)
    markdown = section_markdown_utils.keep_terminal_fences(markdown, _list_frames_of_kind(request.paths, "terminal"))
    # A slide's own title copied with its block is no subheading of the document (DEFECTS 655)
    markdown = section_markdown_utils.write_note_headings_as_bold(markdown, [
        str(entry.get("text") or "") for section in request.inputs.get("sections") or []
        for entry in section.get("entries") or [] if entry.get("kind") == "markdown"])
    # A he or she the recording never used, guessed from a name, becomes they (DEFECTS 502)
    markdown = document_text_utils.write_unheard_pronouns_as_they(
        text=markdown, source_text=phase_file_service.read_transcript_text(request.paths))
    written = section_markdown_utils.split_chapter_markdown(markdown)
    sections = request.inputs.get("sections") or []
    written_ids = section_markdown_utils.list_heading_ids(markdown)
    asked_ids = [str(section.get("topicId") or "") for section in sections]
    written = _order_texts_by_heading(written, written_ids, asked_ids)
    # A range across frames is written out from the request's notes in order: which notes lie between is known here
    ordered = [entry_id for section in sections for entry_id in section.get("entryIds") or []]
    for section, text in zip(sections, written):
        expanded = document_text_utils.expand_cross_frame_ranges(text, ordered)
        document_file_service.write_section(request.paths, section["slug"], _clean_section(expanded))

    return document_file_service.write_chapter_submission(request.paths, _read_slug(request),
                                                       {"sections": len(written),
                                                        "headingIds": written_ids})


# kind -> request -> the messages of one call. In phase order. Each row is one dispatch site in
# `graph/nodes/`, and a test may register a kind of its own to drive the loop with no phase behind it.
MESSAGE_BUILDERS: dict[str, Callable[[AttemptRequest], list[BaseMessage]]] = {
    "frames_processing": frames_processing_messages.build_frame_messages,
    "frame_batch": frames_processing_messages.build_frame_batch_messages,
    "batch_summary": frames_processing_messages.build_batch_summary_messages,
    "critique": frames_processing_messages.build_critique_messages,
    "critique_collection": critique_collection_messages.build_critique_collection_messages,
    "speech_structuring": speech_structuring_messages.build_speech_structuring_messages,
    "takeaway": takeaway_messages.build_takeaway_messages,
    "preprocessing": preprocessing_messages.build_preprocessing_messages,
    "second_pass": frames_processing_messages.build_second_pass_messages,
    "open_questions": frames_processing_messages.build_open_questions_messages,
    "answer_questions": frames_processing_messages.build_answer_questions_messages,
    "parts": document_messages.build_parts_messages,
    "outline": document_messages.build_outline_messages,
    "contents": document_messages.build_contents_messages,
    "section": document_messages.build_section_messages,
    "summary": document_messages.build_summary_messages,
}

# kind -> the structure the answer must parse into.
SCHEMAS: dict[str, type[BaseModel]] = {
    "parts": document_parts.DocumentParts,
    "outline": document_outline.DocumentOutline,
    "contents": document_contents.DocumentContents,
    "section": document_section.DocumentSection,
    "summary": document_summary.DocumentSummary,
}

# kind -> the reader of an answer written in markdown. A kind here is asked for no structure: the model writes the
# document its instructions teach (phase 3's notes).
MARKDOWN_READERS: dict[str, MarkdownReader] = {
    "frames_processing": _read_frame_answer,
    "frame_batch": _read_group_answer,
    "batch_summary": _read_batch_answer,
    "critique": critique_markdown_utils.parse_critique,
    # Phase 6's rules come back in the critique's own markdown
    "critique_collection": critique_markdown_utils.parse_critique,
    # Phase 2's paragraphs of an audio recording
    "speech_structuring": speech_markdown_utils.parse_speech,
    # Phase 4's one call of a takeaway run
    "takeaway": takeaway_markdown_utils.parse_takeaway,
    # Phase 3's prepared notes of an audio recording, one step or one item at a time
    "preprocessing": prepared_markdown_utils.parse_prepared,
    "second_pass": _read_second_pass_answer,
    "open_questions": _read_batch_answer,
    "answer_questions": _read_batch_answer,
}

# The kinds asked as a batch's follow-ups
PHASE3_FOLLOW_UPS = ("critique", "second_pass", "open_questions", "answer_questions")
# The kinds with no output limit of their own: each call may write up to the room its loaded window has left, since a
# lower limit cut long but useful thinking and the next attempt began again. A reply still cut at the window is
# compacted for the next attempt (`thinking_compaction_service`). Phase 2's one call for an audio recording writes the
# whole transcript back, so a model's own limit sized for one frame's notes would cut it; phase 3's first preparation
# step of an audio recording reads the whole speech too.
WINDOW_SIZED_KINDS = (*PHASE3_FOLLOW_UPS, "speech_structuring", "preprocessing")
# The prompt is estimated, never counted: text at 3 characters a token, each frame at 2,000 tokens (a frame enlarged
# to 1280 wide costs about 1,700), and a margin, so the room asked for never runs past the window
_CHARS_PER_TOKEN = 3
_IMAGE_TOKENS = 2_000
_ROOM_MARGIN_TOKENS = 4_000
# What a prompt that fills the window is still given to write
_MIN_ROOM_TOKENS = 1_024


def select_reasoning_effort(job_kind: str) -> str | None:
    """`"none"` for a kind sent with no thinking: a takeaway's one call unless `audio.takeaway.isThinkingOn`, since thinking
    is most of a call's time and the takeaway is the fast profile; None (the model's own) for every other kind."""

    if job_kind == "takeaway" and not app_config.settings.audio.takeaway.is_thinking_on:
        return NO_THINKING

    return None


def estimate_prompt_tokens_by_characters(messages: list[BaseMessage]) -> int:
    """A generous estimate of a prompt's tokens: its text by characters, each non-text part priced as a frame.
    The conversation of phases 4 and 5 counts its own differently (`starting_messages_utils.estimate_prompt_tokens`)."""

    chars, images = 0, 0
    for message in messages:
        content = message.content
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            if isinstance(part, str):
                chars += len(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                chars += len(str(part.get("text") or ""))
            elif isinstance(part, dict):
                images += 1

    return chars // _CHARS_PER_TOKEN + images * _IMAGE_TOKENS


def compute_window_room(messages: list[BaseMessage],
                        window_tokens: int | None) -> int | None:
    """The output limit of a window-sized call (`WINDOW_SIZED_KINDS`): the window less the prompt and a margin, or None
    (the model's own limit) when the window is not known."""

    if not window_tokens:
        return None

    return max(_MIN_ROOM_TOKENS, window_tokens - estimate_prompt_tokens_by_characters(messages) - _ROOM_MARGIN_TOKENS)


# kind -> (request, parsed) -> the file the PROGRAM wrote. No model is ever handed a path.
RECORD_WRITERS: dict[str, Callable[[AttemptRequest, dict[str, Any]], Path]] = {
    "frames_processing": _write_frame_record,
    "frame_batch": _write_group_records,
    "batch_summary": _write_batch_summary,
    "critique": _write_critique,
    "critique_collection": _write_critique_collection,
    "speech_structuring": _write_speech,
    "takeaway": _write_takeaway,
    "preprocessing": _write_prepared,
    "second_pass": _write_second_pass,
    "open_questions": _write_batch_follow_up,
    "answer_questions": _write_batch_follow_up,
    "parts": _write_parts,
    "outline": _write_outline,
    "contents": _write_contents,
    "section": _write_section,
    "chapter": _write_chapter,
    "summary": _write_summary,
    "executive_summary_draft": _write_executive_summary_draft,
    "table_of_contents": _write_table_of_contents,
    "draft_headings": _write_draft_headings,
    "review_todos": _write_review_answer,
    "review_resolution": _write_review_answer,
}


def _get_kind_row(table: dict[str, Any],
                  job_kind: str,
                  what: str) -> Any:
    if job_kind not in table:
        raise KeyError(f"no {what} for chain job kind {job_kind!r}")

    return table[job_kind]


def _read_reply(reply: ChainReplyResult,
                duration_s: float) -> ChainCallResult:
    """What the client KEPT of one reply: the parsed answer, the text, the thinking, how it ended."""

    raw = reply.raw
    parsed = reply.parsed
    usage = chain_response_utils.read_token_usage(raw)

    return ChainCallResult(outcome=call_outcome_vocabulary.CALL_COMPLETED,
                           duration_s=duration_s,
                           parsed=parsed.model_dump() if isinstance(parsed, BaseModel) else parsed,
                           text=message_content_utils.read_message_text(raw.content),
                           thinking=message_content_utils.read_reasoning(raw),
                           finish_reason=finish_reason_utils.read_finish_reason(raw),
                           parsing_error=reply.parsing_error,
                           reply_channel=reply.reply_channel,
                           **usage)


def build_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The messages of one attempt, rendered from the request and nothing else."""

    return _get_kind_row(MESSAGE_BUILDERS, request.job_kind, "message builder")(request)


def write_record(request: AttemptRequest,
                 parsed: dict[str, Any]) -> Path:
    return _get_kind_row(RECORD_WRITERS, request.job_kind, "record writer")(request, parsed)


def _bind_call_options(chat_model: BaseChatModel,
                       spec: ChainCallSpec,
                       messages: list[BaseMessage]) -> BaseChatModel:
    """The configured model with this call's output limit and thinking mode bound to it."""

    room = compute_window_room(messages, spec.window_tokens) if spec.job_kind in WINDOW_SIZED_KINDS else None
    if room is not None:
        chat_model = chat_model.bind(max_tokens=room)
    if spec.reasoning_effort == NO_THINKING:
        return chat_model_service.bind_no_thinking(chat_model)
    if spec.reasoning_effort is not None:
        return chat_model.bind(reasoning_effort=spec.reasoning_effort)

    return chat_model


def invoke_chain(model: BaseChatModel | None,
                 spec: ChainCallSpec,
                 messages: list[BaseMessage]) -> ChainCallResult:
    """Run one call of `spec.phase` on its model, structured or answered in markdown by its kind, and report process facts
    only. A markdown call writes its thinking to `spec.live_path` while it streams; `spec.reasoning_effort` is sent when
    given. An injected `model` is used as it is: no output limit and no reasoning effort are bound to it."""

    phase, job_kind = spec.phase, spec.job_kind

    # STEP 1: the model. `model` is injected by tests, and used as it is. `None` means resolve the phase's configured
    # model, and its configured structured-output method with it.
    chat_model = model if model is not None else _bind_call_options(chat_model_service.get_model(phase), spec, messages)

    # STEP 2: how the reply is read. Looked up before the call: a kind wired to no row is a defect of the program,
    # never a refused call
    reader = MARKDOWN_READERS.get(job_kind)
    schema = None if reader is not None else _get_kind_row(SCHEMAS, job_kind, "schema")

    # STEP 3: the call. One that raised while its host lost the model (a machine or LM Studio restarted under the run)
    # is asked once more when the model is back: retried at once, the step spent its attempts in seconds and blocked
    started = time.monotonic()
    try:
        reply = _call_model(chat_model, reader, schema, phase, messages, spec)
    except Exception as exc:  # noqa: BLE001 (a host that refused is a rejected attempt, not a dead run)
        if model is not None or not host_return_service.wait_for_lost_model(phase):
            return ChainCallResult(outcome=call_outcome_vocabulary.CALL_ERROR, detail=f"{type(exc).__name__}: {exc}",
                                   duration_s=time.monotonic() - started)
        started = time.monotonic()
        try:
            reply = _call_model(chat_model, reader, schema, phase, messages, spec)
        except Exception as again:  # noqa: BLE001 (as above)
            return ChainCallResult(outcome=call_outcome_vocabulary.CALL_ERROR,
                                   detail=f"{type(again).__name__}: {again}", duration_s=time.monotonic() - started)

    return _read_reply(reply, time.monotonic() - started)


def _call_model(chat_model: BaseChatModel,
                reader: MarkdownReader | None,
                schema: Any,
                phase: str,
                messages: list[BaseMessage],
                spec: ChainCallSpec) -> ChainReplyResult:
    """One call to the host: streamed and read as markdown when the kind has a reader, structured otherwise."""

    if reader is not None:
        return chain_runner.invoke_markdown_chain(chat_model, reader, messages, spec.live_path)

    return chain_runner.invoke_structured_chain(
        chat_model, schema, chat_model_service.resolve_structured_method(phase), messages)
