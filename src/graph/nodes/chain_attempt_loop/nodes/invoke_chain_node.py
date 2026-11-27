"""Render the messages, record exactly what was sent, make ONE model call, and put what came back on
disk: the call log always, the record only when a reply parsed.

Everything this node returns is a process fact. None of it decides whether the work was done.
"""

from __future__ import annotations

import dataclasses
import re

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

import config as app_config
from agent_client import finish_reason_utils
from graph.graph_state import AttemptState
from graph.nodes.retry_loop import attempt_request_builder
from models.attempt_call_spec import AttemptCallSpec
from models.chain_call_result import ChainCallResult
from models.chain_call_spec import NO_THINKING, ChainCallSpec
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from models import call_outcome_vocabulary
from models import output_contract
from prompts import compaction_prompt_loader
from prompts import frames_processing_messages
from services import call_log_service
from services import call_record_service
from services import chain_dispatch_service
from services import run_activity_service
from services import thinking_compaction_service
from services import worker_feedback_service
from services import conversation_message_service
from services import worker_message_service
from validators import item_validator
from utils import chain_response_utils
from utils import critique_markdown_utils
from utils import frame_markdown_utils

# The attempt's thinking so far, written while a markdown call streams (`chain_runner`)
LIVE_THINKING_FILE = "thinking-live.md"
# The compaction call's own thinking, written while it streams
LIVE_COMPACTION_FILE = "compaction-live.md"
# The reply written from a settled compaction, while it streams
LIVE_FINISH_FILE = "finish-live.md"
# That reply, kept when it does not parse: it thinks nothing, so its live file holds none of it
FINISH_REPLY_FILE = "finish-reply.md"
# The rest of a batch's answer that ended with parts missing, while it streams
LIVE_GO_ON_FILE = "go-on-live.md"
# The kinds whose answer goes on when it ended with parts missing: a batch of frames
GOES_ON = {"frame_batch"}
# Where each document of an answer starts, and a frame's document heading with its number
_DOCUMENT_START = re.compile(r"(?m)^(?=# )")
_FRAME_HEADING = re.compile(r"# Frame (\d+)\b")


def _read_window(ctx: RunContext,
                 phase: str) -> int | None:
    """The context window this phase's model is loaded with, so the unit's line can say how full a call made it. The
    run command chose it once, the loaded window else the configured one (`run_command.read_phase_windows`)."""

    return ctx.phase_windows.get(phase)


def _build_call_spec(ctx: RunContext,
                     job: JobRetryContext,
                     live_file: str,
                     reasoning_effort: str | None = None) -> ChainCallSpec:
    """A call of this job's phase and kind, sized to its model's window, streaming its thinking into `live_file` under
    the attempt's log directory."""

    return ChainCallSpec(job.phase, job.job_kind, _read_window(ctx, job.phase),
                         job.handles.log.directory / live_file, reasoning_effort)


def _describe_attempt(job: JobRetryContext,
                      state: AttemptState) -> dict:
    """The three keys that join one attempt's rows in the call metrics, the call log and the invocation file."""

    return {"phase": job.phase, "slug": state.get("slug"), "attempt": state["attempt"]}


def _record_call(ctx: RunContext,
                 job: JobRetryContext,
                 state: AttemptState,
                 call: AttemptCallSpec,
                 result: ChainCallResult) -> None:
    """One call on disk: its call log with the messages it was sent, the record when a reply parsed, and the job's
    handle on it. Its row in the run's call record is written by `chain_runner` as the call returns."""

    system_text, human_text = chain_response_utils.render_prompt_as_text(call.messages)

    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, call.name), {
        **_describe_attempt(job, state), "model": app_config.settings.resolve_phase_model_name(job.phase),
        "request": {"system": system_text, "human": human_text},
        "response": result.as_call_log()})
    if result.parsed is not None:
        chain_dispatch_service.write_record(call.request, result.parsed)
    job.handles.result = result


def _record_worker_feedback(ctx: RunContext,
                            reply_text: str,
                            name: str) -> None:
    """Each report a reply wrote under `# Feedback`, into the run's worker-feedback file and onto the console."""

    for report in frame_markdown_utils.read_feedback(reply_text):
        outcome = worker_feedback_service.record_report(ctx.paths.output_root / output_contract.WORKER_FEEDBACK_FILE,
                                                        kind=worker_feedback_service.resolve_kind_or_other(report["kind"]),
                                                        summary=report["summary"], detail=report["detail"], where=name)
        for line in outcome.console_lines:
            ctx.log.warning(line)


def _summarise_cut_thinking(ctx: RunContext,
                            job: JobRetryContext,
                            state: AttemptState,
                            name: str,
                            result: ChainCallResult) -> str:
    """The summary of what a cut thinking settled, written into the attempt's `compaction.md`, said on the console."""

    is_loop = result.finish_reason == finish_reason_utils.THINKING_LOOPED
    why = ("stopped: its thinking repeated the same passage" if is_loop
           else f"cut at the output limit after {result.output_tokens or '?'} tokens while thinking")
    ctx.log.warning(f"  {name} attempt {state['attempt']}: {why}; compacting its thinking for the next attempt …")
    compaction = thinking_compaction_service.summarise_cut_thinking(
        ctx.model, _build_call_spec(ctx, job, LIVE_COMPACTION_FILE), result.thinking)
    path = thinking_compaction_service.write_compaction(job.handles.log.directory, name, result.output_tokens, compaction,
                                                        is_loop)
    ctx.log.warning(f"  {name} attempt {state['attempt']}: compacted {compaction.thinking_chars:,} characters of "
                    f"thinking into {len(compaction.summary.split())} words in {compaction.duration_s:.0f} s ({path.name})")

    return compaction.summary


def _build_nothing_found() -> ChainCallResult:
    """The critique's own answer when it found no mistake, as a call that ended well, no model asked."""

    text = critique_markdown_utils.render_critique_document({"rules": []})
    parsed, _why = critique_markdown_utils.parse_critique(text)

    return ChainCallResult(outcome=call_outcome_vocabulary.CALL_COMPLETED, parsed=parsed, text=text,
                           finish_reason="stop")


def _keep_summary_rules(finished: ChainCallResult,
                        summary: str) -> ChainCallResult:
    """A critique written from its summary with only the rules seen on frames the summary's mistakes name, written
    again as the document it is; the program's "Nothing to criticise" when none is left."""

    kept = thinking_compaction_service.keep_summary_rules(finished.parsed, summary)
    if not kept.get("rules"):
        return _build_nothing_found()
    text = critique_markdown_utils.render_critique_document(kept)
    parsed, _why = critique_markdown_utils.parse_critique(text)

    return dataclasses.replace(finished, parsed=parsed, text=text)


def _write_reply_from_summary(ctx: RunContext,
                              job: JobRetryContext,
                              state: AttemptState,
                              call: AttemptCallSpec,
                              summary: str) -> None:
    """One more call, with no thinking, that writes the reply from a settled summary, or from any on the budget's last
    attempt: its record and call log when it parsed; its call metrics and `finish-reply.md` when not."""

    name = call.name
    log = job.handles.log
    finish = HumanMessage(content=compaction_prompt_loader.read_cut_thinking_finish().format(summary=summary))
    finish_messages = [*call.messages, finish]
    if thinking_compaction_service.is_nothing_found(job.job_kind, summary):
        # A critique whose thinking found no mistake is answered by the program: asked to write it, a call with no
        # thinking named mistakes the summary did not hold, on two runs out of two
        _record_call(ctx, job, state, AttemptCallSpec(name, call.request, finish_messages), _build_nothing_found())
        ctx.log.warning(f"  {name} attempt {state['attempt']}: its thinking had checked everything and found no "
                        f"mistake; the program wrote \"Nothing to criticise\"")
        return
    finished = chain_dispatch_service.invoke_chain(
        ctx.model, _build_call_spec(ctx, job, LIVE_FINISH_FILE, NO_THINKING), finish_messages)
    if finished.parsed is not None and job.job_kind == "critique":
        finished = _keep_summary_rules(finished, summary)
        mistakes = thinking_compaction_service.read_found_mistakes(summary)
        if finished.parsed.get("is_nothing_to_criticise") and mistakes:
            # Every rule of the first answer was on frames the summary does not name, so the mistake the thinking
            # found was answered "Nothing to criticise" (DEFECTS 437): once more, from the mistakes alone
            finish_messages = [*call.messages, HumanMessage(
                content=compaction_prompt_loader.read_cut_thinking_finish_mistakes().format(mistakes=mistakes))]
            again = chain_dispatch_service.invoke_chain(
                ctx.model, _build_call_spec(ctx, job, LIVE_FINISH_FILE, NO_THINKING), finish_messages)
            if again.parsed is not None:
                again = _keep_summary_rules(again, summary)
                finished = again if not again.parsed.get("is_nothing_to_criticise") else finished
        # Its rules given, a reply ran on through the instruction's list of non-mistakes until the loop check stopped
        # it, and was refused as looped (Lecture 7 frames 1 to 13, DEFECTS 552): the rules kept, all on the summary's
        # frames, are its answer
        if finished.finish_reason == finish_reason_utils.THINKING_LOOPED and finished.parsed.get("rules"):
            finished = dataclasses.replace(finished, finish_reason="stop")
            ctx.log.warning(f"  {name} attempt {state['attempt']}: the reply written from its summary ran on past its "
                            f"rules and was stopped; the {len(finished.parsed['rules'])} rule(s) on the summary's frames "
                            f"are kept")
    if finished.parsed is not None:
        # The call log shows the request this reply was sent, the summary's message in it
        _record_call(ctx, job, state, AttemptCallSpec(name, call.request, finish_messages), finished)
    else:
        # The cut call's log stays as the evidence of the cut
        (log.directory / FINISH_REPLY_FILE).write_text(finished.text, encoding="utf-8")
    ctx.log.warning(f"  {name} attempt {state['attempt']}: its thinking had checked everything; the reply "
                    + (f"was written from its summary in {finished.duration_s:.0f} s" if finished.parsed is not None
                       else f"written from its summary did not parse ({finished.parsing_error}; "
                            f"{FINISH_REPLY_FILE}), and the next attempt opens with the summary"))


def _list_frames_to_redo(request,
                         parsed: dict) -> set[int]:
    """The frames of `request` whose notes hold no fact, their heading left out or written empty, where the facts check
    would refuse them for it. Refused, each blocked its batch under zero repair; over the public runs 8 of 11 such
    refusals were right, the frame's text new and its facts written on the next attempt (DEFECTS 563)."""

    records = {record.get("frame"): record for record in parsed.get("frames") or [] if isinstance(record, dict)}

    return {int(item["index"]) for item in request.inputs.get("items") or []
            if int(item["index"]) in records and not records[int(item["index"])].get("facts")
            and item_validator.is_facts_asked(request.paths, item["slug"], records[int(item["index"])])}


def _list_frames_of_other_screens(request,
                                  parsed: dict) -> set[int]:
    """The frames of `request` whose outline quotes words their own reading almost never holds: another frame's screen
    outlined in their place (DEFECTS 569), each asked for again whole; never refused, which blocks under zero repair."""

    records = {record.get("frame"): record for record in parsed.get("frames") or [] if isinstance(record, dict)}

    return {int(item["index"]) for item in request.inputs.get("items") or [] if int(item["index"]) in records
            and item_validator.is_outline_of_another_screen(request.paths, item["slug"], records[int(item["index"])])}


def _describe_missing_parts(request,
                            parsed: dict) -> str:
    """What a batch's answer left out: frames it holds no document of (one it wrote unreadably is no missing one),
    frames whose facts the facts check would ask for, and, when its call asks for it, the account. Empty when nothing
    is missing."""

    written = {record.get("frame") for record in parsed.get("frames") or [] if isinstance(record, dict)}
    unreadable = {str(frame) for frame in (parsed.get("unreadable") or {})}
    asked = [int(item["index"]) for item in request.inputs.get("items") or []]
    left_out = [number for number in asked if number not in written and str(number) not in unreadable]
    lacking = sorted(_list_frames_to_redo(request=request, parsed=parsed))
    other_screens = sorted(_list_frames_of_other_screens(request=request, parsed=parsed) - set(lacking))
    parts = []
    if frames_processing_messages.is_account_asked(request) and not str(parsed.get("batch_summary") or "").strip():
        parts.append(frames_processing_messages.ANSWER_GO_ON_ACCOUNT)
    if left_out:
        parts.append(frames_processing_messages.ANSWER_GO_ON_FRAMES.format(NUMBERS=", ".join(map(str, left_out))))
    if lacking:
        parts.append(frames_processing_messages.ANSWER_GO_ON_FACTS.format(NUMBERS=", ".join(map(str, lacking))))
    if other_screens:
        parts.append(frames_processing_messages.ANSWER_GO_ON_SCREEN.format(NUMBERS=", ".join(map(str, other_screens))))

    return " and ".join(parts)


def _drop_frame_documents(text: str,
                          numbers: set[int]) -> str:
    """`text` without the documents of the frames `numbers`, which their continuation writes again whole."""

    kept = [document for document in _DOCUMENT_START.split(text)
            if not ((heading := _FRAME_HEADING.match(document)) and int(heading.group(1)) in numbers)]

    return "".join(kept)


def _keep_missing_documents(text: str,
                            parsed: dict,
                            redone: set[int]) -> str:
    """The documents of a continuation that the answer before it lacks: a frame it already wrote, unless `redone`
    (written again for the facts it left out), or a "# Batch" it already holds, is left out, so the two never write
    one part twice (refused as "written twice")."""

    written = {record.get("frame") for record in parsed.get("frames") or [] if isinstance(record, dict)} - redone
    has_account = bool(str(parsed.get("batch_summary") or "").strip())
    kept = []
    for document in _DOCUMENT_START.split(text):
        heading = _FRAME_HEADING.match(document)
        if heading and int(heading.group(1)) in written:
            continue
        if document.startswith("# Batch") and has_account:
            continue
        kept.append(document.strip())

    return "\n\n".join(document for document in kept if document)


def _finish_short_answer(ctx: RunContext,
                       job: JobRetryContext,
                       state: AttemptState,
                       call: AttemptCallSpec,
                       result: ChainCallResult) -> None:
    """A batch's answer that ended on its own with parts missing is asked, in this attempt, to go on: one more call
    with the answer as the model's own turn, and the two read as one answer. Asked again in a new attempt, the missing
    frame cost a refusal and a call (DEFECTS 318), the missing account the same (320)."""

    missing = _describe_missing_parts(request=call.request, parsed=result.parsed or {})
    if not missing:
        return
    redone = (_list_frames_to_redo(request=call.request, parsed=result.parsed or {})
              | _list_frames_of_other_screens(request=call.request, parsed=result.parsed or {}))
    go_on = HumanMessage(content=frames_processing_messages.ANSWER_GO_ON.format(MISSING=missing))
    go_on_messages = [*call.messages, AIMessage(content=result.text), go_on]
    more = chain_dispatch_service.invoke_chain(ctx.model, _build_call_spec(
        ctx, job, LIVE_GO_ON_FILE, chain_dispatch_service.select_reasoning_effort(job.job_kind)), go_on_messages)
    kept =_keep_missing_documents(text=more.text, parsed=result.parsed or {}, redone=redone)
    whole_text = f"{_drop_frame_documents(text=result.text, numbers=redone).rstrip()}\n\n{kept}"
    parsed, parsing_error = chain_dispatch_service.MARKDOWN_READERS[job.job_kind](whole_text)
    is_whole = parsed is not None and not _describe_missing_parts(request=call.request, parsed=parsed)
    ctx.log.warning(f"  {call.name} attempt {state['attempt']}: its answer ended without {missing}; asked to go on, "
                    + (f"it wrote them in {more.duration_s:.0f} s" if is_whole else "it still lacks a part"))
    if parsed is None:
        return
    whole = dataclasses.replace(result, text=whole_text, parsed=parsed, parsing_error=parsing_error,
                                duration_s=result.duration_s + more.duration_s)
    _record_call(ctx, job, state, AttemptCallSpec(call.name, call.request, [*go_on_messages, AIMessage(content=more.text)]),
                 whole)


def list_unanswered_rules(request,
                          parsed: dict) -> list[str]:
    """The names of the critique's rules a repair answering "Nothing to change" left unanswered: every rule it was
    handed, when it changed nothing; none otherwise."""

    if not parsed.get("is_nothing_to_change"):
        return []

    return [str(rule.get("name") or "").strip() for rule in (request.inputs.get("batch") or {}).get("critiqueRules") or []
            if isinstance(rule, dict) and str(rule.get("name") or "").strip()]


def _answer_named_rules(ctx: RunContext,
                        job: JobRetryContext,
                        state: AttemptState,
                        call: AttemptCallSpec,
                        result: ChainCallResult) -> None:
    """A repair that answered "Nothing to change" while its critique named rules is asked once more, in this attempt,
    to answer each: a repair planned in its thinking the edit a held rule asked for and wrote "Nothing to change", which
    was accepted with the mistake left (Lecture 7, DEFECTS 576). Its second answer stands, "Nothing to change" again
    included: whether a rule holds is the worker's to judge on the frame."""

    names = list_unanswered_rules(call.request, result.parsed or {})
    if not names:
        return
    go_on = HumanMessage(content=frames_processing_messages.REPAIR_GO_ON.format(
        COUNT=f"{len(names)} mistake{'s' if len(names) > 1 else ''}", NAMES="; ".join(f'"{name}"' for name in names)))
    go_on_messages = [*call.messages, AIMessage(content=result.text), go_on]
    more = chain_dispatch_service.invoke_chain(ctx.model, _build_call_spec(
        ctx, job, LIVE_GO_ON_FILE, chain_dispatch_service.select_reasoning_effort(job.job_kind)), go_on_messages)
    parsed, parsing_error = chain_dispatch_service.MARKDOWN_READERS[job.job_kind](more.text)
    ctx.log.warning(f"  {call.name} attempt {state['attempt']}: it changed nothing past {len(names)} critique rule(s); "
                    + ("asked again, " + ("it still changes nothing" if parsed and parsed.get("is_nothing_to_change")
                                          else f"it answered them in {more.duration_s:.0f} s")
                       if parsed is not None else f"asked again, its answer did not parse ({parsing_error})"))
    if parsed is None:
        return
    whole = dataclasses.replace(result, text=more.text, parsed=parsed, parsing_error=parsing_error,
                                duration_s=result.duration_s + more.duration_s)
    _record_call(ctx, job, state, AttemptCallSpec(call.name, call.request, [*go_on_messages, AIMessage(content=more.text)]),
                 whole)


def _finish_thinking_only(ctx: RunContext,
                          job: JobRetryContext,
                          state: AttemptState,
                          call: AttemptCallSpec,
                          result: ChainCallResult) -> None:
    """A batch's reply that ended on its own inside its thinking, no answer written, is asked in this attempt for the
    answer: one more call with the thinking as the model's own turn. Refused as thinking only, the batch was asked
    again from nothing (DEFECTS 337: 3,713 characters of thinking, a draft of its first frame, and a normal stop)."""

    go_on = HumanMessage(content=frames_processing_messages.ANSWER_AFTER_THINKING)
    go_on_messages = [*call.messages, AIMessage(content=result.thinking), go_on]
    more = chain_dispatch_service.invoke_chain(ctx.model, _build_call_spec(
        ctx, job, LIVE_GO_ON_FILE, chain_dispatch_service.select_reasoning_effort(job.job_kind)), go_on_messages)
    parsed, parsing_error = chain_dispatch_service.MARKDOWN_READERS[job.job_kind](more.text)
    ctx.log.warning(f"  {call.name} attempt {state['attempt']}: its reply ended inside its thinking; asked for the "
                    + (f"answer, it wrote it in {more.duration_s:.0f} s" if parsed is not None
                       else f"answer, it still wrote none that parses ({parsing_error})"))
    if parsed is None:
        return
    whole = dataclasses.replace(result, text=more.text, parsed=parsed, parsing_error=parsing_error,
                                duration_s=result.duration_s + more.duration_s)
    _record_call(ctx, job, state, AttemptCallSpec(call.name, call.request, [*go_on_messages, AIMessage(content=more.text)]),
                 whole)
    _finish_short_answer(ctx, job, state, call, whole)


def invoke_chain_node(state: AttemptState,
                      config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    job: JobRetryContext = config["configurable"]["job"]
    log = job.handles.log
    name = attempt_request_builder.describe_unit_name(job, state.get("slug"))

    # STEP 1: render this attempt's messages and record exactly what is about to be sent. Rendering reads each frame's
    # screen text, minutes for a batch, with no model call: the run's page says so meanwhile
    run_activity_service.write_activity(ctx.run_dir, f"preparing the request for {name}, no model call yet")
    request = attempt_request_builder.build_attempt_request(ctx, job, state)
    messages = chain_dispatch_service.build_messages(request)
    # what the person wrote to this worker from the page reaches this call; a call step has no conversation, so its reply
    # is still read in its set shape and a message is only taken into account
    conversation_message_service.add_waiting_to_conversation(
        worker_message_service.build_phase_messages_path(ctx.paths.output_root, job.phase), messages)
    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
    log.write_system_prompt(system_text)
    log.write_human_message(human_text)
    # The configured model's name, as the call log names it; a test's injected model by its class
    model_name = (type(ctx.model).__name__ if ctx.model is not None
                  else app_config.settings.resolve_phase_model_name(job.phase))
    log.write_invocation({"jobKind": job.job_kind, **_describe_attempt(job, state), "model": model_name})

    # STEP 2: make the one call this attempt is, and the follow-up calls it may need, each a row of this attempt in the
    # run's call record. The thinking is written beside the attempt's other files while it streams, for anyone
    # watching the call
    call_record_service.set_place(run_dir=ctx.run_dir,
                                  phase=job.phase,
                                  slug=state.get("slug"),
                                  attempt=state["attempt"],
                                  window_tokens=_read_window(ctx, job.phase))
    result = chain_dispatch_service.invoke_chain(ctx.model, _build_call_spec(
        ctx, job, LIVE_THINKING_FILE, chain_dispatch_service.select_reasoning_effort(job.job_kind)), messages)

    # STEP 3: put the whole call on disk BEFORE anything judges it, then the record if one parsed.
    # The call log is written whatever the call's outcome, a host error included (`invoke_chain` returns it as
    # `CALL_ERROR`): it is the only evidence of why.
    call = AttemptCallSpec(name, request, messages)
    _record_call(ctx, job, state, call, result)

    # STEP 4: a report the worker wrote under `# Feedback` goes where every worker's report goes, read or not
    if job.job_kind in chain_dispatch_service.MARKDOWN_READERS:
        _record_worker_feedback(ctx, result.text, name)

    is_cut = finish_reason_utils.is_finish_reason_cut_at_output_limit(result.finish_reason)

    # STEP 5: a phase 3 follow-up cut while still thinking is compacted, and the next attempt opens with what it settled
    carry_over = ""
    if thinking_compaction_service.is_compacted(job.job_kind, is_cut, result.thinking):
        carry_over = _summarise_cut_thinking(ctx, job, state, name, result)

        # STEP 6: a thinking that had checked everything is replied from its summary in this attempt, by one call
        # that thinks nothing itself; a reply that does not parse leaves the cut as it was, for the next attempt.
        # On the budget's last attempt no next one reads the summary, so it is replied from as it stands: under
        # zero repair (one attempt) a looped repair whose summary left something unchecked blocked its batch
        # (38c3-797 frames 50 to 63, DEFECTS 539)
        if thinking_compaction_service.is_settled(carry_over) or (carry_over and state.get("is_last")):
            _write_reply_from_summary(ctx, job, state, call, carry_over)

    # STEP 7: a batch's answer that ended on its own with a frame or its account left out goes on in this attempt
    if (job.job_kind in GOES_ON and result.parsed is not None and not is_cut
            and result.outcome == call_outcome_vocabulary.CALL_COMPLETED):
        _finish_short_answer(ctx, job, state, call, result)
    # ... and one that ended inside its thinking, with no answer, is asked for it
    elif (job.job_kind in GOES_ON and result.parsed is None and not is_cut and result.thinking.strip()
          and not result.text.strip() and result.outcome == call_outcome_vocabulary.CALL_COMPLETED):
        _finish_thinking_only(ctx, job, state, call, result)
    # ... and a repair that changed nothing past its critique's rules is asked to answer them
    elif (job.job_kind == "second_pass" and result.parsed is not None and not is_cut
          and result.outcome == call_outcome_vocabulary.CALL_COMPLETED):
        _answer_named_rules(ctx, job, state, call, result)

    return {"carry_over": carry_over}
