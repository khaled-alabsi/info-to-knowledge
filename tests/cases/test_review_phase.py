"""Phase 5, the review, REQUIREMENTS.md R16.

A worker that did not write the finished document reads it as a critic, lists what a reader still cannot know as
todos, then resolves them one follow-up at a time in the same conversation, reading the sources it chooses. The
program checks every answer, applies each accepted edit to the document, keeps the unreviewed copy, adds what the
sources did not answer to the document's open points, and writes the report. Tested here: the edits as pure text
work, every check on its own defect and on the lookalike it must spare, the reading tools, the instruction and the
follow-up messages, the whole phase driven by a scripted model, one session across follow-ups, the resume, the
refusal of a missing input, the dry run and the routing.
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest import mock

import cv2
import numpy
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from support import fakes
from support import harness
from support import merge_inputs
from support import fixtures as fx

import config as app_config
from graph import graph_state
from graph import routing
from graph.nodes import review_document_node
from models import output_contract
from models.review_reading_context import ReviewReadingContext
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from models.write_reading_state import WriteReadingState
from prompts import system_prompt_builder
from prompts import review_messages
from schemas import review_actions
from services import call_log_service
from services import document_folder_service
from services import frame_records_service
from services import host_window_service
from services import document_file_service
from services import phase_file_service
from services import review_conversation_service
from services import review_file_service
from services import review_reading_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import document_edit_utils
from utils import json_store_utils
from utils import clock_utils
from utils import reading_tool_utils
from utils import review_report_utils
from validators import review_validator

VIDEO = "meeting-20260303.mp4"
BODY = """# Orders ship from the new warehouse from March

## Executive summary

Orders ship from the new warehouse from March.

- The order form needs two fields.

## Agenda

1. [Ordering](#ordering): Walks through the order form

## Ordering

The order form needs the customer number and the delivery date. Raising the order limit removes the approval step. Figure 1 has both fields.

![Screenshot of the order form](resources/figure-01.png)

*Figure 1. The order form needs only two fields.*

## Decisions and open points

### Decisions

- Orders ship from the new warehouse from March.

### Open points

- Nothing was left open.
"""
ENTRIES = [{"id": "F0.1", "kind": "facts", "text": "The order form needs the customer number.", "frames": [0],
            "timestamps": [4.0], "source": "frame"},
           {"id": "S0001", "kind": "speech", "text": "Only a team lead may raise the order limit.", "start": 6.0,
            "end": 8.0, "timestamps": [6.0], "source": "transcript"},
           {"id": "F1.1", "kind": "facts", "text": "Raising the order limit removes the approval step.",
            "frames": [1], "timestamps": [9.0], "source": "frame"},
           {"id": "S0002", "kind": "speech", "text": "We will switch the warehouse over in spring.", "start": 10.0,
            "end": 12.0, "timestamps": [10.0], "source": "transcript"}]
RECORDS = [{"slug": "frame-000", "index": 0, "timestamp": 4.0, "facts": ["The order form needs the customer number."],
            "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
            "third_person_description": "The presenter opens the order form.",
            "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []},
           {"slug": "frame-001", "index": 1, "timestamp": 9.0,
            "facts": ["Raising the order limit removes the approval step."],
            "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
            "third_person_description": "The presenter points at the limit field.",
            "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}]
QUOTE = "Raising the order limit removes the approval step."
GAP = {"kind": "gap", "section": "Ordering", "quote": QUOTE, "missing": "who may raise the order limit",
       "why_it_matters": "a reader cannot tell whether they may raise it themselves",
       "where": "the transcript just before the limit is shown"}
QUESTION = {"kind": "open_question", "section": "Executive summary",
            "quote": "Orders ship from the new warehouse from March.", "missing": "from which day in March",
            "why_it_matters": "a reader cannot plan the switch", "where": "not in the sources"}
RESOLVED = {"status": "resolved", "section": "Ordering", "replaces": "", "after": QUOTE,
            "sentences": [{"text": "Only a team lead may raise the order limit.", "transcript_ids": ["S0001"],
                           "frames": [], "entry_ids": []}], "searches": [], "question": ""}
UNRESOLVED = {"status": "unresolved", "section": "", "replaces": "", "after": "", "sentences": [],
              "searches": ["the transcript for 'March'", "the notes of both frames"],
              "question": "From which day in March do orders ship from the new warehouse?"}


def _write_frame(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), numpy.full((40, 60, 3), 200, numpy.uint8))


def _build_finished_run(tmp: Path) -> RunPaths:
    """A run folder as phase 5 leaves it: phases 2 to 4 on disk, and the finished folder published under the
    meeting's own name with its front matter, figure and provenance."""

    paths = RunPaths(repository=tmp, module=VIDEO, output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    merge_inputs.write_merge_inputs(paths, {
        "entries": ENTRIES, "frames": [{"index": 0, "timestamp": 4.0, "screenKind": "web application"},
                                       {"index": 1, "timestamp": 9.0, "screenKind": "web application"}]})
    items = []
    for record in RECORDS:
        _write_frame(paths.output_root / "frames" / f"{record['slug']}.jpg")
        items.append({"slug": record["slug"], "index": record["index"], "timestamp": record["timestamp"],
                      "path": f"frames/{record['slug']}.jpg"})
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items, "video": {}})
    for item, record in zip(items, RECORDS):
        frame_records_service.save_frame_record(paths, item, {key: value for key, value in record.items()
                                                              if key not in ("slug", "index", "timestamp")}, [])
    paths.summary.write_text(BODY, encoding="utf-8")
    # Phase 4's coverage table: what became of every entry. The speech line was heard by a frame and never noted.
    json_store_utils.write_json(paths.output_root / output_contract.DRAFT_PROVENANCE_FILE, {"coverage": {"entries": {
        "F0.1": "cited", "S0001": "setAside", "F1.1": "cited", "S0002": "setAside"}}})
    document_file_service.write_parts(paths, {"points": [], "event_name": "Order process walkthrough",
                                               "speakers": ["Dana"], "topic": "warehouse ordering"})
    document = document_folder_service.build_document_path(paths)
    _write_frame(document.parent / output_contract.RESOURCES_DIR_NAME / "figure-01.png")
    document.write_text(document_folder_service.render_published_front_matter(paths) + BODY, encoding="utf-8")
    json_store_utils.write_json(document.parent / output_contract.DOCUMENT_PROVENANCE_FILE, {
        "document": document.name, "images": [{"figure": 1, "file": "resources/figure-01.png", "entries": [{"id": "F0.1"}]}]})

    return paths


def _find_published(paths: RunPaths) -> Path:
    return document_folder_service.build_document_path(paths)


def _load_run_context(paths: RunPaths,
                      model) -> RunContext:
    run_dir = paths.repository / "run"
    run_dir.mkdir(exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=model)


def _answer_nothing(base_url: str,
                    model: str) -> None:
    """The window of a host that is not there: no window is known."""

    return None


def _run_phase_5(ctx: RunContext,
                 is_dry_run: bool = False) -> dict:
    """Phase 5 alone, the host's window listing answering as a host that is not there, and put back afterwards."""

    previous = host_window_service.set_window_reader(_answer_nothing)
    try:
        return review_document_node.write_reviewed_document_node(graph_state.new_run_state(6, 6, is_dry_run=is_dry_run),
                                                         {"configurable": {"ctx": ctx}})
    finally:
        host_window_service.set_window_reader(previous)


def _submit(name: str,
            **arguments):
    return fakes.tool_call(name, **arguments)


def _write_answer(paths: RunPaths,
                  slug: str,
                  answer: dict) -> None:
    """An answer on disk as the conversation leaves it: its call log first, then the answer."""

    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug), {"response": {
        "outcome": "completed", "parsed": answer, "text": "", "thinking": "", "finishReason": "tool_calls"}})
    review_file_service.write_answer(paths, slug, answer)


def _write_reads(paths: RunPaths,
                 slug: str,
                 names: list[str]) -> None:
    """The calls a follow-up made, as the conversation's log records them."""

    log = paths.output_root / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        for name in names:
            handle.write(json.dumps({"followUp": slug, "calls": [{"name": name, "args": {"words": "x"}}]}) + "\n")


def _prepare_working_document(paths: RunPaths) -> None:
    """The working copy the checks read, as the phase writes it before each follow-up."""

    review_file_service.build_working_document_path(paths).parent.mkdir(parents=True, exist_ok=True)
    review_file_service.build_working_document_path(paths).write_text(_find_published(paths).read_text(encoding="utf-8"),
                                                                      encoding="utf-8")


def _codes(report) -> set[str]:
    return harness.codes(report)


def test_an_edit_replaces_or_inserts_inside_its_section_and_leaves_the_rest_as_it_was():
    inserted = document_edit_utils.apply_edit(BODY, RESOLVED)
    harness.check("an insert goes right after its sentence, in the same paragraph",
                  f"{QUOTE} Only a team lead may raise the order limit. Figure 1 has both fields." in inserted, inserted)
    harness.check("and nothing else moves", inserted.replace(" Only a team lead may raise the order limit.", "") == BODY)
    replaced = document_edit_utils.apply_edit(BODY, {**RESOLVED, "after": "", "replaces": QUOTE, "sentences": [
        {"text": "A team lead may raise the order limit, which removes the approval step."}]})
    harness.check("a replacement takes the sentence's place", QUOTE not in replaced
                  and "The order form needs the customer number and the delivery date. A team lead may raise" in replaced,
                  replaced)
    spread = BODY.replace("the delivery date. Raising", "the delivery date.\nRaising")
    harness.check("a quote is found across a line break, word for word",
                  document_edit_utils.find_quote(spread, "Ordering", "the delivery date. Raising the order limit")
                  is not None)
    harness.check("a quote is found only in the section it names",
                  document_edit_utils.find_quote(BODY, "Agenda", QUOTE) is None
                  and document_edit_utils.find_quote(BODY, "Ordering", "Raising the ORDER limit removes") is None)
    marked = BODY.replace("the customer number and", "the *customer number* and").replace(
        "removes the approval step", "removes the 'approval' step")
    harness.check("found on its second real run: a quote is read as a reader reads it, without emphasis or quote marks",
                  document_edit_utils.find_quote(marked, "Ordering", "The order form needs the customer number and the "
                                                                     "delivery date.") is not None
                  and document_edit_utils.find_quote(marked, "Ordering", QUOTE) is not None)
    harness.check("and a changed word is still refused",
                  document_edit_utils.find_quote(marked, "Ordering", "The order form wants the customer number") is None)
    edited = document_edit_utils.apply_edit(marked, {"section": "Ordering", "replaces": "needs the customer number",
                                                     "after": "", "sentences": [{"text": "asks for the customer number"}]})
    harness.check("an edit over a marked word leaves no stray mark",
                  "The order form asks for the customer number and the delivery date." in edited, edited[:500])
    harness.check("found on its first real run: a section holds its subsections, as a reader reads it",
                  document_edit_utils.find_quote(BODY, "Decisions and open points", "Nothing was left open.")
                  is not None and "### Decisions" in document_edit_utils.read_sections(
                      BODY, "Decisions and open points")[0])
    questions = document_edit_utils.add_open_questions(BODY, ["From which day in March do orders ship?"])
    harness.check("an open question replaces the line saying nothing was left open, in the one list",
                  "- From which day in March do orders ship?\n" in questions + "\n"
                  and output_contract.NOTHING_OPEN not in questions, questions[-300:])
    twice = document_edit_utils.add_open_questions(questions, ["Who signs off a larger order?"])
    harness.check("a second one joins the list under the first",
                  twice.rstrip().endswith("- Who signs off a larger order?")
                  and "- From which day" in twice, twice[-300:])


def test_a_replacement_that_cites_none_of_the_replaced_sentence_s_frames_is_refused_defect():
    # A resolution rewrote a version 17 sentence on the frames of the version 16 slide and never read frame 77, the frame the
    # sentence rested on, and the document came out wrong (release test 2, DEFECTS 443). It must cite one of the
    # frames the replaced sentence rests on
    from models.validation_report import ValidationReport

    paths = fx.make_paths()
    document_file_service.write_section(
        paths, "heading-001-1", "The gateway retries a failing call three times. [F77.5, S0100]\n\nSomething else entirely. [F3.1]\n")
    replaced = "The gateway retries a failing call three times."
    elsewhere = [{"text": "The gateway retries twice.", "frames": [60, 61], "entry_ids": [], "transcript_ids": []}]
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", replaced, elsewhere)
    detail = " ".join(finding.detail for finding in report.findings)
    harness.check("a replacement on other frames is named with the frame the sentence rests on",
                  "rests on frame(s) 77" in detail and "cites none of them" in detail, detail)
    own = [{"text": "The gateway retries twice.", "frames": [77], "entry_ids": [], "transcript_ids": []}]
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", replaced, own)
    harness.check("one citing frame 77 passes", report.findings == [], str(report.findings))
    by_entry = [{"text": "The gateway retries twice.", "frames": [], "entry_ids": ["F77.5"], "transcript_ids": []}]
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", replaced, by_entry)
    harness.check("an entry of frame 77 counts as well", report.findings == [], str(report.findings))
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", "A sentence no section holds.", elsewhere)
    harness.check("a replaced sentence no section holds is not judged", report.findings == [], str(report.findings))


def test_a_replaced_summary_sentence_is_read_from_the_summarys_own_sources_defect():
    # Defect: the executive summary is no section file, so a resolution replacing one of its sentences was never checked
    # for citing what the sentence rests on (a Q&A claim was sourced from an early frame's notes). Fix: the summary's own
    # entry ids are read too.
    from models.validation_report import ValidationReport

    paths = fx.make_paths()
    summary_path = document_file_service.build_summary_path(paths)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({"summary": [{"text": "Questions about the licence stay unanswered.",
                                                     "entry_ids": ["F72.15", "S0348"]}]}), encoding="utf-8")
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", "Questions about the licence stay unanswered.",
                                                 [{"text": "The licence was answered.", "frames": [8], "entry_ids": ["F8.2"]}])
    detail = " ".join(finding.detail for finding in report.findings)
    harness.check("the finding names the frame the summary sentence rests on", "rests on frame(s) 72" in detail, detail)
    report = ValidationReport(subject="review-todo-01")
    review_validator._check_replaced_source_read(report, paths, "review-todo-01", "Questions about the licence stay unanswered.",
                                                 [{"text": "The licence was answered.", "frames": [72], "entry_ids": ["F72.15"]}])
    harness.check("a replacement citing one of its frames passes", not report.findings, str(report.findings))


def test_a_quote_without_a_links_target_is_found_defect():
    # Defect: a reviewer quoted the executive summary's last sentence, which the program ends with a link, copying the link's
    # text and not its target, and was rejected (REVIEW_QUOTE_NOT_FOUND). Fix: both are read without link targets, and an
    # edit takes the target with the sentence.
    body = ("## Executive summary\n\n- Open questions include how to reconcile licences with free software ideals "
            "[Q&A session](#qa-session).\n\n## Q&A session\n\nThe team answered.\n")
    quote = "Open questions include how to reconcile licences with free software ideals [Q&A session]."
    span = document_edit_utils.find_quote(body, "Executive summary", quote)
    harness.check("a quote without the link's target is found", span is not None)
    harness.check("the span takes the target with it, so an edit leaves none behind",
                  span is not None and body[span[0]:span[1]].endswith("[Q&A session](#qa-session)."), str(span))
    harness.check("a quote with the target is found too",
                  document_edit_utils.find_quote(body, "Executive summary", quote.replace("session].", "session](#qa-session).")) is not None)
    harness.check("a changed word is still refused", document_edit_utils.find_quote(
        body, "Executive summary", quote.replace("licences", "licenses")) is None)


def test_a_quote_of_text_inside_a_fenced_block_is_told_it_stands_in_one_defect():
    # A reviewer quoted a slide's list the document prints inside a fenced block, and was told the document does not hold
    # the sentence anywhere, which is false (release test 2, DEFECTS 442). The finding says where it stands
    document = ("# Talk\n\n## Ordering\n\nThe lead raises the limit.\n\n```markdown\n- Orders ship from the new warehouse\n```\n")
    todo = {**GAP, "section": "Ordering", "quote": "- Orders ship from the new warehouse"}
    code, detail, _fix = review_validator.find_todo_defect(document, todo, "TODO-01")
    harness.check("the quote stands inside a fenced block and the finding says so",
                  code == "REVIEW_QUOTE_NOT_FOUND" and "stands inside a fenced block" in detail
                  and "does not hold this sentence anywhere" not in detail, detail)
    absent = {**GAP, "section": "Ordering", "quote": "Orders ship from a warehouse nobody mentioned"}
    _code, absent_detail, _fix = review_validator.find_todo_defect(document, absent, "TODO-02")
    harness.check("a quote the document holds nowhere is still told so",
                  "the document does not hold this sentence anywhere" in absent_detail, absent_detail)


def test_the_todo_list_is_checked_quote_by_quote_and_by_its_length():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_answer(paths, "review-todos", {"todos": [GAP, QUESTION]})
    report = review_validator.validate_review_todos(paths, "review-todos", 20)
    harness.check("a list whose sentences are all in their sections passes", report.ok, report.render_summary())
    paraphrased = {**GAP, "quote": "Raising the limit removes the approval step."}
    _write_answer(paths, "review-todos", {"todos": [GAP, paraphrased]})
    report = review_validator.validate_review_todos(paths, "review-todos", 20)
    harness.check("a paraphrased sentence is REVIEW_QUOTE_NOT_FOUND, naming the todo and the section",
                  _codes(report) == {"REVIEW_QUOTE_NOT_FOUND"} and "TODO-02" in report.findings[0].detail
                  and "Ordering" in report.findings[0].detail, report.render_summary())
    elsewhere = {**GAP, "section": "Agenda"}
    spoken = {**GAP, "quote": "So the lead just raises it in the admin page, I think."}
    _write_answer(paths, "review-todos", {"todos": [elsewhere, spoken]})
    details = [finding.detail for finding in review_validator.validate_review_todos(paths, "review-todos", 20).findings]
    harness.check("a sentence under another heading is placed there, and one the document does not hold is refused",
                  len(details) == 1 and "the document does not hold this sentence anywhere" in details[0], str(details))
    defect = review_validator.find_todo_defect(review_validator._read_document(paths), elsewhere, "TODO-01")
    harness.check("the check itself still says where a sentence stands", defect is not None
                  and 'it stands under "Ordering"' in defect[1], str(defect))
    # The program knows where a quote stands: a todo naming another section than the one holding its sentence was
    # refused and dropped (pdf-02); written by the program's own writer, it is placed under its heading
    review_file_service.write_answer(paths, "review-todos", {"todos": [elsewhere, spoken]})
    placed = review_file_service.read_answer(paths, "review-todos")["todos"]
    harness.check("written, the quote under another heading takes that heading, the one named kept",
                  placed[0]["section"] == "Ordering" and placed[0]["namedSection"] == "Agenda"
                  and placed[1]["section"] == GAP["section"] and "namedSection" not in placed[1], str(placed))
    # A quote with one word changed was sent three times, told each time only that the document does not hold it; one
    # word off is now its sentence (DEFECTS 296), so two words off are told where they differ
    one_word = {**GAP, "quote": "Raising the order limit remove the approval steps."}
    _write_answer(paths, "review-todos", {"todos": [one_word]})
    details = [finding.detail for finding in review_validator.validate_review_todos(paths, "review-todos", 20).findings]
    harness.check("a quote two words off is told the words where it differs from the nearest sentence",
                  len(details) == 1 and "limit remove the'" in details[0] and "limit removes the'" in details[0], str(details))
    _write_answer(paths, "review-todos", {"todos": [GAP] * 3})
    report = review_validator.validate_review_todos(paths, "review-todos", 2)
    harness.check("more todos than allowed is REVIEW_TOO_MANY", "REVIEW_TOO_MANY" in _codes(report),
                  report.render_summary())
    _write_answer(paths, "review-todos", {"todos": []})
    harness.check("an empty list is a valid answer", review_validator.validate_review_todos(paths, "review-todos", 20).ok)
    _write_answer(paths, "review-todos", {"todos": [{**GAP, "missing": " "}]})
    harness.check("a todo that does not say what is missing is REVIEW_TODO_INCOMPLETE",
                  "REVIEW_TODO_INCOMPLETE" in _codes(review_validator.validate_review_todos(paths, "review-todos", 20)))


def test_each_check_on_a_resolution_fires_on_its_own_defect_and_spares_a_good_answer():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    slug = "todo-01"
    _write_answer(paths, slug, RESOLVED)
    report = review_validator.validate_resolution(paths, slug)
    harness.check("a resolved answer citing a transcript line that says it passes", report.ok, report.render_summary())
    _write_reads(paths, slug, ["read_transcript", "read_moments"])
    _write_answer(paths, slug, UNRESOLVED)
    harness.check("an unresolved answer that read the transcript and the frames' notes passes",
                  review_validator.validate_resolution(paths, slug).ok)
    _write_reads(paths, "todo-02", ["read_transcript", "read_transcript"])
    _write_answer(paths, "todo-02", UNRESOLVED)
    harness.check("found on the real run: \"not in the recording\" after reading only the transcript is "
                  "RESOLUTION_NOTES_UNREAD", "RESOLUTION_NOTES_UNREAD"
                  in _codes(review_validator.validate_resolution(paths, "todo-02")))
    _write_reads(paths, "todo-03", ["find"])
    _write_answer(paths, "todo-03", UNRESOLVED)
    harness.check("a `find`, which reads the notes too, is enough", review_validator.validate_resolution(paths, "todo-03").ok)
    unchanged = {**RESOLVED, "after": "", "replaces": "Figure 1 has both fields.",
                 "sentences": [{"text": "Figure 1 has both fields, and the form has no third.", "entry_ids": ["F0.1"]}]}
    _write_answer(paths, slug, unchanged)
    harness.check("a replacement that keeps the figure's pointer is not a touched figure",
                  "RESOLUTION_FIGURE_TOUCHED" not in _codes(review_validator.validate_resolution(paths, slug)))
    # A team term the term list says the transcript writes as words its source holds counts as said
    mapped = {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0], "text": "Only a TL may raise the order limit."}]}
    _write_answer(paths, slug, mapped)
    original_spellings = system_prompt_builder.read_transcript_spellings
    system_prompt_builder.read_transcript_spellings = lambda: {"team lead": "TL"}
    try:
        codes = _codes(review_validator.validate_resolution(paths, slug))
    finally:
        system_prompt_builder.read_transcript_spellings = original_spellings
    harness.check("a team term written as the transcript's words for it is sourced",
                  "RESOLUTION_TERM_UNSOURCED" not in codes, str(codes))
    # A sentence citing nothing is only RESOLUTION_NO_SOURCE: told also that its term "none of its sources says", a
    # worker hunted for another word for one its transcript said (lecture-9 review, 2026-10-21)
    _write_answer(paths, slug, {**RESOLVED, "sentences": [{"text": "Only a team lead may raise it in the ERP above 4,750 units."}]})
    found = _codes(review_validator.validate_resolution(paths, slug))
    harness.check("a sentence with no source is named for that alone", "RESOLUTION_NO_SOURCE" in found
                  and not {"RESOLUTION_TERM_UNSOURCED", "RESOLUTION_NUMBER_UNSOURCED"} & found, str(found))
    for label, answer, code in (
            ("a new sentence with no source", {**RESOLVED, "sentences": [{"text": "Only a team lead may raise it."}]},
             "RESOLUTION_NO_SOURCE"),
            ("a made-up transcript line", {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0],
                                                                    "transcript_ids": ["T4411"]}]},
             "RESOLUTION_SOURCE_UNKNOWN"),
            ("a frame that does not exist", {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0], "frames": [7]}]},
             "RESOLUTION_SOURCE_UNKNOWN"),
            ("a frame's note cited as a transcript line",
             {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0], "transcript_ids": ["F0.1"]}]},
             "RESOLUTION_SOURCE_UNKNOWN"),
            ("a sentence to follow that is not in its section", {**RESOLVED, "after": "Raising the limit helps."},
             "RESOLUTION_TARGET_MISSING"),
            ("both a sentence to replace and one to follow", {**RESOLVED, "replaces": QUOTE}, "RESOLUTION_TARGET_MISSING"),
            ("an edit that drops the figure's pointer",
             {**RESOLVED, "after": "", "replaces": "Figure 1 has both fields.",
              "sentences": [{"text": "The form has two fields.", "entry_ids": ["F0.1"]}]}, "RESOLUTION_FIGURE_TOUCHED"),
            # pdf-05: "shown in Figure 2" was refused as an unsourced 2 and as a figure removed or changed
            ("a new sentence naming a figure by its number",
             {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0],
                                         "text": "Only a team lead may raise it, as Figure 1 shows."}]},
             "RESOLUTION_FIGURE_TOUCHED"),
            ("a name none of its sources says", {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0],
                                                                    "text": "Only a team lead may raise it in the ERP."}]},
             "RESOLUTION_TERM_UNSOURCED"),
            ("an unresolved answer that searched nowhere", {**UNRESOLVED, "searches": [" "]}, "RESOLUTION_NO_SEARCH"),
            ("an unresolved answer with no question", {**UNRESOLVED, "question": ""}, "RESOLUTION_NO_QUESTION"),
            ("a resolved answer that adds nothing", {**RESOLVED, "sentences": []}, "RESOLUTION_NO_SENTENCE"),
            ("a new sentence that describes the recording",
             {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0], "text": "The video shows who may raise it."}]},
             "DOCUMENT_BANNED_PHRASE"),
            # 2026-10-10: a count from another note of the frame was put in, citing notes with a different count
            ("a new sentence stating a number none of its sources holds",
             {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0],
                                         "text": RESOLVED["sentences"][0]["text"].rstrip(".") + " above 4,750 units."}]},
             "RESOLUTION_NUMBER_UNSOURCED")):
        _write_answer(paths, slug, answer)
        found = _codes(review_validator.validate_resolution(paths, slug))
        harness.check(f"{label} is {code}", code in found, str(found))
    _write_answer(paths, slug, {**RESOLVED, "sentences": [{**RESOLVED["sentences"][0],
                                                           "text": "Only a team lead may raise it, as Figure 1 shows."}]})
    report = review_validator.validate_resolution(paths, slug)
    harness.check("a figure's number is no unsourced number, and the finding names the figure",
                  "RESOLUTION_NUMBER_UNSOURCED" not in _codes(report) and "names Figure 1" in str(report.findings),
                  str(report.findings))
    # 2026-10-02: a todo the program's own repair already settled, with nothing left to edit, had no answer to give
    _write_answer(paths, slug, {**RESOLVED, "after": "", "replaces": "", "sentences": [],
                                "searches": ["the section already places Figure 3 and names it in its caption"]})
    found = _codes(review_validator.validate_resolution(paths, slug))
    harness.check("a resolution with no edit, saying where the document already does it, is accepted",
                  "RESOLUTION_NO_SENTENCE" not in found and "RESOLUTION_TARGET_MISSING" not in found, str(found))
    # A new sentence the section already holds is not added again: the answer says where the document does it
    _write_answer(paths, slug, {**RESOLVED, "sentences": [{"text": "The order form needs the customer number and the "
                                                                   "delivery date.", "entry_ids": ["F0.1"]}]})
    report = review_validator.validate_resolution(paths, slug)
    written = review_file_service.read_answer(paths, slug)
    harness.check("a new sentence the section already holds is accepted as no change", report.ok
                  and written["sentences"] == [] and "the document already says" in written["searches"][-1],
                  report.render_summary() + str(written))
    # 2026-10-02: a sentence about the recording is fixed by "nothing", and the check refused a resolution that deleted it
    _write_answer(paths, slug, {**RESOLVED, "after": "", "replaces": QUOTE, "sentences": []})
    found = _codes(review_validator.validate_resolution(paths, slug))
    harness.check("a resolution that deletes the sentence it replaces, adding none, is accepted",
                  "RESOLUTION_NO_SENTENCE" not in found and "RESOLUTION_TARGET_MISSING" not in found, str(found))


def _build_reading(paths: RunPaths,
                   read_budget: int = 10) -> ReviewReadingContext:
    items = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])["items"]
    _prepare_working_document(paths)

    return ReviewReadingContext(paths=paths, document_path=review_file_service.build_working_document_path(paths),
                                transcript=[entry for entry in ENTRIES if entry["kind"] == "speech"], entries=ENTRIES,
                                frame_items={item["index"]: item for item in items},
                                frame_records={record["index"]: record for record in RECORDS},
                                read_chars=4000, notes_chars=400, read_budget=read_budget)


def test_the_reading_tools_find_the_sources_and_count_each_read():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths, read_budget=5)
    state = WriteReadingState()
    found = review_reading_service.run_read(reading, state, "find", {"words": "order limit"}).text
    harness.check("find names the transcript line, the frame note, the entry and the document section",
                  found.startswith("[r1]") and "S0001" in found and "frame 1" in found and "F1.1" in found
                  and 'section "Ordering"' in found, found)
    lines = review_reading_service.run_read(reading, state, "read_transcript", {"start": "0:05", "end": "0:09"}).text
    harness.check("the transcript is read by time range, each line with its id and time",
                  "S0001 00:06 Only a team lead" in lines and "S0002" not in lines, lines)
    moments = review_reading_service.run_read(reading, state, "read_moments", {"start": "0:00", "end": "0:09"}).text
    harness.check("a moment is a frame's notes and the speech while it was shown",
                  "frame 0 at 00:04" in moments and "The presenter opens the order form." in moments
                  and "S0001 00:06 Only a team lead" in moments, moments)
    section = review_reading_service.run_read(reading, state, "read_document", {"heading": "Ordering"}).text
    harness.check("one section of the document is read by its heading", "## Ordering" in section
                  and "Executive summary" not in section and "topic:" not in section, section)
    looked = review_reading_service.run_read(reading, state, "look_at_frame", {"frame": 1})
    harness.check("looking at a frame hands the loop its frame", looked.frame_path is not None
                  and looked.frame_path.is_file(), looked.text)
    spent = review_reading_service.run_read(reading, state, "read_entries", {"ids": ["F1.1"]}).text
    harness.check("the fifth read is still answered", "Raising the order limit" in spent, spent)
    refused = review_reading_service.run_read(reading, state, "read_frame_record", {"frame": 0}).text
    harness.check("a read past the follow-up's budget is refused and says to submit", "budget" in refused
                  and "submit" in refused, refused)
    noted = review_reading_service.run_read(reading, state, "note", {"text": "S0001 answers who may raise the limit."}).text
    harness.check("a note is not a read, and is kept on disk",
                  "Noted" in noted and review_file_service.read_notes(paths) == ["S0001 answers who may raise the limit."],
                  noted)


def test_the_worker_is_told_its_two_jobs_and_every_tool_it_is_given():
    system_text = review_messages.build_review_system_text("knowledge", [], True)
    harness.check("the instruction is the review's, with both jobs", "JOB 1, THE CRITIQUE" in system_text
                  and "JOB 2, RESOLVING ONE TODO" in system_text and "{{" not in system_text, system_text[:200])
    missing = [name for name in [*review_actions.READ_TOOLS, *review_actions.SUBMIT_TOOLS]
               if f"`{name}`" not in system_text]
    harness.check("it names every tool the worker is given", missing == [], str(missing))
    critique = review_messages.build_critique_text(BODY, 20)
    harness.check("the critique follow-up carries the whole document and the limit",
                  critique.startswith("Job 1, the critique.") and "at most 20 todos" in critique
                  and document_edit_utils.render_numbered_document(BODY.strip()) in critique, critique[:300])
    todo = {"id": "TODO-02", **GAP}
    resolve = review_messages.build_resolve_text(todo, 5, [])
    harness.check("with no sources found, no line names them", "wrote this sentence from" not in resolve, resolve)
    # 2026-10-08: the published document drops the ids, and a reviewer guessed a frame per todo, one number up each time
    with_sources = review_messages.build_resolve_text(todo, 5, ["F25.1", "S0570"])
    harness.check("the notes the sentence was written from are named before the reviewer's own guess",
                  "wrote this sentence from the notes F25.1, S0570" in with_sources
                  and with_sources.index("F25.1") < with_sources.index("You thought"), with_sources)
    harness.check("a resolve follow-up names the todo, its sentence, what is missing and where to look",
                  resolve.startswith('Job 2, TODO-02 of 5: gap. In "Ordering", about the sentence: "Raising the order')
                  and "Missing: who may raise the order limit" in resolve
                  and "You thought the answer may be: the transcript just before" in resolve, resolve)
    harness.check("it says an unresolved answer needs this todo's own read of the frames' notes",
                  "in this todo's own turns, even when an earlier todo read the same notes" in resolve, resolve)
    harness.check("the settings hold the review's limits", app_config.settings.review.max_todos == 20
                  and app_config.settings.review.reads == 30, str(app_config.settings.review))


def test_the_switch_off_leaves_the_document_as_phase_4_published_it_and_calls_no_model():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    published = _find_published(paths)
    original = published.read_text(encoding="utf-8")
    model = fakes.scripted()
    ctx = _load_run_context(paths, model)
    previous = app_config.settings.review.is_on
    app_config.settings.review.is_on = False
    try:
        update = _run_phase_5(ctx)
    finally:
        app_config.settings.review.is_on = previous
    harness.check("phase 5 is recorded off, in the state and in the run record",
                  update["phases"]["review"] == "off" and ctx.store.get_phase("review") == "off", str(update))
    harness.check("no model call was made", not model.calls, str(model.calls)[:300])
    harness.check("the published document is phase 4's", published.read_text(encoding="utf-8") == original, "")


def test_an_audio_recording_s_flow_has_no_review():
    """The owner: an audio recording's flow has no critic. Its phase 5 is recorded off, the published document stays as
    phase 4 wrote it, and no model is called."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    phase_file_service.write_phase_file(paths, frames_number, {**phase_file_service.read_phase_file(paths, frames_number),
                                                               "items": [], "medium": output_contract.AUDIO_MEDIUM})
    published = _find_published(paths)
    original = published.read_text(encoding="utf-8")
    model = fakes.scripted()
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    harness.check("phase 5 is recorded off, in the state and in the run record",
                  update["phases"]["review"] == "off" and ctx.store.get_phase("review") == "off", str(update))
    harness.check("no model call was made", not model.calls, str(model.calls)[:300])
    harness.check("the published document is phase 4's", published.read_text(encoding="utf-8") == original, "")


def test_a_pdf_read_as_its_text_only_is_named_so_when_its_review_is_off_defect():
    # Defect: The console said "an audio recording's flow has no review" for a PDF read as its text only.
    # Fix: phase_file_service.describe_frameless_run names the run's kind in the off lines of phases 5 and 6.
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    phase_file_service.write_phase_file(paths, frames_number, {**phase_file_service.read_phase_file(paths, frames_number),
                                                               "items": [], "medium": output_contract.PDF_TEXT_ONLY_MEDIUM})
    harness.check("a PDF read as its text only is named so, never an audio recording",
                  phase_file_service.describe_frameless_run(paths) == "a PDF read as its text only",
                  phase_file_service.describe_frameless_run(paths))
    phase_file_service.write_phase_file(paths, frames_number, {**phase_file_service.read_phase_file(paths, frames_number),
                                                               "medium": output_contract.AUDIO_MEDIUM})
    harness.check("an audio recording is named so", phase_file_service.describe_frameless_run(paths) == "an audio recording",
                  phase_file_service.describe_frameless_run(paths))


def test_the_phase_reviews_resolves_and_publishes_the_reviewed_document():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    published = _find_published(paths)
    original = published.read_text(encoding="utf-8")
    model = fakes.scripted(_submit("submit_review", todos=[GAP, QUESTION]),
                           _submit("read_transcript", words="order limit"),
                           _submit("look_at_frame", frame=1),
                           _submit("submit_resolution", **RESOLVED),
                           _submit("find", words="March"),
                           _submit("submit_resolution", **UNRESOLVED))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    harness.check("phase 5 completes", update["phases"]["review"] == "complete", str(update))
    store = review_file_service.read_store(paths)
    harness.check("the critique is stored as todos with ids, and each todo's outcome beside it",
                  [(todo["id"], todo["status"]) for todo in store["todos"]]
                  == [("TODO-01", "resolved"), ("TODO-02", "unresolved")], json.dumps(store)[:400])
    harness.check("a resolved todo keeps its edit and its sources", store["todos"][0]["sentences"][0]["transcript_ids"]
                  == ["S0001"] and store["todos"][0]["after"] == QUOTE, str(store["todos"][0]))
    reviewed = published.read_text(encoding="utf-8")
    harness.check("the published document holds the new sentence where the edit put it, its front matter kept",
                  f"{QUOTE} Only a team lead may raise the order limit." in reviewed
                  and reviewed.startswith(original.split("# Orders")[0]), reviewed[:400])
    harness.check("the unresolved todo is an open point, in the one list",
                  "- From which day in March do orders ship from the new warehouse?" in reviewed and output_contract.NOTHING_OPEN not in reviewed, reviewed[-500:])
    last_line = reviewed.rstrip().splitlines()[-1]
    harness.check("the document ends with one line counting what was found, fixed and left open, linking the report",
                  "2 points found" in last_line and "1 fixed" in last_line and "1 left open" in last_line
                  and f"]({output_contract.REVIEW_REPORT_FILE})" in last_line, last_line)
    harness.check("the unreviewed copy is kept",
                  review_file_service.build_before_path(paths).read_text(encoding="utf-8") == original)
    report = (published.parent / output_contract.REVIEW_REPORT_FILE).read_text(encoding="utf-8")
    harness.check("the report beside it has one block per todo: kind, where, what was missing, status, change, sources",
                  "TODO-01" in report and "gap" in report and "who may raise the order limit" in report
                  and "resolved" in report and "S0001" in report and "Only a team lead may raise" in report
                  and "TODO-02" in report and "the transcript for 'March'" in report, report)
    harness.check("the run folder holds the same report as the phase's own file",
                  phase_file_service.build_phase_file_path(paths, output_contract.PHASE_NUMBERS["review"])
                  .read_text(encoding="utf-8") == report)
    provenance = json_store_utils.read_json(published.parent / output_contract.DOCUMENT_PROVENANCE_FILE)
    harness.check("the provenance names phase 5 and its counts",
                  provenance["review"]["counts"] == {"todos": 2, "resolved": 1, "unresolved": 1, "blocked": 0}
                  and provenance["images"][0]["figure"] == 1, str(provenance.get("review")))
    harness.check("an open point carries the reads its follow-up made, beside the searches it reported",
                  all("reads" in point for point in provenance["review"]["openPoints"])
                  and len(provenance["review"]["openPoints"]) == 1, str(provenance["review"].get("openPoints")))
    second = model.calls[4]
    harness.check("the second todo is asked in the same conversation as the critique: one system message, the "
                  "critique still in it", sum(isinstance(message, SystemMessage) for message in second) == 1
                  and any(str(message.content).startswith("Job 1, the critique.") for message in second)
                  and str(second[-1].content).startswith("Job 2, TODO-02 of 2"), str([type(m).__name__ for m in second]))
    results = [str(message.content) for message in second if isinstance(message, ToolMessage)]
    harness.check("the first todo's accepted answer got its result before the next question",
                  results.count("Accepted.") == 2, str(results))
    frames = [part for message in model.calls[3] if isinstance(message.content, list) for part in message.content
                if isinstance(part, dict) and part.get("type") == "image_url"]
    harness.check("the frame it looked at reached it, beside the one placed figure the critique was shown",
                  len(frames) == 2, str(len(frames)))
    harness.check("each todo records where the facts was lost, decided by the program from its sources",
                  [(todo["lost_at"], todo["lost_at_by"]) for todo in store["todos"]]
                  == [("outline", "program"), ("not_in_recording", "program")], str(store["todos"]))
    opening = report.split("## TODO-01")[0]
    harness.check("the report opens with the todos counted by where the facts was lost and by kind, each with "
                  "its ids", "set aside by the table of contents: 1 (TODO-01)" in opening
                  and "not in the recording: 1 (TODO-02)" in opening and "gap: 1 (TODO-01)" in opening
                  and "open_question: 1 (TODO-02)" in opening, opening)


def test_a_rejected_answer_is_retried_in_the_conversation_with_the_finding():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    paraphrased = {**GAP, "quote": "Raising the limit removes the approval step."}
    model = fakes.scripted(_submit("submit_review", todos=[paraphrased]), _submit("submit_review", todos=[]))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    retry = model.calls[1]
    rejection = next((str(message.content) for message in retry if isinstance(message, ToolMessage)), "")
    harness.check("the rejection names the code, the todo and the fix", "REVIEW_QUOTE_NOT_FOUND" in rejection
                  and "TODO-01" in rejection and "copy the sentence exactly" in rejection, rejection)
    harness.check("the corrected, empty list completes the phase with nothing to resolve",
                  update["phases"]["review"] == "complete" and review_file_service.read_store(paths)["todos"] == [],
                  str(update))
    last_line = _find_published(paths).read_text(encoding="utf-8").rstrip().splitlines()[-1]
    harness.check("and the document says the review found nothing", "0 points found" in last_line, last_line)


def test_a_resolution_sent_beside_its_own_reads_is_not_judged_yet():
    """A todo was answered "unresolved" in the turn of the find and the read meant to settle it, before their results
    (2026-10-06): the answer is not judged, no attempt is spent, and the next answer is."""

    from langchain_core.messages import AIMessage
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    both = AIMessage(content="", tool_calls=[
        {"name": "find", "args": {"words": "March"}, "id": "call_both_find", "type": "tool_call"},
        {"name": "submit_resolution", "args": UNRESOLVED, "id": "call_both_submit", "type": "tool_call"}])
    model = fakes.scripted(_submit("submit_review", todos=[QUESTION]), both, _submit("submit_resolution", **UNRESOLVED))
    ctx = _load_run_context(paths, model)
    _run_phase_5(ctx)
    after = model.calls[2]
    results = [str(message.content) for message in after if isinstance(message, ToolMessage)]
    harness.check("the answer beside the find is answered 'not judged yet'",
                  any(result.startswith("Not judged yet") for result in results), str(results)[-600:])
    todo = review_file_service.read_store(paths)["todos"][0]
    harness.check("the next answer is judged, with the attempt still its first",
                  todo["status"] == "unresolved" and todo["attempts"] == 1, json.dumps(todo)[:300])


def test_an_unresolved_answer_with_no_notes_read_is_sent_back_once_unjudged():
    """Three todos running spent their one attempt answering "unresolved" on the transcript alone (2026-10-06)."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    model = fakes.scripted(_submit("submit_review", todos=[QUESTION]),
                           _submit("read_transcript", words="March"),
                           _submit("submit_resolution", **UNRESOLVED),
                           _submit("find", words="March"),
                           _submit("submit_resolution", **UNRESOLVED))
    ctx = _load_run_context(paths, model)
    _run_phase_5(ctx)
    after = model.calls[3]
    results = [str(message.content) for message in after if isinstance(message, ToolMessage)]
    harness.check("the unresolved answer on the transcript alone is sent back, unjudged",
                  any("needs the frames' notes" in result for result in results), str(results)[-600:])
    todo = review_file_service.read_store(paths)["todos"][0]
    harness.check("after a notes read the answer is judged and accepted, on its first attempt",
                  todo["status"] == "unresolved" and todo["attempts"] == 1, json.dumps(todo)[:300])


def test_an_unresolved_answer_on_the_transcript_alone_gets_the_notes_of_its_time_and_is_then_accepted_defect():
    # A todo read only the transcript, answered unresolved, was told to read the notes, answered unresolved again and
    # was refused (DEFECTS 724). Now the program reads the notes of that time, hands them over and counts the read
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    model = fakes.scripted(_submit("submit_review", todos=[QUESTION]),
                           _submit("read_transcript", start="0:00", end="0:12"),
                           _submit("submit_resolution", **UNRESOLVED),
                           _submit("submit_resolution", **UNRESOLVED))
    ctx = _load_run_context(paths, model)
    _run_phase_5(ctx)
    results = [str(message.content) for message in model.calls[3] if isinstance(message, ToolMessage)]
    harness.check("the reminder carries the frames' notes of that time",
                  any("The program read the notes" in result and QUOTE in result for result in results),
                  str(results)[-800:])
    todo = review_file_service.read_store(paths)["todos"][0]
    harness.check("the same answer is then accepted, with no rejection",
                  todo["status"] == "unresolved" and todo["attempts"] == 1, json.dumps(todo)[:300])


def test_a_critique_keeps_its_valid_todos_when_its_attempts_run_out():
    """Found on the first real run: two quotes of a long list stayed wrong through every attempt, and every other
    todo was lost with them."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    spoken = {**QUESTION, "quote": "So the lead just raises it in the admin page, I think."}
    model = fakes.scripted(_submit("submit_review", todos=[GAP, spoken]), _submit("submit_review", todos=[GAP, spoken]),
                           _submit("submit_resolution", **RESOLVED))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    store = review_file_service.read_store(paths)
    harness.check("the todos whose sentences stand in the document are kept and resolved",
                  [(todo["id"], todo["status"]) for todo in store["todos"]] == [("TODO-01", "resolved")],
                  json.dumps(store)[:300])
    dropped = store["critique"].get("dropped") or []
    harness.check("the rest are recorded as dropped, each with why", store["critique"]["status"] == "partial"
                  and len(dropped) == 1 and "does not hold this sentence" in dropped[0]["finding"], str(store["critique"]))
    report = (_find_published(paths).parent / output_contract.REVIEW_REPORT_FILE).read_text(encoding="utf-8")
    harness.check("the report lists the dropped point with what it said was missing",
                  "Dropped from the critique" in report and "from which day in March" in report, report[:800])
    harness.check("and the phase completes on what was kept", update["phases"]["review"] == "complete", str(update))


def test_a_partial_answer_keeps_what_the_sources_say_and_asks_the_rest():
    """Found on the real run: a todo the sources answered in part came back unresolved, and the part was lost."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    partial = {**RESOLVED, "question": "Can a team lead raise the limit for another team?"}
    model = fakes.scripted(_submit("submit_review", todos=[GAP]), _submit("read_moments", start="0:00", end="0:12"),
                           _submit("submit_resolution", **partial))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    reviewed = _find_published(paths).read_text(encoding="utf-8")
    harness.check("the sentences the sources support are written, and the rest is an open point",
                  update["phases"]["review"] == "complete" and "Only a team lead may raise the order limit." in reviewed
                  and "- Can a team lead raise the limit for another team?" in reviewed,
                  reviewed[-500:])
    todo = review_file_service.read_store(paths)["todos"][0]
    harness.check("the store keeps the reads the follow-up made, from its own calls",
                  [read.split(" ")[0] for read in todo.get("reads") or []] == ["read_moments"], str(todo.get("reads")))
    report = (_find_published(paths).parent / output_contract.REVIEW_REPORT_FILE).read_text(encoding="utf-8")
    harness.check("the report shows the change, the open point and the reads made",
                  "**Change**" in report and "**Open point added:**" in report and "**Reads made:** read_moments" in report,
                  report[-700:])


def test_a_stopped_review_resumes_at_the_next_pending_todo():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    first = fakes.scripted(_submit("submit_review", todos=[GAP, QUESTION]), _submit("submit_resolution", **RESOLVED),
                           fakes.says("I could not find it."))
    ctx = _load_run_context(paths, first)
    update = _run_phase_5(ctx)
    harness.check("a todo that spends its attempts leaves the phase blocked", update["phases"]["review"] == "blocked",
                  str(update))
    store = review_file_service.read_store(paths)
    harness.check("and is recorded as blocked, with why", store["todos"][1]["status"] == "blocked"
                  and store["todos"][1]["reason"], str(store["todos"][1]))
    harness.check("the accepted edit is already in the published document",
                  "Only a team lead may raise the order limit." in _find_published(paths).read_text(encoding="utf-8"))
    ctx.model = fakes.scripted(_submit("find", words="March"), _submit("submit_resolution", **UNRESOLVED))
    update = _run_phase_5(ctx)
    harness.check("the resume asks only the todo that was not accepted", ctx.model.call_count == 2
                  and str(ctx.model.calls[0][-1].content).startswith("Job 2, TODO-02 of 2"), str(ctx.model.call_count))
    standing = str(ctx.model.calls[0][1].content)
    harness.check("its starting_messages holds the todo list and what was done", "TODO-01" in standing
                  and "[TODO-01: resolved]" in standing, standing)
    reviewed = _find_published(paths).read_text(encoding="utf-8")
    harness.check("the resumed phase completes with the first edit applied once and the question added",
                  update["phases"]["review"] == "complete"
                  and reviewed.count("Only a team lead may raise the order limit.") == 1
                  and "From which day in March" in reviewed, reviewed[-600:])
    ctx.model = fakes.scripted(fakes.says("never asked"))
    _run_phase_5(ctx)
    harness.check("a complete phase whose document is still the reviewed one asks nothing",
                  ctx.model.call_count == 0 and _find_published(paths).read_text(encoding="utf-8") == reviewed)
    _find_published(paths).write_text(review_file_service.build_before_path(paths).read_text(encoding="utf-8"),
                                      encoding="utf-8")
    _run_phase_5(ctx)
    harness.check("a document phase 5 published again unchanged gets the review back without a call",
                  ctx.model.call_count == 0 and _find_published(paths).read_text(encoding="utf-8") == reviewed)
    changed = review_file_service.build_before_path(paths).read_text(encoding="utf-8").replace(
        "delivery date.", "delivery date and a phone number.")
    _find_published(paths).write_text(changed, encoding="utf-8")
    ctx.model = fakes.scripted(_submit("submit_review", todos=[]))
    _run_phase_5(ctx)
    harness.check("a document phase 5 rewrote is reviewed from the start",
                  ctx.model.call_count == 1 and review_file_service.build_before_path(paths).read_text(encoding="utf-8")
                  == changed and review_file_service.read_store(paths)["todos"] == [], str(ctx.model.call_count))
    earlier = sorted(review_file_service.build_earlier_dir(paths).glob("*/"))
    kept = json_store_utils.read_json(earlier[0] / "todos.json") if len(earlier) == 1 else {}
    harness.check("the owner's order: the earlier critique stays on disk, with its outcomes, its answers, its report "
                  "and the document it reviewed", [todo.get("status") for todo in kept.get("todos") or []]
                  == ["resolved", "unresolved"] and (earlier[0] / "answers" / "review-todos.json").is_file()
                  and (earlier[0] / output_contract.REVIEW_REPORT_FILE).is_file()
                  and "Raising the order limit" in (earlier[0] / "before.md").read_text(encoding="utf-8"),
                  str([path.name for path in earlier]))


def test_a_missing_document_is_refused_by_name_and_a_dry_run_renders_the_critique():
    paths = RunPaths(repository=Path(tempfile.mkdtemp()), module=VIDEO,
                     output_root_override=Path(tempfile.mkdtemp()) / "out")
    run_paths_service.ensure_run_paths(paths)
    ctx = _load_run_context(paths, fakes.scripted(fakes.says("never asked")))
    harness.check("with no published document phase 5 fails and calls nothing",
                  _run_phase_5(ctx)["phases"]["review"] == "failed" and ctx.model.call_count == 0)
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    ctx = _load_run_context(paths, fakes.scripted(fakes.says("never asked")))
    update = _run_phase_5(ctx, is_dry_run=True)
    rendered = ctx.run_dir / "dry-run" / "review" / review_conversation_service.TODOS_SLUG / "human-message.md"
    harness.check("a dry run renders the critique it would send, and calls nothing",
                  update["phases"]["review"] == "complete" and ctx.model.call_count == 0 and rendered.is_file()
                  and "Job 1, the critique." in rendered.read_text(encoding="utf-8"), str(update))


def test_phase_5_is_held_while_an_earlier_phase_or_frame_is_blocked():
    """The owner, 2026-09-28: a review of a document with a known hole makes no sense. Held, never marked complete,
    so a later run reviews once the hole is fixed."""

    for phase, status in (("finalize", "blocked"), ("finalize", "failed")):
        paths = _build_finished_run(Path(tempfile.mkdtemp()))
        ctx = _load_run_context(paths, fakes.scripted(fakes.says("never asked")))
        ctx.store.set_phase(phase, status)
        update = _run_phase_5(ctx)
        harness.check(f"a {status} {phase} holds phase 5 and calls nothing",
                      update["phases"]["review"] == "held" and ctx.model.call_count == 0
                      and ctx.store.get_phase("review") != "complete" and not review_file_service.read_store(paths),
                      str(update))
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    ctx = _load_run_context(paths, fakes.scripted(fakes.says("never asked")))
    ctx.store.seed_items([{"slug": "frame-000", "index": 0}])
    ctx.store.set_item_status("frame-000", "blocked")
    harness.check("a frame blocked in phase 3 holds phase 5",
                  _run_phase_5(ctx)["phases"]["review"] == "held" and ctx.model.call_count == 0)


def test_phase_5_follows_the_published_document():
    harness.check("after phase 4 the review runs", routing.route_after_finalize({"phases": {"finalize": "complete"}})
                  == "review")
    harness.check("after a blocked phase 4 the run stops: no review of a document that was never written",
                  routing.route_after_finalize({"phases": {"finalize": "blocked"}}) == "done")
    harness.check("after a failed phase 4 the run stops",
                  routing.route_after_finalize({"phases": {"finalize": "failed"}}) == "failed")
    harness.check("a run told to stop at phase 4 stops there",
                  routing.route_after_finalize({"phases": {"finalize": "complete"}, "last_phase": 4}) == "done")
    harness.check("`--phase 5` alone passes phase 4 without running it",
                  routing.route_after_frames_preparing({"phases": {"frames_preparing": "complete"}, "first_phase": 5, "last_phase": 5})
                  == "review"
                  and routing.route_after_finalize({"phases": {"finalize": "complete"}, "first_phase": 5}) == "review")
    harness.check("phase 5 is the last phase the reader's document depends on, writing its report",
                  output_contract.LAST_DOCUMENT_PHASE == output_contract.PHASE_NUMBERS["review"] == 5
                  and max(output_contract.PHASE_FILES) == output_contract.PHASE_NUMBERS["document_reshaping"] == 7)


def test_a_point_settled_with_no_edit_is_reported_as_no_change_with_nothing_lost_defect():
    # Defect: a point the reviewer resolved with no edit (the document already said it) was reported as
    # 'added after "" ""' and lost at "not placed". Fix: it reads "No change", lost nowhere
    answer = {"status": "resolved", "section": "Setup", "searches": ["read_frame_record 3 - the slide says it"]}
    harness.check("nothing is lost", review_report_utils.decide_lost_at(answer, {}) == ("already_written", "program"),
                  str(review_report_utils.decide_lost_at(answer, {})))
    todo = {"id": "TODO-01", "kind": "gap", "section": "Setup", "quote": "q", "missing": "m", "why_it_matters": "w",
            "where": "x", "status": "resolved", "replaces": "", "after": "", "sentences": [],
            "searches": answer["searches"], "lost_at": "already_written", "lost_at_by": "program"}
    report = review_report_utils.render_report({"todos": [todo], "critique": {}, "audit": {}}, {}, {})
    harness.check("the report says no change and names the reviewer's search",
                  "**No change:**" in report and "read_frame_record 3" in report and 'added after ""' not in report
                  and "nothing lost: the document already says it" in report, report[-600:])


def test_where_the_document_was_lost_is_read_off_the_sources_and_the_coverage_table():
    decide = review_report_utils.decide_lost_at
    statuses = {"F0.1": "setAside", "S0001": "setAside", "F1.1": "cited", "F0.2": "provenance"}
    cited = lambda **sources: {**RESOLVED, "sentences": [{"text": "x", **sources}]}
    harness.check("an entry the table of contents set aside was lost at the outline",
                  decide(cited(entry_ids=["F0.1"]), statuses) == ("outline", "program"))
    harness.check("an entry the document was written from was lost by its section",
                  decide(cited(entry_ids=["F1.1"]), statuses) == ("section", "program"))
    harness.check("of two sources, the one that got furthest decides",
                  decide(cited(entry_ids=["F0.1", "F1.1"]), statuses) == ("section", "program"))
    harness.check("speech the table of contents set aside was lost at the outline",
                  decide(cited(transcript_ids=["S0001"]), statuses) == ("outline", "program"))
    harness.check("a frame alone, or its on-screen text, was lost in the frame notes",
                  decide(cited(frames=[1]), statuses) == ("frame_notes", "program")
                  and decide(cited(entry_ids=["F0.2"]), statuses) == ("frame_notes", "program"))
    harness.check("an unresolved todo is not in the recording", decide(UNRESOLVED, statuses)
                  == ("not_in_recording", "program"))
    harness.check("where the sources cannot tell, the worker's word stands",
                  decide({**cited(entry_ids=["F9.9"]), "lost_at": "outline"}, {}) == ("outline", "worker"))
    harness.check("and with no word from either it is left empty", decide(cited(entry_ids=["F9.9"]), {})
                  == ("", "program"))


def test_the_reading_helpers_both_phases_share():
    harness.check("a clock is read in each form the tools print it", clock_utils.parse_clock("1:02:03") == 3723
                  and clock_utils.parse_clock("0:09") == 9 and clock_utils.parse_clock("soon") is None
                  and clock_utils.render_clock(3723) == "1:02:03")
    paged = reading_tool_utils.select_chunk(["a" * 30, "b" * 30, "c" * 30], 70, 1)
    harness.check("a read comes a chunk at a time and says there is more", "a" * 30 in paged and "c" * 30 not in paged
                  and "ask for chunk 2" in paged, paged)
    notes, answer = reading_tool_utils.update_notes(["one"], "two", None, 5)
    harness.check("a note past the notes' size is refused, and the notes are listed", notes is None
                  and "1. one" in answer, answer)
    notes, answer = reading_tool_utils.update_notes(["one"], "two", 1, 5)
    harness.check("a note may take another's place", notes == ["two"], answer)


def test_a_search_no_line_holds_whole_returns_the_lines_holding_most_of_its_words():
    """A reviewer searched five words at a time, read "No line holds all 5 words" again and again, and never narrowed
    its search (2026-09-30): the lines holding most of the words come back, the most first."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths, read_budget=5)
    found = review_reading_service.run_read(reading, WriteReadingState(), "find",
                                            {"words": "order limit zebra quartz"}).text
    harness.check("the lines holding two of the four words come back, labelled as partial",
                  "No line holds all 4 words" in found and "at least 2 of them" in found and "S0001" in found, found)
    nothing = review_reading_service.run_read(reading, WriteReadingState(), "find", {"words": "zebra quartz"}).text
    harness.check("words no line holds still say why", "No line holds both words: search each one alone" in nothing, nothing)


def test_the_program_repairs_the_published_document_before_the_critique():
    """A review that only asked what a reader cannot know passed two figures of one view, figures almost all masks, code
    spans left open and a diagram Mermaid could not draw (2026-10-01): the program removes and repairs them first."""

    import cv2
    import numpy
    from services import document_audit_service
    from utils import document_audit_utils
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    page = numpy.full((90, 160, 3), 250, dtype=numpy.uint8)
    cv2.putText(page, "Refunds", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    masked = page.copy()
    masked[10:70, 0:160] = 0
    for number, image in ((1, page), (2, page), (3, masked)):
        cv2.imwrite(str(folder / f"resources/figure-0{number}.png"), image)
    body = ("## Refunds\n\nThe refund page lists every open refund.\n\n"
            "![Refunds](resources/figure-01.png)\n\n*Figure 1. The refund page*\n\n"
            "![Refunds again](resources/figure-02.png)\n\n*Figure 2. The refund page again*\n\n"
            "![Masked](resources/figure-03.png)\n\n*Figure 3. Masked*\n\n"
            "The portal is at `portal.acme.example. It lists refunds by date.\n\n"
            "The refund page lists every open refund.\n\n"
            "```mermaid\nflowchart LR\n  Gate[\"Gateway group] --> Store<br/>  Store --> Ledger\n```\n")
    removed = document_audit_service.find_figures_to_remove(folder, body)
    harness.check("the repeated figure and the masked one go, the first stays",
                  set(removed) == {2, 3} and "figure 1" in removed[2], str(removed))
    fixed, repairs = document_audit_utils.repair_text(document_audit_utils.remove_figures(body, set(removed)))
    harness.check("their image and caption lines are gone", "figure-02" not in fixed and "Figure 3." not in fixed, fixed)
    harness.check("the open code span loses its backticks", "`" not in fixed.split("```")[0], fixed)
    harness.check("the sentence said twice stands once", fixed.count("The refund page lists every open refund.") == 1, fixed)
    harness.check("the diagram is drawable again", document_audit_utils.find_text_defects(fixed) == [],
                  str(document_audit_utils.find_text_defects(fixed)))
    harness.check("every repair is reported", len(repairs) == 3, str(repairs))


def test_a_section_named_without_its_number_is_the_numbered_heading():
    """A reviewer named "Revoking a key" for a manual's heading "8 Revoking a key", and its edit was refused as aimed
    at a section the document lacks (pdf-05)."""

    numbered = "# Manual\n\n## 7 Rotating a key\n\nA key is rotated weekly.\n\n## 8 Revoking a key\n\nA key is revoked at once.\n"
    harness.check("the quote is found under the numbered heading",
                  document_edit_utils.find_quote(numbered, "Revoking a key", "A key is revoked at once.") is not None)
    harness.check("the section is read by its name alone",
                  document_edit_utils.read_sections(numbered, "Revoking a key")[0].startswith("## 8 Revoking a key"))
    harness.check("a name with another number is not that section",
                  document_edit_utils.read_sections(numbered, "7 Revoking a key") == [])
    edited = document_edit_utils.apply_edit(numbered, {"section": "Revoking a key", "replaces": "A key is revoked at once.",
                                                       "after": "", "sentences": [{"text": "A key is revoked within a minute."}]})
    harness.check("the edit lands there", "A key is revoked within a minute." in edited, edited)


def test_read_document_says_when_a_section_was_said():
    """A reviewer read the transcript at the wrong minutes for a section, guessing (2026-10-01): a section read by its
    heading starts with the time range its phase 4 section cites."""

    from services import document_file_service
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths)
    body = ("## Ordering\n\n### Order form\n\nA fact.\n\n### Delivery dates\n\nA fact.\n\n"
            "## Decisions, action items and open points\n\n### Decisions\n\n- None.\n")
    titles = ["Order form", "Delivery dates"]
    folder = document_file_service.build_section_path(paths, "heading-000-1").parent
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.glob("heading-*.md"):
        path.unlink()
    for number, _title in enumerate(titles, start=1):
        (folder / f"heading-{number:03d}-1.md").write_text("A fact. [S0001]\n", encoding="utf-8")
    spans = review_reading_service._list_section_times(reading, body)
    harness.check("every heading gets the time of the line its section cites", len(spans) == len(titles) and titles,
                  str(spans))


def test_the_repair_never_joins_a_numbered_list():
    """The repeated-sentence pass read a numbered agenda as a paragraph and joined its items into one line
    (2026-10-01)."""

    from utils import document_audit_utils
    agenda = "## Agenda\n\n1. [Refunds](#refunds): Explains the refund limits.\n2. [Orders](#orders): Explains the order form.\n"
    fixed, repairs = document_audit_utils.repair_text(agenda)
    harness.check("the agenda keeps one item per line", fixed == agenda and repairs == [], fixed)


def test_a_quote_cut_short_with_an_ellipsis_stands_for_the_sentence_it_starts():
    """A critique quoted the start of a long sentence and "...", and the whole critique blocked on it (2026-10-01)."""

    markdown = "# Title\n\n## Target\n\nThe gateway signs tokens. The importer reads the queue, and the ledger keeps every row. Done.\n"
    span = document_edit_utils.find_quote(markdown, "Target", "The importer reads the queue, ...")
    harness.check("the quote stands for the whole sentence", span is not None and markdown[span[0]:span[1]]
                  == "The importer reads the queue, and the ledger keeps every row.", str(span))
    harness.check("a start of fewer than four words is too short to name a sentence",
                  document_edit_utils.find_quote(markdown, "Target", "The gateway ...") is None)
    span = document_edit_utils.find_quote(markdown, "Target", "The gateway signs tokens. The importer reads ... every row.")
    harness.check("a quote with words left out in its middle stands for the passage from its first part to its last",
                  span is not None and markdown[span[0]:span[1]]
                  == "The gateway signs tokens. The importer reads the queue, and the ledger keeps every row.", str(span))
    split = "# Title\n\n## Target\n\nThe gateway signs tokens today.\n\nThe ledger keeps every row.\n"
    harness.check("parts in two paragraphs are no passage",
                  document_edit_utils.find_quote(split, "Target", "The gateway signs tokens ... every row.") is None)
    # A critique quoted a long sentence from its middle with "..." before it, and blocked on it (2026-10-04)
    span = document_edit_utils.find_quote(markdown, "Target", "...and the ledger keeps every row.")
    harness.check("a quote opened with an ellipsis stands for the sentence it ends", span is not None
                  and markdown[span[0]:span[1]] == "The importer reads the queue, and the ledger keeps every row.", str(span))


def test_a_line_found_by_words_comes_with_the_lines_that_finish_its_sentence():
    """A found line stopped mid-sentence, and the half of a time range on the next line was never read (2026-10-01)."""

    transcript = [{"text": "The export pauses from 1 o'clock to"}, {"text": "3 in the morning,"}, {"text": "every night."},
                  {"text": "Something else."}, {"text": "The export is fine."}]
    harness.check("the next lines up to the sentence's end are added",
                  review_reading_service._add_sentence_ends(transcript, [0]) == [0, 1, 2])
    harness.check("a line that ends its sentence comes alone", review_reading_service._add_sentence_ends(transcript, [4]) == [4])


def test_a_number_searched_as_a_word_finds_it_written_in_digits():
    """A search for "ten developers" missed "10 people": the transcript writes most numbers as digits (2026-10-01)."""

    harness.check("ten finds 10", reading_tool_utils.is_word_in("ten", "a team of 10 people"))
    harness.check("10 finds ten", reading_tool_utils.is_word_in("10", "a team of ten people"))
    harness.check("ten does not find 100", not reading_tool_utils.is_word_in("ten", "a budget of 100 days"))


def test_a_searched_word_matches_at_a_word_start_and_a_number_whole():
    """"figure" matched "configured" and "3" matched "ABC3" and "figure-13", and those hits buried the line "Figure 3."
    a reviewer looked for, who then judged the figure missing (2026-10-02)."""

    from utils import reading_tool_utils
    for word, text, is_found in (("figure", "*figure 3. the roles file*", True),
                                 ("figure", "the switch can be configured for each stage", False),
                                 ("3", "*figure 3. the roles file*", True),
                                 ("3", "stages abc1, abc2, abc3", False),
                                 ("3", "resources/figure-13.png", False),
                                 ("token", "the tokens expire", True),
                                 ("three", "3 retries", True)):
        harness.check(f"{word!r} in {text!r}: {is_found}", reading_tool_utils.is_word_in(word, text) == is_found)


def test_removing_a_figure_renumbers_the_ones_after_it():
    """The figure the program removed before the review left the text jumping from Figure 2 to Figure 4, and the
    critique spent a todo on the gap (2026-10-02)."""

    from utils import document_audit_utils
    body = ("Text.\n\n![a](resources/figure-01.png)\n\n*Figure 1. One.*\n\n"
            "![b](resources/figure-02.png)\n\n*Figure 2. Two.*\n\nAs Figure 3 shows, it repeats.\n\n"
            "![c](resources/figure-03.png)\n\n*Figure 3. Three.*\n\nFigure 3 and Figure 1 agree.\n")
    kept = document_audit_utils.remove_figures(body, {2})
    harness.check("the figures after the removed one are numbered on, in captions and in the text",
                  "*Figure 2. Three.*" in kept and "As Figure 2 shows" in kept and "Figure 2 and Figure 1 agree" in kept
                  and "*Figure 3." not in kept and "*Figure 1. One.*" in kept, kept)
    harness.check("each image link is renumbered with its caption, so a caption never shows another number's file",
                  "![c](resources/figure-02.png)" in kept and "figure-03.png" not in kept, kept)


def test_a_read_repeated_word_for_word_is_named_and_spends_no_budget():
    """A reviewer sent the same `find` four times in one todo, each answered in full and each spending a read
    (2026-10-02)."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths, read_budget=5)
    state = WriteReadingState()
    first = review_reading_service.run_read(reading, state, "find", {"words": "order limit"}).text
    again = review_reading_service.run_read(reading, state, "find", {"words": "order limit"}).text
    harness.check("the repeat names the read that answered it and spends nothing",
                  "already made as [r1]" in again and state.reads_used == 1, again[:200])
    state.seen_reads.clear()
    fresh = review_reading_service.run_read(reading, state, "find", {"words": "order limit"}).text
    harness.check("in the next todo the same read is made again", "already made" not in fresh and state.reads_used == 2,
                  fresh[:120])


def test_a_removed_figures_file_is_set_aside_and_put_back_by_a_reset():
    """A figure the review's audit removed kept its file in resources/, and the published folder failed as holding an
    image linked nowhere (2026-10-02). It is moved into the review's folder, and a reset to phase 4 moves it back."""

    from services import document_folder_service
    output_root = Path(tempfile.mkdtemp())
    folder = output_root / "a-meeting"
    resources = folder / output_contract.RESOURCES_DIR_NAME
    resources.mkdir(parents=True)
    for number in (1, 2, 3):
        (resources / f"figure-{number:02d}.png").write_bytes(b"png")
    moved = document_folder_service.set_aside_removed_figures(output_root, folder, {2})
    harness.check("the figures after it are renamed to match the renumbered links",
                  (resources / "figure-02.png").is_file() and not (resources / "figure-03.png").exists(),
                  str(sorted(path.name for path in resources.iterdir())))
    aside = output_root / output_contract.REVIEW_DIR_NAME / output_contract.REVIEW_REMOVED_FIGURES_DIR
    harness.check("the removed figure's file leaves resources/ for the review's folder",
                  moved == ["figure-02.png"] and (aside / "figure-02.png").is_file(), str(moved))
    restored = document_folder_service.restore_removed_figures(output_root, folder)
    harness.check("a reset puts it back and the later files under their first names",
                  restored == ["figure-02.png"] and all((resources / f"figure-{number:02d}.png").is_file()
                                                        for number in (1, 2, 3)), str(restored))


def test_an_unsupported_todo_deletes_only_its_own_sentence():
    """Two deletions of one run took a supported sentence along with the unsupported one, the meeting's date and a lesson
    learned, because `replaces` held both (2026-10-03)."""

    from models.validation_report import ValidationReport
    from validators import review_validator
    quote = "The team calls itself the Fellowship of the Ledger."
    for replaces, is_refused in ((quote, False),
                                 ("The meeting was held on 2025-11-11. " + quote, True)):
        report = ValidationReport(subject="review-todo-08")
        review_validator._check_deletion_scope(report, "review-todo-08", {"replaces": replaces, "sentences": []},
                                               "unsupported", quote)
        harness.check(f"deleting {replaces[:30]!r}... is refused: {is_refused}",
                      ("RESOLUTION_DELETES_MORE" in harness.codes(report)) == is_refused, report.render_summary())


def test_a_review_sentence_about_the_recording_gets_the_review_fix():
    """The chapter's fix points to a `> **Set aside:**` line a review does not have (2026-10-03)."""

    from models.validation_report import ValidationReport
    from validators import review_validator
    report = ValidationReport(subject="review-todo-04")
    report.add("DOCUMENT_RECORDING_NARRATED", "review-todo-04: 1 sentence tells what happened in the recording",
               fix="its entry goes on the `> **Set aside:** <why> [ids]` line")
    review_validator._apply_review_fixes(report)
    harness.check("the fix says what a review does instead", "Set aside" not in report.findings[0].fix
                  and "`question`" in report.findings[0].fix, report.findings[0].fix)


def test_two_tables_of_one_template_with_other_words_are_both_kept():
    """Two slides of one deck, one table layout with other words in every cell, were removed as one figure shown twice
    before the critique (2026-10-04): a repeat looks alike and reads alike."""

    import cv2
    import numpy
    from services import document_audit_service
    from services import text_detection_service
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the figures' words", True)
        return
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    # One layout, other words: the two differ in about 4% of their pixels, under the share that reads as a repeat
    for number, rows in ((1, ("Refund limits daily", "Ledger export nightly", "Invoice archive yearly")),
                         (2, ("Gateway owners weekly", "Dispute review monthly", "Customer records hourly"))):
        table = numpy.full((600, 1500, 3), 250, dtype=numpy.uint8)
        for index, row in enumerate(rows):
            top = 30 + index * 190
            cv2.rectangle(table, (60, top), (380, top + 150), (222, 222, 222), -1)
            cv2.rectangle(table, (1150, top), (1480, top + 150), (200, 140, 100), -1)
            cv2.line(table, (20, top + 170), (1480, top + 170), (180, 180, 180), 2)
            cv2.putText(table, row, (420, top + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
        cv2.imwrite(str(folder / f"resources/figure-0{number}.png"), table)
    body = ("![Packages A to C](resources/figure-01.png)\n\n*Figure 1. The first packages*\n\n"
            "![Packages D to F](resources/figure-02.png)\n\n*Figure 2. The next packages*\n")
    removed = document_audit_service.find_figures_to_remove(folder, body)
    harness.check("both tables stay", removed == {}, str(removed))


def test_a_dark_figure_with_no_mask_is_kept_defect():
    # Eight dark terminals and slides with no mask on them were removed before the critique as 30% to 70% masked.
    # Now a figure's masked share comes from the rectangles the mask tool recorded, and one with no mask hides nothing

    import json
    import cv2
    import numpy
    from services import document_audit_service
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    terminal = numpy.zeros((90, 160, 3), dtype=numpy.uint8)
    cv2.putText(terminal, "$ cargo run", (8, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    other = terminal.copy()
    cv2.putText(other, "ledger: ok", (8, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 220, 120), 1)
    cv2.imwrite(str(folder / "resources/figure-01.png"), terminal)
    cv2.imwrite(str(folder / "resources/figure-02.png"), other)
    (folder / "provenance.json").write_text(json.dumps({"images": [
        {"figure": 1, "masks": 0},
        {"figure": 2, "masks": 1, "maskedAreas": [{"rect": [0, 0, 160, 60], "words": "token"}]}]}), encoding="utf-8")
    body = ("![A terminal](resources/figure-01.png)\n\n*Figure 1. The terminal*\n\n"
            "![A masked terminal](resources/figure-02.png)\n\n*Figure 2. The terminal, its token hidden*\n")
    removed = document_audit_service.find_figures_to_remove(folder, body)
    harness.check("the dark figure with no mask stays, the one two thirds masked goes",
                  set(removed) == {2} and "67%" in removed[2], str(removed))


def test_the_audit_is_worked_out_again_until_the_critique_is_asked():
    """A resume after a fix to the audit's rules kept the figure the old rules removed (2026-10-04)."""

    import cv2
    import numpy
    from services import review_conversation_service
    from services import review_file_service
    from support import fixtures as fx
    paths = fx.make_paths()
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    page = numpy.full((90, 160, 3), 250, dtype=numpy.uint8)
    cv2.putText(page, "Refunds", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    cv2.imwrite(str(folder / "resources/figure-01.png"), page)
    before = review_file_service.build_before_path(paths)
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_text("## Refunds\n\n![Refunds](resources/figure-01.png)\n\n*Figure 1. The refund page*\n", encoding="utf-8")
    stale = {"removedFigures": {"1": "an old rule"}, "repairs": []}
    pending = {"critique": {"status": "pending", "attempts": 0, "reason": ""}, "todos": None, "audit": dict(stale)}
    review_conversation_service.record_document_audit(paths, folder, pending)
    asked = {"critique": {"status": "accepted", "attempts": 1, "reason": ""}, "todos": [], "audit": dict(stale)}
    review_conversation_service.record_document_audit(paths, folder, asked)
    harness.check("before the critique the audit follows the rules as they are now",
                  pending["audit"]["removedFigures"] == {}, str(pending["audit"]))
    harness.check("after it the audit the critique read stays", asked["audit"] == stale, str(asked["audit"]))


def test_a_clause_quoted_from_inside_a_sentence_with_a_capital_is_found():
    """A critique quoted the clause after a semicolon with its first letter a capital, and was refused as quoting
    nothing the document holds."""

    markdown = '# Orders\n\n## Export\n\nThe export fails; currently, it reports "Error" because the service is offline.\n'
    harness.check("the recased clause is found where it stands",
                  document_edit_utils.find_quote(markdown, "Export",
                                                 'Currently, it reports "Error" because the service is offline.')
                  is not None)
    harness.check("a sentence the document does not hold is still not found",
                  document_edit_utils.find_quote(markdown, "Export", "Nothing like this is here.") is None)


def test_the_figure_records_are_renumbered_when_the_review_drops_a_figure():
    """The published document and the image files are renumbered when a figure goes; the record of which frame each
    figure comes from was not, so from the first dropped figure on every record named the wrong frame (2026-10-06)."""

    images = [{"figure": 1, "file": "resources/figure-01.png", "frame": "frame-003", "caption": "one"},
              {"figure": 2, "file": "resources/figure-02.png", "frame": "frame-008", "caption": "two"},
              {"figure": 3, "file": "resources/figure-03.png", "frame": "frame-014", "caption": "three"},
              {"figure": 4, "file": "resources/figure-04.png", "frame": "frame-021", "caption": "four"}]
    kept = review_document_node._list_kept_images(images, {2})
    harness.check("the dropped figure's record goes with it",
                  [image["frame"] for image in kept] == ["frame-003", "frame-014", "frame-021"], str(kept))
    harness.check("the ones after it are numbered as the document numbers them now, file and all",
                  [(image["figure"], image["file"]) for image in kept]
                  == [(1, "resources/figure-01.png"), (2, "resources/figure-02.png"), (3, "resources/figure-03.png")],
                  str(kept))
    harness.check("their captions still belong to the frames they always did",
                  [(image["figure"], image["caption"]) for image in kept] == [(1, "one"), (2, "three"), (3, "four")],
                  str(kept))


def test_the_notes_a_quoted_sentence_was_written_from_are_found_in_its_section():
    """The published document drops the note ids; the section files keep them (2026-10-08)."""

    from services import document_file_service
    paths = fx.make_paths()
    document_file_service.write_section(paths, "heading-003-1",
                                        "Raising the order limit needs a second approver. [F12.1, S0415]\n\nOther text. [F13.2]\n")
    harness.check("the ids closing the block that holds the quote",
                  document_file_service.find_quote_sources(paths, "Raising the order limit needs a second approver.")
                  == ["F12.1", "S0415"], "")
    harness.check("none for a sentence no section holds",
                  document_file_service.find_quote_sources(paths, "A sentence nobody wrote.") == [], "")


def test_a_resolution_citing_a_frame_it_never_read_is_refused():
    """A resolution cited 'read_frame_record frame 14: confirms …' as its source with no read made (2026-10-08)."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    log = paths.output_root / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": "todo-07", "calls": [{"name": "read_frame_record", "args": {"frame": 3}}]}) + "\n")
    _write_answer(paths, "todo-07", {**RESOLVED, "searches": ["read_frame_record frame 3: holds the limit"]})
    harness.check("a frame read in this todo may be cited",
                  "RESOLUTION_SEARCH_NOT_MADE" not in _codes(review_validator.validate_resolution(paths, "todo-07")))
    _write_answer(paths, "todo-07", {**RESOLVED, "searches": ["read_frame_record frame 14: confirms the limit"]})
    report = review_validator.validate_resolution(paths, "todo-07")
    harness.check("a frame never read is RESOLUTION_SEARCH_NOT_MADE, naming the frames that were",
                  "RESOLUTION_SEARCH_NOT_MADE" in _codes(report)
                  and any("frames read so far: 3" in finding.detail for finding in report.findings),
                  str([finding.detail for finding in report.findings]))
    # The todo before read frame 4, and the next todo named it: refused, the review blocked (pdf-02-report-v2)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": "todo-08", "calls": [{"name": "find", "args": {"words": "limit"}}]}) + "\n")
    _write_answer(paths, "todo-08", {**RESOLVED, "searches": ["read_frame_record frame 3: holds the limit"]})
    harness.check("a frame an earlier todo of the conversation read may be cited",
                  "RESOLUTION_SEARCH_NOT_MADE" not in _codes(review_validator.validate_resolution(paths, "todo-08")))


def test_a_sentence_deleted_by_the_review_was_lost_at_the_section():
    """Twelve demo sentences the review deleted were reported as "not placed"; the section wrote them (2026-10-08)."""

    from utils import review_report_utils
    deleted = {"status": "resolved", "section": "Ordering", "replaces": "The page is opened to show the form.",
               "sentences": [], "searches": []}
    harness.check("a deletion is written by the section, decided by the program",
                  review_report_utils.decide_lost_at(deleted, {}) == ("section_written", "program"),
                  str(review_report_utils.decide_lost_at(deleted, {})))


def test_a_transcript_read_in_a_time_range_returns_lines_holding_one_of_its_words():
    """A reviewer asked for three words between two times, no line held two, and it was told only to ask again, six
    times in one review (2026-10-08): within a time range, a line holding one of the words comes back."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths, read_budget=5)
    found = review_reading_service.run_read(reading, WriteReadingState(), "read_transcript",
                                            {"words": "order zebra quartz", "start": "0:00", "end": "59:00"}).text
    harness.check("a line holding one of the words in the range comes back, labelled",
                  "the lines holding one of them" in found and "S0001" in found, found)
    unbounded = review_reading_service.run_read(reading, WriteReadingState(), "read_transcript",
                                                {"words": "order zebra quartz"}).text
    harness.check("with no time range the search stays strict", "the lines holding one of them" not in unbounded, unbounded)


def test_a_missing_published_document_beside_phase_4s_file_is_refused_by_name():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    document_folder_service.build_document_path(paths).unlink()
    ctx = _load_run_context(paths, fakes.scripted(fakes.says("never asked")))
    try:
        status = _run_phase_5(ctx)["phases"]["review"]
    except KeyError as error:
        status = f"raised KeyError {error}"
    harness.check("phase 5 fails by name and calls nothing", status == "failed" and ctx.model.call_count == 0, status)


def test_a_resolved_answer_cannot_say_its_point_is_not_in_the_recording():
    """Only the program says a todo is not in the recording, for an unresolved one: a resolved answer the coverage table
    cannot place would otherwise be reported resolved from the sources and not in the recording at once."""

    resolved = {"status": "resolved", "lost_at": "not_in_recording"}
    try:
        review_actions.Resolution.model_validate(resolved)
        is_refused = False
    except ValueError:
        is_refused = True
    harness.check("a resolution's lost_at never names the program's own not_in_recording", is_refused)
    harness.check("the steps a worker may name are still accepted",
                  review_actions.Resolution.model_validate({**resolved, "lost_at": "outline"}).lost_at == "outline")


def test_a_replaced_note_keeps_its_number():
    notes, _ = reading_tool_utils.update_notes(["a", "b", "c"], "x", 1, 100)
    harness.check("note 1 is overwritten in place, so notes 2 and 3 keep their numbers", notes == ["x", "b", "c"], str(notes))


def test_figures_after_a_removed_one_are_renumbered_by_number_past_ninety_nine():
    root = Path(tempfile.mkdtemp())
    folder = root / "published"
    resources = folder / output_contract.RESOURCES_DIR_NAME
    resources.mkdir(parents=True)
    for number in range(1, 102):
        (resources / document_folder_service.build_figure_file_name(number)).write_text(str(number), encoding="utf-8")
    document_folder_service.set_aside_removed_figures(root, folder, {1})
    harness.check("the old figure 101 is figure 100 now, and the old 100 is 99",
                  (resources / "figure-100.png").read_text(encoding="utf-8") == "101"
                  and (resources / "figure-99.png").read_text(encoding="utf-8") == "100",
                  (resources / "figure-100.png").read_text(encoding="utf-8"))


def test_a_reading_argument_that_is_no_number_is_answered_not_raised():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths)
    answers = []
    for name, args in (("look_at_frame", {"frame": "frame 12"}), ("find", {"words": "order", "chunk": "2 of 4"}),
                       ("read_frame_record", {"frame": "twelve"})):
        try:
            answers.append(review_reading_service.run_read(reading, WriteReadingState(), name, args).text[:60])
        except (TypeError, ValueError) as exc:
            answers.append(f"raised {type(exc).__name__}")
    harness.check("each is answered", not any(answer.startswith("raised") for answer in answers), str(answers))


def test_a_critique_that_kept_part_of_its_answer_is_listed_as_answered():
    lines = review_conversation_service._list_done_lines({"critique": {"status": "partial"}, "todos": []})
    harness.check("a partial critique is a done line", lines == ["[critique: partial]"], str(lines))


def test_a_dropped_todo_is_never_named_by_an_id_a_kept_todo_takes():
    paths = fx.make_paths()
    document = "# Doc\n\n## Ordering\n\nThe form has two fields.\n"
    good = {"kind": "gap", "section": "Ordering", "quote": "The form has two fields.", "missing": "which fields",
            "why_it_matters": "a reader cannot fill it", "where": "the transcript"}
    bad = {**good, "quote": "A sentence the document does not hold."}
    review_file_service.write_answer(paths, review_conversation_service.TODOS_SLUG, {"todos": [bad, good]})
    kept, dropped = review_conversation_service._split_last_answer(paths, document, 10, False)
    kept_ids = [review_file_service.build_todo_id(number) for number in range(1, len(kept) + 1)]
    harness.check("the dropped todo's label is no id a kept todo carries",
                  len(kept) == 1 and dropped and dropped[0]["answerId"] not in kept_ids, f"{kept_ids} {dropped}")


def test_the_reading_tools_take_the_arguments_a_model_sends_as_text():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths)
    state = WriteReadingState()
    review_reading_service.run_read(reading, state, "note", {"text": "A first note."})
    try:
        replaced = review_reading_service.run_read(reading, state, "note", {"text": "A better note.", "replaces": "1"}).text
        refused = review_reading_service.run_read(reading, state, "note", {"text": "Another.", "replaces": "first"}).text
    except Exception as exc:
        replaced = refused = f"{type(exc).__name__}: {exc}"
    harness.check("a note number sent as text replaces that note", state.notes == ["A better note."], replaced)
    harness.check("a note number that is no number is answered by name", "`replaces`" in refused, refused)
    one_id = review_reading_service.run_read(reading, state, "read_entries", {"ids": "F1.1"}).text
    harness.check("one id sent as text is read as that id", "Raising the order limit" in one_id, one_id)


def test_find_never_counts_the_label_of_a_line_as_its_words():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths)
    found = review_reading_service.run_read(reading, WriteReadingState(), "find", {"words": "transcript limit zebra"}).text
    harness.check("a line holding only `limit` is not listed as holding two of the words", "S0001" not in found, found)


def test_a_broken_diagram_right_under_a_paragraph_is_found():
    from utils import document_audit_utils

    body = '## Flow\n\nLead-in:\n```mermaid\nflowchart LR\n  A["open label] --> B\n```\n'
    defects = document_audit_utils.find_text_defects(body)
    harness.check("the diagram Mermaid cannot draw is named", any("Mermaid" in defect["problem"] for defect in defects),
                  str(defects))


def test_a_quote_with_an_ellipsis_is_found_when_its_later_part_drops_the_emphasis():
    markdown = "## Ordering\n\nAlpha beta gamma delta epsilon keep the order of the *new* flow as it was shown.\n"
    span = document_edit_utils.find_quote(markdown, "Ordering", "Alpha beta gamma delta … the new flow")
    harness.check("found from its first word to its last", span is not None
                  and markdown[span[0]:span[1]].startswith("Alpha") and markdown[span[0]:span[1]].endswith("flow"),
                  str(span))


def test_a_table_and_a_paragraph_over_two_lines_are_published_as_written():
    from utils import document_block_utils

    markdown = ("# Weekly\n\n## Limits\n\n| Limit | Value |\n|---|---|\n| Refund parts | 3 |\n\n"
                "A paragraph written\nover two lines. [F1.1]\n")
    blocks = document_block_utils.split_into_blocks(markdown, {})
    rendered = document_block_utils.render_blocks(blocks, {})
    harness.check("the table's rows stay together, and the paragraph's lines", "| Limit | Value |\n|---|---|\n| Refund parts | 3 |"
                  in rendered and "A paragraph written\nover two lines." in rendered, rendered)


def test_no_edit_reaches_a_subsection_heading():
    markdown = "## A\n\nIntro.\n\n### Limits apply\n\nText.\n"
    harness.check("the heading's words are not found as a quote of the section",
                  document_edit_utils.find_quote(markdown, "A", "Limits apply") is None,
                  str(document_edit_utils.find_quote(markdown, "A", "Limits apply")))


def test_the_finder_names_what_the_repair_will_do():
    from utils import document_audit_utils

    sentence = "The gateway sends every refund request to the bank at once."
    body = f"## A\n\n{sentence}\n\n## B\n\nThe gateway sends every `refund request to the bank at once.\n"
    found = [defect["problem"] for defect in document_audit_utils.find_text_defects(body)]
    repairs = document_audit_utils.repair_text(body).repairs
    harness.check("the repeat the repair removes is found too",
                  any("twice" in problem for problem in found) == any("said earlier" in line for line in repairs),
                  f"found {found}, repairs {repairs}")


def test_an_edit_after_a_sentence_with_nothing_to_add_changes_nothing():
    markdown = "## A\n\nIntro. More text.\n"
    edited = document_edit_utils.apply_edit(markdown, {"section": "A", "after": "Intro.", "sentences": []})
    harness.check("the document as it was", edited == markdown, repr(edited))


def test_the_repair_keeps_a_decision_line_and_a_table_under_their_sentence():
    from utils import document_audit_utils

    body = ("## Limits\n\nA refund is split in three parts.\n> **Decision:** keep the limit.\n\n"
            "The totals per tenant follow.\n| Tenant | Total |\n|---|---|\n| A | 3 |\n")
    harness.check("each line stays on its own line", document_audit_utils.repair_text(body).text == body,
                  document_audit_utils.repair_text(body).text)


def test_a_long_sentence_removed_as_said_earlier_is_quoted_to_a_whole_word():
    """The review report quoted a removed sentence cut at 80 characters, mid-word, with nothing to say it went on."""

    from utils import document_audit_utils

    sentence = ("Asked whether the gateway sends every refund request to the bank at once, the team answered that it "
                "remains open.")
    body = f"## Refunds\n\n{sentence}\n\n## Payouts\n\n{sentence}\n"
    repairs = document_audit_utils.repair_text(body).repairs
    harness.check("the quote ends on a whole word and says it is cut", len(repairs) == 1
                  and repairs[0].endswith(' at once, the …"'), str(repairs))


def test_a_summary_sentence_a_chapter_repeats_leaves_the_chapter_as_it_is():
    from models import output_contract
    from utils import document_audit_utils

    sentence = "The gateway sends every refund request to the bank at once."
    body = f"## {output_contract.EXECUTIVE_SUMMARY_HEADING}\n\n{sentence}\n\n## Refunds\n\n{sentence}\n"
    harness.check("no defect, and the chapter keeps its sentence", document_audit_utils.find_text_defects(body) == []
                  and document_audit_utils.repair_text(body).text == body, str(document_audit_utils.find_text_defects(body)))


def test_a_figure_right_under_a_chapter_heading_cites_nothing_of_the_chapter_before():
    from utils import document_block_utils

    markdown = ("# Weekly\n\n## Chapter 1\n\nA refund is split in three. [F1.1]\n\n## Chapter 2\n\n"
                "![The form](04_writing/images/cuts/cut-07.png)\n\n*The refund form*\n")
    blocks = document_block_utils.split_into_blocks(markdown, {"A refund is split in three. [F1.1]": ["F1.1"]})
    harness.check("the paragraph of chapter 1 cites F1.1",
                  any(block.get("entryIds") == ["F1.1"] for block in blocks), str(blocks))
    converted, _ = document_block_utils.convert_placed_images(blocks, {"cut-07": {"frame": "frame-002"}})
    image = next(block for block in converted if block["kind"] == "image")
    harness.check("the figure cites no entry of chapter 1", image["entryIds"] == [], str(image))


def test_a_frame_read_logged_with_no_frame_is_no_crash():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    log = paths.output_root / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": "todo-08", "calls": [{"name": "look_at_frame", "args": {}}]}) + "\n")
    _write_answer(paths, "todo-08", {**RESOLVED, "searches": ["read_frame_record frame 14: confirms the limit"]})
    try:
        codes = _codes(review_validator.validate_resolution(paths, "todo-08"))
    except ValueError as error:
        codes = {f"raised: {error}"}
    harness.check("a search naming an unread frame is reported, never raised", "RESOLUTION_SEARCH_NOT_MADE" in codes,
                  str(codes))


def test_a_shortened_copy_of_the_replaced_sentence_needs_its_sources():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_answer(paths, "todo-01", {**RESOLVED, "after": "", "replaces": "Figure 1 has both fields.",
                                     "sentences": [{"text": "Figure 1 has both"}]})
    harness.check("a part of the replaced sentence is a new sentence, and cites no source",
                  "RESOLUTION_NO_SOURCE" in _codes(review_validator.validate_resolution(paths, "todo-01")))


def test_a_replaces_of_only_spaces_is_no_target_and_a_settled_answer_s_question_is_checked():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_answer(paths, "todo-01", {**RESOLVED, "replaces": "   "})
    harness.check("an `after` beside a `replaces` of spaces is the target", review_validator.validate_resolution(
        paths, "todo-01").ok)
    settled = {**RESOLVED, "after": "", "sentences": [], "searches": ["the section already says it"],
               "question": "Who raised it at 14:05?"}
    _write_answer(paths, "todo-02", settled)
    harness.check("a settled answer's question with a clock time is never refused for it",
                  "DOCUMENT_TIMESTAMP" not in _codes(review_validator.validate_resolution(paths, "todo-02")))
    harness.check("the text checks find the clock time, so it stays out of the open points",
                  review_validator.list_question_problems(settled["question"]) == ["DOCUMENT_TIMESTAMP"])


def test_an_unresolved_question_the_text_checks_refuse_is_left_out_never_the_reason_of_a_block_defect():
    # Defect: an unresolved review answer asked whether a word in "the notes F5.1 and F5.3" was misheard "on the
    # slide"; the document's text checks refused the answer and the run blocked. Fix: the answer stands, and its
    # question stays out of the open points
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_reads(paths, "todo-04", ["find"])
    question = 'Is "Gatway" in the notes F5.1 and F5.3 a misheard name for the system on the slide?'
    _write_answer(paths, "todo-04", {**UNRESOLVED, "question": question})
    report = review_validator.validate_resolution(paths, "todo-04")
    harness.check("the unresolved answer passes", report.ok, report.render_summary())
    harness.check("its question is found to hold an id and a sentence about the recording",
                  set(review_validator.list_question_problems(question))
                  == {"DOCUMENT_ENTRY_ID_IN_TEXT", "DOCUMENT_RECORDING_NARRATED"},
                  str(review_validator.list_question_problems(question)))
    harness.check("a question about the subject is kept", review_validator.list_question_problems(
        UNRESOLVED["question"]) == [])


def test_a_replaces_of_spaces_carries_no_sentence_over():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_answer(paths, "todo-01", {**RESOLVED, "replaces": "   ", "sentences": [{"text": QUOTE}]})
    harness.check("a sentence copying the `after` one is not added again",
                  review_file_service.read_answer(paths, "todo-01")["sentences"] == []
                  and review_validator.validate_resolution(paths, "todo-01").ok)


def test_a_search_naming_a_frame_with_leading_zeros_counts_the_frame_read():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    log = paths.output_root / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": "todo-09", "calls": [{"name": "look_at_frame", "args": {"frame": 3}}]}) + "\n")
    _write_answer(paths, "todo-09", {**RESOLVED, "searches": ["look_at_frame frame-003: holds the limit"]})
    harness.check("frame-003 is frame 3", "RESOLUTION_SEARCH_NOT_MADE" not in _codes(
        review_validator.validate_resolution(paths, "todo-09")))


def test_a_search_naming_a_frame_never_read_is_dropped_when_a_made_search_remains():
    """A reviewer listed "read_frame_record 4" for a frame it had only found words on, and was refused (pdf-02): the
    program knows what was read, so it keeps the real searches. With none left, the answer is refused."""

    from services import review_file_service

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    log = paths.output_root / output_contract.REVIEW_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": "todo-09", "calls": [{"name": "look_at_frame", "args": {"frame": 3}}]}) + "\n")
    _write_answer(paths, "todo-09", {**RESOLVED, "searches": ["look_at_frame 3: holds the limit",
                                                              "look_at_frame 4: shows the table"]})
    harness.check("the search of a frame never read is dropped, the real one kept",
                  review_file_service.read_answer(paths, "todo-09")["searches"] == ["look_at_frame 3: holds the limit"])
    harness.check("and the answer is not refused for it", "RESOLUTION_SEARCH_NOT_MADE" not in _codes(
        review_validator.validate_resolution(paths, "todo-09")))
    _write_answer(paths, "todo-10", {**RESOLVED, "searches": ["look_at_frame 4: shows the table"]})
    harness.check("an answer naming only reads never made is still refused", "RESOLUTION_SEARCH_NOT_MADE" in _codes(
        review_validator.validate_resolution(paths, "todo-10")))


def test_a_gap_s_fix_keeps_the_sentence_it_replaces():
    """A fix replaced the manual's expiry rules with a renewal step no source states, and the rules left the document
    (pdf-05)."""

    from models.validation_report import ValidationReport

    from services import document_file_service

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    sentence = "Sandbox keys expire after 14 days; live keys never expire but can be revoked."
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {"replaces": sentence, "sentences": [
        {"text": "To renew an expired sandbox key, run the same command again."}]}, "gap")
    harness.check("a fix that loses the sentence is refused", "RESOLUTION_DROPS_TEXT" in harness.codes(report),
                  report.render_summary())
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {"replaces": sentence, "sentences": [
        {"text": sentence + " A sandbox key is issued with the key issue command."}]}, "gap")
    harness.check("a fix that keeps it and adds passes", "RESOLUTION_DROPS_TEXT" not in harness.codes(report),
                  report.render_summary())
    # pdf-06: naming who set a rule rewords its sentence and keeps 12 of its 16 words
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {
        "replaces": "A requirement is established that the backup job must run every four hours before any partner is "
                    "pinned to Dublin.",
        "sentences": [{"text": "Jonas requires the backup job to run every four hours before any partner is pinned to "
                               "Dublin."}]}, "gap")
    harness.check("a fix that rewords the sentence to add who passes", "RESOLUTION_DROPS_TEXT" not in harness.codes(report),
                  report.render_summary())
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {"replaces": sentence, "sentences": [
        {"text": "Sandbox keys last two weeks."}]}, "unsupported")
    harness.check("an unsupported sentence may be rewritten", "RESOLUTION_DROPS_TEXT" not in harness.codes(report))
    # pdf-05: "Error 401 means the token expired; ..." was replaced by a second cause of the error alone
    document_file_service.write_section(paths, "heading-003-1", f"{QUOTE} [F1.1]\n")
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {"replaces": QUOTE, "sentences": [
        {"text": "A team lead may also raise the limit."}]}, "unsupported")
    harness.check("an unsupported sentence its own notes state is kept like a gap's",
                  "RESOLUTION_DROPS_TEXT" in harness.codes(report), report.render_summary())
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {"replaces": QUOTE, "sentences": []},
                                      "unsupported")
    harness.check("and may still be deleted", "RESOLUTION_DROPS_TEXT" not in harness.codes(report))


def test_a_gap_s_fix_may_delete_a_line_that_states_nothing_defect():
    # A bare open point of two words ("Open: Sandbox renewal") asks nothing; its gap's fix deleted it and was refused as
    # dropping text, which blocked phase 5. A line of under four words deleted outright drops no statement
    from models.validation_report import ValidationReport

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03",
                                      {"replaces": "> **Open:** sandbox renewal", "sentences": []}, "gap")
    harness.check("deleting a two-word open point passes", "RESOLUTION_DROPS_TEXT" not in harness.codes(report),
                  report.render_summary())
    report = ValidationReport(subject="review-todo-03")
    review_validator._check_text_kept(report, paths, "review-todo-03", {
        "replaces": "> **Open:** sandbox renewal", "sentences": [{"text": "Keys are issued by the platform team."}]}, "gap")
    harness.check("rewriting it into another sentence is still refused", "RESOLUTION_DROPS_TEXT" in harness.codes(report),
                  report.render_summary())


def test_deleting_the_todos_sentence_with_its_code_marks_is_no_more_than_it():
    """The todo quoted a sentence without its code marks and the answer copied it from the document with them:
    "ending in KeyboardInterrupt." against "ending in `KeyboardInterrupt`.", refused as deleting more (2026-10-12)."""

    from models.validation_report import ValidationReport
    report = ValidationReport(subject="review-todo-06")
    review_validator._check_deletion_scope(report, "review-todo-06",
                                           {"replaces": "The console shows a trace ending in `RetryError`.", "sentences": []},
                                           "unsupported", "The console shows a trace ending in RetryError.")
    harness.check("the same sentence with its backticks is no more than it", "RESOLUTION_DELETES_MORE" not in
                  harness.codes(report), report.render_summary())


def test_deleting_the_whole_sentence_an_elided_quote_names_is_its_own_sentence():
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    answer = {**RESOLVED, "after": "", "replaces": QUOTE, "sentences": []}
    _write_answer(paths, "todo-10", answer)
    report = review_validator.validate_resolution(paths, "todo-10", "unsupported", "Raising the order limit … step.")
    harness.check("the whole sentence an elided quote names is no more than it", "RESOLUTION_DELETES_MORE" not in
                  _codes(report), str(_codes(report)))
    report = review_validator.validate_resolution(paths, "todo-10", "unsupported", "Raising the order limit … step")
    harness.check("nor without its full stop", "RESOLUTION_DELETES_MORE" not in _codes(report))
    _write_answer(paths, "todo-11", {**answer, "replaces": f"{QUOTE} Figure 1 has both fields."})
    harness.check("a second sentence beside it is still more", "RESOLUTION_DELETES_MORE" in _codes(
        review_validator.validate_resolution(paths, "todo-11", "unsupported", "Raising the order limit … step.")))


def test_a_todo_about_a_figure_is_asked_to_be_closed_with_no_edit_and_that_passes():
    """A reviewer told only in its instructions to leave figures alone spent three answers editing around a figure's
    caption and was refused each time (2026-10-12)."""

    todo = {"id": "TODO-01", "kind": "contradiction", "section": "Ordering", "missing": "the caption names another form",
            "where": "frame 3", "quote": "*Figure 4. the retry form with its two fields*"}
    text = review_messages.build_resolve_text(todo, 6, ["F3.1"])
    harness.check("a caption todo is asked to be closed with no edit", "Close this todo now" in text and "no `replaces`" in text,
                  text)
    plain = review_messages.build_resolve_text({**todo, "quote": "Only a lead may raise the limit."}, 6, ["F3.1"])
    harness.check("a sentence todo is asked as before", "Close this todo now" not in plain and "Find it in the sources" in plain)
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    _write_answer(paths, "todo-12", {"status": "resolved", "section": "Ordering", "replaces": "", "after": "", "sentences": [],
                                     "searches": ["the review leaves figures as they are"], "question": ""})
    report = review_validator.validate_resolution(paths, "todo-12", "contradiction", todo["quote"])
    harness.check("closing it with no edit passes the checks", report.ok, report.render_summary())


def test_the_critique_looks_at_every_placed_figure_and_only_those():
    """The critique read the document's text only, and a placed figure cut through its slide's left side passed it
    unseen; cuts made and never placed are not sent (the owner, 2026-10-12)."""

    import tempfile
    from pathlib import Path
    from prompts import review_messages
    from schemas import review_actions
    from services import write_conversation_service
    from utils import review_report_utils
    import cv2
    import numpy
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    for name in ("figure-01.png", "figure-02.png", "figure-03.png"):
        cv2.imwrite(str(folder / "resources" / name), numpy.full((40, 80, 3), 255, numpy.uint8))
    document = ("Text.\n\n![The gateway's retries](resources/figure-02.png \"Retries\")\n\nMore.\n\n"
                "![The budget table](resources/figure-01.png)\n\n![Again](resources/figure-02.png)\n\n"
                "![Gone](resources/figure-09.png)\n")
    figures = review_messages.list_placed_figures(document, folder)
    harness.check("the placed figures in their order, once each, only those on disk",
                  [path.name for path in figures] == ["figure-02.png", "figure-01.png"], str(figures))
    message = write_conversation_service.build_figures_message(figures)
    harness.check("they are sent as images under one line naming them, each after its own name",
                  len(message.content) == 5 and "figure-02.png, figure-01.png" in message.content[0]["text"]
                  and message.content[1]["text"] == "figure-02.png:" and message.content[3]["text"] == "figure-01.png:",
                  str(message.content[0]))
    harness.check("a todo may be about a figure",
                  review_actions.ReviewTodo(kind="figure", section="Retries", quote="![The gateway's retries](x)",
                                            missing="the left side of the slide", why_it_matters="a reader cannot read it",
                                            where="frame 4").kind == "figure")
    harness.check("a figure point is filed under phase 4's cut, never as a step of the notes",
                  "figure_cut" in review_report_utils.LOST_AT_LABELS
                  and "figure_cut" not in review_report_utils._STEP_ORDER)


def test_a_figure_todo_may_replace_its_figure_with_a_markdown_block_or_remove_it():
    """A figure that pictures code or a table is replaced by that block, or taken out when the text says what it shows;
    the figure goes whole, with its caption and its box (the owner, 2026-10-12)."""

    document = ("Intro.\n\n![The retry rule](resources/figure-01.png)\n\n*Figure 1. The retry rule*\n\n"
                "> **What the figure shows:** a method returning 5 or 3.\n\nAfter.\n\n"
                "![The table](resources/figure-02.png)\n\n*Figure 2. The table*\n\nEnd.\n")
    block = "```python\ndef max_attempts(order):\n    return 5 if order.is_express else 3\n```"
    replaced = document_edit_utils.apply_edit(document, {"figure_action": "replace",
                                                         "replaces": "![The retry rule](resources/figure-01.png)",
                                                         "markdown_block": block})
    harness.check("the figure, its caption and its box give way to the block, the other figure stays",
                  "figure-01" not in replaced and "Figure 1." not in replaced and "What the figure shows" not in replaced
                  and block in replaced and "![The table](resources/figure-02.png)" in replaced
                  and replaced.startswith("Intro.\n\n```python"), replaced)
    removed = document_edit_utils.apply_edit(document, {"figure_action": "remove",
                                                        "replaces": "![The table](resources/figure-02.png)"})
    harness.check("a removed figure leaves no caption and no gap", "figure-02" not in removed and "Figure 2." not in removed
                  and "After.\n\nEnd.\n" in removed, removed)

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    line = "![Screenshot of the order form](resources/figure-01.png)"
    good = {"status": "resolved", "section": "", "figure_action": "replace", "replaces": line,
            "markdown_block": "| Field | Value |\n|---|---|\n| Name | Jonas |", "block_frames": [0], "searches": []}
    _write_answer(paths, "todo-01", good)
    report = review_validator.validate_resolution(paths, "todo-01", "figure", line)
    harness.check("a figure replaced by a table read off its frame passes", report.ok, report.render_summary())
    for label, answer, kind in (
            ("a figure action on a todo that is not about a figure", good, "gap"),
            ("a line that is no figure of the document", {**good, "replaces": "![Other](resources/figure-09.png)"}, "figure"),
            ("a block that is no markdown block", {**good, "markdown_block": "The form has two fields."}, "figure"),
            ("a fence left open", {**good, "markdown_block": "```python\nx = 1"}, "figure"),
            ("a block read off no frame", {**good, "block_frames": []}, "figure")):
        _write_answer(paths, "todo-01", answer)
        harness.check(f"{label} is RESOLUTION_FIGURE_ACTION",
                      "RESOLUTION_FIGURE_ACTION" in _codes(review_validator.validate_resolution(paths, "todo-01", kind, line)))


def test_a_figure_todo_may_recut_its_figure_and_the_old_image_is_kept():
    """A figure cut through its content is recut from its frame with phase 4's tools; the cut's image takes the figure's
    file, the old one is kept beside the review (the owner, 2026-10-12)."""

    import cv2
    import numpy
    from schemas import review_actions
    from services import document_file_service
    from services import document_folder_service
    from services import review_conversation_service
    harness.check("the review offers phase 4's frame tools", {"look", "cut", "undo_cut"} <= set(review_actions.READ_TOOLS))
    harness.check("a recut keeps the document's text", document_edit_utils.apply_edit(
        "A.\n\n![x](resources/figure-01.png)\n", {"figure_action": "recut", "replaces": "![x](resources/figure-01.png)"})
        == "A.\n\n![x](resources/figure-01.png)\n")
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    _prepare_working_document(paths)
    line = "![Screenshot of the order form](resources/figure-01.png)"
    recut = {"status": "resolved", "section": "", "figure_action": "recut", "replaces": line, "cut": "cut-07",
             "searches": []}
    _write_answer(paths, "todo-01", recut)
    harness.check("a recut naming no cut made is RESOLUTION_FIGURE_ACTION",
                  "RESOLUTION_FIGURE_ACTION" in _codes(review_validator.validate_resolution(paths, "todo-01", "figure", line)))
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-07.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cut_file), numpy.full((30, 60, 3), 90, numpy.uint8))
    store = {"cuts": {"cut-07": {"file": str(cut_file.relative_to(paths.output_root)), "frame": "frame-000"}}}
    document_file_service.write_image_store(paths, store)
    report = review_validator.validate_resolution(paths, "todo-01", "figure", line)
    harness.check("a recut naming a cut made passes", report.ok, report.render_summary())
    figure = document_folder_service.build_document_dir(paths) / "resources" / "figure-01.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(figure), numpy.full((20, 20, 3), 255, numpy.uint8))
    swapped = review_conversation_service._write_recut_figure(paths, recut, store)
    kept = review_file_service.build_review_dir(paths) / "figures-before" / "figure-01.png"
    harness.check("the cut's image takes the figure's file, the old one kept",
                  swapped == figure and cv2.imread(str(figure)).shape[:2] == (30, 60)
                  and cv2.imread(str(kept)).shape[:2] == (20, 20))


def test_a_transcript_line_cited_without_its_leading_zeros_is_read_as_that_line():
    sentence = review_actions.NewSentence.model_validate(
        {"text": "The refund limit is three parts.", "transcript_ids": ["S081", "S0111", " S7 ", "S12345", " S0222 "],
         "entry_ids": ["S081", "F12.3", " S0222 "]})
    harness.check("a shortened id is padded to four digits; a full or longer id is kept, without stray spaces",
                  sentence.transcript_ids == ["S0081", "S0111", "S0007", "S12345", "S0222"], str(sentence.transcript_ids))
    harness.check("an entry id of unread speech is repaired the same way; a note's id is left alone",
                  sentence.entry_ids == ["S0081", "F12.3", "S0222"], str(sentence.entry_ids))


def test_a_thinking_that_loops_is_stopped_and_told_to_answer():
    """A review todo thought 102,473 characters for 16 minutes, one passage over and over, and called no tool: the
    conversation's call is streamed through phase 3's loop stop, and the next turn is told to answer now."""

    from langchain_core.messages import AIMessage
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    passage = "The sources do not settle it, so I submit unresolved. Wait, let me check the wording of the question. " * 60
    looping = AIMessage(content="", additional_kwargs={"reasoning_content": passage})
    model = fakes.scripted(looping, _submit("submit_review", todos=[]))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    told = [str(message.content) for message in model.calls[1] if isinstance(message, HumanMessage)]
    harness.check("the looping turn is followed by THINKING_LOOPED, asking for the answer now",
                  any("THINKING_LOOPED" in text and "`submit_review` now" in text for text in told), str(told)[-400:])
    harness.check("and the answer after it completes the phase", update["phases"]["review"] == "complete", str(update))


def test_a_reply_stopped_as_a_loop_is_never_judged():
    """A chapter's reply wrote the chapter about five times over and was stopped as a loop; its text was still read as
    the chapter's answer, which only the dry check refused (the wiki's chapter 6). A looped reply is no answer: the
    next turn is told to answer, and only that answer is judged."""

    from langchain_core.messages import AIMessage
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    passage = "The sources do not settle it, so the todo stays open and the section keeps its sentence as it reads. " * 60
    looping = AIMessage(content=passage, tool_calls=[{"name": "submit_review", "args": {"todos": []}, "id": "c1"}])
    model = fakes.scripted(looping, _submit("submit_review", todos=[]))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    told = [str(message.content) for message in (model.calls[1] if len(model.calls) > 1 else []) if isinstance(message, HumanMessage)]
    harness.check("the looped reply's call is not judged: the model is asked again and told why",
                  len(model.calls) == 2 and any("THINKING_LOOPED" in text for text in told), str(len(model.calls)))
    harness.check("and the answer after it completes the phase", update["phases"]["review"] == "complete", str(update))


def test_a_reply_stopped_as_a_loop_is_logged_whole_defect():
    # A chapter's answer was stopped as a loop and its log kept only its first 2,000 characters, so the loop could not
    # be checked by anyone; the conversation still keeps the short text, and the log keeps what the model said whole
    import json as json_module
    from langchain_core.messages import AIMessage
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    passage = "The sources do not settle it, so the todo stays open and the section keeps its sentence as it reads. " * 60
    looping = AIMessage(content=passage, tool_calls=[{"name": "submit_review", "args": {"todos": []}, "id": "c1"}])
    model = fakes.scripted(looping, _submit("submit_review", todos=[]))
    _run_phase_5(_load_run_context(paths, model))
    rows = [json_module.loads(line) for log in Path(paths.repository).rglob("conversation.jsonl")
            for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    said = [row.get("said") or "" for row in rows if passage[:60] in (row.get("said") or "")]
    harness.check("the looped reply's log row holds its whole text", bool(said) and len(said[0]) == len(passage),
                  str([len(text) for text in said]))
    sent = [str(message.content) for message in (model.calls[1] if len(model.calls) > 1 else [])
            if isinstance(message, AIMessage)]
    harness.check("and the conversation keeps it short", all(len(text) <= 2_000 for text in sent),
                  str([len(text) for text in sent]))


def test_a_todo_whose_sentence_an_earlier_todo_changed_is_shown_the_section_as_it_reads_now():
    """Found on an audio run: the first todo's question left open replaced "Nothing was left open.", and the next todo,
    about that sentence, was asked about it as written, replaced it again and was refused as naming a sentence the
    section no longer has."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    nothing_open = {"kind": "open_question", "section": "Decisions and open points", "quote": "Nothing was left open.",
                    "missing": "the day in March the shipping starts was left open",
                    "why_it_matters": "a reader thinks every point was settled", "where": "the open points"}
    settled = {"status": "resolved", "section": "", "replaces": "", "after": "", "sentences": [],
               "searches": ["the open points already list the question from the first todo"], "question": ""}
    model = fakes.scripted(_submit("submit_review", todos=[QUESTION, nothing_open]),
                           _submit("find", words="March"), _submit("submit_resolution", **UNRESOLVED),
                           _submit("submit_resolution", **settled))
    ctx = _load_run_context(paths, model)
    _run_phase_5(ctx)
    asked = [str(message.content) for call in model.calls for message in call
             if isinstance(message, HumanMessage) and str(message.content).startswith("Job 2, TODO-02")]
    harness.check("the second todo is told the earlier edit changed its section, with the section as it reads now",
                  bool(asked) and "An earlier todo's edit changed this section" in asked[-1]
                  and "From which day in March" in asked[-1], (asked or ["(never asked)"])[-1][:600])
    todo = review_file_service.read_store(paths)["todos"][1]
    harness.check("and its answer with no edit is accepted on its first attempt",
                  todo["status"] == "resolved" and todo["attempts"] == 1, str(todo.get("reason")))


def test_the_reviewer_s_transcript_line_names_its_speaker():
    """The reviewer checked who said what against a transcript that printed no speaker."""

    import dataclasses
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    reading = _build_reading(paths)
    spoken = dataclasses.replace(reading, transcript=[{**entry, "speaker": "Speaker A"} if entry["id"] == "S0001"
                                                      else entry for entry in reading.transcript])
    lines = review_reading_service.run_read(spoken, WriteReadingState(), "read_transcript", {"start": "0:00", "end": "0:12"}).text
    harness.check("a line with a speaker names it after its time; a line with none is as before",
                  "S0001 00:06 Speaker A: Only a team lead" in lines and "S0002 00:10 We will switch" in lines, lines)


def test_a_blocked_todo_stops_the_review_before_the_next_todo():
    """The owner: a blocked step stops the phase, and nothing after it runs. A todo that spent its attempts was followed
    by every later todo's follow-up."""

    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    model = fakes.scripted(_submit("submit_review", todos=[GAP, QUESTION]), fakes.says("I could not find it."),
                           _submit("submit_resolution", **UNRESOLVED))
    ctx = _load_run_context(paths, model)
    update = _run_phase_5(ctx)
    store = review_file_service.read_store(paths)
    harness.check("the phase is blocked", update["phases"]["review"] == "blocked", str(update))
    harness.check("and the todo after the blocked one was never asked",
                  [todo["status"] for todo in store["todos"]] == ["blocked", "pending"],
                  str([todo["status"] for todo in store["todos"]]))


def test_the_figure_actions_a_resolution_may_hold_are_the_ones_the_code_names():
    from typing import get_args
    allowed = get_args(review_actions.Resolution.model_fields["figure_action"].annotation)
    harness.check("the schema's values are the empty one and the named actions",
                  allowed == ("", *review_actions.FIGURE_ACTIONS), str(allowed))


def test_every_read_the_review_names_in_its_tuples_is_a_tool_it_offers():
    named = {*review_actions.FRAME_READS, *review_actions.NOTES_READS}
    harness.check("the tuples of read names are keys of READ_TOOLS", named <= set(review_actions.READ_TOOLS),
                  str(named - set(review_actions.READ_TOOLS)))


def test_a_new_review_does_not_count_the_earlier_review_s_reads_as_its_own():
    from services import review_file_service
    from utils import json_lines_utils

    paths = RunPaths(repository=Path(tempfile.mkdtemp()), module=VIDEO, output_root_override=Path(tempfile.mkdtemp()) / "out")
    review_file_service.start_review(paths, "The first published document.")
    json_lines_utils.append_json_line(paths.output_root / output_contract.REVIEW_CONVERSATION_LOG, {
        "followUp": "todo-01", "calls": [{"name": "read_transcript", "args": {"words": "price"}}]})
    before = review_file_service.list_follow_up_reads(paths, "todo-01")
    review_file_service.start_review(paths, "Another published document.")
    after = review_file_service.list_follow_up_reads(paths, "todo-01")
    earlier = list((paths.output_root / output_contract.REVIEW_DIR_NAME / "earlier").glob("*/logs/conversation.jsonl"))
    harness.check("the review that wrote the read counts it", len(before) == 1, str(before))
    harness.check("the next review starts with no read of its own", after == [], str(after))
    harness.check("and the earlier review's log is kept with it, not deleted", len(earlier) == 1, str(earlier))


def test_an_unresolved_question_is_its_question_alone_and_a_note_after_it_never_refuses_it_defect():
    # Defect: a reviewer's question "What are the Mystery Tokens …? The transcript only mentions them …" was refused for
    # its second sentence, about the recording, and blocked the run. Fix: the open point is the text up to its first "?".
    from validators import review_validator

    harness.check("the question ends at its question mark",
                  review_validator.build_open_question("What are Mystery Tokens? The transcript only mentions them.")
                  == "What are Mystery Tokens?")
    harness.check("a question with no question mark stays whole",
                  review_validator.build_open_question("Which team owns  the gateway") == "Which team owns the gateway")


def test_a_figure_a_todo_removed_leaves_no_caption_and_its_file_is_set_aside_defect():
    # Defect: a todo removed a figure; the reviewed document kept its caption and "What the figure shows" box, its file
    # stayed in resources linked nowhere, and the folder check failed phase 5. Fix: the todo's figure action is applied, and its file is set aside as the audit's are.
    from services import review_conversation_service
    from graph.nodes import review_document_node

    before = ("## Gateway\n\nThe gateway signs tokens [F1.1].\n\n![The token path](resources/figure-01.png)\n\n"
              "*Figure 1. The token's path*\n\n> **What the figure shows:** a box and an arrow.\n\n"
              "![The refund flow](resources/figure-02.png)\n\n*Figure 2. The refund flow*\n")
    todo = {"status": "resolved", "order": 1, "editSection": "Gateway", "after": "", "sentences": [], "question": "",
            "replaces": "![The token path](resources/figure-01.png)"}
    text = review_conversation_service.render_reviewed_document(before, [todo])
    harness.check("the removed figure's caption and box are gone", "Figure 1. The token's path" not in text
                  and "a box and an arrow" not in text and "figure-02.png" in text, text)
    harness.check("the todo's removal is listed by its number", review_document_node._list_todo_removed_figures([todo]) == {1})
    harness.check("numbers after the audit's removals map back to the published files",
                  review_document_node._map_to_published_numbers(before + "\n![x](resources/figure-03.png)\n", {1}, {1}) == {2})


def test_a_review_question_takes_the_place_of_the_open_points_it_asks_again_defect():
    # Defect: the published open points listed five questions twice, "What is AcmeKey?" beside the review's "What are
    # AcmeKey and GPG …?". Fix: a review question replaces each open point whose words it holds; a point it does not hold stays.
    markdown = ("## Open points\n\n- What is AcmeKey?\n- What is GPG?\n- What is an Assembly?\n"
                "- The winner or prize of the 1,700-game tournament is not stated.\n- What is the cost?\n\nEnd.")
    for question in ("What are AcmeKey and GPG in the context of the gateway's keys?",
                     "What does 'assemblies' refer to at the congress?",
                     "Who won the tournament across 1,700 games, and what was the prize?"):
        markdown = document_edit_utils.add_open_questions(markdown, [question])
    points = [line for line in markdown.splitlines() if line.startswith("- ")]
    harness.check("each point asked again is replaced by the review's question, the unrelated one kept",
                  points == ["- What are AcmeKey and GPG in the context of the gateway's keys?",
                             "- What does 'assemblies' refer to at the congress?",
                             "- Who won the tournament across 1,700 games, and what was the prize?",
                             "- What is the cost?"], str(points))


def test_a_contradiction_s_request_names_the_other_side_s_notes_defect():
    # A contradiction between "an AcmeChip 328" in one section and "an AcmeChip 48/88/168" in another named only the
    # first sentence's notes, and the reviewer read four frames to find the other side; its notes are now named too
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    sections = document_file_service.build_sections_dir(paths)
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "heading-001-1.md").write_text("The board runs an AcmeChip 328 controller. [F5.1]\n", encoding="utf-8")
    (sections / "heading-005-1.md").write_text("The controller is an AcmeChip 48/88/168. [F24.1]\n", encoding="utf-8")
    other = document_file_service.find_other_side_sources(paths, "The board runs an AcmeChip 328 controller",
                                                          "The controller model (AcmeChip 328 or 48/88/168) is contradictory.")
    harness.check("the other side's notes are found", other == ["F24.1"], str(other))
    todo = {"id": "TODO-01", "kind": "contradiction", "section": "Hardware", "quote": "The board runs an AcmeChip 328 controller",
            "missing": "The controller model is contradictory.", "where": "the schematic"}
    text = review_messages.build_resolve_text({**todo, "otherSources": other}, 1, ["F5.1"], "")
    harness.check("and the request names them", "The other side of it stands in a sentence written from the notes F24.1" in text,
                  text)


def test_a_figure_todo_names_the_frame_its_figure_was_cut_from_defect():
    # A figure todo's request named only the reviewer's own guess, "Resources (figure-06.png, figure-07.png)", and the
    # worker searched two frames for the one the figure came from; the published provenance names it
    from services import review_conversation_service
    paths = _build_finished_run(Path(tempfile.mkdtemp()))
    folder = document_folder_service.build_document_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "provenance.json").write_text(json.dumps({"images": [
        {"figure": 6, "file": "resources/figure-06.png", "frame": "frame-067"}]}), encoding="utf-8")
    frame = document_folder_service.find_figure_frame(paths, "![The chart](resources/figure-06.png)")
    harness.check("the figure's frame is found", frame == "frame-067", frame)
    todo = {"id": "TODO-02", "kind": "figure", "section": "Uses", "quote": "![The chart](resources/figure-06.png)",
            "missing": "the legend is too small", "where": "Resources (figure-06.png)"}
    text = review_messages.build_resolve_text({**todo, "figureFrame": frame}, 2, [], "")
    harness.check("and the request names it", "`look_at_frame` at frame-067, the frame it was cut from" in text, text[:300])


def test_a_review_todo_is_told_the_frames_whose_notes_use_its_rare_words_defect():
    # A deferred question was resolved from the frame it was asked on, and the talk's answer on a later frame was never
    # read (DEFECTS 269); now the request names the frames whose notes use a rare word of the todo
    todo = {"id": "TODO-01", "kind": "gap", "section": "Storage", "quote": "Can the ledger cache be bought bigger?",
            "missing": "whether more memory helps", "where": "frame 4", "relatedLines": [(9, "The ledger cache must hold every row.")]}
    text = review_messages.build_resolve_text(todo, 3, ["F4.1"])
    harness.check("the request lists the frame and its line", "- frame 9: The ledger cache must hold every row." in text, text)
    harness.check("none found, no line", "rare word" not in review_messages.build_resolve_text({**todo, "relatedLines": []}, 3, []))


def test_a_quote_one_word_off_its_sentence_finds_that_sentence_defect():
    # A critique quoted a sentence with one word added and blocked on REVIEW_QUOTE_NOT_FOUND (DEFECTS 296); now the
    # one sentence of the section a quote copies with one word off is the one it means
    document = ("## Ledger\n\nThe gateway accepts orders up to the monthly cap and queues the rest for approval. "
                "Admins raise the cap.\n")
    quote = "The gateway accepts orders up to a monthly cap and queues the rest for approval."
    span = document_edit_utils.find_quote(document, "Ledger", quote)
    harness.check("the sentence is found", span is not None
                  and document[span[0]:span[1]].startswith("The gateway accepts orders up to the monthly cap"), str(span))
    harness.check("a short quote one word off is not", document_edit_utils.find_quote(document, "Ledger", "Admins lower the cap.") is None)


def test_a_resolution_counting_note_ids_past_the_frame_keeps_its_real_ids_defect():
    # A resolution listed F10.9, F10.10, ... on to F10.1000 and was refused for the hundreds frame 10 does not have
    # (DEFECTS 297); now the counted run past the real notes is dropped, and an unknown id standing alone still stays
    paths = fx.make_paths()
    saved = review_validator._read_known_sources
    review_validator._read_known_sources = lambda _paths: ({"S0001"}, {"F3.1", "F3.2", "F4.1"}, {3, 4})
    try:
        answer = {"sentences": [{"text": "The cap holds.", "entry_ids": ["F4.1", "F3.1", "F3.2", "F3.3", "F3.4", "F3.5", "F9.9"]}]}
        kept = review_validator.drop_counted_note_ids(answer, paths)["sentences"][0]["entry_ids"]
    finally:
        review_validator._read_known_sources = saved
    harness.check("the count past F3.2 is dropped, the lone unknown F9.9 stays for the check",
                  kept == ["F4.1", "F3.1", "F3.2", "F9.9"], str(kept))


def test_an_unknown_source_beside_a_known_one_is_dropped_and_a_sentence_with_none_known_keeps_its_ids_defect():
    # A resolution cited a real note of a frame beside a note id that frame does not have and was refused
    # RESOLUTION_SOURCE_UNKNOWN (DEFECTS 723); now the unknown id goes when the sentence keeps a known source
    paths = fx.make_paths()
    saved = review_validator._read_known_sources
    review_validator._read_known_sources = lambda _paths: ({"S0001"}, {"F3.1", "F4.1"}, {3, 4})
    try:
        answer = {"sentences": [{"text": "The cap holds.", "entry_ids": ["F3.1", "F3.27"], "frames": [3, 99]},
                                {"text": "Nobody said so.", "entry_ids": ["F9.9"], "transcript_ids": ["S9999"]}]}
        kept = review_validator.drop_unknown_sources_beside_known(answer, paths)["sentences"]
    finally:
        review_validator._read_known_sources = saved
    harness.check("the unknown note and frame beside known ones are dropped",
                  kept[0]["entry_ids"] == ["F3.1"] and kept[0]["frames"] == [3], str(kept[0]))
    harness.check("a sentence with no known source keeps every id for the check",
                  kept[1]["entry_ids"] == ["F9.9"] and kept[1]["transcript_ids"] == ["S9999"], str(kept[1]))


def test_a_point_nobody_in_the_meeting_knew_is_submitted_unresolved_at_once_defect():
    # A review todo asked for a limit the speaker said he did not know; the worker read that and searched four more
    # times (DEFECTS 298); now its instructions say to submit unresolved as soon as a source says nobody knew
    text = review_messages.build_review_system_text("knowledge", [], True)
    harness.check("the rule is in the review's instructions",
                  "When a source says nobody in the meeting knew the answer" in text and "as soon as that read shows it" in text
                  and "Never resolve\n  it with a sentence saying someone did not know" in text)


def test_a_value_worked_out_from_stated_ones_is_never_written_defect():
    # A review worker worked a scale per count out of a 10-bit range and a 1 V reference, a figure no source states
    # (DEFECTS 299); now the review's instructions say to write the stated values, never what is derived from them
    text = review_messages.build_review_system_text("knowledge", [], True)
    harness.check("the rule is in the review's instructions",
                  "A value you work out from values the sources state" in text and "never what you derive" in text)


def test_the_review_checks_the_summary_s_claims_of_what_a_system_does_defect():
    # A summary said a database searched "without external AI services" beside notes saying its embeddings come from
    # outside models, and the review, told to check only numbers, dates and orders, passed it. It checks such claims now
    from prompts import system_prompt_builder

    prompt = system_prompt_builder.build_system_prompt("review")
    harness.check("the summary pass names claims of what a system does, needs, uses or does without",
                  "each claim of what a system does, needs, uses or does without" in prompt, prompt[:300])
    harness.check("a claim the sources never state is a todo", "or never state, is an `unsupported` todo" in prompt)


def test_a_sentence_the_review_deletes_is_reported_written_by_its_section_not_left_out_defect():
    # a narrated sentence the section wrote, deleted by the review, was reported "left out by its section", sending the
    # fix to what a section leaves out; the report now says the section wrote it with no source
    from utils import review_report_utils
    lost_at, _by = review_report_utils.decide_lost_at(
        {"status": "resolved", "section": "Ordering", "replaces": "The presenter opens the form.", "sentences": []}, {})
    label = review_report_utils.LOST_AT_LABELS[lost_at]
    harness.check("the report names the section as the step that wrote it", label == "written by its section with no source",
                  label)


def test_a_todo_s_request_carries_the_words_of_the_notes_its_sentence_states_defect():
    # A sentence its note stated word for word was named by the note's id alone, never read, and rewritten from a figure
    # beside it. Now the request carries each note's words and says when they state the sentence
    todo = {"id": "TODO-01", "kind": "unsupported", "section": "Ordering", "missing": "the figure shows other fields",
            "where": "the figure", "quote": "The form holds three fields: name, date and amount.",
            "sourceNotes": [["F12.3", "The form holds three fields: name, date and amount."]], "isStatedByNotes": True}
    text = review_messages.build_resolve_text(todo, 2, ["F12.3"])
    harness.check("the note's words are listed", "- F12.3: The form holds three fields" in text, text)
    harness.check("the request says the sentence states them", "The sentence says what these notes say" in text, text)
    plain = review_messages.build_resolve_text({**todo, "isStatedByNotes": False}, 2, ["F12.3"])
    harness.check("a sentence its notes do not state is not told so", "The sentence says what these notes say" not in plain)


def test_a_deleted_sentence_its_notes_state_is_filed_under_the_frame_notes_defect():
    # A slide's picture described in a frame's notes, written into the text and deleted by the review, was filed as
    # "written by its section with no source". Now a deletion its notes state names the frame notes
    answer = {"status": "resolved", "section": "Ordering", "replaces": "A drawing of a cup stands on the slide.", "sentences": []}
    lost_at, by = review_report_utils.decide_lost_at(answer, {}, is_stated_by_notes=True)
    harness.check("the frame notes are named", review_report_utils.LOST_AT_LABELS[lost_at]
                  == "written from frame notes that should not hold it", lost_at)
    harness.check("the program decided it", by == "program")
    harness.check("one no note states stays the section's", review_report_utils.decide_lost_at(answer, {})[0]
                  == "section_written")


def test_an_open_question_s_sentence_is_replaced_by_the_answer_the_sources_give_defect():
    # Defect: "no answer was provided" had to be kept beside the answer found at 58:08, by a rule measured on a gap's
    # fix. Fix: an open question's sentence is replaced, unless the notes it was written from state it
    answer = {"status": "resolved", "replaces": "Questions were raised about the limits; no answer was provided.",
              "sentences": [{"text": "Asked about the limits, the team said a reset clears them.",
                             "transcript_ids": ["S0101"]}]}
    for is_stated, expected in ((False, False), (True, True)):
        report = ValidationReport(subject="review-todo-03")
        with mock.patch.object(review_validator, "is_stated_by_its_notes", lambda paths, text: is_stated):
            review_validator._check_text_kept(report, None, "review-todo-03", answer, "open_question")
        harness.check(f"notes stating the sentence: {is_stated}; refused: {expected}",
                      ("RESOLUTION_DROPS_TEXT" in {finding.code for finding in report.findings}) == expected, "")


def test_a_todo_s_request_names_each_frame_with_its_time_defect():
    # Defect: the request named the frames of a todo's notes by number alone, and the reads take a time: a reviewer
    # looked for frame 116, at 46:26, at 04:30 and 06:00. Fix: each frame is named with its time
    todo = {"id": "TODO-03", "kind": "open_question", "section": "Setup", "quote": "Questions were raised.",
            "missing": "the answers", "where": "transcript", "sourceNotes": [("F116.6", "Any questions about these?")],
            "relatedLines": [(116, "Agents support clearing the context window.")], "frameTimes": {116: "46:26"}}
    text = review_messages.build_resolve_text(todo, 3, ["F116.6"])
    harness.check("the note's frame is named with its time", "- F116.6 (frame 116 at 46:26): Any questions" in text, text)
    harness.check("so is a related line's frame", "- frame 116 at 46:26: Agents support" in text, text)
    harness.check("a frame of unknown time is named by number", "- frame 7: x" in review_messages.build_resolve_text(
        {**todo, "relatedLines": [(7, "x")]}, 3, ["F116.6"]), "")


def test_a_number_a_cited_frame_s_outline_shows_is_sourced_defect():
    # Defect: a correction to "Read 12 lines", citing notes of the frame whose outline shows it, was refused as stating
    # a number no source holds: the check read the notes alone. Fix: a cited frame's outline is read with its notes
    entries = {"entries": [{"id": "F13.1", "kind": "facts", "text": "The agent reads the script first."}]}
    records = {13: {"screen": '- console: dark\n  - line "Read 12 lines"'}}
    with mock.patch.object(review_validator.integration_service, "build_integration", lambda paths: entries), \
            mock.patch.object(review_validator.frame_records_service, "map_records_by_index", lambda paths: records):
        by_note = review_validator._list_unsourced_numbers(None, {"text": "It reads 12 lines.", "entry_ids": ["F13.1"]}, "")
        by_frame = review_validator._list_unsourced_numbers(None, {"text": "It reads 12 lines.", "frames": [13]}, "")
        unheld = review_validator._list_unsourced_numbers(None, {"text": "It reads 40 lines.", "frames": [13]}, "")
    harness.check("12 shown in the outline of the frame a cited note is on is sourced", by_note == [], str(by_note))
    harness.check("and so it is when the frame is cited by its number", by_frame == [], str(by_frame))
    harness.check("a number the outline does not show stays unsourced", unheld == ["40"], str(unheld))


def test_a_name_a_cited_frame_s_outline_shows_is_sourced_defect():
    # Defect: a figure's description naming a chart's legend labels, citing the frame whose outline shows them, was
    # refused twice as naming terms no source holds: the term check read the notes alone. Fix: it reads the outline too
    entries = {"entries": [{"id": "F11.1", "kind": "facts", "text": "The chart shows products sold per year."}]}
    records = {11: {"screen": '- chart legend: right\n  - entry "TREASURY BILL"'}}
    with mock.patch.object(review_validator.integration_service, "build_integration", lambda paths: entries), \
            mock.patch.object(review_validator.frame_records_service, "map_records_by_index", lambda paths: records):
        by_note = review_validator._list_unsourced_terms(None, {"text": "It lists TREASURY BILL.", "entry_ids": ["F11.1"]}, "")
        by_frame = review_validator._list_unsourced_terms(None, {"text": "It lists TREASURY BILL.", "frames": [11]}, "")
        unheld = review_validator._list_unsourced_terms(None, {"text": "It lists TERM DEPOSIT.", "frames": [11]}, "")
    harness.check("a name the outline of a cited note's frame shows is sourced", by_note == [], str(by_note))
    harness.check("and so it is when the frame is cited by its number", by_frame == [], str(by_frame))
    harness.check("a name the outline does not show stays unsourced", unheld == ["TERM", "DEPOSIT"], str(unheld))


def test_a_sentence_copied_word_for_word_is_placed_in_its_own_block_whatever_number_is_named_defect():
    # Defect: a resolution copied a sentence of one block and named another block's number, under another heading, and
    # was refused as replacing a sentence the section lacks. Fix: a copy found in one block only is placed there
    body = ("## Setup\n\nThe Gateway signs each token before it forwards it.\n\n## Refunds\n\n"
            "The Gateway refunds an order within a day.\n\n## Limits\n\nThe Gateway caps each order at 500 units.\n")
    answer = {"status": "resolved", "block": 3, "replaces": "The Gateway signs each token before it forwards it."}
    placed = review_file_service._fill_from_block(body, answer, "replaces")
    harness.check("the copy is placed under its own heading", placed["section"] == "Setup"
                  and placed["replaces"] == answer["replaces"], str(placed))
    named = review_file_service._fill_from_block(body, {**answer, "replaces": "refunds"}, "replaces")
    harness.check("a short phrase is no copy to move by", named["section"] == "Limits", str(named))


def test_a_new_sentence_the_document_already_holds_is_not_added_again_defect():
    # Defect: a resolution replaced a sentence by one its block, or another section, already held, and was refused for
    # saying it twice. Fix: a new sentence the document holds outside the replaced text is not added again
    body = ("## Refunds\n\nThe Gateway refunds an order within a day. Asked about refunds, the team answered that the "
            "Gateway refunds within a day.\n\n## Limits\n\nThe Gateway caps each order at 500 units.\n")
    asked = "Asked about refunds, the team answered that the Gateway refunds within a day."
    answer = {"status": "resolved", "replaces": asked, "sentences": [{"text": "The Gateway refunds an order within a day."}]}
    harness.check("a sentence beside the replaced one is not added again",
                  review_file_service._drop_sentences_said_elsewhere(body, answer)["sentences"] == [], "")
    elsewhere = {**answer, "sentences": [{"text": "The Gateway caps each order at 500 units."}, {"text": "Refunds take a day."}]}
    harness.check("nor one another section holds, a new one kept",
                  review_file_service._drop_sentences_said_elsewhere(body, elsewhere)["sentences"]
                  == [{"text": "Refunds take a day."}], "")
    within = {"status": "resolved", "replaces": f"The Gateway refunds an order within a day. {asked}",
              "sentences": [{"text": "The Gateway refunds an order within a day."}]}
    harness.check("a sentence only the replaced text holds is kept",
                  review_file_service._drop_sentences_said_elsewhere(body, within) == within, "")


def test_a_figure_action_naming_the_figure_s_description_block_acts_on_its_figure_defect():
    # Defect: a recut naming the figure's description block, its quote that block's text, was refused as giving no
    # figure line. Fix: the program takes the figure line of the block or of the two before it under the same heading
    body = ("## Setup\n\n![The console](resources/figure-01.png)\n\n*Figure 1. The console*\n\n"
            "> **What the figure shows:** the console reads \"Read 12 lines\".\n\n## Use\n\nThe proxy signs each token.\n")
    answer = {"status": "resolved", "figure_action": "recut", "block": 3,
              "replaces": "> **What the figure shows:** the console reads"}
    placed = review_file_service._fill_from_block(body, answer, "replaces")
    harness.check("the recut acts on the figure line", placed["replaces"] == "![The console](resources/figure-01.png)",
                  str(placed))
    kept = review_file_service._fill_from_block(body, {**answer, "figure_action": "keep"}, "replaces")
    harness.check("a figure kept leaves its sentence as named", kept["replaces"] == answer["replaces"], str(kept))
    other = review_file_service._fill_from_block(body, {**answer, "block": 4, "replaces": "The proxy signs"}, "replaces")
    harness.check("no figure line is taken from another heading", other["replaces"] == "The proxy signs", str(other))


def test_a_figure_s_description_may_be_corrected_but_not_removed_defect():
    # Defect: a description saying "Read 42 lines" where its figure shows 12 could not be corrected: every change to a
    # description was refused as touching the figure. Fix: its words may change, the figure keeps a description
    body = ("## Setup\n\n![The console](resources/figure-01.png)\n\n*Figure 1. The console*\n\n"
            "> **What the figure shows:** the console reads \"Read 42 lines\".\n")
    corrected = body.replace("Read 42 lines", "Read 12 lines")
    harness.check("a description's words corrected keep the figure's marks",
                  document_edit_utils.list_figure_marks(corrected) == document_edit_utils.list_figure_marks(body), "")
    removed = body.replace("> **What the figure shows:** the console reads \"Read 42 lines\".\n", "")
    harness.check("a description removed changes them", document_edit_utils.list_figure_marks(removed)
                  != document_edit_utils.list_figure_marks(body), "")
    recaptioned = body.replace("*Figure 1. The console*", "*Figure 1. A terminal*")
    harness.check("a caption changed still changes them", document_edit_utils.list_figure_marks(recaptioned)
                  != document_edit_utils.list_figure_marks(body), "")


def test_a_contradiction_todo_is_told_which_of_its_two_blocks_to_name_defect():
    # Defect: a contradiction stood in two blocks, the todo had room for one, and the reviewer left the block and the
    # sentence empty, naming both blocks only in its text; phase 5's critique blocked. Fix: the instruction and the
    # rejection say a contradiction gives its first side's block and sentence and names the other side's block
    prompt = " ".join(system_prompt_builder.build_system_prompt("review").split())
    harness.check("the review is told which block a contradiction names",
                  "A contradiction stands in two blocks. Give the first side as the todo's block and sentence" in prompt,
                  prompt[:200])
    body = ("## Origins\n\nThe project began in a summer as a weekend idea.\n\n## Data\n\n"
            "The building data became public a year later.\n")
    todo = {"kind": "contradiction", "section": "Origins", "missing": "Block 1 and block 2 disagree on the dates.",
            "why_it_matters": "a", "where": "b"}
    defect = review_validator.find_todo_defect(body, todo, "TODO-01")
    harness.check("the rejection says a contradiction names its first side's block and the other's in what is missing",
                  defect is not None and "a contradiction gives the block and the sentence of its first side"
                  in defect[2], str(defect))
    placed = review_file_service._fill_from_block(body, {**todo, "block": 1, "quote": ""}, "quote")
    harness.check("a contradiction giving its first block, as taught, passes",
                  review_validator.find_todo_defect(body, placed, "TODO-01") is None, str(placed))


def test_a_todo_and_a_resolution_name_a_numbered_block_and_the_program_places_them_defect():
    # Defect: the review named a place only by copying a sentence of the document, and copies that differed (elided,
    # marks dropped) blocked phase 5. Fix: the document is shown with numbered blocks, and a block named is placed
    body = ("## Setup\n\nThe gateway runs these commands first:\n\n```bash\nsync --all\n```\n\n"
            "The proxy signs each token. It then forwards it to `AcmeSync`.\n")
    numbered = document_edit_utils.render_numbered_document(body)
    harness.check("each block of text is numbered, the code block and the heading not",
                  numbered.count("{block ") == 2 and "{block 2}\nThe proxy" in numbered, numbered)
    harness.check("the marks are read back out of a copy",
                  document_edit_utils.remove_block_marks(numbered) == body, "")
    todo = {"kind": "gap", "section": "", "quote": "The gateway runs ... sync ...", "block": 1, "missing": "a",
            "why_it_matters": "b", "where": "c"}
    placed = review_file_service._fill_from_block(body, todo, "quote")
    harness.check("a quote the block does not hold word for word becomes the block, under its heading",
                  placed["section"] == "Setup" and placed["quote"] == "The gateway runs these commands first:"
                  and review_validator.find_todo_defect(body, placed, "TODO-01") is None, str(placed))
    kept = review_file_service._fill_from_block(body, {**todo, "block": 2, "quote": "The proxy signs each token."},
                                                "quote")
    harness.check("a quote the block holds stays", kept["quote"] == "The proxy signs each token.", str(kept))
    resolution = review_file_service._fill_from_block(body, {"status": "resolved", "block": 2, "after": "It then …"},
                                                      "replaces")
    harness.check("an `after` the block does not hold adds after the whole block",
                  resolution["after"] == "The proxy signs each token. It then forwards it to `AcmeSync`.", str(resolution))
    whole = review_file_service._fill_from_block(body, {"status": "resolved", "block": 2}, "replaces")
    harness.check("a resolution naming no sentence replaces the whole block",
                  whole["replaces"].startswith("The proxy signs") and whole["section"] == "Setup", str(whole))
