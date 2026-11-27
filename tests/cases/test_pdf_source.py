"""A PDF read as a recording: each page's text cleaned of what a reader does not need, each page
at its own minute, each page rendered as a frame. The PDFs are built here, every word invented."""

from __future__ import annotations

import functools
import importlib.util
import logging
import tempfile
import types
from pathlib import Path

import cv2
import numpy
import pymupdf

import config

from support import fixtures as fx
from support import harness

from cli import run_command
from graph.nodes import write_document_node
from models import output_contract
from models.attempt_request import AttemptRequest
from models.input_range_spec import InputRangeSpec
from models.validation_report import ValidationReport
from prompts import document_messages
from services import frame_extraction_service
from services import pdf_conversion_service
from services import pdf_source_service
from services import phase_file_service
from services import run_status_service
from services import validation_service
from utils import document_plan_utils
from utils import section_markdown_utils

PAGES = [["Gateway onboarding guide", "Every partner gets its own tenant and its own retry budget."],
         ["Retry policy", "A failed call is retried at most three times."],
         ["Monitoring", "An alert fires when the error rate stays above 2 percent."],
         ["Known issues", "Refunds above 2,000 EUR can time out at the ledger."]]


def _build_pdf() -> Path:
    """Four pages: a side block naming the guide and a print footer on every page, an author line, a link out and a
    link inside the file on page 1, the content in a column of its own."""

    document = pymupdf.open()
    for number, (title, sentence) in enumerate(PAGES):
        page = document.new_page(width=595, height=842)
        page.insert_text((30, 80), "Page tree", fontsize=9)
        page.insert_text((30, 95), "Gateway onboarding guide", fontsize=9)
        page.insert_text((200, 80), title, fontsize=20)
        page.insert_text((200, 120), sentence, fontsize=11)
        page.insert_text((200, 800), f"Printed from AcmeSync Wiki · page {number + 1}", fontsize=8)
        if number == 0:
            page.insert_text((200, 140), "Created by Jonas Weber, last updated by Mara Keller", fontsize=9)
            page.insert_text((200, 160), "The signature scheme follows HTTP Message Signatures.", fontsize=11)
            label = page.search_for("HTTP Message Signatures")[0]
            page.insert_link({"kind": pymupdf.LINK_URI, "from": label, "uri": "https://example.org/signatures"})
    path = Path(tempfile.mkdtemp()) / "guide.pdf"
    document.save(path)

    return path


def _keep_count(told: list[tuple[int, int, str]],
                read: int,
                total: int,
                step: str) -> None:
    told.append((read, total, step))


def test_phase_1_counts_a_pdf_s_pages_as_pymupdf4llm_reads_them_defect():
    # Defect: a large PDF read by pymupdf4llm showed phase 1 as one stage "waiting 1" for minutes, with no count
    # Fix: each page pymupdf4llm parses is counted ("pages"), once each, and the text read is the same as with no count
    document = pymupdf.open()
    for number in range(6):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 80), f"Gateway step {number + 1}", fontsize=20)
        page.insert_text((72, 120), f"The gateway checks the refund request in step {number + 1}.", fontsize=11)
    path = Path(tempfile.mkdtemp()) / "steps.pdf"
    document.save(path)
    told: list[tuple[int, int, str]] = []
    counted, _removed = pdf_source_service.build_page_transcript(path, on_pages=functools.partial(_keep_count, told))
    plain, _removed = pdf_source_service.build_page_transcript(path)
    steps = ["text layer", "figures", "parsing", "markdown", "page cleaning"]
    harness.check("each step counts the 6 pages once, in order, and the steps come in order",
                  told == [(read, 6, step) for step in steps for read in range(1, 7)], str(told))
    harness.check("the text read is the same as with no count", counted == plain)


def test_phase_1_counts_the_reader_s_second_walk_over_the_pages_defect():
    # Defect: a 6,761-page PDF stood at 6,761 of 6,761 pages for hours: the reader walks the pages twice, parsing them
    # and then writing their markdown, and only the first walk was counted. Fix: the second walk is its own step,
    # "markdown", counted page by page
    document = pymupdf.open()
    for number in range(7):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 80), f"Refund rule {number + 1}", fontsize=20)
    path = Path(tempfile.mkdtemp()) / "rules.pdf"
    document.save(path)
    told: list[tuple[int, int, str]] = []
    pdf_source_service.build_page_transcript(path, on_pages=functools.partial(_keep_count, told))
    markdown = [(read, total) for read, total, step in told if step == "markdown"]
    harness.check("the markdown walk counts each of the 7 pages, after the parsing walk ends",
                  markdown == [(read, 7) for read in range(1, 8)]
                  and told.index((7, 7, "parsing")) < told.index((1, 7, "markdown")), str(told))


def test_the_chrome_on_every_page_leaves_and_the_content_stays():
    transcript, removed = pdf_source_service.build_page_transcript(_build_pdf())
    text = transcript["text"]
    harness.check("every page's content is kept", all(title in text and sentence in text for title, sentence in PAGES),
                  text)
    harness.check("the side block and the print footer on every page are gone",
                  "Page tree" not in text and "Printed from" not in text, text)
    harness.check("the author line is gone", "Created by" not in text, text)
    harness.check("the title is kept, though the side block names it on every page",
                  text.count("Gateway onboarding guide") == 1, text)
    harness.check("every removal is listed with its page and its rule",
                  removed and all({"page", "rule", "text"} <= set(entry) for entry in removed), str(removed[:3]))


def test_a_link_out_keeps_its_address_and_each_page_has_its_minute():
    transcript, _ = pdf_source_service.build_page_transcript(_build_pdf())
    harness.check("the link's address follows its text", "HTTP Message Signatures <https://example.org/signatures>"
                  in transcript["text"], transcript["text"])
    pages = {segment["page"]: segment["start"] for segment in transcript["segments"]}
    harness.check("page N starts at minute N - 1", all(start >= (page - 1) * pdf_source_service.PAGE_SECONDS
                                                      and start < page * pdf_source_service.PAGE_SECONDS
                                                      for page, start in pages.items()), str(pages))


def test_each_page_is_said_over_its_own_frame_whole():
    transcript, _ = pdf_source_service.build_page_transcript(_build_pdf())
    slices = pdf_source_service.build_page_slices(transcript["segments"], list(range(1, len(PAGES) + 1)))
    harness.check("one slice a page, its own text, the page before as what came before",
                  len(slices) == len(PAGES) and "retried at most three" in slices[1]["current"]
                  and "Monitoring" not in slices[1]["current"] and "own tenant" in slices[1]["before"], str(slices[1]))


def test_a_page_range_reads_only_its_pages_with_their_own_numbers_and_still_drops_the_footer():
    # Pages 2 and 3 of four. The print footer and the side block stand on every page of the PDF, so
    # they are found as repeated though two pages alone could not show it
    transcript, removed = pdf_source_service.build_page_transcript(_build_pdf(), page_indexes=[1, 2])
    pages = sorted({segment["page"] for segment in transcript["segments"]})
    harness.check("only pages 2 and 3, by their own numbers", pages == [2, 3], str(pages))
    harness.check("each at its own minute", min(segment["start"] for segment in transcript["segments"])
                  >= pdf_source_service.PAGE_SECONDS, str(transcript["segments"][0]))
    harness.check("the pages out of range are not read", "Gateway onboarding" not in transcript["text"]
                  and "Refunds above" not in transcript["text"], transcript["text"])
    harness.check("the footer is still dropped", "Printed from" not in transcript["text"], transcript["text"])
    harness.check("removals are named on the pages read alone", {entry["page"] for entry in removed} <= {2, 3},
                  str(removed))
    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames = pdf_source_service.render_pages(_build_pdf(), frames_dir, [1, 2])
    harness.check("two frames, numbered from 0, each with its own page",
                  [(item["slug"], item["page"], item["frameNumber"]) for item in frames["items"]]
                  == [("frame-000", 2, 1), ("frame-001", 3, 2)], str(frames["items"]))
    slices = pdf_source_service.build_page_slices(transcript["segments"], [2, 3])
    harness.check("each frame hears its own page", "retried at most three" in slices[0]["current"]
                  and "error rate" in slices[1]["current"], str(slices))


def test_a_table_run_over_pages_is_marked_continuing():
    blocks, header = pdf_source_service._mark_continued_tables(["|A|B|\n|---|---|\n|1|2|"], None, 1)
    continued, _ = pdf_source_service._mark_continued_tables(["|A|B|\n|---|---|\n|3|4|", "After."], header, 2)
    harness.check("the first page's table is as it stands", blocks == ["|A|B|\n|---|---|\n|1|2|"], str(blocks))
    harness.check("the next page's table under the same header continues it",
                  continued[0].startswith("(The table continues the one on page 1") and continued[1] == "After.",
                  str(continued))


def test_a_row_broken_over_pages_leaves_the_repeated_header_defect():
    # A row's last cell ran on to the next page, where the reader joined its end to the header printed again; the
    # table was not marked continuing and the chapter gave the text to the next row (DEFECTS 534)
    _blocks, header = pdf_source_service._mark_continued_tables(["|**Tier**|**Risk**|\n|---|---|\n|Tier 3|Starts here"],
                                                                None, 29)
    page, last = pdf_source_service._mark_continued_tables(
        ["|**Tier**|**Risk** and ends here.|\n|---|---|\n|Tier 4|Its own text.|"], header, 30)
    harness.check("the header is the one before, the spilled text its first row, marked continuing",
                  page[0] == ("(The table continues the one on page 29, the same columns; its first row ends that "
                              "page's last row.)\n|**Tier**|**Risk**|\n|---|---|\n|  | and ends here. |\n"
                              "|Tier 4|Its own text.|") and last == header, page[0])


def test_every_page_becomes_a_frame_and_the_cut_reads_it_larger():
    pdf = _build_pdf()
    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames = pdf_source_service.render_pages(pdf, frames_dir)
    harness.check("one frame a page, each written", len(frames["items"]) == len(PAGES)
                  and all((frames_dir / Path(item["path"]).name).is_file() for item in frames["items"]), str(frames))
    full = pdf_source_service.read_page_image(pdf, 1)
    harness.check("the cut's page is the box's size", full is not None
                  and [full.shape[1], full.shape[0]] == frames["items"][1]["box"][2:], str(frames["items"][1]["box"]))
    harness.check("a page the file does not have is none", pdf_source_service.read_page_image(pdf, 9) is None)


def test_a_page_printed_again_is_a_repeat_of_its_first_showing():
    """A document's closing page printed three times was refused twice for facts the first held word for word (pdf-06)."""

    frames_dir = Path(tempfile.mkdtemp())
    blank = numpy.full((90, 160, 3), 255, numpy.uint8)
    other = blank.copy()
    other[20:70, 20:140] = 0
    for slug, image in (("frame-000", blank), ("frame-001", blank), ("frame-002", other), ("frame-003", blank)):
        cv2.imwrite(str(frames_dir / f"{slug}.jpg"), image)
    items = [{"slug": f"frame-00{index}", "path": f"frames/frame-00{index}.jpg", "transcriptCurrent": text}
             for index, text in enumerate(["Attendees: Jonas.", "Attendees:  Jonas.", "Attendees: Jonas.", "Notes."])]
    marked = pdf_source_service.mark_repeated_pages(items, frames_dir)
    harness.check("the same text and look is a repeat of the first; another look or text is not",
                  [item["repeatOf"] for item in marked] == [None, "frame-000", None, None], str(marked))


def test_a_semicolon_between_two_clauses_is_no_code():
    """A page's sentences joined by "; " were read as code and reached the writer as a block (pdf-06)."""

    harness.check("sentences with a semicolon are prose",
                  section_markdown_utils.is_prose_block("Lea asked who tells the partners; Mara will do it by Friday."))
    harness.check("a line ending in a semicolon, or a loop's, is still code",
                  not section_markdown_utils.is_prose_block("total += 1;")
                  and not section_markdown_utils.is_prose_block("for (i = 0; i < n; i++)"))


def test_the_agenda_call_is_handed_the_document_s_own_headings():
    """Handed only the pages' moments, the worker made one agenda point of a guide with seven headed sections."""

    text = document_messages._render_document_headings([
        {"moment": 1, "level": 1, "title": "Gateway onboarding guide"}, {"moment": 1, "level": 2, "title": "Overview"},
        {"moment": 3, "level": 2, "title": "Retry policy"}])
    harness.check("each heading is listed with its moment, nested under the title, and the second level is the points",
                  "- moment 1: Gateway onboarding guide" in text and "\n  - moment 3: Retry policy" in text
                  and "second level from the top an agenda point" in text, text)
    # The lead under the title, before the first second-level heading: the wiki's agenda call thought 16,405
    # characters about whether it is a point
    harness.check("the document's lead opens the first point", "(the document's lead) opens the first point" in text, text)
    harness.check("headings on one page are told to make one point", "standing on the same moment are one point" in text,
                  text)
    harness.check("a recording is handed nothing", document_messages._render_document_headings([]) == "")


def test_a_clock_time_in_a_pdf_s_text_is_its_content():
    """A guide's retry example ("retried at 10:00:02") was refused as a position in a recording, and the writer dropped
    its seconds (pdf-01). A recording's text still may not hold one."""

    paths = fx.make_paths()
    for medium, is_kept in (("pdf", False), (None, True)):
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "items": [], **({"medium": medium} if medium else {})})
        report = ValidationReport(subject="heading-001-1")
        report.add("DOCUMENT_TIMESTAMP", "heading-001-1: the text holds a clock time: 10:00:02")
        report.add("DOCUMENT_TRANSCRIPT_COPIED", "heading-001-1: a sentence copies speech word for word")
        request = AttemptRequest(job_kind="section", attempt=1, repository=paths.repository, module=paths.module,
                                 paths=paths, ignore=None, inputs={})
        kept = validation_service._drop_speech_only_findings(request, report)
        harness.check(f"a {medium or 'video'} run {'keeps' if is_kept else 'drops'} the clock-time and copied-speech findings",
                      len(kept.findings) == (2 if is_kept else 0), str(kept.findings))


def test_a_pdf_lasts_its_pages_and_is_never_opened_as_a_video():
    """The run's start opened a PDF as a video to read its length, and OpenCV complained on every run."""

    harness.check("four pages last four minutes",
                  frame_extraction_service.read_duration_s(_build_pdf()) == 4 * pdf_source_service.PAGE_SECONDS)


def test_a_pdf_chapter_starts_at_its_first_heading_and_the_rows_before_it_stay_behind():
    """Page 4 ended the tenant table and opened "Incidents": the chapter started at the page, so the table's last rows
    were filed under Incidents (pdf-02, DEFECTS 137)."""

    entries = [{"id": entry_id, "timestamps": [stamp], "kind": "note"}
               for entry_id, stamp in (("F0.1", 0.0), ("F0.2", 0.0), ("F1.1", 60.0), ("F1.2", 60.0), ("F1.3", 60.0))]
    parts = [{"number": 1, "title": "Latency by tenant"}, {"number": 2, "title": "Incidents"}]
    groups = [entries[:2], entries[2:]]
    drafts = [{"chapter": 1, "headings": [{"title": "Tenant table", "first_entry_id": "F0.1"}]},
              {"chapter": 2, "headings": [{"title": "Incident log", "first_entry_id": "F1.3"}]}]
    for is_pdf, chapter_two in ((True, ["F1.3"]), (False, ["F1.1", "F1.2", "F1.3"])):
        built = document_plan_utils.build_table_of_contents(parts, groups, drafts, is_pdf)
        table = document_plan_utils.flatten_table_of_contents(built, entries)
        held = [entry_id for topic in table["topics"] if topic["chapter"] == 2 for entry_id in topic["entry_ids"]]
        harness.check(f"{'a PDF' if is_pdf else 'a recording'}'s chapter 2 holds {chapter_two}", held == chapter_two,
                      str(table["topics"]))


def test_a_pdf_chapter_keeps_no_draft_heading_titled_as_the_chapter_defect():
    # Defect: four of nine chapters of a PDF opened with a heading titled as the chapter itself, over its opening text.
    # Fix: the table of contents drops such a heading beside the document's own; the first own heading opens the chapter.
    entries = [{"id": entry_id, "timestamps": [stamp], "kind": "note"}
               for entry_id, stamp in (("S0080", 0.0), ("S0081", 1.0), ("S0082", 2.0), ("S0083", 3.0))]
    parts = [{"number": 1, "title": "Experimental history"}]
    drafts = [{"chapter": 1, "headings": [{"title": "**Experimental history**", "first_entry_id": "S0080"},
                                          {"title": "Discovery", "first_entry_id": "S0081"},
                                          {"title": "Refinements", "first_entry_id": "S0083"}]}]
    for is_pdf, titles in ((True, ["Discovery", "Refinements"]), (False, ["**Experimental history**", "Discovery", "Refinements"])):
        built = document_plan_utils.build_table_of_contents(parts, [entries], drafts, is_pdf)
        table = document_plan_utils.flatten_table_of_contents(built, entries)
        harness.check(f"{'a PDF' if is_pdf else 'a recording'} keeps {titles}, the first holding the chapter's opening",
                      [topic["title"] for topic in table["topics"]] == titles and table["topics"][0]["entry_ids"][0] == "S0080",
                      str(table["topics"]))


def test_a_pdf_chapter_keeps_only_the_documents_own_headings_defect():
    # Defect: a PDF chapter opened with a heading the writer made up ("Early evolution of photosynthesis") before the
    # document's own headings, though the request listed them and said to add none.
    # Fix: document_plan_utils.keep_document_headings keeps only the draft headings titled as the document's own.
    draft = {"purpose": "p", "headings": [{"title": "Early history", "first_entry_id": "S0062"},
                                          {"title": "**Symbiosis**", "first_entry_id": "S0065"},
                                          {"title": "Lineages.", "first_entry_id": "S0070"}]}
    kept = document_plan_utils.keep_document_headings(draft, ["Symbiosis", "Lineages"])
    harness.check("only the document's own headings stay, marks and a closing full stop aside",
                  [heading["first_entry_id"] for heading in kept["headings"]] == ["S0065", "S0070"], str(kept))
    harness.check("with no title given the draft stays", document_plan_utils.keep_document_headings(draft, []) == draft, "")
    harness.check("with none matching the draft stays",
                  document_plan_utils.keep_document_headings(draft, ["Factors"]) == draft, "")


def test_a_pdf_chapter_s_draft_headings_are_handed_its_own_headings():
    """A chapter holding "January", "February" and "March" was answered with one heading of the worker's own (pdf-02)."""

    from graph.nodes import write_document_node

    headings = [{"moment": 1, "start": 0.0, "level": 1, "title": "Quarterly report"},
                {"moment": 1, "start": 20.0, "level": 2, "title": "Tenants"},
                {"moment": 7, "start": 360.0, "level": 2, "title": "January"},
                {"moment": 7, "start": 380.0, "level": 2, "title": "February"},
                {"moment": 8, "start": 420.0, "level": 2, "title": "Conclusions"}]
    months = write_document_node._list_chapter_headings(headings, {"start": 360.0, "title": "January and February"}, 420.0)
    harness.check("the chapter on page 7 is handed its two headings", months == ["January", "February"], str(months))
    first = write_document_node._list_chapter_headings(headings, {"start": 0.0, "title": "Tenants"}, 360.0)
    harness.check("a chapter whose one heading is its title is handed none", first == [], str(first))
    harness.check("a recording is handed nothing", write_document_node._list_chapter_headings([], {"start": 0.0}, None)
                  is None)
    deck = [{"moment": 1, "start": 0.0, "level": 1, "title": "Gateway 2.15"},
            {"moment": 2, "start": 60.0, "level": 1, "title": "Agenda"},
            {"moment": 3, "start": 120.0, "level": 1, "title": "What changes"},
            {"moment": 4, "start": 180.0, "level": 1, "title": "Refunds"}]
    slides = write_document_node._list_chapter_headings(deck, {"start": 0.0, "title": "What changes in 2.15"}, 180.0)
    harness.check("a deck's chapter is handed its slide titles, the document's title left out (pdf-03)",
                  slides == ["Agenda", "What changes"], str(slides))
    # Read as markdown, a chapter starts mid-page, at a paragraph: page 1's "Tenants" heading is the second chapter's
    mid_page = write_document_node._list_chapter_headings(headings, {"start": 10.0, "title": "Overview"}, 360.0)
    harness.check("a chapter starting mid-page is handed the headings after its start (pdf-02, markdown mode)",
                  mid_page == ["Tenants"], str(mid_page))
    numbered = write_document_node._list_chapter_headings(
        [{"moment": 1, "start": 0.0, "level": 1, "title": "Operator manual"},
         {"moment": 1, "start": 10.0, "level": 2, "title": "1 Installing the CLI"}],
        {"start": 0.0, "title": "Installing the CLI"}, 60.0)
    harness.check("a numbered section is the chapter its agenda point titles without the number", numbered == [],
                  str(numbered))
    # A heading read with its link's address is still the chapter's own title (a wiki article's chapter was told its
    # title was a heading inside it)
    linked = write_document_node._list_chapter_headings(
        [{"moment": 1, "start": 0.0, "level": 1, "title": "Photosynthesis"},
         {"moment": 3, "start": 120.0, "level": 2, "title": "Membranes and organelles <https://wiki.example/Organelle>"}],
        {"start": 120.0, "title": "Membranes and organelles"}, 180.0)
    harness.check("the chapter's own title with an address is no heading inside it", linked == [], str(linked))
    told = document_messages._render_chapter_headings({"documentHeadings": months})
    harness.check("the request lists them and asks for no heading of the worker's own",
                  '"January"; "February"' in told and "add none of your own" in told, told)
    # The chapter's own text before its first heading: the wiki's chapter 3 thought 16,606 characters about it
    harness.check("the text before the first heading is in it, and no heading is titled as the chapter",
                  "before its first heading is already in it" in told and "none titled as the chapter" in told, told)
    harness.check("a chapter with none is one heading under its title",
                  "one heading, titled as the chapter is" in document_messages._render_chapter_headings(
                      {"documentHeadings": []}))
    harness.check("a recording's request is unchanged", document_messages._render_chapter_headings({}) == "")


def _make_markdown_mode_context(model,
                                pdf: Path | None = None):
    """A PDF run in markdown mode, its transcript phase 1's cleaned text of the four pages above, or of `pdf`, which
    is then the run's recording."""

    import logging

    from models.run_context import RunContext
    from models.run_identity import RunIdentity
    from services import recording_medium_service
    from services.ignore_list_service import IgnoreList
    from services.run_store_service import RunStore

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="guide.pdf")
    if pdf is not None:
        (paths.repository / paths.module).write_bytes(pdf.read_bytes())
    transcript, _ = pdf_source_service.build_page_transcript(pdf or _build_pdf(), is_read_as_pages=pdf is None)
    phase_file_service.write_phase_file(paths, 1, transcript)
    # a run made before the rename holds "pdf-markdown", read as the text-only mode
    recording_medium_service.write_chosen_medium(paths.output_root, "pdf-markdown")
    run_dir = paths.output_root / "logs" / "t"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 1}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=model)


def test_a_pdf_in_markdown_mode_has_its_blocks_as_paragraphs_and_no_frame():
    """The owner's second PDF mode: the text alone, written by the program into paragraphs, in place of phase 3's
    batches of pages."""

    from graph.nodes import prepare_speech_node
    from services import speech_file_service
    from support import fakes

    model = fakes.scripted()
    ctx = _make_markdown_mode_context(model)
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phases 2 and 3 complete with no model call", state["phases"] == {
        "frames_preparing": "complete", "frames_processing": "complete"} and model.call_count == 0, str(state))
    frames = phase_file_service.read_phase_file(ctx.paths, 2)
    harness.check("no frame, the medium named", frames["items"] == [] and frames["medium"] == "pdf-text-only", str(frames))
    speech = speech_file_service.read_speech(ctx.paths)
    text = " ".join(paragraph["text"] for paragraph in speech["paragraphs"])
    harness.check("every page's text is in the paragraphs, as phase 1 cleaned it",
                  all(sentence in text for _, sentence in PAGES) and "Printed from" not in text, text)
    harness.check("the run is one with no frame, and a PDF", phase_file_service.is_audio_run(ctx.paths)
                  and phase_file_service.is_pdf_run(ctx.paths))


def test_a_table_s_separator_and_header_repeated_on_every_page_are_kept():
    """A table broken over pages repeats its header and its separator row on each; the rule for lines repeated on most
    pages took the separator out, and the rows were no table (pdf-02)."""

    pages = ["|Tenant|Region|\n|---|---|\n|Tenant 01|Dublin|\nPrinted from AcmeSync Wiki",
             "|Tenant|Region|\n|---|---|\n|Tenant 02|Frankfurt|\nPrinted from AcmeSync Wiki",
             "|Tenant|Region|\n|---|---|\n|Tenant 03|Dublin|\nPrinted from AcmeSync Wiki"]
    repeated = pdf_source_service._list_repeated_lines(pages)
    removed: list[dict] = []
    kept = pdf_source_service._clean_page(pages[0], repeated, 1, removed)
    harness.check("the table's header and separator stay", "|---|---|" in kept and "|Tenant|Region|" in kept, kept)
    harness.check("the print footer still leaves", "Printed from" not in kept, kept)


def test_a_pdf_run_with_no_mode_takes_the_settings_before_it_reads_its_pdf():
    """The owner's default picks a PDF's mode by its size; a run made before, already read as pages, keeps its pages."""

    folder = Path(tempfile.mkdtemp())
    paths = fx.make_paths(folder, module="guide.pdf")
    guide = folder / "guide.pdf"
    document = pymupdf.open()
    for _ in range(120):
        document.new_page()
    document.save(guide)
    document.close()
    harness.check("a new PDF run of 120 pages is read as text only under by-size",
                  config.settings.pdf.default_mode == "by-size"
                  and run_command._read_default_pdf_mode(paths, guide, None) == "text-only")
    harness.check("pages 1 to 10 of it are a small PDF, read as pages as images",
                  run_command._read_default_pdf_mode(paths, guide, InputRangeSpec(first_page=1, last_page=10))
                  == "pages-as-images")
    harness.check("a video takes none", run_command._read_default_pdf_mode(paths, Path("talk.mp4"), None) == "")
    phase_file_service.write_phase_file(paths, 1, {"segments": []})
    harness.check("a run that read its PDF already keeps how it was read",
                  run_command._read_default_pdf_mode(paths, guide, None) == "")


def test_a_queued_pdf_run_carries_its_mode_on_its_run_line():
    """The page's Processing mode reaches the run through the queue's run line, as Process as does."""

    from models.run_job import RunJobRequest
    from services import job_queue_service

    request = RunJobRequest(video=Path("inbox/guide.pdf"), output_root=Path("output/guide"), pdf_mode="text-only")
    command = job_queue_service.build_run_command(request)
    harness.check("the line says --pdf-mode text-only", command[command.index("--pdf-mode") + 1] == "text-only",
                  str(command))
    harness.check("the request keeps it through its JSON form",
                  RunJobRequest.from_dict(request.as_dict()).pdf_mode == "text-only")
    harness.check("pages is the default and adds nothing", "--pdf-mode" not in job_queue_service.build_run_command(
        RunJobRequest(video=Path("inbox/guide.pdf"), output_root=Path("output/guide"))))


def test_a_heading_starts_its_own_block_even_right_under_a_table():
    """A table ending on the next section's heading with no blank line made one block of both (pdf-02)."""

    blocks = pdf_source_service._split_blocks("|A|B|\n|---|---|\n|1|2|\n## January\nIn January 41 million calls.\n\nAfter.")
    harness.check("the table, the heading with its paragraph, and the next paragraph",
                  blocks == ["|A|B|\n|---|---|\n|1|2|", "## January\nIn January 41 million calls.", "After."], str(blocks))


def test_a_pdf_run_branched_from_phase_2_takes_the_mode_chosen():
    """A PDF run past phase 2 changes its processing mode in a branch, so nothing it made is replaced."""

    from services import recording_medium_service
    from services import run_operations_service

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="guide.pdf")
    transcript, _ = pdf_source_service.build_page_transcript(_build_pdf())
    phase_file_service.write_phase_file(paths, 1, transcript)
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf", "items": []})
    result = run_operations_service.create_branch(paths.output_root, 2, "text-only")
    target = Path(result.records[0]["folder"]) if result.records else None
    harness.check("the branch is made and reads the PDF as text only", result.exit_code == 0 and target is not None
                  and recording_medium_service.read_chosen_medium(target) == "pdf-text-only", str(result.lines))
    harness.check("the run branched from keeps its own mode",
                  recording_medium_service.read_chosen_medium(paths.output_root) is None)
    back = run_operations_service.create_branch(target, 2, "pages-as-images")
    harness.check("a branch of it back to pages has no chosen medium", back.exit_code == 0
                  and recording_medium_service.read_chosen_medium(Path(back.records[0]["folder"])) is None, str(back.lines))
    refused = run_operations_service.create_branch(paths.output_root, 3, "markdown")
    harness.check("a mode from another phase than 2 is refused by name", refused.exit_code == 2
                  and "phase 2" in refused.lines[0], str(refused.lines))


def test_a_pdf_run_read_by_the_removed_reader_still_opens_and_branches():
    """MinerU, a second PDF reader, was removed: a run folder that kept its choice in `run-pdf-reader.json`, and a queued
    job that names it, still open, and a branch of that run from phase 1 reads its PDF again with pymupdf4llm."""

    from models.run_job import RunJobRequest
    from services import run_operations_service

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="guide.pdf")
    transcript, _ = pdf_source_service.build_page_transcript(_build_pdf())
    phase_file_service.write_phase_file(paths, 1, transcript)
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf", "items": []})
    (paths.output_root / "run-pdf-reader.json").write_text('{"reader": "mineru"}', encoding="utf-8")
    summary = run_status_service._summarise_folder("output", paths.output_root)
    harness.check("the run's row is read, with no reader in it", summary["name"] == paths.output_root.name
                  and "pdf_reader" not in summary, str(sorted(summary)))
    result = run_operations_service.create_branch(output_root=paths.output_root, phase=1)
    target = Path(result.records[0]["folder"]) if result.records else None
    harness.check("the branch from phase 1 is made and keeps no phase", result.exit_code == 0 and target is not None
                  and not (target / "01_transcript.json").exists(), str(result.lines))
    job = RunJobRequest.from_dict({"video": "inbox/guide.pdf", "output_root": "output/guide", "pdf_reader": "mineru"})
    harness.check("a queued job naming the reader is read without it", job.video == Path("inbox/guide.pdf")
                  and "pdf_reader" not in job.as_dict(), str(job))


def test_the_not_cited_finding_says_a_fact_held_earlier_is_cited_by_its_id_alone():
    """A second part of a heading circled between "no block uses F4.26" and "an earlier section already says" it: the
    first finding's fix said only to add what the entry establishes (pdf-02)."""

    import inspect

    from validators import document_validator

    source = inspect.getsource(document_validator)
    harness.check("the fix names the way out", "is never written again: its id" in source)


def test_a_pdf_page_s_text_heard_by_its_frame_is_no_writing_entry():
    """Page 7's text, one entry, held the last incident and the January to March sections, and was filed under the
    incidents' chapter (pdf-02, DEFECTS 138); a video frame's speech stays an entry."""

    for medium, has_speech in (("pdf", False), (None, True)):
        integration = {"frames": [{"index": 6, "timestamp": 360.0, "medium": medium}], "unreadSpeech": [],
                       "entries": [{"id": "F6.1", "kind": "facts", "text": "INC-3179 closes the log.", "start": 360.0},
                                   {"id": "S0014", "kind": "speech", "text": "## January", "start": 361.0, "end": 362.0}]}
        ids = [entry["id"] for entry in document_plan_utils.select_document_entries(integration)]
        harness.check(f"a {medium or 'video'} frame's heard speech {'is' if has_speech else 'is not'} an entry",
                      ("S0014" in ids) == has_speech and "F6.1" in ids, str(ids))



def test_a_table_written_as_markdown_counts_as_quoted_in_the_outline():
    """Asked to quote every line the reader finds under `## Screen` as well, a PDF page's outline wrote every row of
    its table a second time beside `## As markdown` (pdf-02)."""

    from validators import item_validator

    lines = ["Tenant 01 Frankfurt 162 ms", "Tenant 02 Dublin 118 ms", "Tenant 03 Frankfurt 181 ms"]
    record = {"frame": 0, "screen": '- table: the tenants\' latency, header "Tenant", "Region"',
              "as_markdown": "| Tenant | Region | Median |\n|---|---|---|\n| Tenant 01 | Frankfurt | 162 ms |\n"
                             "| Tenant 02 | Dublin | 118 ms |\n| Tenant 03 | Frankfurt | 181 ms |"}
    previous = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: lines
    try:
        _all, missing = item_validator._list_unquoted_lines(fx.make_paths(), "frame-000", record, set())
        _all, missing_without = item_validator._list_unquoted_lines(fx.make_paths(), "frame-000",
                                                                    {**record, "as_markdown": ""}, set())
    finally:
        item_validator.read_screen_lines = previous
    harness.check("the rows the table writes are quoted", missing == [], str(missing))
    harness.check("without the table they are missing", len(missing_without) == 3, str(missing_without))


def test_a_fact_restating_one_table_row_is_dropped_while_a_fact_remains():
    """A PDF page's worker wrote one fact per incident beside the incident table, 31 a page (pdf-02-report-v2)."""

    from validators import item_validator

    record = {"as_markdown": "| Incident | Date | Duration |\n|---|---|---|\n| INC-3100 | 2026-01-10 | 30 min |\n"
                             "| INC-3101 | 2026-02-11 | 82 min |",
              "facts": ["The log lists every incident of the quarter, INC-3100 to INC-3179.",
                        "Incident INC-3100 occurred on 2026-01-10 and lasted 30 min.",
                        "Incident INC-3101 occurred on 2026-02-11 and lasted 82 min.",
                        "Incident INC-3101 lasted 82 min, the longest of 2026."]}
    kept = item_validator.drop_table_row_facts(record)["facts"]
    harness.check("the row restatements go, the facts beyond a row stay", kept == [record["facts"][0], record["facts"][3]],
                  str(kept))
    only_rows = {**record, "facts": record["facts"][1:3]}
    harness.check("a record whose every fact is a row keeps them", item_validator.drop_table_row_facts(only_rows)
                  == only_rows)


def test_a_page_s_markdown_is_cut_at_its_headings_and_placed_among_its_facts():
    """Page 7's one markdown note held the last incident row and three months' tables, and was filed under March, the
    page's last heading (pdf-02-report-v2, DEFECTS 140)."""

    from utils import frame_merge_utils

    markdown = ("| Incident | Date |\n|---|---|\n| INC-3179 | 2026-02-28 |\n"
                "## January\n| Week | Calls |\n|---|---|\n| Week 1 | 10.2 |\n| Week 2 | 10.4 |\n"
                "## February\n| Week | Calls |\n|---|---|\n| Week 1 | 9.5 |\n| Week 2 | 9.7 |")
    facts = ["The incident log ends with INC-3179 on 2026-02-28.", "In January the gateway handled 41 million calls.",
             "January's weeks ran from 10.2 to 10.4 million calls.", "In February the gateway handled 38 million calls.",
             "February's weeks ran from 9.5 to 9.7 million calls."]
    placed = frame_merge_utils._list_page_texts(facts, markdown, True)
    kinds = [(kind, text.split("\n")[0][:12]) for kind, text in placed]
    harness.check("the continued table first, each month's table after its last fact",
                  [kind for kind, _ in kinds] == ["markdown", "facts", "facts", "facts", "markdown", "facts", "facts",
                                                  "markdown"]
                  and placed[0][1].startswith("| Incident") and placed[4][1].startswith("## January")
                  and placed[7][1].startswith("## February"), str(kinds))
    continued = frame_merge_utils._list_page_texts(["Median latency stayed under 250 ms."],
                                                   "| Week | Calls |\n|---|---|\n| Week 3 | 11.5 |", True)
    harness.check("a page's one table with no heading stands before its facts when it continues the page before (pdf-02)",
                  [kind for kind, _ in continued] == ["markdown", "facts"], str(continued))
    own = frame_merge_utils._list_page_texts(["Error 401 means the token expired."], "| Alert | Action |\n|---|---|\n| A | B |")
    harness.check("the page's own table stands after its facts (pdf-05)", [kind for kind, _ in own] == ["facts", "markdown"],
                  str(own))
    bullets = frame_merge_utils._list_page_texts(["The contact is the platform team."], "- platform-team@acmesync.example")
    harness.check("a page's markdown with no heading stays after its facts (pdf-03)",
                  [kind for kind, _ in bullets] == ["facts", "markdown"], str(bullets))


def test_two_tables_with_no_heading_between_are_placed_apart():
    """A page's notes wrote the last tenants and the first incidents as two tables with no heading between them; both
    were filed after the page's last fact, under the next section (pdf-02)."""

    from utils import frame_merge_utils

    markdown = ("| Tenant | Region |\n|---|---|\n| Tenant54 | Dublin |\n| Tenant60 | Dublin |\n\n"
                "| Incident | Date |\n|---|---|\n| INC-3100 | 2026-01-10 |")
    facts = ["The tenant table ends with Tenant54 to Tenant60.", "The incident log lists every incident of the quarter."]
    placed = frame_merge_utils._list_page_texts(facts, markdown, True)
    harness.check("the tenants' rows before the incidents' fact, the incidents' table after it",
                  [kind for kind, _ in placed] == ["markdown", "facts", "facts", "markdown"]
                  and placed[0][1].startswith("| Tenant") and placed[3][1].startswith("| Incident"), str(placed))


def test_a_comment_in_a_code_fence_is_no_heading_and_a_page_s_own_text_follows_its_facts():
    """A page's yaml block opened with "# gateway.yaml", read as a heading: the text above it was placed before the
    page's facts as if it continued the page before, and the chapter before took it (pdf-05)."""

    from utils import frame_merge_utils

    markdown = ("Sandbox keys expire after 14 days.\n\n```bash\nacmesync key issue\n```\n\n"
                "```yaml\n# gateway.yaml\ntoken:\n```")
    harness.check("the fence's comment does not cut the markdown", len(frame_merge_utils._split_at_headings(markdown)) == 1)
    placed = frame_merge_utils._list_page_texts(["Sandbox keys expire after 14 days."], markdown)
    harness.check("a page opening with its heading has its markdown after its facts",
                  [kind for kind, _ in placed] == ["facts", "markdown"], str(placed))


def test_a_page_s_markdown_of_only_sentences_is_no_entry():
    """A wiki page's markdown held its heading and its sentences, which its facts state; each became an entry, and the
    writer wrote "The wiki page presents these rules verbatim" for it under every heading (pdf-04)."""

    from utils import frame_merge_utils

    record = {"index": 5, "timestamp": 300.0, "facts": ["An alert fires when a tenant's error rate stays above 2 percent."],
              "as_markdown": "### Monitoring\nAn alert fires when a tenant's error rate stays above 2 percent.\n"
                             "### Glossary\n- Tenant: one partner's configuration."}
    entries = frame_merge_utils._list_frame_entries([record], {}, frozenset({5}))
    markdown = [entry["text"] for entry in entries if entry["kind"] == "markdown"]
    harness.check("the sentences' part goes, the list's part stays",
                  markdown == ["### Glossary\n- Tenant: one partner's configuration."], str(markdown))
    video = frame_merge_utils._list_frame_entries([record], {}, frozenset())
    harness.check("a recording's frame keeps its markdown whole",
                  [entry["kind"] for entry in video].count("markdown") == 1, str(video))


def test_a_pdf_s_chapter_request_asks_no_am_or_pm():
    """A wiki page's "10:00:00" was written "10:00:00 AM", the request asking for AM or PM (pdf-04)."""

    from prompts import system_prompt_builder

    text = system_prompt_builder.render_recording_section([], True, is_pdf=True)
    harness.check("the PDF's section says a time stands as written", "no AM or PM is added" in text, text)
    # The wiki's second run planned to replace two figures by diagrams, which the frame tool no longer records
    # The wiki's chapter 8 writer weighed naming Van Helmont by his role, a meeting's privacy rule
    harness.check("the PDF's section says a person it names keeps the printed name", "so is a person it names" in text, text)
    harness.check("the PDF's section says a figure is placed as its cut, never replaced",
                  "is placed as its cut" in text and "never replaced by a diagram" in text, text)


def test_a_fact_saying_a_section_begins_is_dropped():
    """Told a heading is never a fact, a page's worker still wrote "An "Incidents" section begins, listing ..." (pdf-02,
    twice), and the chapter started at it."""

    from validators import item_validator

    record = {"facts": ['An "Incidents" section begins, listing incidents by Incident ID, Date and Cause.',
                        "The table continues from the previous page.", "The tenant table lists 60 tenants.",
                        "The section on retries says a call is retried three times."]}
    harness.check("the structure facts go, the knowledge stays",
                  item_validator.drop_structure_facts(record)["facts"]
                  == ["The tenant table lists 60 tenants.", "The section on retries says a call is retried three times."])
    alone = {"facts": ["A 'January' section begins."]}
    harness.check("a record's only fact stays", item_validator.drop_structure_facts(alone) == alone)


def test_a_table_header_repeated_over_a_continued_table_is_not_said_earlier():
    """A continued table's header row was refused as said earlier, and the writer dropped the table with its last row
    (pdf-02)."""

    from utils import document_text_utils

    earlier = "| Incident | Date |\n|---|---|\n| INC-3100 | 2026-01-10 |"
    later = "| Incident | Date |\n|---|---|\n| INC-3179 | 2026-02-27 |"
    harness.check("the header row is no sentence said earlier",
                  document_text_utils.find_sentences_said_earlier(later, earlier) == [])
    harness.check("a row said twice still is", document_text_utils.find_sentences_said_earlier(earlier, earlier) != [])


def test_a_one_level_document_s_first_heading_is_named_its_title_for_the_agenda():
    """Every heading of a report read by a former reader had one level, and the agenda made the report's title its first point
    (pdf-02, markdown mode)."""

    headings = [{"moment": 1, "level": 1, "title": "Quarterly report"}, {"moment": 5, "level": 1, "title": "Tenants"},
                {"moment": 7, "level": 1, "title": "Incidents"}]
    text = document_messages._render_document_headings(headings)
    harness.check("the first heading is named the title, the others are not",
                  "Quarterly report (the document title, no point)" in text and "Tenants (" not in text, text)
    two_levels = document_messages._render_document_headings([{**headings[0]}, {**headings[1], "level": 2}])
    harness.check("a document with two levels is listed as before", "no point" not in two_levels, two_levels)


def test_a_table_entry_carries_its_row_count_in_the_chapter_request():
    """A writer counted a table of 80 incidents as "Seventy-two incidents" (pdf-02, markdown mode, DEFECTS 139)."""

    from utils import section_markdown_utils

    harness.check("a table on one line, as a speech paragraph holds it",
                  section_markdown_utils.count_table_rows("| A | B | |---|---| | 1 | 2 | | 3 | 4 |") == 2)
    harness.check("two tables on lines of their own, an empty cell kept whole",
                  section_markdown_utils.count_table_rows("| A | B |\n|---|---|\n| 1 | |\n\n| C |\n|---|\n| x |") == 2)
    harness.check("no table, no count", section_markdown_utils.count_table_rows("Two incidents had impact.") == 0)
    mark = document_messages._render_table_rows_mark({"text": "| A |\n|---|\n| 1 |\n| 2 |\n| 3 |"})
    harness.check("the request names the count", "a table of 3 rows" in mark, mark)


def test_a_document_s_own_table_of_contents_is_taken_out():
    """RFC 9457's and a NACA report's contents became a chapter of headings and page numbers (pdf-real-rfc9457,
    pdf-real-nasa-scan-12p)."""

    removed: list[dict] = []
    page_1, running = pdf_source_service._drop_contents_blocks(
        ["## **Table of Contents**\n| 1. Introduction | 3 |\n|---|---|", "| 2. Scope | 4 |\n|---|---|"], False, 1, removed)
    page_2, running = pdf_source_service._drop_contents_blocks(["| 3. Terms | 5 |\n|---|---|", "## 1. Introduction\nText."],
                                                               running, 2, removed)
    harness.check("the contents and their run-on tables go, the first section stays",
                  page_1 == [] and page_2 == ["## 1. Introduction\nText."] and len(removed) == 3, str((page_1, page_2)))
    harness.check("a table after other text is kept", pdf_source_service._drop_contents_blocks(
        ["Text.", "| a | b |\n|---|---|"], False, 1, [])[0] == ["Text.", "| a | b |\n|---|---|"])


def test_a_list_and_its_lead_in_are_one_block():
    """A form's lead-in and its three list items were four paragraphs, each a brief reply (pdf-real-fw9)."""

    blocks = pdf_source_service._split_blocks("To reduce your risk:\n\n- Protect your number,\n\n- Check your employer,"
                                              "\n\n- Choose a preparer with care.\n\nA new paragraph follows.")
    harness.check("the lead-in and its items are one block, the paragraph after stays apart",
                  blocks == ["To reduce your risk:\n- Protect your number,\n- Check your employer,\n- Choose a preparer with care.",
                             "A new paragraph follows."], str(blocks))


def test_a_connective_line_between_formulas_joins_the_formula_after_it_defect():
    # Defect: a scan's "where" between two formulas was a paragraph of one word, an entry the writer had to cite
    # (DEFECTS 497). Fix: a short lower-case line right before a formula block is joined to it
    blocks = pdf_source_service._split_blocks("$$\ny = a x\n$$\n\nwhere\n\n$$\na = 2\n$$\n\nSummary\n\n$$\nb = 3\n$$")
    harness.check("the connective line opens the formula after it",
                  blocks == ["$$\ny = a x\n$$", "where\n$$\na = 2\n$$", "Summary", "$$\nb = 3\n$$"], str(blocks))


def test_a_heading_alone_in_its_block_is_joined_to_the_next():
    """Read as markdown, each heading alone in its block became a paragraph, set aside as "title slide text" under every
    chapter (pdf-02, both readers)."""

    blocks = pdf_source_service._split_blocks("## **January**\n\nIn January 41 million calls.\n\n## February\n\n## March\n\nAfter.")
    harness.check("each heading joined to its text, a heading before a heading left alone",
                  blocks == ["## **January**\nIn January 41 million calls.", "## February", "## March\nAfter."], str(blocks))


def test_a_final_summary_over_its_word_limit_is_sent_back_unjudged():
    """A manual's summary of 94 words against 59 spent its one attempt and blocked the run (pdf-05)."""

    from types import SimpleNamespace

    from services import write_conversation_service

    spec = SimpleNamespace(inputs={"maxWords": 10})
    long = {"summary": [{"text": "one two three four five six"}, {"text": "seven eight nine ten eleven"}]}
    message = write_conversation_service._count_summary_over(spec, long)
    harness.check("over the limit: sent back with its count", message is not None and "11 words, more than its 10" in message,
                  str(message))
    harness.check("within it: judged", write_conversation_service._count_summary_over(
        spec, {"summary": [{"text": "one two three"}]}) is None)


def test_a_final_summary_with_a_sentence_naming_no_id_is_sent_back_unjudged():
    """The second Wikipedia run's summary wrote 9 sentences with no entry id, which spent its one attempt and blocked the
    run; the check names each such sentence, so it is told so first, unjudged, as an over-long summary is."""

    from services import write_conversation_service

    unsourced = {"summary": [{"text": "Light drives the cycle.", "entry_ids": ["S0001"]},
                             {"text": "The Z scheme reduces NADP to NADPH.", "entry_ids": []}]}
    message = write_conversation_service._find_unsourced_summary(unsourced)
    harness.check("a sentence with no id: sent back naming it", message is not None and "The Z scheme reduces" in message
                  and "no attempt was spent" in message, str(message))
    harness.check("every sentence sourced: judged", write_conversation_service._find_unsourced_summary(
        {"summary": [{"text": "Light drives the cycle.", "entry_ids": ["S0001"]}]}) is None)


def test_a_subheading_repeating_its_chapter_s_title_is_not_printed():
    """A PDF's writer made the page's heading a subheading under the chapter of the same name (pdf-02)."""

    from services import document_assembly_service

    section = {"blocks": [{"kind": "subheading", "text": "Incidents"}, {"kind": "subheading", "text": "Follow-up owners"}]}
    lines, _ = document_assembly_service._render_section_blocks(section, set(), 0, ("Incidents", "Q1 incident log"))
    harness.check("the repeat goes, another subheading stays", lines == ["#### Follow-up owners", ""], str(lines))


def test_an_unresolved_todo_about_an_open_point_replaces_it_with_its_question():
    """The review asked "Q1 report's full scope?" again in its own words beside the line, and the document listed one
    point twice (pdf-02)."""

    from utils import document_edit_utils

    body = "## Open points\n\n- Q1 report's full scope?\n- Which two incidents had customer impact?\n"
    replaced = document_edit_utils.add_open_questions(body, ["What does the Q1 report leave out?"],
                                                      "Q1 report's full scope?")
    harness.check("the point is replaced in place, the others kept", replaced.splitlines()[2:4] == [
        "- What does the Q1 report leave out?", "- Which two incidents had customer impact?"], replaced)
    added = document_edit_utils.add_open_questions(body, ["Who owns INC-3179?"])
    harness.check("a question about no listed point is added", "- Who owns INC-3179?" in added
                  and "- Q1 report's full scope?" in added, added)


def test_a_page_sentence_no_note_holds_is_added_as_a_fact():
    """Two bullets a picture covered on a slide were in the file's text and in no note (pdf-03)."""

    from validators import item_validator

    paths = fx.make_paths()
    page_text = ("# Asynchronous refunds\n- The gateway answers 202 Accepted at once\n"
                 "- The partner polls the refund's status URL\n- A webhook reports the final state\n|A|B|\n"
                 "ai Q2 Q3 Q4 Qs\n240\n")
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf", "items": [
        {"slug": "frame-003", "index": 3, "medium": "pdf", "transcriptCurrent": page_text}]})
    record = {"facts": ["The gateway answers 202 Accepted at once."], "screen": '- title "Asynchronous refunds"'}
    facts = item_validator.add_missing_page_sentences(paths, "frame-003", record)["facts"]
    harness.check("the two covered bullets are added, word for word; the held one, the heading and the table not",
                  facts == ["The gateway answers 202 Accepted at once.", "The partner polls the refund's status URL",
                            "A webhook reports the final state"], str(facts))
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "items": [
        {"slug": "frame-003", "index": 3, "transcriptCurrent": page_text}]})
    harness.check("a video frame's speech adds nothing",
                  item_validator.add_missing_page_sentences(paths, "frame-003", record) == record)


def test_a_team_mailbox_is_no_person_s_address():
    """A deck's closing slide gave its team's mailbox as the rollout's contact; refused as a person's address, the
    document kept no way to reach anyone (pdf-03)."""

    from utils import document_text_utils

    text = "Write to platform-team@acmesync.example or support@acmesync.example; Jonas is jonas.weber@acmesync.example."
    harness.check("the two mailboxes pass, the person's address is named",
                  document_text_utils.PERSONAL_EMAIL.findall(text) == ["jonas.weber@acmesync.example"],
                  str(document_text_utils.PERSONAL_EMAIL.findall(text)))


def test_an_address_a_pdf_prints_is_published_and_a_meeting_s_is_still_checked():
    """A published document's own contact, "<office>@<agency>.example", was refused as a person's address; a PDF prints
    what its authors chose to publish, as a social handle is, while an address a meeting's screen shows stays checked."""

    from support import fixtures as fx
    from utils import document_text_utils
    from validators import document_validator

    text = "Send comments to cyberframework@agency.example; ask jonas.weber@acmesync.example."
    for medium, expected in ((output_contract.PDF_TEXT_ONLY_MEDIUM, ["jonas.weber@acmesync.example"]),
                             (output_contract.VIDEO_MEDIUM, ["cyberframework@agency.example", "jonas.weber@acmesync.example"])):
        paths = fx.make_paths(Path(tempfile.mkdtemp()), module="framework.pdf")
        phase_file_service.write_phase_file(paths, 1, {"segments": [
            {"start": 0.0, "end": 1.0, "text": "Comments go to cyberframework@agency.example."}]})
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                       "medium": medium, "items": []})
        found = document_text_utils.PERSONAL_EMAIL.findall(document_validator._hide_published_addresses(paths, text))
        harness.check(f"{medium}: only the addresses the source did not publish are checked", found == expected, str(found))


def test_a_scan_s_page_tree_heading_and_initialled_byline_are_chrome_defect():
    # A scan read by Unlimited-OCR-2 wrote the wiki's side panel title as "# Page tree" and its byline as "JW Created by
    # ..." after the author's avatar initials; both stood as paragraphs (DEFECTS 522). Now both leave; a heading stays
    removed: list[dict] = []
    kept = pdf_source_service._clean_page("# Page tree\n# Overview\nJW Created by Jonas Weber, last updated by Mara Keller "
                                          "214 views APPROVED\nThe gateway runs in two regions.\n# Attachments\n", [], 1,
                                          removed)
    harness.check("the page tree heading and the byline leave", "Page tree" not in kept and "Created by" not in kept, kept)
    harness.check("the section headings and the content stay", "# Overview" in kept and "two regions" in kept
                  and "# Attachments" in kept, kept)


def test_a_like_line_read_with_tags_and_junk_is_chrome():
    """A scan's "Like - 3 people like this" was read as "te<sup>Like</sup> - 3 people like this" and stayed (pdf-04)."""

    removed: list[dict] = []
    kept = pdf_source_service._clean_page("Overview\nte<sup>Like</sup> - 3 people like this\n", [], 3, removed)
    harness.check("the line leaves, the heading stays", "people like this" not in kept and "Overview" in kept, kept)


def test_a_like_line_joined_to_a_sentence_takes_only_itself():
    """The reader joined the like line to the paragraph above it, and the pattern's open start took the paragraph's
    sentence with it (pdf-04, page 2)."""

    removed: list[dict] = []
    kept = pdf_source_service._clean_page(
        "The signature scheme follows HTTP Message Signatures. te<sup>Like</sup> - 3 people like this\n", [], 2, removed)
    harness.check("the sentence stays, the like line leaves",
                  "Message Signatures." in kept and "people like this" not in kept, kept)


def _build_figures_pdf() -> Path:
    """One page: a paragraph, a picture with "Figure 1." under it, a drawn chart of twelve lines, a code block on its
    background, a table, and a paragraph after."""

    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((60, 70), "The gateway signs each token before it leaves.", fontsize=11)
    picture = numpy.full((120, 200, 3), 200, dtype=numpy.uint8)
    cv2.rectangle(picture, (20, 20), (180, 100), (40, 90, 160), -1)
    page.insert_image(pymupdf.Rect(60, 90, 360, 270), stream=cv2.imencode(".png", picture)[1].tobytes())
    page.insert_text((60, 288), "Figure 1. The token's path through the gateway", fontsize=9)
    shape = page.new_shape()
    for step in range(12):
        shape.draw_line((60 + step * 20, 500), (70 + step * 20, 420 + (step % 4) * 15))
    shape.finish(color=(0, 0, 0))
    shape.draw_rect(pymupdf.Rect(60, 560, 540, 600))
    shape.finish(fill=(0.1, 0.1, 0.1))
    shape.commit()
    page.insert_text((70, 585), "acmesync key issue --tenant partner-b", fontsize=10, color=(1, 1, 1))
    for row in range(3):
        for column in range(3):
            cell = pymupdf.Rect(60 + column * 120, 640 + row * 25, 180 + column * 120, 665 + row * 25)
            page.draw_rect(cell, color=(0, 0, 0))
            page.insert_text((cell.x0 + 4, cell.y0 + 16), f"r{row}c{column}", fontsize=9)
    page.insert_text((60, 780), "After the token is signed, the provider checks it.", fontsize=11)
    path = Path(tempfile.mkdtemp()) / "figures.pdf"
    document.save(path)

    return path


def test_a_page_s_pictures_and_drawn_charts_are_its_figures_and_its_code_and_tables_are_not():
    """Read as text only, every chart, diagram and screenshot of a PDF was lost."""

    with pymupdf.open(_build_figures_pdf()) as document:
        figures = pdf_source_service.find_figures(document[0])
    harness.check("two figures: the picture, then the chart; the code block and the table are none",
                  len(figures) == 2 and figures[0]["box"][1] < figures[1]["box"][1], str(figures))
    harness.check("the picture's caption is the Figure line under it",
                  figures[0]["caption"] == "Figure 1. The token's path through the gateway", str(figures))
    harness.check("a chart with no caption line has none: its own words are labels", figures[1]["caption"] == "",
                  str(figures))


def test_read_as_text_only_a_figure_stands_before_its_caption_on_the_timeline():
    """A figure stands where its caption stood; the caption is said over its frame, so no paragraph holds it."""

    transcript, removed = pdf_source_service.build_page_transcript(_build_figures_pdf(), is_read_as_pages=False)
    figures = transcript.get("figures", [])
    following = next(segment for segment in transcript["segments"] if segment["text"].startswith("|"))
    harness.check("both figures are in the transcript with their page", [figure["page"] for figure in figures] == [1, 1],
                  str(figures))
    harness.check("no paragraph holds the caption, and its removal is recorded",
                  not any("Figure 1." in segment["text"] for segment in transcript["segments"])
                  and any(entry["rule"] == "a figure's caption, said over its figure" for entry in removed), str(removed))
    harness.check("the picture stands just before the block that followed its caption",
                  figures[0]["start"] < following["start"]
                  and all(segment["start"] <= figures[0]["start"] or segment["start"] >= following["start"]
                          for segment in transcript["segments"]), str((figures, transcript["segments"])))
    pages_transcript, _removed = pdf_source_service.build_page_transcript(_build_figures_pdf())
    harness.check("read as pages, the page's frame shows them, so none is listed", "figures" not in pages_transcript)


def test_read_as_text_only_each_figure_is_a_frame_phase_3_reads():
    """The owner: a PDF's images and diagrams are cut and put in the document, or redrawn, by the worker that writes it."""

    from graph import routing
    from graph.nodes import prepare_speech_node
    from support import fakes

    model = fakes.scripted()
    ctx = _make_markdown_mode_context(model, _build_figures_pdf())
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    frames = phase_file_service.read_phase_file(ctx.paths, 2)
    harness.check("each figure is an item of the frames file, cut into frames/", len(frames["items"]) == 2
                  and all((ctx.paths.output_root / item["path"]).is_file() for item in frames["items"]), str(frames))
    first = frames["items"][0]
    harness.check("the picture's caption is said over it, and its box is its place on the page at 200 DPI",
                  first["transcriptCurrent"] == "Figure 1. The token's path through the gateway"
                  and first["medium"] == "pdf-figure" and first["box"] == [167, 250, 1000, 750], str(first))
    harness.check("phase 2 is complete, phase 3 left open with the figures queued, and no model call",
                  state["phases"] == {"frames_preparing": "complete"} and state["queue"] == ["frame-000", "frame-001"]
                  and model.call_count == 0, str(state))
    harness.check("the route goes into the frame loop", routing.route_after_speech_structuring(
        {**state, "cursor": 0}) == "frames_processing")
    resumed = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("a resume queues the figures still to read", resumed["queue"] == ["frame-000", "frame-001"], str(resumed))


def test_a_text_only_pdf_s_paragraphs_and_figures_are_its_moments_together():
    from utils import document_plan_utils

    integration = {"frames": [{"timestamp": 14.99, "medium": "pdf-figure", "screenKind": "diagram"}],
                   "entries": [{"kind": "speech", "start": 0.0}, {"kind": "speech", "start": 15.0},
                               {"kind": "speech", "start": 60.0}]}
    harness.check("every paragraph is a moment, the figure one among them",
                  document_plan_utils._list_moment_starts(integration)
                  == [(0.0, ""), (14.99, "diagram"), (15.0, ""), (60.0, "")],
                  str(document_plan_utils._list_moment_starts(integration)))


def test_a_document_s_back_matter_is_taken_out_and_its_content_stays():
    """A Wikipedia article's References and Notes, 11 pages, held one chapter on dry checks for two hours
    (pdf-real-wikipedia-photosynthesis)."""

    removed: list[dict] = []
    page_1, level = pdf_source_service._drop_back_matter(
        ["## Factors\nLight matters.", "## **See also**\n- Gateway design", "## **Notes**\n1. Said as FOH-toh.",
         "## **References**\n1. Weber J (2020). \"Tokens\". _Journal_."], 0, 1, removed)
    page_2, level = pdf_source_service._drop_back_matter(
        ["3. Keller M (2021). \"Keys\".", "### **Books**\n- Tokens, a primer", "## **External links**\n- example.org"],
        level, 2, removed)
    harness.check("the content stays and every back-matter block goes, onto the next page and under its subheadings",
                  page_1 == ["## Factors\nLight matters."] and page_2 == [] and len(removed) == 6, str((page_1, page_2)))
    kept, level = pdf_source_service._drop_back_matter(["## Appendix A\nThe gateway's limits."], level, 3, [])
    harness.check("a heading of the same level that is no back matter ends it", kept == ["## Appendix A\nThe gateway's limits."]
                  and level == 0, str(kept))
    notes, _level = pdf_source_service._drop_back_matter(["## Notes\nSave the key before you rotate it."], 0, 1, [])
    harness.check("a Notes section of prose is content", notes == ["## Notes\nSave the key before you rotate it."], str(notes))


def test_a_figure_s_notes_stand_among_the_paragraphs_by_their_time():
    """The merge sorted a speech file's entries by `start`, which a frame note lacks: phase 3 of the first run with a
    figure ended in a KeyError (pdf-figures-05-manual)."""

    from services import integration_service

    entries = [{"kind": "speech", "start": 75.0}, {"kind": "facts", "timestamps": [74.99]}, {"kind": "speech", "start": 60.0}]
    harness.check("a frame note's time is its first timestamp", [integration_service._read_entry_start(entry)
                                                               for entry in entries] == [75.0, 74.99, 60.0])


def test_a_part_that_names_itself_drops_its_closing_pointer_to_another_frame():
    from utils import frame_markdown_utils

    screen = ("- diagram: centre, white\n  - left side: the gateway's ring of stages (same as Frame 3)\n"
              "  - right side: five numbered steps\n  - header: same as frame 2")
    harness.check("the aside goes and the part's own words stay; a part that is only a pointer keeps it",
                  frame_markdown_utils.drop_pointer_asides(screen)
                  == "- diagram: centre, white\n  - left side: the gateway's ring of stages\n"
                     "  - right side: five numbered steps\n  - header: same as frame 2",
                  frame_markdown_utils.drop_pointer_asides(screen))


def test_a_pdf_s_paragraphs_have_no_speaker():
    """The owner: "there are no speaker in pdf, the speaker are for the audio flow"."""

    from utils import speech_markdown_utils

    parsed = {"profile": None, "paragraphs": [{"first": "S0001", "last": "S0001", "speaker": "", "text": "The gateway signs tokens."}]}
    text = speech_markdown_utils.render_speech(parsed)
    harness.check("the file is Paragraphs, each under its id alone", text.startswith("# Paragraphs")
                  and "## S0001\n" in text and "Speaker" not in text, text)
    harness.check("and reads back the same", speech_markdown_utils.parse_speech(text)[0] == parsed,
                  str(speech_markdown_utils.parse_speech(text)))
    spoken = {"profile": None, "paragraphs": [{**parsed["paragraphs"][0], "speaker": "Speaker B"}]}
    harness.check("an audio recording's keeps its speaker", "## S0001 · Speaker B" in speech_markdown_utils.render_speech(spoken))


def test_a_pdf_figure_s_stored_notes_are_headed_figure_and_read_back():
    from schemas import frame_record
    from utils import frame_markdown_utils

    record = {name: [] if name in ("facts", "builds_on", "contradicts_earlier", "open_questions", "tags", "urls",
                                   "action_items", "questions_asked", "screenshots", "take_aways") else ""
              for name in frame_record.FRAME_FIELDS}
    record.update({"frame": 3, "screen_kind": "chart", "facts": ["The gateway's latency halves in March."]})
    text = frame_markdown_utils.render_frame_document(record, "Figure")
    harness.check("the stored heading names a figure", text.startswith("# Figure 3 · chart"), text[:40])
    harness.check("and the title pattern reads it as frame 3",
                  frame_markdown_utils._FRAME_TITLE.match("Figure 3 · chart").group("number") == "3")


def test_the_hard_figures_are_found_one_each():
    """The owner: "make harder images (people, find on internet) and test with them". Public-domain photos of people
    on nine pages, each a hard case (tests/resources/pdf/build_hard_figures_pdf.py)."""

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_hard_figures_pdf", Path(__file__).resolve().parents[1] / "resources" / "pdf" / "build_hard_figures_pdf.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    path = builder.build(Path(tempfile.mkdtemp()) / "07-hard-figures.pdf")
    with pymupdf.open(path) as document:
        found = [pdf_source_service.find_figures(page) for page in document]
    counts = [len(figures) for figures in found]
    harness.check("one figure a page, the side-by-side portraits two, the directory's three headshots three",
                  counts == [1, 2, 1, 1, 3, 1, 1, 1, 1], str(counts))
    harness.check("each side-by-side portrait takes the caption under it alone",
                  [figure["caption"] for figure in found[1]] == ["Figure 2. The gateway's first owner", "Figure 3. Its second owner"],
                  str(found[1]))
    harness.check("the directory's headshots have no caption from the table's names",
                  all(not figure["caption"] for figure in found[4]), str(found[4]))
    harness.check("the drawn chart's box takes its month labels under the axis, and its Figure line is its caption",
                  found[5][0]["box"][3] > 410 and found[5][0]["caption"].startswith("Figure 4."), str(found[5]))
    harness.check("a photo filling the page with its title written on it is a poster, a figure",
                  found[6][0]["box"] == [0.0, 0.0, 595.0, 842.0], str(found[6]))
    harness.check("a photo stored in two strips is one figure", found[7][0]["box"][1] < 141 and found[7][0]["box"][3] > 512,
                  str(found[7]))
    harness.check("a photo framed with its caption inside the frame takes that caption",
                  found[8][0]["caption"] == "Mara Keller at the partner kick-off", str(found[8]))


def test_a_table_continues_only_the_table_that_ends_the_page_before():
    """A table with the same header after a page of paragraphs, or after text that followed the last table, was marked
    as continuing a table on the page before that had none."""

    table = "|Tenant|Region|\n|---|---|\n|Tenant 01|Dublin|"
    _blocks, header = pdf_source_service._mark_continued_tables([table], None, 3)
    paragraphs, header = pdf_source_service._mark_continued_tables(["Paragraph only."], header, 4)
    page_5, _header = pdf_source_service._mark_continued_tables([table], header, 5)
    harness.check("after a page with no table, a table is its own", not page_5[0].startswith("(The table continues"),
                  page_5[0][:60])
    _blocks, header = pdf_source_service._mark_continued_tables([table, "Text after the table."], None, 6)
    page_7, _header = pdf_source_service._mark_continued_tables([table], header, 7)
    harness.check("after a table that text followed, a table is its own", not page_7[0].startswith("(The table continues"),
                  page_7[0][:60])
    _blocks, header = pdf_source_service._mark_continued_tables(["Text first.", table], None, 8)
    page_9, _header = pdf_source_service._mark_continued_tables([table], header, 9)
    harness.check("a table ending the page before is continued", page_9[0].startswith("(The table continues the one on page 8"),
                  page_9[0][:60])


def test_a_removed_image_or_link_is_recorded_alone_and_its_line_s_words_stay():
    removed: list[dict] = []
    kept = pdf_source_service._clean_page("See ![chart](img.png) for the totals and [the summary](#summary) above.",
                                          [], 2, removed)
    harness.check("the line keeps its words", "for the totals" in kept and "the summary" in kept, kept)
    harness.check("the record holds what was taken out, never the kept words",
                  [entry["text"] for entry in removed] == ["![chart](img.png)", "[the summary](#summary)"], str(removed))


def test_a_highlight_s_mark_tags_are_dropped_and_its_words_kept():
    """The reader writes highlighted text inside `<mark>` tags: a document's title reached phase 4 as
    `# <mark>The Framework</mark>`, and a sentence kept a stray `<mark>` where a highlight began."""

    kept = pdf_source_service._clean_page("# <mark>The Framework 2.0</mark>\nIt pays (e.g. <mark>, renting space).", [], 1, [])
    harness.check("the heading and the sentence keep their words, with no tag", kept
                  == "# The Framework 2.0\nIt pays (e.g. , renting space).", kept)
    # A web page's links are underlined, and the reader writes them `<u>…</u>`, a struck mark `~~:~~` (a wiki article)
    underlined = pdf_source_service._clean_page("The <u>process of photosynthesis takes place in four stages</u> ~~:~~ [14]",
                                                [], 8, [])
    harness.check("underline tags and strikethrough marks go, their words stay",
                  underlined == "The process of photosynthesis takes place in four stages : [14]", underlined)


def test_a_figure_s_caption_finds_its_own_block_and_not_one_naming_another_figure():
    """The caption was matched with its digits masked, so "Figure 3" found the paragraph citing "Figure 1"."""

    figures = [{"caption": "Figure 3. The token path", "box": [60, 500, 300, 700]}]
    blocks = ["See Figure 1. The token path for the old gateway.", "Text between.", "Figure 3. The token path"]
    harness.check("the figure stands before its own caption's block",
                  pdf_source_service._list_figure_places(figures, blocks, 842) == [2],
                  str(pdf_source_service._list_figure_places(figures, blocks, 842)))


def test_a_table_of_contents_over_three_pages_is_taken_out_whole():
    """The continuation mark ran first, and the contents' third page, marked as continuing a table, no longer read as
    contents and was kept."""

    contents = "|Section|Page|\n|---|---|\n|1 Scope|3|"
    pages = [f"## Contents\n\n{contents}", contents, contents]
    removed: list[dict] = []
    carried, kept = {}, []
    for number, text in enumerate(pages, start=1):
        blocks, carried = pdf_source_service._list_page_blocks(text, number, removed, carried)
        kept += blocks
    harness.check("the contents' blocks go on every page", kept == [], str(kept))


def test_contents_read_one_line_a_block_are_taken_out_defect():
    # A scan's contents, read as one title and page number a block ("SUMMARY 1"), stayed after their heading's block
    # went, and a chapter was written from them. Now a block of such lines running on after the contents goes too
    page = "# CONTENTS\nPage\n\nSUMMARY 1\n\nINTRODUCTION 1\n\nDescription of gateways ..... 3\n\nThe gateway sends 12 retries."
    removed: list[dict] = []
    kept, _ = pdf_source_service._list_page_blocks(page, 1, removed, {})
    harness.check("the contents lines go, the text after them stays", kept == ["The gateway sends 12 retries."], str(kept))
    harness.check("a short line ending on a number elsewhere is kept", pdf_source_service._drop_contents_blocks(
        ["Text.", "REPORT No. 824"], False, 1, [])[0] == ["Text.", "REPORT No. 824"])


def test_a_text_only_pdf_of_figures_alone_is_no_failed_phase_2():
    """A PDF whose pages hold pictures and no text failed phase 2 as "no speech to structure"."""

    from graph.nodes import prepare_speech_node
    from support import fakes

    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    picture = numpy.full((200, 300, 3), 120, dtype=numpy.uint8)
    cv2.circle(picture, (150, 100), 60, (30, 160, 60), -1)
    page.insert_image(pymupdf.Rect(60, 90, 520, 400), stream=cv2.imencode(".png", picture)[1].tobytes())
    path = Path(tempfile.mkdtemp()) / "pictures.pdf"
    document.save(path)
    ctx = _make_markdown_mode_context(fakes.scripted(), path)
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phase 2 is complete and its figure queued", state["phases"].get("frames_preparing") == "complete"
                  and state["queue"] == ["frame-000"], str(state))


def test_a_code_block_stays_one_block_through_its_comments_and_blank_lines():
    text = "To issue a key:\n\n```bash\n# then set the key\nacmesync key issue\n\nacmesync key list\n```\n\nAfter the block."
    blocks = pdf_source_service._split_blocks(text)
    harness.check("the fence and everything in it is one block",
                  any(block.startswith("```bash") and block.endswith("```") and "acmesync key list" in block
                      for block in blocks) and "After the block." in blocks, str(blocks))


def test_a_link_s_address_follows_its_own_word_not_a_word_holding_it():
    text = pdf_source_service._add_link_addresses("Somewhere in the guide, click here.", [("here", "https://example.org")])
    harness.check("the address follows the word itself",
                  text == "Somewhere in the guide, click here <https://example.org>.", text)


def test_a_table_after_the_contents_on_the_next_page_is_kept_when_it_is_another_table():
    removed: list[dict] = []
    _blocks, running = pdf_source_service._drop_contents_blocks(
        ["## Contents\n| 1. Scope | 3 |\n|---|---|"], False, 1, removed)
    kept, _running = pdf_source_service._drop_contents_blocks(
        ["| Version | Date | Change |\n|---|---|---|\n| 1.2 | 2026-01 | Partner keys |"], running, 2, removed)
    harness.check("the revision table of the next page stays", len(kept) == 1, str(kept))


def test_a_paragraph_s_code_block_reads_back_with_its_fences():
    """Reading `02_speech.md` back skipped every fence line, so a PDF's code block reached phase 4 as plain lines."""

    from utils import speech_markdown_utils

    parsed = {"profile": None, "paragraphs": [{"first": "S0001", "last": "S0001", "speaker": "",
                                               "text": "```bash\n# then set the key\nacmesync key issue\n```"}]}
    harness.check("the fences stay in the paragraph", speech_markdown_utils.parse_speech(
        speech_markdown_utils.render_speech(parsed))[0] == parsed,
        str(speech_markdown_utils.parse_speech(speech_markdown_utils.render_speech(parsed))))


def test_a_pdf_has_no_speakers_stage():
    """A PDF run got a speakers stage on the page and a diarization recorded "done" though pyannote never ran."""

    from graph.nodes import transcribe_audio_node

    model = None
    ctx = _make_markdown_mode_context(model)
    harness.check("phase 1 tells no speakers apart in a PDF", not transcribe_audio_node._is_diarization_due(ctx))


def test_a_bold_numbered_heading_is_a_heading_when_the_document_marks_its_numbered_headings():
    """The reader marked every numbered section of a framework as a heading but one, which came out a bold line, so
    the agenda and the draft headings never named its section (NIST 5.2)."""

    texts = ["## **5. Improving Communication**\n\nText.\n\n## **5.1. Risk Management**\n\nMore text.",
             "**5.2. Integration with Other Programs**\n\nEvery organization faces risks."]
    marked = pdf_source_service._mark_numbered_headings(texts)
    harness.check("the bold numbered line takes the document's heading mark",
                  marked[1].startswith("## **5.2. Integration with Other Programs**"), marked[1])
    steps = ["# Setup\n\n**1. Install the agent**\n\nRun the installer."]
    harness.check("a document with no numbered heading keeps its bold steps",
                  pdf_source_service._mark_numbered_headings(steps) == steps, str(pdf_source_service._mark_numbered_headings(steps)))


def test_a_heading_keeps_no_link_address_and_the_sentence_after_it_takes_it():
    """A link in a heading kept its address in the heading ("Photosynthetic membranes and organelles <https://...>"),
    which the agenda and the draft headings then carried; a sentence holding the link's word takes the address."""

    text = "## Membranes and organelles\n\nEach organelle holds membranes."
    marked = pdf_source_service._add_link_addresses(text, [("organelles", "https://wiki.example/Organelle")])
    harness.check("the heading has no address and the link stays unwritten there",
                  marked.splitlines()[0] == "## Membranes and organelles", marked)
    sentence = pdf_source_service._add_link_addresses("## Notes\n\nSee the organelles page.",
                                                      [("organelles", "https://wiki.example/Organelle")])
    harness.check("a sentence holding the word takes the address", "organelles <https://wiki.example/Organelle>" in sentence,
                  sentence)


def test_an_equation_set_large_is_read_as_text_not_a_heading():
    """pymupdf4llm reads a line by its font size, and Wikipedia's reaction "2 glycolate + ATP → 3-phosphoglycerate"
    came out as a heading, which the agenda and the draft headings then took as a section. Over 29 PDF runs an arrow
    or a spaced plus marked that line alone; a heading ending in a period ("Appendix B: robustness.") stays one."""

    text = pdf_source_service._clean_page("#### 2 glycolate + ATP → 3-phosphoglycerate + ADP\n\n## Appendix B: robustness.",
                                          [], 1, [])
    harness.check("the equation is a line of text", text.splitlines()[0] == "2 glycolate + ATP → 3-phosphoglycerate + ADP", text)
    harness.check("a heading stays one", "## Appendix B: robustness." in text, text)


def test_a_paragraph_that_is_a_figure_s_caption_is_dropped():
    """A figure's caption is said over its frame, and the page's text also held it as a paragraph: the wiki's
    "Overview of the Calvin cycle and carbon fixation" and its "Z scheme" under a heading were refused as uncovered,
    and the C4 caption was a paragraph of its own with words doubled. A paragraph quoting a caption in a sentence stays
    (NIST's body names "CSF Tiers for cybersecurity risk governance")."""

    blocks = ['## **Light**\nThe "Z scheme"',
              "Overview of the Calvin cycle and carbon fixation <https://en.wikipedia.org/wiki/Carbon_fixation>",
              "The Tiers are shown in Fig. 4. CSF Tiers for cybersecurity risk governance and they describe how rigorous "
              "an organization's practices are over many more words of running text.",
              "Overview of C4 fixation. (This image shows lactic acid instead of lactic acid instead of pyruvate.)",
              "Next."]
    figures = [{"caption": 'The "Z scheme"'}, {"caption": "Overview of the Calvin cycle and carbon fixation"},
               {"caption": "Fig. 4. CSF Tiers for cybersecurity risk governance"},
               {"caption": "Overview of C4 fixation. (This image shows lactic acid instead of pyruvate.)"}]
    removed = []
    kept, places = pdf_source_service._drop_caption_blocks(blocks, figures, [0, 1, 2, 3], 6, removed)
    harness.check("the captions go, the heading and the sentence stay",
                  kept == ["## **Light**", blocks[2], "Next."], str(kept))
    harness.check("each figure still stands before the block that followed its caption", places == [0, 1, 1, 2], str(places))
    harness.check("each removal is recorded", len(removed) == 3, str(removed))


def test_a_pdf_figures_open_questions_are_no_open_point_of_the_document_defect():
    # Defect: A document's open points were complaints about its figures ("not labeled on the axis"), from each figure's open questions.
    # Fix: The merge leaves out a PDF figure's open questions (frame_merge_utils, figure_frames).
    from utils import frame_merge_utils

    record = {"index": 4, "timestamp": 4.0, "facts": ["The chart plots absorbance against wavelength."],
              "open_questions": ["The peaks' values are not marked on the axis."]}
    page = {"index": 3, "timestamp": 3.0, "facts": [], "open_questions": ["Which year does the report cover?"]}
    entries = frame_merge_utils._list_frame_entries([page, record], {}, figure_frames=frozenset({4}))
    questions = [entry["text"] for entry in entries if entry["kind"] == "open_question"]
    harness.check("a figure's question about what its picture leaves unlabelled is dropped; another frame's stays",
                  questions == ["Which year does the report cover?"]
                  and any(entry["kind"] == "facts" for entry in entries), str(questions))


def test_a_pdf_figures_diagram_is_left_out_beside_its_facts_and_kept_when_it_is_its_one_note_defect():
    # Defect: A Mermaid copy of a figure stood beside the figure's cut, wrong where the cut was right.
    # Fix: The merge leaves out a PDF figure's diagram when the figure has facts; one that is its only note stays.
    from utils import frame_merge_utils

    flow = "flowchart LR\n  A[\"Gateway\"] --> B[\"Token store\"]"
    with_facts = {"index": 4, "timestamp": 4.0, "facts": ["The gateway sends the token to the store."], "diagram": flow}
    alone = {"index": 5, "timestamp": 5.0, "facts": [], "diagram": flow.replace("Token store", "Audit log")}
    entries = frame_merge_utils._list_frame_entries([with_facts, alone], {}, figure_frames=frozenset({4, 5}))
    diagrams = [entry["frames"] for entry in entries if entry["kind"] == "diagram"]
    harness.check("a figure's diagram beside its facts is left out; a figure whose one note is its diagram keeps it",
                  diagrams == [[5]], str(diagrams))


def test_pointers_into_the_back_matter_go_with_it_and_named_references_and_links_stay_defect():
    # Defect: A footnote marker ("[116]") reached a chapter: phase 1 removed the reference list but kept the markers pointing at it.
    # Fix: Once the back matter is removed, phase 1 removes the numbered markers too; named references, links and code stay.
    from services import pdf_source_service

    def segments():
        return [{"page": 1, "text": "Gateway signs each token.[42][43] The cycle [12] uses ATP [9,10] and [3–5] keys.[note 1]"},
                {"page": 2, "text": "See [RFC9110] and [1](https://acmesync.example/a) for the rules."},
                {"page": 2, "text": "```\nkeys[1] = token\n```"}]

    kept = segments()
    pdf_source_service._drop_note_markers(kept, [])
    harness.check("with no back matter taken out, every marker stays", kept == segments(), str(kept))
    removed = [{"page": 3, "rule": pdf_source_service._BACK_MATTER_RULE, "text": "## References"}]
    dropped = segments()
    pdf_source_service._drop_note_markers(dropped, removed)
    harness.check("numbered pointers go, with the space before them; a named reference, a link and code stay",
                  [segment["text"] for segment in dropped]
                  == ["Gateway signs each token. The cycle uses ATP and keys.",
                      "See [RFC9110] and [1](https://acmesync.example/a) for the rules.", "```\nkeys[1] = token\n```"],
                  str([segment["text"] for segment in dropped]))
    harness.check("each page's markers are one entry of the cleanup", removed[1:] == [
        {"page": 1, "rule": "pointers to the back matter taken out", "text": "[42] [43] [12] [9,10] [3–5] [note 1]"}],
        str(removed[1:]))


def test_a_figure_beside_a_heading_goes_to_the_section_whose_heading_starts_above_its_middle_defect():
    # Defect: A figure under a section's heading on the page was placed in the section before it.
    # Fix: A figure goes after a heading that starts above the figure's middle (pdf_source_service._list_figure_places).
    blocks = ["The gateway keeps one key per partner.", "## Partner onboarding\nEach partner signs its tokens.", "More."]
    lines = [(70.0, "The gateway keeps one key per partner."), (300.0, "Partner onboarding"), (330.0, "Each partner signs")]
    under = [{"caption": "", "box": [60, 340, 500, 600]}]
    drawn_over = [{"caption": "", "box": [60, 290, 500, 800]}]
    cover = [{"caption": "", "box": [60, 150, 500, 290]}]
    places = [pdf_source_service._list_figure_places(figure, blocks, 842, lines)[0] for figure in (under, drawn_over, cover)]
    harness.check("a picture under its heading and one the heading is drawn over go after the heading; a cover above "
                  "its title stays before it", places == [2, 2, 1], str(places))
    harness.check("with no line tops the figure keeps its place",
                  pdf_source_service._list_figure_places(under, blocks, 842) == [1],
                  str(pdf_source_service._list_figure_places(under, blocks, 842)))


def test_a_pdf_figure_s_fact_that_is_its_page_s_sentence_is_dropped_defect():
    # A scan's figure kept two sentences of its page's text as facts through its critique and repair, though a figure's
    # notes hold only what it shows (DEFECTS 524). Now such a fact goes while another remains; a video frame keeps its own
    from services import phase_file_service
    from support import fixtures as fx
    from validators import item_validator

    paths = fx.make_paths()
    page = "A call enters through the edge proxy, which checks the signature. The router then picks the shard."
    record = {"facts": ["A figure shows Client → Gateway → Keyhole.", "A call enters through the edge proxy, which checks "
                        "the signature.", "The router then picks the shard"]}
    results = {}
    for medium in ("pdf-figure", "video"):
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf-text-only", "droppedFrames": [],
                                                       "items": [{"slug": "frame-000", "index": 0, "medium": medium,
                                                                  "transcriptBefore": page}]})
        results[medium] = item_validator.drop_figure_page_facts(paths, "frame-000", record)["facts"]
    harness.check("the figure keeps only what it shows", results["pdf-figure"] == ["A figure shows Client → Gateway → Keyhole."],
                  str(results["pdf-figure"]))
    harness.check("another frame keeps its facts", results["video"] == record["facts"], str(results["video"]))


def test_a_pdf_figures_diagram_is_dropped_before_the_checks_when_it_has_a_fact_defect():
    # Defect: A figure frame blocked phase 3 for a diagram Mermaid could not draw, though that diagram reached no reader; then the fix stored it as (none), which the check refused too.
    # Fix: item_validator.drop_figure_diagram stores such a diagram empty before the checks run.
    from services import phase_file_service
    from support import fixtures as fx
    from validators import item_validator

    paths = fx.make_paths()
    broken = {"facts": ["The gateway sends the token to the store."], "diagram": 'flowchart LR\n  A -->|"e- | B["Store"]'}
    for medium, expected in (("pdf-figure", ""), ("pdf", broken["diagram"])):
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf-text-only", "droppedFrames": [],
                                                       "items": [{"slug": "frame-005", "index": 5, "medium": medium}]})
        kept = item_validator.drop_figure_diagram(paths, "frame-005", broken)
        harness.check(f"a {medium} frame's diagram beside its facts becomes {expected!r}", kept["diagram"] == expected,
                      kept["diagram"])
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf-text-only", "droppedFrames": [],
                                                   "items": [{"slug": "frame-005", "index": 5, "medium": "pdf-figure"}]})
    alone = item_validator.drop_figure_diagram(paths, "frame-005", {"facts": [], "diagram": broken["diagram"]})
    harness.check("a diagram that is a frame's one note stays", alone["diagram"] == broken["diagram"], alone["diagram"])
    from models.validation_report import ValidationReport

    report = ValidationReport("frame-005")
    item_validator._check_diagram(report, "frame-005", item_validator.drop_figure_diagram(paths, "frame-005", broken))
    harness.check("the dropped diagram passes the diagram checks", not report.findings, str(report.findings))


def test_a_pdf_figures_markdown_of_bare_lines_is_stored_empty_and_its_table_kept_defect():
    # Defect: a tier chart's four tier names, written under `## As markdown` as bare lines, reached a chapter as a slide's
    # sentences and were written twice and run together. Fix: a figure's markdown holding no code and no table is stored empty.
    from services import phase_file_service
    from support import fixtures as fx
    from validators import item_validator

    paths = fx.make_paths()
    bare = {"facts": ["Stage 1 holds the drafts."], "as_markdown": "Stage 1: Draft\nStage 2: Review\nStage 3: Live"}
    table = {"facts": ["Stage 1 holds the drafts."], "as_markdown": "| Stage | Name |\n|---|---|\n| 1 | Draft |"}
    for medium, expected in (("pdf-figure", ""), ("pdf", bare["as_markdown"])):
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf-text-only", "droppedFrames": [],
                                                       "items": [{"slug": "frame-005", "index": 5, "medium": medium}]})
        kept = item_validator.drop_figure_markdown(paths, "frame-005", bare)
        harness.check(f"a {medium} frame's bare lines become {expected!r}", kept["as_markdown"] == expected, kept["as_markdown"])
    kept = item_validator.drop_figure_markdown(paths, "frame-005", table)
    harness.check("a figure's table stays", kept["as_markdown"] == table["as_markdown"], kept["as_markdown"])


def test_a_drawn_figure_takes_its_axis_label_and_never_the_paragraph_line_under_it_defect():
    # Defect: a drawing's box was widened to every word within 14 points of its lines, so a paragraph's first line just
    # under a timeline became part of the figure's frame. Fix: a word of a line of 6 words or more is running text, never a label.
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    shape = page.new_shape()
    for step in range(12):
        shape.draw_line((100 + step * 30, 300), (110 + step * 30, 200 + (step % 4) * 20))
    shape.finish(color=(0, 0, 0))
    shape.commit()
    page.insert_text((280, 312), "(years ago)", fontsize=9)
    page.insert_text((60, 322), "is due to the simple body plans and large surface areas of the cells.", fontsize=11)
    figures = pdf_source_service.find_figures(page)
    harness.check("one figure, widened to its axis label and stopping above the paragraph line",
                  len(figures) == 1 and figures[0]["box"][0] == 100 and 310 <= figures[0]["box"][3] < 320, str(figures))


def test_a_caption_whose_italic_name_the_reader_doubled_is_still_dropped_defect():
    # Defect: the reader wrote a caption's italic name with letters run together ("bellismea bellisea bellis"), only 9 of
    # its 12 words matched the caption, and it stayed as a paragraph the writer had to cite. Fix: a word starting with a caption word of 4 letters or more counts as it.
    removed: list[dict] = []
    kept, _places = pdf_source_service._drop_caption_blocks(
        ["Leaf cells with visible stomata (from a fern, _Acmea bellismea bellisea bellis_ )", "The fern grows in shade."],
        [{"caption": "Leaf cells with visible stomata (from a fern, Acmea bellis)"}], [0], 3, removed)
    harness.check("the doubled caption leaves, the paragraph stays", kept == ["The fern grows in shade."], str(kept))
    harness.check("a paragraph merely sharing a word stays", pdf_source_service._is_caption_line(
        "Leaf shapes vary between ferns and mosses in shade.", [pdf_source_service._list_caption_words(
            "Leaf cells with visible stomata (from a fern, Acmea bellis)")]) is False)


def _build_page_break_figure_pdf() -> Path:
    """Two pages: a framed chart of bars starts low on page 1, with white under its bars where the page broke, and goes
    on at the top of page 2, its caption under it; a paragraph above it and one after."""

    document = pymupdf.open()
    for number in range(2):
        page = document.new_page(width=595, height=842)
        shape = page.new_shape()
        top, bottom = (700, 836) if number == 0 else (36, 300)
        # the frame drawn round it is open where the page broke: no line under its first part, none over its second
        shape.draw_polyline([(230, bottom if number == 0 else top), (230, top if number == 0 else bottom),
                             (550, top if number == 0 else bottom), (550, bottom if number == 0 else top)])
        shape.finish(color=(0, 0, 0), width=2, closePath=False)
        bars_bottom = 760 if number == 0 else 280
        for step in range(10):
            shape.draw_rect(pymupdf.Rect(250 + step * 28, top + 10, 270 + step * 28, bars_bottom))
            shape.finish(fill=(0.2, 0.4 + step * 0.05, 0.6))
        shape.commit()
    document[0].insert_text((60, 400), "The gateway signs each token before it leaves.", fontsize=11)
    document[1].insert_text((240, 318), "Figure 3. Tokens signed per hour", fontsize=9)
    document[1].insert_text((60, 400), "After the token is signed, the provider checks it.", fontsize=11)
    path = Path(tempfile.mkdtemp()) / "page-break.pdf"
    document.save(path)

    return path


def test_a_figure_cut_by_a_page_break_is_one_figure_and_one_frame_defect():
    # Defect: a chart running over a page break was found as two figures, two frames, the first with an empty band where
    # the page broke. Fix: a captionless figure at a page's bottom and one at the next page's top in the same columns are one, its frame the two parts stacked.
    pdf = _build_page_break_figure_pdf()
    transcript, _removed = pdf_source_service.build_page_transcript(pdf, is_read_as_pages=False)
    figures = transcript.get("figures", [])
    harness.check("one figure, on the first page, its two parts listed, the caption of the second",
                  len(figures) == 1 and figures[0]["page"] == 1 and [part["page"] for part in figures[0].get("parts", [])] == [1, 2]
                  and figures[0]["caption"] == "Figure 3. Tokens signed per hour", str(figures))
    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    items = pdf_source_service.render_figures(pdf, transcript, frames_dir)
    image = cv2.imread(str(frames_dir / "frame-000.jpg"))
    stacked = pdf_source_service.read_figure_image(pdf, items[0]["parts"])
    harness.check("one frame: the bars of both pages, the white under the first page's bars left out",
                  len(items) == 1 and image is not None and len(items[0]["parts"]) == 2
                  and image.shape[0] < 0.9 * (136 + 264) * image.shape[1] / 320, str((items, None if image is None else image.shape)))
    harness.check("the cut tool reads the same stacked figure at full resolution",
                  stacked is not None and abs(stacked.shape[0] / stacked.shape[1] - image.shape[0] / image.shape[1]) < 0.02,
                  str((None if stacked is None else stacked.shape, image.shape)))


def test_a_pdf_s_instruction_is_never_marked_an_action_defect():
    # A manual's command was written "> **Action:** Use the following syntax ...", which the closing lists as something
    # assigned (DEFECTS 515). Now the PDF lines say a step the document gives its reader is never an action
    from prompts import system_prompt_builder

    text = system_prompt_builder.render_recording_section([], True, is_pdf=True)
    harness.check("an instruction to the reader is never an action", "never as an action" in text
                  and "an incident's follow-up with its owner" in text, text)
    harness.check("a video is told nothing of it", "never as an action" not in
                  system_prompt_builder.render_recording_section(["demo"], True), "")


def test_a_pdf_with_no_figure_is_never_called_an_audio_recording_defect():
    # A text-only PDF with no figure was told "This source is a PDF document" and "it is an audio recording", and its
    # writer weighed the two (DEFECTS 521). Now it is told the document has no figure; an audio recording keeps its line
    from prompts import system_prompt_builder

    pdf = system_prompt_builder.render_recording_section([], False, is_pdf=True)
    audio = system_prompt_builder.render_recording_section([], False)
    harness.check("the PDF is no audio recording", "audio recording" not in pdf and "This document has no figure" in pdf, pdf)
    harness.check("an audio recording still is", "it is an audio recording" in audio, audio)


def test_a_pdf_chapter_s_one_heading_starts_at_its_first_note_by_id_defect():
    # Told to start "at its first note" in a chapter opening on the document's lead, a writer thought 44,527 characters
    # over which note was meant (DEFECTS 520). Now the request names the note by its id
    from prompts import document_messages

    told = document_messages._render_chapter_headings({"documentHeadings": [], "startIds": ["S0001", "S0004"]})
    harness.check("the first note is named by its id", "starting at its first note, S0001." in told, told)


def test_a_pdf_heading_keeps_the_document_s_title_whatever_its_length_defect():
    # A one-word chapter, "Troubleshooting", was to be titled as the chapter and in two to eight words, and the writer
    # thought 6,866 characters about which wins (DEFECTS 510). Now the request says the document's title wins
    from prompts import document_messages

    alone = document_messages._render_chapter_headings({"documentHeadings": []})
    listed = document_messages._render_chapter_headings({"documentHeadings": ["Glossary", "Retries"]})
    harness.check("one heading titled as the chapter, whatever its length", "titled as the chapter is, whatever its length"
                  in alone, alone)
    harness.check("the document's headings kept whatever their length", "titled as the document titles it whatever its length"
                  in listed, listed)


@functools.lru_cache(maxsize=1)
def _build_boxed_text_pdf() -> Path:
    """The PDF of tables of rounded cells, a placeholder and a chart (tests/resources/pdf/build_boxed_text_pdf.py)."""

    spec = importlib.util.spec_from_file_location(
        "build_boxed_text_pdf", Path(__file__).resolve().parents[1] / "resources" / "pdf" / "build_boxed_text_pdf.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    return builder.build(Path(tempfile.mkdtemp()) / "boxed-text.pdf")


@functools.lru_cache(maxsize=1)
def _find_boxed_text_pdf_figures() -> tuple[tuple[tuple[float, ...], ...], ...]:
    """Each page's figure boxes in the made PDF."""

    with pymupdf.open(_build_boxed_text_pdf()) as document:
        found = [pdf_source_service.find_figures(page) for page in document]

    return tuple(tuple(tuple(figure["box"]) for figure in figures) for figures in found)


def test_a_table_of_rounded_cells_is_no_figure_defect():
    # A wiki export's tables, their cells rounded boxes the table finder missed, were 695 of one PDF's 1245 figures and
    # reached phase 3 as frames (DEFECTS 667). Now a drawing of boxes holding its words is text, never a figure
    found = _find_boxed_text_pdf_figures()
    harness.check("the table of rounded cells with a tick in one is no figure", found[0] == (), str(found[0]))
    harness.check("a bar chart, words under its bars, is still a figure",
                  found[3] == ((100.0, 200.0, 500.0, 416.7),), str(found[3]))


def test_a_grey_file_placeholder_is_no_figure_defect():
    # A wiki's grey file placeholder, a flat field with one small icon, was a figure, and so was its rounded frame once
    # the picture was set aside (DEFECTS 667). Now a blank picture and the frame round it are none
    found = _find_boxed_text_pdf_figures()
    harness.check("the placeholder and its frame leave, the screenshot under them stays",
                  found[1] == ((70.0, 360.0, 400.0, 560.0),), str(found[1]))


def _build_headed_pdf_run(segments: list[dict]) -> types.SimpleNamespace:
    """A run folder of a PDF read as text only whose phase 1 wrote `segments`, and the context phase 4's steps read."""

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="wiki.pdf")
    phase_file_service.write_phase_file(paths, 1, {"segments": segments})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": output_contract.PDF_TEXT_ONLY_MEDIUM,
                                                   "items": []})

    return types.SimpleNamespace(paths=paths, log=logging.getLogger("test"))


def test_a_pdf_s_agenda_and_chapter_headings_are_its_own_headings_with_no_model_call():
    """The owner: phase 4's first two steps were handed every paragraph of a 1,493-page wiki export, three times the
    model's window, and each chapter was read twice; "Ok apply" to the agenda and the chapters' headings taken from the
    PDF's own headings."""

    texts = ["Cover text before any heading.", "# Ordering", "The order form needs two fields.", "## The form",
             "It asks for the customer number.", "### A detail", "The date is optional.", "## Shipping",
             "Orders ship from March.", "# Returns", "A return needs its order number."]
    segments = [{"text": text, "start": float(number * 10), "page": 1} for number, text in enumerate(texts)]
    ctx = _build_headed_pdf_run(segments)
    harness.check("a text-only PDF with two top-level headings is planned by them",
                  write_document_node._is_planned_by_document_headings(ctx))
    moments = [{"number": number + 1, "start": segment["start"]} for number, segment in enumerate(segments)]
    parts, announced, title = write_document_node._build_parts_from_headings(ctx, moments)
    harness.check("one chapter a top-level heading, the first opening at the first moment, no announced agenda",
                  [(part["title"], part["firstMoment"]) for part in parts] == [("Ordering", 1), ("Returns", 10)]
                  and announced == [] and title == "Ordering", str(parts))
    entries = [{"id": f"S{number + 1:04d}", "timestamps": [segment["start"]]} for number, segment in enumerate(segments)]
    drafts = write_document_node._build_draft_headings_from_document(ctx, entries, parts)
    harness.check("each chapter's headings are its next level only, each starting at its own paragraph",
                  [[(heading["title"], heading["first_entry_id"]) for heading in draft["headings"]] for draft in drafts]
                  == [[("The form", "S0004"), ("Shipping", "S0008")], []], str(drafts))
    one_level = _build_headed_pdf_run([{"text": "# Only one", "start": 0.0, "page": 1}])
    harness.check("one top-level heading is no plan: the model's steps stay",
                  not write_document_node._is_planned_by_document_headings(one_level))


def _find_boxed_text_pdf_kinds() -> list[list[dict]]:
    with pymupdf.open(_build_boxed_text_pdf()) as document:
        return [pdf_source_service.find_figures(page) for page in document]


def test_a_pdf_of_many_figures_has_them_placed_unread_and_the_mode_chooses_which_are_read():
    """The owner: above a number of figures, place them where they stood unread (the default); or read only the
    drawings; or read all, as before. 532 figures of one wiki export were about 17 hours of
    phase 3."""

    figures = [{"slug": "frame-000", "kind": "picture"}, {"slug": "frame-001", "kind": "drawing"},
               {"slug": "frame-002", "kind": ""}]
    read, placed = pdf_source_service.split_figures_to_read(items=figures, figure_reading="place-above-limit", read_limit=2)
    harness.check("more figures than the limit: every one placed, none read", (read, placed) == ([], figures),
                  str((read, placed)))
    read, placed = pdf_source_service.split_figures_to_read(items=figures, figure_reading="place-above-limit", read_limit=3)
    harness.check("as many as the limit: every one read", (read, placed) == (figures, []), str((read, placed)))
    read, placed = pdf_source_service.split_figures_to_read(items=figures, figure_reading="read-drawings-only", read_limit=100)
    harness.check("drawings only: the chart read, the picture and the figure of no kind placed",
                  [item["slug"] for item in read] == ["frame-001"]
                  and [item["slug"] for item in placed] == ["frame-000", "frame-002"], str((read, placed)))
    read, placed = pdf_source_service.split_figures_to_read(items=figures, figure_reading="read-all", read_limit=1)
    harness.check("read all: every one read", (read, placed) == (figures, []), str((read, placed)))
    kinds = [[figure["kind"] for figure in page] for page in _find_boxed_text_pdf_kinds()]
    harness.check("phase 1 names each figure a picture or a drawing", kinds == [[], ["picture"], ["picture"], ["drawing"]],
                  str(kinds))


def test_a_table_holding_a_small_screenshot_gives_the_screenshot_alone_defect():
    # A table holding one small screenshot was taken whole as the screenshot's frame, its cells and words with it
    # (DEFECTS 667). Now only a picture filling half its frame or more is widened to it
    found = _find_boxed_text_pdf_figures()
    harness.check("the screenshot alone", found[2] == ((225.0, 205.0, 365.0, 275.0),), str(found[2]))


def test_a_pdf_s_markdown_conversion_places_each_figure_before_the_paragraph_that_followed_it():
    paragraphs = [{"first": "S0001", "last": "S0001", "text": "## Overview"},
                  {"first": "S0002", "last": "S0004", "text": "The gateway forwards refunds."},
                  {"first": "S0005", "last": "S0005", "text": "Retries are capped."}]
    figures = [{"slug": "frame-002", "path": "frames/frame-002.jpg", "page": 2, "caption": "", "placeBefore": ""},
               {"slug": "frame-001", "path": "frames/frame-001.jpg", "page": 1, "caption": "The flow",
                "placeBefore": "S0003"}]
    markdown, placed = pdf_conversion_service.render_converted_document(paragraphs, figures)
    harness.check("a figure before the paragraph holding the line after it",
                  markdown.index("The flow") < markdown.index("The gateway forwards"), markdown)
    harness.check("a figure with no line after it goes last", markdown.rstrip().endswith("*Figure 2 (page 2)*"),
                  markdown)
    harness.check("numbered in the order they stand", [figure["frame"] for figure in placed] == ["frame-001", "frame-002"])
    titles = run_status_service.describe_phase(4, True, output_contract.PDF_MODE_MARKDOWN_CONVERSION)
    harness.check("the page names phase 4 by what it does", titles["title"] == "markdown conversion"
                  and titles["is_model_used"] is False, str(titles))
