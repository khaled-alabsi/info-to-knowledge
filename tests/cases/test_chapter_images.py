"""A chapter places its own frames (the owner, 2026-09-28): phase 4's chapter follow-up gets phase 5's look, cut,
mark and mask tools, a section lists its images, the document carries them as a frame line and a caption line, and
phase 5 adopts them as placed images."""

from support import fixtures as fx
from support import harness

from models.illustration_context import IllustrationContext
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from schemas import write_actions
from services import document_file_service
from services import illustration_action_service
from services import write_reading_service
from utils import document_block_utils
from validators import document_validator


# A figure's description as a section writes it under the frame
FIGURE_DESCRIPTION = ('> **What the figure shows:** the refund form on white, the heading "Refund request" above the '
                      'fields "Order number", "Amount" and "Parts", "Parts" showing "3", and the button "Send" at the '
                      'bottom right.')
# A frame line with its caption, as a section places it
IMAGE_LINE = '![The refund form](cut-01 "The form caps a refund at three parts.")\n'


def test_phase_5_adopts_a_frame_phase_4_placed_with_the_entries_of_its_paragraph():
    blocks = [{"id": "B001", "kind": "heading", "level": 2, "text": "Refunds", "section": "Refunds", "sectionId": "B001",
               "entryIds": []},
              {"id": "B002", "kind": "paragraph", "text": "A refund splits in three.", "section": "Refunds",
               "sectionId": "B001", "entryIds": ["F12.1"]},
              {"id": "B003", "kind": "paragraph", "text": "![The refund form](04_writing/images/cuts/cut-02.png)",
               "section": "Refunds", "sectionId": "B001", "entryIds": []},
              {"id": "B004", "kind": "paragraph", "text": "*The form caps a refund at three parts.*", "section": "Refunds",
               "sectionId": "B001", "entryIds": []},
              {"id": "B005", "kind": "paragraph", "text": FIGURE_DESCRIPTION, "section": "Refunds",
               "sectionId": "B001", "entryIds": []}]
    adopted, converted_count = document_block_utils.convert_placed_images(blocks, {"cut-02": {"frame": "frame-012",
                                                                                   "file": "04_writing/images/cuts/cut-02.png"}})
    image = adopted[-1]
    harness.check("one frame adopted, the frame and caption lines replaced by one image block",
                  converted_count == 1 and len(adopted) == 3 and image["kind"] == "image", str(adopted))
    harness.check("the description travels with it, so phase 5 keeps what a reader who cannot see it reads",
                  image["description"].startswith("the refund form on white"), str(image.get("description")))
    harness.check("it keeps its cut, frame, caption and alt text, and cites the paragraph it follows",
                  image["cut"] == "cut-02" and image["frame"] == "frame-012"
                  and image["caption"] == "The form caps a refund at three parts." and image["alt"] == "The refund form"
                  and image["entryIds"] == ["F12.1"], str(image))
    kept, converted_count = document_block_utils.convert_placed_images(blocks, {})
    harness.check("a frame whose cut is unknown stays as it is", converted_count == 0 and len(kept) == 5)


def test_a_section_image_must_be_a_cut_that_exists_with_a_caption():
    paths = fx.make_paths()
    cut = paths.output_root / "04_writing" / "images" / "cuts" / "cut-01.png"
    cut.parent.mkdir(parents=True, exist_ok=True)
    cut.write_bytes(b"\x89PNG not a real frame")
    document_file_service.write_image_store(paths, {"cuts": {"cut-01": {"file": "04_writing/images/cuts/cut-01.png",
                                                                        "frame": "frame-012"}}})
    text = "A refund splits in three. [F12.1]\n\n"
    document_file_service.write_section(paths, "heading-001-1", text + IMAGE_LINE + FIGURE_DESCRIPTION + '\n[F12.1]\n')
    harness.check("a cut that exists, placed where the text needs it, with a caption, passes",
                  document_validator.validate_section_images(paths, "heading-001-1").ok)
    harness.check("the section's record gives the frame its cut's file and frame",
                  document_file_service.read_section(paths, "heading-001-1")["images"][0]["cutFile"]
                  == "04_writing/images/cuts/cut-01.png")
    document_file_service.write_section(paths, "heading-001-1", text + '![The refund form](cut-09 "")\n'
                                        + FIGURE_DESCRIPTION + '\n[F12.1]\n')
    report = document_validator.validate_section_images(paths, "heading-001-1")
    harness.check("a cut never made and an empty caption are named",
                  harness.codes(report) == {"DOCUMENT_IMAGE_INVALID"} and "no cut" in report.findings[0].detail
                  and "caption" in report.findings[0].detail, report.render_summary())
    document_file_service.write_section(paths, "heading-001-1", text + IMAGE_LINE + '[F12.1]\n')
    undescribed = document_validator.validate_section_images(paths, "heading-001-1")
    harness.check("a figure nobody described is refused, so a reader who cannot see it is never left with nothing",
                  harness.codes(undescribed) == {"DOCUMENT_IMAGE_NOT_DESCRIBED"}
                  and "described in 0 word(s)" in undescribed.findings[0].detail, undescribed.render_summary())


def test_the_frame_tools_spend_no_read():
    context = WriteReadingContext(paths=fx.make_paths(), moments=[], point_starts=[], frame_items={}, frame_records={},
                                  read_chars=4000, notes_chars=120, read_budget=0)
    state = WriteReadingState()
    result = write_reading_service.run_read(context, state, "look", {"frame": "frame-000"})
    harness.check("with no recording known, the tool says so, and no read is spent although the budget is 0",
                  "cannot be cut" in result.text and state.reads_used == 0, result.text)


def test_look_and_cut_return_the_label_phase_3_gave_the_frame():
    records = {0: {"index": 0, "slug": "frame-000", "screenshots": [
                   {"part": "the settings form", "why": "a reader must find the field", "importance": "needed"}]},
               1: {"index": 1, "slug": "frame-001", "screenshot": "helps", "screenshot_why": "the list is dense",
                   "screenshot_part": "the service list"},
               2: {"index": 2, "slug": "frame-002"}}
    context = WriteReadingContext(paths=fx.make_paths(), moments=[], point_starts=[], frame_items={}, frame_records=records,
                                  read_chars=4000, notes_chars=120, read_budget=5)
    needed = write_reading_service.describe_suggestion(context, "frame-000")
    helps = write_reading_service.describe_suggestion(context, "frame-001")
    harness.check("a `needed` frame says needed, with its reason and its part",
                  "frame-000 `needed`: a reader must find the field (the settings form)" in needed, needed)
    harness.check("a `helps` frame says helps, also from the older record shape",
                  "frame-001 `helps`: the list is dense (the service list)" in helps, helps)
    harness.check("a frame with no suggestion says so", "suggested no screenshot of frame-002" in
                  write_reading_service.describe_suggestion(context, "frame-002"))


def test_a_needed_frame_named_on_a_set_aside_line_is_told_that_the_line_records_nothing_defect():
    # A writer wrote "> **Set aside:** The table is written as markdown. [frame-000]" for a needed frame it had written as a
    # block, five drafts of one run's chapters, and the finding only said to call `leave_out`. It names the frame now
    paths = fx.make_paths()
    slug = "heading-001-1"
    document_file_service.write_image_store(paths, illustration_action_service.build_empty_store([]))
    document_file_service.write_section(
        paths, slug, "A proxy forwards the token. [F1.1]\n\n> **Set aside:** The table is written as a markdown block. [frame-000]\n")
    named = " ".join(f"{finding.detail} {finding.fix}" for finding in document_validator.validate_needed_screenshots(
        paths, slug, ["frame-000"], "chapter-01-text").findings)
    harness.check("the finding says the named frame needs the leave_out tool and the line goes",
                  "frame-000 is named in your text, which records nothing: call the `leave_out` tool" in named, named)
    document_file_service.write_section(paths, slug, "A proxy forwards the token. [F1.1]\n")
    plain = " ".join(f"{finding.detail} {finding.fix}" for finding in document_validator.validate_needed_screenshots(
        paths, slug, ["frame-000"], "chapter-01-text").findings)
    harness.check("a frame named nowhere in the text gets no such sentence", "is named in your text" not in plain, plain)


def test_phase_4_records_a_needed_frame_replaced_by_a_complete_diagram():
    paths = fx.make_paths()
    illustration = IllustrationContext(
        paths=paths, video_path=paths.repository / paths.module / "fixture.mp4",
        frames={"frame-000": {"slug": "frame-000"}},
        min_cut_pixels=50, chapter="chapter-01-text")
    context = WriteReadingContext(
        paths=paths, moments=[], point_starts=[], frame_items={}, frame_records={}, read_chars=4000, notes_chars=120,
        read_budget=0, illustration=illustration)
    image_store = illustration_action_service.build_empty_store([])
    image_store["looks"]["frame-000"] = {"width": 800, "height": 600, "boxes": {}}
    state = WriteReadingState(image_store=image_store)
    result = write_reading_service.run_read(
        context, state, "replace_with_diagram",
        {"frame": "frame-000", "reason": "The complete diagram preserves every boundary and labelled relationship."})
    harness.check("the replacement is accepted and explained to the worker",
                  result.text.endswith("recorded as replaced by a complete diagram."), result.text)
    harness.check("the replacement survives in the image store with its chapter",
                  state.image_store["diagramReplacements"] == [{
                      "frame": "frame-000", "reason": "The complete diagram preserves every boundary and labelled relationship.",
                      "chapter": "chapter-01-text"}], str(state.image_store))
    old_store = illustration_action_service.build_empty_store([])
    old_store.pop("diagramReplacements")
    old_store["looks"]["frame-000"] = {"width": 800, "height": 600, "boxes": {}}
    old_state = WriteReadingState(image_store=old_store)
    old_result = write_reading_service.run_read(
        context, old_state, "replace_with_diagram",
        {"frame": "frame-000", "reason": "The complete diagram preserves every boundary and labelled relationship."})
    harness.check("a run resumed from the older image-store shape can record the replacement",
                  "recorded as replaced" in old_result.text and len(old_state.image_store["diagramReplacements"]) == 1,
                  old_result.text)


def test_a_needed_frame_must_be_placed_or_replaced_by_a_mermaid_diagram():
    paths = fx.make_paths()
    slug = "heading-001-1"
    chapter = "chapter-01-text"

    # STEP 1: a needed frame neither placed nor replaced
    document_file_service.write_section(paths, slug, "A proxy forwards the token. [F1.1]\n")
    document_file_service.write_image_store(paths, illustration_action_service.build_empty_store([]))
    missing = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("an unaccounted needed frame rejects the chapter",
                  harness.codes(missing) == {"DOCUMENT_NEEDED_FRAME_UNACCOUNTED"}, missing.render_summary())

    # STEP 2: recorded as replaced, with no diagram; then a diagram under a later heading, from its notes or not
    store = illustration_action_service.build_empty_store([])
    store["diagramReplacements"] = [{"frame": "frame-000", "reason": "All boundaries are preserved.",
                                      "chapter": chapter}]
    document_file_service.write_image_store(paths, store)
    no_diagram = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("a diagram replacement without a Mermaid block is rejected",
                  harness.codes(no_diagram) == {"DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID"},
                  no_diagram.render_summary())
    document_file_service.write_section(
        paths, "heading-002-1", "The gateway checks it. [F0.2]\n\n```mermaid\nflowchart LR\nA --> B\n```\n[F0.1]\n")
    later = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("the replacing diagram drawn under a later heading, from the frame's own note, counts",
                  "DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID" not in harness.codes(later), later.render_summary())
    document_file_service.write_section(
        paths, "heading-002-1", "The gateway checks it. [F0.2]\n\n```mermaid\nflowchart LR\nA --> B\n```\n[F7.1]\n")
    other = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("a diagram of another frame's notes does not",
                  harness.codes(other) == {"DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID"}, other.render_summary())
    document_file_service.build_section_path(paths, "heading-002-1").unlink()

    # STEP 3: the diagram under this heading
    document_file_service.write_section(
        paths, slug, "A proxy forwards the token. [F1.1]\n\n```mermaid\nflowchart LR\nA --> B\n```\n[F1.1]\n")
    replaced = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("an explicit complete-diagram replacement with Mermaid passes", replaced.ok, replaced.render_summary())

    # STEP 4: the frame's cut placed
    store = illustration_action_service.build_empty_store([])
    store["cuts"]["cut-01"] = {"frame": "frame-000", "file": "04_writing/images/cuts/cut-01.png"}
    document_file_service.write_image_store(paths, store)
    document_file_service.write_section(
        paths, slug, "A proxy forwards the token. [F1.1]\n\n![The token flow](cut-01 \"The proxy forwards it.\")\n[F1.1]\n")
    placed = document_validator.validate_needed_screenshots(paths, slug, ["frame-000"], chapter)
    harness.check("a placed needed screenshot passes", placed.ok, placed.render_summary())


def test_a_needed_frame_placed_under_another_heading_is_accounted_for():
    """A frame whose notes span two headings is shown once; asking for it again under the second heading pushed the
    worker towards the same figure twice."""

    paths = fx.make_paths()
    store = illustration_action_service.build_empty_store([])
    store["cuts"]["cut-001"] = {"frame": "frame-000", "file": "cuts/cut-001.png"}
    document_file_service.write_image_store(paths, store)
    document_file_service.write_section(paths, "heading-001-1", 'A proxy forwards the token. [F1.1]\n\n'
                                                                '![The proxy](cut-001 "The proxy and its token")\n[F1.1]\n')
    document_file_service.write_section(paths, "heading-002-1", "The token expires after an hour. [F1.2]\n")
    second = document_validator.validate_needed_screenshots(paths, "heading-002-1", ["frame-000"], "chapter-01-text")
    harness.check("the frame placed under the first heading counts for the second", harness.codes(second) == set(),
                  second.render_summary())


def test_every_frame_tool_the_writer_is_offered_reaches_the_frame_tools():
    """`undo_cut` was offered to the writer and answered "There is no tool called undo_cut": the reading service's
    list of frame tools left it out, so the call went to the readers."""

    # `read_document` is a reader in phase 4 (the frame code carries it out for phase 5's edits only)
    offered = [name for name in write_actions.READ_TOOLS
               if name in illustration_action_service.RUNNERS and name != "read_document"]
    missing = [name for name in offered if name not in write_actions.FRAME_TOOLS]
    harness.check("every offered frame tool is routed to the frame tools", missing == [], str(missing))


def test_a_frame_after_an_open_line_cites_the_last_paragraph_with_entries():
    """Four figures of one published document stood after an `> **Open:**` line, which cites no entries in the
    document's blocks; each took that line's empty list and the publication was refused (2026-10-03)."""

    blocks = [{"id": "B001", "kind": "heading", "level": 2, "text": "Refunds", "section": "Refunds", "sectionId": "B001",
               "entryIds": []},
              {"id": "B002", "kind": "paragraph", "text": "A refund splits in three.", "section": "Refunds",
               "sectionId": "B001", "entryIds": ["F12.1"]},
              {"id": "B003", "kind": "paragraph", "text": "> **Open:** Who approves a fourth part?", "section": "Refunds",
               "sectionId": "B001", "entryIds": []},
              {"id": "B004", "kind": "paragraph", "text": "![The refund form](04_writing/images/cuts/cut-02.png)",
               "section": "Refunds", "sectionId": "B001", "entryIds": []},
              {"id": "B005", "kind": "paragraph", "text": "*The form caps a refund at three parts.*", "section": "Refunds",
               "sectionId": "B001", "entryIds": []}]
    adopted, _ = document_block_utils.convert_placed_images(blocks, {"cut-02": {"frame": "frame-012",
                                                                               "file": "04_writing/images/cuts/cut-02.png"}})
    harness.check("the figure cites the paragraph before the open line", adopted[-1]["entryIds"] == ["F12.1"], str(adopted[-1]))


def test_a_heading_whose_section_was_never_written_is_judged_not_raised():
    paths = fx.make_paths()
    store = illustration_action_service.build_empty_store([])
    store["diagramReplacements"] = [{"frame": "frame-000", "reason": "All boundaries are preserved.",
                                      "chapter": "chapter-01-text"}]
    document_file_service.write_image_store(paths, store)
    try:
        report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-000"], "chapter-01-text")
        codes = harness.codes(report)
    except FileNotFoundError as error:
        codes = {f"raised: {error}"}
    harness.check("a missing section is a finding, never a crash",
                  codes == {"DOCUMENT_NEEDED_FRAME_REPLACEMENT_INVALID"}, str(codes))
