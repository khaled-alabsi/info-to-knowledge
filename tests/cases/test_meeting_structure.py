"""The document follows the meeting's own parts, REQUIREMENTS.md R9, and opens with an executive summary and the
agenda, R11.

What the program decides is tested here: the moments a parts call is shown, which part an entry falls in, that a
chunk never crosses a part, when a short return to an earlier subject is folded back into it, the checks on the
parts, the sections' decisions and action items, and the summary, and the whole path with a scripted model: the
chapters in the meeting's order, the summary and the agenda at the top, and one closing with three lists that may
say again what a section said.
"""

import json
import logging
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

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
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import call_log_service
from services import document_file_service
from services import performance_log_service
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from schemas import document_parts
from schemas import document_section
from schemas import document_summary
from services import chain_dispatch_service
from utils import frame_merge_utils
from utils import document_plan_utils
from utils import document_text_utils
from validators import document_validator

FINALIZE = output_contract.PHASE_NUMBERS["finalize"]
ENTRIES = [
    {"id": "S0001", "kind": "speech", "text": "Welcome. First the portal, then the tokens, then a demo.", "start": 0.0,
     "end": 4.0, "timestamps": [0.0], "source": "transcript"},
    {"id": "F0.1", "kind": "facts", "text": "The partner portal gives partners one sign-in.", "timestamps": [5.0],
     "frames": [0], "source": "frame"},
    {"id": "F1.1", "kind": "facts", "text": "The login service issues a token.", "timestamps": [20.0],
     "frames": [1], "source": "frame"},
    {"id": "F1.2", "kind": "facts", "text": "A token is valid for 30 minutes.", "timestamps": [20.0],
     "frames": [1], "source": "frame"},
    {"id": "F2.1", "kind": "facts", "text": "The contracts view lists every contract of a partner.",
     "timestamps": [40.0], "frames": [2], "source": "frame"},
    {"id": "F2.2", "kind": "facts", "text": "The one sign-in also covers the contracts view.", "timestamps": [40.0],
     "frames": [2], "source": "frame"},
    {"id": "F3.1", "kind": "facts", "text": "The portal launches in May.", "timestamps": [60.0], "frames": [3],
     "source": "frame"},
    {"id": "F3.2", "kind": "facts", "text": "The platform team writes the migration guide by Friday.",
     "timestamps": [60.0], "frames": [3], "source": "frame"},
    {"id": "F3.3", "kind": "open_question", "text": "who rotates the signing key", "timestamps": [60.0],
     "frames": [3], "source": "frame"},
    {"id": "S0002", "kind": "speech", "text": "So that is the plan.", "start": 61.0, "end": 63.0,
     "timestamps": [61.0], "source": "transcript"}]
FRAMES = [{"index": 0, "timestamp": 5.0, "screenKind": "slide",
           "screen": '- slide: white\n  - title "Agenda: Portal, Tokens, Demo, Pricing": top'},
          {"index": 1, "timestamp": 20.0, "screenKind": "slide", "screen": '- slide: white\n  - title "Tokens": top'},
          {"index": 2, "timestamp": 40.0, "screenKind": "web application",
           "screen": '- the contracts page: white\n  - heading "Contracts": top left'},
          {"index": 3, "timestamp": 60.0, "screenKind": "slide"}]
INTEGRATION = {"schemaVersion": 1, "entries": ENTRIES, "frames": FRAMES, "contradictions": [], "gaps": [],
               "unreadSpeech": [{"start": 0.0, "end": 5.0, "why": "before the first frame"}],
               "counts": {"frames": 4, "framesAccepted": 4}}
PARTS = [{"title": "The partner portal", "first_moment": 1, "covers_announced": ["Portal"]},
         {"title": "Tokens", "first_moment": 3, "covers_announced": ["Tokens"]},
         {"title": "Contracts demo", "first_moment": 4, "covers_announced": ["Demo"]},
         {"title": "Launch plan", "first_moment": 5, "covers_announced": []}]
ANNOUNCED = ["Portal", "Tokens", "Demo", "Pricing"]


def test_the_moments_list_each_frame_with_its_title_its_kind_and_its_speech():
    moments = document_plan_utils.list_moments(INTEGRATION, speech_chars=30)
    harness.check("the speech before the first frame is a moment of its own, then one per frame",
                  [(moment["number"], moment["start"]) for moment in moments]
                  == [(1, 0.0), (2, 5.0), (3, 20.0), (4, 40.0), (5, 60.0)], str(moments))
    harness.check("a frame's title is the text its screen outline calls a title, and its kind comes from the frame",
                  (moments[1]["title"], moments[1]["screenKind"], moments[3]["screenKind"])
                  == ("Agenda: Portal, Tokens, Demo, Pricing", "slide", "web application"), str(moments))
    harness.check("each moment carries what was said during it, cut to the limit",
                  moments[0]["speech"] == ENTRIES[0]["text"][:30] + " ..." and "the plan" in moments[4]["speech"],
                  str([moment["speech"] for moment in moments]))
    harness.check("each moment carries the first facts it established",
                  moments[2]["facts"] == "The login service issues a token.", str(moments[2]))
    older = document_plan_utils.list_moments({**INTEGRATION, "frames": []}, speech_chars=30)
    harness.check("an integration file written before the merge listed its frames gives the same moments, kind unknown",
                  [(moment["start"], moment["screenKind"]) for moment in older]
                  == [(0.0, ""), (5.0, ""), (20.0, ""), (40.0, ""), (60.0, "")], str(older))


def test_the_merge_lists_each_accepted_frame_with_its_screen_kind():
    records = [{"slug": "frame-001", "index": 1, "timestamp": 9.0, "screen_kind": "code editor", "facts": ["a"]},
               {"slug": "frame-000", "index": 0, "timestamp": 4.0, "screen_kind": "slide", "facts": ["b"]}]
    frames = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 4.0},
                        {"slug": "frame-001", "index": 1, "timestamp": 9.0},
                        {"slug": "frame-002", "index": 2, "timestamp": 15.0}]}
    merged = frame_merge_utils.merge_frame_records({"segments": []}, frames, records, {"frame-002": "blocked"})
    harness.check("the accepted frames in order, each with its kind; a blocked one has no record to list",
                  [(frame["index"], frame["timestamp"], frame["screenKind"]) for frame in merged["frames"]]
                  == [(0, 4.0, "slide"), (1, 9.0, "code editor")], str(merged["frames"]))


def test_entries_fall_into_the_part_their_moment_opens_and_no_chunk_crosses_a_part():
    entries = document_plan_utils.select_document_entries(INTEGRATION)
    groups = document_plan_utils.split_by_parts(entries, [0.0, 20.0, 40.0, 60.0])
    harness.check("each entry sits in the last part that started at or before it",
                  [[entry["id"] for entry in group] for group in groups]
                  == [["S0001", "F0.1"], ["F1.1", "F1.2"], ["F2.1", "F2.2"], ["F3.1", "F3.2", "F3.3", "S0002"]],
                  str([[entry["id"] for entry in group] for group in groups]))
    chunks = [chunk for group in groups for chunk in document_plan_utils.split_into_chunks(group, 60, 18_000)]
    harness.check("cut per part, a chunk never holds entries of two parts, however large the limits",
                  len(chunks) == 4, str(len(chunks)))
    harness.check("with no part at all, every entry is one group",
                  [len(group) for group in document_plan_utils.split_by_parts(entries, [])] == [len(entries)])


def _build_outlines() -> list[dict]:
    return [{"topics": [{"title": "Partner portal", "entry_ids": ["F0.1"]}], "set_aside_entry_ids": ["S0001"]},
            {"topics": [{"title": "Tokens", "entry_ids": ["F1.1", "F1.2"]}]},
            {"topics": [{"title": "Contracts view", "entry_ids": ["F2.1"]},
                        {"title": "  partner PORTAL ", "entry_ids": ["F2.2"]}]},
            {"topics": [{"title": "Launch plan", "entry_ids": ["F3.1", "F3.2", "F3.3"]}]}]


def test_a_short_return_to_an_earlier_subject_is_folded_back_and_recorded():
    topics, folds = document_plan_utils.merge_part_topics(_build_outlines(), [1, 2, 3, 4], fold_entries=5)
    harness.check("the return joins the topic where the subject was first discussed, as its second part",
                  [(topic["number"], topic["title"], topic["part"], [part["entryIds"] for part in topic["parts"]])
                   for topic in topics]
                  == [(1, "Partner portal", 1, [["F0.1"], ["F2.2"]]), (2, "Tokens", 2, [["F1.1", "F1.2"]]),
                      (3, "Contracts view", 3, [["F2.1"]]), (4, "Launch plan", 4, [["F3.1", "F3.2", "F3.3"]])],
                  str(topics))
    harness.check("and the fold is recorded with the title and both parts",
                  folds == [{"title": "partner PORTAL", "fromPart": 3, "toPart": 1, "entryIds": ["F2.2"]}], str(folds))
    topics, folds = document_plan_utils.merge_part_topics(_build_outlines(), [1, 2, 3, 4], fold_entries=0)
    harness.check("a return larger than the fold limit stays in its own part",
                  not folds and [topic["part"] for topic in topics if "portal" in topic["title"].casefold()] == [1, 3],
                  str(topics))
    alone = _build_outlines()
    alone[2] = {"topics": [{"title": "Partner portal", "entry_ids": ["F2.1", "F2.2"]}]}
    topics, folds = document_plan_utils.merge_part_topics(alone, [1, 2, 3, 4], fold_entries=5)
    harness.check("a part that is only that subject is a part of its own, never folded away",
                  not folds and [topic["part"] for topic in topics if topic["title"] == "Partner portal"] == [1, 3],
                  str(topics))
    harness.check("within one part a title named again is one topic, as before",
                  [len(topic["parts"]) for topic in document_plan_utils.merge_part_topics(
                      [{"topics": [{"title": "A", "entry_ids": ["E1"]}]}, {"topics": [{"title": "a", "entry_ids": ["E2"]}]}],
                      [1, 1], fold_entries=0)[0]] == [2])


def _write_part(paths,
                slug: str,
                parsed: dict,
                write) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": parsed, "finishReason": "stop"}})
    write(paths, slug, parsed)


def _list_parts_codes(paths,
                      parts: list[dict],
                      announced: list[str]) -> set[str]:
    _write_part(paths, document_file_service.PARTS_SLUG, {"announced_agenda": announced, "points": parts},
                lambda paths, slug, parsed: document_file_service.write_parts(paths, parsed))

    return harness.codes(document_validator.validate_parts(paths, moment_count=5))


def test_the_parts_run_in_the_meetings_order_from_its_first_moment():
    paths = fx.make_paths()
    harness.check("parts in order from moment 1 are accepted", _list_parts_codes(paths, PARTS, ANNOUNCED) == set(),
                  str(_list_parts_codes(paths, PARTS, ANNOUNCED)))
    for label, parts, code in (
            ("a first part that does not open the meeting", [{**PARTS[0], "first_moment": 2}, *PARTS[1:]],
             "DOCUMENT_PARTS_ORDER"),
            ("two parts out of order", [PARTS[0], PARTS[2], PARTS[1], PARTS[3]], "DOCUMENT_PARTS_ORDER"),
            ("a part past the last moment", [*PARTS[:3], {**PARTS[3], "first_moment": 6}], "DOCUMENT_PARTS_ORDER"),
            ("no part at all", [], "DOCUMENT_PARTS_EMPTY"),
            ("a part covering an item nobody announced", [{**PARTS[0], "covers_announced": ["Billing"]}, *PARTS[1:]],
             "DOCUMENT_PARTS_ANNOUNCED"),
            ("a title that names the recording", [{**PARTS[0], "title": "Frame 1 intro"}, *PARTS[1:]],
             "DOCUMENT_BANNED_PHRASE")):
        harness.check(f"{label} is {code}", _list_parts_codes(paths, parts, ANNOUNCED) == {code},
                      str(_list_parts_codes(paths, parts, ANNOUNCED)))


def _write_section_markdown(paths,
                            slug: str,
                            markdown: str) -> None:
    _write_part(paths, slug, {"markdown": markdown}, lambda at, name, parsed: document_file_service.write_section(
        at, name, parsed["markdown"]))


def test_a_sections_marked_decisions_and_action_items_cite_its_entries_and_are_said_once():
    paths = fx.make_paths()
    merge_inputs.write_merge_inputs(paths, INTEGRATION)
    section = ("> **Decision:** The portal launches in May. [F3.1]\n\n"
               "> **Action:** The platform team writes the migration guide by Friday. [F3.2]\n")
    _write_section_markdown(paths, "heading-004-1", section)
    report = document_validator.validate_section(paths, "heading-004-1", ["F3.1", "F3.2"], earlier_slugs=[])
    harness.check("a marked decision and action item, each citing its entry, are accepted", report.ok,
                  report.render_summary())
    for label, markdown, code in (
            ("a decision citing no entry", section.replace(" [F3.1]", ""), "DOCUMENT_CLAIM_UNSOURCED"),
            ("an action item citing another topic's entry", section.replace("[F3.2]", "[F1.1]"),
             "DOCUMENT_UNKNOWN_SOURCE"),
            ("a decision that narrates the recording",
             section.replace("The portal launches", "The video shows the portal launch"), "DOCUMENT_BANNED_PHRASE"),
            ("a decision that repeats a paragraph word for word",
             "The portal launches in May. [F3.1]\n\n" + section, "DOCUMENT_REPEATED_SENTENCE")):
        _write_section_markdown(paths, "heading-004-1", markdown)
        codes = harness.codes(document_validator.validate_section(paths, "heading-004-1", ["F3.1", "F3.2"], earlier_slugs=[]))
        harness.check(f"{label} is {code}", code in codes, str(codes))
    # 2026-10-02: a set-aside line without its ids was called "a decision, an action item or an open point", and the
    # worker, who had written none, could not find what to fix
    _write_section_markdown(paths, "heading-004-1", section + "\n> **Set aside:** who presents\n")
    report = document_validator.validate_section(paths, "heading-004-1", ["F3.1", "F3.2"], earlier_slugs=[])
    found = next((finding.detail for finding in report.findings if finding.code == "DOCUMENT_CLAIM_UNSOURCED"), "")
    harness.check("the finding quotes the marked line that names no id", "Set aside" in found and "who presents" in found, found)
    # 2026-10-02: the check stopped at the unsourced line, so a wrong id beside it surfaced only on the next answer, a
    # whole blocked step later
    _write_section_markdown(paths, "heading-004-1", section.replace("[F3.2]", "[F1.1]") + "\n> **Set aside:** who presents\n")
    codes = harness.codes(document_validator.validate_section(paths, "heading-004-1", ["F3.1", "F3.2"], earlier_slugs=[]))
    harness.check("an unsourced marked line and a foreign id are both found in one answer",
                  {"DOCUMENT_CLAIM_UNSOURCED", "DOCUMENT_UNKNOWN_SOURCE"} <= codes, str(codes))


SUMMARY = {"summary": [{"text": "The partner portal launches in May, with sign-in by token.", "entry_ids": ["F3.1", "F1.1"]},
                       {"text": "The closing lists the one decision and the one action item.", "entry_ids": ["F3.1", "F3.2"]}],
           "agenda": [{"part_number": 1, "text": "Introduces the partner portal"},
                      {"part_number": 2, "text": "Walks through how tokens are issued"},
                      {"part_number": 3, "text": "Demonstrates the contracts view"},
                      {"part_number": 4, "text": "Discusses the launch plan"}]}


def _list_summary_codes(paths,
                        summary: dict,
                        max_words: int = 60) -> set[str]:
    _write_part(paths, document_file_service.SUMMARY_SLUG, summary,
                lambda paths, slug, parsed: document_file_service.write_summary(paths, parsed))

    return harness.codes(document_validator.validate_summary(paths, part_count=4, known_entry_ids=["F1.1", "F3.1", "F3.2"],
                                                              max_words=max_words))


def test_a_summary_claim_with_a_degree_its_notes_never_give_is_refused_defect():
    # Three runs' summaries added a degree or a certainty to their notes ("a strict workflow", "guarantee", "ensuring").
    # A claim holding such a word that none of its cited notes holds is now refused, naming the word
    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, 1, {"segments": [{"start": 0.0, "end": 4.0, "text": ENTRIES[0]["text"]}]})
    merge_inputs.write_merge_inputs(paths, {"entries": ENTRIES, "frames": []})
    added = {**SUMMARY, "summary": [{"text": "The partner portal is guaranteed to launch in May, strictly by token.",
                                     "entry_ids": ["F3.1", "F1.1"]}]}
    harness.check("a claim adding 'guaranteed' and 'strictly' is refused",
                  "DOCUMENT_SUMMARY_DEGREE_UNSOURCED" in _list_summary_codes(paths, added), "")
    harness.check("a claim worded as its notes passes",
                  "DOCUMENT_SUMMARY_DEGREE_UNSOURCED" not in _list_summary_codes(fx.make_paths(), SUMMARY), "")


def test_the_summary_cites_what_the_sections_rest_on_and_the_agenda_has_one_line_per_part():
    paths = fx.make_paths()
    harness.check("a short, sourced summary and one agenda line per part in order are accepted",
                  _list_summary_codes(paths, SUMMARY) == set(), str(_list_summary_codes(paths, SUMMARY)))
    for label, change, code in (
            ("no summary sentence", {"summary": []}, "DOCUMENT_SUMMARY_EMPTY"),
            ("a sentence citing nothing", {"summary": [{"text": "It launches.", "entry_ids": []}]},
             "DOCUMENT_SUMMARY_UNSOURCED"),
            ("a sentence citing an entry no section rests on",
             {"summary": [{"text": "It launches in May.", "entry_ids": ["P001"]}]}, "DOCUMENT_UNKNOWN_SOURCE"),
            ("an agenda missing a part", {"agenda": SUMMARY["agenda"][:3]}, "DOCUMENT_AGENDA_PARTS"),
            ("an agenda out of order", {"agenda": [SUMMARY["agenda"][1], SUMMARY["agenda"][0], *SUMMARY["agenda"][2:]]},
             "DOCUMENT_AGENDA_PARTS"),
            ("an agenda line with a clock time", {"agenda": [{"part_number": 1, "text": "Opens at 0:05"},
                                                             *SUMMARY["agenda"][1:]]}, "DOCUMENT_TIMESTAMP")):
        codes = _list_summary_codes(paths, {**SUMMARY, **change})
        harness.check(f"{label} is {code}", codes == {code}, str(codes))
    codes = _list_summary_codes(paths, SUMMARY, max_words=10)
    harness.check("a summary over its word limit is DOCUMENT_SUMMARY_TOO_LONG", codes == {"DOCUMENT_SUMMARY_TOO_LONG"},
                  str(codes))


def _build_answer(schema: str,
            **fields):
    # A section is stated by its parts and answered as the markdown the agent writes.
    fields = section_fixtures.build_section_answer(fields) if schema == "DocumentSection" else fields
    message = fakes.tool_call(schema, **fields)
    message.response_metadata.update({"done_reason": "stop"})

    return message


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


def _run_phase(ctx: RunContext) -> dict:
    return write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                     {"configurable": {"ctx": ctx}})


def _read_call_text(ctx: RunContext,
                    number: int) -> str:
    return ctx.model.calls[number][1].content[0]["text"]


PARTS_ANSWER = _build_answer("DocumentParts", announced_agenda=ANNOUNCED, points=PARTS)
# The speech frames read is enriched once (R8); nothing was missing here.
OUTLINE_ANSWERS = [_build_answer("DocumentOutline", not_established=[], set_aside_entry_ids=outline.get("set_aside_entry_ids", []),
                           topics=[{"title": topic["title"], "first_entry_id": topic["entry_ids"][0]}
                                   for topic in outline["topics"]]) for outline in _build_outlines()]
TITLE_ANSWER = _build_answer("DocumentContents", title="The partner portal launches in May with sign-in by token")
SECTION_ANSWERS = [
    _build_answer("DocumentSection", heading="Partner portal", not_established=[],
            paragraphs=[{"text": "The partner portal gives partners one sign-in.", "entry_ids": ["F0.1"]}]),
    _build_answer("DocumentSection", heading="Partner portal", not_established=[],
            paragraphs=[{"text": "That sign-in also covers the contracts view.", "entry_ids": ["F2.2"]}]),
    _build_answer("DocumentSection", heading="Tokens", not_established=[],
            paragraphs=[{"text": "The login service issues a token, valid for 30 minutes.", "entry_ids": ["F1.1", "F1.2"]}]),
    _build_answer("DocumentSection", heading="Contracts view", not_established=[],
            paragraphs=[{"text": "The contracts view lists every contract of a partner.", "entry_ids": ["F2.1"]}]),
    _build_answer("DocumentSection", heading="Launch plan", not_established=["Who rotates the signing key is not settled."],
            paragraphs=[{"text": "The portal launches in May.", "entry_ids": ["F3.1"]},
                        {"text": "The platform team writes the migration guide by Friday.", "entry_ids": ["F3.2"]},
                        {"text": "Who rotates the signing key is open.", "entry_ids": ["F3.3"]},
                        {"text": "That is the whole plan.", "entry_ids": ["S0002"]}],
            decisions=[{"text": "The portal launches in May.", "entry_ids": ["F3.1"]}],
            action_items=[{"text": "The platform team writes the migration guide by Friday.", "entry_ids": ["F3.2"]}])]
SUMMARY_ANSWER = _build_answer("DocumentSummary", **SUMMARY)


def test_the_document_follows_the_meeting_and_opens_with_its_summary_and_its_agenda():
    ctx = _load_run_context(fakes.scripted(PARTS_ANSWER, *OUTLINE_ANSWERS, TITLE_ANSWER, *SECTION_ANSWERS,
                                           SUMMARY_ANSWER))
    update = _run_phase(ctx)
    harness.check("phase 4 completes", update["phases"]["finalize"] == "complete", str(update))
    harness.check("the parts, one outline per part, the title, five section parts and the summary",
                  ctx.model.call_count == 12, f"calls={ctx.model.call_count}")
    harness.check("the parts call is shown every moment with its title and its speech",
                  "Agenda: Portal, Tokens, Demo, Pricing" in _read_call_text(ctx, 0)
                  and "First the portal" in _read_call_text(ctx, 0), _read_call_text(ctx, 0))
    harness.check("an outline is told the part its chunk belongs to", "Contracts demo" in _read_call_text(ctx, 3),
                  _read_call_text(ctx, 3))
    harness.check("the summary call is handed the sections' decisions and the parts to write the agenda for",
                  "The portal launches in May." in _read_call_text(ctx, 11) and "Contracts demo" in _read_call_text(ctx, 11),
                  _read_call_text(ctx, 11))
    markdown = ctx.paths.summary.read_text(encoding="utf-8")
    order = [f"## {output_contract.EXECUTIVE_SUMMARY_HEADING}", f"## {output_contract.AGENDA_HEADING}",
             "## The partner portal", "## Tokens", "## Contracts demo", "## Launch plan",
             f"## {output_contract.CLOSING_HEADING_ALL}"]
    harness.check("the title, the summary and the agenda come first, then the parts in the meeting's order, then the "
                  "closing", markdown.startswith("# The partner portal launches in May with sign-in by token\n")
                  and all(heading in markdown for heading in order)
                  and [markdown.index(heading) for heading in order] == sorted(markdown.index(heading) for heading in order),
                  markdown)
    harness.check("the agenda links each part to its chapter",
                  "[Contracts demo](#contracts-demo): Demonstrates the contracts view" in markdown, markdown)
    harness.check("and names what was announced and never reached",
                  f"{output_contract.ANNOUNCED_NOT_COVERED}: Pricing." in markdown, markdown)
    harness.check("the short return is written where the subject was first discussed",
                  markdown.index("also covers the contracts view") < markdown.index("## Tokens"), markdown)
    closing = markdown[markdown.index(f"## {output_contract.CLOSING_HEADING_ALL}"):]
    harness.check("the closing lists the decision, the action item and the open point, each under its own heading",
                  closing.index(f"### {output_contract.DECISIONS_HEADING}") < closing.index("- The portal launches in May.")
                  < closing.index(f"### {output_contract.ACTION_ITEMS_HEADING}")
                  < closing.index("- The platform team writes the migration guide by Friday.")
                  < closing.index(f"### {output_contract.OPEN_POINTS_HEADING}")
                  < closing.index("- Who rotates the signing key is not settled."), closing)
    report = document_validator.validate_document(ctx.paths)
    harness.check("the document passes its validator though the closing and the summary say again what a section said",
                  report.ok, report.render_summary())
    provenance = json.loads((ctx.paths.output_root / output_contract.DRAFT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("the provenance keeps the parts, the fold and the summary with its entries",
                  [part["title"] for part in provenance["parts"]] == [part["title"] for part in PARTS]
                  and provenance["folds"][0]["fromPart"] == 3
                  and [entry["id"] for entry in provenance["summary"][0]["entries"]] == ["F3.1", "F1.1"], str(provenance))

    ctx.store.set_phase("finalize", "blocked")
    update = _run_phase(ctx)
    harness.check("a resumed phase keeps every part that still validates and asks nothing again",
                  update["phases"]["finalize"] == "complete" and ctx.model.call_count == 12,
                  f"{update} calls={ctx.model.call_count}")


def test_a_blocked_step_stops_the_phase_and_nothing_after_it_is_asked():
    """The owner, 2026-09-29: "the run should stop when block, no continue after block in any step"."""

    bad_parts = _build_answer("DocumentParts", announced_agenda=[], points=[
        {"title": "Opening", "first_moment": 1, "covers_announced": []},
        {"title": "Opening again", "first_moment": 1, "covers_announced": []}])
    ctx = _load_run_context(fakes.scripted(bad_parts, bad_parts, PARTS_ANSWER, *OUTLINE_ANSWERS, TITLE_ANSWER,
                                           *SECTION_ANSWERS, SUMMARY_ANSWER))
    update = _run_phase(ctx)
    harness.check("the phase is blocked, never complete", update["phases"]["finalize"] == "blocked", str(update))
    harness.check("only the blocked step's two attempts were asked, nothing after it", ctx.model.call_count == 2,
                  f"calls={ctx.model.call_count}")
    harness.check("no document was written from what was left", not ctx.paths.summary.is_file())


def test_a_rejected_summary_stops_the_phase_before_any_document_is_written():
    bad_summary = _build_answer("DocumentSummary", summary=[], agenda=[])
    script = [PARTS_ANSWER, *OUTLINE_ANSWERS, TITLE_ANSWER, *SECTION_ANSWERS, bad_summary, bad_summary]
    ctx = _load_run_context(fakes.scripted(*script))
    update = _run_phase(ctx)
    harness.check("the phase is blocked", update["phases"]["finalize"] == "blocked", str(update))
    harness.check("every step before the summary was asked, and the summary twice", ctx.model.call_count == len(script),
                  f"calls={ctx.model.call_count} of {len(script)}")
    harness.check("no document and no published folder were written", not ctx.paths.summary.is_file())


def test_every_field_the_new_calls_answer_is_named_in_the_instruction_they_are_sent():
    for schema, instruction in (
            (document_parts.DocumentParts, "parts.md"), (document_parts.AgendaPoint, "parts.md"),
            (document_summary.DocumentSummary, "summary.md"), (document_summary.AgendaLine, "summary.md"),
            (document_section.DocumentSection, "section.md")):
        text = (app_config.AGENT_CONTEXT_DIR / "instructions" / instruction).read_text(encoding="utf-8")
        for name in schema.model_fields:
            harness.check(f"{instruction} names `{name}` of {schema.__name__}", f"`{name}`" in text,
                          "the schema asks for it and the instruction never says what belongs in it")


def test_a_return_split_over_two_chunks_is_folded_whole_or_not_at_all():
    outlines = [{"topics": [{"title": "Partner portal", "entry_ids": ["E1"]}]},
                {"topics": [{"title": "partner portal", "entry_ids": ["E2", "E3", "E4"]}, {"title": "Demo", "entry_ids": ["E5"]}]},
                {"topics": [{"title": "Partner portal", "entry_ids": ["E6", "E7", "E8"]}]}]
    topics, folds = document_plan_utils.merge_part_topics(outlines, [1, 3, 3], fold_entries=5)
    harness.check("six entries of one subject in the later part, over the limit of five, stay there as one topic",
                  not folds and [(topic["part"], sum(len(part["entryIds"]) for part in topic["parts"]))
                                 for topic in topics if "portal" in topic["title"].casefold()] == [(1, 1), (3, 6)],
                  str(topics))
    topics, folds = document_plan_utils.merge_part_topics(outlines, [1, 3, 3], fold_entries=6)
    harness.check("within the limit, both chunks of the return are folded, never one of them",
                  len(folds) == 2 and [topic["part"] for topic in topics if "portal" in topic["title"].casefold()] == [1],
                  str(folds))


# R12: the table of contents is built by the program from the draft headings of each chapter (the owner, 2026-09-29): a
# chapter's notes are read, then its headings named by where they start. A subject the talk comes back to keeps its
# title; the program fills in each heading's entries.
READINGS = [("The partner portal", [("Partner portal", "F0.1")]), ("Tokens", [("Tokens", "F1.1")]),
            ("Contracts view", [("Contracts view", "F2.1"), ("Partner portal", "F2.2")]),
            ("Launch plan", [("Launch plan", "F3.1")])]
DRAFT = {"working_title": "The partner portal launches in May", "sentences": [
    "The partner portal launches in May.", "Partners sign in once, with a token.", "Who rotates the key is open."]}


# The five sections by chapter: the portal's two parts (a short return is folded into its first chapter), the tokens,
# the contracts view, the launch plan. One `submit_chapter` answers each chapter (the owner, 2026-09-28).
CHAPTER_SIZES = [2, 1, 1, 1]


def _group_into_chapters(sections: list) -> list:
    """Section answers, each a `submit_section` call, regrouped into one `submit_chapter` call per chapter: one markdown,
    each heading's own line followed by its text."""

    calls, start = [], 0
    for chapter, size in enumerate(CHAPTER_SIZES, start=1):
        parts = [f"### Heading {number} (T{chapter:02d}.{number})\n\n{call.tool_calls[0]['args']['markdown']}"
                 for number, call in enumerate(sections[start:start + size], start=1)]
        calls.append(fakes.tool_call("submit_chapter", markdown="\n\n".join(parts)))
        start += size

    return calls


def _script_conversation(sections: list) -> list:
    """The follow-ups of one conversation: the draft, the agenda, the draft headings of each chapter (its notes read,
    then its headings), the sections and the final. No table of contents is asked: the program builds it."""

    # The program sets nothing aside, so the opening speech belongs to the first section
    first = dict(sections[0].tool_calls[0]["args"])
    first["markdown"] = first["markdown"].replace("[F0.1]", "[S0001, F0.1]")
    sections = [fakes.tool_call("submit_section", **first), *sections[1:]]
    readings = [call for number, (title, headings) in enumerate(READINGS, start=1) for call in (
        fakes.tool_call("read_notes", chapter=number),
        fakes.tool_call("submit_draft_headings", purpose=f"Show {title.lower()}.",
                        headings=[{"title": name, "first_entry_id": start, "what": f"What {name.lower()} is."}
                                  for name, start in headings]))]

    return [fakes.tool_call("submit_executive_summary_draft", **DRAFT),
            fakes.tool_call("submit_agenda", announced_agenda=ANNOUNCED, points=PARTS), *readings,
            *_group_into_chapters(sections),
            fakes.tool_call("submit_final", title="The partner portal launches in May with sign-in by token", **SUMMARY)]


def test_the_same_meeting_written_as_one_conversation_gives_the_same_document_with_its_links():
    """R12: one session, one step, one worker. Every piece is a follow-up of one conversation, the draft first,
    each answered through its submit tool and judged by the calls path's own validators; a `[T01.1]` in a section
    becomes a link to that topic's heading."""

    sections = [fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS]
    tokens = dict(SECTION_ANSWERS[2].tool_calls[0]["args"])
    tokens["paragraphs"] = [{"text": "The login service issues a token, valid for 30 minutes, for the sign-in of "
                                     "[T01.1].", "entry_ids": ["F1.1", "F1.2"]}]
    sections[2] = fakes.tool_call("submit_section", **tokens)
    ctx = _load_run_context(fakes.scripted(*_script_conversation(sections)))
    update = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                         {"configurable": {"ctx": ctx}})
    harness.check("phase 4 completes", update["phases"]["finalize"] == "complete", str(update))
    harness.check("15 calls: the draft, the agenda, four draft headings of two calls each, no table of contents, "
                  "one call per chapter (4), the final", ctx.model.call_count == 15, f"calls={ctx.model.call_count}")
    reading_text = str(ctx.model.calls[2][-1].content) if ctx.model.call_count > 2 else ""
    harness.check("each agenda point is read first, whole, through its own follow-up",
                  "draft headings of chapter 1 of 4" in reading_text and "with chapter 1: the whole chapter comes back in one read" in reading_text, reading_text)
    table = json.loads(document_file_service.build_outline_path(ctx.paths, "table-of-contents").read_text(encoding="utf-8"))
    harness.check("the table of contents is built by the program from the draft headings: one chapter per agenda point, "
                  "each heading as its chapter's follow-up named it, and no model call for it",
                  [chapter["title"] for chapter in table["chapters"]] == [point["title"] for point in PARTS]
                  and [topic["title"] for topic in table["chapters"][2]["headings"]] == ["Contracts view", "Partner portal"]
                  and not any("submit_table_of_contents" in str(call[-1].content) for call in ctx.model.calls), str(table))
    harness.check("no table-of-contents request was saved, since none was sent",
                  not document_file_service.build_follow_up_request_path(ctx.paths, "table-of-contents").is_file()
                  and document_file_service.build_follow_up_request_path(ctx.paths, "chapter-01").is_file())
    markdown_text = ctx.paths.summary.read_text(encoding="utf-8")
    contents = markdown_text.split("## Contents\n", 1)[1].split("\n\n", 1)[0] if "## Contents\n" in markdown_text else ""
    harness.check("the table of contents is a section of the document, after the agenda, one clickable line per chapter "
                  "and per heading",
                  markdown_text.index("## Agenda") < markdown_text.index("## Contents") < markdown_text.index("## Tokens")
                  and "- [Tokens](#tokens)" in contents and "- [Contracts demo](#contracts-demo)" in contents
                  and all(f"## {title}" in markdown_text for title in ("Tokens", "Contracts demo", "Launch plan")), contents)
    draft_text = document_file_service.build_working_draft_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the working file has the table of contents as a section too, with links",
                  "## Contents" in draft_text and "- [Tokens](#tokens)" in draft_text, draft_text[:600])
    harness.check("no separate call: every call of phase 4 opens with the conversation's one instruction",
                  all("# How the conversation goes" in str(call[0].content) for call in ctx.model.calls),
                  str([str(call[0].content)[:80] for call in ctx.model.calls]))
    opening = ctx.model.calls[0]
    harness.check("the draft comes first, from the moments; no separate check of the speech runs before it",
                  "moments of the meeting" in str(opening[-1].content)
                  and not any("submit_enrichment" in str(message.content) for message in opening), str(opening[-1].content)[:300])
    markdown = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("the final title heads the document", markdown.startswith(
        "# The partner portal launches in May with sign-in by token\n"), markdown[:120])
    harness.check("the chapters follow the meeting", [markdown.index(f"## {title}") for title in
                  ("The partner portal", "Tokens", "Contracts demo", "Launch plan")] == sorted(
        markdown.index(f"## {title}") for title in ("The partner portal", "Tokens", "Contracts demo", "Launch plan")))
    harness.check("the link to another topic is a link to the heading it is printed under", "[The partner portal](#the-partner-portal)" in markdown
                  and "[T01.1]" not in markdown, markdown)
    report = document_validator.validate_document(ctx.paths)
    harness.check("the document passes its validator", report.ok, report.render_summary())
    working = document_file_service.build_working_draft_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the working file shows the plan, then the scaffold with every section's text in place",
                  "Working title: The partner portal launches in May" in working and "# The scaffold" in working
                  and working.index("## The partner portal") < working.index("### Tokens") < working.index("## Launch plan")
                  and "[placeholder" not in working and ": accepted]" not in working, working)


class _Lines(logging.Handler):
    """Every line the run printed, as the operator reads it."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_a_resume_on_the_conversation_path_keeps_what_the_first_life_accepted_and_asks_nothing_again():
    """2026-09-29, run5: every resume deleted the agenda, the table outline and the sections before asking, because only the
    run store was asked whether an earlier life existed, and the conversation path never writes its attempts there."""

    sections = [fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS]
    ctx = _load_run_context(fakes.scripted(*_script_conversation(sections)))
    first = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                        {"configurable": {"ctx": ctx}})
    harness.check("the first life completes", first["phases"]["finalize"] == "complete", str(first))
    written = document_file_service.build_parts_path(ctx.paths).read_text(encoding="utf-8")
    ctx.model = fakes.scripted()
    ctx.store.set_phase("finalize", "blocked")
    second = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                         {"configurable": {"ctx": ctx}})
    harness.check("the second life completes and asks the model nothing", second["phases"]["finalize"] == "complete"
                  and ctx.model.call_count == 0, f"{second} calls={ctx.model.call_count}")
    harness.check("the agenda and the sections of the first life are still on disk, unchanged",
                  document_file_service.build_parts_path(ctx.paths).read_text(encoding="utf-8") == written
                  and list((ctx.paths.output_root / "04_writing" / "sections").glob("*.md")))


def test_each_follow_up_of_the_conversation_names_its_place_when_it_starts():
    """The owner reads the console live and wants to see how many follow-ups are left ("this is not showing the total
    number of follow up left"): a kind asked many times names its number and its total, `section 4 of 38 …`, and a
    follow-up asked once its plain name, never its slug: the table of contents is built by the program and has no follow-up."""

    ctx = _load_run_context(fakes.scripted(*_script_conversation([
        fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS])))
    ctx.log = logging.getLogger(f"follow-up-places-{ctx.run_dir.parent.name}")
    ctx.log.setLevel(logging.INFO)
    ctx.log.propagate = False
    lines = _Lines()
    ctx.log.addHandler(lines)
    write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE), {"configurable": {"ctx": ctx}})
    started = [line for line in lines.lines if line.endswith(" …")]
    harness.check("each start line says where its follow-up stands: its number and the total of its kind, or its plain "
                  "name when it is asked once",
                  started == ["drafting the executive summary …", "building the agenda …",
                              *[f"building the headings of chapter {number} of 4 …" for number in range(1, 5)],
                              *[f"writing chapter {number} of 4 …" for number in range(1, 5)],
                              "writing the final executive summary …"],
                  "\n".join(lines.lines))
    slugs = {row["slug"] for row in performance_log_service.read_rows(ctx.run_dir) if row.get("level") == "part"}
    harness.check("no line names a follow-up by its slug, and each outcome is printed under its start line",
                  len(slugs) == 11 and not any(line.removesuffix(" …").split(" · ")[0] in slugs for line in lines.lines)
                  and sum(line.startswith("  ") and line.endswith("accepted") for line in lines.lines) == 11,
                  "\n".join(lines.lines))


def test_a_meeting_with_no_entry_asks_only_the_draft_and_the_agenda():
    """A meeting with no entry and nothing said gives the chapters nothing: only the summary draft and the agenda are
    asked."""

    quiet = {**INTEGRATION, "unreadSpeech": [{"start": 0.0, "end": 5.0}], "frames": FRAMES[:1],
             "entries": []}
    ctx = _load_run_context(fakes.scripted(
        fakes.tool_call("submit_executive_summary_draft", **DRAFT),
        fakes.tool_call("submit_agenda", announced_agenda=[], points=[{"title": "Welcome", "first_moment": 1,
                                                                      "covers_announced": []}])))
    merge_inputs.write_merge_inputs(ctx.paths, quiet)
    write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE), {"configurable": {"ctx": ctx}})
    harness.check("two follow-ups, the draft and the agenda, and no reading", ctx.model.call_count == 2,
                  f"calls={ctx.model.call_count}")


def test_a_chapter_asked_again_keeps_each_heading_that_passed_before():
    """A retried chapter is rewritten whole, and the model loses a heading it had right (2026-09-29: a chapter blocked
    after three answers, each fixing one heading and dropping notes another had covered). A heading whose earlier text
    passed keeps it when the new answer breaks it."""

    sections = [fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS]
    calls = _script_conversation(sections)
    first_chapter = next(number for number, call in enumerate(calls) if call.tool_calls and call.tool_calls[0]["name"] == "submit_chapter")
    good = calls[first_chapter].tool_calls[0]["args"]["markdown"]
    heading_one, heading_two = good.split("\n\n### Heading 2", 1)
    heading_two = "### Heading 2" + heading_two
    broken = lambda text: re.sub(r"\[[^\]]*\]\s*$", "", text.rstrip()) + "\n"       # its last block loses its ids
    calls[first_chapter:first_chapter + 1] = [
        fakes.tool_call("check_chapter", markdown=good),     # a check first, so the first answer is judged
        fakes.tool_call("submit_chapter", markdown=heading_one + "\n\n" + broken(heading_two)),
        AIMessage(content=""),                              # the turn that asks why the answer was rejected: no report
        fakes.tool_call("submit_chapter", markdown=broken(heading_one) + "\n\n" + heading_two)]
    ctx = _load_run_context(fakes.scripted(*calls))
    budgets = app_config.settings.budgets
    kept = budgets.write_chapter_attempts
    budgets.write_chapter_attempts = 2                     # a second answer, as a resume after a blocked chapter gives
    try:
        update = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                             {"configurable": {"ctx": ctx}})
    finally:
        budgets.write_chapter_attempts = kept
    harness.check("the second answer is accepted: heading 1 keeps the text that passed, heading 2 takes the fixed one",
                  update["phases"]["finalize"] == "complete" and ctx.model.call_count == 18,
                  f"{update['phases']} calls={ctx.model.call_count}")


def test_a_chapter_written_as_reply_text_is_read_as_its_answer():
    """A worker that writes the chapter's markdown in its reply, with no call, gave the answer on the wrong channel: it
    is read as the `submit_chapter` answer and judged, as a call written as text is (2026-09-29: five chapter answers
    of one run came as text, each costing a whole turn)."""

    sections = [fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS]
    calls = _script_conversation(sections)
    first_chapter = next(number for number, call in enumerate(calls) if call.tool_calls and call.tool_calls[0]["name"] == "submit_chapter")
    calls[first_chapter] = fakes.says(calls[first_chapter].tool_calls[0]["args"]["markdown"])
    ctx = _load_run_context(fakes.scripted(*calls))
    update = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                         {"configurable": {"ctx": ctx}})
    harness.check("the chapter given as text is accepted with no extra call", update["phases"]["finalize"] == "complete"
                  and ctx.model.call_count == 15, f"{update['phases']} calls={ctx.model.call_count}")


def test_a_note_cited_under_another_heading_of_the_same_request_counts():
    """The headings' starts are an earlier answer's choice: a note that falls in one heading may be about the next one's
    subject, and the worker writes it there. Within one request that note is written up, so it is covered and not foreign
    (2026-09-29: notes introducing a subject fell under the heading before it)."""

    sections = [fakes.tool_call("submit_section", **message.tool_calls[0]["args"]) for message in SECTION_ANSWERS]
    calls = _script_conversation(sections)
    first_chapter = next(number for number, call in enumerate(calls) if call.tool_calls and call.tool_calls[0]["name"] == "submit_chapter")
    markdown = calls[first_chapter].tool_calls[0]["args"]["markdown"]
    heading_one, heading_two = markdown.split("\n\n### Heading 2", 1)
    bracket = next(match for match in re.finditer(r"\[([FS][^\]]*,[^\]]*)\]", heading_one))
    moved = bracket.group(1).split(",")[-1].strip()
    heading_one = heading_one.replace(bracket.group(0), "[" + ", ".join(i.strip() for i in bracket.group(1).split(",")[:-1]) + "]", 1)
    heading_two = re.sub(r"\[([FS][^\]]*)\](?=[^\[]*$)", lambda match: f"[{match.group(1)}, {moved}]", "### Heading 2" + heading_two, count=1)
    calls[first_chapter] = fakes.tool_call("submit_chapter", markdown=heading_one + "\n\n" + heading_two)
    ctx = _load_run_context(fakes.scripted(*calls))
    update = write_mode.run_phase_4_in_conversation_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                         {"configurable": {"ctx": ctx}})
    harness.check(f"{moved} cited under heading 2 instead of heading 1 is accepted in one answer",
                  update["phases"]["finalize"] == "complete" and ctx.model.call_count == 15,
                  f"{update['phases']} calls={ctx.model.call_count}")


def test_a_summary_that_opens_with_the_title_is_rejected():
    """The published summary's first sentence was the title word for word (2026-10-01)."""

    paths = fx.make_paths()
    repeated = {**SUMMARY, "title": "The partner portal launches in May, with sign-in by token"}
    harness.check("a summary opening with the title is named",
                  "DOCUMENT_SUMMARY_REPEATS_TITLE" in _list_summary_codes(paths, repeated))
    other = {**SUMMARY, "title": "Partner portal launch plan"}
    harness.check("a summary in other words passes",
                  "DOCUMENT_SUMMARY_REPEATS_TITLE" not in _list_summary_codes(fx.make_paths(), other))


def test_a_heading_said_to_start_at_a_frame_starts_at_its_first_note():
    """A worker named a frame (`F<frame>`) as a heading's start, and the step blocked on its one attempt."""

    owners = document_plan_utils.map_start_owners(ENTRIES)
    harness.check("a frame's own id resolves to its first note", owners.get("F1") == "F1.1", str(owners.get("F1")))
    harness.check("a note id still resolves to itself", owners.get("F1.2") == "F1.2")
    harness.check("a transcript line's id written without its leading zeros names that line ('S1' for S0001)",
                  owners.get("S1") == owners.get("S0001") and owners.get("S0001") is not None, str(owners.get("S1")))
    plan = document_plan_utils.flatten_table_of_contents(
        {"chapters": [{"title": "Portal", "first_entry_id": "S0001",
                       "headings": [{"title": "Portal", "first_entry_id": "S0001"},
                                    {"title": "Tokens", "first_entry_id": "F1"}]}]}, ENTRIES)
    starts = [topic["first_entry_id"] for topic in plan["topics"]]
    harness.check("the table of contents places that heading at the frame's first note", "F1.1" in starts, str(starts))


def test_a_heading_started_at_the_speech_a_frame_heard_starts_at_the_frame_defect():
    # A heading started at the first line a slide heard, and the slide's own notes, listed before that speech, filled
    # the heading before. Speech a frame heard is that frame's moment: such a start is the frame's first note
    entries = [*ENTRIES[:2],
               {"id": "S0004", "kind": "speech", "text": "So the login hands out a token.", "start": 20.0, "end": 24.0,
                "timestamps": [20.0], "source": "transcript", "segments": ["S0004", "S0005"], "heardByFrame": True},
               *ENTRIES[2:4]]
    entries = [entries[0], entries[1], entries[3], entries[4], entries[2]]
    owners = document_plan_utils.map_start_owners(entries)
    harness.check("the heard speech's id starts at its frame's first note", owners.get("S0004") == "F1.1",
                  str(owners.get("S0004")))
    harness.check("a later line it holds keeps the speech's own place", owners.get("S0005") == "S0004",
                  str(owners.get("S0005")))
    harness.check("speech no frame heard still starts at itself", owners.get("S0001") == "S0001",
                  str(owners.get("S0001")))


def test_a_heading_started_at_a_late_line_of_heard_speech_leaves_that_speech_with_its_frame_defect():
    # A heading started at a later line of a slide's heard speech took the whole speech, said over the slide, from the
    # heading the slide's notes stay with. Now such a start is the moment after that speech
    entries = [{"id": "F1.1", "kind": "facts", "timestamps": [10.0]},
               {"id": "S0004", "kind": "speech", "timestamps": [20.0], "segments": ["S0004", "S0005", "S0006"],
                "heardByFrame": True},
               {"id": "F2.1", "kind": "facts", "timestamps": [30.0]}]
    owners = document_plan_utils.map_start_owners(entries)
    harness.check("a late line of the heard speech starts at the next frame", owners.get("S0006") == "F2.1",
                  str(owners.get("S0006")))
    harness.check("written without its zeros too", owners.get("S5") == "F2.1", str(owners.get("S5")))
    harness.check("its first line still starts at its own frame", owners.get("S0004") == "F1.1", str(owners.get("S0004")))
    plan = document_plan_utils.expand_topic_starts(
        {"topics": [{"title": "Login", "first_entry_id": "F1.1"}, {"title": "Tokens", "first_entry_id": owners["S0006"]}]},
        entries)
    harness.check("so the speech stays with the slide's heading", plan["topics"][0]["entry_ids"] == ["F1.1", "S0004"],
                  str(plan["topics"]))


def test_a_speech_visual_alignment_note_may_be_cited_and_never_has_to_be():
    """Covering them made the worker write sentences about the speaker, rejected three chapters in a row (2026-10-01)."""

    entries = [{"id": "F1.1", "kind": "facts"}, {"id": "F1.2", "kind": "speech_visual_alignment"},
               {"id": "S0001", "kind": "speech", "heardByFrame": True}, {"id": "S0002", "kind": "speech"}]
    harness.check("only the facts and the unheard speech are required",
                  document_plan_utils.list_required_ids(entries) == ["F1.1", "S0002"],
                  str(document_plan_utils.list_required_ids(entries)))


def test_an_agenda_whose_first_point_starts_late_is_moved_to_the_first_moment_when_written():
    """What comes before the first point the worker named (a sound check, a greeting) can only belong to it: the
    program moves the start, where a rejection blocked the run."""


    paths = fx.make_paths()
    late = [{**PARTS[0], "first_moment": 2}, *PARTS[1:]]
    chain_dispatch_service._write_parts(SimpleNamespace(paths=paths), {"announced_agenda": ANNOUNCED, "points": late})
    written = document_file_service.read_parts(paths)
    harness.check("the first point now starts at moment 1, the others where they were",
                  [point["first_moment"] for point in written["points"]] == [1, *[part["first_moment"] for part in PARTS[1:]]],
                  str(written["points"]))
    harness.check("and the agenda passes the order check",
                  "DOCUMENT_PARTS_ORDER" not in harness.codes(document_validator.validate_parts(paths, moment_count=5)))


def test_a_summary_range_across_frames_is_written_out_from_the_sections_ids():
    """A summary cited a chapter's span as `[F12.1-F15.3]` and was refused: read as it stood it was one unknown id."""


    paths = fx.make_paths()
    known = ["F12.1", "F13.1", "S0707", "F14.2", "F15.3", "F16.1"]
    request = SimpleNamespace(paths=paths, inputs={"knownEntryIds": known})
    chain_dispatch_service._write_summary(request, {"title": "Refunds", "summary": [
        {"text": "The move cuts the cost of running the platform [F12.1-F15.3].", "entry_ids": []},
        {"text": "It starts with the first feed.", "entry_ids": ["F12.1-F14.2"]}]})
    written = document_file_service.read_summary(paths)["summary"]
    harness.check("a range in the text becomes every frame note between its ends",
                  written[0]["entry_ids"] == ["F12.1", "F13.1", "F14.2", "F15.3"], str(written[0]))
    harness.check("and so does a range in the id list", written[1]["entry_ids"] == ["F12.1", "F13.1", "F14.2"],
                  str(written[1]))



def test_a_summary_range_to_a_transcript_line_and_an_agenda_line_s_ids_leave_the_text_defect():
    # A final answer ended every summary sentence and agenda line with its ids, as its chapters' blocks did; ranges to a
    # line (`F63.1-S0249`) stayed as ids in the text, `S0234-S0236` named an uncited line. Now written out from the
    # sections' ids, and an agenda line's bracket taken out
    paths = fx.make_paths()
    known = ["F12.1", "S0700", "S0702", "F13.1", "S0707"]
    request = SimpleNamespace(paths=paths, inputs={"knownEntryIds": known})
    chain_dispatch_service._write_summary(request, {"title": "Refunds", "summary": [
        {"text": "The gateway refunds within a day [F12.1-S0702].", "entry_ids": []},
        {"text": "Jonas owns the rollout [S0700-S0707].", "entry_ids": []}],
        "agenda": [{"part_number": 1, "text": "Walks through the AcmeSync refunds [F12.1-S0702, F13.1]."}]})
    written = document_file_service.read_summary(paths)
    harness.check("a range from a note to a line cites every id between its ends",
                  written["summary"][0]["entry_ids"] == ["F12.1", "S0700", "S0702"], str(written["summary"][0]))
    harness.check("a range of lines cites only the lines the sections cite",
                  written["summary"][1]["entry_ids"] == ["S0700", "S0702", "F13.1", "S0707"], str(written["summary"][1]))
    harness.check("no id is left in a sentence",
                  not any(document_text_utils.find_entry_ids(claim["text"]) for claim in written["summary"]))
    harness.check("the agenda line keeps its words and loses its ids",
                  written["agenda"][0]["text"] == "Walks through the AcmeSync refunds.", str(written["agenda"]))

def test_an_agenda_point_with_no_entry_of_its_own_never_shifts_the_points_after_it():
    """Two agenda points starting at one moment: the first has no entry, so the table has one chapter fewer. Paired by
    position, the later chapter took the empty point's place and the last point's own fields were lost."""

    paths = fx.make_paths(Path(tempfile.mkdtemp()))
    ctx = SimpleNamespace(paths=paths, log=logging.getLogger("test"))
    entries = [{"id": "S0001", "kind": "speech", "timestamps": [0.0]}, {"id": "S0002", "kind": "speech", "timestamps": [12.0]}]
    parts = [{"number": 1, "title": "Intro", "start": 0.0, "coversAnnounced": ["Intro"]},
             {"number": 2, "title": "Refunds", "start": 10.0, "coversAnnounced": ["Refunds"]},
             {"number": 3, "title": "Orders", "start": 10.0, "coversAnnounced": ["Orders"]}]
    _table, chapter_parts, _outlines = write_document_node._write_table_of_contents(ctx, entries, parts, [])
    harness.check("the point with entries keeps its own fields under its chapter, numbered as the chapters are",
                  [(part["number"], part["title"], part["coversAnnounced"]) for part in chapter_parts]
                  == [(1, "Intro", ["Intro"]), (2, "Orders", ["Orders"])], str(chapter_parts))


def test_the_summary_of_a_pdf_writes_out_an_address_the_pdf_prints():
    """Only the chapter check passed over a PDF's own addresses: NIST's final summary was refused for the contact
    office's address the document prints, under strict settings a block."""

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, 1, {"segments": [
        {"start": 0.0, "end": 1.0, "text": "Send comments to cyberframework@agency.example."}]})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.PDF_TEXT_ONLY_MEDIUM, "items": []})
    with_address = {**SUMMARY, "summary": [{"text": "Comments go to cyberframework@agency.example by e-mail.",
                                            "entry_ids": ["F3.1"]}, *SUMMARY["summary"][1:]]}
    codes = _list_summary_codes(paths, with_address)
    harness.check("the published address is no person's", "DOCUMENT_PERSONAL_IN_TEXT" not in codes, str(codes))


def test_a_summary_sentence_drops_a_section_link_cited_beside_its_notes_defect():
    # A summary sentence cited a section's link id (T02.2) beside the note it rests on, copied from the section text it
    # was shown, and the run blocked; the writer drops the link id when a note is cited beside it, never alone
    from services import chain_dispatch_service

    known = ["S0096", "S0428"]
    beside = chain_dispatch_service._expand_claim({"text": "AcmeSync doubles its storage.", "entry_ids": ["T02.2", "S0096"]},
                                                  known)
    alone = chain_dispatch_service._expand_claim({"text": "AcmeSync doubles its storage.", "entry_ids": ["T02.2"]}, known)
    harness.check("the link beside a note is dropped", beside["entry_ids"] == ["S0096"], str(beside))
    harness.check("a link cited alone stays for the check to name", alone["entry_ids"] == ["T02.2"], str(alone))


def test_a_heading_started_after_its_subject_s_first_notes_starts_where_they_begin_defect():
    # a heading on a radio survey started after every note of the survey, which fell under the heading before it;
    # the program now moves its start back to where the notes holding its words begin, and a fitting start stays
    entries = [{"id": "F1.1", "kind": "facts", "text": "The board reads four analog sensors with the adcinall command."},
               {"id": "F1.2", "kind": "facts", "text": "A remote unit is selected by its address before reading."},
               {"id": "F2.1", "kind": "facts", "text": "The rssisurvey command scans every channel for interference."},
               {"id": "F2.2", "kind": "facts", "text": "Radio parameters such as channel and power are saved to eeprom."},
               {"id": "F3.1", "kind": "facts", "text": "Internal programs run from a memory slot of the Gateway."}]
    draft = {"purpose": "the CLI", "headings": [
        {"title": "Sensor reading", "first_entry_id": "F1.1", "what": "Reading analog sensors locally and remotely."},
        {"title": "Radio survey", "first_entry_id": "F3.1",
         "what": "The rssisurvey command scans channels for interference; radio parameters are saved to eeprom."}]}
    moved = document_plan_utils.move_late_heading_starts(draft, entries)
    harness.check("the survey heading starts at its first note", moved["headings"][1]["first_entry_id"] == "F2.1",
                  str(moved["headings"]))
    fitting = {**draft, "headings": [draft["headings"][0], {**draft["headings"][1], "first_entry_id": "F2.1"}]}
    harness.check("a heading started at its subject keeps its start",
                  document_plan_utils.move_late_heading_starts(fitting, entries) == fitting)
