"""Phase 5's critique and each todo's resolution, asked as follow-ups of one conversation. REQUIREMENTS.md R16.

Phase 5's node decides the order (the critique, then each todo not yet answered, in the critique's order); this
service opens the conversation, asks each follow-up the node hands it, and records its outcome in the store the moment
it is known. The working document, `05_review/document.md`, is derived from the store and the document as the review
found it: every accepted edit made and every unresolved question added, in the order they were accepted. So the
checks judge each answer against the document as the review has it now, and a kill between two writes never makes an
edit twice or loses one: the next life derives it again.
"""

from __future__ import annotations

import dataclasses
import shutil
import re
from pathlib import Path
from typing import Any

import config as app_config
from models import output_contract
from models import tool_set_vocabulary
from models.starting_messages_spec import StartingMessagesSpec
from models.conversation_limits_spec import ConversationLimitsSpec
from models.follow_up_outcome import FollowUpOutcome
from models.follow_up_spec import FollowUpSpec
from models.review_reading_context import ReviewReadingContext
from models.run_context import RunContext
from models.run_paths import RunPaths
from models.write_conversation_state import WriteConversationState
from models.write_reading_state import WriteReadingState
from prompts import review_messages
from schemas import review_actions
from services import chat_model_service
from services import document_file_service
from services import document_folder_service
from services import frame_records_service
from services import host_window_service
from services import integration_service
from services import ingesting_profile_service
from services import phase_file_service
from services import run_tags_service
from services import performance_log_service
from services import document_audit_service
from services import review_file_service
from services import write_conversation_service
from utils import clock_utils
from utils import document_audit_utils
from utils import document_edit_utils
from utils import document_text_utils
from utils import earlier_notes_utils
from utils import review_report_utils
from validators import review_validator

__all__ = ["open_review_conversation", "request_critique", "request_resolution", "write_working_document",
           "render_reviewed_document", "build_todo_slug", "TODOS_SLUG", "record_document_audit"]

PHASE = "review"
TODOS_SLUG = "review-todos"


def _render_plan(store: dict[str, Any]) -> str:
    todos = store.get("todos")

    return review_messages.NO_PLAN_YET if todos is None else review_messages.render_todo_plan(todos)


def _list_done_lines(store: dict[str, Any]) -> list[str]:
    """One line per follow-up answered or given up so far, as the starting_messages carries them."""

    critique = store.get("critique") or {}
    lines = [f"[critique: {critique['status']}]"] if critique.get("status", "pending") != "pending" else []

    answered = [todo for todo in store.get("todos") or [] if todo["status"] != "pending"]

    return lines + [f"[{todo['id']}: {todo['status']}]" for todo in answered]


def _update_starting_messages(state: WriteConversationState,
                              store: dict[str, Any]) -> None:
    """The starting_messages as the store has it now, for the next follow-up and for any compaction."""

    state.starting_messages = dataclasses.replace(state.starting_messages, plan_text=_render_plan(store), notes=list(state.reading_state.notes),
                                      done_lines=_list_done_lines(store))


def _run_timed_follow_up(state: WriteConversationState,
                         spec: FollowUpSpec,
                         label: str) -> FollowUpOutcome:
    """One follow-up, timed as a unit of the phase, with the follow-up's reads counted from zero."""

    ctx = state.ctx
    state.reading_state.reads_used = 0
    state.reading_state.seen_reads.clear()
    handles = performance_log_service.start_unit(ctx, PHASE, spec.slug, label=label)
    outcome = write_conversation_service.run_follow_up(state, spec)
    performance_log_service.record_unit(ctx, handles, "part", "accepted" if outcome.is_accepted else "blocked", [])
    if not outcome.is_accepted:
        ctx.log.warning(f"  {label} blocked after {outcome.attempts} attempt(s): {outcome.reason}")

    return outcome


def _split_last_answer(paths: RunPaths,
                       document: str,
                       max_todos: int,
                       is_accepted: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The critique's last answer as the todos it keeps and the ones it drops, each dropped one with why. An accepted
    answer keeps them all. One that ran out of attempts keeps each todo that passes its checks, the first `max_todos`
    of them: found on the first real run, where two bad quotes of a long list lost every other todo with them."""

    if not review_file_service.build_answer_path(paths, TODOS_SLUG).is_file():
        return [], []
    todos = list(review_file_service.read_answer(paths, TODOS_SLUG).get("todos") or [])
    if is_accepted:
        return todos, []
    kept, dropped = [], []
    for number, todo in enumerate(todos, start=1):
        # Named by its place in the answer, never by a todo id: the kept todos are numbered again from 1, and a dropped
        # one named TODO-01 stood beside a kept TODO-01 in the report
        label = f"item {number} of the answer"
        defect = review_validator.find_todo_defect(document, todo, label)
        if defect is None and len(kept) < max_todos:
            kept.append(todo)
        else:
            detail = defect[1] if defect is not None else f"{label} is past the {max_todos} todos allowed"
            dropped.append({**todo, "answerId": label, "finding": detail})

    return kept, dropped


def build_todo_slug(todo_id: str) -> str:
    """The slug a todo's answer and call log are written under: `review-todo-03`."""

    return f"review-{todo_id.lower()}"


def render_reviewed_document(before_text: str,
                             todos: list[dict[str, Any]],
                             removed_figures: set[int] | None = None) -> str:
    """`before_text` with the program's audit applied first (the figures `removed_figures` names taken out, the text
    defects repaired), then every answered todo in the order it was accepted: a resolved one's edit made, and its
    question, when the sources answered only part of it, added to the open points; an unresolved one's question added
    to the open points. Its front matter is kept as it was."""

    front_matter, body = document_edit_utils.split_front_matter(before_text)
    body = document_audit_utils.repair_text(document_audit_utils.remove_figures(body, removed_figures or set())).text
    for todo in sorted((todo for todo in todos if todo.get("status") in review_report_utils.FINAL_STATUSES),
                       key=lambda todo: todo["order"]):
        if todo["status"] == "resolved":
            body = document_edit_utils.apply_edit(body, {"section": todo["editSection"], "replaces": todo["replaces"],
                                                         "after": todo["after"], "sentences": todo["sentences"],
                                                         "figure_action": read_figure_action(todo),
                                                         "markdown_block": todo.get("markdownBlock") or ""})
        if str(todo.get("question") or "").strip():
            body = document_edit_utils.add_open_questions(
                body, [todo["question"]], todo.get("quote") if todo["status"] != "resolved" else "")

    return front_matter + body


def read_figure_action(todo: dict[str, Any]) -> str:
    """The figure action a resolved todo took: the one it stored, or, for a todo stored without it, a removal when its
    edit replaces a figure's line with no sentence. Applied as a sentence edit, a removal took the image line out and
    left its caption and its "What the figure shows" box behind."""

    if todo.get("figureAction"):
        return str(todo["figureAction"])
    is_figure_line = bool(document_audit_utils.list_figures(str(todo.get("replaces") or "")))

    return review_actions.FIGURE_REMOVE if is_figure_line and not todo.get("sentences") else ""


def write_working_document(paths: RunPaths,
                           store: dict[str, Any]) -> str:
    """The working document derived from the store and `before.md`, written where the checks and the tools read it."""

    removed = {int(number) for number in ((store.get("audit") or {}).get("removedFigures") or {})}
    text = render_reviewed_document(review_file_service.build_before_path(paths).read_text(encoding="utf-8"),
                                    store.get("todos") or [], removed)
    review_file_service.build_working_document_path(paths).write_text(text, encoding="utf-8")

    return text


def build_system_text(paths: RunPaths) -> str:
    """`review.md` with the run's ingesting profile, its tags and whether it has frames."""

    folder = paths.output_root

    return review_messages.build_review_system_text(ingesting_profile_service.resolve_profile(folder),
                                                    run_tags_service.read_tags(folder), phase_file_service.has_run_frames(paths),
                                                    phase_file_service.is_pdf_run(paths))


def open_review_conversation(ctx: RunContext,
                             reading: ReviewReadingContext,
                             store: dict[str, Any],
                             max_attempts: int) -> WriteConversationState:
    """The conversation phase 5 reviews in: its model, its instruction, its reading tools, its limits, and the notes
    and outcomes a previous life of the phase left."""

    document, review = app_config.settings.document, app_config.settings.review
    phase_model = app_config.settings.resolve_phase_model(PHASE)
    notes = review_file_service.read_notes(ctx.paths)
    has_frames = phase_file_service.has_run_frames(ctx.paths)
    starting_messages = StartingMessagesSpec(system_text=build_system_text(ctx.paths),
                                  plan_text=_render_plan(store), notes=notes, done_lines=_list_done_lines(store),
                                  topic_parts_text="", follow_up_text="", findings_text="")
    limits = ConversationLimitsSpec(size_limit_tokens=document.conversation_size_limit_tokens,
                                    window_tokens=host_window_service.resolve_conversation_window(
                                        ctx.log, phase_model, output_contract.PHASE_NUMBERS[PHASE]),
                                    max_output_tokens=host_window_service.resolve_max_output_tokens(phase_model),
                                    chars_per_token=document.chars_per_token, frame_tokens=document.frame_tokens,
                                    max_calls=review.calls, max_attempts=max_attempts,
                                    should_ask_why_after_rejection=app_config.settings.budgets.ask_why_after_rejection)
    model = ctx.model if ctx.model is not None else chat_model_service.get_model(PHASE)

    # Phase 4's frame store, so a figure todo's recut is a cut of the same store, its id after phase 4's last
    image_store = document_file_service.read_image_store(ctx.paths)

    return WriteConversationState(ctx=ctx, model=model, reading=reading,
                                  reading_state=WriteReadingState(notes=notes, image_store=image_store),
                                  starting_messages=starting_messages, limits=limits, tool_set=tool_set_vocabulary.REVIEW_TOOL_SET,
                                  has_frames=has_frames)


def request_critique(state: WriteConversationState,
                     store: dict[str, Any],
                     max_todos: int) -> None:
    """Job 1: the critique over the whole document. Its todos enter the store, numbered in its order and pending: all
    of them when it was accepted, the ones that pass their checks when it ran out of attempts. Its outcome is recorded
    either way, with the todos it dropped."""

    paths = state.ctx.paths
    _front_matter, body = document_edit_utils.split_front_matter(
        review_file_service.build_working_document_path(paths).read_text(encoding="utf-8"))
    _update_starting_messages(state, store)
    outcome = _run_timed_follow_up(state, FollowUpSpec(submit_tool="submit_review", slug=TODOS_SLUG,
                                       inputs={"slug": TODOS_SLUG, "maxTodos": max_todos},
                                       text=review_messages.build_critique_text(body, max_todos),
                                       position="the critique",
                                       figures=tuple(review_messages.list_placed_figures(
                                           body, document_folder_service.build_document_dir(paths)))),
                                "critiquing the document")
    kept, dropped = _split_last_answer(paths, body, max_todos, outcome.is_accepted)
    if outcome.is_accepted or kept:
        store["todos"] = [{"id": review_file_service.build_todo_id(number), **todo, "status": "pending", "reason": ""}
                          for number, todo in enumerate(kept, start=1)]
    status = "accepted" if outcome.is_accepted else "partial" if kept else "blocked"
    store["critique"] = {"status": status, "attempts": outcome.attempts, "reason": outcome.reason, "dropped": dropped}
    review_file_service.write_store(paths, store)


def _write_recut_figure(paths: RunPaths,
                        answer: dict[str, Any],
                        image_store: dict[str, Any]) -> Path | None:
    """A recut figure: the cut's image written over the figure's file in the published folder, the file it replaces
    kept first in `05_review/figures-before/`, so the document's line stays and shows the new cut. None when the
    answer recuts nothing."""

    if str(answer.get("figure_action") or "") != review_actions.FIGURE_RECUT:
        return None
    match = re.search(r"\]\(([^)\s]+)", str(answer.get("replaces") or ""))
    cut = (image_store.get("cuts") or {}).get(str(answer.get("cut") or "")) or {}
    if not match or not cut.get("file"):
        return None
    target = document_folder_service.build_document_dir(paths) / match.group(1)
    kept = review_file_service.build_review_dir(paths) / "figures-before" / target.name
    kept.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not kept.exists():
        shutil.copy2(target, kept)
    shutil.copy2(paths.output_root / cut["file"], target)

    return target


def _read_changed_section(paths: RunPaths,
                          todo: dict[str, Any]) -> str:
    """The todo's section as the review has it now, when its quoted sentence is no longer there; else empty."""

    _front_matter, document = document_edit_utils.split_front_matter(
        review_file_service.build_working_document_path(paths).read_text(encoding="utf-8"))
    section, quote = str(todo.get("section") or ""), str(todo.get("quote") or "")
    if not quote.strip() or document_edit_utils.find_quote(document, section, quote) is not None:
        return ""
    sections = document_edit_utils.read_sections(document, section)

    return sections[0] if sections else ""


def _add_todo_hints(paths: RunPaths,
                    todo: dict[str, Any]) -> dict[str, Any]:
    """The todo with what the program knows of where to look: a figure's frame by the provenance (DEFECTS 246), a
    contradiction's other side's notes (DEFECTS 245), the lines of any frame's notes that use a rare word of it
    (DEFECTS 269), and the words of the notes its sentence was written from, with whether they state it (DEFECTS 402)."""

    quote = str(todo.get("quote") or "")
    other = (document_file_service.find_other_side_sources(paths, quote, str(todo.get("missing") or ""))
             if todo.get("kind") == "contradiction" else [])

    # The frames whose notes use a rare word of the todo: a deferred question was resolved from the frame it was asked
    # on only, and the talk's answer two frames on was never read (DEFECTS 269)
    records = frame_records_service.read_frame_records(paths)
    related = earlier_notes_utils.list_earlier_lines([f"{quote} {todo.get('missing') or ''}"], records)

    # The words of the notes the sentence was written from: named by id alone, a reviewer read none of them, judged a
    # sentence its note states word for word against the figure beside it and wrote that figure's content in its
    # place (DEFECTS 402)
    sources = set(document_file_service.find_quote_sources(paths, quote))
    notes = [(str(entry["id"]), " ".join(str(entry.get("text") or "").split()))
             for entry in integration_service.build_integration(paths).get("entries", [])
             if entry["id"] in sources] if sources else []

    return {**todo, "figureFrame": document_folder_service.find_figure_frame(paths, quote), "otherSources": other,
            "relatedLines": related, "sourceNotes": notes,
            # Each frame's time, which the reads take: named by number alone, frame 116 at 46:26 was looked for at
            # 04:30 and 06:00 (DEFECTS 613)
            "frameTimes": {int(record["index"]): clock_utils.render_clock(float(record["timestamp"])) for record in records},
            "isStatedByNotes": bool(notes) and review_validator.is_stated_by_its_notes(paths, quote)}


def request_resolution(state: WriteConversationState,
                       store: dict[str, Any],
                       todo: dict[str, Any]) -> None:
    """Job 2, for one todo of the store. On acceptance its answer and the order it was accepted in are recorded, and the
    working document is derived again; otherwise it is recorded as not settled, with why."""

    # STEP 1: the request: the todo, where its quote stands in the sources, the section it sits in
    paths = state.ctx.paths
    todos = store["todos"]
    slug = build_todo_slug(todo["id"])
    _update_starting_messages(state, store)
    spec = FollowUpSpec(submit_tool="submit_resolution", slug=slug,
                        inputs={"slug": slug, "todoKind": str(todo.get("kind") or ""), "todoQuote": str(todo.get("quote") or "")},
                        text=review_messages.build_resolve_text(
                            _add_todo_hints(paths, todo), len(todos),
                            document_file_service.find_quote_sources(paths, str(todo.get("quote") or "")),
                            _read_changed_section(paths, todo)),
                        position=todo["id"])

    # STEP 2: the follow-up, and what an accepted resolution does to the todo
    outcome = _run_timed_follow_up(state, spec, f"resolving todo {todos.index(todo) + 1} of {len(todos)}")
    if outcome.is_accepted:
        answer = review_file_service.read_answer(paths, slug)
        swapped = _write_recut_figure(paths, answer, state.reading_state.image_store)
        if swapped is not None:
            state.ctx.log.info(f"  {todo['id']}: {swapped.name} recut from {answer.get('cut')}")
        order = 1 + max((int(other.get("order") or 0) for other in todos), default=0)
        lost_at, lost_at_by = (("figure_cut", "program") if todo.get("kind") == "figure" else
                               review_report_utils.decide_lost_at(
                                   answer, review_file_service.read_entry_statuses(paths),
                                   review_validator.is_stated_by_its_notes(paths, str(answer.get("replaces") or ""))))
        # What goes into the document keeps no secret of a web address, as everything the document holds (R8)
        sentences = [{**sentence,
                      "text": document_text_utils.hide_secret_query_values(str(sentence.get("text") or ""))}
                     for sentence in answer.get("sentences") or []]
        question, problems = _build_todo_question(answer)
        if problems:
            state.ctx.log.info(f"  {todo['id']}: its question is left out of the open points ({', '.join(problems)})")
        todo.update({"status": answer["status"], "editSection": answer.get("section", ""),
                     "replaces": answer.get("replaces", ""), "after": answer.get("after", ""), "sentences": sentences,
                     "searches": answer.get("searches") or [],
                     "figureAction": str(answer.get("figure_action") or ""),
                     "markdownBlock": str(answer.get("markdown_block") or ""),
                     "question": document_text_utils.hide_secret_query_values(question),
                     "lost_at": lost_at, "lost_at_by": lost_at_by, "order": order, "attempts": outcome.attempts,
                     "reason": "", "reads": review_file_service.list_follow_up_reads(paths, slug)})
    else:
        todo.update({"status": "blocked", "lost_at": "", "lost_at_by": "program", "attempts": outcome.attempts,
                     "reason": outcome.reason})

    # STEP 3: the store and the working document
    review_file_service.write_store(paths, store)
    write_working_document(paths, store)


def _build_todo_question(answer: dict[str, Any]) -> tuple[str, list[str]]:
    """The question a resolution leaves for the document's open points, and what the document's text checks find in it.
    A question they find something in is left out, so it is empty then (DEFECTS 709)."""

    question = review_validator.build_open_question(str(answer.get("question") or ""))
    problems = review_validator.list_question_problems(question)

    return ("" if problems else question), problems


def record_document_audit(paths: RunPaths,
                          folder: Path,
                          store: dict[str, Any]) -> None:
    """The program's audit of the published document, once per review: the figures it removes and why, and the text
    repairs it makes, kept in the store for the report. It changes no document: the working document is rendered with
    the same repairs worked out again. Until the critique is first asked nothing rests on it, so it is worked out again:
    a resume after a fix to the audit's rules kept the figure the old rules removed."""

    critique = store.get("critique") or {}
    is_critique_asked = critique.get("status", "pending") != "pending" or int(critique.get("attempts") or 0) > 0
    if store.get("audit") is not None and is_critique_asked:
        return
    _front_matter, body = document_edit_utils.split_front_matter(
        review_file_service.build_before_path(paths).read_text(encoding="utf-8"))
    removed = document_audit_service.find_figures_to_remove(folder, body)
    repairs = document_audit_utils.repair_text(document_audit_utils.remove_figures(body, set(removed))).repairs
    store["audit"] = {"removedFigures": {str(number): reason for number, reason in removed.items()}, "repairs": repairs}
    review_file_service.write_store(paths, store)
