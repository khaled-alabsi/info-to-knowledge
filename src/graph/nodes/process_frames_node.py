"""Phase 3: one frame, or one group of consecutive frames, per invocation, finished by ONE model call per attempt.

Advances `cursor` by the frames it handled, so the loop-back edge is the graph's cycle and no loop over the
queue hides in this node. A frame that exhausts its retry budget is marked `blocked`, and phase 3 stops at once: its
batch asks no follow-up, and no later batch runs.

FRAMES ARE SEQUENTIAL, by decision: each frame's message carries the accepted notes of the frames
before it (REQUIREMENTS.md R2). Those notes are read FROM DISK, from each frame's item in `02_frames.json`, and
handed over as data, so a frame agent still starts from an empty context. Every accepted frame's notes are put into
its item the moment it is accepted, which is also what the next frame reads.

SEVERAL FRAMES PER CALL (`framesProcessing.framesPerCall` above 1): the frame at the cursor and the pending frames
after it, up to that many, go to the model in one call, each in its own message, a slide built up in steps never split.
The batch gets ONE attempt, because asking a long call again costs minutes and usually fails the same way; each frame
whose record in the reply passes its checks is accepted. The frames it did not finish go back TOGETHER for up to
`framesProcessing.repairCalls` repair rounds (the owner's order of 2026-09-27), handed the notes the batch got right
and why each failed; a frame still not finished is then asked ALONE `framesProcessing.singleFrameAttempts` times. A
batch cut at the output limit goes back as two halves. Both settings 0 is zero repair: a refused frame is blocked at
once.

`--worker N` re-runs one frame and rewrites its one entry. The frames after it were shown its OLD
notes, so they are named as stale in the log. Re-running them is the operator's decision.

EVERY UNIT TELLS ITS BATCH (REQUIREMENTS.md R15): a group's reply carries `batch_summary`, the batch told in the
third person; it is judged like a record, asked again in the repair rounds when the group had them, and otherwise in
a text-only call of its own, with `budgets.maxAttempts` attempts. A unit of one frame stands in with its record's
`third_person_description`. Each accepted account goes into `03_batch_summaries.json` the moment it is accepted.

PERFORMANCE: every frame asked alone, every repeat and every group writes its line to `performance.jsonl` the moment
it ends (`services/performance_log_service.py`), and the node that empties the queue writes the phase's line.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pathlib import Path

from langchain_core.runnables import RunnableConfig

import config as app_config
from agent_client import finish_reason_utils
from graph.graph_state import RunState
from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models.batch_follow_up_result import BatchFollowUpResult
from models.frame_repair_result import FrameRepairResult
from models.job_retry_context import JobRetryContext, JobRetryOutcome
from models.validation_report import ValidationReport
from models.run_context import RunContext
from models.run_paths import RunPaths
from services import attempt_log_service
from services import batch_summaries_service
from services import call_log_service
from services import chain_dispatch_service
from services import dry_run_service
from services import document_file_service
from services import fast_frame_note_service
from services import frame_records_service
from services import integration_service
from services import batch_history_service
from services import performance_log_service
from services import phase_file_service
from services import record_file_service
from services import validation_service
from services import video_workflow_service
from utils import chain_response_utils
from utils import critique_markdown_utils
from utils import earlier_notes_utils
from utils import addition_utils
from utils import finding_text_utils
from utils import json_store_utils
from utils import frame_markdown_utils
from utils import meeting_notes_utils
from utils import moment_label_utils
from validators import item_validator

# How many of the next frame's words a batch's open questions and answers are handed: about 25 seconds of speech
SPEECH_AFTER_WORDS = 60

PHASE = "frames_processing"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]

# Which message builder, schema, record writer and validator this step uses. NOT the phase.
FRAMES_PROCESSING_KIND = "frames_processing"
GROUP_KIND = "frame_batch"
# Which validator judges one frame of a group, whose call the group's own validator already judged.
RECORD_KIND = "frame_record"
# R15: the call that asks for a batch's account alone, and the validator of an account a group call wrote.
SUMMARY_KIND = "batch_summary"
# R19: the critique and the second pass from it, each behind its own switch.
CRITIQUE_KIND = "critique"
SECOND_PASS_KIND = "second_pass"
OPEN_QUESTIONS_KIND = "open_questions"
ANSWER_QUESTIONS_KIND = "answer_questions"
SUMMARY_RECORD_KIND = "batch_summary_record"


# How the frames a batch's call did not finish are asked again (`_choose_repair`)
REPAIR_TOGETHER = "together"
REPAIR_ALONE = "alone"
REPAIR_NONE = "none"

# Where a stored batch account came from: the batch's own call, or its frames' accepted descriptions standing in
BATCH_SOURCE = "batch"
STAND_IN_SOURCE = "third_person_description"
ITEM_LEVEL = performance_log_service.ITEM_LEVEL
BATCH_LEVEL = performance_log_service.BATCH_LEVEL

# What each follow-up of a batch adds to the batch's slug, for its call and its working record
REPAIR_SUFFIX = "repair"
ALONE_SUFFIX = "alone"
CRITIQUE_SUFFIX = "critique"
SECOND_PASS_SUFFIX = "second-pass"
OPEN_QUESTIONS_SUFFIX = batch_summaries_service.OPEN_QUESTIONS_SUFFIX
ANSWERS_SUFFIX = batch_summaries_service.ANSWERS_SUFFIX
# A batch of accepted frames no account covered, its descriptions standing in
STAND_IN_SUFFIX = "stand-in"


def _snapshot_targets(ctx: RunContext,
                      slug: str) -> list[Path]:
    """Every file one attempt writes. The capture node copies them into the attempt's own directory,
    which is what keeps a rejected attempt's call log after the next attempt has overwritten it."""

    return [record_file_service.build_record_path(ctx.paths, slug),
            call_log_service.build_call_log_path(ctx.paths, slug)]


def _read_open_questions_before(paths: RunPaths,
                                index: int) -> list[str]:
    """The questions the latest batch before frame `index` left open after its answers; none when it left none."""

    earlier = [batch for batch in batch_summaries_service.read_batch_summaries(paths)
               if batch.get("frames") and max(int(number) for number in batch["frames"]) < index]
    if not earlier:
        return []
    latest = max(earlier, key=lambda batch: max(int(number) for number in batch["frames"]))

    return [str(question) for question in latest.get("openQuestions") or []]


def _build_inputs(ctx: RunContext,
                  slug: str,
                  item: dict,
                  call_items: list[dict]) -> dict:
    """What a call that opens at frame `item` needs, its messages and its validator: the notes before it, and the earlier
    showings of the screens `call_items`, the frames it asks, show again. Data only, so what was asked can be logged."""

    index = int(item["index"])
    # The frames file read once for everything below: each read hides the secrets of every record again
    records = frame_records_service.read_frame_records(ctx.paths)
    previous = frame_records_service.list_previous_records(records, index, app_config.settings.prompt.previous_frames_limit)
    latest_diagram = frame_records_service.find_latest_diagram(records, index)
    built_from = [record["slug"] for record in previous]
    if latest_diagram is not None and latest_diagram["slug"] not in built_from:
        built_from.append(latest_diagram["slug"])

    earlier = [record for record in records if record["index"] < index]
    tags = moment_label_utils.list_tags_so_far(earlier, app_config.settings.frames.tags_chars)

    return {"slug": slug, "item": item, "previousRecords": previous, "latestDiagram": latest_diagram,
            "builtFrom": built_from, "tagsSoFar": tags, "returns": _describe_earlier_showings(ctx, call_items, records),
            "openQuestionsBefore": _read_open_questions_before(ctx.paths, index)}


def _is_fresh_pass(ctx: RunContext,
                   cursor: int) -> bool:
    """Is this the first frame of a pass over EVERY frame, with nothing accepted in this run yet?"""

    return cursor == 0 and not ctx.selected_slugs and ctx.store.count_by_status().get("written", 0) == 0


def _mark_frames_blocked_and_clear_notes(ctx: RunContext,
                                         slugs: list[str],
                                         reason: str,
                                         attempts: int = 1,
                                         **fields) -> list[str]:
    """Every frame of `slugs` blocked with `reason`, so phase 3 stops, and the notes an earlier pass left for it cleared:
    a resume asks it again, so none of them may stand (a frame asked again by --worker or a resume). `fields` are kept
    on each, the refusal among them, so the request that asks it again says why."""

    for slug in slugs:
        ctx.store.set_item_status(slug, "blocked", attempts=attempts, blockedReason=reason, **fields)
    frame_records_service.clear_frame_records(ctx.paths, list(slugs))

    return list(slugs)


def _mark_frame_written(ctx: RunContext,
                        item: dict,
                        built_from: list[str],
                        **fields) -> str:
    """A frame whose record passed: written, its old refusal dropped (the store merges fields, and a later request would
    open with it), and its notes saved, built from `built_from`. Under `--worker` the frames after it were shown its
    old notes, and are named as stale."""

    slug = item["slug"]
    ctx.store.set_item_status(slug, "written", lastRefusal="", **fields)
    frame_records_service.save_frame_record(ctx.paths, item, record_file_service.read_frame_record(ctx.paths, slug),
                                            built_from)
    if ctx.selected_slugs:
        stale = frame_records_service.list_stale_slugs(ctx.paths, slug)
        if stale:
            ctx.log.warning(f"  {len(stale)} later frame(s) were shown the OLD notes of {slug} and are now stale: "
                            f"{', '.join(stale)}. Re-run them with `--from {PHASE_NUMBER}` if that matters.")

    return slug


def _record_outcome(ctx: RunContext,
                    item: dict,
                    built_from: list[str],
                    outcome: JobRetryOutcome) -> dict:
    """Write the decision to the store, and return this node's own state update. `written` means
    the ORCHESTRATOR validated bytes on disk."""

    slug = item["slug"]
    if not outcome.is_accepted:
        ctx.log.warning(f"  blocked after {outcome.attempts} attempt(s): {outcome.summary}")

        return {"blocked": _mark_frames_blocked_and_clear_notes(ctx, [slug], outcome.summary, outcome.attempts,
                                                blockedFindings=outcome.findings,
                                                lastRefusal=finding_text_utils.render_for_model(outcome.findings))}

    _mark_frame_written(ctx, item, built_from, attempts=outcome.attempts)

    return {"written": [slug]}


def _render_frame_numbers(items: list[dict]) -> str:
    """`frame 4` for one, `frames 1, 4, 16, 18` for several. One frame printed as "frames 4" read as a count
    (the owner, 2026-09-28)."""

    numbers = ", ".join(str(item["index"]) for item in items)

    return f"frame{'s' if len(items) != 1 else ''} {numbers}"


def _list_group(ctx: RunContext,
                queue: list[str],
                cursor: int,
                size: int) -> list[dict]:
    """The frame at the cursor and the frames after it in the queue, up to `size` of them, grown past `size` to hold a
    build's last step."""

    # Every frame of the queue is taken. A slide built up step by step (`stepOf`) is never split: the batch grows past
    # `size` to hold its last step
    items = [ctx.store.get_item(queue[cursor])]
    for slug in queue[cursor + 1:]:
        item = ctx.store.get_item(slug)
        if len(items) >= size and not _is_next_step(items[-1], item):
            break
        items.append(item)

    return items


def _is_next_step(before: dict,
                  item: dict) -> bool:
    """Whether `item` is a step of the build `before` belongs to or starts, so the two go in one batch."""

    return bool(item.get("stepOf")) and item["stepOf"] in (before["slug"], before.get("stepOf"))


def _build_group_slug(items: list[dict]) -> str:
    return f"frames-{int(items[0]['index']):03d}-{int(items[-1]['index']):03d}"


def _build_repair_slug(group_slug: str,
                       round_number: int) -> str:
    """Repair round 1 is `<batch>-repair`, round N after it `<batch>-repair-N`."""

    return f"{group_slug}-{REPAIR_SUFFIX}" + ("" if round_number == 1 else f"-{round_number}")


def _list_repair_slugs(group_slug: str) -> list[str]:
    """The call slug of every repair round the settings allow."""

    rounds = range(1, app_config.settings.frames_processing.repair_calls + 1)

    return [_build_repair_slug(group_slug, round_number) for round_number in rounds]


def _build_group_inputs(ctx: RunContext,
                        items: list[dict]) -> dict:
    """What a batch's call is handed, for the call and for the dry run alike: the first frame's inputs under the
    batch's slug, its frames, and the earlier showings of the screens it shows again."""

    return {**_build_inputs(ctx, _build_group_slug(items), items[0], items), "items": items}


def _describe_earlier_showings(ctx: RunContext,
                               items: list[dict],
                               records: list[dict]) -> dict[str, dict]:
    """Each frame of `items` that shows an earlier screen again, by its number: the frame it repeats, when that one was
    shown, its accepted notes among `records` (None while that frame is in the same batch) and the words said over it
    then."""

    records_by_slug = {record["slug"]: record for record in records}
    returns = {}
    for item in items:
        if not item.get("repeatOf"):
            continue
        original = ctx.store.get_item(item["repeatOf"])
        returns[str(item["index"])] = {"originalIndex": int(original["index"]), "originalTimestamp": original["timestamp"],
                                       "originalRecord": records_by_slug.get(original["slug"]),
                                       "originalSpeech": original.get("transcriptCurrent") or ""}

    return returns


def _describe_repeats(ctx: RunContext,
                      repeats: list[dict]) -> list[dict]:
    """The frames of a batch that show an earlier screen again, as its follow-ups' words name them: their words, and the
    frame they repeat."""

    return [{"slug": repeat["slug"], "index": int(repeat["index"]), "timestamp": repeat["timestamp"],
             "transcriptCurrent": repeat.get("transcriptCurrent") or "",
             "repeatOfIndex": int(ctx.store.get_item(repeat["repeatOf"])["index"])} for repeat in repeats]


def _list_spoken(ctx: RunContext,
                 covered: list[dict]) -> list[dict]:
    """What was said while each frame of the batch was shown, a frame that shows an earlier screen again marked with
    the frame it repeats."""

    spoken = [{"index": int(item["index"]), "timestamp": item["timestamp"],
               "transcriptCurrent": item.get("transcriptCurrent") or ""} for item in covered if not item.get("repeatOf")]
    repeats = [item for item in covered if item.get("repeatOf")]

    return spoken + _describe_repeats(ctx, repeats) + _list_spoken_between(ctx, covered)


def _list_spoken_between(ctx: RunContext,
                         covered: list[dict]) -> list[dict]:
    """The words of each frame between the batch's first and last frame that an earlier batch noted: a resumed batch
    held a frame asked again and the frames after it, and its follow-ups never heard the accepted frame between them,
    whose words held the answer to the batch's question."""

    indexes = {int(item["index"]) for item in covered}
    low, high = min(indexes), max(indexes)

    return [{"index": int(item["index"]), "timestamp": item["timestamp"], "notedEarlier": True,
             "transcriptCurrent": item.get("transcriptCurrent") or ""}
            for item in ctx.store.list_items().values()
            if low < int(item["index"]) < high and int(item["index"]) not in indexes and not item.get("ignored")]


def _list_frames_after(ctx: RunContext,
                       covered: list[dict]) -> list[dict]:
    """Every frame after the batch's last one, in order, skipped frames included: the batch's span and the words after
    it are both read from it, each saying whether a skipped frame counts."""

    last_index = max(int(item["index"]) for item in covered)

    return sorted((item for item in ctx.store.list_items().values() if int(item["index"]) > last_index),
                  key=lambda item: int(item["index"]))


def _compute_batch_span(ctx: RunContext,
                        covered: list[dict]) -> tuple[float, float]:
    """A batch's first frame's time, and the time the frame after its last one appears, a skipped frame too, which is
    where the batch's words end; its last frame's time when no frame follows."""

    later = [float(item["timestamp"]) for item in _list_frames_after(ctx, covered)]
    start = min(float(item["timestamp"]) for item in covered)

    return start, min(later) if later else max(float(item["timestamp"]) for item in covered)


def _list_frame_numbers(items: list[dict]) -> list[int]:
    """The frame numbers of these frames, in order."""

    return sorted(int(item["index"]) for item in items)


def _save_batch(ctx: RunContext,
                slug: str,
                covered: list[dict],
                summary: str,
                source: str,
                atmosphere: str = "",
                open_questions: list[str] | None = None,
                answered_questions: list[str] | None = None) -> dict:
    """R15: one batch into `03_batch_summaries.json`, spanning its first frame's time to the time the frame after its
    last one appears, which is where that frame's words end."""

    start, end = _compute_batch_span(ctx, covered)
    entry = {"slug": slug, "frames": _list_frame_numbers(covered), "start": start, "end": end,
             "title": _read_batch_title(ctx, slug, summary), "summary": summary, "source": source,
             "atmosphere": atmosphere, "openQuestions": open_questions or [],
             "answeredQuestions": answered_questions or []}
    batch_summaries_service.save_batch_summary(ctx.paths, entry)

    return entry


def _read_batch_record_if_present(ctx: RunContext,
                                  summary_slug: str) -> dict:
    """The batch record a call wrote under `summary_slug`, or nothing when it wrote none."""

    path = record_file_service.build_record_path(ctx.paths, summary_slug)

    return record_file_service.read_batch_record(ctx.paths, summary_slug) if path.is_file() else {}


def _read_batch_title(ctx: RunContext,
                      group_slug: str,
                      summary: str) -> str:
    """The title stored beside the account the batch kept: the batch's own reply, its repair or the account asked alone
    each leave a record, and only the one whose account was kept names the batch."""

    for call_slug in _list_account_call_slugs(group_slug):
        stored = _read_batch_record_if_present(ctx, batch_summaries_service.build_summary_slug(call_slug))
        if str(stored.get("batch_summary") or "").strip() == summary.strip():
            return str(stored.get("batch_title") or "").strip()

    return ""


def _list_account_call_slugs(group_slug: str) -> list[str]:
    """The calls of a batch that can leave an account record: the batch's own, each repair round and the account asked
    alone."""

    return [group_slug, *_list_repair_slugs(group_slug), f"{group_slug}-{ALONE_SUFFIX}"]


def _read_batch_account(ctx: RunContext,
                        call_slug: str) -> tuple[str, str]:
    """The account and the mood a call wrote for its batch, stripped; empty when it wrote none. R18: the mood travels
    with the account, so the two are read and stored together."""

    stored = _read_batch_record_if_present(ctx, batch_summaries_service.build_summary_slug(call_slug))

    return str(stored.get("batch_summary") or "").strip(), str(stored.get("atmosphere") or "").strip()


def _read_accepted_records(ctx: RunContext,
                           slugs: set[str]) -> list[dict]:
    """The accepted notes of these frames, as they stand on disk."""

    return [entry for entry in frame_records_service.read_frame_records(ctx.paths) if entry["slug"] in slugs]


def _judge_frame_records(ctx: RunContext,
                         items: list[dict],
                         call_slug: str) -> list[ValidationReport]:
    """Each frame's record the call `call_slug` left, judged by the program: one report a frame, in order."""

    return [validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, RECORD_KIND,
                                                              inputs={"slug": item["slug"], "callSlug": call_slug}))
        for item in items]


def _write_accepted_frames(ctx: RunContext,
                           items: list[dict],
                           reports: list[ValidationReport],
                           built_from: list[str],
                           attempts: int,
                           answered_in: str) -> list[str]:
    """Every frame whose record passed is accepted, in order, each built from `built_from` and the frames written before
    it in this reply; the slugs written."""

    written = []
    for item, report in zip(items, reports):
        if report.ok:
            slug = _mark_frame_written(ctx, item, built_from, attempts=attempts, answeredIn=answered_in)
            built_from = [*built_from, slug]
            written.append(slug)

    return written


def _judge_summary(ctx: RunContext,
                   call_slug: str,
                   items: list[dict]) -> ValidationReport:
    """The account a group call left, judged by the program against the batch's frames."""

    return validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, SUMMARY_RECORD_KIND,
                                                              inputs={"slug": call_slug, "items": items}))


def _request_summary_alone(ctx: RunContext,
                           items: list[dict],
                           reason: str) -> tuple[str, str]:
    """R15 and R18: one short text-only call for a batch's account and its atmosphere alone, handed the batch's
    accepted notes and everything said while its frames were shown. Both empty when this call did not pass either."""

    # STEP 1: what the batch holds, from disk: its accepted notes and its words
    group_slug = _build_group_slug(items)
    call_slug = f"{group_slug}-{ALONE_SUFFIX}"
    slugs = [item["slug"] for item in items]
    ctx.log.warning(f"  {group_slug}: the batch's account was refused → asked alone: {reason}")
    handles = performance_log_service.start_unit(
        ctx, PHASE, call_slug, slugs, label=performance_log_service.render_batch_follow_up_header(ctx, slugs, "account of"))
    accepted = _read_accepted_records(ctx, set(slugs))
    start, end = _compute_batch_span(ctx, items)
    batch = {"frames": _list_frame_numbers(items), "start": start, "end": end, "reason": reason, "accepted": accepted,
             "spoken": _list_spoken(ctx, items)}

    # STEP 2: up to `budgets.maxAttempts` attempts, each retry told why the last was refused; judged like any call
    summary_slug = batch_summaries_service.build_summary_slug(call_slug)
    job = JobRetryContext(phase=PHASE, job_kind=SUMMARY_KIND,
                          snapshot_targets=[record_file_service.build_record_path(ctx.paths, summary_slug),
                                            call_log_service.build_call_log_path(ctx.paths, call_slug)],
                          inputs={"slug": call_slug, "batch": batch})
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=call_slug,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL,
                                        "summary accepted" if outcome.is_accepted else "summary blocked", slugs)
    if not outcome.is_accepted:
        ctx.log.warning(f"  {group_slug}: no account of this batch passed: {outcome.summary}")

        return "", ""

    return _read_batch_account(ctx, call_slug)


def _process_alone(ctx: RunContext,
                   item: dict,
                   max_attempts: int) -> dict:
    """One frame, one call per attempt, asked again until its record validates or `max_attempts` are spent."""

    slug = item["slug"]
    handles = performance_log_service.start_unit(ctx, PHASE, slug, [slug])
    inputs = _build_inputs(ctx, slug, item, [item])
    job = JobRetryContext(phase=PHASE, job_kind=FRAMES_PROCESSING_KIND,
                          snapshot_targets=_snapshot_targets(ctx, slug), inputs=inputs)
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=slug, max_attempts=max_attempts)
    update = _record_outcome(ctx, item, inputs["builtFrom"], outcome)
    performance_log_service.record_unit(ctx, handles, ITEM_LEVEL, "written" if update.get("written") else "blocked", [slug])

    return update


def _record_judged_findings(job: JobRetryContext,
                            reports: list[ValidationReport]) -> None:
    """Write what the program refused in the call's notes (a frame's record, the batch's account) into the call's
    attempt `validation.json`, which held only the checks of the reply itself. Without it a refusal the batch recovered
    from (the account asked alone, a frame repaired) left no refused attempt on disk: the page's batch table and the
    monitor's gate never saw froscon talk 3211's first batch, whose account was missing (DEFECTS 272)."""

    findings = [finding.as_dict() for report in reports for finding in report.findings]
    if not findings or job.handles.log is None:
        return
    path = job.handles.log.directory / attempt_log_service.VALIDATION_FILE
    payload = json_store_utils.read_json(path) if path.is_file() else {"subject": path.parent.parent.name, "checksRun": []}
    payload["ok"] = False
    payload["findings"] = [*(payload.get("findings") or []), *findings]
    job.handles.log.write_validation(payload)


def _log_rejections(ctx: RunContext,
                    rejected: list[tuple[dict, ValidationReport]]) -> None:
    """One console line per frame a call got wrong, naming the checks it failed. The operator sees WHY a batch is
    being retried while it happens, not only that it is (the owner, 2026-09-28)."""

    for item, report in rejected:
        ctx.log.warning(f"  frame {item['index']} rejected: {finding_text_utils.render_for_console(report.findings)}")


def _log_notes(ctx: RunContext,
               judged: list[tuple[dict, ValidationReport]]) -> None:
    """What a record was accepted with a warning for: kept, never asked again, and said once so nobody opens the report.
    The line names the frame once, so each warning's own "frame N's" or "frame-NNN: " opening is said as "its"."""

    for item, report in judged:
        if report.notes:
            warnings = finding_text_utils.render_warnings_for_console(report.notes)
            warnings = warnings.replace(f"frame {item['index']}'s ", "its ").replace(f"{item['slug']}: ", "")
            ctx.log.info(f"  frame {item['index']} accepted with a warning: {warnings}")


def _is_unfinished_frame_asked_alone() -> bool:
    """Is a frame its batch and repair rounds did not finish asked alone (`framesProcessing.singleFrameAttempts` above
    0), rather than blocked?"""

    return app_config.settings.frames_processing.single_frame_attempts > 0


def _process_unfinished_frame(ctx: RunContext,
                              item: dict,
                              report: ValidationReport) -> dict:
    """A frame its batch did not finish, asked alone `framesProcessing.singleFrameAttempts` times; with 0, blocked at
    once and asked nothing, so phase 3 stops there (the owner, 2026-10-02: "0 asking on its own")."""

    # The refused record must not pass for the reply of the call that asks the frame alone
    record_file_service.remove_record(ctx.paths, item["slug"])
    if _is_unfinished_frame_asked_alone():
        return _process_alone(ctx, item, app_config.settings.frames_processing.single_frame_attempts)

    return _mark_refused_frame_blocked_and_clear_notes(
        ctx, item, report,
        "its batch did not finish it, and framesProcessing.singleFrameAttempts is 0: no frame is asked alone")


def _render_repair_round_outcome(still_failed_items: list[dict],
                                   is_round_left: bool) -> str:
    """What a repair round's console line says comes next for the frames it did not finish: another round, each asked
    alone, or blocked when no frame is asked alone; nothing when it finished them all."""

    if not still_failed_items:
        return ""
    if is_round_left:
        return " → asked again together"
    if _is_unfinished_frame_asked_alone():
        return f" → asked alone ({_render_frame_numbers(still_failed_items)})"

    return f" → blocked ({_render_frame_numbers(still_failed_items)})"


def _render_batch_outcome(failed_items: list[dict],
                            repair: str,
                            is_sent_back_in_halves: bool) -> str:
    """What a batch's console line says comes next for the frames its call did not finish."""

    if not failed_items:
        return ""
    if is_sent_back_in_halves:
        how = "the batch goes back in halves"
    else:
        how = {REPAIR_TOGETHER: "retried together", REPAIR_ALONE: "each retried alone",
               REPAIR_NONE: "blocked, no repair"}[repair]

    return f" → {how} ({_render_frame_numbers(failed_items)})"


def _choose_repair() -> str:
    """How the frames a batch's call did not finish are asked again: "together" in repair calls, each "alone", or
    "none". Both settings 0 is zero repair (the owner, while runs are monitored before production): no frame is asked
    again, and no batch cut at the output limit goes back in halves."""

    frames_settings = app_config.settings.frames_processing
    if frames_settings.repair_calls:
        return REPAIR_TOGETHER
    if _is_unfinished_frame_asked_alone():
        return REPAIR_ALONE

    return REPAIR_NONE


def _mark_batch_blocked_and_clear_notes(ctx: RunContext,
                                        slugs: list[str],
                                        call_slug: str) -> list[str]:
    """A batch whose critique, repair, open questions, answers or account were not accepted: every frame of it blocked, so phase 3
    stops and a resume asks the whole batch again (the owner: "there are no continue after block")."""

    ctx.log.warning(f"  {call_slug} blocked: {len(slugs)} frame(s) of its batch blocked, phase 3 stops")

    return _mark_frames_blocked_and_clear_notes(ctx, slugs, f"{call_slug} was not accepted; the batch is asked again on a resume")


def _mark_refused_frame_blocked_and_clear_notes(ctx: RunContext,
                                                item: dict,
                                                report: ValidationReport,
                                                reason: str) -> dict:
    """A frame its batch's reply did not finish: blocked, so phase 3 stops, and any notes an earlier pass left for it
    cleared. Its refusal is kept on it in the validator's words, so the request that asks it again on a resume says why
    (`lastRefusal`)."""

    ctx.log.warning(f"  {item['slug']} blocked: {reason}")

    return {"blocked": _mark_frames_blocked_and_clear_notes(ctx, [item["slug"]], reason,
                                            lastRefusal=finding_text_utils.render_for_model(report.findings))}


def _process_single_frames(ctx: RunContext,
                           failed: list[tuple[dict, ValidationReport]]) -> FrameRepairResult:
    """With no repair call (`framesProcessing.repairCalls` 0), each frame a batch call did not finish goes straight to its own
    single-frame calls. The batch's account, when it failed, is asked alone afterwards."""

    result = FrameRepairResult()
    for item, report in failed:
        update = _process_unfinished_frame(ctx, item, report)
        result.written += update.get("written", [])
        result.blocked += update.get("blocked", [])
        if result.blocked:
            break

    return result


def _build_repair_inputs(ctx: RunContext,
                         items: list[dict],
                         failed: list[tuple[dict, ValidationReport]],
                         accepted: list[dict],
                         repair_slug: str,
                         finish_reason: str,
                         summary_reason: str) -> dict:
    """What a repair call is handed: the frames that failed, the notes the group got right and why each failed."""

    failed_items = [item for item, _ in failed]
    repair = {"batchNumbers": [int(item["index"]) for item in items], "accepted": accepted,
              "failed": [{"frame": int(item["index"]), "reason": finding_text_utils.render_for_model(report.findings)}
                         for item, report in failed],
              "isCutAtLimit": finish_reason_utils.is_finish_reason_cut_at_output_limit(finish_reason),
              "summaryReason": summary_reason}

    return {**_build_inputs(ctx, repair_slug, items[0], failed_items), "items": failed_items, "repair": repair}


def _repair_group(ctx: RunContext,
                  items: list[dict],
                  failed: list[tuple[dict, ValidationReport]],
                  finish_reason: str,
                  summary_reason: str,
                  round_number: int = 1) -> FrameRepairResult:
    """The frames a group call did not finish, again in ONE call, handed the notes the group got right and why each of
    these failed, and, when the group's account of it failed too, why (R15). A frame this call does not finish either
    goes into the next repair round while `framesProcessing.repairCalls` has one left, and is asked alone after the last
    one. Returns a `FrameRepairResult`: the frames written and blocked, and the repaired account and mood, empty when
    none passed."""

    # STEP 1: what the group got right and what it did not, from disk, and no record it left may pass for this reply
    failed_items = [item for item, _ in failed]
    repair_slug = _build_repair_slug(_build_group_slug(items), round_number)
    failed_slugs = [item["slug"] for item in failed_items]
    handles = performance_log_service.start_unit(
        ctx, PHASE, repair_slug, failed_slugs,
        label=performance_log_service.render_listed_frames_header(ctx, failed_slugs))
    accepted = _read_accepted_records(ctx, {item["slug"] for item in items} - set(failed_slugs))
    for slug in failed_slugs:
        record_file_service.remove_record(ctx.paths, slug)
    inputs = _build_repair_inputs(ctx, items, failed, accepted, repair_slug, finish_reason, summary_reason)
    job = JobRetryContext(phase=PHASE, job_kind=GROUP_KIND,
                          snapshot_targets=[*(record_file_service.build_record_path(ctx.paths, slug)
                                              for slug in failed_slugs),
                                            call_log_service.build_call_log_path(ctx.paths, repair_slug)],
                          inputs=inputs, are_rejections_logged_by_caller=True)
    chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=repair_slug, max_attempts=1)

    # STEP 2: judge each frame's record, and log the call as the batch it was
    reports = _judge_frame_records(ctx, failed_items, repair_slug)
    still_rejected = [(item, report) for item, report in zip(failed_items, reports) if not report.ok]
    _log_rejections(ctx, still_rejected)
    _record_judged_findings(job, reports)
    still_failed_items = [item for item, _ in still_rejected]
    still_failed_count = len(still_failed_items)
    summary, atmosphere = "", ""
    if summary_reason:
        if _judge_summary(ctx, repair_slug, items).ok:
            summary, atmosphere = _read_batch_account(ctx, repair_slug)
    is_round_left = bool(still_failed_items) and round_number < app_config.settings.frames_processing.repair_calls
    next_step = _render_repair_round_outcome(still_failed_items, is_round_left)
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL,
                                        f"{len(failed_slugs) - still_failed_count} of {len(failed_slugs)} accepted{next_step}",
                                        failed_slugs)

    # STEP 3: every repaired frame joins the records, built from the group's accepted notes; the rest go into the next
    # repair round while `framesProcessing.repairCalls` has one left, and are asked alone after the last round
    blocked = []
    built_from = [*inputs["builtFrom"], *(entry["slug"] for entry in accepted)]
    written = _write_accepted_frames(ctx, failed_items, reports, built_from, round_number + 1, repair_slug)
    if is_round_left:
        again = _repair_group(ctx, items, still_rejected, finish_reason,
                              "" if summary else summary_reason, round_number + 1)
        written += again.written
        blocked += again.blocked
        summary, atmosphere = (summary, atmosphere) if summary else (again.summary, again.atmosphere)
    else:
        asked_alone = _process_single_frames(ctx, still_rejected)
        written += asked_alone.written
        blocked += asked_alone.blocked

    return FrameRepairResult(written, blocked, summary, atmosphere)


def _process_halves(ctx: RunContext,
                    items: list[dict]) -> dict:
    """A call cut at the output limit is not asked again with the same frames: the model spent the whole limit
    thinking, and the same input makes it think the same way. The group's frames go back as two smaller batches, each
    with its own account, follow-ups and file, or alone when a half is one frame."""

    # STEP 1: the first half and the second half, each as its own call
    middle = (len(items) + 1) // 2
    halves = [items[:middle], items[middle:]]
    written, blocked = [], []
    for half in halves:
        update = (_process_alone(ctx, half[0], app_config.settings.budgets.max_attempts) if len(half) == 1
                  else _process_frame_batch(ctx, half))
        written += update.get("written", [])
        blocked += update.get("blocked", [])
        if blocked:
            break

    return {"written": written, "blocked": blocked}


def _read_speech_after(ctx: RunContext,
                       covered: list[dict]) -> str:
    """The first words said over the frame after the batch: most questions a batch leaves open ask what came next, and
    without them the open questions spent minutes on a sentence the next frame finishes."""

    later = [item for item in _list_frames_after(ctx, covered) if not item.get("ignored")]
    words = str(later[0].get("transcriptCurrent") or "").split() if later else []

    return " ".join(words[:SPEECH_AFTER_WORDS]) + (" …" if len(words) > SPEECH_AFTER_WORDS else "")


def _describe_batch(ctx: RunContext,
                    covered: list[dict],
                    summary: str,
                    atmosphere: str) -> dict:
    """What a follow-up on a batch is handed: its frames' accepted notes as they stand on disk, its frames' paths,
    the words said while each frame was shown, and the account and mood written for it."""

    accepted = _read_accepted_records(ctx, {item["slug"] for item in covered})
    start, end = _compute_batch_span(ctx, covered)

    # `slug`, `stepOf` and `isTextOnly` are what tell a follow-up that a frame is a step of a build, and that the first
    # call read it as text
    return {"frames": _list_frame_numbers(covered), "start": start, "end": end, "accepted": accepted,
            "summary": summary, "atmosphere": atmosphere,
            "items": [{"index": int(item["index"]), "timestamp": item["timestamp"], "path": item.get("path", ""),
                       "transcriptBefore": item.get("transcriptBefore") or "", "slug": item["slug"],
                       **{key: item[key] for key in ("stepOf", "isTextOnly") if key in item}} for item in covered],
            "spoken": _list_spoken(ctx, covered), "speechAfter": _read_speech_after(ctx, covered)}


def _run_batch_follow_up(ctx: RunContext,
                         group_slug: str,
                         call_suffix: str,
                         kind: str,
                         verb: str,
                         covered: list[dict],
                         batch: dict,
                         describe_record: Callable[[dict], str]) -> dict | None:
    """A follow-up on a batch, judged like any call, with up to `budgets.maxAttempts` attempts. The record it left, or
    None when it did not pass: the caller blocks the batch on None."""

    call_slug = f"{group_slug}-{call_suffix}"
    slugs = [item["slug"] for item in covered]
    handles = performance_log_service.start_unit(
        ctx, PHASE, call_slug, slugs, label=performance_log_service.render_batch_follow_up_header(ctx, slugs, verb))
    job = JobRetryContext(phase=PHASE, job_kind=kind,
                          snapshot_targets=_snapshot_targets(ctx, call_slug),
                          inputs={"slug": call_slug, "batch": batch})
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=call_slug,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    record = record_file_service.read_batch_record(ctx.paths, call_slug) if outcome.is_accepted else None
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL, describe_record(record) if record is not None else "blocked",
                                        slugs)
    if record is None:
        ctx.log.warning(f"  {call_slug}: not accepted: {outcome.summary}")

    return record


def _render_open_questions_status(record: dict) -> str:
    """The open-questions call's outcome on the console: how many it found."""

    return f"{len(record.get('open_questions') or [])} found"


def _render_answers_status(record: dict) -> str:
    """The answers call's outcome on the console: how many it answered, and how many stay open."""

    return (f"{len(record.get('answered_questions') or [])} answered, "
            f"{len(record.get('open_questions') or [])} stay open")


def _list_earlier_lines(ctx: RunContext,
                        covered: list[dict],
                        questions: list[str]) -> list[tuple[int, str]]:
    """The lines of the frames before the batch whose notes use a rare word of its open questions (DEFECTS 268)."""

    first = min(int(item["index"]) for item in covered)
    earlier = [record for record in frame_records_service.read_frame_records(ctx.paths) if int(record["index"]) < first]

    return earlier_notes_utils.list_earlier_lines(questions, earlier)


def _request_open_questions_and_answers(ctx: RunContext,
                                        group_slug: str,
                                        covered: list[dict],
                                        summary: str,
                                        atmosphere: str) -> BatchFollowUpResult:
    """The batch's two follow-ups, one after the other: what it leaves open, then the answers found for those. The
    questions still open, and the answered ones (`<question> → <answer>`); nothing when the first follow-up asked
    nothing. The slug of the follow-up that did not pass, when one did not: a block stops phase 3."""

    if not app_config.settings.frames_processing.is_open_questions_on:
        ctx.log.info(f"  {group_slug}: no open questions, framesProcessing.isOpenQuestionsOn is off")

        return BatchFollowUpResult()
    if not app_config.settings.frames_processing.is_pdf_open_questions_on and phase_file_service.is_pdf_run(ctx.paths):
        ctx.log.info(f"  {group_slug}: no open questions, framesProcessing.isPdfOpenQuestionsOn is off for a PDF")

        return BatchFollowUpResult()

    # STEP 1: what the batch leaves open, asked of the notes, the words and the frames as they stand
    batch = _describe_batch(ctx, covered, summary, atmosphere)
    asked = _run_batch_follow_up(ctx, group_slug, OPEN_QUESTIONS_SUFFIX, OPEN_QUESTIONS_KIND, "open questions of",
                                 covered, batch, _render_open_questions_status)
    if asked is None:
        return BatchFollowUpResult(blocked_call_slug=f"{group_slug}-{OPEN_QUESTIONS_SUFFIX}")
    questions = list(asked.get("open_questions") or [])
    if not questions:
        return BatchFollowUpResult()

    # STEP 2: the answers, looked for in the same notes, words and frames
    answered = _run_batch_follow_up(ctx, group_slug, ANSWERS_SUFFIX, ANSWER_QUESTIONS_KIND,
                                    f"answers to the {len(questions)} open question{'s' if len(questions) != 1 else ''} of",
                                    covered, {**batch, "questions": questions,
                                              "earlierLines": _list_earlier_lines(ctx, covered, questions)},
                                    _render_answers_status)
    if answered is None:
        return BatchFollowUpResult(blocked_call_slug=f"{group_slug}-{ANSWERS_SUFFIX}")
    answers = list(answered.get("answered_questions") or [])
    # A question the answers step set aside as no question leaves the open ones and never reaches the document
    # (DEFECTS 486, 580); the console names it, so a right question set aside shows
    for entry in answered.get("not_questions") or []:
        ctx.log.info(f"  {group_slug}: set aside as no question: {str(entry)[:160]}")
    still_open = frame_markdown_utils.drop_answered_questions(list(answered.get("open_questions") or []), answers)
    still_open, answers = frame_markdown_utils.move_restated_answers_to_open(still_open, answers)

    return BatchFollowUpResult(open_questions=still_open, answered_questions=answers)


def _write_final_files(ctx: RunContext,
                       group_slug: str,
                       items: list[dict],
                       written: list[str],
                       entry: dict) -> None:
    """A batch ends as one file per frame and one summary for the batch (the owner, 2026-09-29): each written frame's
    `frame-NNN.md` holds its accepted notes with the second pass's additions in them, and
    `<group slug>-batch-summary.md` holds the account, the mood, the open questions and the answers found. The working
    files of the follow-ups and of the repairs are removed; the accepted notes live on in `02_frames.json` too."""

    accepted = {record["slug"]: record for record in frame_records_service.read_frame_records(ctx.paths)}
    frame_entries = {item["slug"]: item for item in
                     phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"]).get("items", [])}
    for item in items:
        if item["slug"] in written and item["slug"] in accepted:
            record_file_service.write_stored_frame_record(ctx.paths, item["slug"], accepted[item["slug"]],
                                                          frame_entries.get(item["slug"]))
    batch = {"batch_title": entry["title"], "batch_summary": entry["summary"], "atmosphere": entry["atmosphere"],
             "open_questions": entry["openQuestions"], "answered_questions": entry["answeredQuestions"]}
    record_file_service.write_batch_record(ctx.paths, batch_summaries_service.build_summary_slug(group_slug), batch, entry)
    # The batch's own account was just written under `group_slug`; every other call that can leave one is cleared
    leftovers = [*(batch_summaries_service.build_summary_slug(slug) for slug in _list_account_call_slugs(group_slug)
                   if slug != group_slug),
                 *(f"{group_slug}-{suffix}" for suffix in (SECOND_PASS_SUFFIX, OPEN_QUESTIONS_SUFFIX, ANSWERS_SUFFIX))]
    for slug in leftovers:
        record_file_service.remove_record(ctx.paths, slug)


def _request_critique(ctx: RunContext,
                      group_slug: str,
                      covered: list[dict],
                      summary: str,
                      atmosphere: str) -> list[dict] | None:
    """Ask this batch what went WRONG in its own accepted notes, and return the anti-pattern rules it reported. It
    CHANGES NOTHING: the rules go into `<batch>-critique.md` as an artifact, and the second pass that follows reads them
    (the owner). None when the critique itself was not accepted: a block stops phase 3."""

    call_slug = f"{group_slug}-{CRITIQUE_SUFFIX}"
    covered_slugs = [item["slug"] for item in covered]
    handles = performance_log_service.start_unit(
        ctx, PHASE, call_slug, covered_slugs, label=performance_log_service.render_batch_follow_up_header(ctx, covered_slugs, "critique of"))
    batch = _describe_batch(ctx, covered, summary, atmosphere)
    job = JobRetryContext(phase=PHASE, job_kind=CRITIQUE_KIND,
                          snapshot_targets=_snapshot_targets(ctx, call_slug),
                          inputs={"slug": group_slug, "batch": batch})
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=call_slug,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    if not outcome.is_accepted:
        performance_log_service.record_unit(ctx, handles, BATCH_LEVEL, "critique blocked", covered_slugs)

        return None
    rules = record_file_service.read_critique_record(ctx.paths, call_slug).get("rules") or []
    frames = sorted({number for rule in rules for number in critique_markdown_utils.list_rule_frames(rule)})
    status = batch_history_service.describe_critique_result(len(rules), len(frames), len(covered))
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL, status, covered_slugs)

    return rules


def _request_second_pass(ctx: RunContext,
                         group_slug: str,
                         covered: list[dict],
                         summary: str,
                         atmosphere: str,
                         rules: list[dict]) -> tuple[str, str] | None:
    """Ask this batch to repair its own accepted notes from the rules its critique reported, and return the account
    and the mood after it (R19). Every addition passes the guards in `utils/addition_utils.py` and is judged again, so
    the second pass can only add. A refused frame is never in it: no frame is repaired. None when the second pass was not
    accepted: a block stops phase 3."""

    # STEP 1: the batch as it stands on disk, with the frames whose records were accepted
    call_slug = f"{group_slug}-{SECOND_PASS_SUFFIX}"
    covered_slugs = [item["slug"] for item in covered]
    handles = performance_log_service.start_unit(
        ctx, PHASE, call_slug, covered_slugs, label=performance_log_service.render_batch_follow_up_header(ctx, covered_slugs, "fix from the critique of"))
    batch = _describe_batch(ctx, covered, summary, atmosphere)

    # STEP 2: up to `budgets.maxAttempts` attempts, handed the rules the critique reported. A repair whose last reply
    # fails its checks is a block, and nothing after a block runs
    batch = {**batch, "critiqueRules": list(rules)}
    job = JobRetryContext(phase=PHASE, job_kind=SECOND_PASS_KIND,
                          snapshot_targets=_snapshot_targets(ctx, call_slug),
                          inputs={"slug": group_slug, "batch": batch})
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=call_slug,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    if not outcome.is_accepted:
        performance_log_service.record_unit(ctx, handles, BATCH_LEVEL, "second pass blocked", covered_slugs)

        return None

    # STEP 3: each addition applied to its own record, guarded, judged again, and kept only if it still passes
    second_pass_record = record_file_service.read_second_pass_record(ctx.paths, call_slug)
    added_to, dropped = _apply_additions(ctx, covered, second_pass_record.get("additions") or [], call_slug, rules)
    status = "nothing to change" if not added_to else f"{len(added_to)} of {len(covered)} frames gained notes"
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL,
                                        f"{status}, {dropped} dropped" if dropped else status, covered_slugs)

    # STEP 4: the account and the mood, when the second pass wrote better ones and they pass their own checks
    summary, atmosphere = _keep_better_account(ctx, group_slug, covered, second_pass_record, summary, atmosphere)

    return summary, atmosphere


def _list_edited_out_quotes(before: dict,
                            after: dict) -> list[str]:
    """The texts the lines of the outline `before` quote that the outline `after` no longer holds: what the fix's edits
    replaced or removed, which it judged wrong on the frame. Read line by line: a code line's own quote marks pair
    with the next line's across a whole outline."""

    kept = set(str(after.get("screen") or "").split("\n"))
    # The worker's own lines alone: the program's reader lines are its reading, and a fix that wrote the outline again
    # whole left them out unread; counted, the frame's real lines were kept out and the right outline was refused
    # (Lecture 7 frame 110, DEFECTS 571)
    written = frame_markdown_utils.strip_program_reading(str(before.get("screen") or ""))
    gone = [line for line in written.split("\n") if line.strip() and line not in kept]

    return list(dict.fromkeys(quote for line in gone for quote in frame_markdown_utils.list_screen_quotes(line)))


def _apply_additions(ctx: RunContext,
                     covered: list[dict],
                     additions: list[dict],
                     call_slug: str,
                     rules: list[dict]) -> tuple[list[int], int]:
    """Each addition onto its own accepted record: the frames added to, and how many additions were dropped."""

    items_by_index = {int(item["index"]): item for item in covered}
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    added_to: list[int] = []
    dropped = 0
    # The second pass passed its check, which refuses an addition whose `frame` is not a frame number of this batch
    for addition in sorted(additions, key=lambda each: each["frame"]):
        index = addition["frame"]
        accepted = records.get(index)
        if index not in items_by_index or accepted is None:
            ctx.log.warning(f"  frame {index}: addition dropped, it is not a written frame of this batch")
            dropped += 1
            continue
        record = {name: value for name, value in accepted.items() if name not in frame_records_service.RECORD_CONTEXT_KEYS}
        changed, changes, refusals = addition_utils.apply_addition(
            record, addition, addition_utils.list_critiqued_fields(rules, index, str(record.get("screen") or "")))
        if refusals:
            ctx.log.warning(f"  {addition_utils.describe_refusals(index, refusals)}")
        if not changes:
            continue
        item = items_by_index[index]
        # An outline the fix replaced was written without the program's part, which the fix was not shown: the reader's
        # lines it leaves out are added again, as for every frame's notes written. The reader returns no lines when it
        # cannot read the frame, so the outline stays as the fix wrote it, for the check below to judge
        # ... except a line the fix named not on the frame, or whose quoted text it replaced or removed: the reader's
        # "Read 434 lines", outlined as read, was corrected to the frame's "Read 131 lines" and added back beside it
        # (Lecture 7 frame 95, DEFECTS 566)
        changed = item_validator.add_unquoted_screen_lines(ctx.paths, item["slug"], changed,
                                                           [*(str(part) for part in addition.get("wrong_parts") or []),
                                                            *_list_edited_out_quotes(record, changed)])
        if _keep_addition_if_it_passes(ctx, item, accepted, record, changed, call_slug):
            added_to.append(index)
        else:
            dropped += 1

    return added_to, dropped


def _keep_addition_if_it_passes(ctx: RunContext,
                                item: dict,
                                accepted: dict,
                                record: dict,
                                changed: dict,
                                call_slug: str) -> bool:
    """The record with its additions written and judged again; whether it passed. When it did not, the accepted
    record goes back, byte for byte, since the one with the additions is worse than the one that passed."""

    record_file_service.write_frame_record(ctx.paths, item["slug"], changed)
    report = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, RECORD_KIND, inputs={"slug": item["slug"]}))
    # Facts the addition left as they were are no fault of it: a repair added two lines a terminal showed to a frame with
    # no facts, the fuller outline asked for facts, and the lines were dropped with it (Lecture 7 frame 30, DEFECTS 555)
    is_facts_left_as_they_were = ({finding.code for finding in report.findings} == {"FRAME_FACTS_MISSING"}
                                  and changed.get("facts") == record.get("facts"))
    if not report.ok and not is_facts_left_as_they_were:
        record_file_service.write_frame_record(ctx.paths, item["slug"], record)
        ctx.log.warning(f"  frame {item['index']}: addition dropped, it would not pass: "
                        f"{finding_text_utils.render_for_console(report.findings)}")

        return False
    frame_records_service.save_frame_record(ctx.paths, item, changed, list(accepted.get("builtFrom") or []))
    ctx.store.set_item_status(item["slug"], "written", additionsFrom=call_slug)

    return True


def _keep_better_account(ctx: RunContext,
                         group_slug: str,
                         covered: list[dict],
                         second_pass_record: dict,
                         summary: str,
                         atmosphere: str) -> tuple[str, str]:
    """Keep the account and the mood the second pass wrote when they pass the same checks, else keep the accepted
    pair (R19)."""

    proposed_summary = str(second_pass_record.get("batch_summary") or "").strip()
    proposed_atmosphere = str(second_pass_record.get("atmosphere") or "").strip()
    if not proposed_summary and not proposed_atmosphere:
        return summary, atmosphere
    summary_slug = batch_summaries_service.build_summary_slug(group_slug)
    title = _read_batch_title(ctx, group_slug, summary)
    record_file_service.write_batch_record(ctx.paths, summary_slug,
                                           {"batch_title": str(second_pass_record.get("batch_title") or "").strip() or title,
                                            "batch_summary": proposed_summary or summary,
                                            "atmosphere": proposed_atmosphere or atmosphere})
    # Through the guard, never the validator directly: an exception here would kill the run over a rewrite of the
    # account that the batch can do without (the accepted account is kept).
    report = _judge_summary(ctx, group_slug, covered)
    if report.ok:
        ctx.log.info("  the batch's account was rewritten by the second pass")

        return proposed_summary or summary, proposed_atmosphere or atmosphere
    record_file_service.write_batch_record(ctx.paths, summary_slug,
                                           {"batch_title": title, "batch_summary": summary, "atmosphere": atmosphere})
    ctx.log.warning(f"  the second pass's account was dropped: {finding_text_utils.render_for_console(report.findings)}")

    return summary, atmosphere


def _list_figure_slugs(ctx: RunContext) -> set[str]:
    """The frames phase 2 cut as a PDF's figures (`medium` `pdf-figure` in `02_frames.json`); none for any other run."""

    if not phase_file_service.is_pdf_run(ctx.paths):
        return set()
    items = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"])["items"]

    return {item["slug"] for item in items if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM}


def _run_critique_and_second_pass(ctx: RunContext,
                                  group_slug: str,
                                  covered: list[dict],
                                  summary: str,
                                  atmosphere: str) -> BatchFollowUpResult:
    """R19, each call behind its own switch (the owner): the critique, then the second pass from its rules. The account and
    the mood after them; the slug of the call that did not pass, when one did not: a block stops phase 3."""

    frames_settings = app_config.settings.frames_processing
    # A batch of figures alone is criticised whatever the PDF switch says: with no check a chart's misread curve reached
    # the document (DEFECTS 489)
    is_figure_batch = bool(covered) and _list_figure_slugs(ctx) >= {item["slug"] for item in covered}
    is_criticised_figure_batch = is_figure_batch and frames_settings.is_pdf_figure_critique_on
    if (frames_settings.is_critique_on and not frames_settings.is_pdf_critique_on and not is_criticised_figure_batch
            and phase_file_service.is_pdf_run(ctx.paths)):
        ctx.log.info(f"  {group_slug}: no critique and no repair, framesProcessing.isPdfCritiqueOn is off for a PDF")

        return BatchFollowUpResult(summary=summary, atmosphere=atmosphere)
    if not frames_settings.is_critique_on:
        if frames_settings.is_critique_repair_on:
            # The repair has nothing to repair FROM: a run line that turned the whole critique off should not have to
            # turn its repair off as well (the owner).
            ctx.log.info(f"  {group_slug}: no repair, framesProcessing.isCritiqueOn is off and there is nothing to "
                         f"repair from")

        return BatchFollowUpResult(summary=summary, atmosphere=atmosphere)
    rules = _request_critique(ctx, group_slug, covered, summary, atmosphere)
    if rules is None:
        return BatchFollowUpResult(blocked_call_slug=f"{group_slug}-{CRITIQUE_SUFFIX}")
    if not frames_settings.is_critique_repair_on:
        return BatchFollowUpResult(summary=summary, atmosphere=atmosphere)
    repaired = _request_second_pass(ctx, group_slug, covered, summary, atmosphere, rules)
    if repaired is None:
        return BatchFollowUpResult(blocked_call_slug=f"{group_slug}-{SECOND_PASS_SUFFIX}")
    repaired_summary, repaired_atmosphere = repaired

    return BatchFollowUpResult(summary=repaired_summary, atmosphere=repaired_atmosphere)


def _repair_failed_frames(ctx: RunContext,
                          items: list[dict],
                          failed: list[tuple[dict, ValidationReport]],
                          repair: str,
                          finish_reason: str,
                          summary_reason: str) -> FrameRepairResult:
    """The frames of a batch its call did not finish, repaired as `repair` says: together in rounds, handed the call's
    finish reason and why the account failed; each alone; or, under zero repair, blocked."""

    if repair == REPAIR_TOGETHER:
        return _repair_group(ctx, items, failed, finish_reason, summary_reason)
    if repair == REPAIR_ALONE:
        return _process_single_frames(ctx, failed)

    # Zero repair (the owner): a refused frame is never asked again, in no call, the second pass included, and never
    # accepted with a warning: it is blocked, and phase 3 stops
    repaired = FrameRepairResult()
    for item, report in failed:
        reason = "its notes were refused; no frame is repaired or asked again"
        repaired.blocked += _mark_refused_frame_blocked_and_clear_notes(ctx, item, report, reason)["blocked"]

    return repaired


def _finish_batch(ctx: RunContext,
                  items: list[dict],
                  written: list[str],
                  summary: str,
                  atmosphere: str,
                  group_slug: str = "") -> dict:
    """A batch whose frames were all accepted or repaired, with its account: its critique and repair, its open questions
    and their answers, then its files. Any follow-up not accepted blocks every frame of the batch. `group_slug` names
    the batch call whose account it carries, when its frames are not that call's whole range."""

    group_slug = group_slug or _build_group_slug(items)

    # STEP 1: R19, in two calls, each behind its own switch (the owner): the critique REPORTS what went wrong, as
    # rules, into an artifact; then the second pass fixes the notes from those rules, and may only add to them. Nothing
    # after a block runs. Both off, the notes stand as the first call wrote them.
    if written:
        reviewed = _run_critique_and_second_pass(ctx, group_slug, items, summary, atmosphere)
        if reviewed.blocked_call_slug:
            return {"written": [], "blocked": _mark_batch_blocked_and_clear_notes(ctx, written, reviewed.blocked_call_slug)}
        summary, atmosphere = reviewed.summary, reviewed.atmosphere

    # STEP 2: what the batch leaves open, then the answers found for it, from the notes as the second pass left them
    open_questions, answered_questions = [], []
    # Every frame of the batch is written by now: one not written was blocked, and a block ends the batch before this
    if written:
        asked = _request_open_questions_and_answers(ctx, group_slug, items, summary, atmosphere)
        if asked.blocked_call_slug:
            return {"written": [], "blocked": _mark_batch_blocked_and_clear_notes(ctx, written, asked.blocked_call_slug)}
        open_questions, answered_questions = asked.open_questions, asked.answered_questions

    # STEP 3: the batch, as it stands after every look, joins the batches, and the batch becomes one file
    entry = _save_batch(ctx, group_slug, items, summary, BATCH_SOURCE, atmosphere, open_questions, answered_questions)
    if written:
        _write_final_files(ctx, group_slug, items, written, entry)

    return {"written": written, "blocked": []}


def _process_frame_batch(ctx: RunContext,
                         items: list[dict]) -> dict:
    """Consecutive frames in one call with one attempt; the frames whose record did not pass are repaired as
    `_choose_repair` says (together in rounds, each alone, or blocked), and a batch cut at the output limit goes back in
    halves."""

    # STEP 1: no record a previous run left may pass for this call's reply
    group_slug = _build_group_slug(items)
    handles = performance_log_service.start_unit(ctx, PHASE, group_slug, [item["slug"] for item in items])
    for item in items:
        record_file_service.remove_record(ctx.paths, item["slug"])
    summary_slug = batch_summaries_service.build_summary_slug(group_slug)
    record_file_service.remove_record(ctx.paths, summary_slug)
    inputs = _build_group_inputs(ctx, items)
    job = JobRetryContext(phase=PHASE, job_kind=GROUP_KIND,
                          snapshot_targets=[*(record_file_service.build_record_path(ctx.paths, item["slug"])
                                              for item in items),
                                            record_file_service.build_record_path(ctx.paths, summary_slug),
                                            call_log_service.build_call_log_path(ctx.paths, group_slug)],
                          inputs=inputs)
    chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=group_slug, max_attempts=1)

    # STEP 2: judge each frame's record in the reply, and log the call as the batch it was, before any re-ask
    reports = _judge_frame_records(ctx, items, group_slug)
    failed = [(item, report) for item, report in zip(items, reports) if not report.ok]
    _log_notes(ctx, list(zip(items, reports)))
    _log_rejections(ctx, failed)
    summary_report = _judge_summary(ctx, group_slug, items)
    _record_judged_findings(job, [*reports, summary_report])
    summary, atmosphere = _read_batch_account(ctx, group_slug)
    summary_reason = "" if summary_report.ok else finding_text_utils.render_for_model(summary_report.findings)
    repair = _choose_repair()
    # No result when the call never ran (its budget spent on an earlier life)
    finish_reason = job.handles.result.finish_reason if job.handles.result is not None else ""
    is_sent_back_in_halves = (finish_reason_utils.is_finish_reason_cut_at_output_limit(finish_reason)
                              and len(failed) > 1 and repair != REPAIR_NONE)
    retried = _render_batch_outcome([item for item, _ in failed], repair, is_sent_back_in_halves)
    performance_log_service.record_unit(ctx, handles, BATCH_LEVEL, f"{len(items) - len(failed)} of {len(items)} accepted{retried}",
                                        [item["slug"] for item in items])

    # STEP 3: every frame whose record passed is accepted, in order, each built from the ones before it
    written = _write_accepted_frames(ctx, items, reports, list(inputs["builtFrom"]), 1, group_slug)
    blocked = []

    # STEP 4: the others are repaired as the settings allow: together in rounds, told what this call got right, why each
    # failed and why the account failed when it did; each alone; or blocked. A cut batch goes back in halves
    if failed:
        if is_sent_back_in_halves:
            return _process_halves(ctx, items)
        repaired = _repair_failed_frames(ctx, items, failed, repair, finish_reason, summary_reason)
        written, blocked = written + repaired.written, repaired.blocked
        if summary_reason and repaired.summary:
            summary, atmosphere, summary_reason = repaired.summary, repaired.atmosphere, ""

    # A blocked frame stops phase 3 here: nothing after a block runs, not even this batch's follow-ups (the owner:
    # "there are no continue after block")
    if blocked:
        return {"written": written, "blocked": blocked}

    # STEP 5: an account still missing is asked alone, text only, with `budgets.maxAttempts` attempts like any other
    # step. The batch's account is no frame, and nothing of a frame's notes is touched by asking for it (2026-10-06:
    # ten accepted frames were blocked because the reply that wrote them all forgot the account at its end). Does that
    # call fail too, the batch blocks on it and nothing after the block runs.
    if summary_reason:
        summary, atmosphere = _request_summary_alone(ctx, items, summary_reason)
    if summary_reason and not summary:
        ctx.log.warning(f"  {group_slug}: the batch's account was refused, and asking for it alone did not pass "
                        f"either: {summary_reason}")

        return {"written": [], "blocked": _mark_batch_blocked_and_clear_notes(ctx, written, batch_summaries_service.build_summary_slug(group_slug))}

    # STEP 6: the batch's follow-ups, then its files (`_finish_batch`)
    return _finish_batch(ctx, items, written, summary, atmosphere)


def _read_stand_in_description(record: dict | None) -> str:
    """The description an accepted frame record stands in with, empty when it has none."""

    return str((record or {}).get("third_person_description") or "").strip()


def _save_stand_in_batch(ctx: RunContext,
                         item: dict) -> None:
    """R15: a unit of one frame has no batch account; its accepted description stands in, so the batches have no
    holes. Nothing when the frame was not accepted."""

    records = _read_accepted_records(ctx, {item["slug"]})
    description = _read_stand_in_description(records[0] if records else None)
    if not description:
        return
    _save_batch(ctx, item["slug"], [item], description, STAND_IN_SOURCE)


def _list_unaccounted_runs(items: list[dict],
                           covered: set[int],
                           records: dict[int, dict]) -> list[list[dict]]:
    """Each run, in order, of consecutive frames of `items` that no batch account covers and whose accepted notes hold a
    description to stand in with."""

    runs: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        index = int(item["index"])
        if index not in covered and _read_stand_in_description(records.get(index)):
            current.append(item)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    return runs


def _cover_unaccounted_frames(ctx: RunContext) -> None:
    """R15: every accepted frame no batch account covers gets one, so the batches have no holes: each run of such frames
    in a row stands in with their accepted descriptions, as a unit of one frame does. A batch that blocked after its
    frames were accepted, and frames asked again by `--worker` or alone, left their frames with none, and phase 4's agenda
    is built from the accounts (2026-10-05: frames 31 to 85 of one run had none)."""

    covered = {index for batch in batch_summaries_service.read_batch_summaries(ctx.paths) for index in batch["frames"]}
    records = {int(record["index"]): record for record in frame_records_service.read_frame_records(ctx.paths)}
    items = sorted(ctx.store.list_items().values(), key=lambda item: int(item["index"]))
    for run in _list_unaccounted_runs(items, covered, records):
        summary = " ".join(_read_stand_in_description(records[int(each["index"])]) for each in run)
        slug = f"{_build_group_slug(run)}-{STAND_IN_SUFFIX}"
        _save_batch(ctx, slug, run, summary, STAND_IN_SOURCE)


_GROUP_SLUG = re.compile(r"^frames-(\d+)-(\d+)")


def _list_account_ranges(ctx: RunContext) -> list[tuple[int, int, str]]:
    """Each batch call that left an account on disk, as (its first frame, its last frame, its group slug), in order."""

    folder = record_file_service.build_record_path(ctx.paths, _build_group_slug([{"index": 0}])).parent
    ranges: dict[str, tuple[int, int, str]] = {}
    for path in folder.glob(f"frames-*-{batch_summaries_service.SUMMARY_SUFFIX}.md"):
        match = _GROUP_SLUG.match(path.stem)
        if match:
            ranges[match.group(0)] = (int(match.group(1)), int(match.group(2)), match.group(0))

    return sorted(ranges.values())


def list_unfinished_batches(ctx: RunContext) -> list[tuple[str, list[dict]]]:
    """The accepted frames no batch account covers, grouped by the batch call whose account covers them last, as
    (that call's group slug, its frames). A batch that blocked on one of its frames under zero repair, or a run stopped
    between a batch's answer and its follow-ups, leaves its accepted frames so: a resume asks only the frames with no
    notes, and froscon talk 3211's frames 29 to 54 and 57 to 71 reached phase 4 with no critique, no second pass and no
    open questions (DEFECTS 285)."""

    covered = {index for batch in batch_summaries_service.read_batch_summaries(ctx.paths) for index in batch["frames"]}
    accepted = {record["slug"] for record in frame_records_service.read_frame_records(ctx.paths)}
    ranges = _list_account_ranges(ctx)
    groups: dict[str, list[dict]] = {}
    for item in sorted(ctx.store.list_items().values(), key=lambda item: int(item["index"])):
        index = int(item["index"])
        if item.get("status") != "written" or index in covered or item["slug"] not in accepted:
            continue
        holders = [slug for first, last, slug in ranges if first <= index <= last]
        groups.setdefault(holders[-1] if holders else _build_group_slug([item]), []).append(item)

    return list(groups.items())


def _is_whole_range(ctx: RunContext,
                    group_slug: str,
                    items: list[dict]) -> bool:
    """Whether `items` are every frame kept in the range of the batch call `group_slug` names: its account tells of
    that range, and handed to two of its frames whose later ones a later call answered, it told the batch of frames it
    no longer holds (froscon 3211's frames 55 and 56 with the account of 55 to 66, DEFECTS 289)."""

    match = _GROUP_SLUG.match(group_slug)
    if not match:
        return False
    first, last = int(match.group(1)), int(match.group(2))
    in_range = {int(item["index"]) for item in ctx.store.list_items().values()
                if first <= int(item["index"]) <= last and item.get("status") != "skipped"}

    return in_range == {int(item["index"]) for item in items}


def finish_unfinished_batches(ctx: RunContext) -> list[str]:
    """Each batch `list_unfinished_batches` names given what every batch gets after its answer (the critique, the
    second pass, the open questions and their answers), with the account its call left when the batch holds every frame
    of that call's range, or else its frames' descriptions. The frames blocked, when a follow-up blocks: phase 3 stops there."""

    for group_slug, items in list_unfinished_batches(ctx):
        is_whole = _is_whole_range(ctx, group_slug, items)
        summary, atmosphere = _read_batch_account(ctx, group_slug) if is_whole else ("", "")
        if not summary:
            records = {record["slug"]: record for record in _read_accepted_records(ctx, {item["slug"] for item in items})}
            summary = " ".join(_read_stand_in_description(records.get(item["slug"])) for item in items).strip()
        ctx.log.info(f"  {group_slug}: {_render_frame_numbers(items)} accepted with no follow-up; the batch is finished now")
        # Part of a call's range has follow-ups of its own: under the call's slug they shared the attempts the call's
        # own follow-ups had spent, and the critique of 38c3-797's frames 51 to 60 blocked with no call (DEFECTS 541)
        outcome = _finish_batch(ctx, items, [item["slug"] for item in items], summary, atmosphere,
                                group_slug if is_whole else _build_group_slug(items))
        if outcome["blocked"]:
            return list(outcome["blocked"])

    return []


def _write_meeting_notes(ctx: RunContext) -> None:
    integration = integration_service.build_integration(ctx.paths)
    recording_name = (ctx.paths.repository / ctx.paths.module).name
    document_file_service.write_meeting_notes(
        ctx.paths, meeting_notes_utils.render_meeting_notes(recording_name, integration))


def _run_fast_session(ctx: RunContext,
                      state: RunState,
                      queue: list[str],
                      cursor: int) -> dict:
    """One session of a fast-processing run's phase 3 (`fast_frame_note_service`): the next `frameFollowUps` frames
    described, each written. The session that empties the queue ends the phase itself, so a fast run never reaches
    `end_phase_on_empty_queue` (no meeting notes, no stand-in accounts). A dry run advances without a call."""

    count = app_config.settings.video.fast_processing.frame_follow_ups
    slugs = queue[cursor:cursor + count]
    if state.get("is_dry_run"):
        ctx.log.info(f"  [dry-run] a fast-processing session would describe {len(slugs)} frame(s)")

        return {"cursor": cursor + len(slugs)}
    written = fast_frame_note_service.describe_session(ctx, [ctx.store.get_item(slug) for slug in slugs])
    update = {"cursor": cursor + len(slugs), "written": written, "blocked": []}
    if update["cursor"] >= len(queue):
        ctx.store.set_phase(PHASE, "complete")
        performance_log_service.record_phase(ctx, PHASE, "complete", None)

    return update


def stop_phase_on_block(ctx: RunContext,
                         blocked_slugs: list[str]) -> None:
    """Phase 3 is recorded blocked, in the performance log and in the run's own record, which a "complete" from an
    earlier pass would otherwise let a resume skip."""

    frames = _render_frame_numbers([ctx.store.get_item(blocked_slug) for blocked_slug in blocked_slugs])
    ctx.log.warning(f"phase {PHASE_NUMBER} stops: {frames} blocked, and every later phase is built on the notes. "
                    f"Fix the cause, then `--resume`.")
    performance_log_service.record_phase(ctx, PHASE, "blocked", None)
    ctx.store.set_phase(PHASE, "blocked")


def end_phase_on_empty_queue(ctx: RunContext) -> list[str]:
    """The node that empties the queue ends the phase and writes its performance line, after the batches left with no
    follow-up are finished; the frames blocked when one of those blocks."""

    # A queue of the frames `--worker` named ends with the others still unread: that is no phase 3 complete. A run
    # of five named frames recorded it complete with seven never read, and a plain resume began phase 4 without them
    # (a phase 3 an operator skipped with `--from 4` is still theirs to leave incomplete)
    counts = ctx.store.count_by_status()
    status = ("blocked" if counts.get("blocked") else
              "incomplete" if ctx.selected_slugs and counts.get("pending") else "complete")
    if status == "complete":
        blocked = finish_unfinished_batches(ctx)
        if blocked:
            stop_phase_on_block(ctx, blocked)

            return blocked
        _cover_unaccounted_frames(ctx)
        # A "blocked" a block wrote is taken back once no frame is blocked: left standing, it held phase 5 after the
        # frames named again were written
        ctx.store.set_phase(PHASE, "complete")
    _write_meeting_notes(ctx)
    performance_log_service.record_phase(ctx, PHASE, status, None)

    return []


def process_frames_node(state: RunState,
                        config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]

    # STEP 1: take the frame at the cursor, or do nothing if the queue is empty
    cursor = state.get("cursor", 0)
    queue = state.get("queue", [])
    # Both edges into this node are guarded by an emptiness check (routing.py). This one repeats it, for a direct call.
    if cursor >= len(queue):
        return {}
    slug = queue[cursor]
    item = ctx.store.get_item(slug)

    # STEP 2: honour an operator signal. Between frames is the one point where that is safe.
    # A stop sent to a paused run reaches here once the pause is left: it ends the phase before the next batch, as drain does
    signal = ctx.control_check() if ctx.control_check else None
    if signal in ("drain", "stop"):
        ctx.log.info(f"  draining: {len(queue) - cursor} frame(s) still pending, each keeping its full retry budget")
        performance_log_service.record_phase(ctx, PHASE, "drained", None)

        return {"is_drained": True}

    # STEP 3: a pass over every frame starts the records file empty, so it never mixes this run's
    # frames with frames an earlier run produced under another prompt or another model
    is_fast = video_workflow_service.is_fast_processing(ctx.paths.output_root)
    if not state.get("is_dry_run") and _is_fresh_pass(ctx, cursor):
        frame_records_service.clear_all_frame_records(ctx.paths)
        batch_summaries_service.reset_batch_summaries(ctx.paths)
        if is_fast:
            fast_frame_note_service.start_descriptions(ctx.paths)

    # STEP 3b: a fast-processing run describes its frames a session at a time, with no check
    if is_fast:
        return _run_fast_session(ctx, state, queue, cursor)

    # STEP 4: the frames this call takes: this one, and with several frames per call the ones after it
    group = _list_group(ctx, queue, cursor, app_config.settings.resolve_frames_per_call())

    # STEP 5: on a dry run, render what the model WOULD be sent, and advance without calling it
    if state.get("is_dry_run"):
        if len(group) > 1:
            kind, inputs = GROUP_KIND, _build_group_inputs(ctx, group)
        else:
            kind, inputs = FRAMES_PROCESSING_KIND, _build_inputs(ctx, slug, item, [item])
        messages = chain_dispatch_service.build_messages(
            attempt_request_builder.build_standalone_request(ctx, kind, inputs=inputs))
        system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
        dry_run_service.describe_dry_run(ctx, PHASE, inputs["slug"], system_text, human_text)

        return {"cursor": cursor + len(group)}

    # STEP 6: several frames go in one call, the ones whose record did not pass asked again together; one frame is
    # asked again until its record validates or the budget is spent. The decision is recorded: written and in the
    # records file, or blocked and reported.
    # A PDF's figure alone is still a batch, so it gets its account and its critique and repair: asked alone, a PDF of
    # one figure had none, and a figure's misread stood (DEFECTS 489)
    is_lone_figure = (len(group) == 1 and app_config.settings.frames_processing.is_pdf_figure_critique_on
                      and group[0]["slug"] in _list_figure_slugs(ctx))
    is_one_frame = len(group) == 1 and not is_lone_figure
    # A unit of one frame (one frame per call, or the last frame left) is an ordinary frame: `budgets.maxAttempts`, never
    # `framesProcessing.singleFrameAttempts`, which is for frames a batch left unfinished
    outcome = (_process_alone(ctx, item, app_config.settings.budgets.max_attempts) if is_one_frame
               else _process_frame_batch(ctx, group))
    if is_one_frame:
        _save_stand_in_batch(ctx, item)
    update = {"cursor": cursor + len(group), "written": list(outcome.get("written", [])),
              "blocked": list(outcome.get("blocked", []))}

    # STEP 7: a blocked frame ends the phase and the run, whatever is left in the queue: the document, the frames
    # and the review are all built on the frames' notes, so none of them may run over a frame that has none.
    if update["blocked"]:
        stop_phase_on_block(ctx, update["blocked"])

        return {**update, "phases": {PHASE: "blocked"}}

    # STEP 8: the node that empties the queue ends the phase, and writes its performance line
    if update["cursor"] >= len(queue):
        blocked = end_phase_on_empty_queue(ctx)
        if blocked:
            return {**update, "blocked": blocked, "phases": {PHASE: "blocked"}}

    return update
