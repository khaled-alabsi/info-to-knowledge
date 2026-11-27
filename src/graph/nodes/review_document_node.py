"""Phase 5: the review. REQUIREMENTS.md R16.

A worker that did not write the finished document reads it as a critic and lists what a reader still cannot know, as
todos; then, in the same conversation, it resolves them one follow-up at a time, reading the sources it chooses. The
PROGRAM checks every reply, keeps the document as phase 4 published it (`05_review/before.md`), makes each accepted
edit, adds each question the sources did not answer to the document's open points, publishes the reviewed document in
place of phase 4's with one closing line and the report beside it, and judges the published folder from disk.

Built on the document phase 4 published, found by the name phase 4 gave it, and for the sources on phase 2's frames,
phase 3's notes and the merge of both with the transcript. A missing input is refused by name. On a resume the store
decides: an accepted todo is never asked again, one that was not settled is. A document phase 4 published again
unchanged gets the review back without a call; one phase 4 rewrote is reviewed from the start.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph.graph_state import RunState
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models import video_workflow_vocabulary
from models.illustration_context import IllustrationContext
from models.pipeline_errors import MissingPhaseInput
from models.review_reading_context import ReviewReadingContext
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from prompts import review_messages
from schemas import review_actions
from services import document_folder_service
from services import dry_run_service
from services import frame_records_service
from services import integration_service
from services import performance_log_service
from services import phase_file_service
from services import phase_hold_service
from services import review_conversation_service
from services import review_file_service
from services import validation_service
from services import video_workflow_service
from utils import document_audit_utils
from utils import document_edit_utils
from utils import json_store_utils
from utils import review_report_utils

PHASE = "review"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]


def _mark_phase(ctx: RunContext,
                status: str,
                handles: TimingHandles) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def _list_kept_images(images: list[dict],
                      removed: set[int]) -> list[dict]:
    """The figure records of the figures the review kept, numbered as the published document numbers them now."""

    kept = [image for image in images if int(image["figure"]) not in removed]

    return [{**image, "figure": number,
             "file": f"{output_contract.RESOURCES_DIR_NAME}/{document_folder_service.build_figure_file_name(number)}"}
            for number, image in enumerate(sorted(kept, key=lambda row: int(row["figure"])), start=1)]


def _find_published_document(ctx: RunContext) -> Path:
    """The document phase 4 published, by the name it published it under. Raises `MissingPhaseInput`, by name."""

    phase_file_service.require_phase_output(ctx.paths.summary, output_contract.PHASE_NUMBERS["finalize"])
    published = document_folder_service.build_document_path(ctx.paths)
    phase_file_service.require_phase_output(published, output_contract.PHASE_NUMBERS["finalize"])

    return published


def _build_reading(ctx: RunContext) -> ReviewReadingContext:
    """What the reading tools read: the merge, phase 2's frames and phase 3's notes.
    Raises `MissingPhaseInput`, by name, for the merge or the frames."""

    integration = integration_service.build_integration(ctx.paths)
    entries = list(integration.get("entries", []))
    entries.sort(key=lambda entry: float((entry.get("timestamps") or [entry.get("start") or 0.0])[0]))
    items = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"]).get("items", [])
    document, review = app_config.settings.document, app_config.settings.review

    return ReviewReadingContext(
        paths=ctx.paths, document_path=review_file_service.build_working_document_path(ctx.paths),
        transcript=integration_service.list_transcript_lines(integration),
        entries=entries, frame_items={int(item["index"]): item for item in items},
        frame_records=frame_records_service.map_records_by_index(ctx.paths),
        read_chars=document.read_chars, notes_chars=document.notes_chars, read_budget=review.reads,
        illustration=IllustrationContext(paths=ctx.paths, video_path=ctx.paths.repository / ctx.paths.module,
                                         frames={item["slug"]: item for item in items},
                                         min_cut_pixels=document.min_cut_pixels, chapter="review"))


def _list_removed_figures(store: dict) -> dict:
    """The figures the program removed before the critique, by number."""

    return (store.get("audit") or {}).get("removedFigures") or {}


def _list_todo_removed_figures(todos: list[dict]) -> set[int]:
    """The figures accepted todos removed or replaced by a block, by their number in the reviewed document: left with
    their files in resources and the later figures unnumbered, a removed figure failed the folder's check as linked
    nowhere and stopped the phase (the Closing Ceremony's review)."""

    actions = (review_actions.FIGURE_REMOVE, review_actions.FIGURE_REPLACE)

    return {number for todo in todos
            if todo.get("status") == "resolved" and review_conversation_service.read_figure_action(todo) in actions
            for number, _file in document_audit_utils.list_figures(str(todo.get("replaces") or ""))}


def _map_to_published_numbers(before_text: str,
                              audit_removed: set[int],
                              reviewed_numbers: set[int]) -> set[int]:
    """`reviewed_numbers`, figures numbered as the reviewed document numbers them once the audit's removals were made,
    as the published folder's files number them."""

    kept = [number for number, _file in document_audit_utils.list_figures(before_text) if number not in audit_removed]

    return {kept[number - 1] for number in reviewed_numbers if 0 < number <= len(kept)}


def _load_or_start_review(ctx: RunContext,
                          published_text: str) -> dict:
    """The review a previous life left, when the published document is the one it found or the one it made; otherwise
    a new review of the published document."""

    store = review_file_service.read_store(ctx.paths)
    digest = review_file_service.compute_text_hash(published_text)
    is_own = bool(store) and digest in (store.get("sourceHash"), store.get("reviewedHash"))
    if is_own and review_file_service.build_before_path(ctx.paths).is_file():
        return store
    ctx.log.info(f"phase {PHASE_NUMBER}: a new review of the published document")

    return review_file_service.start_review(ctx.paths, published_text)


def _publish(ctx: RunContext,
             published: Path,
             store: dict,
             reading: ReviewReadingContext) -> None:
    """The report, as the phase's file; and, once the critique was accepted, the reviewed document in place of phase 4's
    with its closing line, the report beside it, and the review in the folder's provenance. Sets `store["reviewedHash"]`
    on the review and saves it."""

    entries_by_id = {entry["id"]: entry for entry in reading.entries}
    frame_times = {number: float(item.get("timestamp", 0.0)) for number, item in reading.frame_items.items()}
    report = review_report_utils.render_report(store, entries_by_id, frame_times)
    review_file_service.write_report(ctx.paths, report)
    beside = published.parent / output_contract.REVIEW_REPORT_FILE
    if not review_file_service.is_critique_accepted(store):
        beside.unlink(missing_ok=True)

        return
    todos = store["todos"]
    counts = review_report_utils.count_outcomes(todos)
    working = review_conversation_service.write_working_document(ctx.paths, store)
    # A figure a todo removed goes as the audit's do: the rest numbered on, its file set aside with theirs
    removed_by_todos = _list_todo_removed_figures(todos)
    working = document_audit_utils.remove_figures(working, removed_by_todos)
    text = document_edit_utils.add_review_line(working, counts)
    published.write_text(text, encoding="utf-8")
    beside.write_text(report, encoding="utf-8")
    audit_removed = {int(number) for number in _list_removed_figures(store)}
    removed_figures = audit_removed | _map_to_published_numbers(
        review_file_service.build_before_path(ctx.paths).read_text(encoding="utf-8"), audit_removed, removed_by_todos)
    document_folder_service.set_aside_removed_figures(ctx.paths.output_root, published.parent, removed_figures)
    _write_provenance(published, todos, counts, removed_figures)
    store["reviewedHash"] = review_file_service.compute_text_hash(text)
    review_file_service.write_store(ctx.paths, store)


def _write_provenance(published: Path,
                      todos: list[dict],
                      counts: dict,
                      removed_figures: set[int]) -> None:
    """The published folder's provenance with the review in it: the figures renumbered past the ones it removed, each
    sentence it added and each point it left open."""

    provenance_path = published.parent / output_contract.DOCUMENT_PROVENANCE_FILE
    provenance = json_store_utils.read_json(provenance_path) if provenance_path.is_file() else {}
    # The document and the image files are renumbered when the review drops a figure; the record of which frame each
    # figure comes from was not, so from the first dropped figure on it named the wrong frame while its caption and
    # alt text still matched.
    provenance["images"] = _list_kept_images(provenance.get("images") or [], removed_figures)
    provenance["review"] = {
        "phase": PHASE_NUMBER, "report": output_contract.REVIEW_REPORT_FILE, "counts": counts,
        "sentences": [{"todo": todo["id"], "section": todo["editSection"],
                       "text": document_edit_utils.render_new_text([sentence]),
                       "transcriptIds": sentence.get("transcript_ids") or [], "frames": sentence.get("frames") or [],
                       "entryIds": sentence.get("entry_ids") or []}
                      for todo in todos if todo["status"] == "resolved" for sentence in todo["sentences"]],
        # The reads its follow-up made, from its own calls, beside the searches it reported: a worker reported finds
        # and a transcript read it never made
        "openPoints": [{"todo": todo["id"], "question": todo["question"], "searches": todo["searches"],
                        "reads": todo.get("reads") or []}
                       for todo in todos if todo["status"] == "unresolved"]}
    json_store_utils.write_json(provenance_path, provenance)


def _record_off(ctx: RunContext,
                reason: str) -> dict:
    ctx.log.info(f"phase {PHASE_NUMBER}: {reason}")
    ctx.store.set_phase(PHASE, "off")

    return {"phases": {PHASE: "off"}}


def write_reviewed_document_node(state: RunState,
                                 config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]

    # STEP 1: the switch; then hold the review while anything before it is blocked. "held" is no word a resume treats
    # as done, nor is "off", so turning the switch on and resuming runs the review
    if PHASE_NUMBER in output_contract.AUDIO_OFF_PHASES and phase_file_service.is_audio_run(ctx.paths):
        return _record_off(ctx, f"off, {phase_file_service.describe_frameless_run(ctx.paths)} has no review")
    if (PHASE_NUMBER in video_workflow_vocabulary.FAST_OFF_PHASES
            and video_workflow_service.is_fast_processing(ctx.paths.output_root)):
        return _record_off(ctx, "off, a fast-processing run has no review")
    if not app_config.settings.review.is_on:
        return _record_off(ctx, "not started, review.isOn is off")
    hold_reasons = phase_hold_service.list_hold_reasons(ctx.store, PHASE_NUMBER)
    if hold_reasons:
        ctx.log.info(f"phase {PHASE_NUMBER}: not started, because {'; '.join(hold_reasons)}")

        return {"phases": {PHASE: "held"}}

    # STEP 2: find the published document and the sources, and refuse by name when something is missing
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    try:
        published = _find_published_document(ctx)
        reading = _build_reading(ctx)
    except MissingPhaseInput as exc:
        if state.get("is_dry_run"):
            ctx.log.info(f"phase {PHASE_NUMBER}: [dry-run] nothing to review yet")

            return {"phases": {PHASE: "complete"}}
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {exc}")

        return _mark_phase(ctx, "failed", handles)
    published_text = published.read_text(encoding="utf-8")

    # STEP 3: a resume skips a phase that completed on the document still published. The store on disk decides.
    store = review_file_service.read_store(ctx.paths)
    is_reviewed = bool(store) and review_file_service.compute_text_hash(published_text) == store.get("reviewedHash")
    if not state.get("is_dry_run") and ctx.store.get_phase(PHASE) == "complete" and is_reviewed:
        ctx.log.info(f"phase {PHASE_NUMBER}: already complete; not reviewing again")

        return {"phases": {PHASE: "complete"}}

    # STEP 4: on a dry run, render what the critique WOULD be sent, and call no model
    max_todos = app_config.settings.review.max_todos
    if state.get("is_dry_run"):
        _front_matter, body = document_edit_utils.split_front_matter(published_text)
        dry_run_service.describe_dry_run(ctx, PHASE, review_conversation_service.TODOS_SLUG,
                                         review_conversation_service.build_system_text(ctx.paths),
                                         review_messages.build_critique_text(body, max_todos))

        return {"phases": {PHASE: "complete"}}

    # STEP 5: the review kept or started, and the working document derived from it
    ctx.log.info("")
    ctx.log.info(f"phase {PHASE_NUMBER}: reviewing {published.parent.name}/{published.name}")
    store = _load_or_start_review(ctx, published_text)
    review_conversation_service.record_document_audit(ctx.paths, published.parent, store)
    audit = store.get("audit") or {}
    ctx.log.info(f"phase {PHASE_NUMBER}: the program removed {len(_list_removed_figures(store))} figure(s) and made "
                 f"{len(audit.get('repairs') or [])} text repair(s) before the critique")
    review_conversation_service.write_working_document(ctx.paths, store)

    # STEP 6: the critique when it has no accepted reply, then every todo not yet resolved, in the critique's order
    conversation = review_conversation_service.open_review_conversation(
        ctx, reading, store, ctx.store.read_budget("maxAttempts", app_config.settings.budgets.max_attempts))
    if not review_file_service.is_critique_accepted(store):
        review_conversation_service.request_critique(conversation, store, max_todos)
    # A todo that spends its attempts stops the review there (a blocked step stops the phase, and nothing after it
    # runs); what was accepted before it is published, and a resume asks it and the todos after it
    pending = [candidate for candidate in store.get("todos") or [] if candidate["status"] not in review_report_utils.FINAL_STATUSES]
    for todo in pending:
        # `request_resolution` records the todo's outcome on `todo` itself, which the check below reads
        review_conversation_service.request_resolution(conversation, store, todo)
        if todo["status"] == "blocked":
            ctx.log.warning(f"phase {PHASE_NUMBER}: {todo['id']} is blocked; no later todo is asked")
            break

    # STEP 7: publish the reviewed document and its report, then judge the published folder from disk
    _publish(ctx, published, store, reading)
    report = validation_service.validate_or_report_crash(ctx, attempt_request_builder.build_standalone_request(
        ctx, PHASE, inputs={"folder": str(published.parent), "document": published.name,
                            "knownEntryIds": sorted(entry["id"] for entry in reading.entries)}))
    if not report.ok:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {published.name} was rejected: {report.render_summary()} ({report.render_hint()})")

        return _mark_phase(ctx, "failed", handles)
    counts = review_report_utils.count_outcomes(store.get("todos") or [])
    dropped = (store.get("critique") or {}).get("dropped") or []
    ctx.log.info(f"phase {PHASE_NUMBER}: {counts['todos']} point(s) found: {counts['resolved']} fixed, "
                 f"{counts['unresolved']} left open, {counts['blocked']} not settled"
                 + ("" if review_file_service.is_critique_accepted(store) else "; the critique was not accepted")
                 + (f"; {len(dropped)} dropped from the critique" if dropped else ""))
    is_settled = review_file_service.is_critique_accepted(store) and not counts["blocked"]

    return _mark_phase(ctx, "complete" if is_settled else "blocked", handles)
