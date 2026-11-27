"""Phase 4 on a meeting too long for one call, REQUIREMENTS.md R3.

An 87-minute meeting gives about 6,000 layer-2 entries, and one outline call handed all of them overflows
the 32,768-token context many times over. So the document is written in parts: from the entries that
carry facts, sorted into topics one chunk of the meeting at a time, arranged into chapters by one
call that sees every topic's title, and written one topic part at a time. What is tested here is what
the program decides: which entries the document is written from, where the chunks are cut, how the
topics of two chunks become one, which diagram a later one supersedes, and the whole path with a
scripted model.
"""

import json
import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx
from support import harness
from support import section_fixtures
from support import merge_inputs
from support import write_mode

import config as app_config
from graph import graph_state
from graph.nodes import write_document_node
from models import output_contract
from models.attempt_request import AttemptRequest
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from prompts import document_messages
from prompts import write_messages
from services import call_log_service
from services import conversation_log_service
from services import chain_dispatch_service
from services import document_file_service
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from schemas import document_outline
from services.run_store_service import RunStore
from utils import frame_merge_utils
from utils import document_plan_utils
from utils import document_text_utils
from validators import document_validator

FINALIZE = output_contract.PHASE_NUMBERS["finalize"]
FIRST_DIAGRAM = 'flowchart LR\n    A["Portal"] --> B["Login service"]'
EXTENDED_DIAGRAM = FIRST_DIAGRAM + '\n    B --> C["Token store"]'
ENTRIES = [
    {"id": "S0001", "kind": "speech", "text": "Guten Abend.", "start": 0.0, "end": 2.0, "timestamps": [0.0]},
    {"id": "F0.1", "kind": "facts", "text": "The login service issues a token.", "timestamps": [5.0], "frames": [0]},
    {"id": "F0.2", "kind": "diagram", "text": FIRST_DIAGRAM, "timestamps": [5.0], "frames": [0]},
    {"id": "F1.1", "kind": "facts", "text": "The token is valid for 30 minutes.", "timestamps": [10.0], "frames": [1]},
    {"id": "F1.2", "kind": "diagram", "text": EXTENDED_DIAGRAM, "timestamps": [10.0], "frames": [1]},
    {"id": "F1.3", "kind": "open_question", "text": "who rotates the signing key", "timestamps": [10.0], "frames": [1]},
    {"id": "S0002", "kind": "speech", "text": "Das ist wichtig.", "start": 11.0, "end": 12.0, "timestamps": [11.0]}]
INTEGRATION = {"schemaVersion": 1, "entries": ENTRIES, "contradictions": [], "gaps": [],
               "unreadSpeech": [{"start": 0.0, "end": 5.0, "why": "before the first frame"}],
               "counts": {"frames": 2, "framesAccepted": 2}}


def test_the_speech_no_model_read_is_marked_as_unread():
    transcript = {"segments": [{"text": "Hallo.", "start": 0.0, "end": 1.0}]}
    frames = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 4.0},
                        {"slug": "frame-001", "index": 1, "timestamp": 9.0},
                        {"slug": "frame-002", "index": 2, "timestamp": 15.0, "repeatOf": "frame-000",
                         "builtFrom": ["frame-000"],
                         "record": {"builds_on": ["frame 0: the same screen, shown again; its notes are reused"]}},
                        {"slug": "frame-003", "index": 3, "timestamp": 20.0, "repeatOf": "frame-001"},
                        {"slug": "frame-004", "index": 4, "timestamp": 25.0, "repeatOf": "frame-000",
                         "builtFrom": ["frame-000", "frame-003"], "record": {"builds_on": ["frame 0: shown again"]}}]}
    records = [{"slug": slug, "index": index, "timestamp": timestamp, "facts": ["a fact"]}
               for slug, index, timestamp in (("frame-000", 0, 4.0), ("frame-002", 2, 15.0), ("frame-003", 3, 20.0),
                                              ("frame-004", 4, 25.0))]
    merged = frame_merge_utils.merge_frame_records(transcript, frames, records, {"frame-001": "blocked"})
    spans = [(span["start"], span["end"]) for span in merged["unreadSpeech"]]
    harness.check("before the first frame, a blocked frame and an earlier run's copied repeat are unread; a repeat "
                  "read again hears its own speech",
                  spans == [(0.0, 4.0), (9.0, 15.0), (15.0, 20.0)], str(merged["unreadSpeech"]))
    nothing = frame_merge_utils.merge_frame_records(transcript, {"items": []}, [], {})
    harness.check("with no frame at all, every word is unread",
                  [(span["start"], span["end"]) for span in nothing["unreadSpeech"]] == [(0.0, None)],
                  str(nothing["unreadSpeech"]))


def test_a_local_file_path_noted_as_an_address_is_a_fact_with_its_user_folder_hidden():
    """A frame's notes put the editor's run line, `C:/Users/<name>/.../lec3.py`, among its web addresses, and every web
    address is written into the text as a link: the user's folder would be published (2026-10-12)."""

    frames = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 4.0}]}
    records = [{"slug": "frame-000", "index": 0, "timestamp": 4.0, "facts": ["a fact"],
                "urls": [{"url": "C:/Users/<name>/Documents/course/lec3.py", "what_it_is": "the lecture's code file"},
                         {"url": "/home/<name>/retry.py", "what_it_is": "a script"},
                         {"url": "https://acme.example.com/settings", "what_it_is": "the settings page"}]}]
    entries = frame_merge_utils.merge_frame_records({"segments": []}, frames, records, {})["entries"]
    by_kind = {}
    for entry in entries:
        by_kind.setdefault(entry["kind"], []).append(entry["text"])
    harness.check("the web address stays an address, the two local paths are facts with the user folder hidden",
                  by_kind.get("url") == ["https://acme.example.com/settings | the settings page"]
                  and "C:/Users/[user]/Documents/course/lec3.py | the lecture's code file" in by_kind.get("facts", [])
                  and "/home/[user]/retry.py | a script" in by_kind.get("facts", [])
                  and "<name>" not in str(entries), str(by_kind))


def test_the_document_is_written_from_document_and_the_speech_no_model_read():
    selected = [entry["id"] for entry in document_plan_utils.select_document_entries(INTEGRATION)]
    harness.check("facts, diagrams, open questions and the unread speech are kept",
                  selected == ["S0001", "F0.1", "F0.2", "F1.1", "F1.2", "F1.3"], str(selected))
    harness.check("speech a frame already read stays in layer 2 only", "S0002" not in selected)
    with_alignment = {**INTEGRATION, "entries": [*INTEGRATION["entries"], {
        "id": "F1.4", "kind": "speech_visual_alignment", "text": "the speech names the token", "timestamps": [10.0], "frames": [1]}]}
    handed = {entry["id"]: entry["kind"] for entry in document_plan_utils.select_document_entries(with_alignment)}
    harness.check("a Speech visual alignment note is an entry the writer is handed (the owner, 2026-09-29)",
                  handed.get("F1.4") == "speech_visual_alignment", str(handed))


def test_all_speech_reaches_the_writer_one_entry_per_frame_or_per_span_no_frame_read():
    """There is no separate check for what was said and never noted: the writer is handed the speech itself. The lines
    a frame heard are one entry under that frame, a span no frame read is one entry, each under its first line's id;
    the lines stay in layer 2 for the review to cite, and what the document does with an entry counts for its lines."""

    lines = [{"id": f"S{number:04d}", "kind": "speech", "text": text, "start": start, "end": start + 1.0,
              "timestamps": [start], "source": "transcript"}
             for number, (text, start) in enumerate((("Hello.", 0.0), ("Today the portal.", 1.0), ("Then tokens.", 2.0),
                                                     ("Heard by a frame.", 6.0), ("Also heard.", 7.0)), start=1)]
    integration = {"entries": lines, "frames": [{"index": 0, "timestamp": 5.0}],
                   "unreadSpeech": [{"start": 0.0, "end": 5.0, "why": "before the first frame"}]}
    speech = [entry for entry in document_plan_utils.select_document_entries(integration) if entry["kind"] == "speech"]
    harness.check("the unread span is one entry, and the lines frame 0 heard are one entry under it",
                  [(entry["id"], entry["text"], entry["segments"]) for entry in speech]
                  == [("S0001", "Hello. Today the portal. Then tokens.", ["S0001", "S0002", "S0003"]),
                      ("S0004", "Heard by a frame. Also heard.", ["S0004", "S0005"])], str(speech))
    statuses = document_plan_utils.list_entry_statuses(integration, cited={"S0001"}, set_aside={"S0004"})
    harness.check("what became of an entry is what became of each of its lines",
                  statuses == {"S0001": "cited", "S0002": "cited", "S0003": "cited", "S0004": "setAside",
                               "S0005": "setAside"}, str(statuses))


def test_the_entries_are_cut_into_chunks_that_never_split_one_moment():
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    chunks = document_plan_utils.split_into_chunks(entries, max_entries=3, max_characters=10_000)
    harness.check("three entries at most per chunk, each moment whole",
                  [[entry["id"] for entry in chunk] for chunk in chunks] == [["S0001", "F0.1", "F0.2"],
                                                                             ["F1.1", "F1.2", "F1.3"]],
                  str([[entry["id"] for entry in chunk] for chunk in chunks]))
    chunks = document_plan_utils.split_into_chunks(entries, max_entries=2, max_characters=10_000)
    harness.check("a moment larger than the limit stays one chunk rather than being split",
                  [[entry["id"] for entry in chunk] for chunk in chunks] == [["S0001"], ["F0.1", "F0.2"],
                                                                             ["F1.1", "F1.2", "F1.3"]],
                  str([[entry["id"] for entry in chunk] for chunk in chunks]))
    chunks = document_plan_utils.split_into_chunks(entries, max_entries=100, max_characters=60)
    harness.check("the character budget cuts too", len(chunks) > 1, str(len(chunks)))
    harness.check("and every entry is in exactly one chunk, in order",
                  [entry["id"] for chunk in chunks for entry in chunk] == [entry["id"] for entry in entries])


def test_a_topic_named_again_in_a_later_chunk_is_one_topic_in_two_parts():
    outlines = [{"topics": [{"title": "Login", "entry_ids": ["F0.1", "F0.2"]}]},
                {"topics": [{"title": "  login ", "entry_ids": ["F1.1"]}, {"title": "Key rotation", "entry_ids": ["F1.3"]},
                            {"title": "Empty", "entry_ids": []}]}]
    topics = document_plan_utils.merge_part_topics(outlines, [1, 1], fold_entries=0)[0]
    harness.check("the same title, whatever its case and spaces, is one topic; an empty topic is none",
                  [(topic["number"], topic["title"], [part["entryIds"] for part in topic["parts"]]) for topic in topics]
                  == [(1, "Login", [["F0.1", "F0.2"], ["F1.1"]]), (2, "Key rotation", [["F1.3"]])], str(topics))
    harness.check("each part knows its chunk", [part["chunk"] for part in topics[0]["parts"]] == [1, 2])


def test_a_diagram_an_extension_contains_is_superseded():
    unrelated = 'flowchart LR\n    X["Batch"] --> Y["Report"]'
    superseded = document_plan_utils.list_superseded_diagrams(
        [FIRST_DIAGRAM, unrelated, f"```mermaid\n{EXTENDED_DIAGRAM}\n```", FIRST_DIAGRAM])
    harness.check("every line of the first is in the extension, so only the extension is drawn; an unrelated "
                  "diagram stays", superseded == {0, 3}, str(superseded))
    harness.check("of two identical diagrams the first is kept",
                  document_plan_utils.list_superseded_diagrams([unrelated, unrelated]) == {1})


def test_banned_phrases_are_whole_words_so_a_framework_is_not_a_frame():
    harness.check("a framework, an iframe and a timeframe pass",
                  document_text_utils.find_banned_phrases("The Spring framework renders an iframe in a timeframe.") == [])
    harness.check("a frame and frames are found",
                  document_text_utils.find_banned_phrases("Seen in frame 5 and in later frames.") == ["frame"])
    harness.check("a banned phrase of two words still is",
                  document_text_utils.find_banned_phrases("The Video Shows a chart.") == ["video shows"])
    harness.check("a customer segment is the subject's word, not the recording's",
                  document_text_utils.find_banned_phrases("Cases are handled per customer segment.") == []
                  and document_text_utils.find_banned_phrases("Should the segment-based model remain?") == [],
                  "the segment")
    harness.check("the recording's own part is still refused",
                  document_text_utils.find_banned_phrases("The segment of the talk about retries.") == ["the segment"])
    harness.check("a frame that is the subject's term, or a label quoted from the tool, is not the recording's",
                  [document_text_utils.find_banned_phrases(text) for text in (
                      "Each call gets its own stack frame.", "The `Global frame` panel lists x and i.",
                      'The panel titled "Global frame" lists x and i.', "A data frame holds the rows.",
                      # English idioms: a framework's guides "revised in their own time frames" (NIST)
                      "The guides are revised in their own time frames.", "It gives a frame of reference.")]
                  == [[], [], [], [], [], []])
    harness.check("the recording's frame outside a quote is still refused",
                  document_text_utils.find_banned_phrases('The frame shows the "Global" panel.') == ["frame"])


def test_a_network_protocol_s_frame_is_the_subject_s_word_defect():
    # A chapter about a radio network's own protocol wrote its "frame sequence numbers" and was refused as naming the
    # recording's frame; a protocol's frame, its header, check or sequence numbers, is the subject's word
    harness.check("a protocol's frame terms pass",
                  [document_text_utils.find_banned_phrases(text) for text in (
                      "A custom protocol uses frame sequence numbers and a CRC checksum.",
                      "Each radio frame carries a frame header and a frame check sequence.",
                      "An Ethernet frame holds at most 1,500 bytes of payload.",
                      "The `setup` command sends a setup frame to the given address.",
                      # a protocol's handshake frame (froscon 3211's chapter 3, DEFECTS 242)
                      "The `select` command waits for a handshake frame from the node.")] == [[], [], [], [], []])
    harness.check("the recording's frame is still refused",
                  [document_text_utils.find_banned_phrases(text) for text in (
                      "The next frame shows the header.", "Frame 12 shows the sequence numbers.")] == [["frame"], ["frame"]])


def _write_part(paths,
                slug: str,
                parsed: dict,
                write) -> None:
    # A section is stated by its parts and stored as the markdown the agent writes.
    if write is document_file_service.write_section:
        parsed = section_fixtures.build_section_answer(parsed)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": parsed, "finishReason": "stop"}})
    write(paths, slug, parsed["markdown"] if write is document_file_service.write_section else parsed)


def _write_markdown(paths,
                    slug: str,
                    parsed: dict) -> None:
    """A section written as markdown the way its writer laid it out, past `build_section_answer`'s shape."""

    document_file_service.write_section(paths, slug, parsed["markdown"])


def _answer_topic_starts(paths,
                         job_kind: str,
                         slug: str,
                         entries: list[dict],
                         answer: dict) -> dict:
    """What the program writes for a model's outline or table of contents that names where each topic starts."""

    request = AttemptRequest(job_kind=job_kind, attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": slug, "entries": entries})
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": answer, "finishReason": "stop"}})
    chain_dispatch_service.write_record(request, answer)
    written = document_file_service.read_outline(paths, slug)
    # The whole table of contents is stored as the agent decided it, and ordered and filled in where it is used
    if job_kind == "table_of_contents":
        harness.check("the stored table of contents holds only the agent's answer, no filled-in ids",
                      set(written) == {"chapters", "set_aside_entry_ids", "not_established"}
                      and all("entry_ids" not in heading for chapter in written["chapters"] for heading in chapter["headings"]),
                      str(written)[:300])

        return document_plan_utils.flatten_table_of_contents(written, entries)

    return written


def _list_kept_ids(entries: list[dict]) -> list[str]:
    """The ids no answer may set aside, as the table of contents' check is handed them."""

    return [entry["id"] for entry in entries if entry["kind"] in document_plan_utils.NEVER_SET_ASIDE_KINDS]


def test_a_topic_is_answered_by_the_entry_it_starts_at_and_the_program_fills_in_the_rest():
    """The model never copies every id: it names where each topic starts, and the program puts every entry from there
    up to the next start into that topic. A subject that comes back is a later start with the same title. The whole
    table of contents names its chapters the same way (the owner, 2026-09-28)."""

    paths = fx.make_paths()
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    topics = [{"title": "Login", "first_entry_id": "F0.1"}, {"title": "Tokens", "first_entry_id": "F1.1"},
              {"title": "Login", "first_entry_id": "F1.3"}]
    answers = {"outline": {"topics": topics},
               "table_of_contents": {"chapters": [{"title": "The login", "first_entry_id": "F0.1", "topics": topics}]}}
    for job_kind, slug in (("table_of_contents", "table-of-contents"), ("outline", "outline-01")):
        written = _answer_topic_starts(paths, job_kind, slug, entries, {
            **answers[job_kind], "set_aside_entry_ids": ["S0001", "F1.2"], "not_established": []})
        harness.check(f"{job_kind}: each topic holds every entry from its start to the next, set-aside ones left out",
                      [(topic["title"], topic["entry_ids"]) for topic in written["topics"]]
                      == [("Login", ["F0.1", "F0.2"]), ("Tokens", ["F1.1"]), ("Login", ["F1.3"])], str(written))
        ids = [entry["id"] for entry in entries]
        report = (document_validator.validate_table_of_contents(paths, slug, entries, _list_kept_ids(entries), 1)
                  if job_kind == "table_of_contents" else document_validator.validate_outline(paths, slug, ids))
        harness.check(f"{job_kind}: and it is accepted", report.ok, report.render_summary())
    harness.check("the answer's schema asks for a start, never a list of ids",
                  set(document_outline.DocumentTopic.model_fields) == {"title", "first_entry_id"})


def test_the_program_orders_the_starts_and_the_first_topic_opens_the_list():
    """A start is a place in the list, so its order is bookkeeping (the owner, 2026-09-28): starts given backwards are
    put in order, and the first topic also holds the entries before its start. Only a start that is no entry is
    refused."""

    paths = fx.make_paths()
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    ids = [entry["id"] for entry in entries]
    written = _answer_topic_starts(paths, "outline", "outline-01", entries, {
        "topics": [{"title": "Tokens", "first_entry_id": "F1.1"}, {"title": "Login", "first_entry_id": "F0.2"}],
        "set_aside_entry_ids": [], "not_established": []})
    harness.check("starts given backwards are put in order, and the first topic holds the entries before its start",
                  [topic["title"] for topic in written["topics"]] == ["Login", "Tokens"]
                  and written["topics"][0]["entry_ids"][0] == ids[0], str(written["topics"]))
    harness.check("and it is accepted", document_validator.validate_outline(paths, "outline-01", ids).ok)
    _answer_topic_starts(paths, "outline", "outline-01", entries, {
        "topics": [{"title": "Login", "first_entry_id": "F99.9"}], "set_aside_entry_ids": [], "not_established": []})
    harness.check("a start that is no entry's id is DOCUMENT_UNKNOWN_SOURCE",
                  "DOCUMENT_UNKNOWN_SOURCE" in harness.codes(document_validator.validate_outline(paths, "outline-01", ids)))


def test_a_heading_under_the_wrong_chapter_lands_in_the_chapter_its_start_falls_in():
    """The run of 2026-09-28: a heading listed under an earlier chapter than its entries. The program files every
    heading by where it starts, and each chapter's start opens a heading."""

    entries = [{"id": f"E{number}"} for number in range(1, 9)]
    table = document_plan_utils.flatten_table_of_contents({"chapters": [
        {"title": "Refunds", "first_entry_id": "E2", "topics": [{"title": "Limits", "first_entry_id": "E1"},
                                                                 {"title": "Screen", "first_entry_id": "E6"},
                                                                 {"title": "Approval", "first_entry_id": "E4"}]},
        {"title": "The new screen", "first_entry_id": "E6", "topics": []}], "set_aside_entry_ids": [], "not_established": []},
        entries)
    harness.check("the first chapter opens the list, the chapters in order",
                  [(chapter["title"], chapter["first_entry_id"]) for chapter in table["chapters"]]
                  == [("Refunds", "E1"), ("The new screen", "E6")], str(table["chapters"]))
    harness.check("each heading holds its entries and sits in the chapter its start falls in",
                  [(topic["title"], topic["entry_ids"], topic["chapter"]) for topic in table["topics"]]
                  == [("Limits", ["E1", "E2", "E3"], 1), ("Approval", ["E4", "E5"], 1), ("Screen", ["E6", "E7", "E8"], 2)],
                  str(table["topics"]))
    harness.check("one outline per chapter, in order", [[topic["title"] for topic in outline["topics"]] for outline in
                                                        document_plan_utils.group_topics_by_chapter(table)]
                  == [["Limits", "Approval"], ["Screen"]])
    empty = document_plan_utils.flatten_table_of_contents({"chapters": [
        {"title": "Refunds", "first_entry_id": "E1", "topics": [{"title": "Limits", "first_entry_id": "E1"}]},
        {"title": "The new screen", "first_entry_id": "E6", "topics": []}]}, entries)
    harness.check("a chapter with no heading gets one under its own title", [topic["title"] for topic in empty["topics"]
                                                                            if topic["chapter"] == 2] == ["The new screen"])


def test_headings_started_inside_one_paragraph_keep_their_subjects_under_the_first():
    """An audio run: a writer started five headings at five lines of one paragraph. Filed under that paragraph, four
    were dropped as empty and the one left named one of five subjects; now the first holds the paragraph and the
    others' titles go with it as subjects, which the chapter request names as subheadings."""

    entries = [{"id": "S0001", "segments": ["S0001", "S0002"]}, {"id": "S0003", "segments": [f"S000{n}" for n in range(3, 8)]},
               {"id": "S0008", "segments": ["S0008"]}]
    table = document_plan_utils.flatten_table_of_contents({"chapters": [
        {"title": "Sprint", "first_entry_id": "S0001", "headings": [
            {"title": "Intro", "first_entry_id": "S0001"}, {"title": "Gateway", "first_entry_id": "S0003"},
            {"title": "Proxy", "first_entry_id": "S0005"}, {"title": "Cleanup", "first_entry_id": "S0006"},
            {"title": "Hotfix", "first_entry_id": "S0008"}]}]}, entries)
    harness.check("one heading per paragraph, the first title kept, the others its subjects",
                  [(topic["title"], topic["entry_ids"], topic.get("subjects")) for topic in table["topics"]]
                  == [("Intro", ["S0001"], None), ("Gateway", ["S0003"], ["Proxy", "Cleanup"]), ("Hotfix", ["S0008"], None)],
                  str(table["topics"]))
    merged, _folds = document_plan_utils.merge_part_topics(document_plan_utils.group_topics_by_chapter(table), [1], 5)
    harness.check("the subjects reach the merged topic", [topic.get("subjects") for topic in merged] == [[], ["Proxy", "Cleanup"], []],
                  str(merged))


def test_a_table_of_contents_needs_one_chapter_per_agenda_point():
    paths = fx.make_paths()
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    ids = [entry["id"] for entry in entries]
    _answer_topic_starts(paths, "table_of_contents", "table-of-contents", entries, {"chapters": [
        {"title": "The login", "first_entry_id": ids[0], "topics": [{"title": "Login", "first_entry_id": ids[0]}]}],
        "set_aside_entry_ids": [], "not_established": []})
    harness.check("one chapter for two agenda points is DOCUMENT_TOC_CHAPTERS",
                  "DOCUMENT_TOC_CHAPTERS" in harness.codes(document_validator.validate_table_of_contents(
                      paths, "table-of-contents", entries, _list_kept_ids(entries), 2)))


def test_draft_headings_are_accepted_only_when_every_chunk_was_read():
    """The run of 2026-09-28 skipped the notes file: finding a chapter's draft headings is now its own follow-up, and the
    program checks from the conversation's log that every chunk of the chapter was read."""

    paths = fx.make_paths()
    slug = "chapter-02"
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": {}, "finishReason": "stop"}})
    document_file_service.write_draft_headings(paths, slug, {"purpose": "Show the tokens.", "headings": [
        {"title": "Token lifetime", "first_entry_id": "F1.1", "what": "A token lasts 30 minutes."}]})
    log = paths.output_root / output_contract.WRITE_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"followUp": slug, "calls": [{"name": "read_notes", "args": {"chapter": 2, "chunk": 1}}]}) + "\n",
                   encoding="utf-8")
    report = document_validator.validate_draft_headings(paths, slug, 2, 2, [{"id": "F1.1"}])
    harness.check("chunk 2 of 2 unread is DOCUMENT_CHAPTER_NOT_READ", harness.codes(report) == {"DOCUMENT_CHAPTER_NOT_READ"},
                  report.render_summary())
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"followUp": slug, "calls": [{"name": "read_notes", "args": {"chapter": 2, "chunk": 2}}]}) + "\n")
    harness.check("the log is read back as the calls the follow-up made",
                  [call["args"]["chunk"] for call in conversation_log_service.list_follow_up_calls(log, slug)] == [1, 2])
    report = document_validator.validate_draft_headings(paths, slug, 2, 2, [{"id": "F1.1"}])
    harness.check("every chunk read, and headings at real entries, is accepted", report.ok, report.render_summary())
    report = document_validator.validate_draft_headings(paths, slug, 2, 2, [{"id": "F9.9"}])
    harness.check("a subject at an entry that does not exist is DOCUMENT_UNKNOWN_SOURCE",
                  "DOCUMENT_UNKNOWN_SOURCE" in harness.codes(report))
    document_file_service.write_draft_headings(paths, slug, {"purpose": "Show the tokens.", "headings": [
        {"title": "The presenter explains token lifetime", "first_entry_id": "F1.1", "what": "A token lasts 30 minutes."},
        {"title": "Refresh limits", "first_entry_id": "F1.1", "what": "A token is refreshed at most twice."}]})
    report = document_validator.validate_draft_headings(paths, slug, 2, 2, [{"id": "F1.1"}])
    summary = " | ".join(finding.detail for finding in report.findings)
    harness.check("a narrating title is quoted alone, never run together with the title after it",
                  "The presenter explains token lifetime" in summary and "Refresh limits" not in summary, summary)


def test_a_long_topic_is_accepted_and_written_in_even_section_parts():
    """A table of contents names subjects, never counts entries: a topic of any size is accepted, and the program
    writes a long one in consecutive sections under its one heading (the owner, 2026-09-28)."""

    paths = fx.make_paths()
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    ids = [entry["id"] for entry in entries]
    _answer_topic_starts(paths, "table_of_contents", "table-of-contents", entries, {"chapters": [
        {"title": "The login", "first_entry_id": ids[0], "topics": [{"title": "Login", "first_entry_id": ids[0]}]}],
        "set_aside_entry_ids": [], "not_established": []})
    report = document_validator.validate_table_of_contents(paths, "table-of-contents", entries, _list_kept_ids(entries), 1)
    harness.check("one topic holding every entry is accepted", report.ok, report.render_summary())
    topics = document_plan_utils.split_long_parts([{"title": "Login", "parts": [
        {"chunk": 1, "entryIds": [f"F{number}.1" for number in range(30)]}, {"chunk": 2, "entryIds": ["F40.1"]}]}], 12)
    harness.check("30 entries at 12 become three even parts, a short part stays whole, the order kept",
                  [len(part["entryIds"]) for part in topics[0]["parts"]] == [10, 10, 10, 1]
                  and [entry_id for part in topics[0]["parts"] for entry_id in part["entryIds"]][:3] == ["F0.1", "F1.1", "F2.1"],
                  str(topics))


def test_the_table_of_contents_is_asked_for_a_size_that_fits_the_meeting():
    entries = [{"id": f"F{minute}.1", "kind": "facts", "text": "A fact.", "timestamps": [minute * 60.0]}
               for minute in range(0, 88, 4)]
    request = AttemptRequest(job_kind="table_of_contents", attempt=1, repository=Path("."), module="m", paths=fx.make_paths(),
                             ignore=IgnoreList(), inputs={"slug": "table-of-contents", "entries": entries,
                                                          "points": [{"number": 1, "title": "Refunds", "firstEntryId": "F0.1"}],
                                                          "draftHeadings": [{"chapter": 1, "purpose": "Explain refunds.", "headings": [
                                                              {"title": "Refund limits", "first_entry_id": "F0.1",
                                                               "what": "Refunds split in three."}]}]})
    text = document_messages.build_table_of_contents_text(request)
    harness.check("an 84-minute meeting is asked for about 16 to 28 headings, with the agenda and the draft headings",
                  "runs 84 minutes, so about 16 to 28 headings" in text and "1. Refunds (from F0.1)" in text
                  and "Refund limits (from F0.1): Refunds split in three." in text and "1 in all" in text, text[-900:])


def test_the_instructions_teach_how_to_make_a_table_of_contents():
    instructions = Path(__file__).resolve().parents[2] / "agent-context" / "instructions"
    outline = (instructions / "outline.md").read_text(encoding="utf-8")
    write = (instructions / "_shared" / "write.md").read_text(encoding="utf-8")
    harness.check("no entry cap on a topic", "at most 12 entries" not in outline and "even for a single entry" not in outline)
    harness.check("a topic is a subject a reader looks up, and asides stay in their topic",
                  "a reader would look up" in outline and "short aside" in outline)
    drafting = (instructions / "draft_headings.md").read_text(encoding="utf-8")
    table = (instructions / "table_of_contents.md").read_text(encoding="utf-8")
    harness.check("the flow: the agenda, the draft headings per chapter, then the whole table of contents as the scaffold",
                  all(phrase in write for phrase in ("The draft headings of each chapter", "the scaffold", "Hold the backbone",
                                                     "Start from your draft headings")))
    harness.check("drafting a chapter's headings reads the whole chapter in one read", "the whole chapter comes back in one read" in drafting)
    harness.check("the table of contents writes chapters and headings and never orders them",
                  "`chapters`: one per agenda point" in table and "nothing\nhas to be counted or ordered" in table)


def test_an_outline_is_judged_against_its_own_chunk():
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    outline = {"topics": [{"title": "Login", "first_entry_id": "F0.1", "entry_ids": ["F0.1", "F0.2"]}],
               "set_aside_entry_ids": ["S0001"],
               "not_established": []}
    _write_part(paths, "outline-01", outline, document_file_service.write_outline)
    report = document_validator.validate_outline(paths, "outline-01", ["S0001", "F0.1", "F0.2"])
    harness.check("every id of the chunk placed once is accepted", report.ok, report.render_summary())
    _write_part(paths, "outline-01", {**outline, "set_aside_entry_ids": ["S0001", "F1.1"]},
                document_file_service.write_outline)
    harness.check("an id of another chunk is DOCUMENT_UNKNOWN_SOURCE",
                  harness.codes(document_validator.validate_outline(paths, "outline-01", ["S0001", "F0.1", "F0.2"]))
                  == {"DOCUMENT_UNKNOWN_SOURCE"})


def test_ids_a_section_cites_in_brackets_are_moved_into_its_entry_ids():
    paths = fx.make_paths()
    request = AttemptRequest(job_kind="section", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "heading-001-1"})
    chain_dispatch_service.write_record(request, {
        "markdown": "The token carries a roles claim [F24.7]. Entra ID issues it [F24.6, S2419].\n"})
    paragraph = document_file_service.read_section(paths, "heading-001-1")["paragraphs"][0]
    harness.check("the bracketed ids leave the text, which reads cleanly",
                  paragraph["text"] == "The token carries a roles claim. Entra ID issues it.", paragraph["text"])
    harness.check("and every one of them is in entry_ids, once, in the order cited",
                  paragraph["entry_ids"] == ["F24.7", "F24.6", "S2419"], str(paragraph["entry_ids"]))
    harness.check("a bare id is left for the validator to reject: the program never guesses where it belongs",
                  document_text_utils.find_entry_ids("See F20.4 for the rest.") == ["F20.4"])


def test_what_an_outline_or_a_section_leaves_open_is_checked_like_its_prose():
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    outline = {"topics": [{"title": "Login", "first_entry_id": "F0.1", "entry_ids": ["F0.1", "F0.2"]}],
               "set_aside_entry_ids": ["S0001"],
               "not_established": ["frame 56: stated the AAD team creates the application; this frame states the owner does."]}
    _write_part(paths, "outline-01", outline, document_file_service.write_outline)
    harness.check("an outline's open point that names a frame is DOCUMENT_BANNED_PHRASE, before it reaches the document",
                  harness.codes(document_validator.validate_outline(paths, "outline-01", ["S0001", "F0.1", "F0.2"]))
                  == {"DOCUMENT_BANNED_PHRASE"})
    _write_part(paths, "outline-01", {**outline, "not_established": ["Whether the AAD team or the owner creates the "
                                                                     "application is not settled."]},
                document_file_service.write_outline)
    harness.check("stated about the subject, it is accepted",
                  document_validator.validate_outline(paths, "outline-01", ["S0001", "F0.1", "F0.2"]).ok)
    section = {"heading": "Lifetime", "not_established": ["Seen in frame 5 only."],
               "paragraphs": [{"text": "A token is valid for 30 minutes.", "entry_ids": ["F1.1"]}]}
    _write_part(paths, "heading-001-1", section, document_file_service.write_section)
    harness.check("a section's open point that names a frame is DOCUMENT_BANNED_PHRASE",
                  harness.codes(document_validator.validate_section(paths, "heading-001-1", ["F1.1"], earlier_slugs=[]))
                  == {"DOCUMENT_BANNED_PHRASE"})


CONTENTS = {"title": "Tokens secure the portal login"}


def _list_contents_codes(paths,
                         contents: dict) -> set[str]:
    _write_part(paths, "contents", contents, lambda paths, slug, parsed: document_file_service.write_contents(paths, parsed))

    return harness.codes(document_validator.validate_contents(paths))


def test_the_title_call_writes_one_claim_and_arranges_nothing():
    paths = fx.make_paths()
    harness.check("a title stating a claim is accepted", _list_contents_codes(paths, CONTENTS) == set())
    harness.check("chapters an older answer still carries are ignored: the parts are the chapters (R9)",
                  _list_contents_codes(paths, {**CONTENTS, "chapters": [{"title": "A", "topic_numbers": [9]}]}) == set())
    harness.check("an empty title is DOCUMENT_TITLE_MISSING",
                  _list_contents_codes(paths, {"title": "  "}) == {"DOCUMENT_TITLE_MISSING"})
    harness.check("a title that narrates the recording is DOCUMENT_BANNED_PHRASE",
                  _list_contents_codes(paths, {**CONTENTS, "title": "The video shows a login"})
                  == {"DOCUMENT_BANNED_PHRASE"})


def test_an_open_line_that_gives_its_answer_is_refused():
    """A chapter marked a question as open and gave its answer after it; the closing left it out as answered, and the
    document said nothing was left open under the open line (lecture-9 chapter 10, 2026-10-21)."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    section = {"heading": "Login", "not_established": ["Who rotates the key? The security team does, weekly."],
               "paragraphs": [{"text": "The login service issues a token.", "entry_ids": ["F0.1"]}]}
    _write_part(paths, "heading-001-1", section, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-1", ["F0.1"], earlier_slugs=[])
    harness.check("an open line with its answer is DOCUMENT_OPEN_ANSWERED", "DOCUMENT_OPEN_ANSWERED" in harness.codes(report),
                  report.render_summary())
    _write_part(paths, "heading-001-1", {**section, "not_established": ["Who rotates the key?"]},
                document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-1", ["F0.1"], earlier_slugs=[])
    harness.check("a question with no answer passes", "DOCUMENT_OPEN_ANSWERED" not in harness.codes(report),
                  report.render_summary())


def test_an_open_line_in_the_asked_and_answered_form_is_refused_defect():
    # Defect: the check's own fix asks for "Asked whether <question>, the team answered that <answer>", which holds no
    # "?", so a Q&A's open lines in that form passed and stood in the closing's open points. Fix: that form is an answer.
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    section = {"heading": "Login", "not_established": ["Asked how often the key rotates, the security team answered that "
                                                       "it rotates weekly."],
               "paragraphs": [{"text": "The login service issues a token.", "entry_ids": ["F0.1"]}]}
    _write_part(paths, "heading-001-1", section, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-1", ["F0.1"], earlier_slugs=[])
    harness.check("the asked and answered form is DOCUMENT_OPEN_ANSWERED", "DOCUMENT_OPEN_ANSWERED" in harness.codes(report),
                  report.render_summary())
    harness.check("nobody answering leaves it open", not document_text_utils.is_answered_question(
        "Asked how often the key rotates, nobody answered that it rotates weekly."))
    harness.check("a line that only reports the question is no answer", not document_text_utils.is_answered_question(
        "Asked how often the key rotates, the team took the question away."))


def test_a_question_inside_a_parenthesis_is_no_answered_point():
    """An open point ending on its own options in a parenthesis was read as a question answered by ")" (pdf-04)."""

    harness.check("a parenthesised question is no answered point",
                  not document_text_utils.is_answered_question(
                      'The source of the "Retry-After: 60" header is not specified (gateway or ledger?) [F3.5]'))
    harness.check("a question and its answer still is",
                  document_text_utils.is_answered_question("Who rotates the key? The security team does, weekly."))
    # pdf-06: "Nobody knew." after the question was read as its answer
    harness.check("a question nobody could answer stays open",
                  not document_text_utils.is_answered_question("Open question: does a key change? Nobody knew.")
                  and not document_text_utils.is_answered_question("Is it final? Not decided yet."))
    harness.check("an answer that starts with nobody is still an answer",
                  document_text_utils.is_answered_question("Who objected? Nobody objected to the plan."))


def test_an_open_point_an_earlier_section_holds_is_said_earlier():
    """One open point under two headings of a chapter passed each section and failed the finished document as a repeated
    sentence, with no answer left to fix it (2026-10-06)."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    first = {"heading": "Login", "not_established": ["Who rotates the key?"],
             "paragraphs": [{"text": "The login service issues a token.", "entry_ids": ["F0.1"]}]}
    _write_part(paths, "heading-001-1", first, document_file_service.write_section)
    later = {"heading": "Lifetime", "not_established": ["Who rotates the key?"],
             "paragraphs": [{"text": "It is valid for 30 minutes.", "entry_ids": ["F1.1"]}]}
    _write_part(paths, "heading-001-2", later, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("an open line an earlier section holds is DOCUMENT_SAID_EARLIER",
                  "DOCUMENT_SAID_EARLIER" in harness.codes(report), report.render_summary())
    # 2026-10-08: told to "drop the sentence", a worker kept a repeated open line three times and moved its ids only
    fix = next((finding.fix for finding in report.findings if finding.code == "DOCUMENT_SAID_EARLIER"), "")
    harness.check("the fix says to delete the whole marked line", "delete the whole `> **Open:**` line" in fix, fix)
    _write_part(paths, "heading-001-2", {**later, "not_established": ["Who renews it?"]}, document_file_service.write_section)
    other = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("another open point passes", "DOCUMENT_SAID_EARLIER" not in harness.codes(other), other.render_summary())


def test_a_section_check_reads_a_code_block_as_one_sentence():
    """The repeat check read a code block between its fences only in the finished document: the section's record holds
    its text without them, and a workflow file repeating its checkout step in each job was refused four checks in a row
    (lecture-9 chapter 7, 2026-10-21)."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    workflow = ("```yaml\njobs:\n  test:\n    steps:\n      - uses: actions/checkout@v5\n      - run: hatch test\n"
                "  fmt:\n    steps:\n      - uses: actions/checkout@v5\n      - run: hatch fmt --check\n```\n[F1.1]\n")
    markdown = f"The workflow runs two jobs. [F1.1]\n\n{workflow}"
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, "heading-001-1", markdown)
    report = document_validator.validate_section(paths, "heading-001-1", ["F1.1"], earlier_slugs=[])
    harness.check("one block repeating its own line passes the section's repeat check",
                  "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(report), report.render_summary())


def test_a_section_may_not_say_again_what_an_earlier_section_said():
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    first = {"heading": "Login", "not_established": [],
             "paragraphs": [{"text": "The login service issues a token.", "entry_ids": ["F0.1"]}]}
    _write_part(paths, "heading-001-1", first, document_file_service.write_section)
    later = {"heading": "Lifetime", "not_established": [],
             "paragraphs": [{"text": "The login service issues a token. It is valid for 30 minutes.",
                             "entry_ids": ["F1.1"]}]}
    _write_part(paths, "heading-001-2", later, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("a sentence an earlier section holds is DOCUMENT_SAID_EARLIER",
                  harness.codes(report) == {"DOCUMENT_SAID_EARLIER"}, report.render_summary())
    harness.check("and the finding quotes it", "the login service issues a token" in report.render_hint().casefold(),
                  report.render_hint())
    _write_part(paths, "heading-001-2", {**later, "paragraphs": [{"text": "It is valid for 30 minutes.",
                                                                "entry_ids": ["F1.1"]}]},
                document_file_service.write_section)
    harness.check("only what is new is accepted",
                  document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"]).ok)


def test_a_code_block_an_earlier_section_holds_is_said_earlier():
    """A code block printed under two headings, each followed by a paragraph, passed every chapter and failed the
    finished document: the section's blocks were joined by one line break, so the code ran on into the next paragraph
    as one sentence and matched nothing (lecture-1, 2026-10-14)."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    code = "```python\nfloat(3)\nint(3.9)\nround(3.9)\n```\n[F0.1]"
    for slug, markdown in (("heading-001-1", f"{code}\n\nThe login service issues a token. [F0.1]\n"),
                           ("heading-001-2", f"{code.replace('F0.1', 'F1.1')}\n\nIt is valid for 30 minutes. [F1.1]\n")):
        _write_part(paths, slug, {"markdown": markdown}, _write_markdown)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("the repeated code block is DOCUMENT_SAID_EARLIER",
                  "DOCUMENT_SAID_EARLIER" in harness.codes(report), report.render_summary())


def test_a_table_repeating_an_earlier_one_but_for_a_new_row_names_the_row_defect():
    # A table an earlier section held but for one new row was refused by one old row five checks running, and the writer
    # never saw that the table was the repeat. Now the finding names the block as held and lists the row it adds
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    rows = "| Gateway | 4 |\n| Ledger | 2 |\n| Refunds | 7 |\n| Tokens | 1 |"
    first = f"| Service | Calls |\n|---|---|\n{rows}\n\n[F0.1]"
    again = f"| Service | Calls |\n|---|---|\n| Payouts | 3 |\n{rows}\n\n[F1.1]"
    _write_part(paths, "heading-001-1", {"markdown": f"{first}\n\nThe login service issues a token. [F0.1]\n"}, _write_markdown)
    _write_part(paths, "heading-001-2", {"markdown": f"{again}\n\nIt is valid for 30 minutes. [F1.1]\n"}, _write_markdown)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    said = [finding for finding in report.findings if finding.code == "DOCUMENT_SAID_EARLIER"]
    harness.check("one finding names the table as held and its one new row",
                  len(said) == 1 and "repeats a block" in said[0].detail and "payouts" in said[0].detail.casefold(),
                  report.render_summary())


def test_a_repeated_block_differing_by_an_elision_line_adds_nothing_defect():
    # Defect: a diff repeated from an earlier section but for a "..." line was named as adding "...", and the writer was
    # told to write that line as a block of its own (Lecture 7's chapter 1, DEFECTS 587). Fix: an elision line is no
    # content, so the block adds nothing and is cited by the earlier one
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    lines = "+import argparse\n+import re\nfrom urllib.request import urlopen\ndef refund(order) -> int:"
    first = f"```python\n{lines}\n```\n[F0.1]"
    again = f"```python\n{lines}\n...\n```\n[F1.1]"
    _write_part(paths, "heading-001-1", {"markdown": f"{first}\n\nThe login service issues a token. [F0.1]\n"}, _write_markdown)
    _write_part(paths, "heading-001-2", {"markdown": f"{again}\n\nIt is valid for 30 minutes. [F1.1]\n"}, _write_markdown)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    said = [finding for finding in report.findings if finding.code == "DOCUMENT_SAID_EARLIER"]
    harness.check("the block is named as adding nothing, never as adding '...'",
                  len(said) == 1 and "adds nothing" in said[0].detail and "adds only" not in said[0].detail,
                  report.render_summary())


def test_a_code_block_extending_an_earlier_one_is_not_said_earlier():
    """The chapter request asks a block written as it stands until one earlier block holds 60% of its lines; the check
    refused a file extended by new functions for the one function an earlier chapter held, and the writer went round
    between the two (lecture-9 chapter 7, 2026-10-21)."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    first = "```python\ndef refund(order):\n    return order.total\n```\n[F0.1]"
    extended = ("```python\ndef refund(order):\n    return order.total\n\ndef split(order, parts):\n"
                "    return [order.total / parts] * parts\n\ndef retry(call):\n    return call()\n```\n[F1.1]")
    _write_part(paths, "heading-001-1", {"markdown": f"{first}\n\nThe login service issues a token. [F0.1]\n"}, _write_markdown)
    _write_part(paths, "heading-001-2", {"markdown": f"{extended}\n\nIt is valid for 30 minutes. [F1.1]\n"}, _write_markdown)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("a block an earlier one holds under 60% is written as it stands",
                  "DOCUMENT_SAID_EARLIER" not in harness.codes(report), report.render_summary())


def test_two_screen_lists_sharing_a_side_panels_lines_are_no_repeat_but_the_writers_own_list_is():
    """Two lists of one web page, each written as it stands as the chapter request asks, share its side panel's lines,
    and the shared lines were refused as said twice (lecture-9 chapter 9, 2026-10-21). A list of the writer's own
    sentences is still compared line by line."""

    panel = "- flavor: pcre2, python, golang\n- match information: no match found"
    # In the merge's order: a frame's markdown note after its diagram, before its open question
    entries = [*ENTRIES[:3], {"id": "F0.3", "kind": "markdown", "text": f"{panel}\n- pattern: a+", "timestamps": [5.0], "frames": [0]},
               *ENTRIES[3:5], {"id": "F1.3", "kind": "markdown", "text": f"{panel}\n- pattern: b+", "timestamps": [10.0], "frames": [1]},
               {**ENTRIES[5], "id": "F1.4"}, *ENTRIES[6:]]
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, {**INTEGRATION, "entries": entries})
    screen = (f"The tester lists the flavors. [F0.1]\n\n{panel}\n- pattern: a+\n[F0.3]\n\n"
              f"Another pattern is tried. [F1.1]\n\n{panel}\n- pattern: b+\n[F1.3]\n")
    _write_part(paths, "heading-001-1", {"markdown": screen}, _write_markdown)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": screen}, "finishReason": "stop"}})
    report = document_validator.validate_section(paths, "heading-001-1", ["F0.1", "F0.3", "F1.1", "F1.3"], earlier_slugs=[])
    harness.check("two screen lists sharing lines pass", "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(report),
                  report.render_summary())
    own = ("Tokens expire. [F0.1]\n\n- The login service issues a token for every session.\n- Tokens last 30 minutes.\n[F1.1]\n\n"
           "- The login service issues a token for every session.\n- Keys rotate weekly.\n[F0.1]\n")
    _write_part(paths, "heading-001-2", {"markdown": own}, _write_markdown)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-2"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": own}, "finishReason": "stop"}})
    report = document_validator.validate_section(paths, "heading-001-2", ["F0.1", "F1.1"], earlier_slugs=[])
    harness.check("the writer's own list saying a sentence twice is still refused",
                  "DOCUMENT_REPEATED_SENTENCE" in harness.codes(report), report.render_summary())


def test_a_list_copied_from_a_source_paragraph_may_repeat_as_the_source_does():
    """A reference list citing one work twice, copied from its text-only PDF paragraph, was refused as sentences said
    more than once four checks in a row (pdf-real-wikipedia-photosynthesis)."""

    references = "1. Smith J (2001). Light and plants. Green Press.\n2. Smith J (2001). Light and plants. Green Press."
    entries = [*ENTRIES, {"id": "S0003", "kind": "speech", "text": references, "start": 20.0, "end": 22.0,
                          "timestamps": [20.0]}]
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, {**INTEGRATION, "entries": entries})
    markdown = f"The article lists its sources. [F0.1]\n\n{references}\n[S0003]\n"
    _write_part(paths, "heading-001-1", {"markdown": markdown}, _write_markdown)
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown}, "finishReason": "stop"}})
    report = document_validator.validate_section(paths, "heading-001-1", ["F0.1", "S0003"], earlier_slugs=[])
    harness.check("the source's own repeated item is no repeat", "DOCUMENT_REPEATED_SENTENCE" not in harness.codes(report),
                  report.render_summary())


def test_a_block_written_from_part_of_a_note_is_measured_by_the_whole_note():
    """The request asked a code note held under 60% written as it stands; the writer wrote only its part an earlier
    section held, and the check refused that part (lecture-9 chapter 10, 2026-10-21). Both now measure the note."""

    function = "def refund(order):\n    return order.total"
    note = (f"```python\n{function}\n\ndef split(order, parts):\n    return [order.total / parts] * parts\n\n"
            "def retry(call):\n    return call()\n```")
    entries = [*ENTRIES[:5], {"id": "F1.3", "kind": "markdown", "text": note, "timestamps": [10.0], "frames": [1]},
               {**ENTRIES[5], "id": "F1.4"}, *ENTRIES[6:]]
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, {**INTEGRATION, "entries": entries})
    _write_part(paths, "heading-001-1", {"markdown": f"```python\n{function}\n```\n[F0.1]\n\nTokens expire. [F0.1]\n"},
                _write_markdown)
    _write_part(paths, "heading-001-2", {"markdown": f"Refunds are computed in code. [F1.1]\n\n```python\n{function}\n```\n[F1.3]\n"},
                _write_markdown)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1", "F1.3"], earlier_slugs=["heading-001-1"])
    harness.check("the part of a note the request asked written as it stands is not said earlier",
                  "DOCUMENT_SAID_EARLIER" not in harness.codes(report), report.render_summary())


def test_a_later_chapter_is_handed_the_code_tables_and_lists_earlier_sections_wrote():
    paths = fx.make_paths()
    markdown = ("The login service issues a token. [F0.1]\n\n```python\nretries = 3\n```\n[F0.1]\n\n"
                "- one\n- two\n[F0.1]\n")
    _write_part(paths, "heading-001-1", {"markdown": markdown}, _write_markdown)
    blocks = write_document_node._list_written_blocks(paths, ["heading-001-1", "heading-009-1"])
    harness.check("the code and the list are handed on, the paragraph and a missing section not",
                  blocks == ["retries = 3", "- one\n- two"], blocks)


def _load_run_context(model) -> RunContext:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=tmp, module="meeting.mp4", output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(tmp), module="meeting.mp4",
                                        output_root=str(paths.output_root)), budgets={"maxAttempts": 2}, models=[])
    merge_inputs.write_merge_inputs(paths, INTEGRATION)

    return RunContext(repository=tmp, module="meeting.mp4", paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(),
                      model=model)


def _answer(schema: str,
            **fields):
    # A section is stated by its parts and answered as the markdown the agent writes.
    fields = section_fixtures.build_section_answer(fields) if schema == "DocumentSection" else fields
    message = fakes.tool_call(schema, **fields)
    message.response_metadata.update({"done_reason": "stop"})

    return message


PARTS = _answer("DocumentParts", announced_agenda=[],
                points=[{"title": "Signing in", "first_moment": 1, "covers_announced": []}])
SUMMARY = _answer("DocumentSummary", agenda=[{"part_number": 1, "text": "Walks through how the portal signs users in"}],
                  summary=[{"text": "The login service issues the portal a token valid for 30 minutes.",
                            "entry_ids": ["F0.1", "F1.1"]}])
OUTLINES = [_answer("DocumentOutline", topics=[{"title": "Login", "first_entry_id": "F0.1"}],
                    set_aside_entry_ids=["S0001"], not_established=[]),
            _answer("DocumentOutline", topics=[{"title": "login", "first_entry_id": "F1.1"},
                                                {"title": "Key rotation", "first_entry_id": "F1.3"}],
                    set_aside_entry_ids=[], not_established=["Whether tokens can be revoked."])]
SECTIONS = [
    _answer("DocumentSection", heading="Login", not_established=[],
            paragraphs=[{"text": "The login service issues a token to the portal.", "entry_ids": ["F0.1"]}],
            diagrams=[{"mermaid": FIRST_DIAGRAM, "entry_ids": ["F0.2"]}]),
    _answer("DocumentSection", heading="Token lifetime", not_established=[],
            paragraphs=[{"text": "A token is valid for 30 minutes.", "entry_ids": ["F1.1"]}],
            diagrams=[{"mermaid": EXTENDED_DIAGRAM, "entry_ids": ["F1.2"]}]),
    _answer("DocumentSection", heading="Key rotation", not_established=[],
            paragraphs=[{"text": "Who rotates the signing key is not settled.", "entry_ids": ["F1.3", "S0002"]}])]


def _run_phase(ctx: RunContext) -> dict:
    previous = app_config.settings.document.chunk_entries
    app_config.settings.document.chunk_entries = 4
    try:
        update = write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                      {"configurable": {"ctx": ctx}})
    finally:
        app_config.settings.document.chunk_entries = previous

    return update


def _read_call_text(ctx: RunContext,
                    number: int) -> str:
    return ctx.model.calls[number][1].content[0]["text"]


def test_a_heading_id_written_bare_in_a_sentence_is_rejected_defect():
    # Defect: "joining the earlier code structure shown in T01.2" reached the reader with the heading's id in it, though
    # in brackets the id is a link (Lecture 7's chapter 1, and two more over the public runs; DEFECTS 588). Fix: a bare
    # heading id is DOCUMENT_ENTRY_ID_IN_TEXT; one in brackets is not
    harness.check("a bare heading id is found, a bracketed one is not",
                  document_text_utils.find_bare_topic_ids("The refund rules (as established in T07.2) apply [T03.1, F4.2].")
                  == ["T07.2"], str(document_text_utils.find_bare_topic_ids("as established in T07.2 [T03.1]")))
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    section = {"heading": "Lifetime", "not_established": [],
               "paragraphs": [{"text": "A token is valid for 30 minutes, as shown in T01.2.", "entry_ids": ["F1.1"]}]}
    _write_part(paths, "heading-001-1", section, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-1", ["F1.1"], earlier_slugs=[])
    harness.check("a section with a bare heading id in a sentence is DOCUMENT_ENTRY_ID_IN_TEXT",
                  "DOCUMENT_ENTRY_ID_IN_TEXT" in harness.codes(report), report.render_summary())


def test_an_entry_id_written_into_the_text_is_rejected():
    harness.check("an entry id in the prose is found, bracketed or bare",
                  document_text_utils.find_entry_ids("The tier is P2 [S0001]; see F12.3 and [S0004, F7.1].")
                  == ["S0001", "F12.3", "S0004", "F7.1"])
    harness.check("names that only look like one are not: E2E, EC2, E-mail, E5",
                  document_text_utils.find_entry_ids("E2E tests run on EC2, by E-mail, in round E5.") == [])
    harness.check("a citation's article number is no transcript line",
                  document_text_utils.find_entry_ids("doi:10.1016/S0959-4388(02)00301-1, PII S1473550418000423, "
                                                     "ISSN S0074-7696; see S0012.") == ["S0012"])
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    section = {"heading": "Lifetime", "not_established": [],
               "paragraphs": [{"text": "A token is valid for 30 minutes, as F1.1 says.", "entry_ids": ["F1.1"]}]}
    _write_part(paths, "heading-001-1", section, document_file_service.write_section)
    report = document_validator.validate_section(paths, "heading-001-1", ["F1.1"], earlier_slugs=[])
    harness.check("a section that writes a bare entry id into a sentence is DOCUMENT_ENTRY_ID_IN_TEXT",
                  harness.codes(report) == {"DOCUMENT_ENTRY_ID_IN_TEXT"}, report.render_summary())
    _write_part(paths, "heading-001-1", {**section, "paragraphs": [{"text": "A token is valid for 30 minutes.",
                                                                  "entry_ids": ["F1.1"]}]},
                document_file_service.write_section)
    harness.check("with the id only in the brackets that end the block it is accepted",
                  document_validator.validate_section(paths, "heading-001-1", ["F1.1"], earlier_slugs=[]).ok)


def test_a_long_meeting_is_written_in_parts_and_assembled_into_chapters():
    ctx = _load_run_context(fakes.scripted(PARTS, *OUTLINES, _answer("DocumentContents", **CONTENTS), *SECTIONS,
                                           SUMMARY))
    update = _run_phase(ctx)
    harness.check("phase 4 completes", update["phases"]["finalize"] == "complete", str(update))
    harness.check("the parts, two outlines, the title, three section parts and the summary: no separate check of the speech",
                  ctx.model.call_count == 8, f"calls={ctx.model.call_count}")
    harness.check("the first chunk's outline holds its own entries and no later one",
                  "S0001 [speech]" in _read_call_text(ctx, 1) and "F1.1" not in _read_call_text(ctx, 1), _read_call_text(ctx, 1))
    harness.check("the second is told the topics the first made, and is handed the speech its frame heard",
                  "Login" in _read_call_text(ctx, 2) and "F1.1 [facts]" in _read_call_text(ctx, 2)
                  and "S0002 [speech] Das ist wichtig." in _read_call_text(ctx, 2), _read_call_text(ctx, 2))
    harness.check("the title call sees every topic", "Login" in _read_call_text(ctx, 3)
                  and "Key rotation" in _read_call_text(ctx, 3), _read_call_text(ctx, 3))
    harness.check("the topic's second part is handed what its first part already says",
                  "The login service issues a token to the portal." in _read_call_text(ctx, 5), _read_call_text(ctx, 5))
    markdown = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("the document opens with the contents' claim, then its chapter",
                  markdown.startswith("# Tokens secure the portal login\n") and "## Signing in" in markdown, markdown)
    chapter = markdown[markdown.index("## Signing in"):]
    harness.check("both parts of a topic sit under its one heading, before the next topic",
                  chapter.index("### Login") < chapter.index("issues a token to the portal")
                  < chapter.index("valid for 30 minutes") < chapter.index("### Key rotation"), chapter)
    harness.check("the extended diagram is drawn once and the version it contains is not",
                  markdown.count("```mermaid") == 1 and "Token store" in markdown, markdown)
    harness.check("what an outline left open is in the closing section", "Whether tokens can be revoked." in markdown)
    report = document_validator.validate_document(ctx.paths)
    harness.check("the document passes its own validator", report.ok, report.render_summary())
    provenance = json.loads((ctx.paths.output_root / output_contract.DRAFT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("the provenance keeps the superseded diagram, marked",
                  [diagram["superseded"] for diagram in provenance["diagrams"]] == [True, False], str(provenance["diagrams"]))
    harness.check("and the entry set aside", [entry["id"] for entry in provenance["setAside"]] == ["S0001"])

    ctx.store.set_phase("finalize", "blocked")
    update = _run_phase(ctx)
    harness.check("a resumed phase keeps every part that still validates and asks nothing again",
                  update["phases"]["finalize"] == "complete" and ctx.model.call_count == 8,
                  f"{update} calls={ctx.model.call_count}")


def test_a_rejected_title_stops_the_phase_before_any_section_is_asked():
    """The owner, 2026-09-29: "no continue after block in any step"; the calls path once wrote the document on."""

    bad_contents = _answer("DocumentContents", title="The video shows tokens")
    ctx = _load_run_context(fakes.scripted(PARTS, *OUTLINES, bad_contents, bad_contents, *SECTIONS, SUMMARY))
    attempts = ctx.store.read_budget("maxAttempts", app_config.settings.budgets.max_attempts)
    update = _run_phase(ctx)
    harness.check("the phase is blocked, never complete", update["phases"]["finalize"] == "blocked", str(update))
    asked = 1 + len(OUTLINES) + attempts
    harness.check("and nothing after the title call was asked", ctx.model.call_count == asked,
                  f"calls={ctx.model.call_count}, expected {asked}")
    harness.check("so no document is written", not ctx.paths.summary.is_file())


def test_a_resumed_phase_keeps_the_sections_on_disk_and_asks_only_for_what_is_missing():
    bad_section = _answer("DocumentSection", heading="Token lifetime", not_established=[],
                          paragraphs=[{"text": "The video shows the lifetime.", "entry_ids": ["F1.1"]}])
    first_summary = _answer("DocumentSummary", agenda=[{"part_number": 1, "text": "Walks through signing in"}],
                            summary=[{"text": "The login service issues a token.", "entry_ids": ["F0.1"]}])
    second_summary = _answer("DocumentSummary", agenda=[{"part_number": 1, "text": "Walks through signing in"}],
                             summary=[{"text": "A token lives 30 minutes.", "entry_ids": ["F1.1"]}])
    ctx = _load_run_context(fakes.scripted(PARTS, *OUTLINES, _answer("DocumentContents", **CONTENTS), SECTIONS[0],
                                           bad_section, bad_section, SECTIONS[2], first_summary, second_summary))
    first = _run_phase(ctx)
    harness.check("the first life is blocked by the rejected section", first["phases"]["finalize"] == "blocked", str(first))
    # The section written by hand between two lives, as R4 allows: a phase reads the files on disk.
    _write_part(ctx.paths, "heading-001-2", {"heading": "Token lifetime", "not_established": [],
                                           "diagrams": [{"mermaid": EXTENDED_DIAGRAM, "entry_ids": ["F1.2"]}],
                                           "paragraphs": [{"text": "A token is valid for 30 minutes.", "entry_ids": ["F1.1"]}],
                                           "decisions": [], "action_items": []}, document_file_service.write_section)
    ctx.store.set_phase("finalize", "blocked")
    second = _run_phase(ctx)
    summary = document_file_service.read_summary(ctx.paths)
    harness.check("the resume keeps the sections on disk, asks for the rest and the summary, and completes",
                  second["phases"]["finalize"] == "complete" and ctx.model.call_count == 9
                  and summary["summary"][0]["text"] == "The login service issues a token.", f"{ctx.model.call_count} {summary}")


def test_a_cut_an_earlier_section_placed_is_refused_and_a_figure_is_no_repeated_sentence():
    """A later part of one heading placed the cut an earlier part had placed; the whole document then failed as a
    repeated sentence on the figure's line and caption (2026-10-03). The repeat belongs to the section check, and a
    figure is no sentence."""

    from utils import document_text_utils
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    figure = '![The retry form](cut-001 "The form that holds the retry budget")'
    for slug, text in (("heading-001-1", f"The login service issues a token. [F0.1]\n\n{figure}\n"),
                       ("heading-001-2", f"It is valid for 30 minutes. [F1.1]\n\n{figure}\n")):
        call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                        {"response": {"outcome": "completed", "parsed": {"markdown": text},
                                                      "finishReason": "stop"}})
        document_file_service.write_section(paths, slug, text)
    report = document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])
    harness.check("the cut placed again is DOCUMENT_IMAGE_PLACED_TWICE", "DOCUMENT_IMAGE_PLACED_TWICE" in harness.codes(report)
                  and "cut-001" in report.render_hint(), report.render_summary())
    document_file_service.write_section(paths, "heading-001-2", "It is valid for 30 minutes. [F1.1]\n")
    harness.check("without it the section passes that check", "DOCUMENT_IMAGE_PLACED_TWICE" not in harness.codes(
        document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=["heading-001-1"])))
    # One cut twice in one section: the hard figures PDF's crew photo stood as its Figure 1 and its Figure 8
    document_file_service.write_section(paths, "heading-001-2",
                                        f"It is valid for 30 minutes. [F1.1]\n\n{figure}\n\nAgain. [F1.1]\n\n{figure}\n")
    harness.check("one cut placed twice in one section is refused too", "DOCUMENT_IMAGE_PLACED_TWICE" in harness.codes(
        document_validator.validate_section(paths, "heading-001-2", ["F1.1"], earlier_slugs=[])))
    rendered = "![A form](04_writing/images/cuts/cut-001.png)\n*The form that holds the retry budget*\n"
    harness.check("a figure's line and caption shown twice are no repeated sentence",
                  document_text_utils.find_repeated_sentences(rendered + rendered) == [])


def test_a_cut_of_another_heading_s_frame_is_refused():
    """A worker placed a cut of one heading's frame under another heading, captioned as that heading's screen
    (2026-10-04): a cut stands under the heading one of whose notes its frame holds."""

    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    document_file_service.write_image_store(paths, {"cuts": {"cut-001": {"frame": "frame-000"},
                                                             "cut-002": {"frame": "frame-005"}}})
    for cut_id, label in (("cut-001", "a cut of its own frame passes"), ("cut-002", "a cut of a frame with no note here")):
        text = f"The login service issues a token. [F0.1]\n\n![A form]({cut_id} \"The form\")\n"
        call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-1"),
                                        {"response": {"outcome": "completed", "parsed": {"markdown": text},
                                                      "finishReason": "stop"}})
        document_file_service.write_section(paths, "heading-001-1", text)
        codes = harness.codes(document_validator.validate_section(paths, "heading-001-1", ["F0.1"], earlier_slugs=[]))
        is_refused = "DOCUMENT_IMAGE_OFF_HEADING" in codes
        harness.check(label, is_refused == (cut_id == "cut-002"), str(codes))



def test_every_template_ends_with_the_sentence_the_conversation_rewrites_to_its_submit_tool():
    """The conversation swaps "Answer through the function you were given, once." for the follow-up's submit tool; a
    template worded otherwise would leave its worker never told which tool answers."""

    names = sorted(name for name in dir(document_messages) if name.endswith("_HUMAN_MESSAGE"))
    missing = [name for name in names
               if not write_messages._ANSWER_THROUGH.search(getattr(document_messages, name).template)]
    harness.check("every human message template holds the sentence", bool(names) and not missing, str(missing))


def test_an_entry_no_section_has_to_cite_is_kept_beside_the_document_never_missing():
    from utils import document_plan_utils

    integration = {"entries": [
        {"id": "F1.1", "kind": "facts", "text": "A refund is split in three.", "timestamps": [10.0], "frames": [1]},
        {"id": "F1.2", "kind": "speech_visual_alignment", "text": "the speech names the slide", "timestamps": [10.0],
         "frames": [1]}]}
    statuses = document_plan_utils.list_entry_statuses(integration, cited={"F1.1"}, set_aside=set())
    harness.check("the uncited speech visual alignment note is provenance", statuses.get("F1.2") == "provenance",
                  str(statuses))


def test_a_chunk_number_the_tool_refused_is_never_counted_as_read():
    paths = fx.make_paths()
    slug = "chapter-02"
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": {}, "finishReason": "stop"}})
    document_file_service.write_draft_headings(paths, slug, {"purpose": "Show the tokens.", "headings": [
        {"title": "Token lifetime", "first_entry_id": "F1.1", "what": "A token lasts 30 minutes."}]})
    log = paths.output_root / output_contract.WRITE_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"followUp": slug, "calls": [{"name": "read_notes", "args": {"chapter": 2, "chunk": chunk}}]}
            for chunk in (1, "two", 2.5)]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    try:
        codes = harness.codes(document_validator.validate_draft_headings(paths, slug, 2, 2, [{"id": "F1.1"}]))
    except ValueError as error:
        codes = {f"raised: {error}"}
    harness.check("chunk 2 is unread: \"two\" and 2.5 were refused by the tool", codes == {"DOCUMENT_CHAPTER_NOT_READ"},
                  str(codes))


def test_ids_in_doubled_brackets_leave_the_text_whole_defect():
    # Defect: a writer cited "[[S0036]]", the inner bracket was read and taken out, and the published document showed
    # "[]" 185 times. Fix: a doubled bracket of ids is one bracket, taken out whole, its ids cited.
    moved = document_text_utils.move_bracketed_entry_ids(
        {"text": "RuBisCO captures CO₂ [[S0036]]. It is reduced [[F6.3]], [[S0039]].", "entry_ids": []})
    harness.check("no bracket is left, and every id is cited once in order",
                  moved["text"] == "RuBisCO captures CO₂. It is reduced." and moved["entry_ids"] == ["S0036", "F6.3", "S0039"],
                  str(moved))


def test_a_recording_s_chapter_is_told_how_many_headings_its_minutes_call_for_defect():
    # Defect: the guide of one heading per three to five minutes of talk stood only in the table of contents' request,
    # which the program no longer sends; a chapter of a minute and a half got three headings. Fix: each chapter's draft headings request says its minutes and the headings they call for.
    from prompts import document_messages

    short = document_messages._render_chapter_headings({"documentHeadings": None, "chapterMinutes": 1.5})
    long = document_messages._render_chapter_headings({"documentHeadings": None, "chapterMinutes": 12.4})
    harness.check("a chapter under three minutes is one heading", "1.5 minutes" in short and "one heading" in short, short)
    harness.check("a twelve-minute chapter calls for about 2 to 4", "12 minutes" in long and "about 2 to 4 headings" in long, long)
    harness.check("a PDF chapter is told its own headings instead",
                  "minutes" not in document_messages._render_chapter_headings({"documentHeadings": ["Scope"], "chapterMinutes": 2}))


def test_the_draft_headings_request_says_a_line_of_speech_keeps_its_s_id_defect():
    # Defect: a chapter's draft headings started two subjects at "F17.91" and "F17.109", the frame's number joined to the
    # numbers of lines said over it, and were refused as unknown ids. Fix: the request says a start is copied as the notes print it, a line of speech by its S id.
    text = document_messages.DRAFT_HEADINGS_HUMAN_MESSAGE.format(chapter=3, chapter_count=4, title="Highlights",
                                                                start_ids="", document_headings="", previous_findings="")
    harness.check("the request names both id forms and that speech keeps its S id",
                  "`F<frame>.<note>`" in text and "`S<line>`" in text and "never a note of that frame" in text, text)
    listed = document_messages._render_start_ids(["F16.1", "F16.2", "F16.3", "S0059", "S0060", "F17.1", "F17.2", "S0061", "S0090"])
    harness.check("the chapter's ids are listed as runs", "F16.1 to F16.3, S0059 to S0060, F17.1 to F17.2, S0061, S0090" in listed,
                  listed)


def test_a_sentence_whose_subject_is_the_talk_is_narration_defect():
    # "The presentation defines strict boundaries regarding excluded topics" reached a published chapter: only verbs of
    # moving on were named; a sentence whose subject is the talk is now narration whatever its verb
    harness.check("the talk as the subject is named, with any verb",
                  [bool(document_text_utils.find_narration_words(text)) for text in (
                      "The presentation defines strict limits for the scope.", "The lecture concludes after this summary.",
                      "The talk's agenda covers three parts.", "The session runs for twenty minutes.")] == [True] * 4)
    harness.check("a meeting's decision and a process's demonstration are not",
                  [bool(document_text_utils.find_narration_words(text)) for text in (
                      "The meeting decided to move the release to Friday.",
                      "The demonstration of the import uses a dry run first.",
                      "A session token expires after one hour.")] == [False] * 3)


def test_a_name_with_an_initial_is_one_sentence_and_no_copy_of_its_last_word_defect():
    # "John T. Jackalope." was cut after the initial, and its lone "Jackalope." matched a spoken sentence cut the same
    # way: refused as copied speech; an initial is now never a sentence's end
    text = "Some builds ship under the name Jonas T. Gateway. [F4.1]"
    harness.check("the name is not refused as copied speech",
                  document_text_utils.find_copied_speech(text, ["Es gibt eine Version, Jonas T. Gateway."]) == [],
                  str(document_text_utils.find_copied_speech(text, ["Es gibt eine Version, Jonas T. Gateway."])))
    harness.check("a lone spoken word copied whole is still a copy",
                  document_text_utils.find_copied_speech("Back. [F4.1]", ["Back."]) == ["Back."],
                  str(document_text_utils.find_copied_speech("Back. [F4.1]", ["Back."])))


def test_someone_giving_the_talk_at_an_event_is_narration_defect():
    # "Michael presents a talk at FrosCon 19" reached an accepted chapter: no rule named a presenter by name; the act
    # of giving the talk at an event is now narration, whoever gives it
    harness.check("a named presenter giving the talk at an event is named",
                  bool(document_text_utils.find_narration_words("Jonas presents a talk at AcmeConf 7, the open source fair.")))
    harness.check("a meeting's own task to present to someone is not",
                  not document_text_utils.find_narration_words("Jonas gives a presentation to the board on Friday."))


def test_ids_parted_by_semicolons_are_read_defect():
    # A chapter wrote every bracket as [F14.1; F15.2; S0200] and each was refused as unread; semicolons between ids
    # are now read as commas
    text = document_text_utils.write_short_ranges_in_full("Relays switch the outputs. [F14.1; F15.2; S0200]")
    harness.check("the bracket reads as three ids", text.endswith("[F14.1, F15.2, S0200]") and not
                  document_text_utils.find_unread_brackets(text), text)
    spaced = document_text_utils.write_short_ranges_in_full("Relays switch the outputs. [F14.1 F15.2 S0200]")
    harness.check("ids parted by spaces read the same", spaced.endswith("[F14.1, F15.2, S0200]"), spaced)
    ranged = document_text_utils.write_short_ranges_in_full("The steps run in order. [F1.2 - F1.4, S0200]")
    harness.check("a range keeps its ends", "[F1.2-F1.4, S0200]" in ranged or "[F1.2 - F1.4, S0200]" in ranged, ranged)


def test_the_speech_as_the_subject_is_narration_defect():
    # "The speech explains that saving these settings prevents …" reached a chapter draft: the talk-as-subject rule
    # named the presentation and the lecture but not the speech; speech recognition stays the subject's
    harness.check("the speech explaining is narration",
                  bool(document_text_utils.find_narration_words("The speech explains that a reboot keeps the address.")))
    harness.check("speech recognition is not",
                  not document_text_utils.find_narration_words("The speech recognition runs on the device."))


def test_a_banned_word_s_finding_quotes_the_words_round_it_defect():
    # A finding naming only "frame" left a worker reading its whole chapter for it, at a sentence's end ("from
    # frame-055"); the finding now quotes the words round each one
    snippets = document_text_utils.find_banned_snippets(
        "The status line shows the units. It names the values required to verify the radio from frame-055. [F55.5]")
    harness.check("the words round the banned one are quoted", len(snippets) == 1 and "from frame-055" in snippets[0],
                  str(snippets))
    harness.check("the subject's own frame is not",
                  document_text_utils.find_banned_snippets("Each call gets its own stack frame.") == [])


def test_a_time_read_off_a_chart_in_its_description_is_no_recording_position_defect():
    # A chart's description named the wood stove's spike "at approximately 20:00", read off the axis, and was refused as
    # a position in the recording; a figure's description line is the figure's, and a time in prose still is refused
    described = ("![The stove's chart](cut-011 \"The ignition spike\")\n\n"
                 "> **What the figure shows:** a line chart over one evening; the orange line jumps at about 20:00.")
    harness.check("the axis time in the description passes", document_text_utils.find_timestamps(described) == [],
                  str(document_text_utils.find_timestamps(described)))
    harness.check("a time in the prose is still found",
                  document_text_utils.find_timestamps("At 20:00 the speaker opened the chart.") == ["20:00"])
