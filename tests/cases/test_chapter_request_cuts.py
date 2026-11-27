"""A chapter asked again sees the cuts it already made, so it places one instead of cutting the same frame again.

Measured 2026-09-29 on a real run: a chapter asked on three resumes cut its architecture frame three times, and one frame
five times on one resume, each cut a new file.
"""

import json
from types import SimpleNamespace

from support import fixtures as fx
from support import harness

import config
from prompts import document_messages, system_prompt_builder
from utils import section_markdown_utils
from models.section_judging_context import SectionJudgingContext
from services import call_log_service, document_file_service
from services import figure_match_service
from validators import document_validator


def test_the_cuts_already_made_from_a_heading_s_frames_are_listed_by_id():
    section = {"heading": "Refund flow", "topicId": "T01.1", "entries": [], "suggestions": ["frame-002 needed: the flow"],
               "existingCuts": ["cut-004 from frame-002", "cut-007 from frame-002"]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("each earlier cut of the heading's frames is named, to be placed by its id",
                  "cut-004 from frame-002" in text and "cut-007 from frame-002" in text, text[-400:])


def test_a_screenshot_s_reason_and_a_take_away_are_named_judgements_not_facts_defect():
    # A frame's reason ("the values the CLI reads from the environment") and a take-away's guess were written into a
    # chapter as a fact the page never states (DEFECTS 514). Now the request and the notes say both may guess
    from utils import meeting_notes_utils

    section = {"heading": "Signing in", "topicId": "T02.1", "entries": [],
               "suggestions": ["frame-000 needed: shows the values the CLI reads from the environment"]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 2, "chapterCount": 2, "chapterTitle": "Signing in",
                                      "documentTitle": "Manual"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the suggestion's reason is named a judgement, never a fact to write",
                  "(a judgement of why it helps, never a fact to write): frame-000 needed" in text, text[-400:])
    frame = {"slug": "frame-000", "frame": 0, "index": 0, "timestamp": 0.0, "path": "frames/frame-000.jpg",
             "takeAways": ["the values feed the CLI"]}
    lines = [line for line in meeting_notes_utils._render_frame(frame, [], [], [], True) if line.startswith("Take aways")]
    harness.check("the take-aways are named a reading that may guess", lines == [
        "Take aways (phase 3's reading, which may guess; write as fact only what the notes' facts or the source state):"],
                  str(lines))


def test_a_note_repeated_on_later_frames_brings_only_its_own_frame_s_screenshots():
    """A note whose text repeats on later frames (a web address left on screen) is one merged entry listing every such
    frame; its heading is the one of its first frame. The later frames' screenshots belong to their own headings
    (2026-09-29: four frames of other headings were demanded under one heading through a repeated web address)."""

    from graph.nodes import write_document_node
    entries = {"F22.5": {"id": "F22.5", "kind": "url", "frames": [22, 26, 29]}}
    needed = {"screenshots": [{"part": "the table", "importance": "needed", "why": "its rows"}]}
    records = {22: {"slug": "frame-022", "screenshots": []}, 26: {"slug": "frame-026", **needed},
               29: {"slug": "frame-029", **needed}}
    harness.check("no frame after the note's own is demanded under its heading",
                  write_document_node._list_needed_screenshots(["F22.5"], entries, records) == [],
                  str(write_document_node._list_needed_screenshots(["F22.5"], entries, records)))
    harness.check("nor suggested", write_document_node._list_screenshot_suggestions(["F22.5"], entries, records) == [])


def test_a_large_chapter_is_asked_in_parts_of_whole_headings():
    """One answer covered about 60 notes well and left 30 to 90 of 239 uncovered on five tries (2026-09-29): a chapter
    past the limit is asked in parts of whole headings, one answer each; a heading past it alone is a part of its own."""

    from graph.nodes import write_document_node

    def section(topic, count):
        return {"topicId": topic, "entryIds": [f"F{topic}.{number}" for number in range(count)]}

    sections = [section("T1", 30), section("T2", 30), section("T3", 25), section("T4", 90), section("T5", 10)]
    parts = write_document_node._group_sections(sections, 70)
    harness.check("whole headings, in order, each part within the limit unless one heading alone passes it",
                  [[item["topicId"] for item in part] for part in parts] == [["T1", "T2"], ["T3"], ["T4"], ["T5"]],
                  str([[item["topicId"] for item in part] for part in parts]))
    harness.check("a chapter within the limit is one part", len(write_document_node._group_sections(sections[:2], 70)) == 1)


def test_a_chapter_part_says_which_part_it_is():
    section = {"heading": "Refund flow", "topicId": "T01.1", "entries": []}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 3, "chapterCount": 11, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds", "part": 1, "partCount": 3}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("a part names itself, counts its own headings and says all of them are written (2026-10-02: told "
                  "\"part 1 of 3\" over three headings, a worker wrote two and read the part as one heading)",
                  "request 1 of 3 for this chapter" in text and "holds 1 heading" in text and "write all of it" in text,
                  text[:400])
    harness.check("a part whose headings all begin here says nothing of a heading continuing (2026-09-30: told so in a "
                  "part that began new headings, a worker stopped twice to find the heading that continued)",
                  "continues here" not in text, text[:600])
    continuing = SimpleNamespace(inputs={**request.inputs, "part": 2, "sections": [{**section, "slug": "heading-001-2"}],
                                         "earlierSlugs": ["heading-001-1"]},
                                 findings=[])
    harness.check("a part holding the later piece of a heading says it continues here with only its listed notes "
                  "(2026-09-30: a worker spent 11,588 characters of thinking deciding whether a repeated heading was new)",
                  "continues here" in document_messages.build_chapter_text(continuing))
    whole = document_messages.build_chapter_text(SimpleNamespace(inputs={**request.inputs, "part": 1, "partCount": 1}, findings=[]))
    harness.check("a whole chapter says nothing of parts", "request 1 of" not in whole)


def test_a_chapter_part_asks_for_its_own_headings_not_the_whole_chapter_defect():
    # A chapter asked in parts said "the whole chapter as one text" beside "write it with only the notes listed"; the
    # writer weighed the two. Now a part asks for this request's headings, a whole chapter still for the chapter.
    section = {"heading": "Refund flow", "topicId": "T01.1", "entries": [], "slug": "heading-001-2"}
    inputs = {"sections": [section], "chapter": 3, "chapterCount": 11, "chapterTitle": "Refunds",
              "documentTitle": "Refunds", "part": 2, "partCount": 3, "earlierSlugs": ["heading-001-1"]}
    part_text = document_messages.build_chapter_text(SimpleNamespace(inputs=inputs, findings=[]))
    whole_text = document_messages.build_chapter_text(
        SimpleNamespace(inputs={**inputs, "part": 1, "partCount": 1, "earlierSlugs": []}, findings=[]))
    harness.check("a part asks for its own headings, never the whole chapter",
                  "this request's headings as one text" in part_text and "the whole chapter" not in part_text,
                  part_text[-1200:])
    harness.check("a whole chapter is still asked as the whole chapter", "the whole chapter as one text" in whole_text)


def test_a_pdf_s_chapter_request_names_page_text_and_the_document_s_naming_defect():
    # A PDF's chapter request listed its paragraphs as [speech] and sent the meeting's naming rule; the writer called a
    # table's lead-in a speech line and weighed the rule against an author the report thanks. Now: page text, its own rule.
    entry = {"id": "S0165", "kind": "speech", "text": "Acknowledgement is made to Jonas Acme for his guidance."}
    section = {"heading": "History", "topicId": "T08.1", "entries": [entry]}
    inputs = {"sections": [section], "chapter": 8, "chapterCount": 14, "chapterTitle": "History",
              "documentTitle": "Gateway report"}
    pdf_text = document_messages.build_chapter_text(SimpleNamespace(inputs={**inputs, "isPdf": True}, findings=[]))
    video_text = document_messages.build_chapter_text(SimpleNamespace(inputs=inputs, findings=[]))
    harness.check("a PDF's paragraph is listed as page text, never as speech",
                  "S0165 [page text]" in pdf_text and "[speech]" not in pdf_text and "speech lines" not in pdf_text,
                  pdf_text[:600])
    harness.check("a PDF's request sends the document's naming rule, not the meeting's",
                  document_messages.PDF_NAMING_RULE in pdf_text and "speaks in the meeting" not in pdf_text)
    harness.check("a video's request keeps its speech and the meeting's naming rule",
                  "S0165 [speech]" in video_text and document_messages.MEETING_NAMING_RULE in video_text)


def test_a_heading_past_the_limit_is_asked_in_its_parts():
    from graph.nodes import write_document_node
    sections = [{"topicId": "T1", "entryIds": [f"F1.{n}" for n in range(52)]},
                {"topicId": "T1", "entryIds": [f"F2.{n}" for n in range(51)]},
                {"topicId": "T2", "entryIds": [f"F3.{n}" for n in range(10)]}]
    parts = write_document_node._group_sections(sections, 70)
    harness.check("each part of a heading past the limit is a request of its own; a small heading joins the next part",
                  [[len(section["entryIds"]) for section in part] for part in parts] == [[52], [51, 10]],
                  str([[len(section["entryIds"]) for section in part] for part in parts]))


def test_a_frame_phase_3_says_shows_a_secret_is_placed_only_masked():
    """A client secret small enough that the text reader cannot read it was placed unmasked (2026-09-29): phase 3's own
    note on the frame says it shows a secret, and a cut of that frame with no mask is refused."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-090", "index": 90, "timestamp": 1.0, "path": "frames/frame-090.jpg", "record": {
        "screenshots": [{"part": "the new secret row", "importance": "needed", "why": "it shows the generated secret value"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-040.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    store = {"looks": {}, "cuts": {"cut-040": {"frame": "frame-090", "file": "04_writing/images/cuts/cut-040.png", "masks": 0}}}
    document_file_service.write_image_store(paths, store)
    document_file_service.write_section(paths, "heading-021-1", 'The secret is created. [F90.1]\n\n![The secret](cut-040 "The new secret")\n[F90.1]\n')
    report = document_validator.validate_section_images(paths, "heading-021-1")
    codes = harness.codes(report)
    harness.check("unmasked: refused", "DOCUMENT_IMAGE_SECRET_UNMASKED" in codes, str(codes))
    store["cuts"]["cut-040"]["boxTexts"] = {"C1": "Description Expires Value"}
    document_file_service.write_image_store(paths, store)
    codes = harness.codes(document_validator.validate_section_images(paths, "heading-021-1"))
    harness.check("the text reader finding no value on the cut is no proof there is none: still refused (2026-09-30: it "
                  "read no value on a cut whose secret was plainly readable)", "DOCUMENT_IMAGE_SECRET_UNMASKED" in codes, str(codes))
    store["cuts"]["cut-040"].pop("boxTexts")
    fix = next(finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_SECRET_UNMASKED")
    harness.check("the fix says to cut the frame again when earlier masks left no word of the value", "cut frame-090 again" in fix, fix)
    harness.check("the fix says what to mask when the screen shows the value only as asterisks, so a frame with no "
                  "readable value can still be placed", "asterisks" in fix, fix)
    for label, masked_texts, is_refused in (
            ("a mask whose words were never recorded: refused", None, True),
            ("a mask over a sentence of ordinary words only: refused", ["A secret string the application uses"], True),
            ("a mask over a value: accepted", ["xQ9~fake_value_7"], False),
            ("a mask over no word the reader could read proves nothing: refused", [""], True),
            ("a mask over dark text the reader could not read: accepted", [("", True)], False),
            ("a mask over blank space or an earlier mask: refused", [("", False)], True),
            ("a mask over tab labels with counts proves nothing: refused", ["Certificates (0) Client secrets (3)"], True),
            ("a mask over a value the screen shows as asterisks: accepted", ["X2-****************"], False),
            ("one mask on the value beside one on a sentence: accepted", ["Client secrets are listed here", "Sample_K7Q 3/2/2030"], False)):
        store["cuts"]["cut-040"] = {**store["cuts"]["cut-040"], "masks": len(masked_texts or [1])}
        store["cuts"]["cut-040"].pop("maskedAreas", None)
        if masked_texts is not None:
            store["cuts"]["cut-040"]["maskedAreas"] = [
                {"rect": [0, 0, 10, 10], "words": text[0], "coveredText": text[1]} if isinstance(text, tuple)
                else {"rect": [0, 0, 10, 10], "words": text} for text in masked_texts]
        document_file_service.write_image_store(paths, store)
        codes = harness.codes(document_validator.validate_section_images(paths, "heading-021-1"))
        harness.check(label, ("DOCUMENT_IMAGE_SECRET_UNMASKED" in codes) == is_refused, str(codes))


def test_a_cut_whose_own_text_shows_a_credential_is_placed_only_masked():
    """A configuration file's passwords were cut and offered to place unmasked: phase 3's note named "the values used",
    never a secret, and every secret check waited on that note (2026-10-02). The cut's own text is evidence too."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-090", "index": 90, "timestamp": 1.0, "path": "frames/frame-090.jpg", "record": {
        "screenshots": [{"part": "the settings file", "importance": "needed", "why": "it shows the values each call uses"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-040.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {
        "cut-040": {"frame": "frame-090", "file": "04_writing/images/cuts/cut-040.png", "masks": 0}}})
    document_file_service.write_section(paths, "heading-021-1", 'The settings file. [F90.1]\n\n![The file](cut-040 "The file")\n[F90.1]\n')
    original = text_detection_service.find_words
    try:
        for label, words, is_refused in (
                ("a password key with its value on the cut: refused", ['"syncUserPassword":', '"Qx%2B7kLm",'], True),
                ("only names and addresses: placed", ['"tokenUrl":', '"https://auth.acme.test/token",', '"region":', '"eu"'], False)):
            text_detection_service.find_words = lambda path, words=words, **kwargs: [
                {"text": text, "box": [10 + 60 * number, 10, 65 + 60 * number, 20], "block": 1} for number, text in enumerate(words)]
            report = document_validator.validate_section_images(paths, "heading-021-1")
            codes = harness.codes(report)
            harness.check(label, ("DOCUMENT_IMAGE_SECRET_UNMASKED" in codes) == is_refused, str(codes))
            if is_refused:
                fix = next(finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_SECRET_UNMASKED")
                harness.check("the fix names the key and says its value is a secret in any form",
                              "'\"syncUserPassword\":'" in fix and "whatever its form" in fix, fix)
    finally:
        text_detection_service.find_words = original


def test_a_masked_cut_with_no_value_left_is_told_to_be_cut_again():
    """A worker opened a cut whose rows ten guessed masks had blacked out four times and never cut the frame again: the
    fix named that route only as its last condition (2026-09-30). With no value left to read, the fix is that alone."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-089", "index": 89, "timestamp": 1.0, "path": "frames/frame-089.jpg", "record": {
        "screenshots": [{"part": "the add a secret form", "importance": "needed", "why": "it shows the client secret form"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-047.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-047": {
        "frame": "frame-089", "file": "04_writing/images/cuts/cut-047.png", "masks": 2,
        "maskedAreas": [{"rect": [0, 0, 10, 10], "words": "Value Secret ID"}]}}})
    document_file_service.write_section(paths, "heading-021-1", 'A form. [F89.1]\n\n![The form](cut-047 "The form")\n[F89.1]\n')
    original = text_detection_service.find_words
    text_detection_service.find_words = lambda path, **kwargs: [{"text": "Expires", "box": [0, 0, 10, 10], "block": 1}]
    try:
        report = document_validator.validate_section_images(paths, "heading-021-1")
    finally:
        text_detection_service.find_words = original
    fix = next(finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_SECRET_UNMASKED")
    harness.check("the fix is to cut the frame again and place the new cut", fix.startswith("No value is left on cut-047")
                  and "`cut` frame-089 again" in fix and "instead of cut-047" in fix, fix)


def test_a_cut_with_a_secret_still_readable_beside_its_mask_is_refused():
    """A worker masked a request line holding a tenant id on a cut that also showed an authorization header, a code and
    a cookie, and the one mask over a value cleared the cut (2026-09-30). A cut is cleared only when the text reader
    reads no long value and no credential label left on it."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-076", "index": 76, "timestamp": 1.0, "path": "frames/frame-076.jpg", "record": {
        "screenshots": [{"part": "the token request", "importance": "needed", "why": "it shows the token request"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-009.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-009": {
        "frame": "frame-076", "file": "04_writing/images/cuts/cut-009.png", "masks": 1,
        "maskedAreas": [{"rect": [0, 0, 10, 10], "words": "POST /3f1a9c2e-77b0-4d2c/token"}]}}})
    document_file_service.write_section(paths, "heading-030-1", 'A request. [F76.1]\n\n![The request](cut-009 "The request")\n[F76.1]\n')
    original = text_detection_service.find_words
    try:
        for label, words, is_refused in (
                ("a credential label left readable: refused", ["authorization:", "Basic"], True),
                ("a label read with a bracket before it: refused", ["(cookie:", "session=abc"], True),
                ("a cookie pair left readable: refused", ["x-slice=abc;", "sessioncookie=abc;"], True),
                ("a long value left readable: refused", ["grant_type=authorization_code&code=0.AXk7fakeValue9"], True),
                ("a web address with digits is no value: accepted", ["https://login.example.com/3f1a9c2e/v2.0/token"], False),
                ("an id in GUID form, whole or cut off at the edge, is no secret: accepted",
                 ["a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "¢9d41e2c-7a3b-4f60-a1d8-5c3"], False),
                ("nothing left but ordinary words: accepted", ["content-type:", "application/json"], False)):
            text_detection_service.find_words = lambda path, words=words, **kwargs: [
                {"text": text, "box": [0, 0, 10, 10], "block": 1} for text in words]
            report = document_validator.validate_section_images(paths, "heading-030-1")
            harness.check(label, ("DOCUMENT_IMAGE_SECRET_UNMASKED" in harness.codes(report)) == is_refused,
                          str([finding.detail for finding in report.findings]))
    finally:
        text_detection_service.find_words = original
    store = document_file_service.read_image_store(paths)
    store["cuts"]["cut-009"]["maskedAreas"].append({"rect": [0, 20, 10, 30], "words": "cookie:"})
    document_file_service.write_image_store(paths, store)
    text_detection_service.find_words = lambda path, **kwargs: []
    try:
        report = document_validator.validate_section_images(paths, "heading-030-1")
    finally:
        text_detection_service.find_words = original
    harness.check("a mask over a credential label without its value keeps the cut refused, told to cut the frame again",
                  "DOCUMENT_IMAGE_SECRET_UNMASKED" in harness.codes(report)
                  and any("`cut` frame-076 again" in (finding.fix or "") for finding in report.findings),
                  str([finding.fix for finding in report.findings]))
    narrow = {"rect": [0, 0, 10, 10], "words": "cookie: session=abcdefgh1234"}
    for label, areas, is_refused in (("a credential line's mask short of the edge: refused", [narrow], True),
                                     ("a credential line's mask to the edge: accepted", [{**narrow, "reachesEdge": True}], False),
                                     ("the same box masked again to the edge: accepted",
                                      [narrow, {**narrow, "rect": [0, 0, 90, 14], "reachesEdge": True}], False)):
        store = document_file_service.read_image_store(paths)
        store["cuts"]["cut-009"]["maskedAreas"] = areas
        document_file_service.write_image_store(paths, store)
        text_detection_service.find_words = lambda path, **kwargs: []
        try:
            report = document_validator.validate_section_images(paths, "heading-030-1")
        finally:
            text_detection_service.find_words = original
        harness.check(label, ("DOCUMENT_IMAGE_SECRET_UNMASKED" in harness.codes(report)) == is_refused
                      and (not is_refused or any("same box again" in (finding.fix or "") for finding in report.findings)),
                      str([finding.fix for finding in report.findings]))
    store = document_file_service.read_image_store(paths)
    store["cuts"]["cut-009"]["maskedAreas"] = [{"rect": [0, 0, 90, 14], "words": "Basic cTd4Mm16c", "reachesEdge": True}]
    document_file_service.write_image_store(paths, store)
    text_detection_service.find_words = lambda path, **kwargs: []
    try:
        codes = harness.codes(document_validator.validate_section_images(paths, "heading-030-1"))
    finally:
        text_detection_service.find_words = original
    harness.check("a credential line masked to the edge covers its value, though its start has no digit",
                  "DOCUMENT_IMAGE_SECRET_UNMASKED" not in codes, str(codes))


def test_a_heading_names_its_notes_easily_missed_in_one_line():
    """Asked why, a worker said it left out web addresses and speech visual alignment notes because they read as
    metadata, not facts; so it did in five of seven parts of one chapter (2026-09-30). Each heading names them."""

    entries = [{"id": "F1.1", "kind": "facts", "text": "A refund splits in three."},
               {"id": "F1.2", "kind": "url", "text": "https://acme.example.com/refunds"},
               {"id": "F1.3", "kind": "speech_visual_alignment", "text": "The speaker points at the split."}]
    section = {"heading": "Refund flow", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    text = document_messages.build_chapter_text(request)
    line = next((line for line in text.splitlines() if "easily missed" in line), "")
    harness.check("the url is named; the fact and the alignment note, which never has to be cited, are not",
                  "F1.2" in line and "F1.3" not in line and "F1.1" not in line, line)
    # 2026-10-08: a chapter's parts wrote the same address line again and again, each refused as said earlier
    harness.check("an address is written once in the document; a later part cites its id only",
                  "written as a link once in the whole document" in line and "never on the set-aside line" in line, line)


def test_a_heading_names_its_notes_holding_a_misheard_known_term():
    """A chapter wrote the transcript's word for a known term as a stakeholder, though the term mapping was in its
    instructions (2026-10-06): the notes holding such a word are named under their heading, with the term."""

    import tempfile
    from pathlib import Path
    import config
    root = Path(tempfile.mkdtemp())
    (root / "instructions" / "_shared").mkdir(parents=True)
    (root / "instructions" / "_shared" / "known-terms.md").write_text(
        "# Terms\n\n- **AcmeSync**: the sync service. The transcript writes it \"Acme Zinc\" or \"Akmesink\".\n"
        "- **Gateway**: the API gateway.\n", encoding="utf-8")
    entries = [{"id": "F1.1", "kind": "facts", "text": "Akmesink pushes the refund."},
               {"id": "F1.2", "kind": "facts", "text": "AcmeSync is named correctly here."}]
    section = {"heading": "Refund flow", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    original = config.AGENT_CONTEXT_DIR
    config.AGENT_CONTEXT_DIR = root
    try:
        text = document_messages.build_chapter_text(request)
    finally:
        config.AGENT_CONTEXT_DIR = original
    line = next((line for line in text.splitlines() if "known term" in line), "")
    harness.check("the note holding the misheard word is named with its term; the right spelling is not",
                  "F1.1 ('Akmesink' for AcmeSync)" in line and "F1.2" not in line, line)


def test_a_heading_names_its_notes_holding_a_name_a_screen_spells_otherwise_defect():
    # A note wrote "The simulation uses RepGL", the speech's spelling of the WebGL a later slide shows (DEFECTS 620). Now
    # the chapter request names such a note with the screen's spelling
    from utils import garbled_name_utils

    spellings = garbled_name_utils.map_misheard_screen_names(
        "The map draws with RepGL in the browser. AcmeSync stores the panels.",
        ['- title: "WebGL renderer"', '- footer: "AcmeSync"'])
    harness.check("the heard name is mapped to the screen's spelling, the name the screen shows is not",
                  spellings == {"RepGL": "WebGL"}, str(spellings))
    entries = [{"id": "F5.1", "kind": "facts", "text": "The simulation uses RepGL."},
               {"id": "F5.2", "kind": "facts", "text": "AcmeSync stores the panels."}]
    section = {"heading": "Simulation", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Simulation",
                                      "documentTitle": "Solar", "screenSpellings": spellings}, findings=[])
    text = document_messages.build_chapter_text(request)
    line = next((line for line in text.splitlines() if "misheard for a name a screen" in line), "")
    harness.check("the note holding the heard name is named with the screen's spelling",
                  "F5.1 ('RepGL' for WebGL)" in line and "F5.2" not in line, line)


def test_a_list_and_code_note_is_shown_in_one_fence_and_read_back_as_list_and_code_defect():
    # A note of a slide's list and then its code was shown bare, fenced by the writer with the code's ``` inside, and
    # the code was lost (DEFECTS 650). Now it is shown in one markdown fence, its code fence in tildes, and read back
    # as the list and a code block
    note = "### Auth\n- buffer from TEA\n\n```c\nsumm = summ + 0x9e3779b9;\n```"
    line = document_messages._render_entry_line({"id": "F3.4", "kind": "markdown", "text": note}, note)
    harness.check("the note is shown in one markdown fence, its code fence in tildes",
                  "```markdown\n### Auth" in line and "~~~c" in line and line.rstrip().endswith("```"), line)
    blocks = section_markdown_utils.parse_section(line.split(" ", 2)[2].strip() + "\n[F3.4]\n")
    harness.check("the list is read as markdown, not as code",
                  any(block["kind"] != "code" and "buffer from TEA" in block["text"] for block in blocks), str(blocks))
    harness.check("the code is read as a code block of its own, cited",
                  any(block["kind"] == "code" and block["text"] == "summ = summ + 0x9e3779b9;"
                      and block["entry_ids"] == ["F3.4"] for block in blocks), str(blocks))


def test_a_heading_names_its_diagram_notes_that_draw_a_chart():
    """A note's gantt of a timeline slide, its days invented, was drawn into the text beside the slide (2026-10-06)."""

    entries = [{"id": "F2.1", "kind": "facts", "text": "Testing runs from spring to autumn."},
               {"id": "F2.2", "kind": "diagram", "text": "gantt\n    Build : 2025-01-01, 2025-02-28"},
               {"id": "F2.3", "kind": "diagram", "text": 'flowchart LR\n    A["Shop"] --> B["Billing"]'}]
    section = {"heading": "Test plan", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Tests",
                                      "documentTitle": "Tests"}, findings=[])
    line = next((line for line in document_messages.build_chapter_text(request).splitlines()
                 if line.startswith("Diagram notes that draw a chart")), "")
    harness.check("the gantt note is named not to draw; the flowchart is not", line.endswith("F2.2"), line)
    dense = "flowchart LR\n" + "\n".join(f'    N{n}["Step {n}"] --> N{n + 1}["Step {n + 1}"]' for n in range(11))
    section["entries"] = entries + [{"id": "F2.4", "kind": "diagram", "text": dense}]
    dense_line = next((line for line in document_messages.build_chapter_text(request).splitlines()
                       if line.startswith("Diagram notes with more arrows")), "")
    harness.check("a diagram of more than ten arrows is named not to draw, with its count; the small flowchart is not",
                  dense_line.endswith("F2.4 (11 arrows)") and "F2.3" not in dense_line, dense_line)


def test_a_note_s_clock_time_is_written_as_it_stands_and_never_refused_defect():
    # A process table's CPU times were named "to be written with AM, PM or o'clock" and refused as recording positions,
    # and the writer replaced the column with "--". Now a time a note holds is its content: no line, no refusal
    from models.text_check_context import TextCheckContext
    from models.validation_report import ValidationReport
    from validators import document_validator

    entries = [{"id": "F3.1", "kind": "markdown", "text": "PID TIME+\n1 0:09.96\n445 0:04.51"}]
    section = {"heading": "Process viewer", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Tools",
                                      "documentTitle": "Tools"}, findings=[])
    harness.check("no request line asks to rewrite a note's clock time",
                  "Notes holding a clock time" not in document_messages.build_chapter_text(request), "")
    note_times = frozenset({"0:09", "0:04"})
    kept = ValidationReport(subject="heading-001-1")
    document_validator.check_text(kept, "heading-001-1", "PID TIME+\n1 0:09.96\n445 0:04.51", "",
                                  TextCheckContext(note_times=note_times))
    harness.check("a time a note holds is not refused", "DOCUMENT_TIMESTAMP" not in harness.codes(kept), str(kept.findings))
    refused = ValidationReport(subject="heading-001-1")
    document_validator.check_text(refused, "heading-001-1", "At 12:40 the speaker opens the editor.", "",
                                  TextCheckContext(note_times=note_times))
    harness.check("a time no note holds still is", "DOCUMENT_TIMESTAMP" in harness.codes(refused), str(refused.findings))


def test_a_memory_dump_s_address_on_a_cut_is_no_record_number_defect():
    # A cut of a memory dump was refused until its address "000094e9", read "00009429", was masked as a person's record
    # number (DEFECTS 652). Now a run of digits on a cut showing hexadecimal numbers is one of them
    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-041", "index": 41, "timestamp": 1.0, "path": "frames/frame-041.jpg", "record": {}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-011.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-011": {
        "frame": "frame-041", "file": "04_writing/images/cuts/cut-011.png", "masks": 0}}})
    document_file_service.write_section(paths, "heading-031-1", 'The dump. [F41.1]\n\n![The dump](cut-011 "The dump")\n[F41.1]\n')
    original = text_detection_service.find_words
    try:
        for label, words, is_refused in (("a dump's addresses: accepted", ["000094e8", "00009429", "000094ea", "00h"], False),
                                         ("a bare record number: still refused", ["Kunde", "44155027"], True)):
            text_detection_service.find_words = lambda path, words=words, **kwargs: [
                {"text": text, "box": [0, 0, 10, 10], "block": 1} for text in words]
            report = document_validator.validate_section_images(paths, "heading-031-1")
            harness.check(label, ("DOCUMENT_IMAGE_PERSONAL_UNMASKED" in harness.codes(report)) == is_refused,
                          str([finding.detail for finding in report.findings]))
    finally:
        text_detection_service.find_words = original


def test_an_email_address_readable_on_a_placed_cut_is_refused():
    """A cut placed with a person's e-mail address readable was accepted: no check looked (2026-09-30)."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-091", "index": 91, "timestamp": 1.0, "path": "frames/frame-091.jpg",
            "record": {"screenshots": [{"part": "the approval page", "importance": "needed", "why": "the error"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-011.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-011": {
        "frame": "frame-091", "file": "04_writing/images/cuts/cut-011.png", "masks": 0}}})
    document_file_service.write_section(paths, "heading-031-1", 'The page. [F91.1]\n\n![The page](cut-011 "The page")\n[F91.1]\n')
    original = text_detection_service.find_words
    try:
        for label, words, is_refused in (("an e-mail address readable: refused", ["jonas.doe@acme.example.com"], True),
                                         # A mail client's narrow account tree cut an address short
                                         ("an e-mail address cut short: refused", ["jonas.doe@ac..."], True),
                                         ("an e-mail address cut short with an ellipsis: refused", ["jonas.doe@\u2026"], True),
                                         ("no address: accepted", ["Need", "admin", "approval"], False),
                                         # 2026-10-08: a warning panel quoted a person's name, behind no honorific
                                         ("a person's name in quotes: refused", ["mit", "dem", '"Jonas', 'Berg"'], True),
                                         ("a company's name in quotes: accepted", ["Namen", '"Acme', 'AG"'], False),
                                         # Two cuts held a chat title's and a file list's "<Last>, <First>" unmasked
                                         ("a listed name with a guest mark: refused", ["Berg,", "Jonas", "(extern)"], True),
                                         ("a listed name with an honorific: refused", ["Berg,", "Dr.", "Jonas"], True),
                                         # A window title cut the guest mark short at its edge
                                         ("a listed name with a guest mark cut to '(ex': refused", ["Berg,", "Jonas", "(ex"], True),
                                         # A call tile wrote a person with two first names before the guest mark
                                         ("a listed name with two first names and a guest mark: refused",
                                          ["Berg,", "Jonas", "Kai", "(extern)"], True),
                                         ("two nouns joined by a comma: accepted", ["Kauf,", "Tausch", "Sparplan"], False),
                                         # A folder named by its date was masked as a customer's number
                                         ("a date written year, month, day: accepted", ["20260722", "Offer"], False),
                                         ("an eight-digit number that is no date: refused", ["44155027"], True),
                                         # A web address's host with a 0 read as @ is no person's address (2026-10-06)
                                         ("a host read with @ for 0: accepted", ["\"https:", "//api.acme@8.example.", "internal"], False)):
            text_detection_service.find_words = lambda path, words=words, **kwargs: [
                {"text": text, "box": [0, 0, 10, 10], "block": 1} for text in words]
            report = document_validator.validate_section_images(paths, "heading-031-1")
            harness.check(label, ("DOCUMENT_IMAGE_PERSONAL_UNMASKED" in harness.codes(report)) == is_refused,
                          str([finding.detail for finding in report.findings]))
            if is_refused:
                fix = next(finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_PERSONAL_UNMASKED")
                harness.check("the fix gives the address's rectangle, since `look` may list no box for it (2026-09-30: "
                              "read below the boxes' confidence floor, the address was in no box)", "[0, 0, 10, 10]" in fix, fix)
                # 2026-10-08: told "an e-mail address or a person's name" at a number, a worker looked for a name
                harness.check("the fix quotes the words read at each rectangle", words[-1] in fix, fix)
        # A file list's author column held "<Last>, <First>" rows below a "<Last>, Dr. <First>" row
        column = [("Berg,", 100, 0), ("Dr.", 160, 0), ("Jonas", 200, 0), ("Lind,", 101, 30), ("Mara", 160, 30),
                  ("Kauf,", 400, 60), ("Tausch", 460, 60)]
        text_detection_service.find_words = lambda path, **kwargs: [
            {"text": text, "box": [left, top, left + 50, top + 20], "block": 1} for text, left, top in column]
        report = document_validator.validate_section_images(paths, "heading-031-1")
        fix = next((finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_PERSONAL_UNMASKED"), "")
        harness.check("a listed name in the column of one with an honorific is named; two nouns elsewhere are not",
                      "'Lind, Mara'" in fix and "Kauf" not in fix, fix)
        # A list of people's records is left out, never masked row by row (2026-10-08: 101 names on one cut)
        many = [f"user{number}@acme.example.com" for number in range(document_validator.MOST_PERSONAL_TO_MASK + 1)]
        text_detection_service.find_words = lambda path, **kwargs: [
            {"text": text, "box": [0, 10 * row, 10, 10 * row + 8], "block": 1} for row, text in enumerate(many)]
        report = document_validator.validate_section_images(paths, "heading-031-1")
        fix = next((finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_PERSONAL_UNMASKED"), "")
        harness.check("a cut holding more people than can be masked is refused with `leave_out` of its frame",
                      "`leave_out` frame-091" in fix and "`mask`" not in fix, fix)
    finally:
        text_detection_service.find_words = original


def test_a_heading_names_its_notes_whose_wording_the_text_may_not_copy():
    """A note that says "shown on screen" was copied into the text, and the chapter blocked on the banned phrase
    (2026-09-30, the third such block): each heading names those notes and the words to leave out."""

    entries = [{"id": "F2.1", "kind": "facts", "text": "Jonas owns the Gateway group."},
               {"id": "F2.2", "kind": "speech_visual_alignment", "text": "The speaker names the group shown on screen."}]
    request = SimpleNamespace(inputs={"sections": [{"heading": "Groups", "topicId": "T02.1", "entries": entries}],
                                      "chapter": 2, "chapterCount": 2, "chapterTitle": "Groups", "documentTitle": "Groups"},
                              findings=[])
    line = next((line for line in document_messages.build_chapter_text(request).splitlines() if "may not copy" in line), "")
    harness.check("the note and its words are named, the clean note is not", "F2.2" in line and "on screen" in line
                  and "F2.1" not in line, line)


def test_a_value_read_below_the_confidence_floor_still_refuses_the_cut():
    """A token's last line and an encoded request value were read only at low confidence, and the check passed the cut
    (2026-09-30): the check reads with no floor."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-078", "index": 78, "timestamp": 1.0, "path": "frames/frame-078.jpg",
            "record": {"screenshots": [{"part": "the response", "why": "the access token came back"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-012.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-012": {
        "frame": "frame-078", "file": "04_writing/images/cuts/cut-012.png", "masks": 1,
        "maskedAreas": [{"rect": [0, 0, 10, 10], "words": "access_token eyJ0eXAiOiJKV1QiLCJhbGci9"}]}}})
    document_file_service.write_section(paths, "heading-032-1", 'A token. [F78.1]\n\n![The token](cut-012 "The token")\n[F78.1]\n')
    original = text_detection_service.find_words
    text_detection_service.find_words = lambda path, min_confidence=40.0: (
        [{"text": "pxODFkZW1vIiwiYXVkIjoiNyIsInRl", "box": [0, 20, 90, 30], "block": 2}] if min_confidence == 0 else [])
    try:
        codes = harness.codes(document_validator.validate_section_images(paths, "heading-032-1"))
    finally:
        text_detection_service.find_words = original
    harness.check("refused", "DOCUMENT_IMAGE_SECRET_UNMASKED" in codes, str(codes))


def test_an_image_written_inside_a_sentence_is_named_as_not_placed():
    """A worker wrote `... headers ![alt](cut-060 "caption").` inside a sentence; the frame counted as not placed and
    the finding said only which frame (2026-09-30)."""

    from support import fixtures as fx
    from services import document_file_service
    from validators import document_validator
    paths = fx.make_paths()
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-013.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-013": {
        "frame": "frame-079", "file": "04_writing/images/cuts/cut-013.png", "masks": 0}}})
    document_file_service.write_section(paths, "heading-033-1",
                                        'The call returns 200 ![The response](cut-013 "The response"). [F79.1]\n')
    report = document_validator.validate_needed_screenshots(paths, "heading-033-1", ["frame-079"], "chapter-03-text")
    details = " ".join(finding.detail for finding in report.findings)
    harness.check("the finding names the image written inside a sentence", "cut-013" in details and "own line" in details, details)


def test_a_chapter_with_too_few_sections_is_told_the_exact_heading_lines():
    """Told only "1 section(s) for 3 heading(s)", a worker whose later heading lines lacked their ids looked for blank
    lines (2026-09-30): the fix lists the lines."""

    import json
    from support import fixtures as fx
    from services import call_log_service, document_file_service
    from validators import document_validator
    paths = fx.make_paths()
    log = call_log_service.build_call_log_path(paths, "chapter-04-text")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"response": {"outcome": "completed", "finishReason": "stop", "parsed": {"markdown": "x"}}}), encoding="utf-8")
    answer = document_file_service.build_chapter_submission_path(paths, "chapter-04-text")
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text(json.dumps({"sections": 1}), encoding="utf-8")
    lines = ["### Refund flow (T04.1)", "### Refund limits (T04.2)"]
    report = document_validator.validate_chapter_count(paths, "chapter-04-text", lines)
    fix = next((finding.fix for finding in report.findings if finding.code == "DOCUMENT_CHAPTER_SECTIONS"), "")
    harness.check("the fix lists every heading line, id included", all(line in fix for line in lines),
                  fix or str([finding.code for finding in report.findings]))


def test_a_chapter_written_over_and_over_is_told_its_heading_stands_several_times():
    """An answer that looped held the chapter about five times; told only "6 section(s) for 1 heading(s)", the writer
    looked for headings it had not written (the wiki's chapter 6)."""

    import json
    from support import fixtures as fx
    from services import call_log_service, document_file_service
    from validators import document_validator
    paths = fx.make_paths()
    log = call_log_service.build_call_log_path(paths, "chapter-06-text")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"response": {"outcome": "completed", "finishReason": "stop", "parsed": {"markdown": "x"}}}), encoding="utf-8")
    answer = document_file_service.build_chapter_submission_path(paths, "chapter-06-text")
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text(json.dumps({"sections": 6, "headingIds": ["T06.1"] * 6}), encoding="utf-8")
    report = document_validator.validate_chapter_count(paths, "chapter-06-text", ["### Efficiency (T06.1)"])
    finding = next((finding for finding in report.findings if finding.code == "DOCUMENT_CHAPTER_SECTIONS"), None)
    harness.check("the finding says the heading stands 6 times and the text is repeated",
                  finding is not None and "6 times" in finding.detail and "once" in finding.fix,
                  str(finding))


def test_a_frame_noted_only_for_a_token_needs_a_mask_only_when_a_value_shows():
    """A cut of a documentation page about "token exchange changes" was held to need a mask, with no value on it to mask
    (2026-09-30): "token" alone is a weak sign; the cut needs a mask only when the reader finds a value on it. "secret",
    "password" and "credential" stay strict."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    items = [{"slug": "frame-081", "index": 81, "timestamp": 1.0, "path": "frames/frame-081.jpg", "record": {
                  "screenshots": [{"part": "the section on the change", "why": "it explains the token exchange change"}]}},
             {"slug": "frame-082", "index": 82, "timestamp": 2.0, "path": "frames/frame-082.jpg", "record": {
                  "screenshots": [{"part": "the new secret row", "why": "it shows the client secret"}]}}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    for cut in ("cut-021", "cut-022"):
        path = paths.output_root / "04_writing" / "images" / "cuts" / f"{cut}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {
        "cut-021": {"frame": "frame-081", "file": "04_writing/images/cuts/cut-021.png", "masks": 0},
        "cut-022": {"frame": "frame-082", "file": "04_writing/images/cuts/cut-022.png", "masks": 0}}})
    original = text_detection_service.find_words
    try:
        for label, cut, words, is_refused in (
                ("prose about tokens, no value: accepted", "cut-021", ["The", "solution", "uses", "roles"], False),
                ("the same frame with a long value showing: refused", "cut-021", ["eyJhbGciOiJSUzI1NiIsInR5cCI6"], True),
                ("a frame noted for a secret stays strict, value or not: refused", "cut-022", ["Description", "Expires"], True)):
            document_file_service.write_section(paths, "heading-034-1", f'A page. [F81.1]\n\n![The page]({cut} "The page")\n[F81.1]\n')
            text_detection_service.find_words = lambda path, words=words, **kwargs: [
                {"text": text, "box": [0, 0, 10, 10], "block": 1} for text in words]
            codes = harness.codes(document_validator.validate_section_images(paths, "heading-034-1"))
            harness.check(label, ("DOCUMENT_IMAGE_SECRET_UNMASKED" in codes) == is_refused, str(codes))
    finally:
        text_detection_service.find_words = original


def test_a_chapter_answer_that_writes_another_parts_heading_is_told_so():
    """Asked for a chapter's fourth part, a worker wrote its third part again, one heading for one heading; the split
    goes by position, so the third part's text was judged as the fourth's (2026-09-30). The ids written are compared
    with the ids asked for."""

    import json
    from support import fixtures as fx
    from services import call_log_service, document_file_service
    from utils import section_markdown_utils
    from validators import document_validator
    paths = fx.make_paths()
    log = call_log_service.build_call_log_path(paths, "chapter-04-text-4")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"response": {"outcome": "completed", "finishReason": "stop", "parsed": {"markdown": "x"}}}), encoding="utf-8")
    markdown = "### Refund limits (T04.4)\n\nA refund is capped. [F12.1]\n"
    harness.check("the heading ids are read from the answer", section_markdown_utils.list_heading_ids(markdown) == ["T04.4"])
    answer = document_file_service.build_chapter_submission_path(paths, "chapter-04-text-4")
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text(json.dumps({"sections": 1, "headingIds": ["T04.4"]}), encoding="utf-8")
    report = document_validator.validate_chapter_count(paths, "chapter-04-text-4", ["### Refund audit (T04.5)"])
    details = " ".join(finding.detail for finding in report.findings)
    harness.check("the finding names the heading written and the one asked for", "T04.4" in details and "T04.5" in details,
                  details)
    answer.write_text(json.dumps({"sections": 1, "headingIds": ["T04.5"]}), encoding="utf-8")
    harness.check("the heading asked for passes",
                  document_validator.validate_chapter_count(paths, "chapter-04-text-4", ["### Refund audit (T04.5)"]).ok)


def test_a_heading_s_questions_are_named_once_more_with_what_each_needs():
    """Three chapters running were refused for open-question notes cited nowhere."""

    entries = [{"id": "F3.1", "kind": "facts", "text": "Refunds come in three parts."},
               {"id": "F3.2", "kind": "open_question", "text": "Who approves a fourth part?"},
               {"id": "F3.3", "kind": "question", "text": "Can a refund be split? Yes, in three parts."}]
    section = {"heading": "Refund limits", "topicId": "T01.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    text = document_messages.build_chapter_text(request)
    open_line = next((line for line in text.splitlines() if line.startswith("Open questions under")), "")
    asked_line = next((line for line in text.splitlines() if line.startswith("Questions asked in the meeting")), "")
    harness.check("an open question is named for an Open line, and only there", open_line.endswith("F3.2"), open_line)
    harness.check("a question asked is named to be cited with its answer, even when another note states it",
                  asked_line.endswith("F3.3") and "even when another note states" in asked_line, asked_line)
    harness.check("a fact is on neither line", "F3.1" not in open_line + asked_line, f"{open_line} | {asked_line}")


def test_a_note_worded_as_the_meetings_course_is_named_to_be_cited_not_retold():
    entries = [{"id": "F4.1", "kind": "facts", "text": "The presentation moves to the second use case: refunds."},
               {"id": "F4.2", "kind": "facts", "text": "A refund is split into at most three parts."},
               # A question worded about the speaker stays among the questions only: told both to write it as an Open
               # line and to set it aside, a worker could not do both
               {"id": "F4.3", "kind": "open_question", "text": "What problem the speaker mentioned with the refund module."}]
    section = {"heading": "Refunds", "topicId": "T02.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 2, "chapterCount": 2, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    text = document_messages.build_chapter_text(request)
    line = text.split("Notes worded as what happened in the meeting")[1].split("\n")[0] if "Notes worded as" in text else ""
    harness.check("the narrating note is named, the subject note and the question are not",
                  "F4.1" in line and "F4.2" not in line and "F4.3" not in line, line or text[-400:])


def test_an_open_note_saying_a_step_is_not_shown_yet_is_listed_to_set_aside():
    """Listed as an Open line by the request and refused as narration by the check, a worker reworded "the syntax is
    not yet shown" into "the syntax is undefined", beside the syntax (2026-10-12)."""

    entries = [{"id": "F5.1", "kind": "open_question", "text": "The retry syntax is not yet written or shown."},
               {"id": "F5.2", "kind": "open_question", "text": "Who owns the retry budget is not settled."}]
    section = {"heading": "Retries", "topicId": "T03.1", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 3, "chapterCount": 3, "chapterTitle": "Retries",
                                      "documentTitle": "Retries"}, findings=[])
    text = document_messages.build_chapter_text(request)
    open_line = text.split("Open questions under this heading")[1].split("\n")[0]
    aside_line = text.split("Open notes that only say a step is not shown yet")[1].split("\n")[0] if "only say a step" in text else ""
    harness.check("the not-yet note is listed to set aside, the real question as an Open line",
                  "F5.2" in open_line and "F5.1" not in open_line and "F5.1" in aside_line, text[-600:])


def test_a_frame_suggested_under_several_headings_is_listed_once_with_place_it_once():
    first = {"heading": "Cost", "topicId": "T06.1", "entries": [], "suggestions": ["frame-022 needed: the cost chart"]}
    second = {"heading": "Savings", "topicId": "T06.2", "entries": [], "suggestions": ["frame-022 needed: the cost chart"]}
    request = SimpleNamespace(inputs={"sections": [first, second], "chapter": 6, "chapterCount": 6, "chapterTitle": "Costs",
                                      "documentTitle": "Costs"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the frame's suggestion is written once, and the later heading says to place it once",
                  text.count("frame-022 needed: the cost chart") == 1 and "Place each such frame once" in text, text[-500:])


def test_a_placed_cut_of_an_empty_area_is_refused():
    """A cut of an empty area of an editor, one even dark grey, was placed as the figure of a code block
    (2026-10-02)."""

    import cv2
    import numpy
    from support import fixtures as fx
    from services import document_file_service
    from validators import document_validator
    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    empty = numpy.full((120, 300, 3), 40, numpy.uint8)
    lines = empty.copy()
    for row in range(10, 110, 20):
        lines[row:row + 8, 20:260] = 200
    cv2.imwrite(str(cuts_dir / "cut-041.png"), empty)
    cv2.imwrite(str(cuts_dir / "cut-042.png"), lines)
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {
        "cut-041": {"frame": "frame-090", "file": "04_writing/images/cuts/cut-041.png", "masks": 0},
        "cut-042": {"frame": "frame-090", "file": "04_writing/images/cuts/cut-042.png", "masks": 0}}})
    for cut, is_refused in (("cut-041", True), ("cut-042", False)):
        document_file_service.write_section(paths, "heading-021-1", f'The code. [F90.1]\n\n![The code]({cut} "The code")\n[F90.1]\n')
        report = document_validator.validate_section_images(paths, "heading-021-1")
        codes = harness.codes(report)
        harness.check(f"{cut} refused as empty: {is_refused}", ("DOCUMENT_IMAGE_BLANK" in codes) == is_refused, str(codes))
        if is_refused:
            fix = next(finding.fix for finding in report.findings if finding.code == "DOCUMENT_IMAGE_BLANK")
            harness.check("the fix names the frame's other cut", "cut-042 of the same frame" in fix, fix)


def test_the_closing_names_every_needed_frame_of_the_chapter_once():
    """A chapter of five headings left the five frames they marked needed unplaced: each heading listed its own, and
    the closing named none (2026-10-04; 180 such findings over six runs)."""

    sections = [{"heading": "Refund flow", "topicId": "T01.1", "entries": [], "neededFrames": ["frame-002", "frame-003"]},
                {"heading": "Refund limits", "topicId": "T01.2", "entries": [], "neededFrames": ["frame-003", "frame-005"]},
                {"heading": "Refund history", "topicId": "T01.3", "entries": []}]
    request = SimpleNamespace(inputs={"sections": sections, "chapter": 1, "chapterCount": 1, "chapterTitle": "Refunds",
                                      "documentTitle": "Refunds"}, findings=[])
    closing = document_messages.build_chapter_text(request).split("Return `markdown`", 1)[1]
    harness.check("the closing names each needed frame once, in order",
                  "one by one: frame-002, frame-003, frame-005." in closing, closing[-500:])
    request.inputs["sections"] = sections[2:]
    harness.check("a chapter with no needed frame has no such line",
                  "marks needed" not in document_messages.build_chapter_text(request))


def test_a_cut_already_made_is_offered_with_its_warnings():
    """A cut that left a table's last column out was offered as "already made" with no word of its warning, and the
    worker planned to place it (2026-10-04)."""

    from graph.nodes import write_document_node
    entries = {"F2.1": {"id": "F2.1", "kind": "facts", "frames": [2]}}
    cuts = {"cut-004": {"frame": "frame-002", "file": "a.png", "warnings": ["text in line with the cut is left out"]},
            "cut-005": {"frame": "frame-002", "file": "b.png", "warnings": []}}
    listed = write_document_node._list_existing_cuts(["F2.1"], entries, cuts, {})
    harness.check("the cut with a warning carries it, the clean one does not",
                  listed == ["cut-004 from frame-002 (its warnings: text in line with the cut is left out)",
                             "cut-005 from frame-002"], str(listed))
    # A frame already left out keeps its cut out: offered with no word of it, a worker placed it four answers running
    left = write_document_node._list_existing_cuts(["F2.1"], entries, {"cut-005": cuts["cut-005"]},
                                                   {"frame-002": "a placed frame shows it"})
    harness.check("the cut of a left-out frame says it is not to be placed",
                  left == ["cut-005 from frame-002 (not to place while frame-002 stays left out, a placed frame shows it; if a "
                          "rejection says no placed frame shows frame-002, place this cut)"], str(left))


def test_a_needed_frame_an_earlier_chapter_already_placed_is_named():
    """Two slides of one deck showed one diagram under two titles; the later chapter cut it again and was refused as a
    repeat after writing the chapter (2026-10-04)."""

    import cv2
    import numpy
    from graph.nodes import write_document_node
    from services import document_file_service
    from support import fixtures as fx
    paths = fx.make_paths()
    frames, cuts_dir = paths.output_root / "frames", paths.output_root / "04_writing" / "images" / "cuts"
    sections = paths.output_root / "04_writing" / "sections"
    for folder in (frames, cuts_dir, sections):
        folder.mkdir(parents=True, exist_ok=True)
    diagram = numpy.full((723, 1280, 3), 255, numpy.uint8)
    for box in range(5):
        cv2.rectangle(diagram, (100 + box * 220, 300), (260 + box * 220, 420), (120, 80, 40), -1)
    cv2.imwrite(str(frames / "frame-002.jpg"), diagram)
    cv2.imwrite(str(frames / "frame-009.jpg"), diagram)
    cv2.imwrite(str(frames / "frame-011.jpg"), numpy.full((723, 1280, 3), 255, numpy.uint8))
    cv2.imwrite(str(cuts_dir / "cut-001.png"), cv2.resize(diagram[280:440, 80:1220], None, fx=1.5, fy=1.5))
    (sections / "heading-001-1.md").write_text('Flow. [F2.1]\n\n![flow](cut-001 "c") [F2.1]\n', encoding="utf-8")
    cuts = {"cut-001": {"frame": "frame-002", "file": "04_writing/images/cuts/cut-001.png", "lookRect": [80, 280, 1220, 440]}}
    matches = figure_match_service.list_placed_matches(paths, ["frame-009", "frame-011"], cuts, ["heading-001-1"])
    harness.check("the frame showing the placed diagram again is named, the blank one is not",
                  matches == ["frame-009 shows what cut-001 (from frame-002) already shows"], str(matches))
    request = SimpleNamespace(inputs={"sections": [{"heading": "Flow again", "topicId": "T02.1", "entries": [],
                                                    "placedMatches": matches}], "chapter": 2, "chapterCount": 2,
                                      "chapterTitle": "Target", "documentTitle": "Flows"}, findings=[])
    harness.check("the request tells the writer to leave that frame out",
                  "Shown already: frame-009" in document_messages.build_chapter_text(request))


def test_a_needed_frame_that_repeats_an_earlier_needed_frame_of_the_chapter_is_named():
    """A slide shown twice was two needed frames; the chapter cut and placed both (2026-10-04)."""

    import cv2
    import numpy
    from graph.nodes import write_document_node
    from support import fixtures as fx
    paths = fx.make_paths()
    frames = paths.output_root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    slide = numpy.full((723, 1280, 3), 255, numpy.uint8)
    cv2.rectangle(slide, (200, 200), (900, 500), (90, 60, 30), -1)
    other = numpy.full((723, 1280, 3), 255, numpy.uint8)
    cv2.circle(other, (640, 360), 200, (30, 90, 60), -1)
    for name, image in (("frame-004", slide), ("frame-007", slide), ("frame-008", other)):
        cv2.imwrite(str(frames / f"{name}.jpg"), image)
    twins = figure_match_service.list_twin_frames(paths, ["frame-007", "frame-008"], ["frame-004"])
    harness.check("the slide shown again is named with its first showing, the other slide is not",
                  twins == ["frame-007 shows what frame-004 shows: place one of the two"], str(twins))
    placed = figure_match_service.list_twin_frames(paths, ["frame-007"], ["frame-004"], {"frame-004": "cut-02"})
    harness.check("when an earlier part placed the first showing, the later frame is to be left out, naming the cut",
                  placed == ["frame-007 shows what frame-004 shows, placed already as cut-02: leave frame-007 out"],
                  str(placed))


def test_one_slide_taken_in_two_shapes_is_named_by_its_words():
    """One slide was taken twice, once with a camera column the crop left in; the frames' shapes differed, so their
    pixels were never compared, and both were cut (2026-10-04)."""

    import cv2
    import numpy
    from graph.nodes import write_document_node
    from services import text_detection_service
    from support import fixtures as fx
    paths = fx.make_paths()
    frames = paths.output_root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    lines = ["Refund review needs approval", "Ledger export runs nightly", "Invoices archive after audit",
             "Payment holds expire weekly", "Customer records sync hourly", "Disputes escalate within days"]

    def draw_slide(width: int, height: int) -> numpy.ndarray:
        slide = numpy.full((height, width, 3), 255, numpy.uint8)
        for number, line in enumerate(lines):
            cv2.putText(slide, line, (60, 90 + number * 80), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 2)
        return slide

    wide = draw_slide(1280, 629)
    cv2.rectangle(wide, (1110, 0), (1280, 629), (60, 60, 60), -1)
    cv2.imwrite(str(frames / "frame-003.jpg"), draw_slide(1280, 723))
    cv2.imwrite(str(frames / "frame-005.jpg"), wide)
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the frames' words", True)
        return
    twins = figure_match_service.list_twin_frames(paths, ["frame-005"], ["frame-003"])
    harness.check("the slide taken in another shape is named", twins == ["frame-005 shows what frame-003 shows: place one of the two"],
                  str(twins))


def test_a_frame_left_out_as_shown_elsewhere_must_be_shown_by_a_placed_frame():
    """Frames of other dashboard sections were left out as "the same dashboard", which no placed cut showed
    (2026-10-04)."""

    import cv2
    import numpy
    from services import document_file_service
    from support import fixtures as fx
    from validators import document_validator
    paths = fx.make_paths()
    frames, cuts_dir = paths.output_root / "frames", paths.output_root / "04_writing" / "images" / "cuts"
    sections = paths.output_root / "04_writing" / "sections"
    for folder in (frames, cuts_dir, sections):
        folder.mkdir(parents=True, exist_ok=True)
    first = numpy.full((723, 1280, 3), 255, numpy.uint8)
    cv2.rectangle(first, (100, 300), (900, 420), (120, 80, 40), -1)
    other = numpy.full((723, 1280, 3), 255, numpy.uint8)
    cv2.circle(other, (640, 360), 220, (30, 100, 60), -1)
    for name, image in (("frame-002", first), ("frame-003", first), ("frame-004", other)):
        cv2.imwrite(str(frames / f"{name}.jpg"), image)
    cv2.imwrite(str(cuts_dir / "cut-001.png"), first[280:440, 80:920])
    (sections / "heading-001-1.md").write_text('Flow. [F2.1]\n\n![flow](cut-001 "c") [F2.1]\n', encoding="utf-8")
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-001": {"frame": "frame-002", "file": "04_writing/images/cuts/cut-001.png", "lookRect": [80, 280, 920, 440]}},
        "leftOut": [{"frame": "frame-003", "reason": "a placed frame shows the same view", "chapter": "chapter-01-text"},
                    {"frame": "frame-004", "reason": "the same dashboard as the placed one", "chapter": "chapter-01-text"}]})
    report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-002", "frame-003", "frame-004"],
                                                            "chapter-01-text")
    text = json.dumps([vars(finding) if hasattr(finding, "__dict__") else str(finding) for finding in report.findings], default=str)
    harness.check("the frame no placed cut shows is named, the one cut-001 shows is not",
                  "DOCUMENT_LEAVE_OUT_UNFOUNDED" in text and "frame-004" in text and "frame-003" not in text.split("frame-004")[0][-80:],
                  text)


def test_a_frame_left_out_as_written_in_the_text_is_no_claim_of_a_placed_frame():
    """"The code line is written as a markdown code block, making this frame redundant" named a block the section
    writes; its "redundant" read as a claim that a placed frame shows it, which no cut did (Lecture 9 chapter 3)."""

    from validators import document_validator
    store = {"leftOut": [{"frame": "frame-044", "chapter": "chapter-03-text",
                          "reason": "The code line is written as a markdown code block, making this frame redundant."},
                         {"frame": "frame-045", "chapter": "chapter-03-text", "reason": "redundant with the placed cut"}]}
    dispositions = document_validator._read_frame_dispositions(store, "chapter-03-text")
    harness.check("the frame written as a block is left out plainly; the one a placed cut shows still claims a frame",
                  dispositions.shown_elsewhere == {"frame-045"} and dispositions.left_out == {"frame-044", "frame-045"},
                  str(dispositions))


def test_one_page_scrolled_to_other_lines_is_no_twin():
    """An editor page scrolled from its first lines to lines far below differs in few pixels, since the editor is a
    small part of the page; it was named a twin, and the worker spent a long reasoning on which to leave out
    (2026-10-06). Frames that look alike but hold other words are not twins; a frame of an image with few words still is."""

    from pathlib import Path

    import cv2
    import numpy
    from services import text_detection_service
    from support import fixtures as fx
    paths = fx.make_paths()
    frames = paths.output_root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    page = numpy.full((723, 1280, 3), 255, numpy.uint8)
    cv2.rectangle(page, (200, 200), (900, 500), (90, 60, 30), -1)
    for name in ("frame-010", "frame-011", "frame-020", "frame-021"):
        cv2.imwrite(str(frames / f"{name}.jpg"), page)
    texts = {"frame-010": " ".join(f"header{number:02d}" for number in range(20)),
             "frame-011": " ".join(f"probes{number:02d}" for number in range(20)),
             "frame-020": "logo", "frame-021": "logo"}
    previous = text_detection_service.find_words
    text_detection_service.find_words = lambda path, min_confidence=0: [
        {"text": word, "line": 0, "box": [0, 0, 10, 10], "block": 1} for word in texts[Path(path).stem].split()]
    try:
        scrolled = figure_match_service.list_twin_frames(paths, ["frame-011"], ["frame-010"])
        few_words = figure_match_service.list_twin_frames(paths, ["frame-021"], ["frame-020"])
    finally:
        text_detection_service.find_words = previous
    harness.check("two frames alike in pixels with other words are no twins", scrolled == [], str(scrolled))
    harness.check("two frames alike in pixels with too few words to tell stay twins",
                  few_words == ["frame-021 shows what frame-020 shows: place one of the two"], str(few_words))


def test_a_caption_naming_the_slide_as_its_subject_is_refused_in_the_chapter():
    """A caption opening "the slide details …" stood behind its alt text in one sentence, passed every chapter check and
    failed the finished document (2026-10-08): each is checked as a block of its own."""

    from support import fixtures as fx
    from models import output_contract
    from services import document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-091", "index": 91, "timestamp": 1.0, "path": "frames/frame-091.jpg",
            "record": {"screenshots": [{"part": "the approval page", "importance": "needed", "why": "the error"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-011.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-011": {
        "frame": "frame-091", "file": "04_writing/images/cuts/cut-011.png", "masks": 0}}})
    markdown = 'Approval needs an admin. [F91.1]\n\n![approval page](cut-011 "the slide details the approval rule")\n[F91.1]\n'
    from services import call_log_service
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-031-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, "heading-031-1", markdown)
    original = text_detection_service.find_words
    try:
        text_detection_service.find_words = lambda path, **kwargs: []
        report = document_validator.validate_section(paths, "heading-031-1", ["F91.1"], earlier_slugs=[])
        harness.check("the caption is refused as narration in the chapter, as in the finished document",
                      "DOCUMENT_RECORDING_NARRATED" in harness.codes(report), str(report.findings))
        # Told only the sentence, a writer looked for it in its prose six checks in a row (lecture-1, 2026-10-14)
        harness.check("the finding says the sentence is the caption of cut-011",
                      "in the caption of cut-011" in str(report.findings), str(report.findings))
    finally:
        text_detection_service.find_words = original


def test_a_figure_description_quoting_a_screen_again_is_no_repeat():
    """A success message quoted in a chapter and, exactly as write.md asks, in its figure's description failed the
    finished document as a sentence said twice (2026-10-08)."""

    from utils import document_text_utils
    markdown = ('The page confirms "Thank you, your consent was sent" at once.\n\n'
                '![page](cut-01 "consent is confirmed at once")\n'
                '> **What the figure shows:** the heading "Thank you, your consent was sent" over\n'
                '> a line of help text.\n[F1.1]\n\nNext paragraph.')
    kept = document_text_utils.remove_figure_descriptions(markdown)
    harness.check("the description goes, with its continued line, and nothing else",
                  "What the figure shows" not in kept and "a line of help text" not in kept
                  and "The page confirms" in kept and "[F1.1]" in kept and "Next paragraph." in kept, kept)


def test_a_web_address_note_on_the_set_aside_line_is_refused():
    """write.md's "never set aside a web address" had no check; a chapter set its address notes aside (2026-10-08)."""

    from support import fixtures as fx
    from models.section_judging_context import SectionJudgingContext
    from services import call_log_service, document_file_service
    from validators import document_validator
    paths = fx.make_paths()
    markdown = ("Approval needs an admin. [F91.1]\n\n> **Set aside:** the page's address [F91.2]\n")
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-031-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, "heading-031-1", markdown)
    scope = SectionJudgingContext(entry_ids=["F91.1", "F91.2"], kept_ids=["F91.2"])
    report = document_validator.judge_section(paths, "heading-031-1", scope, [])
    harness.check("a web address note set aside is refused", "DOCUMENT_KEPT_NOTE_SET_ASIDE" in harness.codes(report),
                  str([finding.code for finding in report.findings]))
    cited = "Approval needs an admin. [F91.1, F91.2]\n"
    document_file_service.write_section(paths, "heading-031-1", cited)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-031-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": cited},
                                                  "finishReason": "stop"}})
    report = document_validator.judge_section(paths, "heading-031-1", scope, [])
    harness.check("cited in a block's brackets it passes", "DOCUMENT_KEPT_NOTE_SET_ASIDE" not in harness.codes(report),
                  str([finding.code for finding in report.findings]))


def test_a_web_address_note_holding_a_persons_email_may_be_set_aside_defect():
    # A note holding the presenter's e-mail had to be kept, and the text may not write a person's address: the writer
    # cited it on a paragraph that never wrote it. Such a note is no longer one that is never set aside
    from utils import document_plan_utils
    harness.check("a person's e-mail note may be set aside", not document_plan_utils.is_kept_note(
        {"kind": "url", "text": "jonas.berg@acmesync.example | the presenter's email address"}))
    harness.check("a page's address is still kept", document_plan_utils.is_kept_note(
        {"kind": "url", "text": "https://docs.acmesync.example/gateway | the gateway's guide"}))
    harness.check("a team's mailbox is still kept", document_plan_utils.is_kept_note(
        {"kind": "url", "text": "platform-team@acmesync.example | the rollout's contact"}))
    harness.check("an action item is still kept", document_plan_utils.is_kept_note(
        {"kind": "action_item", "text": "Jonas sends jonas.berg@acmesync.example the export"}))


def test_a_heading_quoted_in_the_text_and_in_its_figure_description_is_no_repeat():
    """A legal heading cited in a paragraph and quoted, as write.md asks, in the figure's description was refused as
    said twice by the chapter check while the document check passed it (2026-10-08)."""

    from support import fixtures as fx
    from models import output_contract
    from services import call_log_service, document_file_service, phase_file_service, text_detection_service
    from validators import document_validator
    paths = fx.make_paths()
    item = {"slug": "frame-091", "index": 91, "timestamp": 1.0, "path": "frames/frame-091.jpg",
            "record": {"screenshots": [{"part": "the form", "importance": "needed", "why": "the rule"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    cut_file = paths.output_root / "04_writing" / "images" / "cuts" / "cut-011.png"
    cut_file.parent.mkdir(parents=True, exist_ok=True)
    cut_file.write_bytes(b"png")
    document_file_service.write_image_store(paths, {"looks": {}, "cuts": {"cut-011": {
        "frame": "frame-091", "file": "04_writing/images/cuts/cut-011.png", "masks": 0}}})
    markdown = ('Owners are confirmed under "Owners of the company (gem. § 4 Abs. 2 des Gesetzes)". [F91.1]\n\n'
                '![the owner form](cut-011 "owners are confirmed explicitly")\n'
                '> **What the figure shows:** the heading "Owners of the company (gem. § 4 Abs. 2 des Gesetzes)" '
                'over two choices to confirm the owners listed.\n[F91.1]\n')
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-031-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, "heading-031-1", markdown)
    original = text_detection_service.find_words
    try:
        text_detection_service.find_words = lambda path, **kwargs: []
        report = document_validator.validate_section(paths, "heading-031-1", ["F91.1"], earlier_slugs=[])
        harness.check("the description's quote of the heading is no repeat",
                      "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(report), str(report.findings))
    finally:
        text_detection_service.find_words = original


def test_a_heading_both_of_whose_pieces_are_in_one_request_is_not_said_to_continue():
    """A short return folded into an earlier chapter's heading puts both its pieces in one request."""

    base = {"chapter": 2, "chapterCount": 5, "chapterTitle": "Refunds", "documentTitle": "Refunds", "partCount": 2,
            "part": 1, "earlierSlugs": []}
    both = [{"heading": "Refund flow", "topicId": "T02.1", "entries": [], "slug": "heading-005-1"},
            {"heading": "Refund flow", "topicId": "T02.1", "entries": [], "slug": "heading-005-2"}]
    text = document_messages.build_chapter_text(SimpleNamespace(inputs={**base, "sections": both}, findings=[]))
    harness.check("a heading begun in this same request does not continue here", "continues here" not in text, text[:600])
    later = SimpleNamespace(inputs={**base, "part": 2, "sections": both[1:], "earlierSlugs": ["heading-005-1"]}, findings=[])
    harness.check("one an earlier request began does", "continues here" in document_messages.build_chapter_text(later))


def test_a_block_holding_a_persons_email_is_asked_for_without_the_address_defect():
    # A closing slide's block, asked for as it stands, held the speaker's address, which the check refuses in the text:
    # the writer spent a check on it. The request now names such a block and asks for it without the address
    block = "- Thank you!\n- Questions?\n- jonas.berg@acmesync.example"
    section = {"heading": "Wrap-up", "topicId": "T01.1",
               "entries": [{"id": "F9.4", "kind": "markdown", "text": block},
                           {"id": "F9.5", "kind": "markdown", "text": "| Tool | System |\n| NVDA | Windows |"}]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "End",
                                      "documentTitle": "End"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the block holding an address is asked for without it",
                  "blocks holding a person's e-mail address: write them without the address: F9.4" in text, text)


def test_a_markdown_note_repeating_an_earlier_block_is_named_cite_only():
    """One slide shown three times held one list three times; the request asked each to be written as it stands and
    the check refused the second as said earlier, and a writer went round six times (2026-10-13)."""

    block = "# What to do now?\n- Read the Gateway docs\n- Avoid non-standard parts\n- Use a screen reader"
    first = {"heading": "Docs", "topicId": "T01.1", "entries": [{"id": "F40.8", "kind": "markdown", "text": block}]}
    again = {"heading": "Libraries", "topicId": "T01.2",
             "entries": [{"id": "F41.8", "kind": "markdown", "text": block.replace("- ", "* ")},
                         {"id": "F41.9", "kind": "markdown", "text": "| Tool | System |\n| NVDA | Windows |"}]}
    request = SimpleNamespace(inputs={"sections": [first, again], "chapter": 1, "chapterCount": 1, "chapterTitle": "Doing",
                                      "documentTitle": "Doing"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the first block is written as it stands", "for that part: F40.8" in text, text)
    harness.check("its repeat is cite only", "already writes: never write it again; its id may go in the brackets of the "
                  "block this heading writes about it, and need not: F41.8" in text, text)
    harness.check("a new block under the same heading is still written", "for that part: F41.9" in text, text)


def test_a_markdown_note_repeating_a_block_an_earlier_chapter_wrote_is_named_cite_only():
    """A slide's code an earlier chapter already held was asked for as it stands, refused as said earlier, and a writer
    went round between the two (lecture-1 chapter 6, 2026-10-14)."""

    code = "retries = 3\ntimeout = retries * 2\nprint(timeout)"
    section = {"heading": "Timeouts", "topicId": "T02.1",
               "entries": [{"id": "F70.4", "kind": "markdown", "text": f"```python\n{code}\n```"},
                           {"id": "F70.5", "kind": "markdown", "text": "limit = 10"}]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 2, "chapterCount": 2, "chapterTitle": "Timeouts",
                                      "documentTitle": "Gateway", "earlierBlocks": [code]}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the block an earlier chapter wrote is cite only",
                  "its id may go in the brackets of the block this heading writes about it, and need not: F70.4" in text, text)
    harness.check("a new block is still written as it stands", "for that part: F70.5" in text, text)


def test_a_markdown_note_of_sentences_or_a_title_is_stated_never_boxed():
    """A task slide's sentences and a slide's lone title were written as markdown and copied into the document as code
    boxes of prose, five in one document (2026-10-13)."""

    section = {"heading": "Task", "topicId": "T01.1", "entries": [
        {"id": "F14.4", "kind": "markdown", "text": "Task: Check which interfaces\n(touch, keyboard) your Gateway has."},
        {"id": "F35.6", "kind": "markdown", "text": "# Robust"},
        {"id": "F40.8", "kind": "markdown", "text": "- Read the docs\n- Use a screen reader"},
        {"id": "F12.3", "kind": "markdown", "text": "retries = 3"},
        # Console lines of calls alone are code too (lecture-1, 2026-10-15)
        {"id": "F12.4", "kind": "markdown", "text": "float(3)\nGateway.ping()"}]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Task",
                                      "documentTitle": "Task"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("sentences and a lone title are stated, never boxed",
                  "never as a block or a heading; a slide that only names the talk" in text
                  and "`> **Set aside:**` line: F14.4, F35.6" in text, text)
    harness.check("a list and code are still written as they stand", "for that part: F40.8, F12.3, F12.4" in text, text)



def test_a_title_slide_note_may_be_set_aside_defect():
    # Asked to state a title slide's notes in sentences, a writer narrated the talk three times and was refused for it
    # each time (DEFECTS 291); now the line says a slide naming only the talk, its speaker or its event is set aside
    section = {"heading": "Opening", "topicId": "T01.1", "entries": [
        {"id": "F0.4", "kind": "markdown", "text": "**Acme Days 2030**\n\nA ledger for small shops\n\nJonas, Gateway Ltd"}]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Opening",
                                      "documentTitle": "Ledger"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the title slide's note may go on the set-aside line",
                  "only names the talk, its speaker or its event teaches nothing" in text and "F0.4" in text, text)


def test_a_term_entry_naming_more_before_its_colon_is_read_with_its_spellings():
    """A term list's entry "- **ZEB** (numbered: ...) and **LIVE**: ... The transcript writes ZEB "Zepp"" was not read:
    the reader wanted the colon right after the name and "writes it"."""

    import tempfile
    from pathlib import Path
    import config
    root = Path(tempfile.mkdtemp())
    (root / "instructions" / "_shared").mkdir(parents=True)
    (root / "instructions" / "_shared" / "known-terms.md").write_text(
        "# Terms\n\n- **ZEB** (numbered: ZEB1, ZEB2, ...) and **LIVE**: the stages a service runs in. The transcript "
        "writes ZEB \"Zepp\" or \"Seb\".\n- **AcmeSync**: the sync service. The transcript writes it \"Acme Zinc\".\n",
        encoding="utf-8")
    original = config.AGENT_CONTEXT_DIR
    config.AGENT_CONTEXT_DIR = root
    try:
        spellings = system_prompt_builder.read_transcript_spellings()
    finally:
        config.AGENT_CONTEXT_DIR = original
    harness.check("both entries' spellings are read, each with its term",
                  spellings == {"Zepp": "ZEB", "Seb": "ZEB", "Acme Zinc": "AcmeSync"}, str(spellings))


def test_a_heading_names_the_subjects_folded_into_it_as_subheadings():
    """Headings started inside one paragraph are folded into the first; the writer is told their subjects, so each
    keeps a subheading of its own instead of being lost."""

    section = {"heading": "Gateway", "topicId": "T01.1", "entries": [], "suggestions": [], "subjects": ["Proxy", "Cleanup"]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Sprint",
                                      "documentTitle": "Sprint"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the folded subjects are named under their heading, each as a subheading",
                  "### Gateway (T01.1)\nThis heading also covers these subjects; write each under a `####` subheading of "
                  "its own: Proxy; Cleanup\n" in text, text[:600])


def test_a_note_of_indented_code_is_not_a_prose_block():
    harness.check("an indented command is code to the prompt as it is to phase 4",
                  not section_markdown_utils.is_prose_block("    kubectl get pods"))
    harness.check("a sentence is prose", section_markdown_utils.is_prose_block("The gateway sends the token."))


def test_the_marks_a_chapter_request_prints_are_the_ones_it_and_the_instruction_name():
    write_text = (config.AGENT_CONTEXT_DIR / "instructions" / "_shared" / "write.md").read_text(encoding="utf-8")
    entries = [{"id": "S0001", "kind": "speech", "text": "Yes.", "heardByFrame": True, "isBriefReply": True}]
    line = document_messages._render_entry_line(entries[0], entries[0]["text"], document_messages._render_speech_marks(entries[0]))
    harness.check("both marks stand on the entry line, in order",
                  line == "S0001 [speech] (heard by a frame) (a brief reply) Yes.", line)
    harness.check("write.md names both marks as the request prints them",
                  document_messages._HEARD_BY_FRAME_MARK in write_text and document_messages._BRIEF_REPLY_MARK in write_text)


def test_two_lists_that_differ_only_in_a_figure_are_not_the_same_block():
    harness.check("a bullet's own figure is part of the line",
                  section_markdown_utils.list_block_lines("- 12% churn") != section_markdown_utils.list_block_lines("- 15% churn"))
    harness.check("a list number is not part of the line",
                  section_markdown_utils.list_block_lines("1. step") == section_markdown_utils.list_block_lines("2. step"))


def test_a_placed_cut_with_no_frame_recorded_is_still_matched_against_a_needed_frame():
    import cv2
    import numpy
    from support import fixtures as fx
    paths = fx.make_paths()
    frames, cuts_dir = paths.output_root / "frames", paths.output_root / "04_writing" / "images" / "cuts"
    sections = paths.output_root / "04_writing" / "sections"
    for folder in (frames, cuts_dir, sections):
        folder.mkdir(parents=True, exist_ok=True)
    diagram = numpy.full((723, 1280, 3), 255, numpy.uint8)
    for box in range(5):
        cv2.rectangle(diagram, (100 + box * 220, 300), (260 + box * 220, 420), (120, 80, 40), -1)
    cv2.imwrite(str(frames / "frame-009.jpg"), diagram)
    cv2.imwrite(str(cuts_dir / "cut-001.png"), cv2.resize(diagram[280:440, 80:1220], None, fx=1.5, fy=1.5))
    (sections / "heading-001-1.md").write_text('Flow.\n\n![flow](cut-001 "c")\n', encoding="utf-8")
    cuts = {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "lookRect": [80, 280, 1220, 440]}}
    try:
        matches = figure_match_service.list_placed_matches(paths, ["frame-009"], cuts, ["heading-001-1"])
        outcome = str(matches)
    except KeyError as missing:
        outcome = f"KeyError {missing}"
    harness.check("the match is named without the cut's frame", outcome.startswith("['frame-009 shows what cut-001"), outcome)


def test_a_cited_paragraph_s_code_block_is_written_in_the_section():
    """A specification's two central examples were cited after a sentence pointing to their section and never written
    (pdf-real-rfc9457)."""

    paths = fx.make_paths()
    code = 'POST /orders HTTP/1.1\nHost: shop.example\n{\n  "item": 42\n}'
    scope = SectionJudgingContext(entry_ids=["S0023"], code_blocks={"S0023": [code]})
    for markdown, is_refused, label in (
            ("The format is defined in the next section. [S0023]\n", True, "a pointer sentence citing the paragraph"),
            ("An order is posted as:\n\n```\n" + code + "\n```\n[S0023]\n", False, "the block written out")):
        document_file_service.write_section(paths, "heading-031-1", markdown)
        call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-031-1"),
                                        {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                      "finishReason": "stop"}})
        report = document_validator.judge_section(paths, "heading-031-1", scope, [])
        harness.check(f"{label}: {'refused' if is_refused else 'passes'}",
                      ("DOCUMENT_CODE_LEFT_OUT" in harness.codes(report)) == is_refused,
                      str([finding.code for finding in report.findings]))


def test_a_web_address_worded_as_what_the_slide_showed_is_never_listed_to_set_aside_defect():
    # Defect: a web address note worded "displayed as a link on the slide" was listed among the notes to set aside,
    # beside the line saying a web address is never set aside. Fix: the kinds never set aside stay off that list.
    entries = [{"id": "F7.1", "kind": "url", "text": "https://acme.example/docs | the team's docs | displayed on the slide"},
               {"id": "F7.2", "kind": "speech_visual_alignment",
                "text": "The presenter points at the links shown on the slide."}]
    section = {"heading": "Contacts", "topicId": "T05.4", "entries": entries}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 5, "chapterCount": 5, "chapterTitle": "Contacts",
                                      "documentTitle": "Contacts"}, findings=[])
    text = document_messages.build_chapter_text(request)
    line = text.split("Notes worded as what happened in the meeting")[1].split("\n")[0] if "Notes worded as" in text else ""
    harness.check("the narrating note is listed, the web address is not", "F7.2" in line and "F7.1" not in line,
                  line or text[-400:])


def test_two_notes_naming_different_things_are_never_named_one_point_defect():
    # Two notes of one figure, an arrow from "Client" and an arrow from "Gateway", shared most words and were named one
    # point; the writer weighed merging them (DEFECTS 501). Now notes naming different things are two points
    section = {"heading": "Architecture", "topicId": "T02.1", "entries": [
        {"id": "F0.3", "kind": "facts", "text": 'A black arrow points directly from "Client" to the second component.'},
        {"id": "F0.5", "kind": "facts", "text": 'A black arrow points directly from "Gateway" to the third component.'},
        {"id": "F0.6", "kind": "facts", "text": 'Directly from "Client", a black arrow points to the second component.'}]}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 2, "chapterCount": 2, "chapterTitle": "Architecture",
                                      "documentTitle": "Guide"}, findings=[])
    line = next((line for line in document_messages.build_chapter_text(request).splitlines() if "may say again" in line), "")
    harness.check("the arrow from another name is no restatement", "F0.5" not in line, line)
    harness.check("the same arrow said again still is", "F0.6 (as F0.3)" in line, line)


def test_a_note_restating_an_earlier_note_of_the_chapter_is_named_beside_it_defect():
    # a chapter wrote one point twice, from two notes saying it in other words under two headings; the later heading
    # now names its note beside the earlier one, to be written once with both ids
    first = {"heading": "Radio module", "topicId": "T01.1", "entries": [
        {"id": "F3.1", "kind": "facts", "text": "The Gateway replaced its discontinued 5V radio module with a 3.3V one."}]}
    later = {"heading": "Board revision", "topicId": "T01.2", "entries": [
        {"id": "F4.2", "kind": "facts", "text": "The discontinued 5V radio module of the Gateway was replaced with a 3.3V module."},
        {"id": "F4.3", "kind": "facts", "text": "The board takes a 2 A supply from a USB port of the host."}]}
    request = SimpleNamespace(inputs={"sections": [first, later], "chapter": 1, "chapterCount": 1, "chapterTitle": "Hardware",
                                      "documentTitle": "Hardware"}, findings=[])
    text = document_messages.build_chapter_text(request)
    line = next((line for line in text.splitlines() if "may say again" in line), "")
    harness.check("the restating note is named with the earlier one, the new fact is not",
                  "F4.2 (as F3.1)" in line and "F4.3" not in line and "write it once and cite both ids" in line, line)


def test_a_markdown_file_holding_a_code_block_is_shown_as_one_block_defect():
    # A markdown file on screen held a code block; its ``` closed the note's own fence early and the writer could not
    # write it as one block (DEFECTS 598). Inner fences are shown with tildes, which never close a ``` block
    note = "```markdown\n# Notes\n\nRun this:\n\n```python\nprint(1)\n```\n\nThen go on.\n```"
    line = document_messages._render_entry_line({"id": "F9.4", "kind": "markdown", "text": note}, note)
    harness.check("the inner fences are tildes", "~~~python" in line and "\n~~~\n" in line, line)
    blocks = section_markdown_utils.parse_section(line.split(" ", 2)[2].strip() + "\n[F9.4]\n")
    # Copied as shown, the fence is read as the markdown it holds, its code a code block (DEFECTS 650): a code box of
    # markdown source is no block a reader wants
    harness.check("copied as shown, every block it opens into cites the note, its code a closed code block",
                  blocks and all(block["entry_ids"] == ["F9.4"] for block in blocks)
                  and any(block["kind"] == "code" and block["text"] == "print(1)" and not block.get("is_unclosed")
                          for block in blocks), str(blocks))
    two = "```python\nprint(1)\n```\n```text\n1\n```"
    harness.check("two code blocks in a row are shown as written",
                  document_messages._render_entry_line({"id": "F9.5", "kind": "markdown", "text": two}, two).endswith(two), "")


def test_a_markdown_note_of_several_paragraphs_is_shown_fenced_and_cited_once_defect():
    # A terminal's summary, a markdown note of several paragraphs shown bare, was copied as loose paragraphs and each
    # refused as naming no id, eight checks running (DEFECTS 596). It is shown in a fence; copied so, one ids line
    # covers it
    entry = {"id": "F23.4", "kind": "markdown", "text": "5. Problem Solving:\n- Converted a script\n\n7. Pending Tasks:\n- None."}
    line = document_messages._render_entry_line(entry, entry["text"])
    harness.check("the note is shown inside a markdown fence", "```markdown\n5. Problem Solving:" in line
                  and line.rstrip().endswith("```"), line)
    copied = line.split(" ", 2)[2].strip() + "\n[F23.4]\n"
    blocks = section_markdown_utils.parse_section(copied)
    harness.check("written as shown, every block cites the note", blocks and all(block["entry_ids"] == ["F23.4"]
                                                                                for block in blocks), str(blocks))
    one = {"id": "F23.5", "kind": "markdown", "text": "- a\n- b"}
    harness.check("a note of one paragraph is shown as before",
                  document_messages._render_entry_line(one, one["text"]) == "F23.5 [markdown] - a\n- b", "")
