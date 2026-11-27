"""Every batch of frames comes back with one third-person summary of the batch. REQUIREMENTS.md R15, part 2.

`batch_summary` is required on the batch answer and never asked of a one-frame call. The closing message asks for
it, and a copy passed over inside the batch sends its speech as text only, so the summary's input misses nothing
said in the batch. It is checked like a record; a failed one is asked again in the repair call, or, when every
frame passed, in one short text-only call. Accepted summaries collect in `03_batch_summaries.json` in time order,
a unit of one frame standing in with its description, and phase 4's parts call, executive summary draft and `list_batches`
read them.
"""

import json
from pathlib import Path

import cv2
import numpy
from langchain_core.messages import AIMessage

from support import fakes
from support import frame_batch_fixtures
from support import fixtures as fx
from support import harness

import config

from models import output_contract
from models.attempt_request import AttemptRequest
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from prompts import document_messages
from prompts import frames_processing_messages
from prompts import write_messages
from utils import frame_markdown_utils
from schemas import frame_record
from schemas import write_actions
from graph.nodes import process_frames_node
from services import batch_summaries_service
from services import frame_records_service
from services import chain_dispatch_service
from services import call_log_service
from services import record_file_service
from services import write_reading_service
from services.ignore_list_service import IgnoreList
from utils import reading_tool_utils
from validators import item_validator


def _all_text(messages) -> str:
    return "\n".join(str(part.get("text", "")) for message in messages
                     for part in (message.content if isinstance(message.content, list) else [{"text": message.content}])
                     if isinstance(part, dict))


def _count_frames(messages) -> int:
    return sum(part.get("type") == "image_url" for message in messages if isinstance(message.content, list)
               for part in message.content if isinstance(part, dict))


def test_a_group_answer_carries_the_batch_and_the_instruction_teaches_its_shape():
    answer = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, summary="")
    parsed = chain_dispatch_service.MARKDOWN_READERS["frame_batch"](answer.content)[0]
    harness.check("a group answer with no account reads as an empty one, for the check to refuse",
                  parsed["batch_summary"] == "" and parsed["frames"][0]["frame"] == 0, str(parsed))
    whole = chain_dispatch_service.MARKDOWN_READERS["frame_batch"](frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}).content)[0]
    harness.check("and one with it carries it", whole["batch_summary"] == frame_batch_fixtures.SUMMARY)
    instruction = " ".join((config.AGENT_CONTEXT_DIR / "instructions" / "_shared" / "frames-processing.md").read_text(encoding="utf-8").split())
    harness.check("frames_processing.md tells the model the shape", "Three to ten sentences" in instruction
                  and f"at most {item_validator.SUMMARY_MAX_WORDS} words" in instruction
                  and "Do not go frame by frame" in instruction)


def test_the_group_message_asks_for_the_batch_s_title_and_account():
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-002", "items": items, "previousRecords": []})
    messages = frames_processing_messages.build_frame_batch_messages(request)
    closing = messages[-1].content[0]["text"]
    harness.check("the closing message asks for the batch's title first", 'Write "# Batch" FIRST, before the first frame\'s notes, from what you found reading every frame. Under "## Title": the topic '
                  "of THESE frames" in " ".join(closing.split()), closing)
    harness.check("then for its account", 'Under "## Story of the batch": the whole batch these frames and their '
                  "words cover" in " ".join(closing.split()), closing)
    harness.check("still one frame per frame asked", _count_frames(messages) == 2)


def test_each_batch_summary_breach_fires_its_own_code():
    paths = fx.make_paths()

    def codes(summary,
              atmosphere: str = frame_batch_fixtures.ATMOSPHERE) -> set[str]:
        record_file_service.write_batch_record(paths, "frames-000-001-summary",
                                         {} if summary is None
                                         else {"batch_title": "Retry budget settings", "batch_summary": summary, "atmosphere": atmosphere})
        return harness.codes(item_validator.validate_batch_summary_record(paths, "frames-000-001-summary"))

    harness.check("a good summary passes", codes(frame_batch_fixtures.SUMMARY) == set(), str(codes(frame_batch_fixtures.SUMMARY)))
    harness.check("none is BATCH_SUMMARY_MISSING", codes(None) == {"BATCH_SUMMARY_MISSING"})
    harness.check("an account with no atmosphere is BATCH_ATMOSPHERE_MISSING",
                  codes(frame_batch_fixtures.SUMMARY, atmosphere=" ") == {"BATCH_ATMOSPHERE_MISSING"}, str(codes(frame_batch_fixtures.SUMMARY, atmosphere=" ")))
    harness.check("a gendered word in the atmosphere is noted, never rejected",
                  codes(frame_batch_fixtures.SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE + " Then he laughs.") == set())
    too_few = " ".join(["The presenter shows the limit."] * (item_validator.SUMMARY_MIN_SENTENCES - 1))
    harness.check("one sentence short of the least is BATCH_SUMMARY_SHAPE", codes(too_few) == {"BATCH_SUMMARY_SHAPE"})
    # The most sentences, each long enough that together they reach the word limit exactly.
    words_each = item_validator.SUMMARY_MAX_WORDS // item_validator.SUMMARY_MAX_SENTENCES
    long_sentence = "The presenter reads " + "every " * (words_each - 4) + "value."
    at_limit = " ".join([long_sentence] * item_validator.SUMMARY_MAX_SENTENCES)
    harness.check("the most sentences at the word limit pass", codes(at_limit) == set(), str(codes(at_limit)))
    harness.check("one word past it is BATCH_SUMMARY_SHAPE", codes(at_limit + " Then") == {"BATCH_SUMMARY_SHAPE"})
    harness.check("one sentence past the most is BATCH_SUMMARY_SHAPE",
                  codes(" ".join(["The presenter saves it."] * (item_validator.SUMMARY_MAX_SENTENCES + 1)))
                  == {"BATCH_SUMMARY_SHAPE"})
    harness.check("'we' does not reject the account", codes(frame_batch_fixtures.SUMMARY + " Then we stop.") == set())
    record_file_service.write_batch_record(paths, "frames-000-001-summary", {"batch_title": "Retry budget settings", "batch_summary": frame_batch_fixtures.SUMMARY + " Then we stop.", "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    harness.check("and it is not noted: the person check was removed (the owner)",
                  not item_validator.validate_batch_summary_record(paths, "frames-000-001-summary").notes,
                  str([note.code for note in
                       item_validator.validate_batch_summary_record(paths, "frames-000-001-summary").notes]))
    # A gendered pronoun is nothing: the check and its warning were removed (the owner), so the account is neither
    # rejected nor noted for one.
    harness.check("'she' does not reject the account", codes(frame_batch_fixtures.SUMMARY + " Then she stops.") == set())
    record_file_service.write_batch_record(paths, "frames-000-001-summary", {"batch_title": "Retry budget settings", "batch_summary": frame_batch_fixtures.SUMMARY + " Then she stops.", "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    harness.check("and it is not noted either",
                  not item_validator.validate_batch_summary_record(paths, "frames-000-001-summary").notes,
                  str([note.code for note in
                       item_validator.validate_batch_summary_record(paths, "frames-000-001-summary").notes]))
    record_file_service.write_batch_record(paths, "frames-000-001-summary", {"batch_title": "Retry budget settings", "batch_summary": "One. Two.", "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    hint = item_validator.validate_batch_summary_record(paths, "frames-000-001-summary").render_hint()
    harness.check("the shape finding counts sentences and words", "2 sentences and 2 words" in hint
                  and (f"{item_validator.SUMMARY_MIN_SENTENCES} to {item_validator.SUMMARY_MAX_SENTENCES} sentences and at "
                       f"most {item_validator.SUMMARY_MAX_WORDS} words") in hint, hint)


def test_a_batch_of_fewer_frames_than_the_least_sentences_needs_one_sentence_a_frame():
    """A batch of three frames asked again was blocked for writing three sentences against five: a short batch has
    less to retell."""

    paths = fx.make_paths()
    three = "The presenter opens the settings and raises the limit. The page saves it."
    record_file_service.write_batch_record(paths, "frames-000-002-summary",
                                           {"batch_title": "Retry budget settings", "batch_summary": three,
                                            "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    harness.check("two sentences pass for two frames",
                  harness.codes(item_validator.validate_batch_summary_record(paths, "frames-000-002-summary", 2)) == set())
    harness.check("and are short for ten",
                  harness.codes(item_validator.validate_batch_summary_record(paths, "frames-000-002-summary", 10))
                  == {"BATCH_SUMMARY_SHAPE"})


def test_accepted_summaries_are_stored_in_time_order_with_their_span():
    first = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    second = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 2}, {**frame_batch_fixtures.FRAME, "frame": 3},
                    summary=frame_batch_fixtures.SUMMARY.replace("order limit", "refund rule"))
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(first, second), count=5)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002", "frame-003"], frames_per_call=2)
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("one entry per batch, in time order", [entry["slug"] for entry in stored]
                  == ["frames-000-001", "frames-002-003"], str(stored))
    harness.check("each with its frames, its span and its summary", stored[0]["frames"] == [0, 1]
                  and stored[0]["start"] == 0.0 and stored[0]["end"] == 12.0 and stored[0]["summary"] == frame_batch_fixtures.SUMMARY
                  and stored[1]["start"] == 12.0 and stored[1]["end"] == 24.0, str(stored))
    harness.check("in its own file", (ctx.paths.output_root / "03_batch_summaries.json").is_file())


def test_a_failed_summary_is_asked_again_in_the_repair_call():
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.EMPTY_FRAME, "frame": 1}, summary="Too short.")
    repair = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.run_two_frame_batch(group, repair)
    sent = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the group, its repair, and R19's critique and repair", ctx.model.call_count == 5,
                  f"calls={ctx.model.call_count}")
    harness.check("the repair call is told the summary failed, and why", "BATCH_SUMMARY_SHAPE" in sent
                  and '"# Batch"' in sent and "frames 0, 1" in sent, sent[-900:])
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("the repaired summary is the one stored", [entry["summary"] for entry in stored] == [frame_batch_fixtures.SUMMARY],
                  str(stored))


def test_when_only_the_summary_failed_one_text_only_call_asks_for_it():
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1}, summary="We show it and he saves.")
    alone = fakes.frame_notes(batch_summary=frame_batch_fixtures.SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE)
    ctx = frame_batch_fixtures.run_two_frame_batch(group, alone)
    sent = ctx.model.calls[1] if ctx.model.call_count > 1 else []
    harness.check("the group, one summary call, then R19's critique and repair", ctx.model.call_count == 5,
                  f"calls={ctx.model.call_count}")
    harness.check("the summary call sends no frame", sent and _count_frames(sent) == 0)
    text = _all_text(sent)
    harness.check("it is handed the accepted notes and the batch's speech",
                  "89 % of voters want cheaper fuel" in text and "words of frame 0" in text
                  and "words of frame 1" in text, text[-900:])
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("its summary is stored for the batch", [(entry["slug"], entry["summary"]) for entry in stored]
                  == [("frames-000-001", frame_batch_fixtures.SUMMARY)], str(stored))


def test_a_unit_of_one_frame_stands_in_with_its_third_person_description():
    answer = fakes.frame_notes(**{**frame_batch_fixtures.FRAME, "frame": 0})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(answer), count=2)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000"], frames_per_call=1)
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("the frame's third-person description stands for its batch",
                  [(entry["frames"], entry["summary"], entry["source"]) for entry in stored]
                  == [([0], frame_batch_fixtures.DESCRIPTION, "third_person_description")], str(stored))


BATCHES = [{"slug": "frames-000-003", "frames": [0, 1, 2, 3], "start": 0.0, "end": 95.0, "summary": frame_batch_fixtures.SUMMARY,
              "source": "batch", "atmosphere": frame_batch_fixtures.ATMOSPHERE},
             {"slug": "frame-004", "frames": [4], "start": 95.0, "end": 130.0, "summary": frame_batch_fixtures.DESCRIPTION,
              "source": "third_person_description"}]


def test_the_final_summary_is_asked_with_no_number_to_count():
    """Told only the total, the writer counted its summary word by word in its thinking, phase 4's slowest call (DEFECTS.md
    64); told "at most 41 sentences of at most 12 words", it counted each sentence's words for 22 minutes (the Wikipedia
    runs). The program counts; the request gives no number to count against."""

    paths = fx.make_paths()
    request = AttemptRequest(job_kind="summary", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs={"slug": "summary", "title": "AcmeSync rollout", "parts": [],
                                                          "sections": [], "maxWords": 50})
    text = _all_text(document_messages.build_summary_messages(request))
    ask = text[text.index("Write the executive summary"):]
    harness.check("the summary is asked as its conclusion and main points, no number given, never counted in the thinking",
                  "conclusion first" in ask and "never count words or sentences in your thinking" in ask
                  and "50" not in ask and "12 words" not in ask, ask[:600])


def test_the_summary_is_handed_the_notes_each_section_cites_defect():
    # Handed each section's first paragraph alone, the writer's rewording, three summaries stated what no note says: "all
    # configuration changes executed successfully" for "none of them rolled back" (DEFECTS 505). Now each section line
    # carries the text of the notes it cites
    from graph.nodes import write_document_node
    from models.document_spec import DocumentChapterSpec, DocumentTopicSpec

    paths = fx.make_paths()
    section = {"paragraphs": [{"text": "6 configuration changes were made, none of which required rollback.",
                               "entry_ids": ["S0008"]}], "blocks": []}
    chapters = [DocumentChapterSpec(title="January", topics=[DocumentTopicSpec(title="January", sections=[section])])]
    entries = {"S0008": {"id": "S0008", "text": "6 configuration changes were made, none of them rolled back."}}
    inputs = write_document_node._build_summary_inputs("Q1 report", [], chapters, [], entries)
    request = AttemptRequest(job_kind="summary", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs=inputs)
    text = _all_text(document_messages.build_summary_messages(request))
    harness.check("the note's own sentence stands under the section line",
                  "S0008: 6 configuration changes were made, none of them rolled back." in text, text[:900])


def test_phase_4_parts_call_and_executive_summary_draft_receive_the_batches():
    paths = fx.make_paths()
    moments = [{"number": 1, "start": 0.0, "screenKind": "web application", "title": "Billing", "first": "a limit",
                "speech": "the limit"}]
    inputs = {"slug": "parts", "moments": moments, "batches": BATCHES}
    parts = AttemptRequest(job_kind="parts", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                           ignore=IgnoreList(), inputs=inputs)
    parts_text = _all_text(document_messages.build_parts_messages(parts))
    harness.check("the parts call gets the batches in time order, without their spans: a batch is never a point",
                  "0:00 to 1:35" not in parts_text and frame_batch_fixtures.SUMMARY in parts_text and "never an agenda point" in parts_text
                  and parts_text.index(frame_batch_fixtures.SUMMARY) < parts_text.index(frame_batch_fixtures.DESCRIPTION), parts_text[-900:])
    draft = AttemptRequest(job_kind="executive_summary_draft", attempt=1, repository=paths.repository, module=paths.module,
                           paths=paths, ignore=IgnoreList(), inputs={**inputs, "slug": "executive-summary-draft"})
    draft_text = write_messages.build_follow_up_text(draft)
    harness.check("so does the executive summary draft", frame_batch_fixtures.SUMMARY in draft_text and "1:35 to 2:10" in draft_text, draft_text)


def test_list_batches_returns_the_summaries_with_their_spans():
    paths = fx.make_paths()
    context = WriteReadingContext(paths=paths, moments=[], point_starts=[], frame_items={}, frame_records={},
                                  read_chars=4000, notes_chars=100, read_budget=10, batches=BATCHES)
    text = write_reading_service.run_read(context, WriteReadingState(), "list_batches", {}).text
    harness.check("each batch, its span, its frames and its account", "00:00 to 01:35" in text and frame_batch_fixtures.SUMMARY in text
                  and "frames 0 to 3" in text and "01:35 to 02:10" in text, text)
    harness.check("offered as a reading tool", "list_batches" in write_actions.READ_TOOLS)
    empty = WriteReadingContext(paths=paths, moments=[], point_starts=[], frame_items={}, frame_records={},
                                read_chars=4000, notes_chars=100, read_budget=10)
    harness.check("a run with none says so", "No batch" in write_reading_service.run_read(
        empty, WriteReadingState(), "list_batches", {}).text)


def test_the_file_name_comes_from_the_output_contract():
    harness.check("03_batch_summaries.json is named once", output_contract.BATCH_SUMMARIES_FILE == "03_batch_summaries.json")
    paths = fx.make_paths()
    harness.check("an absent file is no batches yet", batch_summaries_service.read_batch_summaries(paths) == [])


def test_every_field_the_account_s_checks_require_is_asked_for_in_the_message():
    """A check whose field the message never asks for is a retry the run pays for, per batch: the owner, 2026-09-28,
    after `atmosphere` was checked and never asked for, and every batch paid an extra call. Built from the schema, so
    a section added to `# Batch` later fails here until both messages ask for it."""


    paths = fx.make_paths()
    batch = {"frames": [0, 1], "start": 0.0, "end": 12.0, "reason": "too short", "accepted": [],
               "spoken": [{"index": 0, "timestamp": 0.0, "transcriptCurrent": "a word"}]}
    request = AttemptRequest(job_kind="batch_summary", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-000-001", "batch": batch})
    alone = _all_text(frames_processing_messages.build_batch_summary_messages(request))
    group_closing = frames_processing_messages.SUMMARY_WRITE
    for heading in frame_record.BATCH_SECTIONS.values():
        harness.check(f"the account-alone message asks for `## {heading}`", f'"## {heading}"' in alone, alone[-600:])
        harness.check(f"and the group's closing asks for `## {heading}`", f'"## {heading}"' in group_closing,
                      group_closing)


def test_every_limit_the_account_s_checks_hold_is_stated_where_the_model_is_asked():
    """The owner's rule of 2026-09-28: a check whose limit the instruction never states is a retry the run pays for,
    per batch. Measured three times in one hour: the field nobody asked for, then the sentence count nobody stated,
    each costing about 70 seconds a batch. The numbers come from the validator, so the two cannot drift."""


    paths = fx.make_paths()
    batch = {"frames": [0, 1], "start": 0.0, "end": 12.0, "reason": "too short", "accepted": [],
               "spoken": [{"index": 0, "timestamp": 0.0, "transcriptCurrent": "a word"}]}
    request = AttemptRequest(job_kind="batch_summary", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-000-001", "batch": batch})
    alone = _all_text(frames_processing_messages.build_batch_summary_messages(request))
    for limit in (item_validator.SUMMARY_MIN_SENTENCES, item_validator.SUMMARY_MAX_SENTENCES,
                  item_validator.SUMMARY_MAX_WORDS):
        harness.check(f"the group's closing states the limit {limit}", str(limit) in frames_processing_messages.SUMMARY_WRITE,
                      frames_processing_messages.SUMMARY_WRITE)
        harness.check(f"and the account-alone message states {limit}", str(limit) in alone, alone[-500:])


def test_every_follow_up_of_a_batch_hears_what_was_said_before_its_first_frame():
    """A critique judged a point said just before the batch a mistake, and the repair, the open questions and the
    answers went on from it, none of them handed what the first pass had heard before the batch."""

    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-004.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((90, 160, 3), 200, dtype=numpy.uint8))
    items = [{"index": 5, "timestamp": 50.0, "path": "frames/frame-004.jpg", "transcriptBefore": "too late"},
             {"index": 4, "timestamp": 40.0, "path": "frames/frame-004.jpg",
              "transcriptBefore": "Jonas suggests a counter named tries."}]
    batch = {"frames": [4, 5], "start": 40.0, "end": 60.0, "accepted": [], "items": items, "questions": ["who?"],
             "spoken": [{"index": 4, "timestamp": 40.0, "transcriptCurrent": "ok, tries it is"}]}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-005", "batch": batch})
    for name, build in (("critique", frames_processing_messages.build_critique_messages),
                        ("repair", frames_processing_messages.build_second_pass_messages),
                        ("open questions", frames_processing_messages.build_open_questions_messages),
                        ("answers", frames_processing_messages.build_answer_questions_messages)):
        text = _all_text(build(request))
        harness.check(f"the {name} hears what was said before the batch's first frame",
                      "Jonas suggests a counter named tries." in text and "too late" not in text, text[:400])


def test_every_follow_up_of_a_pdf_batch_is_told_its_frames_are_pages():
    """A PDF's critique was handed no item, judged its pages' notes as a meeting's, looped and was cut (pdf-01)."""

    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-004.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((90, 160, 3), 200, dtype=numpy.uint8))
    for medium, is_told in ((output_contract.PDF_MEDIUM, True), (None, False)):
        items = [{"index": 4, "timestamp": 0.0, "path": "frames/frame-004.jpg", **({"medium": medium} if medium else {})}]
        batch = {"frames": [4], "start": 0.0, "end": 60.0, "accepted": [], "items": items, "questions": ["who?"],
                 "spoken": [{"index": 4, "timestamp": 0.0, "transcriptCurrent": "A refund is split in three."}]}
        request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                                 paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-004", "batch": batch})
        for name, build in (("critique", frames_processing_messages.build_critique_messages),
                            ("repair", frames_processing_messages.build_second_pass_messages),
                            ("open questions", frames_processing_messages.build_open_questions_messages),
                            ("answers", frames_processing_messages.build_answer_questions_messages)):
            text = _all_text(build(request))
            harness.check(f"the {name} of a {medium or 'video'} batch {'is' if is_told else 'is not'} told of pages",
                          ("This source is a PDF document" in text) == is_told)


def test_the_critique_and_the_repair_are_told_which_frame_is_a_later_step_of_a_build():
    """The critique, not told that a frame was a build's second step, called its outline of only what the step adds a
    mistake and asked for a whole outline; the first pass had been told, and wrote the step's form right."""

    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-004.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((90, 160, 3), 200, dtype=numpy.uint8))
    items = [{"index": 4, "slug": "frame-004", "timestamp": 40.0, "path": "frames/frame-004.jpg"},
             {"index": 5, "slug": "frame-005", "stepOf": "frame-004", "timestamp": 50.0, "path": "frames/frame-004.jpg"},
             {"index": 6, "slug": "frame-006", "timestamp": 60.0, "path": "frames/frame-004.jpg"}]
    batch = {"frames": [4, 5, 6], "start": 40.0, "end": 70.0, "accepted": [], "items": items, "spoken": []}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-006", "batch": batch})
    for name, build in (("critique", frames_processing_messages.build_critique_messages),
                        ("repair", frames_processing_messages.build_second_pass_messages)):
        text = _all_text(build(request))
        harness.check(f"the {name} names frame 5 as step 2 of 2 of frame 4's screen, and frames 4 and 6 as plain frames",
                      "Frame 5, at 50.0 s, step 2 of 2 of the screen first shown as frame 4" in text
                      and "Frame 4, at 40.0 s:" in text and "Frame 6, at 60.0 s:" in text, text[-900:])


def test_the_open_questions_and_answers_hear_the_start_of_what_is_said_after_the_batch():
    """Most questions a batch left open asked what came next, and minutes went on a sentence the next frame finishes."""

    paths = fx.make_paths()
    frame = paths.output_root / "frames" / "frame-004.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), numpy.full((90, 160, 3), 200, dtype=numpy.uint8))
    batch = {"frames": [4], "start": 40.0, "end": 40.0, "accepted": [], "questions": ["what does it print?"],
             "items": [{"index": 4, "timestamp": 40.0, "path": "frames/frame-004.jpg", "transcriptBefore": ""}],
             "spoken": [{"index": 4, "timestamp": 40.0, "transcriptCurrent": "so if we run"}],
             "speechAfter": "it, it prints one, two, three"}
    request = AttemptRequest(job_kind="open_questions", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frame-004", "batch": batch})
    for name, build in (("open questions", frames_processing_messages.build_open_questions_messages),
                        ("answers", frames_processing_messages.build_answer_questions_messages)):
        harness.check(f"the {name} hear what is said after the batch",
                      "it, it prints one, two, three" in _all_text(build(request)), "")


def test_a_follow_up_s_batch_keeps_each_frame_s_earlier_speech_and_the_next_frame_s_first_words():
    """The follow-ups' batch dropped `transcriptBefore`, so the speech before a batch, though rendered, was never there."""

    ctx = frame_batch_fixtures.load_run_context(count=3)
    ctx.store.get_item("frame-001")["transcriptBefore"] = "Jonas suggests tries"
    ctx.store.get_item("frame-002")["transcriptCurrent"] = " ".join(["word"] * 80)
    batch = process_frames_node._describe_batch(ctx, [ctx.store.get_item("frame-001")], "", "")
    harness.check("the frame keeps its earlier speech", batch["items"][0]["transcriptBefore"] == "Jonas suggests tries",
                  str(batch["items"]))
    harness.check("the next frame's first 60 words, marked as going on",
                  batch["speechAfter"] == " ".join(["word"] * 60) + " …", batch["speechAfter"][-30:])
    last = process_frames_node._describe_batch(ctx, [ctx.store.get_item("frame-002")], "", "")
    harness.check("nothing after the last frame", last["speechAfter"] == "", last["speechAfter"])


def test_a_batch_title_names_its_own_topic_once():
    """The owner: a batch's title is focused on the topic of its frames, never the meeting's, and as unique as possible.
    Missing, too long, the recording's name, or an earlier batch's title again, each fires its own code."""

    paths = fx.make_paths(module="gateway-tokens.mp4")
    account = {"batch_summary": frame_batch_fixtures.SUMMARY, "atmosphere": frame_batch_fixtures.ATMOSPHERE}
    batch_summaries_service.save_batch_summary(paths, {"slug": "frames-000-001", "frames": [0, 1], "start": 0.0,
                                                       "end": 12.0, "title": "Refund split limits", "summary": frame_batch_fixtures.SUMMARY,
                                                       "source": "batch"})

    def codes(title: str,
              slug: str = "frames-002-003") -> set[str]:
        record_file_service.write_batch_record(paths, f"{slug}-batch-summary", {"batch_title": title, **account})
        return {code for code in harness.codes(item_validator.validate_batch_summary_record(paths, f"{slug}-batch-summary"))
                if code.startswith("BATCH_TITLE")}

    harness.check("a title of its own passes", codes("Token exchange at the gateway") == set())
    harness.check("no title is BATCH_TITLE_MISSING", codes("") == {"BATCH_TITLE_MISSING"})
    harness.check("one word is BATCH_TITLE_SHAPE", codes("Tokens") == {"BATCH_TITLE_SHAPE"})
    harness.check("twelve words pass: every title refused at a limit of 10 had 11 or 12 (2026-10-15)",
                  codes("How the gateway exchanges one token for another at every partner call today") == set())
    harness.check("fifteen words are BATCH_TITLE_SHAPE",
                  codes("How the gateway exchanges one token for another at every single partner call on each "
                        "day") == {"BATCH_TITLE_SHAPE"})
    harness.check("an earlier batch's title is BATCH_TITLE_NOT_UNIQUE", codes("Refund split limits") == {"BATCH_TITLE_NOT_UNIQUE"})
    harness.check("one word changed is still BATCH_TITLE_NOT_UNIQUE", codes("Refund split limit") == {"BATCH_TITLE_NOT_UNIQUE"})
    harness.check("the batch's own earlier answer is no other batch", codes("Refund split limits", "frames-000-001") == set())
    harness.check("nor is a later repair round of it", codes("Refund split limits", "frames-000-001-repair-2") == set())
    meeting = " ".join(word for word in paths.module.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").split()
                       if not any(character.isdigit() for character in word))
    harness.check("the recording's name is BATCH_TITLE_GENERIC", "BATCH_TITLE_GENERIC" in codes(meeting), meeting)


def test_the_earlier_titles_reach_the_next_batchs_request():
    paths = fx.make_paths()
    batch_summaries_service.save_batch_summary(paths, {"slug": "frames-000-001", "frames": [0, 1], "start": 0.0,
                                                       "end": 12.0, "title": "Refund split limits", "summary": frame_batch_fixtures.SUMMARY,
                                                       "source": "batch"})
    request = AttemptRequest(job_kind="batch_summary", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-002-003"})
    harness.check("the request lists the earlier titles not to repeat",
                  "none to be repeated:\n- Refund split limits" in frames_processing_messages.render_earlier_titles(request))


def test_the_batch_just_before_is_named_among_the_earlier_titles_defect():
    # A demo going on over two batches titled the second one with the first's topic, and the title was refused. The request
    # now names the batch just before (by its frames, whatever order the batches were stored in) and what to title instead
    paths = fx.make_paths()
    for slug, frames, title in (("frames-000-001", [0, 1], "Refund split limits"), ("frames-004-005", [4, 5], "Partner token exchange"),
                                ("frames-002-003", [2, 3], "Gateway retry budget")):
        batch_summaries_service.save_batch_summary(paths, {"slug": slug, "frames": frames, "start": 0.0, "end": 12.0,
                                                           "title": title, "summary": frame_batch_fixtures.SUMMARY,
                                                           "source": "batch"})
    request = AttemptRequest(job_kind="batch_summary", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-006-007"})
    text = frames_processing_messages.render_earlier_titles(request)
    harness.check("the batch ending last before frame 6 is named the one just before",
                  "- Partner token exchange (the batch just before this one" in text and text.count("just before") == 1, text)


def test_read_notes_takes_a_chapter_given_as_text():
    """A host's tool parser sent every chapter as text ("1"), and every chapter read empty (2026-10-03)."""

    context = WriteReadingContext(paths=fx.make_paths(), moments=[], point_starts=[0.0, 50.0], frame_items={},
                                  frame_records={}, read_chars=4000, notes_chars=100, read_budget=10,
                                  note_sections=[(0.0, "Gateway opening"), (60.0, "Retry budget")])
    as_text = write_reading_service.run_read(context, WriteReadingState(), "read_notes", {"chapter": "2"}).text
    as_number = write_reading_service.run_read(context, WriteReadingState(), "read_notes", {"chapter": 2}).text
    harness.check("chapter \"2\" reads what chapter 2 reads", "Retry budget" in as_text
                  and "Gateway opening" not in as_text and as_text.split("] ", 1)[-1] == as_number.split("] ", 1)[-1],
                  f"{as_text!r} / {as_number!r}")


def test_a_chapter_read_given_as_text_counts_as_read():
    """The check that every chunk of a chapter was read compared the logged "1" with 1 and refused a chapter read
    whole (2026-10-03)."""

    harness.check("text and number name the same chapter", reading_tool_utils.read_chapter_number("1") == 1
                  and reading_tool_utils.read_chapter_number(2) == 2 and reading_tool_utils.read_chapter_number(None) is None)


def test_frames_no_batch_account_covers_get_a_stand_in_when_the_phase_ends():
    """Frames accepted in a batch that then blocked, and asked again by --worker or alone, were left with no account:
    the agenda of phase 4 is built from the accounts, so a third of a meeting would be missing from it."""

    ctx = frame_batch_fixtures.load_run_context(count=5)
    for index in range(5):
        item = ctx.store.get_item(f"frame-{index:03d}")
        frame_records_service.save_frame_record(ctx.paths, item, {**frame_batch_fixtures.FRAME, "frame": index,
                                                                    "third_person_description": f"Step {index} is shown."}, [])
    batch_summaries_service.save_batch_summary(ctx.paths, {"slug": "frames-000-001", "frames": [0, 1], "start": 0.0,
                                                           "end": 12.0, "summary": frame_batch_fixtures.SUMMARY, "source": "batch"})
    process_frames_node._cover_unaccounted_frames(ctx)
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("the frames after the account get one stand-in, from their own descriptions, in order",
                  [(entry["frames"], entry["source"]) for entry in stored]
                  == [([0, 1], "batch"), ([2, 3, 4], "third_person_description")]
                  and stored[1]["summary"] == "Step 2 is shown. Step 3 is shown. Step 4 is shown.", str(stored))


def test_the_batch_closing_asks_for_the_sentences_the_check_requires_not_more():
    """Told "at least five" while the check passes three, a model padded its account."""

    closing = frames_processing_messages.GROUP_CLOSING_MESSAGE.template
    harness.check("the closing names the check's own minimum",
                  f"at least {item_validator.SUMMARY_MIN_SENTENCES} sentences" in closing and "at least five sentences" not in closing,
                  closing[closing.find("Story of the batch"):][:200])


def test_an_unreadable_batch_answer_is_named_as_unreadable_not_as_missing():
    paths = fx.make_paths()
    path = record_file_service.build_record_path(paths, "frames-000-001-summary")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Batch\n\n## No such section\nwords\n", encoding="utf-8")
    try:
        record_file_service.read_batch_record(paths, "frames-000-001-summary")
        harness.check("the fixture is unreadable", False, "it was read")
        return
    except ValueError:
        pass
    codes = harness.codes(item_validator.validate_batch_summary_record(paths, "frames-000-001-summary"))
    harness.check("the unreadable answer has its own finding, and only it", codes == {"BATCH_SUMMARY_UNPARSEABLE"},
                  str(codes))


def test_a_batch_account_asked_alone_keeps_the_small_batch_minimum():
    paths = fx.make_paths()
    record_file_service.write_batch_record(paths, "frames-000-001-summary", {
        "batch_title": "Retry budget settings", "batch_summary": "The presenter opens the retry settings. The budget is set to three.",
        "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frames-000-001"),
                                    {"response": {"outcome": "completed", "parsed": {}, "finishReason": "stop"}})
    codes = harness.codes(item_validator.validate_batch_summary(paths, "frames-000-001", "frames-000-001-summary", 2))
    harness.check("two sentences for a batch of two frames pass", "BATCH_SUMMARY_SHAPE" not in codes, str(codes))


def test_a_frame_of_a_batch_whose_notes_could_not_be_read_says_why():
    paths = fx.make_paths()
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frames-000-001"),
                                    {"response": {"outcome": "completed", "finishReason": "stop",
                                                  "parsed": {"unreadable": {"1": "no `## Screen` heading"}}}})
    codes = harness.codes(item_validator.validate_frame_group(paths, "frames-000-001", ["frame-001"]))
    harness.check("the frame's unread notes are named, never 'no notes were written'",
                  codes == {"FRAME_NOTES_UNREADABLE"}, str(codes))


def test_a_follow_up_s_batch_carries_its_span_and_what_marks_a_frame_as_a_build_step_or_text_only():
    """A follow-up was told the batch ran from 0.0 s to 0.0 s, never heard a frame was a step of a build, and was sent a
    frame the first call read as text as its image: the batch handed over held no span and no `slug`, `stepOf` or
    `isTextOnly`."""

    ctx = frame_batch_fixtures.load_run_context(count=3)
    ctx.store.get_item("frame-001")["stepOf"] = "frame-000"
    ctx.store.get_item("frame-001")["isTextOnly"] = True
    covered = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-001")]
    batch = process_frames_node._describe_batch(ctx, covered, "", "")
    start, end = process_frames_node._compute_batch_span(ctx, covered)
    harness.check("the batch carries its first and last second", (batch.get("start"), batch.get("end")) == (start, end),
                  str((batch.get("start"), batch.get("end"))))
    harness.check("a frame item carries its slug", [item.get("slug") for item in batch["items"]] == ["frame-000", "frame-001"],
                  str(batch["items"]))
    harness.check("a step of a build says so", batch["items"][1].get("stepOf") == "frame-000", str(batch["items"][1]))
    harness.check("a frame marked text-only says so", batch["items"][1].get("isTextOnly") is True, str(batch["items"][1]))


def test_a_pdf_batch_names_its_text_the_page_s_and_its_frames_pages():
    """Labelled as speech, a PDF page's text left the worker reasoning over whether a wiki page's comments were spoken
    (pdf-01)."""

    ctx = frame_batch_fixtures.load_run_context(count=1)
    for medium, is_pdf in ((output_contract.PDF_MEDIUM, True), (None, False)):
        item = {**ctx.store.get_item("frame-000"), "transcriptCurrent": "A refund is split in three.", "page": 1,
                **({"medium": medium} if medium else {})}
        request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                                 paths=ctx.paths, ignore=ctx.ignore,
                                 inputs={"slug": "frames-000-000", "items": [item], "previousRecords": []})
        text = _all_text(frames_processing_messages.build_frame_batch_messages(request))
        label = "pdf" if is_pdf else "video"
        harness.check(f"a {label} frame's text is named {'the page' if is_pdf else 'speech'}",
                      ("This page's own text, read from the file:" in text) == is_pdf
                      and ("Spoken from this frame until the next one:" in text) != is_pdf, text[:300])
        harness.check(f"a {label} frame is {'a page' if is_pdf else 'a time'}",
                      ("Frame 0, page 1." in text) == is_pdf, text[:300])


def test_the_first_batch_s_closing_ends_with_the_titles_line_too_defect():
    # Defect: a recording's first batch stopped after its last frame with no "# Batch" on two talks, and the account
    # was asked alone; every later batch, whose closing ends listing the titles taken, wrote it. Fix: the first ends alike.
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-002", "items": items, "previousRecords": []})
    closing = frames_processing_messages.build_frame_batch_messages(request)[-1].content[0]["text"]
    harness.check("the first batch's closing ends naming no earlier title and asking for its # Batch",
                  "none yet, this is the first batch." in closing
                  and closing.rstrip().endswith('Write its "# Batch" before the first frame\'s notes.'),
                  closing[-300:])


def test_a_batch_s_follow_ups_hear_a_frame_noted_earlier_between_its_frames_defect():
    # A resumed batch held frames 1 and 3, frame 2 accepted in an earlier batch, and its follow-ups never heard frame
    # 2's words, which held the answer to a question; they now hear every frame between the batch's first and last
    ctx = frame_batch_fixtures.load_run_context(count=4)
    ctx.store.get_item("frame-002")["transcriptCurrent"] = "AcmeSync splits every file into chunks of four megabytes"
    batch = process_frames_node._describe_batch(ctx, [ctx.store.get_item("frame-001"), ctx.store.get_item("frame-003")],
                                                "", "")
    heard = {entry["index"]: entry for entry in batch["spoken"]}
    harness.check("frame 2's words are heard, marked as noted earlier",
                  heard.get(2, {}).get("notedEarlier") is True and "four megabytes" in heard[2]["transcriptCurrent"],
                  str(batch["spoken"]))
    harness.check("the frame after the batch is not", 4 not in heard, str(sorted(heard)))
    text = frames_processing_messages._render_speech(sorted(batch["spoken"], key=lambda entry: entry["index"]))
    harness.check("the message names it noted in an earlier batch", "noted in an earlier batch" in text, text)


def test_the_batch_s_account_is_asked_before_the_first_frame_s_notes_defect():
    # Asked after the last frame, the account was left out on 16 batches of 9 runs, a line asking for it there did not
    # stop it, and each was asked again alone. Now the closing and the instructions ask for it first.
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-002", "items": items, "previousRecords": []})
    closing = " ".join(frames_processing_messages.build_frame_batch_messages(request)[-1].content[0]["text"].split())
    harness.check("the closing asks for the account first", 'Write "# Batch" FIRST, before the first frame' in closing
                  and "after its last frame" not in closing, closing[-600:])
    instruction = (Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "frames-processing.md").read_text(encoding="utf-8")
    harness.check("the instructions ask for it first", "written FIRST, before the first frame's notes" in instruction
                  and "After the last frame, write `# Batch`" not in instruction)
    batch = frame_markdown_utils.render_batch_document({"batch_title": "Gateway rollout plan",
                                                         "batch_summary": frame_batch_fixtures.BATCH_SUMMARY,
                                                         "atmosphere": frame_batch_fixtures.ATMOSPHERE})
    frame = frame_markdown_utils.render_frame_document({**frame_batch_fixtures.RECORD, "frame": 0})
    parsed = frame_markdown_utils.parse_answer(batch + "\n" + frame, is_addition=False)
    harness.check("an answer opening with its account reads both", parsed["batch"] is not None
                  and [record["frame"] for record in parsed["frames"]] == [0] and not parsed["errors"], str(parsed["errors"]))


def test_the_answer_s_shape_opens_with_the_batch_s_account_defect():
    # Told the frames' documents "in this order" first and only at the message's end to write the account first, the
    # worker opened with the frames and left the account out on 3 of 26 batches; the shape line opens with it now
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-002", "items": items, "previousRecords": []})
    closing = " ".join(frames_processing_messages.build_frame_batch_messages(request)[-1].content[0]["text"].split())
    shape = closing[closing.index("Answer once"):closing.index("in this order")]
    harness.check("the answer's shape names the account before the frames' documents",
                  shape.index('"# Batch"') < shape.index('"# Frame N'), shape)


def test_the_answers_step_is_handed_earlier_frames_lines_using_a_question_rare_word_defect():
    # A question an earlier batch's slide answered stayed open: the answers step read its own batch only (DEFECTS 268);
    # now it is handed the earlier frames' lines that use a rare word of the question
    from utils import earlier_notes_utils

    earlier = [{"index": 3, "facts": ["The ledger keeps a `bref` column, a reference to the order row."],
                "screen": '- slide: white\n  - item "[step, bref, total]"'},
               *({"index": number, "facts": [f"The ledger is checked at step {number}."], "screen": ""}
                 for number in range(4, 9))]
    lines = earlier_notes_utils.list_earlier_lines(["What is `bref` in the ledger?"], earlier)
    harness.check("the lines holding the rare word come back, and no line for a word five frames use",
                  [frame for frame, _line in lines] == [3, 3] and all("bref" in line for _frame, line in lines), str(lines))
    note = frames_processing_messages.render_earlier_lines({"earlierLines": lines})
    harness.check("the answers step's message lists them with their frame", "- frame 3: The ledger keeps" in note, note)
    harness.check("no line, no note", frames_processing_messages.render_earlier_lines({}) == "")


def test_a_question_s_plural_finds_the_earlier_line_with_its_singular_defect():
    # Defect: a question about "MCPs" was never handed the earlier line explaining "MCP", and stayed open for the
    # document (Lecture 7, DEFECTS 581). Fix: a word of four letters or more is matched without its final "s"
    from utils import earlier_notes_utils

    earlier = [{"index": 3, "facts": ["The Gateway Retry Protocol (GRP) connects the ledger to the refund queue."]},
               *({"index": number, "facts": [f"The ledger is checked at step {number}."]} for number in range(4, 9))]
    lines = earlier_notes_utils.list_earlier_lines(['What do the initials "GRPs" stand for?'], earlier)
    harness.check("the line explaining the singular comes back", [frame for frame, _line in lines] == [3], str(lines))


def test_one_frame_s_many_lines_leave_room_for_another_frame_s_best_line_defect():
    # Defect: one frame's lines filled the eight places a question is handed, and the later frame answering it was
    # never named. Fix: among lines sharing as many rare words, each frame's best line comes before any frame's next
    from utils import earlier_notes_utils

    earlier = [{"index": 2, "facts": [f"Gateway step {number} logs its retries." for number in range(9)]},
               {"index": 6, "facts": ["Ledger size: under 2 GB of RAM."]}]
    lines = earlier_notes_utils.list_earlier_lines(["Do the retries fit in RAM?"], earlier)
    harness.check("the later frame's line is among the eight", 6 in {frame for frame, _line in lines}
                  and len(lines) == earlier_notes_utils.MOST_LINES_A_QUESTION, str(lines))


def test_the_first_batch_is_told_its_title_is_never_the_talk_s_own_defect():
    # Defect: a talk's first batch, its title card and title slide, was titled with the talk's own name and refused as
    # naming the meeting. Fix: the first batch's closing says what such an opening is titled
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [ctx.store.get_item("frame-000"), ctx.store.get_item("frame-002")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-002", "items": items, "previousRecords": []})
    closing = frames_processing_messages.build_frame_batch_messages(request)[-1].content[0]["text"]
    harness.check("the closing names the opening's title shape", "never the talk's own title" in closing
                  and '"Opening: event and speaker"' in closing, closing[-400:])


def test_an_answer_that_ended_with_a_frame_and_its_account_left_out_goes_on_in_the_same_attempt_defect():
    # a batch of two was answered for its first frame only, its account left out, and both were asked again as a
    # refused attempt; now the same attempt asks the model to go on with what it left out, and reads both as one answer
    partial = fakes.frame_notes(frames=[{**frame_batch_fixtures.FRAME, "frame": 0}])
    rest = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.run_two_frame_batch(partial, rest)
    asked = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the second call asks to go on with what was left out",
                  "ended before it was whole" in asked and "frames 1" in asked and '"# Batch"' in asked, asked[-500:])
    harness.check("it is handed the answer so far as its own turn",
                  any(message.type == "ai" for message in ctx.model.calls[1]) if ctx.model.call_count > 1 else False)
    harness.check("both frames' notes are written", all(record_file_service.build_record_path(ctx.paths, slug).is_file()
                                                       for slug in ("frame-000", "frame-001")))
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("the account written on going on is the batch's", [entry["summary"] for entry in stored]
                  == [frame_batch_fixtures.SUMMARY], str(stored))
    attempts = list((ctx.run_dir / "frames_processing" / "frames-000-001").glob("attempt-*/validation.json"))
    refused = [path for path in attempts if not json.loads(path.read_text(encoding="utf-8"))["ok"]]
    harness.check("the batch's attempt is on disk", len(attempts) >= 1, str(attempts))
    harness.check("no attempt of the batch was refused", refused == [], str(refused))


def test_a_reply_that_stopped_inside_its_thinking_is_asked_for_its_answer_in_the_same_attempt_defect():
    # a batch's reply stopped on its own inside its thinking, a draft of its first frame and no answer, and was refused
    # as thinking only and asked again from nothing (DEFECTS 337); now the same attempt asks it for the answer
    thinking = "Frame 0 shows the Gateway slide. Let's structure Frame 0:\n# Frame 0 · slide\n## Facts\n- The Gateway"
    whole = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0},
                                             {**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.run_two_frame_batch(fakes.thinks_only(thinking), whole)
    asked = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the second call asks for the answer after the thinking", "ended inside your thinking" in asked,
                  asked[-400:])
    harness.check("it is handed its thinking as its own turn", any(message.type == "ai" and "Gateway slide" in
                  str(message.content) for message in ctx.model.calls[1]) if ctx.model.call_count > 1 else False)
    attempts = list((ctx.run_dir / "frames_processing" / "frames-000-001").glob("attempt-*/validation.json"))
    refused = [path for path in attempts if not json.loads(path.read_text(encoding="utf-8"))["ok"]]
    harness.check("the batch's attempt is on disk and none refused", len(attempts) >= 1 and refused == [],
                  str([json.loads(path.read_text(encoding="utf-8"))["findings"] for path in refused]))


def _drop_facts_of_frame_1(text: str) -> str:
    frame_1 = text.index("# Frame 1")
    start = text.index("## Facts", frame_1)
    end = text.index("\n## ", start + 1)

    return text[:start] + text[end + 1:]


def test_a_frame_whose_answer_left_its_facts_heading_out_is_written_again_in_the_same_attempt_defect():
    # Lecture 7's frame 85 left the whole "## Facts" heading out, read as no facts, and blocked its batch under zero
    # repair (DEFECTS 563); now the same attempt asks for that frame's document again, whole, and keeps it in its place
    # Frame 1 shows a screen of its own, so the facts check asks it for a fact
    frame_1 = {**frame_batch_fixtures.FRAME, "frame": 1, "facts": ["Gateway refunds over 500 euros need approval"],
               "screen": '- slide: white, the whole frame\n  - title "Gateway refunds"\n  - line "over 500 euros"\n'
                         '  - line "need approval"\n  - line "by the finance team"'}
    whole = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, frame_1)
    partial = AIMessage(content=_drop_facts_of_frame_1(str(whole.content)), response_metadata=whole.response_metadata)
    again = fakes.frame_notes(frames=[frame_1])
    said = "Gateway refunds over five hundred euros now need the approval of the finance team before they are paid."
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(partial, again), count=2, speech={1: said})
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    asked = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the second call asks for frame 1 again with its facts",
                  "ended before it was whole" in asked and "frames 1 again" in asked and '"## Facts"' in asked,
                  asked[-500:])
    is_written = record_file_service.build_record_path(ctx.paths, "frame-001").is_file()
    stored = record_file_service.read_frame_record(ctx.paths, "frame-001") if is_written else {}
    harness.check("frame 1's notes hold the fact written on going on",
                  any("500 euros" in fact for fact in stored.get("facts") or []), str(stored.get("facts")))
    attempts = list((ctx.run_dir / "frames_processing" / "frames-000-001").glob("attempt-*/validation.json"))
    refused = [path for path in attempts if not json.loads(path.read_text(encoding="utf-8"))["ok"]]
    harness.check("the batch's attempt is on disk and none refused", len(attempts) >= 1 and refused == [],
                  str([json.loads(path.read_text(encoding="utf-8"))["findings"] for path in refused]))


def test_a_frame_written_with_no_fact_on_new_text_is_asked_for_its_facts_in_the_same_attempt_defect():
    # a dense slide written "## Facts" with nothing under it was refused and blocked its batch under zero repair, its
    # facts written only on the next attempt (mariadb frames 29 to 38); now the same attempt asks for them
    frame_1 = {**frame_batch_fixtures.FRAME, "frame": 1, "facts": ["Gateway refunds over 500 euros need approval"],
               "screen": '- slide: white, the whole frame\n  - title "Gateway refunds"\n  - line "over 500 euros"\n'
                         '  - line "need approval"\n  - line "by the finance team"'}
    partial = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_1, "facts": []})
    said = "Gateway refunds over five hundred euros now need the approval of the finance team before they are paid."
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(partial, fakes.frame_notes(frames=[frame_1])), count=2,
                                                speech={1: said})
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    asked = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the second call asks for frame 1 again with its facts", "frames 1 again" in asked, asked[-500:])
    is_written = record_file_service.build_record_path(ctx.paths, "frame-001").is_file()
    stored = record_file_service.read_frame_record(ctx.paths, "frame-001") if is_written else {}
    harness.check("frame 1's notes hold the fact written on going on",
                  any("500 euros" in fact for fact in stored.get("facts") or []), str(stored.get("facts")))


def test_a_frame_outlined_as_another_screen_is_asked_for_again_in_the_same_attempt_defect():
    # A terminal frame was outlined as another frame's room view, quoting none of its words, and passed every check
    # (DEFECTS 569); now the same attempt asks for that frame's document again, written from its own image
    shown = ["Gateway refund ledger export finished", "Exported twelve thousand records into the archive bucket",
             "Checksum verified against nightly snapshot"]
    elsewhere = {**frame_batch_fixtures.FRAME, "frame": 1,
                 "screen": '- room: lecture hall\n  - board "Agent harness reusable prompts"\n  - board "parallel agents'
                           ' worktrees"\n  - screen "Claude Code version banner prompt"'}
    right = {**frame_batch_fixtures.FRAME, "frame": 1, "screen": "- terminal: the whole frame\n"
             + "\n".join(f'  - line "{line}"' for line in shown)}
    partial = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, elsewhere)
    saved = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: shown if slug == "frame-001" else []
    try:
        ctx = frame_batch_fixtures.load_run_context(fakes.scripted(partial, fakes.frame_notes(frames=[right])), count=2)
        with fx.critique_switches():
            frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    finally:
        item_validator.read_screen_lines = saved
    asked = _all_text(ctx.model.calls[1]) if ctx.model.call_count > 1 else ""
    harness.check("the second call asks for frame 1 again from its own image",
                  "frames 1 again" in asked and "frame's own image" in asked, asked[-500:])


def test_a_speakers_words_written_in_brackets_are_accepted_with_no_second_call_defect():
    # A speaker's "wait, no no" written in brackets outside quote marks was refused as the worker's own thinking and
    # blocked the batch under zero repair; now the thinking-aloud check is removed, and the answer stands as written
    bracketed = {**frame_batch_fixtures.FRAME, "frame": 1,
                 "speech_visual_alignment": "The speaker interrupts (wait no no), reacting to the refund table."}
    answer = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, bracketed)
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(answer), count=2)
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    asked_again = [_all_text(call)[-500:] for call in ctx.model.calls[1:] if "frames 1 again" in _all_text(call)]
    harness.check("no later call asks for frame 1 again", asked_again == [], str(asked_again))
    attempts = list((ctx.run_dir / "frames_processing" / "frames-000-001").glob("attempt-*/validation.json"))
    refused = [path for path in attempts if not json.loads(path.read_text(encoding="utf-8"))["ok"]]
    harness.check("the batch's attempt passed, nothing refused", len(attempts) >= 1 and refused == [],
                  str([json.loads(path.read_text(encoding="utf-8"))["findings"] for path in refused]))


def test_a_continuation_that_writes_a_frame_again_keeps_only_what_was_missing_defect():
    # asked to go on, a model can start its answer over: frame 0 written twice was refused as unreadable; now the
    # continuation's documents of parts the answer already held are left out
    partial = fakes.frame_notes(frames=[{**frame_batch_fixtures.FRAME, "frame": 0}])
    again = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0},
                                             {**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.run_two_frame_batch(partial, again)
    attempts = list((ctx.run_dir / "frames_processing" / "frames-000-001").glob("attempt-*/validation.json"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in attempts]
    harness.check("the attempt passed, nothing written twice", bool(reports) and all(report["ok"] for report in reports),
                  str([report["findings"] for report in reports]))


def test_the_critique_and_the_repair_are_told_the_markdown_block_is_the_quote_defect():
    # a critique asked the outline to repeat a table's cells that ## As markdown wrote whole, and the repair copied
    # them by hand with misreads; both are now told a text the block writes counts as quoted
    paths = fx.make_paths()
    batch = {"frames": [4], "start": 0.0, "end": 60.0, "accepted": [], "items": [], "questions": ["who?"],
             "spoken": [{"index": 4, "timestamp": 0.0, "transcriptCurrent": "The table lists the files."}]}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-004", "batch": batch})
    critique = " ".join(frames_processing_messages.build_critique_messages(request)[0].content.split())
    repair = " ".join(frames_processing_messages.build_second_pass_messages(request)[0].content.split())
    harness.check("the critique is told a text the markdown block writes counts as quoted",
                  "Text written under `## As markdown`** counts as quoted" in critique)
    harness.check("and the repair answers a rule asking for a second copy by nothing",
                  "already writes whole is answered by nothing" in repair)


def test_the_critique_and_the_repair_are_told_a_name_only_heard_is_no_fact_as_heard_defect():
    # a critique asked for a name the transcript misheard, quoting it, and the repair wrote it as a fact; both are now
    # told a name only the speech gives goes under open questions as heard, unless plainly a known product misheard
    paths = fx.make_paths()
    batch = {"frames": [4], "start": 0.0, "end": 60.0, "accepted": [], "items": [], "questions": ["who?"],
             "spoken": [{"index": 4, "timestamp": 0.0, "transcriptCurrent": "There is a version called Jonty Gateway."}]}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-004", "batch": batch})
    critique = frames_processing_messages.build_critique_messages(request)[0].content
    repair = frames_processing_messages.build_second_pass_messages(request)[0].content
    harness.check("the critique is told a rule asking a heard name into the facts is wrong",
                  "into\n  the facts as the transcript writes it is wrong" in critique
                  or "the facts as the transcript writes it is wrong" in " ".join(critique.split()))
    harness.check("and the repair keeps it under open questions whatever a rule asks",
                  "as heard, whatever a critique rule asks. A name only the speech gives" in " ".join(repair.split()))


def test_the_critique_is_told_the_step_line_names_no_count_defect():
    # a critique called a later step's line wrong for lacking "of 2", its instruction having named the line "step k of
    # n"; it is now told the line its message gives, which names no count of steps
    paths = fx.make_paths()
    batch = {"frames": [4], "start": 0.0, "end": 60.0, "accepted": [], "items": [], "questions": ["who?"],
             "spoken": [{"index": 4, "timestamp": 0.0, "transcriptCurrent": "The card adds the repository."}]}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "frames-004-004", "batch": batch})
    critique = " ".join(frames_processing_messages.build_critique_messages(request)[0].content.split())
    harness.check("the step line is the message's, with no count of steps",
                  '"- step <k> of the screen of frame <first>: ...", with no count of steps' in critique
                  and '"step k of n" line' not in critique)


def test_the_batch_account_is_read_under_its_coming_heading_defect():
    # Batches left "## What happens" out of their frames, the account's own heading opening with that name
    # (DEFECTS 310); the account is to be renamed "## Story of the batch", and the parser reads it already
    from utils import frame_markdown_utils

    text = ("# Batch\n\n## Title\nGateway token flow\n\n## Story of the batch\nThe speaker shows how AcmeSync fetches "
            "the token. Jonas asks about the queue.\n\n## Atmosphere\ncalm\n")
    batch = frame_markdown_utils.parse_answer(text, is_addition=False).get("batch") or {}
    harness.check("the account is read under the new heading", batch.get("batch_summary", "").startswith("The speaker"),
                  str(batch))


def test_the_critique_is_told_a_text_quoted_in_the_outline_and_the_block_is_no_mistake_defect():
    # A critique called a comment quoted both in the outline and in the markdown block a mistake, and the repair
    # stripped the outline's quotes; the critique is now told the second copy costs a reader nothing
    from prompts import system_prompt_builder

    text = system_prompt_builder.build_system_prompt(frames_processing_messages.CRITIQUE_PHASE, {})
    harness.check("the critique's instruction says a text quoted twice is no mistake",
                  "quoted both under `## As markdown` and under `## Screen`** is no mistake" in " ".join(text.split()))


def test_a_subject_the_speech_garbles_far_from_the_screens_spelling_is_named_defect():
    # The speech model wrote a talk's subject as another word 31 times and the notes carried it; one or two letters off
    # is all the program respells (DEFECTS 339). Said 20 times or more and on no screen, beside a screen word alike and
    # no dictionary word, it is named to the worker
    from utils import garbled_name_utils

    speech = "Jonas joined the acmesyncing project early."
    transcript = " ".join(["The acmesyncing library grows."] * 21 + ["Ledger changes."] * 3)
    screens = ['- slide\n  - title "acmesnc library"', '- slide\n  - text "Ledger change"']
    harness.check("the garbled subject is named with the screen's spelling",
                  garbled_name_utils.list_garbled_names(speech, transcript, screens) == [("acmesyncing", "acmesnc")],
                  str(garbled_name_utils.list_garbled_names(speech, transcript, screens)))
    harness.check("a word said a few times is left alone",
                  garbled_name_utils.list_garbled_names("the ledger changes", transcript, screens) == [])


def test_the_batch_account_is_asked_under_a_heading_no_frame_section_shares_defect():
    # Defect: the batch's account was asked under "## What happens in the batch", which opens with a frame section's
    # name, and batches left "## What happens" out of their frames (DEFECTS 310). Fix: it is "## Story of the batch".
    from pathlib import Path
    from schemas import frame_record
    folder = Path(__file__).resolve().parents[2] / "agent-context"
    written = "\n".join(path.read_text(encoding="utf-8") for path in folder.rglob("*.md"))
    harness.check("the account's heading opens with no frame section's name",
                  frame_record.BATCH_SECTIONS["batch_summary"] == "Story of the batch")
    harness.check("no instruction names the old heading", "What happens in the batch" not in written)

