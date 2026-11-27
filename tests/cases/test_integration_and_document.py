"""Layer 2 and layer 3, REQUIREMENTS.md R3.

Layer 2 is a merge in plain code, tested on inputs that conflict. Layer 3 is written by a model, so
what is tested here is everything a program can check about it: every claim names entries that
exist, no entry was dropped, and the text holds no timestamp, no banned phrase and no repeat.
"""

import json

from support import fixtures as fx
from support import harness
from support import section_fixtures

from models import output_contract
from models.document_spec import DocumentChapterSpec, DocumentSpec, DocumentTopicSpec
from services import call_log_service
from services import document_assembly_service
from utils import document_heading_utils
from services import document_file_service
from services import phase_file_service
from utils import frame_merge_utils
from utils import document_text_utils
from prompts import frames_processing_messages
from utils import mermaid_text_utils
from utils import section_markdown_utils
from validators import document_folder_validator
from validators import document_validator
from models.section_judging_context import SectionJudgingContext
from models.text_check_context import TextCheckContext
from models.validation_report import ValidationReport
from utils import document_block_utils

TRANSCRIPT = {"hasAudio": True, "segments": [
    {"text": " 89 Prozent wollen billigeren Sprit.", "start": 12.0, "end": 16.0, "words": []},
    {"text": " ", "start": 16.0, "end": 16.5, "words": []},
    {"text": " Guten Abend.", "start": 0.0, "end": 2.0, "words": []}]}
FRAMES = {"items": [{"slug": f"frame-{index:03d}", "index": index, "timestamp": index * 6.0} for index in range(4)]}
RECORDS = [
    {"slug": "frame-000", "index": 0, "timestamp": 0.0, "frame": 0, "screen_kind": "title card",
     "facts": ["The evening news opens with a report on fuel prices."],
     "diagram": "", "screen": '- slide: white, the whole frame\n  - text "tagesthemen": centre', "speech_visual_alignment": "", "builds_on": [], "contradicts_earlier": [],
     "open_questions": []},
    {"slug": "frame-002", "index": 2, "timestamp": 12.0, "frame": 2, "screen_kind": "slide",
     "facts": ["89 % of voters want cheaper fuel"], "diagram": "",
     "screen": '- slide: white, the whole frame\n  - text "89 %": centre\n  - text "TAGESTHEMEN": centre', "speech_visual_alignment": "the graphic carries the 89 % figure being read out",
     "builds_on": [], "contradicts_earlier": ["frame 0 noted a green cardigan, here it is grey"],
     "open_questions": []},
    {"slug": "frame-003", "index": 3, "timestamp": 18.0, "frame": 3, "screen_kind": "slide",
     "facts": ["89 % OF VOTERS want cheaper fuel"], "diagram": "",
     "screen": '- slide: white, the whole frame\n  - text "89 %": centre', "speech_visual_alignment": "", "builds_on": ["frame 2: the same poll"], "contradicts_earlier": [],
     "open_questions": []}]
BLOCKED = {"frame-001": "RESPONSE_THINKING_ONLY"}
# The keys a frame item carries, which the model's record does not
ITEM_KEYS = ("slug", "index", "timestamp")


def _merge() -> dict:
    return frame_merge_utils.merge_frame_records(TRANSCRIPT, FRAMES, RECORDS, BLOCKED)


def _write_merge_inputs(paths,
                        records: list[dict]) -> None:
    """Phases 1 to 3's files, which layer 2 is merged from in memory: the transcript, and the frames with each accepted
    frame's notes in its own item."""

    notes = {record["slug"]: record for record in records}
    items = [{**item, "record": {key: value for key, value in notes[item["slug"]].items()
                                 if key not in ITEM_KEYS}, "builtFrom": []}
             if item["slug"] in notes else item for item in FRAMES["items"]]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], TRANSCRIPT)
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {**FRAMES, "items": items})


def _list_texts(merged: dict,
                kind: str) -> list[str]:
    return [entry["text"] for entry in merged["entries"] if entry["kind"] == kind]


OUTLINE_SLUG = "outline-01"


def _write_outline(paths,
                   outline: dict) -> None:
    _write_merge_inputs(paths, RECORDS)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, OUTLINE_SLUG),
                                    {"response": {"outcome": "completed", "parsed": outline, "finishReason": "stop"}})
    document_file_service.write_outline(paths, OUTLINE_SLUG, outline)


def _build_outline(**changes) -> dict:
    ids = [entry["id"] for entry in _merge()["entries"]]
    outline = {"topics": [{"title": "Fuel", "first_entry_id": ids[1], "entry_ids": ids[1:]}], "set_aside_entry_ids": ids[:1],
               "not_established": []}
    outline.update(changes)

    return outline


def _validate_outline(paths):
    return document_validator.validate_outline(paths, OUTLINE_SLUG, [entry["id"] for entry in _merge()["entries"]])


def _build_spec(sections: list[dict],
                not_established: list[str] | None = None) -> DocumentSpec:
    """The document the older cases assemble: one topic, standing alone, under the claim they all use."""

    records = [section_markdown_utils.build_section_record(section_fixtures.render_section(section)) for section in sections]

    return DocumentSpec(title="Fuel prices decide the vote",
                        chapters=[DocumentChapterSpec(title="", topics=[DocumentTopicSpec(title="Fuel", sections=records)])],
                        not_established=not_established or [])


SECTION = {"heading": "Fuel prices", "not_established": [],
           "paragraphs": [{"text": "89 % of voters want cheaper fuel. The figure is the evening's headline.",
                           "entry_ids": ["F0.1", "S0002"]}]}
TOPIC = {"slug": "topic-01", "title": "Fuel", "entry_ids": ["F0.1", "S0002"]}


def _write_section_markdown(paths,
                            markdown: str) -> None:
    """The topic's section as phase 4 leaves it: the answer in its call log and the markdown in its file."""

    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, TOPIC["slug"]),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, TOPIC["slug"], markdown)


def _write_section(paths,
                   section: dict) -> None:
    _write_merge_inputs(paths, RECORDS)
    _write_section_markdown(paths, section_fixtures.build_section_answer(section)["markdown"])


def _list_section_codes(paths) -> set[str]:
    return harness.codes(document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[]))


DIAGRAM_SOURCE = 'flowchart LR\n    A["Poll"] --> B["Fuel price demand"]\n    B --> C["Vote against the government"]'


BARE_NODES = ('flowchart LR\n    "Partner Portal" -->|1 User wants to access| "Gateway OIDC Proxy"\n'
              '    "Gateway OIDC Proxy" -->|2 Call API| C["OIDC Provider (IdP)"]\n    "OIDC Provider (IdP)" --> "LDAP"')


def test_speech_becomes_entries_in_clock_order_and_empty_segments_are_not_entries():
    harness.check("two spoken segments, in order, the blank one dropped",
                  _list_texts(_merge(), "speech") == ["Guten Abend.", "89 Prozent wollen billigeren Sprit."],
                  str(_list_texts(_merge(), "speech")))


def test_the_same_document_from_two_frames_is_one_entry():
    merged = _merge()
    harness.check("one facts entry for frames 2 and 3, spelled as it first was", _list_texts(merged, "facts")
                  == ["The evening news opens with a report on fuel prices.", "89 % of voters want cheaper fuel"],
                  str(_list_texts(merged, "facts")))


def test_a_contradiction_is_reported_never_resolved():
    merged = _merge()
    harness.check("the frame's own contradiction note is collected with where it was seen",
                  merged["contradictions"] == [{"frame": 2, "timestamp": 12.0,
                                                "text": "frame 0 noted a green cardigan, here it is grey"}],
                  str(merged["contradictions"]))


def test_a_frame_with_no_accepted_record_is_a_gap_with_its_reason():
    merged = _merge()
    harness.check("the blocked frame is a gap", merged["gaps"] == [{"slug": "frame-001", "index": 1, "timestamp": 6.0,
                                                                   "reason": "RESPONSE_THINKING_ONLY"}], str(merged["gaps"]))
    harness.check("the counts say so", (merged["counts"]["frames"], merged["counts"]["framesAccepted"]) == (4, 3),
                  str(merged["counts"]))


def test_every_entry_has_a_unique_id_a_kind_and_a_source():
    entries = _merge()["entries"]
    ids = [entry["id"] for entry in entries]
    harness.check("ids are unique", len(ids) == len(set(ids)), str(ids))
    harness.check("a transcript line is S0001 on, in clock order; a frame's note is F<frame>.<n> within its frame",
                  ids == ["S0001", "F0.1", "S0002", "F2.1", "F2.2"], str(ids))
    harness.check("every entry names where it came from", all(entry["source"] in ("transcript", "frame")
                                                              for entry in entries))


def test_a_video_whose_frames_all_failed_still_merges_from_the_transcript():
    merged = frame_merge_utils.merge_frame_records(TRANSCRIPT, FRAMES, [], {})
    harness.check("speech entries survive", len(_list_texts(merged, "speech")) == 2)
    harness.check("every frame is a gap", len(merged["gaps"]) == 4 and merged["counts"]["framesAccepted"] == 0)


def test_the_text_checks_find_what_layer_3_may_not_contain():
    harness.check("a clock time is found", document_text_utils.find_timestamps("At 1:23 prices rose.") == ["1:23"])
    harness.check("a port mapping is no recording position, its second number not being minutes",
                  document_text_utils.find_timestamps("Publish the container as 80:80 and 8080:80.") == [],
                  str(document_text_utils.find_timestamps("Publish the container as 80:80 and 8080:80.")))
    harness.check("a dated clock time is meeting metadata, not recording provenance",
                  document_text_utils.find_timestamps("Recorded on 2026-03-03 at 17:03 UTC.") == [])
    harness.check("a clock time with a time zone after it is a time of day, however the date is written",
                  document_text_utils.find_timestamps("Recorded on March 3, 2026, at 17:03 UTC, and again at 9:15 CET.")
                  == [], document_text_utils.find_timestamps("Recorded on March 3, 2026, at 17:03 UTC, and again at 9:15 CET."))
    harness.check("a clock time after a date written day first is data, one alone is still found",
                  document_text_utils.find_timestamps("The file changed on 25.11.2025 14:30. At 12:05 the export starts.")
                  == ["12:05"])
    harness.check("a clock time after a date named by its month is data",
                  document_text_utils.find_timestamps("Closed from October 18th at 23:30 until 19 October, 05:00, "
                                                      "and from 18.10. - 23:30.") == [])
    harness.check("a file's line and column in a linter's output are no clock time; a time beside them is still found",
                  document_text_utils.find_timestamps("EM101 at utils.py:25:26 and S602 at utils.py:32:12, said at 4:10.")
                  == ["4:10"], str(document_text_utils.find_timestamps("EM101 at utils.py:25:26 and S602 at utils.py:32:12, said at 4:10.")))
    harness.check("a percentage and a year are not clock times",
                  document_text_utils.find_timestamps("89 % in 2026, a ratio of 3 to 1.") == [])
    # A time of day the meeting names is the subject's (2026-10-01: a nightly stop window was rejected as a clock time)
    harness.check("a time of day with AM, PM or o'clock is not a recording position",
                  document_text_utils.find_timestamps("The job stops from 2:00 AM to 5:15 AM.") == [])
    harness.check("an ISO date-time in data is no recording time (2026-09-30: '04:18' was found in "
                  "'2026-03-03T18:04:18.17889Z')",
                  document_text_utils.find_timestamps('"createdOn": "2026-03-03T18:04:18.17889Z"') == [],
                  str(document_text_utils.find_timestamps('"createdOn": "2026-03-03T18:04:18.17889Z"')))
    harness.check("a banned phrase is found whatever its case",
                  document_text_utils.find_banned_phrases("The Video Shows a graphic.") == ["video shows"])
    harness.check("plain statements hold none",
                  document_text_utils.find_banned_phrases("89 % of voters want cheaper fuel.") == [])
    harness.check("a sentence said twice is found once",
                  document_text_utils.find_repeated_sentences("Fuel is dear. Voters are angry. Fuel is dear.")
                  == ["fuel is dear"])
    harness.check("two different sentences are not a repeat",
                  document_text_utils.find_repeated_sentences("Fuel is dear. Fuel is cheap.") == [])


def test_an_outline_that_places_every_entry_exactly_once_is_accepted():
    paths = fx.make_paths()
    _write_outline(paths, _build_outline())
    harness.check("a complete outline raises no finding", _validate_outline(paths).ok,
                  _validate_outline(paths).render_summary())


def test_each_outline_code_fires_on_its_own_defect():
    paths = fx.make_paths()
    ids = [entry["id"] for entry in _merge()["entries"]]

    _write_outline(paths, _build_outline(set_aside_entry_ids=[]))
    harness.check("an entry placed nowhere is DOCUMENT_ENTRY_UNCOVERED",
                  harness.codes(_validate_outline(paths)) == {"DOCUMENT_ENTRY_UNCOVERED"})

    _write_outline(paths, _build_outline(set_aside_entry_ids=[ids[0], "P999"]))
    harness.check("an id that does not exist is DOCUMENT_UNKNOWN_SOURCE",
                  harness.codes(_validate_outline(paths)) == {"DOCUMENT_UNKNOWN_SOURCE"})

    _write_outline(paths, _build_outline(set_aside_entry_ids=ids[:2]))
    harness.check("an entry placed twice is DOCUMENT_ENTRY_TWICE",
                  harness.codes(_validate_outline(paths)) == {"DOCUMENT_ENTRY_TWICE"})

    _write_outline(paths, _build_outline(topics=[], set_aside_entry_ids=ids))
    harness.check("setting everything aside is DOCUMENT_NO_TOPICS",
                  harness.codes(_validate_outline(paths)) == {"DOCUMENT_NO_TOPICS"})


def test_a_section_that_uses_every_entry_and_names_its_sources_is_accepted():
    paths = fx.make_paths()
    _write_section(paths, SECTION)
    codes = _list_section_codes(paths)
    harness.check("a clean section raises no finding", codes == set(), str(codes))


def test_a_block_without_ids_is_quoted_in_its_finding():
    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    _write_section(paths, {**SECTION, "paragraphs": [paragraph, {**paragraph, "text": "Frame-092 is left out.", "entry_ids": []}]})
    report = document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[])
    messages = [str(finding) for finding in report.findings]
    harness.check("the finding quotes the block it means",
                  any("'Frame-092 is left out.'" in message for message in messages), str(messages))


def test_an_unsourced_block_does_not_hide_the_other_findings():
    """Under one attempt a step was refused three times in a row, each refusal naming one finding: an unsourced block
    stopped every later check, so an entry left uncovered was named only on the next ask (2026-10-06)."""

    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    _write_section(paths, {**SECTION, "paragraphs": [{**paragraph, "entry_ids": ["F0.1"]},
                                                      {**paragraph, "text": "The steps follow.", "entry_ids": []}]})
    codes = _list_section_codes(paths)
    harness.check("the unsourced block and the uncovered entries are named together",
                  {"DOCUMENT_PARAGRAPH_UNSOURCED", "DOCUMENT_ENTRY_UNCOVERED"} <= codes, str(codes))


def test_each_section_code_fires_on_its_own_defect():
    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    for label, changed, code in (
            ("a paragraph that names no entry", {**paragraph, "entry_ids": []}, "DOCUMENT_PARAGRAPH_UNSOURCED"),
            ("a bracket naming a whole frame", {**paragraph, "text": paragraph["text"] + " [F0]"}, "DOCUMENT_BRACKET_UNREAD"),
            ("a paragraph citing an entry of another topic", {**paragraph, "entry_ids": ["F0.1", "S0002", "S0001"]},
             "DOCUMENT_UNKNOWN_SOURCE"),
            ("an entry no paragraph uses", {**paragraph, "entry_ids": ["F0.1"]}, "DOCUMENT_ENTRY_UNCOVERED"),
            ("a clock time", {**paragraph, "text": "At 1:23 the figure rose to 89 %."}, "DOCUMENT_TIMESTAMP"),
            ("meta-narration", {**paragraph, "text": "The video shows that 89 % want cheaper fuel."},
             "DOCUMENT_BANNED_PHRASE"),
            ("a claim said twice", {**paragraph, "text": "Fuel is dear. 89 % of voters are angry. Fuel is dear."},
             "DOCUMENT_REPEATED_SENTENCE")):
        _write_section(paths, {**SECTION, "paragraphs": [changed]})
        # A block with no ids leaves its entries uncovered too, and both are named in one answer's refusal
        expected = {code, "DOCUMENT_ENTRY_UNCOVERED"} if code == "DOCUMENT_PARAGRAPH_UNSOURCED" else {code}
        codes = _list_section_codes(paths)
        harness.check(f"{label} is {code}", codes == expected, str(codes))

    _write_section(paths, {**SECTION, "paragraphs": []})
    harness.check("a section with no paragraph is DOCUMENT_SECTION_EMPTY",
                  _list_section_codes(paths) == {"DOCUMENT_SECTION_EMPTY"})


def test_two_lists_sharing_a_line_pass_the_finished_document_check():
    """Two screen lists of one web page passed their chapter, and the finished document failed on a side panel line
    both hold, with no answer left to fix it (lecture-9, 2026-10-21)."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    document_assembly_service.write_document(paths, _build_spec([SECTION], ["Whether prices will keep rising."]), _merge())
    lists = ("- flavor: pcre2, python, golang\n- pattern: a+\n- match information: match 1 0-6 helloa\n\n"
             "Another pattern is tried.\n\n- flavor: pcre2, python, golang\n- pattern: b+\n- match information: match 1 0-6 helloa\n")
    paths.summary.write_text(paths.summary.read_text(encoding="utf-8").replace("## Fuel\n", f"## Fuel\n\n{lists}\n", 1),
                             encoding="utf-8")
    codes = harness.codes(document_validator.validate_document(paths))
    harness.check("two lists sharing lines are no repeated sentence", "DOCUMENT_REPEATED_SENTENCE" not in codes, str(codes))


def test_a_list_said_twice_in_the_finished_document_is_a_warning_not_a_failed_run_defect():
    """Defect: a recording's run failed on its last step because a chapter wrote a screen's API list as its own list and
    again as the screen's, and no model call was left to fix the finished document.
    Fix: the finished document's check notes a repeated sentence; the chapter checks still refuse it."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    document_assembly_service.write_document(paths, _build_spec([SECTION], ["Whether prices will keep rising."]), _merge())
    items = ("- Sonstige Buchungen (Daueraufträge, Vorträge, etc.)\n- Kategorisierungen aller Umsätze (inkl. Fremdbank)\n"
             "- Realtime Saldo-Ermittlung\n- Paginierungsmechanismus\n")
    lists = f"An API holds these parts:\n{items}\nThe plan reads:\n\n- Für die API wird benötigt\n" + items.replace("- ", "  - ")
    paths.summary.write_text(paths.summary.read_text(encoding="utf-8").replace("## Fuel\n", f"## Fuel\n\n{lists}\n", 1),
                             encoding="utf-8")
    report = document_validator.validate_document(paths)
    note_codes = [note.code for note in report.notes]
    harness.check("the repeated list does not reject the document", "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(report),
                  report.render_summary())
    harness.check("the repeated list is named in a warning", note_codes == ["DOCUMENT_REPEATED_SENTENCE"], str(note_codes))
    published = paths.output_root / "published"
    published.mkdir()
    (published / "document.md").write_text(paths.summary.read_text(encoding="utf-8"), encoding="utf-8")
    folder_report = document_folder_validator.validate_document_file(folder=published,
                                                                      document_name="document.md",
                                                                      known_entry_ids=[])
    harness.check("the published folder's check does not reject it either",
                  "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(folder_report), folder_report.render_summary())
    harness.check("the published folder's check names it in a warning",
                  [note.code for note in folder_report.notes] == ["DOCUMENT_REPEATED_SENTENCE"], str(folder_report.notes))


def test_the_table_of_contents_is_not_read_as_a_narrated_sentence_and_a_narrated_paragraph_is():
    """A heading accepted chapters before failed the finished document: the table of contents, read as one run of list
    items, held its words (2026-10-04)."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    document_assembly_service.write_document(paths, _build_spec([SECTION], ["Whether prices will keep rising."]), _merge())
    markdown = paths.summary.read_text(encoding="utf-8")
    contents = (f"## {output_contract.CONTENTS_HEADING}\n\n- [Gateway overview and presenter role]"
                f"(#gateway-overview-and-presenter-role)\n\n")
    paths.summary.write_text(markdown.replace("## Fuel\n", contents + "## Fuel\n", 1), encoding="utf-8")
    codes = harness.codes(document_validator.validate_document(paths))
    harness.check("a heading's words in the table of contents are no narrated sentence",
                  "DOCUMENT_RECORDING_NARRATED" not in codes, str(codes))
    paths.summary.write_text(paths.summary.read_text(encoding="utf-8").replace(
        "## Fuel\n", "## Fuel\n\nThe presenter explains the refund limit.\n", 1), encoding="utf-8")
    codes = harness.codes(document_validator.validate_document(paths))
    harness.check("the same words in a paragraph still are", "DOCUMENT_RECORDING_NARRATED" in codes, str(codes))
    fix = next(finding.fix for finding in document_validator.validate_document(paths).findings
               if finding.code == "DOCUMENT_RECORDING_NARRATED")
    # A worker told an action item goes "into the brackets ... with no sentence of its own" read it against write.md's
    # `> **Action:**` line; only an action item about the recording itself has no line of its own
    harness.check("the fix keeps an action item about the subject on its own line",
                  "keeps its `> **Action:**` or `> **Open:**` line" in fix and "about the recording itself" in fix, fix)


def test_the_document_is_about_the_subject_and_its_provenance_sits_beside_it():
    paths = fx.make_paths()
    merged = _merge()
    _write_merge_inputs(paths, RECORDS)
    document_assembly_service.write_document(paths, _build_spec([SECTION], ["Whether prices will keep rising."]), merged)
    markdown = paths.summary.read_text(encoding="utf-8")
    harness.check("the title is the outline's claim", markdown.startswith("# Fuel prices decide the vote"), markdown[:80])
    harness.check("the section is there, under the heading the table of contents gave it",
                  "## Fuel\n" in markdown and "89 % of voters want cheaper fuel." in markdown, markdown)
    harness.check("the closing section states what was left open",
                  f"## {output_contract.OPEN_POINTS_HEADING}\n" in markdown and "Whether prices will keep rising." in markdown
                  and "None were stated" not in markdown and "### Decisions" not in markdown)
    harness.check("and that part of the frames could not be analysed, without naming what is banned",
                  "could not be analysed" in markdown)
    harness.check("the finished document passes its own validator",
                  document_validator.validate_document(paths).ok,
                  document_validator.validate_document(paths).render_summary())
    provenance = json.loads((paths.output_root / output_contract.DRAFT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("every paragraph's entries carry their timestamps in the provenance file",
                  provenance["paragraphs"][0]["entries"][0]["id"] == "F0.1", str(provenance)[:300])


def test_a_language_lesson_s_document_has_its_headings_in_the_lesson_s_language():
    from services import ingesting_profile_service
    from utils import document_edit_utils

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    transcript_path = paths.output_root / output_contract.TRANSCRIPT_FILE
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.is_file() else {"segments": []}
    transcript_path.write_text(json.dumps({**transcript, "language": "de"}), encoding="utf-8")
    harness.check("a meeting is written in English whatever it was spoken in",
                  ingesting_profile_service.read_document_language(paths.output_root) == "en")
    ingesting_profile_service.write_profile(paths.output_root, "language-lesson", "tag")
    harness.check("a language lesson is written in the language phase 1 heard",
                  ingesting_profile_service.read_document_language(paths.output_root) == "de")
    document_assembly_service.write_document(paths, _build_spec([SECTION]), _merge())
    markdown = paths.summary.read_text(encoding="utf-8")
    harness.check("the program's own headings are in that language", "## Offene Punkte\n" in markdown
                  and f"## {output_contract.OPEN_POINTS_HEADING}\n" not in markdown, markdown[-400:])
    report = document_validator.validate_document(paths)
    harness.check("and the finished document passes its own validator", report.ok, report.render_summary())
    edited = document_edit_utils.add_open_questions(markdown, ["Which article does the noun take?"])
    harness.check("the review adds its open question under that heading",
                  edited.split("## Offene Punkte\n", 1)[1].count("- Which article does the noun take?") == 1, edited[-300:])


def test_each_document_code_fires_on_its_own_defect():
    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    harness.check("no document is DOCUMENT_MISSING",
                  harness.codes(document_validator.validate_document(paths)) == {"DOCUMENT_MISSING"})
    document_assembly_service.write_document(paths, _build_spec([SECTION]), _merge())
    text = paths.summary.read_text(encoding="utf-8")
    paths.summary.write_text(text.replace(f"## {output_contract.OPEN_POINTS_HEADING}\n", "## Other\n"), encoding="utf-8")
    harness.check("a document with no closing section is DOCUMENT_NO_CLOSING_SECTION",
                  harness.codes(document_validator.validate_document(paths)) == {"DOCUMENT_NO_CLOSING_SECTION"})
    paths.summary.write_text(text + "\nSeen in frame 5 at 0:42.\n", encoding="utf-8")
    harness.check("a leaked frame number and clock time are both found",
                  harness.codes(document_validator.validate_document(paths))
                  == {"DOCUMENT_BANNED_PHRASE", "DOCUMENT_TIMESTAMP"})


def test_when_every_frame_failed_the_document_says_it_rests_on_speech_alone():
    paths = fx.make_paths()
    merged = frame_merge_utils.merge_frame_records(TRANSCRIPT, FRAMES, [], {})
    _write_merge_inputs(paths, [])
    ids = [entry["id"] for entry in merged["entries"]]
    section = {**SECTION, "paragraphs": [{**SECTION["paragraphs"][0], "entry_ids": ids}]}
    document_assembly_service.write_document(paths, _build_spec([section]), merged)
    harness.check("the document says none of the frames could be analysed",
                  "None of the visual material could be analysed" in paths.summary.read_text(encoding="utf-8"))


def test_mermaid_is_recognised_with_or_without_its_fence():
    fenced = f"```mermaid\n{DIAGRAM_SOURCE}\n```"
    harness.check("a flowchart is Mermaid", mermaid_text_utils.is_mermaid(DIAGRAM_SOURCE))
    harness.check("so is one inside a mermaid fence", mermaid_text_utils.is_mermaid(fenced))
    harness.check("a list of arrows is not", not mermaid_text_utils.is_mermaid("Poll -> demand -> vote"))
    harness.check("an empty string is not", not mermaid_text_utils.is_mermaid("  "))
    harness.check("the fence comes off", mermaid_text_utils.strip_fence(fenced) == DIAGRAM_SOURCE)
    harness.check("a document's diagram blocks are removed before its text is checked",
                  mermaid_text_utils.remove_blocks(f"Before.\n\n{fenced}\n\nAfter.") == "Before.\n\n\n\nAfter.")


def test_a_section_diagram_uses_its_entries_and_must_be_sourced_mermaid():
    paths = fx.make_paths()
    paragraph = {**SECTION["paragraphs"][0], "entry_ids": ["F0.1"]}
    diagram = {"mermaid": DIAGRAM_SOURCE, "entry_ids": ["S0002"]}
    _write_section(paths, {**SECTION, "paragraphs": [paragraph], "diagrams": [diagram]})
    codes = _list_section_codes(paths)
    harness.check("an entry only a diagram uses is used", codes == set(), str(codes))
    _write_section(paths, {**SECTION, "paragraphs": [paragraph], "diagrams": [{**diagram, "mermaid": "Poll -> vote"}]})
    harness.check("a diagram that is not Mermaid is DOCUMENT_DIAGRAM_NOT_MERMAID",
                  _list_section_codes(paths) == {"DOCUMENT_DIAGRAM_NOT_MERMAID"}, str(_list_section_codes(paths)))
    _write_section(paths, {**SECTION, "paragraphs": [paragraph], "diagrams": [{**diagram, "entry_ids": []}]})
    harness.check("a diagram that names no entry is DOCUMENT_DIAGRAM_UNSOURCED",
                  _list_section_codes(paths) == {"DOCUMENT_DIAGRAM_UNSOURCED"}, str(_list_section_codes(paths)))
    _write_section(paths, {**SECTION, "paragraphs": [paragraph], "diagrams": [{**diagram, "entry_ids": ["S0001"]}]})
    harness.check("a diagram citing another topic's entry is DOCUMENT_UNKNOWN_SOURCE",
                  "DOCUMENT_UNKNOWN_SOURCE" in _list_section_codes(paths), str(_list_section_codes(paths)))


def test_the_document_draws_each_diagram_and_its_labels_are_not_prose():
    paths = fx.make_paths()
    merged = _merge()
    _write_merge_inputs(paths, RECORDS)
    source = DIAGRAM_SOURCE + '\n    C --> D["Results at 22:15"]'
    section = {**SECTION, "diagrams": [{"mermaid": source, "entry_ids": ["S0002"]}]}
    document_assembly_service.write_document(paths, _build_spec([section]), merged)
    markdown = paths.summary.read_text(encoding="utf-8")
    harness.check("the diagram is drawn once, as a mermaid block",
                  markdown.count("```mermaid") == 1 and f"```mermaid\n{source}\n```" in markdown, markdown)
    report = document_validator.validate_document(paths)
    harness.check("a clock time inside a diagram label is not a leaked timestamp", report.ok, report.render_summary())
    provenance = json.loads((paths.output_root / output_contract.DRAFT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("the diagram's entries are in the provenance",
                  provenance["diagrams"][0]["entries"][0]["id"] == "S0002", str(provenance.get("diagrams")))


def test_a_node_written_as_a_bare_label_gets_an_id_mermaid_can_draw():
    fixed = mermaid_text_utils.name_bare_nodes(BARE_NODES)
    harness.check("each bare label becomes an id with its label, the same label always the same id",
                  fixed == ('flowchart LR\n    n1["Partner Portal"] -->|1 User wants to access| n2["Gateway OIDC Proxy"]\n'
                            '    n2["Gateway OIDC Proxy"] -->|2 Call API| C["OIDC Provider (IdP)"]\n'
                            '    C["OIDC Provider (IdP)"] --> n3["LDAP"]'), fixed)
    valid = 'flowchart LR\n    A["Poll"] -->|"a label"| B("Fuel {price}")\n    B -- "link text" --> C{"Vote?"}'
    harness.check("a diagram that already names its nodes is left as it is, quoted link text included",
                  mermaid_text_utils.name_bare_nodes(valid) == valid, mermaid_text_utils.name_bare_nodes(valid))
    sequence = 'sequenceDiagram\n    Portal->>Proxy: "token"'
    harness.check("only a flowchart is touched", mermaid_text_utils.name_bare_nodes(sequence) == sequence)
    broken = 'flowchart LR\n    SP["Service account\n= App registration"] --> P["Portal permission"]\n    P --> R["Request"]'
    harness.check("a line break inside a quoted label becomes Mermaid's <br/>, and no node is split or renamed",
                  mermaid_text_utils.name_bare_nodes(broken)
                  == 'flowchart LR\n    SP["Service account<br/>= App registration"] --> P["Portal permission"]\n'
                     '    P --> R["Request"]', mermaid_text_utils.name_bare_nodes(broken))


def test_every_layer_writes_a_diagram_with_named_nodes():
    merged = frame_merge_utils.merge_frame_records(TRANSCRIPT, FRAMES, [{**RECORDS[1], "diagram": BARE_NODES}], {})
    diagram = next(entry["text"] for entry in merged["entries"] if entry["kind"] == "diagram")
    harness.check("the merge hands phase 4 a diagram Mermaid can draw", diagram.startswith('flowchart LR\n    n1["'),
                  diagram)


def test_the_latest_diagram_a_frame_extends_is_shown_with_named_nodes():
    text = frames_processing_messages.render_latest_diagram({"index": 2, "diagram": BARE_NODES})
    harness.check("a diagram an earlier record wrote with bare labels reaches the next frame drawable",
                  'n1["Partner Portal"]' in text and '\n    "Partner Portal"' not in text, text)
    harness.check("it says that a different flow gets a diagram of its own, so the frame does not have to guess",
                  "a different flow gets a diagram of its own" in " ".join(text.split()), text)
    # Told only to extend it, three batches copied one frame's misread arrows on unchecked (2026-10-13)
    harness.check("it asks for each arrow to be found on this frame and the count written",
                  "can be wrong" in text and "frame 2: diagram checked" in text, text)


def test_a_note_set_aside_counts_as_covered_and_its_reason_is_not_checked_as_prose():
    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    markdown = ("89 % of voters want cheaper fuel. The figure is the evening's headline. [F0.1]\n\n"
                "> **Set aside:** it only says what the video shows on screen. [S0002]\n")
    _write_section_markdown(paths, markdown)
    codes = _list_section_codes(paths)
    harness.check("the section is accepted with its set-aside note covered", codes == set(), str(codes))


def test_a_transcript_line_inside_a_speech_entry_is_filed_under_that_entry():
    """A range of transcript lines, `[S0002-S0003]`, names S0003, a line of the speech entry S0002: it is that entry,
    never a foreign id (2026-09-29: a chapter blocked on a line id its own range produced)."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    markdown = "89 % of voters want cheaper fuel. The figure is the evening's headline. [F0.1, S0002-S0003]\n"
    _write_section_markdown(paths, markdown)
    report = document_validator.judge_section(paths, TOPIC["slug"], SectionJudgingContext(
        entry_ids=TOPIC["entry_ids"], line_owners={"S0002": "S0002", "S0003": "S0002", "F0.1": "F0.1"}), [])
    harness.check("the line is its entry: no finding", harness.codes(report) == set(), str(harness.codes(report)))
    unmapped = document_validator.judge_section(paths, TOPIC["slug"], SectionJudgingContext(entry_ids=TOPIC["entry_ids"]), [])
    harness.check("without the map the same line is foreign", "DOCUMENT_UNKNOWN_SOURCE" in harness.codes(unmapped),
                  str(harness.codes(unmapped)))


def test_an_unread_bracket_names_the_item_it_could_not_read_and_why():
    """Told only that '[S0764, **F171.3**]' could not be read, a worker spent a turn looking for a wrong id; the marks
    around it were the fault (2026-09-30)."""

    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    _write_section(paths, {**SECTION, "paragraphs": [{**paragraph, "text": paragraph["text"] + " [F0.1, **F0.2**]"}]})
    details = " ".join(finding.detail for finding in document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[]).findings
                       if finding.code == "DOCUMENT_BRACKET_UNREAD")
    harness.check("the finding names the item and the marks around it", "'**F0.2**'" in details and "marks" in details, details)


def test_a_persons_account_name_in_the_text_is_refused():
    """A chapter wrote out the presenter's sign-in account name from a note on the login page (2026-10-06): a person's
    account is as personal as their e-mail address."""

    for label, text, is_refused in (
            ("a quoted account name after 'username': refused",
             'The login page has fields for Username and Password, with the username entered as "jdoe42" [F45.2].', True),
            ("signed in 'as' a quoted account: refused", 'The command logs in as "JDOE42" with the token provided.', True),
            ("the field named without a value: accepted", "The login page has fields for Username and Password.", False),
            # 2026-10-08: a bank's word for an account kind, quoted after "account", was refused as a person's account
            ("an account kind quoted after 'account': accepted",
             'The overview card shows the main account ("Checking") and its balance [F30.4].', False),
            ("an id quoted after 'account': refused", 'The transfer leaves account "jdoe.42" for the savings pot.', True),
            # 2026-10-08: an order's reference after "order number" passed; only the German label was known
            ("an order's reference after 'order number': refused",
             'The page title includes the order number "AB0125XYQWEKL7" [F50.5].', True),
            ("the order's reference named, not copied: accepted", "The page title includes the order's reference [F50.5].", False),
            ("a name quoted after 'account name': refused", 'The account name is "Jonas" on the card.', True),
            ("a placeholder: accepted", 'Sign in with `oc login --username=<user>`; the username is "your-user".', False),
            # 2026-10-07: a chapter copied a demo customer's number and name out of a form, and nothing looked
            ("a customer number with its label: refused",
             'The workflow collects personal data, including Customer Number "4415502703 | Jonas Berg" [F63.1].', True),
            ("the same in the meeting's own language: refused",
             "Die Kundennummer 4415502703 steht oben rechts [F63.1].", True),
            # 2026-10-08: a cut's advisor number masked, then written into the text under its own label
            ("a person's full name quoted after 'name': refused",
             'The saved advisor name, "Jonas Berger", is visible in the summary [F67.3].', True),
            ("a person's full name in brackets after 'name': refused",
             "The fields include the advisor's full name (Jonas Berger) [F85.3].", True),
            ("a company's name after 'name': accepted", 'The field "Name gemäß Registereintrag" holds "Acme AG" [F60.1].', False),
            ("an advisor number with its label: refused", "The Beraternummer FKB field holds 12345678 [F56.3].", True),
            ("the advisor number named, not copied: accepted", "The field holds the advisor's number [F56.3].", False),
            ("the field named without its value: accepted",
             "The form holds the customer number and the name of the insured person [F63.1].", False),
            ("a number that is no record: accepted", "A review every 5 years applies to 10.000,00 € contracts.", False),
            # 2026-10-13: a talk's closing slide gave the speaker's social handle for the audience to use
            ("a social handle: accepted", "Jonas can be reached at @jonas@social.example [F9.1].", False),
            ("an address beside it: refused", "Write to jonas@acmesync.example [F9.1].", True)):
        report = ValidationReport(subject="topic-01")
        document_validator.check_text(report, "topic-01", text)
        harness.check(label, ("DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(report)) == is_refused, str(report.findings))


def test_a_token_claim_name_in_code_marks_is_no_persons_account_defect():
    # A chapter describing a sign-in token's claims, `acr` and `session_state` after "user id" and "account", was refused
    # as one person's account names. Now only a value in quotes counts; a quoted account is still refused.

    for label, text, is_refused in (
            ("claim names in code marks: accepted",
             "The token carries the user id claim `sub`, then `acr` and the account's `session_state` [F12.1].", False),
            ("an account in quotes: refused", 'The command logs in as "jdoe42" with the token provided [F12.2].', True)):
        report = ValidationReport(subject="topic-01")
        document_validator.check_text(report, "topic-01", text)
        harness.check(label, ("DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(report)) == is_refused, str(report.findings))


def test_a_persons_email_address_in_the_text_is_refused():
    """A chapter's prose wrote out the signed-in person's e-mail address, copied from a note, while the cuts of the same
    page were masked (2026-09-30)."""

    report = ValidationReport(subject="topic-01")
    document_validator.check_text(report, "topic-01", "The page names the signed-in user jonas.doe@acme.example.com.")
    harness.check("refused, and told to name the role", "DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(report)
                  and any("role" in (finding.fix or "") for finding in report.findings), str(report.findings))
    clean = ValidationReport(subject="topic-01")
    document_validator.check_text(clean, "topic-01", "The page names the signed-in user.")
    harness.check("no address, no finding", "DOCUMENT_PERSONAL_IN_TEXT" not in harness.codes(clean))


def test_a_section_number_after_customer_number_is_no_record_number_defect():
    # A PDF's chapter wrote "customer number selection (2.6.22)", its section's number, and was refused as a record number.
    # Now a record number needs six digits or more; a real customer number under its label is still refused.

    section = ValidationReport(subject="topic-01")
    document_validator.check_text(section, "topic-01", "The steps go on with customer number selection (2.6.22) [S0049].")
    harness.check("a section number passes", "DOCUMENT_PERSONAL_IN_TEXT" not in harness.codes(section),
                  str(section.findings))
    number = ValidationReport(subject="topic-01")
    document_validator.check_text(number, "topic-01", "The customer number 4415 5027 is shown [F63.1].")
    harness.check("a customer number is still refused", "DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(number),
                  str(number.findings))


def test_a_git_remote_address_is_no_persons_email():
    """A chapter quoting a repository's SSH remote, `git@<host>:<owner>/<repository>.git`, was refused as a person's
    address (lecture-9, 2026-10-21)."""

    report = ValidationReport(subject="topic-01")
    document_validator.check_text(report, "topic-01", "Push with `git remote add origin git@github.com:acme/minilib.git`.")
    harness.check("a git remote passes", "DOCUMENT_PERSONAL_IN_TEXT" not in harness.codes(report), str(report.findings))
    person = ValidationReport(subject="topic-01")
    document_validator.check_text(person, "topic-01", "Mail gitta@acme.example.com for access.")
    harness.check("an address starting with the same letters is still a person's",
                  "DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(person), str(person.findings))


def test_a_nested_list_stays_one_block_with_its_indentation():
    """The published contents lost its nesting: each indented heading became a paragraph of its own (2026-10-01)."""

    markdown = "## Contents\n\n- [Refunds](#refunds)\n  - [Limits](#limits)\n  - [Approvals](#approvals)\n- [Orders](#orders)\n"
    blocks = [block for block in document_block_utils.split_into_blocks(markdown, {}) if block["kind"] != "heading"]
    harness.check("the contents is one list block, its headings indented under their chapter",
                  len(blocks) == 1 and blocks[0]["kind"] == "list" and "\n  - [Limits](#limits)" in blocks[0]["text"],
                  str(blocks))


def test_a_note_stating_a_decision_is_cited_by_a_marked_decision():
    """Two decisions the slides labelled were written as prose, and the closing section said none was stated
    (2026-10-01)."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    owners = {"S0002": "S0002", "F0.1": "F0.1"}
    for markdown, is_rejected in (("89 % of voters want cheaper fuel. [F0.1, S0002]\n", True),
                                  ("> **Decision:** Fuel duty is cut. [F0.1]\n\nThe figure is the headline. [S0002]\n", False)):
        _write_section_markdown(paths, markdown)
        report = document_validator.judge_section(paths, TOPIC["slug"], SectionJudgingContext(
            entry_ids=TOPIC["entry_ids"], line_owners=owners, decision_ids=["F0.1"]), [])
        harness.check(f"unmarked decision rejected: {is_rejected}",
                      ("DOCUMENT_DECISION_UNMARKED" in harness.codes(report)) == is_rejected, str(harness.codes(report)))


def test_the_open_points_drop_answered_questions_and_group_unexplained_terms():
    """The owner: an empty list adds nothing, an answered question is not open, and terms never explained are one line."""

    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    open_points = ['How many clerks check a batch? About four.', 'The meaning of "GW-2" is not explained.',
                   'The term "Status K" appears in the list but its exact definition remains unclear.',
                   'Which retry runs when the Gateway refuses a batch?']
    document_assembly_service.write_document(paths, _build_spec([SECTION], open_points), _merge())
    closing = paths.summary.read_text(encoding="utf-8").split(f"## {output_contract.OPEN_POINTS_HEADING}\n")[-1]
    harness.check("the answered question is gone", "How many clerks" not in closing, closing)
    harness.check("the unexplained terms are one line, first",
                  closing.lstrip().startswith('- Terms the meeting used and never explained: "GW-2", "Status K".'), closing)
    harness.check("the open question stays", "- Which retry runs when the Gateway refuses a batch?" in closing, closing)


def test_a_sentence_whose_subject_is_the_slide_is_narration():
    """"The slide separates the logical steps from the screen" reached a chapter: it is about the recording."""

    found = document_text_utils.find_narrated_sentences(
        "The slide separates the order steps from the screen they lead to. The diagram shows the order flow. "
        "A slide deck is kept for each client.")
    harness.check("a sentence opening with the slide is narration, a diagram and a slide deck are not",
                  found == ["The slide separates the order steps from the screen they lead to."], str(found))


def test_a_quote_from_a_block_whose_ids_stand_on_their_own_line_finds_them():
    paths = fx.make_paths()
    document_file_service.write_section(paths, "heading-001-1", "The refund is split in three parts.\n\n[F12.1, S0415]\n")
    harness.check("the ids under the block are the quote's sources",
                  document_file_service.find_quote_sources(paths, "The refund is split in three parts.") == ["F12.1", "S0415"],
                  str(document_file_service.find_quote_sources(paths, "The refund is split in three parts.")))


def test_a_chapter_asked_in_ten_parts_lists_part_two_before_part_ten():
    paths = fx.make_paths()
    document_file_service.write_parts(paths, {"points": [{"title": "One"}]})
    for part in range(1, 11):
        document_file_service.write_follow_up_record(paths, f"chapter-01-text-{part}", {"status": "accepted"})
    steps = [slug for slug, _ in document_file_service.list_follow_up_steps(paths) if slug.startswith("chapter-01-text")]
    harness.check("the parts in number order", steps == [f"chapter-01-text-{part}" for part in range(1, 11)], str(steps))


def test_the_part_being_written_and_the_parts_after_it_are_listed_defect():
    # Defect: the steps list held only the parts with a record, so the part being written and the ones after it were
    # missing and the page showed the next chapter as worked on. Now its request and the chapter's part count list them.
    paths = fx.make_paths()
    document_file_service.write_parts(paths, {"points": [{"title": "One"}, {"title": "Two"}]})
    for part in (1, 2):
        document_file_service.write_follow_up_record(paths, f"chapter-01-text-{part}", {"status": "accepted"})
    document_file_service.write_follow_up_request(paths, "chapter-01-text-3", "the third part")
    steps = dict(document_file_service.list_follow_up_steps(paths))
    harness.check("the part asked, with no record, is listed waiting", steps.get("chapter-01-text-3") == "waiting", str(steps))
    harness.check("no whole-chapter step beside its parts", "chapter-01-text" not in steps, str(steps))
    document_file_service.write_chapter_part_count(paths, 1, 4)
    names = [slug for slug, _ in document_file_service.list_follow_up_steps(paths) if slug.startswith("chapter-")
             and "-text" in slug]
    harness.check("every part of the chapter, then the next chapter",
                  names == [f"chapter-01-text-{part}" for part in range(1, 5)] + ["chapter-02-text"], str(names))


def test_a_batch_s_unanswered_questions_are_open_points_of_the_document():
    """A PDF's batch left four questions unanswered that no section marked, and the closing said nothing was left
    open (pdf-01). Each is written without the frames it names."""

    plan = DocumentSpec(title="Refund rules", chapters=[])
    integration = {"batchSummaries": [{"openQuestions": ['What is the "Keyhole" component shown in the diagram (Frame 1)?',
                                                         "Who approves the live key (Frames 3-4)?"]}]}
    lines = document_assembly_service._render_closing_lines(plan, integration, document_heading_utils.select_headings(""))
    harness.check("each unanswered question of a batch is an open point, its frames left out",
                  lines == ['What is the "Keyhole" component shown in the diagram?', "Who approves the live key?"], str(lines))


def test_a_batch_s_question_naming_its_frame_in_words_is_written_without_it_defect():
    # Defect: three open points of a talk named their frame in words ("mentioned in Frame 2"), and the document's banned
    # word check failed phase 4 after every chapter was accepted. Fix: every frame pointer is taken out, not only "(Frame 1)".
    plan = DocumentSpec(title="Refund rules", chapters=[])
    integration = {"batchSummaries": [{"openQuestions": ['What is the help desk "Acme" mentioned in Frame 2?',
                                                         'What does "GW2" stand for, as mentioned in the Frame 19 facts and transcript?',
                                                         'Who owns the refund list on the Frame 19 slide?',
                                                         "Which frame rate does the codec use?"]}]}
    lines = document_assembly_service._render_closing_lines(plan, integration, document_heading_utils.select_headings(""))
    # "on the slide" tells what the recording showed, which the document's own check refuses: that question is left out
    harness.check("no frame is named; the subject's own word stays",
                  lines == ['What is the help desk "Acme"?', 'What does "GW2" stand for?',
                            "Which frame rate does the codec use?"],
                  str(lines))


def test_a_batch_s_question_a_section_marked_open_is_not_listed_twice():
    """pdf-04's open points listed a section's "The identity of "Keyhole" is not explained" and its batch's "What is the
    identity of "Keyhole"?", and so four points twice."""

    plan = DocumentSpec(title="Refund rules", chapters=[],
                        not_established=['The identity of "Keyhole" is not explained in this section.',
                                         "The duration of each step is not specified."])
    integration = {"batchSummaries": [{"openQuestions": ['What is the identity of "Keyhole"?',
                                                         "What is the duration of each rollout step?",
                                                         "Who approves the live key?"]}]}
    lines = document_assembly_service._render_closing_lines(plan, integration, document_heading_utils.select_headings(""))
    harness.check("a question a section holds is left out, a new one kept",
                  lines[1:] == ["The duration of each step is not specified.", "Who approves the live key?"]
                  and "Keyhole" in lines[0], str(lines))



def test_a_batch_s_question_about_the_recording_is_left_out_of_the_open_points_defect():
    # A batch's open question told what the transcript and a speaker said ("In Frame 100, the transcript quotes ...");
    # the published document failed its last check after every chapter passed. Such a question is now left out
    plan = DocumentSpec(title="Refund rules", chapters=[], not_established=[])
    integration = {"batchSummaries": [{"openQuestions": [
        "In Frame 12, the transcript quotes Speaker B asking about the refund window.",
        "What did Speaker B actually refer to?",
        "Who approves the AcmeSync live key?"]}]}
    lines = document_assembly_service._render_closing_lines(plan, integration, document_heading_utils.select_headings(""))
    harness.check("only the question about the subject is listed", lines == ["Who approves the AcmeSync live key?"],
                  str(lines))

def test_a_batch_s_question_naming_its_sources_in_brackets_stays_without_them_defect():
    # A question whose brackets named who said each value ("(as stated by the presenter)") was left out whole as about
    # the recording, and the point was lost. Now it stays with those brackets taken out
    plan = DocumentSpec(title="Refund rules", chapters=[], not_established=[])
    integration = {"batchSummaries": [{"openQuestions": [
        "Is the refund window 14 days (as stated by the presenter) or 30 days (as shown on the slide)?"]}]}
    lines = document_assembly_service._render_closing_lines(plan, integration, document_heading_utils.select_headings(""))
    harness.check("the question stays without its source brackets", lines == ["Is the refund window 14 days or 30 days?"],
                  str(lines))

def test_a_frame_s_open_question_goes_when_its_batch_asked_its_own_defect():
    # Defect: a 15-minute talk's open points held nine of its frames' own questions, each asking what the next frame
    # showed ("the content behind the QR codes is not detailed yet"). Fix: a batch that asked its open questions with every frame in view replaces its frames' own.
    from services import integration_service

    question = {"kind": "open_question", "frames": [8], "text": "The addresses are not shown yet."}
    harness.check("a question of a frame whose batch asked its own goes", integration_service._is_asked_by_batch(question, {0, 8, 9}))
    harness.check("one of a frame outside such a batch stays", not integration_service._is_asked_by_batch(question, {44}))
    harness.check("a fact stays", not integration_service._is_asked_by_batch({**question, "kind": "facts"}, {8}))


def test_a_note_number_past_its_frame_s_last_is_named_with_the_frame_s_notes_defect():
    # A chapter cited F16.2 where frame 16 has only F16.1, and cited it again after the finding called it only "not this
    # topic's"; the finding now names the frame's notes the topic holds
    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    _write_section(paths, {**SECTION, "paragraphs": [{**paragraph, "entry_ids": ["F0.1", "F0.2", "S0002"]}]})
    report = document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[])
    details = [finding.detail for finding in report.findings if finding.code == "DOCUMENT_UNKNOWN_SOURCE"]
    harness.check("the finding names the frame's own note beside the invented one",
                  bool(details) and "this topic holds F0.1 of that frame, and no F0.2" in details[0], str(details))


def test_a_talk_s_agenda_sentence_is_told_to_be_set_aside_never_reworded_defect():
    # Refused for "The presentation agenda covers five topics", a worker planned "The project documentation covers five
    # areas", a claim no note makes; the fix text now says such a sentence is dropped and its note set aside
    paths = fx.make_paths()
    paragraph = SECTION["paragraphs"][0]
    _write_section(paths, {**SECTION, "paragraphs": [{**paragraph, "text": "The presentation agenda covers three topics."}]})
    report = document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[])
    fixes = [finding.fix for finding in report.findings if finding.code == "DOCUMENT_RECORDING_NARRATED"]
    harness.check("the fix says to set aside, never to reword onto another thing",
                  bool(fixes) and "never reworded as a claim about something else" in fixes[0], str(fixes)[:300])


def test_a_narrated_sentence_after_a_code_block_is_named_by_the_chapter_check_defect():
    # "The presentation structure outlines …" stood after a slide's code block; the chapter check joined the two with one
    # line break, read no sentence starting there, and the finished document failed on it
    paths = fx.make_paths()
    _write_merge_inputs(paths, RECORDS)
    _write_section_markdown(paths, "```text\nRefunds\nLimits\n```\n[F0.1]\n\n"
                                   "The presentation structure outlines two topics: refunds and limits. [F0.1, S0002]\n")
    report = document_validator.validate_section(paths, TOPIC["slug"], TOPIC["entry_ids"], earlier_slugs=[])
    harness.check("the chapter check names it, as the document does",
                  "DOCUMENT_RECORDING_NARRATED" in harness.codes(report), str(harness.codes(report)))


def test_a_block_copied_from_the_screen_keeps_the_screen_words_defect():
    # A slide's list "Own network protocol with Frame serial numbers" was asked for as it stands and refused for its
    # word "frame" (DEFECTS 293); now a block copied from the screen is left out of the banned-word scan, prose is not
    block = "- Own network protocol with Frame serial numbers and CRC checksum"
    report = ValidationReport(subject="heading-002-1")
    document_validator.check_text(report, "heading-002-1", f"The gateway speaks its own protocol [F9.1].\n\n{block}",
                                  parts=TextCheckContext(screen_blocks=[block]))
    harness.check("the screen's word passes", "DOCUMENT_BANNED_PHRASE" not in [f.code for f in report.findings],
                  str([f.detail for f in report.findings]))
    report = ValidationReport(subject="heading-002-1")
    document_validator.check_text(report, "heading-002-1", f"The frame shows the protocol [F9.1].\n\n{block}",
                                  parts=TextCheckContext(screen_blocks=[block]))
    harness.check("the writer's own word is still refused", "DOCUMENT_BANNED_PHRASE" in [f.code for f in report.findings])


def test_a_code_block_with_an_identifier_no_note_spells_is_named_defect():
    # A chapter's code block printed "pgench_accounts_3" for the screen's "pgbench_accounts_3", and every check passed it
    # (release test 2, DEFECTS 440). A word of six characters or more in a fenced block that no note and no sentence of the
    # section spells is named now, with the nearest word the notes spell
    note_words = frozenset({"pgbench_accounts_3", "pgbench_accounts_4", "partitions", "MINVALUE", "MAXVALUE", "VALUES"})
    report = ValidationReport(subject="heading-001-1")
    document_validator.check_code_words(
        report, "heading-001-1", "Partitions are listed. [F1.1]\n\n```sql\npgench_accounts_3 FOR VALUES FROM (MINVALUE)\n```\n",
        note_words)
    detail = " ".join(finding.detail for finding in report.findings)
    harness.check("the misspelt identifier is named with the notes' spelling",
                  "'pgench_accounts_3' (the notes spell 'pgbench_accounts_3')" in detail, detail)
    report = ValidationReport(subject="heading-001-1")
    document_validator.check_code_words(
        report, "heading-001-1", "Partitions are listed. [F1.1]\n\n```sql\npgbench_accounts_3 FOR VALUES FROM (MINVALUE)\n```\n",
        note_words)
    harness.check("an identifier the notes spell is not named", report.findings == [], str(report.findings))
    report = ValidationReport(subject="heading-001-1")
    document_validator.check_code_words(
        report, "heading-001-1", "The tool pg_helper_tool prints it.\n\n```sql\npg_helper_tool --run\n```\n", note_words)
    harness.check("a word the section's own sentence spells is not named either", report.findings == [], str(report.findings))


def test_a_code_block_opened_inside_an_open_one_is_named_defect():
    # A chapter wrote "```markdown", a slide's lines and "```sql" with no closing line between them, in two sections, and
    # the document printed the second fence as text (release test 2, DEFECTS 441)
    report = ValidationReport(subject="heading-001-1")
    document_validator.check_code_fences(
        report, "heading-001-1", "Intro. [F1.1]\n\n```markdown\nA slide line\n\n```sql\nSELECT 1\n```\n")
    detail = " ".join(finding.detail for finding in report.findings)
    harness.check("the unclosed block is named", "opens with `" in detail and "```sql" in detail, detail)
    report = ValidationReport(subject="heading-001-1")
    document_validator.check_code_fences(
        report, "heading-001-1", "Intro. [F1.1]\n\n```markdown\nA slide line\n```\n\n```sql\nSELECT 1\n```\n")
    harness.check("two closed blocks are not", report.findings == [], str(report.findings))


def test_a_fact_that_is_a_line_of_its_own_frame_s_outline_need_not_be_cited_defect():
    # A frame's facts held its terminal's lines one by one, each a text its outline quotes, and each stood as an entry the
    # chapter had to cite: ten findings of the writer's check on one frame (release test 2's frame 71, DEFECTS 435).
    # They may be cited and need not be now; a fact of its own stays required
    from utils import document_plan_utils

    record = {"index": 4, "timestamp": 4.0, "facts": ["The gateway retries a failing call three times.",
                                                       "`# SELECT retries FROM gateway_limits`",
                                                       "`retries | 3`"],
              "screen": '- terminal: black\n  - line "# SELECT retries FROM gateway_limits"\n  - line "retries | 3"',
              "diagram": "", "as_markdown": "", "speech_visual_alignment": "", "open_questions": [], "urls": [],
              "action_items": [], "questions_asked": []}
    entries = frame_merge_utils._add_entry_ids(frame_merge_utils._list_frame_entries([record], {}))
    required = document_plan_utils.list_required_ids(entries)
    harness.check("the fact of its own is required and the two outline lines are not",
                  "F4.1" in required and "F4.2" not in required and "F4.3" not in required, str(required))
    harness.check("the entries are all still there to be cited",
                  [entry["id"] for entry in entries if entry["kind"] == "facts"] == ["F4.1", "F4.2", "F4.3"],
                  str([entry["id"] for entry in entries]))


def test_a_time_a_note_holds_passes_the_whole_document_s_check_defect():
    # A process table's CPU times ("0:09.96") passed every chapter, the notes holding them, and failed the document's
    # own check, which read no note's times. Now it reads them as each section's check does; a time no note holds fails
    records = [{**RECORDS[0], "facts": [*RECORDS[0]["facts"], "The process table lists systemd with CPU time 0:09.96."]},
               *RECORDS[1:]]
    paths = fx.make_paths()
    _write_merge_inputs(paths, records)
    document_assembly_service.write_document(paths, _build_spec([SECTION]), _merge())
    text = paths.summary.read_text(encoding="utf-8")
    paths.summary.write_text(text + "\n```\n1 root 20 0 0:09.96 /usr/lib/systemd/systemd\n```\n", encoding="utf-8")
    harness.check("the note's time is no clock time", "DOCUMENT_TIMESTAMP" not in harness.codes(
        document_validator.validate_document(paths)))
    paths.summary.write_text(text + "\nSeen at 0:42.\n", encoding="utf-8")
    harness.check("a time no note holds still is", "DOCUMENT_TIMESTAMP" in harness.codes(
        document_validator.validate_document(paths)))
