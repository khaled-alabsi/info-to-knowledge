"""Phase 4's pieces asked as follow-ups of one conversation. REQUIREMENTS.md R12.

Phase 4's node decides the order (the executive summary draft, the agenda, each chapter's draft headings, each
chapter's text, the final summary and title; the table of contents is the program's,
`document_plan_utils.build_table_of_contents`); this service opens the conversation and asks each piece the node hands
it. A piece is kept from disk when a previous life of the run wrote it and it still passes its validator, exactly as
on the calls path, so a resume continues at the first piece without an accepted attempt. After every piece the
working file `04_writing/draft.md` shows the plan and what is done.
"""

from __future__ import annotations

import os
import dataclasses
from pathlib import Path

import config as app_config
from models import output_contract
from models.attempt_request import AttemptRequest
from models.starting_messages_spec import StartingMessagesSpec
from models.conversation_limits_spec import ConversationLimitsSpec
from models.follow_up_spec import FollowUpSpec
from models.illustration_context import IllustrationContext
from models.run_context import RunContext
from models.write_conversation_state import WriteConversationState
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from prompts import write_messages
from services import batch_summaries_service
from services import chat_model_service
from services import frame_records_service
from services import host_window_service
from services import ingesting_profile_service
from services import illustration_action_service
from services import document_file_service
from services import performance_log_service
from services import phase_file_service
from services import run_tags_service
from services import validation_service
from services import write_conversation_service
from services import write_reading_service
from utils import document_plan_utils
from utils import document_text_utils
from utils import section_markdown_utils

__all__ = ["show_scaffold", "open_conversation", "record_agenda", "write_part_in_conversation"]

PHASE = output_contract.FINALIZE_PHASE
CHAPTER_KIND = "chapter"
DRAFT_HEADINGS_KIND = "draft_headings"
# Only the summary is asked again when its inputs changed (it summarises the sections). Every other piece, once accepted,
# is never written again on a resume.
_INPUT_BOUND_FOLLOW_UP_KINDS = ("summary",)
# The working file's two top headings: the contents' links are worked out over every heading of the file, these first
_WORKING_TITLE = "The document as it is being written"
_SCAFFOLD_HEADING = "The scaffold"


def _build_attempt_request(ctx: RunContext,
                           job_kind: str,
                           inputs: dict) -> AttemptRequest:
    return AttemptRequest(job_kind=job_kind, attempt=1, repository=ctx.repository, module=ctx.module,
                          paths=ctx.paths, ignore=ctx.ignore, inputs=inputs)


def _is_accepted_on_disk(ctx: RunContext,
                         job_kind: str,
                         inputs: dict) -> bool:
    return validation_service.validate_or_report_crash(ctx, _build_attempt_request(ctx, job_kind, inputs)).ok


def _render_plan(state: WriteConversationState) -> str:
    """The standing plan the agent writes toward: the working title and executive summary draft, and the agenda's points, read
    from disk as they are accepted."""

    paths = state.ctx.paths
    lines = []
    if document_file_service.build_executive_summary_draft_path(paths).is_file():
        draft = document_file_service.read_executive_summary_draft(paths)
        lines += [f"Working title: {draft.get('working_title', '')}", "", "Executive summary draft:",
                  *[f"- {sentence}" for sentence in draft.get("sentences") or []]]
    if document_file_service.is_agenda_known(paths):
        parts = document_file_service.read_parts(paths).get("points") or []
        lines += ["", "The agenda, one point per chapter:",
                  *[f"{number}. {part.get('title', '')}" for number, part in enumerate(parts, start=1)]]

    return ("\n".join(lines) if lines
            else "The plan is not written yet: the executive summary draft and the agenda write it.")


def _render_link_targets(state: WriteConversationState) -> str:
    if not state.link_targets:
        return ""

    return ("\n\nThe topics a section may link to, by id: "
            + "; ".join(f"{topic_id} {title}" for topic_id, title in sorted(state.link_targets.items())) + ".")


def _render_contents(state: WriteConversationState) -> list[str]:
    """The table of contents as a section of the working file, each chapter and heading a link to where it stands below;
    the anchors are worked out over the headings of this file, in its order."""

    titles: list[tuple[int, str]] = []
    for chapter_title, headings in state.scaffold:
        titles += [(0, chapter_title), *[(1, heading_title) for heading_title, _ in headings]]
    if not titles:
        return []
    leading = [_WORKING_TITLE, _SCAFFOLD_HEADING, output_contract.CONTENTS_HEADING]
    anchors = document_text_utils.build_heading_anchors([*leading, *[text for _, text in titles]])[len(leading):]

    return [f"## {output_contract.CONTENTS_HEADING}", "",
            *[f"{'  ' * level}- [{text}](#{anchor})" for (level, text), anchor in zip(titles, anchors)], ""]


def _render_scaffold(state: WriteConversationState) -> list[str]:
    """Every chapter and heading of the accepted table of contents, each heading's text where its sections are written
    and a placeholder where they are not (the working file shows the scaffold). Empty until the table of contents is
    accepted: the working file then shows the draft headings."""

    if not state.scaffold:
        return []
    paths = state.ctx.paths
    section_numbers = {slug: number for number, slug in enumerate(
        (slug for _, headings in state.scaffold for _, slugs in headings for slug in slugs), start=1)}
    # A frame is written as its cut's id; the working file sits in 04_writing/, so it points at the file from there
    draft_dir = document_file_service.build_working_draft_path(paths).parent
    cut_files = {cut_id: os.path.relpath(paths.output_root / cut["file"], draft_dir)
                 for cut_id, cut in (document_file_service.read_image_store(paths).get("cuts") or {}).items() if cut.get("file")}
    lines = _render_contents(state)
    for chapter_title, headings in state.scaffold:
        lines += [f"## {chapter_title}", ""]
        for heading_title, slugs in headings:
            lines += [f"### {heading_title}", ""]
            for slug in slugs:
                path = document_file_service.build_section_path(paths, slug)
                if path.is_file():
                    lines += [section_markdown_utils.replace_cut_ids(
                        section_markdown_utils.strip_entry_ids(path.read_text(encoding="utf-8")), cut_files)]
                else:
                    lines += [f"[placeholder: section {section_numbers[slug]} of {len(section_numbers)}, not written yet]", ""]

    return lines


def _render_draft_headings(state: WriteConversationState) -> list[str]:
    """Before the table of contents: every chapter of the agenda, with its draft headings once they are accepted (the draft
    headings show in the working file as the table of contents to be)."""

    paths = state.ctx.paths
    if not document_file_service.is_agenda_known(paths):
        return []
    lines: list[str] = []
    for number, point in enumerate(document_file_service.read_parts(paths).get("points") or [], start=1):
        lines += [f"## {point.get('title', '')}", ""]
        slug = document_file_service.build_draft_headings_slug(number)
        if document_file_service.build_draft_headings_path(paths, slug).is_file():
            lines += [f"### {heading.get('title', '')} (draft)"
                      for heading in document_file_service.read_draft_headings(paths, slug).get("headings") or []]
        else:
            lines += ["[draft headings not found yet]"]
        lines += [""]

    return lines


def _write_working_draft(state: WriteConversationState) -> None:
    """The working file: the plan, then the scaffold as it fills. `04_document.md` is written only at the end."""

    path = document_file_service.build_working_draft_path(state.ctx.paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    scaffold = (_render_scaffold(state) or _render_draft_headings(state)
                or ["The table of contents is not written yet: once it is, every heading appears here with a "
                    "placeholder, and each section's text replaces its placeholder."])
    path.write_text("\n".join([f"# {_WORKING_TITLE}", "", _render_plan(state), "",
                               f"# {_SCAFFOLD_HEADING}", "", *scaffold, ""]), encoding="utf-8")


def _record_done_and_rewrite_draft(state: WriteConversationState,
                                   slug: str,
                                   status: str) -> None:
    done_lines = [*state.starting_messages.done_lines, f"[{slug}: {status}]"]
    state.starting_messages = dataclasses.replace(state.starting_messages, done_lines=done_lines)
    _write_working_draft(state)


def open_conversation(ctx: RunContext,
                      entries: list[dict],
                      transcript: list[dict],
                      note_sections: list[tuple[float, str]],
                      max_attempts: int) -> WriteConversationState:
    """The conversation phase 4 writes in: its model, its instruction, its reading tools over `entries`, the whole
    `transcript` and the notes file's sections, its limits, and the notes a previous life of the run left."""

    # STEP 1: the frames and the reading context
    document, budgets = app_config.settings.document, app_config.settings.budgets
    phase_model = app_config.settings.resolve_phase_model(PHASE)
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    frames = (phase_file_service.read_phase_file(ctx.paths, frames_number).get("items", [])
              if phase_file_service.has_phase_file(ctx.paths, frames_number) else [])
    reading = WriteReadingContext(
        paths=ctx.paths, moments=write_reading_service.build_reading_moments(entries), point_starts=[],
        frame_items={item["index"]: item for item in frames},
        frame_records={record["index"]: record for record in frame_records_service.read_frame_records(ctx.paths)},
        read_chars=document.read_chars, notes_chars=document.notes_chars, read_budget=budgets.write_reads,
        transcript=transcript, batches=batch_summaries_service.read_batch_summaries(ctx.paths),
        note_sections=note_sections,
        illustration=IllustrationContext(paths=ctx.paths, video_path=ctx.paths.repository / ctx.paths.module,
                                         frames={item["slug"]: item for item in frames},
                                         min_cut_pixels=document.min_cut_pixels, chapter=""))

    # STEP 2: the starting messages
    notes = document_file_service.read_agent_notes(ctx.paths)
    folder = ctx.paths.output_root
    starting_messages = StartingMessagesSpec(system_text=write_messages.build_write_system_text(
                                                 ingesting_profile_service.resolve_profile(folder),
                                                 run_tags_service.read_tags(folder), bool(frames),
                                                 phase_file_service.is_pdf_run(ctx.paths)),
                                             plan_text="", notes=notes, done_lines=[], topic_parts_text="",
                                             follow_up_text="", findings_text="")

    # STEP 3: the limits and the model
    limits = ConversationLimitsSpec(size_limit_tokens=document.conversation_size_limit_tokens,
                                    window_tokens=host_window_service.resolve_conversation_window(
                                        ctx.log, phase_model, output_contract.PHASE_NUMBERS[PHASE]),
                                    max_output_tokens=host_window_service.resolve_max_output_tokens(phase_model),
                                    chars_per_token=document.chars_per_token, frame_tokens=document.frame_tokens,
                                    max_calls=budgets.write_calls, max_attempts=max_attempts,
                                    should_ask_why_after_rejection=budgets.ask_why_after_rejection,
                                    can_dry_run_chapters=budgets.chapter_dry_runs,
                                    should_stop_on_frame_tool_refusal=budgets.fail_on_frame_cut_fail)
    model = ctx.model if ctx.model is not None else chat_model_service.get_model(PHASE)

    # STEP 4: the image store, with the last call forgotten
    image_store = document_file_service.read_image_store(ctx.paths)
    illustration_action_service.clear_last_call(image_store)

    return WriteConversationState(ctx=ctx, model=model, reading=reading,
                                  reading_state=WriteReadingState(notes=notes, image_store=image_store),
                                  starting_messages=starting_messages, limits=limits, has_frames=bool(frames))


def record_agenda(state: WriteConversationState,
                  parts: list[dict]) -> None:
    """The steps the reading tools and the plan name, once the agenda is accepted."""

    state.reading = dataclasses.replace(state.reading, point_starts=[part["start"] for part in parts])


def write_part_in_conversation(state: WriteConversationState,
                               job_kind: str,
                               slug: str,
                               written_path: Path,
                               inputs: dict) -> bool:
    """One piece of the document as a follow-up. True when it is accepted, now or by a previous life of the run."""

    ctx = state.ctx

    # STEP 1: a piece a previous life of the run accepted, from inputs that did not change, is not asked again
    is_input_bound = job_kind in _INPUT_BOUND_FOLLOW_UP_KINDS
    expected_hash = document_plan_utils.compute_inputs_hash(inputs)
    can_reuse_accepted_attempt = not is_input_bound or document_plan_utils.is_inputs_hash_current(written_path, inputs)
    if job_kind == CHAPTER_KIND and state.link_targets:
        inputs = {**inputs, "topicIds": sorted(state.link_targets)}
    if can_reuse_accepted_attempt and _is_accepted_on_disk(ctx, job_kind, inputs):
        # An attempt that blocked on a check fixed since passes now: its record says so, or the page counts a blocked
        # step under a run that went on past it
        record = document_file_service.read_follow_up_records(ctx.paths).get(slug)
        if record is not None and record.get("status") != output_contract.FOLLOW_UP_ACCEPTED:
            document_file_service.write_follow_up_record(ctx.paths, slug, {**record, "status": output_contract.FOLLOW_UP_ACCEPTED,
                                                                            "reason": ""})
        _record_done_and_rewrite_draft(state, slug, "accepted in an earlier life of this run")
        return True

    # STEP 2: the follow-up's text, a chapter's with the topics it may link to and the tags noted elsewhere
    state.starting_messages = dataclasses.replace(state.starting_messages, plan_text=_render_plan(state),
                                                  notes=list(state.reading_state.notes), topic_parts_text="")
    text = write_messages.build_follow_up_text(_build_attempt_request(ctx, job_kind, inputs))
    if job_kind == CHAPTER_KIND:
        chapter_ids = [entry_id for section in inputs.get("sections") or [] for entry_id in section.get("entryIds") or []]
        elsewhere = write_reading_service.describe_tags_elsewhere(state.reading, chapter_ids)
        text += _render_link_targets(state) + (f"\n\n{elsewhere}" if elsewhere else "")
        if state.reading.illustration is not None:
            state.reading = dataclasses.replace(
                state.reading, illustration=dataclasses.replace(state.reading.illustration, chapter=slug))

    # STEP 3: asked, and its outcome recorded
    document_file_service.write_follow_up_request(ctx.paths, slug, text)
    # The follow-up names its place, `section 4 of 38`, so the console shows how many of its kind are left
    handles = performance_log_service.start_unit(ctx, PHASE, slug, label=state.positions.get(slug))
    outcome = write_conversation_service.run_follow_up(state, FollowUpSpec(
        submit_tool=write_messages.SUBMIT_TOOL_BY_KIND[job_kind], slug=slug, inputs=inputs, text=text,
        position=f"the {job_kind.replace('_', ' ')} follow-up {slug}",
        is_size_warned=job_kind != DRAFT_HEADINGS_KIND,
        max_calls={DRAFT_HEADINGS_KIND: app_config.settings.budgets.write_headings_calls,
                   CHAPTER_KIND: app_config.settings.budgets.write_chapter_calls}.get(job_kind),
        max_attempts=app_config.settings.budgets.write_chapter_attempts if job_kind == CHAPTER_KIND else None))
    status = output_contract.FOLLOW_UP_ACCEPTED if outcome.is_accepted else output_contract.FOLLOW_UP_BLOCKED
    performance_log_service.record_unit(ctx, handles, performance_log_service.PART_LEVEL, status, [])
    if not outcome.is_accepted:
        ctx.log.warning(f"  {slug} blocked after {outcome.attempts} attempt(s): {outcome.reason}")
    elif is_input_bound:
        document_plan_utils.build_inputs_hash_path(written_path).write_text(expected_hash, encoding="utf-8")
    document_file_service.write_follow_up_record(ctx.paths, slug, {
        "status": status, "attempts": outcome.attempts, "calls": outcome.calls,
        "reason": outcome.reason})
    _record_done_and_rewrite_draft(state, slug, status if outcome.is_accepted else f"{status}: {outcome.reason}")

    return outcome.is_accepted


def show_scaffold(state: WriteConversationState,
                  scaffold: list[tuple[str, list[tuple[str, list[str]]]]]) -> None:
    """The accepted table of contents moved into the working file at once, a placeholder under every heading."""

    state.scaffold = scaffold
    _write_working_draft(state)
