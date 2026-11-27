"""Several frames in one model call, the owner's request of 2026-09-26: `framesProcessing.framesPerCall`, at most 300 (the owner, 2026-10-02).

A batch is the frames in queue order. Each frame travels in its own message, its words and its frame together, so
an answer cannot drift to the wrong frame; the answer holds one record per frame, and the program writes each under
its own number. A batch gets one attempt: the frames whose record does not pass go back together in a repair call,
told what the batch got right and why each failed, and a frame that call does not finish either is asked ALONE. A
frame showing a screen again is a frame of its batch like any other, read again with its earlier showing's notes.
"""

import json
import logging
import logging.handlers
import tempfile
from pathlib import Path

from pydantic import ValidationError

from support import fakes
from support import frame_batch_fixtures
from support import fixtures as fx
from support import harness

import config as app_config
from graph import graph_state
from graph.nodes import process_frames_node
from models import output_contract
from models import validation_report
from models.attempt_request import AttemptRequest
from models.run_context import RunContext
from models.run_identity import RunIdentity
from prompts import frames_processing_messages
from services import batch_summaries_service
from services import phase_file_service
from services import call_log_service
from services import chain_dispatch_service
from services import frame_records_service
from services import record_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from schemas import frame_record
from validators import item_validator

# The fact of the second frame, beside frame_batch_fixtures.RECORD's own
SECOND_FACT = "Two thirds fear their bills"


def _read_accepted_notes(ctx: RunContext,
                         slug: str) -> dict:
    """A frame's accepted notes as `02_frames.json` keeps them: the batch's one file holds them too, once the batch is done."""

    return next((entry for entry in frame_records_service.read_frame_records(ctx.paths) if entry["slug"] == slug), {})


def test_the_earlier_frames_a_batch_is_handed_carry_their_facts_never_their_screen_outlines():
    """A frame's outline copied parts from the earlier frames' outlines it was handed (a console's lines, a box's value,
    a window bar the frame does not show), three times in one run. The earlier notes
    carry what continuity needs, their facts, and no outline to copy from; a screen shown again still gets its own."""

    record = {"index": 4, "timestamp": 12.0, "screen_kind": "slide", "facts": ["The gateway retries three times."],
              "screen": '- slide: white\n  - window bar "AcmeSync Editor": top', "builds_on": [], "open_questions": []}
    earlier = frames_processing_messages.render_previous_frames([record], is_screen_shown=False)
    harness.check("the facts travel", "The gateway retries three times." in earlier, earlier)
    harness.check("the outline does not", "AcmeSync Editor" not in earlier and "screen:" not in earlier, earlier)
    harness.check("a repeat's own earlier showing keeps its outline",
                  "AcmeSync Editor" in frames_processing_messages.render_previous_frames([record]))


def test_a_batch_is_handed_the_questions_the_batch_before_left_open():
    """A student's question left open by one batch was answered over the next batch's first frame, and that answer was
    read without its question and written as a wrong fact. The batch
    after is handed the questions still open, and only those of the batch right before it."""

    text = frames_processing_messages.render_open_questions_before(["Why does the run print 0 to 4 after 45?"])
    harness.check("the open question is in the message with what to do with it",
                  "Why does the run print 0 to 4 after 45?" in text and "answer" in text, text)
    harness.check("no open question, no section", frames_processing_messages.render_open_questions_before([]) == "")
    paths = fx.make_paths()
    for frames, questions in (([1, 2], ["Who owns the gateway?"]), ([3, 4], ["Why five retries?"]), ([5, 6], [])):
        batch_summaries_service.save_batch_summary(paths, {"slug": f"frames-{frames[0]:03d}-{frames[1]:03d}",
                                                           "frames": frames, "start": 0.0, "end": 1.0, "title": "t",
                                                           "summary": "s", "atmosphere": "a",
                                                           "openQuestions": questions, "answeredQuestions": []})
    harness.check("the batch right before frame 5 is frames 3-4",
                  process_frames_node._read_open_questions_before(paths, 5) == ["Why five retries?"],
                  str(process_frames_node._read_open_questions_before(paths, 5)))
    harness.check("before frame 7 the batch right before left none open",
                  process_frames_node._read_open_questions_before(paths, 7) == [])


def test_frames_per_call_is_one_by_default_and_at_most_three_hundred():
    harness.check("one frame per call unless asked", app_config.FramesProcessingSettings().frames_per_call == 1)
    harness.check("three hundred is allowed", app_config.FramesProcessingSettings(frames_per_call=300).frames_per_call == 300)
    for wrong in (0, 301):
        try:
            app_config.FramesProcessingSettings(frames_per_call=wrong)
        except ValidationError:
            harness.check(f"{wrong} frames per call is refused", True)
        else:
            harness.check(f"{wrong} frames per call is refused", False)
    harness.check("the camelCase key binds", app_config.FramesProcessingSettings(**{"framesPerCall": 4}).frames_per_call == 4)


def test_a_group_message_gives_each_frame_its_own_words_and_frame():
    ctx = frame_batch_fixtures.load_run_context()
    items = [ctx.store.get_item(slug) for slug in ("frame-001", "frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-002", "items": items, "previousRecords": [], "latestDiagram": None})
    messages = frames_processing_messages.build_frame_batch_messages(request)
    frame_messages = [message for message in messages[1:] if any(part.get("type") == "image_url"
                                                                 for part in message.content)]
    harness.check("one message per frame, each with exactly one frame",
                  len(frame_messages) == 2 and all(sum(part.get("type") == "image_url" for part in message.content) == 1
                                                   for message in frame_messages), str(len(frame_messages)))
    harness.check("each frame's words travel with its own frame",
                  "Frame 1" in frame_messages[0].content[0]["text"] and "words of frame 1" in frame_messages[0].content[0]["text"]
                  and "Frame 2" in frame_messages[1].content[0]["text"] and "words of frame 2" in frame_messages[1].content[0]["text"])
    closing = messages[-1].content[0]["text"]
    harness.check("the last message asks for one record per frame, in order, by number",
                  "1, 2" in closing, closing)


def test_the_group_answer_is_written_one_record_per_frame():
    paths = fx.make_paths()
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(),
                             inputs={"slug": "frames-000-001", "items": [{"slug": "frame-000", "index": 0},
                                                                         {"slug": "frame-001", "index": 1}]})
    record_file_service.write_frame_record(paths, "frame-001", {**frame_batch_fixtures.RECORD, "frame": 1, "facts": ["stale"]})
    chain_dispatch_service.write_record(request, {"frames": [{**frame_batch_fixtures.RECORD, "frame": 0}]})
    harness.check("the frame the answer holds is written under its own number",
                  record_file_service.read_frame_record(paths, "frame-000")["frame"] == 0)
    harness.check("a frame the answer left out has no record, not an older one",
                  not record_file_service.build_record_path(paths, "frame-001").is_file())
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frames-000-001"),
                                    {"response": {"outcome": "completed", "parsed": {}, "finishReason": "stop"}})
    report = item_validator.validate_frame_group(paths, "frames-000-001", ["frame-000", "frame-001"])
    harness.check("the group is judged frame by frame, and the missing one is named",
                  harness.codes(report) == {"FRAME_RECORD_MISSING"} and "frame-001" in report.render_hint(),
                  report.render_hint())
    harness.check("the frame that is there passes on its own", item_validator.validate_record(paths, "frame-000").ok)


def test_two_frames_in_one_call_are_both_written_in_order():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 0},
                                                {**frame_batch_fixtures.RECORD, "frame": 1, "facts": [SECOND_FACT]}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group), count=2)
    with fx.critique_switches():
        state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=4)
    harness.check("one call for both frames, then its critique, its repair and its open questions", ctx.model.call_count == 4,
                  f"calls={ctx.model.call_count}")
    harness.check("both written", state["written"] == ["frame-000", "frame-001"], str(state["written"]))
    records = frame_records_service.read_frame_records(ctx.paths)
    harness.check("both in the records file, each with its own facts",
                  [record["facts"][0] for record in records] == [frame_batch_fixtures.RECORD["facts"][0],
                                                                     *fakes.own_facts(1, [SECOND_FACT])], str(records))


def test_the_frames_a_group_failed_are_asked_again_together_told_what_it_got_right():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 2}])
    repair = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 1, "facts": [SECOND_FACT]},
                                                 {**frame_batch_fixtures.RECORD, "frame": 2, "facts": ["Prices rose twice"]}])
    # the attempt's continuation for the missing frame 1 writes nothing, so the repair call runs
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, fakes.says(""), repair), count=3)
    with fx.critique_switches():
        state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3)
    repair_messages = ctx.model.calls[2] if ctx.model.call_count > 2 else []
    context = repair_messages[1].content[0]["text"] if repair_messages else ""
    harness.check("the group once, its continuation, ONE call for both frames it failed, then the critique, the repair "
                  "and the open questions", ctx.model.call_count == 6,
                  f"calls={ctx.model.call_count}")
    harness.check("that call carries only the failed frames, one message each",
                  sum("captured at" in str(message.content) for message in repair_messages) == 2
                  and "Frame 0, captured" not in "".join(str(message.content) for message in repair_messages),
                  str([str(message.content)[:60] for message in repair_messages]))
    harness.check("all three are written, the repaired ones from the second call",
                  sorted(state["written"]) == ["frame-000", "frame-001", "frame-002"]
                  and _read_accepted_notes(ctx, "frame-001")["facts"] == fakes.own_facts(1, [SECOND_FACT]),
                  str(state["written"]))
    harness.check("it is told an earlier worker did not finish frames 1 and 2", "did not finish" in context
                  and "frames 1, 2" in context, context[:600])
    harness.check("it is handed the notes the earlier worker got right", frame_batch_fixtures.RECORD["facts"][0] in context,
                  context[:600])
    harness.check("and why each frame failed, in the validator's words",
                  "FRAME_RECORD_MISSING" in context and "frame 2:" in context, context[:900])


def test_a_group_cut_at_the_output_limit_goes_back_as_smaller_calls_not_the_same_repair_call():
    alone_0 = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 0})
    alone_1 = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 1})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(fakes.cut_off("checking every field again"), alone_0, alone_1), count=2)
    state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    retry_texts = ["".join(str(message.content) for message in call) for call in ctx.model.calls[1:3]]
    harness.check("the two frames are asked one by one, never as one repair call",
                  len(retry_texts) == 2 and not any("did not finish" in text for text in retry_texts)
                  and "Frame 0" in retry_texts[0] and "Frame 1" in retry_texts[1], str([text[:80] for text in retry_texts]))
    harness.check("both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))


def _build_group_texts(repair: dict | None) -> tuple[str, str]:
    """The repair note and the closing instruction a group call for frames 0 and 1 is sent."""

    ctx = frame_batch_fixtures.load_run_context(count=2)
    items = [ctx.store.get_item(slug) for slug in ("frame-000", "frame-001")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-001", "items": items, "previousRecords": [],
                                     "latestDiagram": None, "repair": repair})
    messages = frames_processing_messages.build_frame_batch_messages(request)
    context_text = messages[1].content[0]["text"]
    closing_text = messages[-1].content[0]["text"]

    return context_text, closing_text


def test_each_frame_is_sent_the_lines_of_text_the_program_reads_on_it_and_checks_its_outline_against():
    """A worker refused for unquoted screen text never saw which lines the check counts; each frame's message now lists
    them, so quoting is checking a list off (a finding that blocked across runs)."""

    ctx = frame_batch_fixtures.load_run_context()
    items = [ctx.store.get_item(slug) for slug in ("frame-001", "frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-002", "items": items, "previousRecords": [], "latestDiagram": None})
    previous = item_validator.list_screen_text_items
    item_validator.list_screen_text_items = lambda paths, slug: (
        [item_validator.ScreenTextItem("line", ["Refunds are split by order", "total « price*2"]),
         item_validator.ScreenTextItem("line", ["Express orders retry twice"])] if slug == "frame-001" else [])
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        item_validator.list_screen_text_items = previous
    texts = [message.content[0]["text"] for message in messages[1:]
             if any(part.get("type") == "image_url" for part in message.content)]
    texts += [""] * (2 - len(texts))
    harness.check("frame 1's message lists each line the program read, quoted, says the outline is checked on them and "
                  "that each text is spelt as the image shows it",
                  '"Refunds are split by order"' in texts[0] and '"Express orders retry twice"' in texts[0]
                  and "checked" in texts[0] and "as the IMAGE spells it" in texts[0], texts[0])
    harness.check("a frame with no line read gets no list", "as the IMAGE spells it" not in texts[1], texts[1])
    harness.check("a mark the reader invents for an unread character is listed as \"…\", never as itself",
                  '"total … price*2"' in texts[0] and "«" not in texts[0], texts[0])


def test_running_text_and_a_side_panel_are_each_named_as_one_part_with_its_lines():
    """Listed loose, a screenshot's small-print paragraph was summed up as "a text block starting with ..." and a wiki's
    page tree of ten entries as "sidebar: left side", by a worker that had quoted such a paragraph whole one call before.
    The message names each as one part, its lines under it; other lines stay loose."""

    ctx = frame_batch_fixtures.load_run_context()
    items = [ctx.store.get_item(slug) for slug in ("frame-001", "frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-002", "items": items, "previousRecords": [], "latestDiagram": None})
    running = ["The supplier confirms each order within two working days of receiving it",
               "and sends the delivery date together with the invoice number to the buyer",
               "who may cancel the order free of charge until the goods have left the warehouse"]
    panel = ["Getting started", "Release notes", "Team pages"]
    previous = item_validator.list_screen_text_items
    item_validator.list_screen_text_items = lambda paths, slug: (
        [item_validator.ScreenTextItem("top bar", ["Team wiki", "Build status", "Release board"]),
         item_validator.ScreenTextItem("side panel", panel), item_validator.ScreenTextItem("paragraph", running),
         item_validator.ScreenTextItem("line", ["Order placed today"])] if slug == "frame-001" else [])
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        item_validator.list_screen_text_items = previous
    text = next((message.content[0]["text"] for message in messages[1:]
                 if any(part.get("type") == "image_url" for part in message.content)), "")
    harness.check("the paragraph is named one paragraph of 3 lines, its lines under it",
                  "one paragraph of running text, 3 lines read" in text and f'  - "{running[0]}"' in text, text)
    harness.check("the side panel is named with its entries under it",
                  "the side panel on the left, 3 lines read" in text and '  - "Release notes"' in text, text)
    harness.check("a bar of short entries across the top is window chrome: none of its texts is listed",
                  "Build status" not in text and "Release board" not in text, text)
    harness.check("another line stays a loose line", '\n- "Order placed today"' in text, text)


def test_a_frame_refused_before_is_told_why_when_it_is_asked_again():
    """Under zero repair a blocked frame is asked again only on a resume; phase 4 opens such a follow-up with the
    rejection that blocked it, and a frame's message now does the same."""

    ctx = frame_batch_fixtures.load_run_context()
    items = [{**ctx.store.get_item("frame-001"), "lastRefusal": "FRAME_SCREEN_TEXT_MISSING: 2 of 9 lines quoted"},
             ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-002", "items": items, "previousRecords": [], "latestDiagram": None})
    texts = [message.content[0]["text"] for message in frames_processing_messages.build_frame_batch_messages(request)[1:]
             if any(part.get("type") == "image_url" for part in message.content)]
    texts += [""] * (2 - len(texts))
    harness.check("frame 1's message carries its last refusal", "2 of 9 lines quoted" in texts[0]
                  and "refused" in texts[0], texts[0][-400:])
    harness.check("frame 2's carries none", "refused" not in texts[1].lower() or "lines quoted" not in texts[1], texts[1][-300:])


def test_a_frame_refused_again_loses_the_notes_an_earlier_pass_left():
    """A frame asked again by `--worker` or a resume still held the notes of an earlier pass; refused, it kept them,
    and phase 3 read as finished, so the next resume started phase 4 with the frame blocked."""

    ctx = frame_batch_fixtures.load_run_context()
    item = ctx.store.get_item("frame-001")
    frame_records_service.save_frame_record(ctx.paths, item, {**frame_batch_fixtures.RECORD, "frame": 1}, [])
    report = validation_report.ValidationReport(subject="frame-001")
    report.add("FRAME_SCREEN_TEXT_MISSING", "2 of 9 lines")
    process_frames_node._mark_refused_frame_blocked_and_clear_notes(ctx, item, report, "its notes were refused")
    harness.check("blocked, and its earlier notes are gone from the frames file",
                  ctx.store.get_item("frame-001")["status"] == "blocked"
                  and "frame-001" not in [record["slug"] for record in frame_records_service.read_frame_records(ctx.paths)],
                  str([record["slug"] for record in frame_records_service.read_frame_records(ctx.paths)]))


def test_the_closing_message_lists_every_section_heading_in_order():
    """Two batches' answers left out sections their instructions name (`## What happens` in all ten frames, `## Facts`
    in five); the closing message now lists the headings, from the record's own table."""

    ctx = frame_batch_fixtures.load_run_context()
    items = [ctx.store.get_item(slug) for slug in ("frame-001", "frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-002", "items": items, "previousRecords": [], "latestDiagram": None})
    closing = frames_processing_messages.build_frame_batch_messages(request)[-1].content[0]["text"]
    headings = [f"## {heading}" for heading, _ in frame_record.FRAME_SECTIONS.values()]
    places = [closing.find(heading) for heading in headings]
    harness.check("every heading is in the closing message, in the record's order", all(place >= 0 for place in places)
                  and places == sorted(places), str(list(zip(headings, places))))
    # A worker counted the title line as the first of the sections and listed one section fewer, dropping
    # `## What happens` from both frames' answers (lecture-1 frames 67-68 and 93-94, 2026-10-13)
    harness.check("the title line is named as coming before the sections, never as one of them",
                  f"under its title line, each document has these {len(headings)} sections" in closing.casefold(), closing[:400])
    harness.check("and the frame's own `## What happens` is told apart from the batch's",
                  '"## What happens" is each frame\'s own' in closing, closing[:600])


def test_a_repair_call_is_told_once_what_to_do_with_the_batch_summary():
    """The owner's run showed a repair told both "set batch_summary to an empty string" and "Then write batch_summary":
    the model read the reasoning twice and picked the wrong one. The closing states it once."""

    repair_after_account_passed = {"accepted": [], "failed": [{"frame": 1, "reason": "no facts"}], "batchNumbers": [0, 1]}
    context_text, closing_text = _build_group_texts(repair_after_account_passed)
    both = context_text + closing_text
    harness.check("when the earlier account passed, it is told to write no batch, and never to write it",
                  'Write no "# Batch"' in both and 'Write "# Batch" FIRST' not in both, both[-900:])
    repair_after_account_refused = {**repair_after_account_passed, "summaryReason": "too long"}
    context_text, closing_text = _build_group_texts(repair_after_account_refused)
    harness.check("when the earlier account failed, it is told to write it again, with the reason",
                  'Write "# Batch" FIRST' in closing_text and "too long" in context_text
                  and 'Write no "# Batch"' not in context_text + closing_text, closing_text)
    _, closing_text = _build_group_texts(None)
    harness.check("a first call writes it", 'Write "# Batch" FIRST' in closing_text, closing_text)


def test_with_no_repair_call_a_failed_frame_goes_straight_to_its_single_frame_call():
    """`framesProcessing.repairCalls` 0: the frames a batch call did not finish are never sent back together."""

    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    alone = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 1, "facts": [SECOND_FACT]})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, alone), count=2)
    with fx.critique_switches():
        state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=0)
    harness.check("the group, the frame alone, then the critique, the repair and the open questions: no repair call",
                  ctx.model.call_count == 5,
                  f"calls={ctx.model.call_count}")
    harness.check("both are written", sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))


def test_with_no_repeat_a_refused_frame_is_blocked_at_once_and_never_repaired():
    """The owner: zero repair. A frame the batch's answer did not finish is blocked as it stands: no call is added
    for it, the second look never sees it, and nothing after the block runs."""

    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    model = fakes.scripted(group, fakes.second_pass(is_nothing_to_change=True))
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=0, single_frame_attempts=0)
    blocked = ctx.store.get_item("frame-001")
    harness.check("the refused frame is blocked, the finished one written",
                  state["written"] == ["frame-000"] and blocked["status"] == "blocked"
                  and "no frame is repaired" in str(blocked.get("blockedReason")), f"{state['written']} {blocked}")
    harness.check("only the batch's call ran: no repair, no second look after the block", len(model.calls) == 1,
                  f"{len(model.calls)} calls")


def test_a_frame_the_repair_call_also_failed_is_asked_alone():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    repair = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    alone = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 1, "facts": [SECOND_FACT]})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, repair, alone), count=2)
    with fx.critique_switches():
        state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    harness.check("the group, the repair call, the frame alone, then the critique, the repair and the open questions",
                  ctx.model.call_count == 6,
                  f"calls={ctx.model.call_count}")
    harness.check("both are written", sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))


def test_a_return_inside_a_batch_is_sent_as_a_frame_with_its_first_showing():
    batch = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": number} for number in (0, 1, 2)])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(batch), count=3)
    ctx.store.set_item_status("frame-002", "pending", repeatOf="frame-000")
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3)
    first = ctx.model.calls[0] if ctx.model.calls else []
    texts = [" ".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict))
             for message in first[1:] if isinstance(message.content, list)]
    return_message = next((text for text in texts if text.startswith("Frame 2,")), "")
    harness.check("one call for frames 0, 1 and 2: the return is a frame of the batch",
                  "frames 0, 1, 2" in " ".join(texts) or "0, 1, 2" in " ".join(texts), " | ".join(texts)[:600])
    harness.check("its message names the earlier showing in the same batch",
                  "This screen was shown before, as frame 0" in return_message
                  and "the ones you write for frame 0 in this answer" in return_message, return_message)
    harness.check("every frame gets its own notes",
                  [entry["slug"] for entry in frame_records_service.read_frame_records(ctx.paths)]
                  == ["frame-000", "frame-001", "frame-002"], str(frame_records_service.read_frame_records(ctx.paths)))


def test_a_slide_built_in_steps_stays_in_one_batch_and_each_step_is_told_its_place():
    batch = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": number} for number in (0, 1, 2)])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(batch), count=3)
    ctx.store.set_item_status("frame-001", "pending", stepOf="frame-000")
    ctx.store.set_item_status("frame-002", "pending", stepOf="frame-000")
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=2)
    first = ctx.model.calls[0] if ctx.model.calls else []
    texts = [" ".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict))
             for message in first[1:] if isinstance(message.content, list)]
    by_frame = {number: next((text for text in texts if text.startswith(f"Frame {number},")), "") for number in (0, 1, 2)}
    harness.check("the batch grows past its size to hold the whole build: frames 0, 1 and 2 in one call",
                  all(by_frame.values()), " | ".join(texts)[:600])
    harness.check("the first step is told it starts a build of 3 steps",
                  "Frames 0 to 2 show one screen built up in 3 steps" in by_frame[0], by_frame[0])
    harness.check("a later step is told its place and what to write",
                  "step 3 of 3 of the screen first shown as frame 0" in by_frame[2]
                  and "- step 3 of the screen of frame 0:" in by_frame[2], by_frame[2])


def test_a_one_frame_unit_gets_the_general_budget_when_no_frame_is_asked_alone():
    answer = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 0})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(answer), count=1)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000"], frames_per_call=1, single_frame_attempts=0)
    harness.check("a frame that is a unit of its own is asked, though framesProcessing.singleFrameAttempts is 0",
                  ctx.store.get_item("frame-000").get("status") == "written", str(ctx.store.get_item("frame-000")))


def test_the_records_file_stays_in_frame_order_whatever_order_frames_are_accepted_in():
    ctx = frame_batch_fixtures.load_run_context(count=3)
    for index in (2, 0, 1):
        item = ctx.store.get_item(f"frame-{index:03d}")
        frame_records_service.save_frame_record(ctx.paths, item, {**frame_batch_fixtures.RECORD, "frame": index}, [])
    harness.check("the file lists frames by number", [entry["index"] for entry in
                                                      frame_records_service.read_frame_records(ctx.paths)] == [0, 1, 2],
                  str([entry["index"] for entry in frame_records_service.read_frame_records(ctx.paths)]))


def test_a_feedback_document_in_a_batch_answer_is_recorded_and_the_notes_are_kept():
    """Phase 3 answers in markdown with no tools, so its report is a `# Feedback` document at the end of its answer:
    the program writes it into the run's worker-feedback.md, read or not the rest, and the notes stand as they are."""

    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}])
    feedback = ("\n\n# Feedback\n\n## Kind\nunclear_instruction\n\n## Summary\nthe rule on repeated slides can be read "
                "two ways\n\n## Detail\nIt says outline it whole, and also write only what it adds.")
    model = fakes.scripted(type(group)(content=str(group.content) + feedback))
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    report = ctx.paths.output_root / output_contract.WORKER_FEEDBACK_FILE
    text = report.read_text(encoding="utf-8") if report.is_file() else ""
    harness.check("the report is in worker-feedback.md with its kind and summary",
                  "unclear_instruction" in text and "read two ways" in text, text[:400])
    harness.check("and both frames' notes are written as if it were not there", state["written"] == ["frame-000", "frame-001"],
                  str(state["written"]))


def test_a_frame_refused_only_for_unquoted_lines_is_blocked_too():
    """The owner: no frame is accepted with a warning. Screen lines left unquoted block the frame like any finding."""

    quoted = ["Order intake overview", "Refunds are split", "Express orders retry", "The books close"]
    screen = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in quoted)
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1, "screen": screen}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, fakes.second_pass(is_nothing_to_change=True)), count=2)
    unquoted = ["Fractional part of shares", "Tax rate applied here", "Payment method used", "Settlement runs at noon",
                "Every refund needs approval", "Old routes close in March"]
    previous = item_validator.read_screen_lines, item_validator.add_unquoted_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: [*quoted, *unquoted] if slug == "frame-001" else []
    # The program completes every outline as it is written; the check is the guard for when it does not
    item_validator.add_unquoted_screen_lines = lambda paths, slug, record: record
    try:
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=0, single_frame_attempts=0)
    finally:
        item_validator.read_screen_lines, item_validator.add_unquoted_screen_lines = previous
    item = ctx.store.get_item("frame-001")
    harness.check("blocked, never accepted with a warning", item["status"] == "blocked"
                  and not item.get("acceptedWithFindings"), str(item))
    harness.check("the refusal is kept on the frame in the validator's words, for the frame's next request",
                  "FRAME_SCREEN_TEXT_MISSING" in str(item.get("lastRefusal")) and "Tax rate applied here" in str(item.get("lastRefusal")),
                  str(item.get("lastRefusal")))


def test_a_stop_ends_phase_3_before_the_next_batch():
    """A stop sent to a paused run leaves the pause and reaches the frame loop, which ends the phase before asking the
    next batch: before, it acted on drain alone and the paused run went on with its next batch."""

    model = fakes.scripted()
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    ctx.control_check = lambda: "stop"
    state = {**graph_state.new_run_state(frame_batch_fixtures.FRAMES_PROCESSING, frame_batch_fixtures.FRAMES_PROCESSING), "queue": ["frame-000", "frame-001"]}
    update = process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}})
    harness.check("the phase ends and no model is called", update.get("is_drained") is True and not model.calls,
                  f"{update} {len(model.calls)} calls")


def test_a_build_step_asked_alone_points_to_its_first_step_only_when_that_step_has_notes():
    """A step asked alone was told its first step's notes were among the earlier notes when the first step had none
    (blocked), and the worker, looking for a slide it was never shown, left the editor on screen unoutlined."""

    step = {"slug": "frame-054", "index": 54, "stepOf": "frame-053"}
    noted = frames_processing_messages.render_build_note([step], step, {"frame-053"})
    unnoted = frames_processing_messages.render_build_note([step], step, set())
    harness.check("with the first step's notes among the earlier ones: a step of the screen of frame 53",
                  "frame 53" in noted and "screen" in noted, noted)
    harness.check("without them: no step note, so the frame is outlined whole", unnoted == "", unnoted)


def test_an_account_missing_under_zero_repair_is_asked_alone_and_the_frames_are_kept():
    """Ten accepted frames were blocked because the answer that wrote them all forgot the batch's account at its end
    (2026-10-06). Zero repair is about frames: no frame is asked again, and the account, which is no frame, is."""

    group = fakes.frame_notes(batch_summary="", atmosphere="", frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}])
    alone = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}])
    # the attempt's continuation for the account writes nothing, so it is asked alone
    model = fakes.scripted(group, fakes.says(""), alone, fakes.second_pass(is_nothing_to_change=True),
                           fakes.nothing_open())
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    printed = logging.handlers.BufferingHandler(capacity=10_000)
    ctx.log.addHandler(printed)
    level = ctx.log.level
    ctx.log.setLevel(logging.INFO)
    with fx.critique_switches():
        state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2,
                                                           repair_calls=0, single_frame_attempts=0)
    ctx.log.removeHandler(printed)
    ctx.log.setLevel(level)
    lines = [record.getMessage() for record in printed.buffer]
    harness.check("the console says why the account is asked alone, and names the call for what it asks",
                  any("account was refused → asked alone" in line for line in lines)
                  and any(line.startswith("account of frames 0 → 1") for line in lines), str(lines))
    harness.check("both frames are kept, none blocked for the account",
                  sorted(state["written"]) == ["frame-000", "frame-001"] and not state.get("blocked"),
                  f"{state['written']} {state.get('blocked')}")
    harness.check("the account was asked alone, in a call of its own after the continuation, before the critique",
                  len(model.calls) == 6, f"{len(model.calls)} calls")


def test_a_repeat_may_point_at_the_frame_it_repeats_and_the_program_fills_its_outline():
    """A frame marked a repeat was blocked for pointing at the frame it shows again (2026-10-06). The screens are the
    same by the rule that marked it, so the program knows the outline and no call writes it twice."""

    from services import frame_records_service, record_file_service
    from utils import frame_markdown_utils
    harness.check("a one-line pointer is read as a pointer",
                  frame_markdown_utils.is_screen_pointer("- same as frame 15")
                  and frame_markdown_utils.is_screen_pointer("as in Frame 15"),
                  "is_screen_pointer")
    harness.check("an outline that merely mentions a frame in a quoted text is no pointer",
                  not frame_markdown_utils.is_screen_pointer('- slide: white\n  - title "Frame 15 of the deck"'),
                  "is_screen_pointer")


def test_an_account_written_by_the_second_repair_round_keeps_its_title_and_leaves_no_working_record():
    group = fakes.frame_notes(frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    first_repair = fakes.frame_notes(frames=[{**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    second_repair = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, batch_title="Gateway rollout plan",
                                      frames=[{**frame_batch_fixtures.RECORD, "frame": 1}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, first_repair, second_repair), count=2)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=2)
    entry = next((batch for batch in batch_summaries_service.read_batch_summaries(ctx.paths)
                  if batch["slug"] == "frames-000-001"), {})
    harness.check("the batch keeps the title its second repair round wrote", entry.get("title") == "Gateway rollout plan",
                  str(entry))
    leftover = record_file_service.build_record_path(ctx.paths, "frames-000-001-repair-2-batch-summary")
    harness.check("and the round's working record is removed", not leftover.is_file(), str(leftover))


def test_a_refused_frame_blocked_with_no_single_frame_attempt_drops_its_old_notes_and_keeps_its_refusal():
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=1)
    item = ctx.store.get_item("frame-000")
    frame_records_service.save_frame_record(ctx.paths, item, {**frame_batch_fixtures.RECORD, "frame": 0}, [])
    report = validation_report.ValidationReport(subject="frame-000")
    report.add("FRAME_SCREEN_TEXT_MISSING", "the screen outline quotes too little of the frame's text")
    with fx.single_frame_attempts(0):
        process_frames_node._process_unfinished_frame(ctx, item, report)
    harness.check("its earlier notes are gone", all(entry["slug"] != "frame-000"
                                                    for entry in frame_records_service.read_frame_records(ctx.paths)))
    harness.check("and its refusal is kept for the resume",
                  "FRAME_SCREEN_TEXT_MISSING" in str(ctx.store.get_item("frame-000").get("lastRefusal")),
                  str(ctx.store.get_item("frame-000")))


def test_a_frame_accepted_after_a_refusal_no_longer_carries_the_refusal():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group), count=2)
    ctx.store.set_item_status("frame-001", "pending", lastRefusal="FRAME_SCREEN_TEXT_MISSING: quotes too little")
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    item = ctx.store.get_item("frame-001")
    harness.check("the accepted frame is written", item.get("status") == "written", str(item))
    harness.check("and its old refusal is gone, so no later request opens with it", not item.get("lastRefusal"), str(item))


def test_a_repair_round_tells_every_refused_return_its_earlier_showing():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 2}])
    repair = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE, frames=[{**frame_batch_fixtures.RECORD, "frame": 2}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, repair), count=3)
    ctx.store.set_item_status("frame-002", "pending", repeatOf="frame-000")
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3)
    repair_call = ctx.model.calls[1] if len(ctx.model.calls) > 1 else []
    texts = [" ".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict))
             for message in repair_call[1:] if isinstance(message.content, list)]
    harness.check("the repair call's message for frame 2 names its earlier showing",
                  any(text.startswith("Frame 2,") and "This screen was shown before, as frame 0" in text for text in texts),
                  " | ".join(texts)[:800])


def test_the_console_names_the_step_that_runs_for_frames_a_call_did_not_finish():
    frame = {"slug": "frame-004", "index": 4}
    with fx.single_frame_attempts(0):
        after_last_round = process_frames_node._render_repair_round_outcome([frame], is_round_left=False)
    harness.check("after the last repair round with no frame asked alone, the frame is blocked, not asked alone",
                  after_last_round == " → blocked (frame 4)", after_last_round)
    halves = process_frames_node._render_batch_outcome([frame, {"slug": "frame-005", "index": 5}],
                                                         process_frames_node.REPAIR_TOGETHER, is_sent_back_in_halves=True)
    harness.check("a batch cut at the output limit says it goes back in halves",
                  halves == " → the batch goes back in halves (frames 4, 5)", halves)


def test_a_repair_call_does_not_call_the_frames_it_leaves_out_repeats():
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.EMPTY_RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 2}])
    repair = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                               frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 2}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, repair), count=3)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3)
    repair_call = ctx.model.calls[1] if len(ctx.model.calls) > 1 else []
    texts = " ".join(" ".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict))
                     for message in repair_call[1:] if isinstance(message.content, list))
    harness.check("frame 1, finished by the first call, is not called a repeat of a screen already noted",
                  "repeat screens already noted" not in texts, texts[:600])


def test_a_batch_with_a_gap_in_its_numbers_does_not_call_the_gap_repeats():
    """No repeat is left out of a batch any more: a gap (`--worker 0 2`, a resume) is frames this call does not ask."""

    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 2}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group), count=3)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-002"], frames_per_call=2)
    first = ctx.model.calls[0] if ctx.model.calls else []
    texts = " ".join(" ".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict))
                     for message in first[1:] if isinstance(message.content, list))
    harness.check("the call does not say frame 1 repeats a screen", "repeat screens already noted" not in texts, texts[:500])


def test_frames_repaired_in_one_call_are_built_from_each_other_as_a_batch_s_frames_are():
    """A batch's frames are each built from the ones before them in the reply; frames one repair call wrote together
    were each built from the batch alone, so `--worker` rewriting one did not name the other stale."""

    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0},
                                      {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1},
                                      {**frame_batch_fixtures.EMPTY_RECORD, "frame": 2}])
    repair = fakes.frame_notes(frames=[{**frame_batch_fixtures.RECORD, "frame": 1, "facts": [SECOND_FACT]},
                                       {**frame_batch_fixtures.RECORD, "frame": 2}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, repair), count=3)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3)
    built_from = {entry["slug"]: entry.get("builtFrom") for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the second repaired frame is built from the first", "frame-001" in (built_from.get("frame-002") or []),
                  str(built_from))


def test_a_batch_cut_at_the_limit_asks_no_second_half_once_the_first_half_blocked():
    items = [{"slug": f"frame-00{number}", "index": number} for number in range(4)]
    asked = []

    def blocked_batch(ctx, half):
        asked.append([item["slug"] for item in half])

        return {"written": [], "blocked": [half[0]["slug"]]}

    original = process_frames_node._process_frame_batch
    process_frames_node._process_frame_batch = blocked_batch
    try:
        result = process_frames_node._process_halves(None, items)
    finally:
        process_frames_node._process_frame_batch = original
    harness.check("only the first half was asked", asked == [["frame-000", "frame-001"]], str(asked))
    harness.check("and its block is reported", result["blocked"] == ["frame-000"], str(result))


def test_an_account_asked_alone_leaves_the_batch_s_refusal_on_disk_defect():
    # A batch whose answer forgot its account was asked for it alone, and its attempt's validation.json still said ok
    # with no finding, so the page and the monitor's gate saw no refusal. Now the program's judgement is written there.
    group = fakes.frame_notes(batch_summary="", atmosphere="", frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}])
    alone = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}])
    # the attempt's continuation for the account writes nothing, so it is asked alone
    model = fakes.scripted(group, fakes.says(""), alone, fakes.second_pass(is_nothing_to_change=True),
                           fakes.nothing_open())
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2,
                                                   repair_calls=0, single_frame_attempts=0)
    found = sorted(ctx.run_dir.rglob("frames-000-001/attempt-*/validation.json"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in found]
    harness.check("the batch's attempt is recorded refused, naming the missing account",
                  any(report["ok"] is False and "BATCH_SUMMARY_MISSING" in [f["code"] for f in report["findings"]]
                      for report in reports), str([str(path) for path in found]) + str(reports))


def test_a_batch_blocked_on_one_frame_gets_its_follow_ups_once_the_frame_is_written_defect():
    # A batch blocked on one frame under zero repair left its accepted frames with no critique, second pass or open
    # questions, and the resume asked only the blocked frame (DEFECTS 285); now the phase finishes such a batch first
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}])
    model = fakes.scripted(group, fakes.frame_notes(frames=[{**frame_batch_fixtures.FRAME, "frame": 1}]),
                           fakes.second_pass(is_nothing_to_change=True), fakes.nothing_open())
    ctx = frame_batch_fixtures.load_run_context(model, count=2)
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=0,
                                                   single_frame_attempts=0)
        harness.check("the block left frame 0 accepted and no batch account",
                      ctx.store.get_item("frame-000")["status"] == "written"
                      and not batch_summaries_service.read_batch_summaries(ctx.paths))
        # The resume: the blocked frame is asked again, alone
        ctx.store.set_item_status("frame-001", "pending")
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-001"], frames_per_call=2, repair_calls=0,
                                                   single_frame_attempts=0)
    batches = {batch["slug"]: batch for batch in batch_summaries_service.read_batch_summaries(ctx.paths)}
    # Part of its call's range, it is finished under its own frames' name (DEFECTS 541)
    harness.check("frame 0's batch is finished as a batch of its own frames",
                  batches.get("frames-000-000", {}).get("frames") == [0]
                  and batches["frames-000-000"]["source"] == "batch", str(batches))
    harness.check("its critique and second pass ran", any("critique" in str(call) for call in model.calls)
                  and ctx.store.get_phase("frames_processing") == "complete", f"{len(model.calls)} calls")


def test_a_batch_holding_part_of_its_call_range_takes_no_account_of_that_call_defect():
    # Two frames were finished with the account of a call that also answered three later frames, which another batch
    # now holds (DEFECTS 289); now a call's account goes only to a batch holding every frame of its range
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=3)
    items = [ctx.store.get_item(f"frame-{index:03d}") for index in range(3)]
    harness.check("part of the range takes no account", not process_frames_node._is_whole_range(ctx, "frames-000-002", items[:2]))
    harness.check("the whole range takes it", process_frames_node._is_whole_range(ctx, "frames-000-002", items))
