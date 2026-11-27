"""Phase 2 for an audio recording: ONE call turns the transcript into paragraphs of clean
speech, `02_speech.md`, every line in exactly one paragraph; the frame list is written empty, phase 3 has nothing to
read, and the ingesting profile comes from a tag or, when no tag names one, from the same call. Every name is
invented."""

import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import fakes
from support import fixtures as fx
from support import harness

import config as app_config

from support import graph_inspection
from graph import routing
from graph.nodes import prepare_speech_node
from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from prompts import document_messages
from prompts import speech_structuring_messages
from graph.nodes.retry_loop import attempt_request_builder
from services import ingesting_profile_service
from services import performance_log_service
from services import phase_file_service
from services import run_tags_service
from services import speech_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import frame_merge_utils
from utils import speech_markdown_utils
from validators import loop_items_validator
from validators import speech_validator

# Six lines: Speaker A greets and asks, Speaker B answers in two lines, Speaker A closes
SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "um so welcome welcome to the the weekly call about the AcmeSync import"},
    {"start": 4.0, "end": 8.0, "text": "Jonas is the import job is it finished already"},
    {"start": 8.0, "end": 12.0, "text": "yes it it finished on Monday it took four hours"},
    {"start": 12.0, "end": 16.0, "text": "which is twice as long as we planned because the files were larger"},
    {"start": 16.0, "end": 18.0, "text": "  "},
    {"start": 18.0, "end": 22.0, "text": "okay thanks then we look at the export format next week"},
]
GOOD = """# Speech

## S0001-S0002 · Speaker A
Welcome to the weekly call about the AcmeSync import. Jonas, is the import job finished already?

## S0003-S0004 · Speaker B
Yes, it finished on Monday. It took four hours, which is twice as long as we planned, because the files were larger.

## S0005 · Speaker A
Okay, thanks. Then we look at the export format next week.
"""


def _make_paths():
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="Jonas-weekly.mp3")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": SEGMENTS})

    return paths


def _make_context(model) -> RunContext:
    paths = _make_paths()
    run_dir = paths.output_root / "logs" / "t"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=model)


def _validate(paths, text: str, is_profile_asked: bool = False) -> set[str]:
    speech_file_service.build_speech_path(paths).write_text(text, encoding="utf-8")

    return harness.codes(speech_validator.validate_speech_record(paths, is_profile_asked))


def test_the_lines_are_numbered_as_the_merge_numbers_them():
    lines = speech_markdown_utils.list_numbered_lines({"segments": SEGMENTS})
    merged = frame_merge_utils.merge_frame_records({"segments": SEGMENTS}, {"items": []}, [], {})
    harness.check("an empty line is left out, the rest S0001 on, as the merge's speech entries",
                  [line["id"] for line in lines] == [entry["id"] for entry in merged["entries"] if entry["kind"] == "speech"]
                  == ["S0001", "S0002", "S0003", "S0004", "S0005"], str(lines))


def test_the_answer_is_read_and_written_back_the_same():
    parsed, why = speech_markdown_utils.parse_speech(GOOD)
    harness.check("three paragraphs with their lines and speakers", parsed is not None and
                  [(p["first"], p["last"], p["speaker"]) for p in parsed["paragraphs"]]
                  == [("S0001", "S0002", "Speaker A"), ("S0003", "S0004", "Speaker B"), ("S0005", "S0005", "Speaker A")], why)
    again, _ = speech_markdown_utils.parse_speech(speech_markdown_utils.render_speech(parsed))
    harness.check("the file the program writes reads back the same", again == parsed, str(again))
    harness.check("text before the first paragraph is refused",
                  speech_markdown_utils.parse_speech("Here is the speech:\n" + GOOD)[0] is None)
    harness.check("an answer with no paragraph is refused", speech_markdown_utils.parse_speech("# Speech\n")[0] is None)
    with_profile, _ = speech_markdown_utils.parse_speech(GOOD.replace("# Speech\n", "# Speech\n\nProfile: meeting\n"))
    harness.check("the profile line is read", with_profile["profile"] == "meeting", str(with_profile))


def test_the_validator_accepts_good_paragraphs_and_names_each_fault():
    paths = _make_paths()
    harness.check("good paragraphs pass", _validate(paths, GOOD) == set(), str(_validate(paths, GOOD)))
    harness.check("no file", "SPEECH_MISSING" in harness.codes(speech_validator.validate_speech_record(_make_paths(), False)))
    harness.check("no paragraph", _validate(paths, "# Speech\nsome text\n") == {"SPEECH_UNREADABLE"})
    harness.check("a line not in the transcript", "SPEECH_LINE_UNKNOWN" in _validate(paths, GOOD.replace("S0005 ·", "S0009 ·")))
    harness.check("a line left out between paragraphs",
                  "SPEECH_LINE_MISSING" in _validate(paths, GOOD.replace("## S0003-S0004", "## S0004")))
    harness.check("the last line left out", "SPEECH_LINE_MISSING" in _validate(paths, GOOD.split("## S0005")[0]))
    harness.check("a line in two paragraphs", "SPEECH_LINE_TWICE" in _validate(paths, GOOD.replace("## S0003-S0004", "## S0002-S0004")))
    repeated_end = GOOD + "\n## S0005 · Speaker A\nOkay, thanks.\n"
    harness.check("the last line written again after the paragraph that ends there is refused, never a crash",
                  "SPEECH_LINE_TWICE" in _validate(paths, repeated_end)
                  and "leave the paragraph out" in str(speech_validator.validate_speech_record(paths, False).findings),
                  str(_validate(paths, repeated_end)))
    harness.check("a speaker not written `Speaker <letter>`",
                  "SPEECH_SPEAKER_SHAPE" in _validate(paths, GOOD.replace("S0003-S0004 · Speaker B", "S0003-S0004 · Jonas")))
    harness.check("a paragraph with no text",
                  "SPEECH_PARAGRAPH_EMPTY" in _validate(paths, GOOD.replace("Okay, thanks. Then we look at the export format next week.\n", "")))
    harness.check("a paragraph summarised", "SPEECH_PARAGRAPH_SHORTENED" in _validate(paths, GOOD.replace(
        "Yes, it finished on Monday. It took four hours, which is twice as long as we planned, because the files were larger.",
        "Yes, done Monday.")))
    harness.check("a paragraph holding what nobody said", "SPEECH_PARAGRAPH_ADDED" in _validate(paths, GOOD.replace(
        "because the files were larger.", "because the files were larger. " + "The team will add more servers to make "
        "the job faster, and the budget for them was approved by the board in its last session as well.")))
    harness.check("a profile asked for and not given", "SPEECH_PROFILE_UNKNOWN" in _validate(paths, GOOD, True))
    harness.check("a profile not in the list", "SPEECH_PROFILE_UNKNOWN" in _validate(
        paths, GOOD.replace("# Speech\n", "# Speech\n\nProfile: poem\n"), True))
    harness.check("a profile from the list passes", _validate(
        paths, GOOD.replace("# Speech\n", "# Speech\n\nProfile: meeting\n"), True) == set())


def test_the_message_holds_every_line_and_asks_for_a_profile_only_when_no_tag_named_one():
    lines = speech_markdown_utils.list_numbered_lines({"segments": SEGMENTS})
    ctx = _make_context(fakes.scripted())
    asked = attempt_request_builder.build_standalone_request(ctx, "speech_structuring", inputs={
        "lines": lines, "tags": ["week 42"], "is_profile_asked": True, "recording": "Jonas-weekly.mp3"})
    text = speech_structuring_messages.build_speech_structuring_messages(asked)[1].content[0]["text"]
    harness.check("every line with its id and time", "S0003 [00:08] yes it it finished on Monday" in text
                  and "from S0001 to S0005" in text, text)
    harness.check("the tags as a hint", "The owner tagged it: week 42." in text, text)
    harness.check("the profiles to pick from", "`language-lesson`:" in text and "Profile: <name>" in text, text)
    quiet = attempt_request_builder.build_standalone_request(ctx, "speech_structuring", inputs={
        "lines": lines, "tags": [], "is_profile_asked": False})
    text = speech_structuring_messages.build_speech_structuring_messages(quiet)[1].content[0]["text"]
    harness.check("no profile asked for when a tag named one", "Profile: <name>" not in text and "tagged" not in text, text)
    system = speech_structuring_messages.build_speech_structuring_messages(quiet)[0].content
    system = system if isinstance(system, str) else str(system)
    harness.check("a turn is split at every point, never kept whole for being one turn (DEFECTS.md 65)",
                  "Write one paragraph for each point the speaker makes in the turn. A long turn is not one paragraph just "
                  "because one person speaks" in " ".join(system.split()), system[:300])


def test_an_empty_frame_list_is_right_only_for_an_audio_recording():
    paths = _make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.AUDIO_MEDIUM, "items": []})
    harness.check("an audio recording's empty list passes", loop_items_validator.validate_loop_items(paths).ok)
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION, "items": []})
    harness.check("a video's empty list is still refused",
                  "C_EMPTY" in harness.codes(loop_items_validator.validate_loop_items(paths)))


def test_an_audio_run_goes_from_phase_1_to_the_speech_and_the_graph_has_the_step():
    harness.check("audio", routing.route_after_transcribe({"phases": {"transcribe": "complete"}, "is_audio": True})
                  == "speech_structuring")
    harness.check("video", routing.route_after_transcribe({"phases": {"transcribe": "complete"}}) == "frames_preparing")
    drawn = graph_inspection.build_graph_for_inspection().get_graph().draw_mermaid()
    harness.check("the graph holds the step", "speech_structuring" in drawn, drawn)


def test_the_node_writes_the_paragraphs_the_empty_frame_list_and_the_model_s_profile():
    model = fakes.scripted(fakes.says(GOOD.replace("# Speech\n", "# Speech\n\nProfile: meeting\n")))
    ctx = _make_context(model)
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phase 2 complete, and phase 3 with nothing to read", state["phases"] == {
        "frames_preparing": "complete", "frames_processing": "complete"} and state["queue"] == [], str(state))
    harness.check("02_speech.md holds the three paragraphs",
                  len((speech_file_service.read_speech(ctx.paths) or {"paragraphs": []})["paragraphs"]) == 3)
    frames = phase_file_service.read_phase_file(ctx.paths, 2)
    harness.check("the frame list is empty and says it is audio", frames["items"] == [] and frames["medium"] == "audio",
                  str(frames))
    harness.check("the profile the model picked, written as its", ingesting_profile_service.read_profile(
        ctx.paths.output_root) == {"profile": "meeting", "chosenBy": "model"})
    harness.check("one call", model.call_count == 1, str(model.call_count))
    rows = performance_log_service.read_rows(ctx.run_dir)
    harness.check("the call is the phase's one step, and the phase line counts its call",
                  [(row["level"], row.get("slug"), row.get("calls")) for row in rows
                   if row.get("phase") == "frames_preparing"]
                  == [("part", output_contract.SPEECH_SLUG, 1), ("phase", None, 1)], str(rows))


def test_a_run_ending_at_phase_1_neither_writes_nor_fails_phase_2_defect():
    # Defect: a PDF run limited to phase 1 (`--to 1`) recorded phase 2 failed, "02_speech.md is missing ... run --phase 2
    # first", though phase 2 was not asked for (DEFECTS 488). Fix: a run ending before phase 2 leaves it alone
    model = fakes.scripted(fakes.says(GOOD))
    ctx = _make_context(model)
    state = prepare_speech_node.prepare_speech_node({"phases": {}, "first_phase": 1, "last_phase": 1},
                                                    {"configurable": {"ctx": ctx}})
    harness.check("no call, no paragraphs written", model.call_count == 0
                  and speech_file_service.read_speech(ctx.paths) is None, str(model.call_count))
    harness.check("phase 2 is not recorded failed", ctx.store.get_phase("frames_preparing") != "failed"
                  and state["phases"].get("frames_preparing") != "failed", str(state))


def test_a_tag_naming_a_profile_decides_it_and_the_model_is_not_asked():
    model = fakes.scripted(fakes.says(GOOD))
    ctx = _make_context(model)
    run_tags_service.write_tags(ctx.paths.output_root, ["AcmeSync", "learn German"])
    prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    sent = model.calls[0][-1].content[0]["text"]
    harness.check("the profile from the tag", ingesting_profile_service.read_profile(ctx.paths.output_root)
                  == {"profile": "language-lesson", "chosenBy": "tag"})
    harness.check("the call was not asked for one", "Profile: <name>" not in sent, sent[-400:])


def test_a_profile_chosen_by_hand_wins_over_a_tag_and_the_model_is_not_asked():
    model = fakes.scripted(fakes.says(GOOD))
    ctx = _make_context(model)
    run_tags_service.write_tags(ctx.paths.output_root, ["learn German"])
    ingesting_profile_service.write_profile(ctx.paths.output_root, "meeting", "user")
    prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    sent = model.calls[0][-1].content[0]["text"]
    harness.check("the hand-picked profile stays, the tag does not replace it",
                  ingesting_profile_service.read_profile(ctx.paths.output_root) == {"profile": "meeting", "chosenBy": "user"},
                  str(ingesting_profile_service.read_profile(ctx.paths.output_root)))
    harness.check("the call was not asked for one", "Profile: <name>" not in sent, sent[-400:])


def test_paragraphs_refused_on_every_attempt_block_phase_2():
    model = fakes.scripted(fakes.says(GOOD.split("## S0005")[0]), fakes.says(GOOD.split("## S0005")[0]))
    ctx = _make_context(model)
    run_tags_service.write_tags(ctx.paths.output_root, ["chat"])
    previous = app_config.settings.budgets.max_attempts
    app_config.settings.budgets.max_attempts = 2
    try:
        state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    finally:
        app_config.settings.budgets.max_attempts = previous
    harness.check("blocked after its two attempts", state["phases"] == {"frames_preparing": "blocked"}
                  and model.call_count == 2, str(state))
    retry = model.calls[1][-1].content[0]["text"]
    harness.check("and the retry was told the missing line", "S0005 belongs to no paragraph" in retry, retry[-600:])
    harness.check("no frame list is written, so a resume and the page do not read phase 2 as done",
                  not phase_file_service.build_phase_file_path(ctx.paths, 2).exists())


def test_a_dry_run_renders_the_call_and_writes_nothing():
    model = fakes.scripted()
    ctx = _make_context(model)
    run_tags_service.write_tags(ctx.paths.output_root, ["chat"])
    state = prepare_speech_node.prepare_speech_node({"phases": {}, "is_dry_run": True}, {"configurable": {"ctx": ctx}})
    harness.check("no call, no speech, no profile, no frame list", model.call_count == 0
                  and speech_file_service.read_speech(ctx.paths) is None
                  and ingesting_profile_service.read_profile(ctx.paths.output_root) is None
                  and not (ctx.paths.output_root / output_contract.LOOP_ITEMS_FILE).exists(), str(state))


def test_phase_4_writes_from_one_entry_per_paragraph_with_its_clean_text():
    from services import integration_service
    from utils import document_plan_utils
    from utils import meeting_notes_utils

    paths = _make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.AUDIO_MEDIUM, "items": []})
    speech_file_service.build_speech_path(paths).write_text(GOOD, encoding="utf-8")
    integration = integration_service.build_integration(paths)
    selected = document_plan_utils.select_document_entries(integration)
    harness.check("one entry per paragraph, under its first line's id", [entry["id"] for entry in selected]
                  == ["S0001", "S0003", "S0005"], str([entry["id"] for entry in selected]))
    harness.check("each holds the paragraph's clean text and its lines",
                  selected[1]["text"].startswith("Yes, it finished on Monday.")
                  and selected[1]["segments"] == ["S0003", "S0004"], str(selected[1]))
    harness.check("and its speaker", [entry.get("speaker") for entry in selected] == ["Speaker A", "Speaker B", "Speaker A"])
    harness.check("every paragraph must be cited", document_plan_utils.list_required_ids(selected) == ["S0001", "S0003", "S0005"])
    notes = meeting_notes_utils.render_meeting_notes("Jonas-weekly.mp3", integration)
    harness.check("the notes file shows each paragraph under its speaker, with its lines",
                  "## 00:08–00:18 · Speaker B" in notes
                  and "S0003 Yes, it finished on Monday." in notes and "(lines S0003 to S0004)" in notes, notes)
    harness.check("and no raw line of a paragraph", "yes it it finished" not in notes, notes)


def test_a_brief_reply_need_not_be_cited_and_the_chapter_request_says_so():
    from services import integration_service
    from utils import document_plan_utils

    paths = _make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.AUDIO_MEDIUM, "items": []})
    brief = GOOD.replace("Okay, thanks. Then we look at the export format next week.", "Okay, thanks.")
    speech_file_service.build_speech_path(paths).write_text(brief, encoding="utf-8")
    selected = document_plan_utils.select_document_entries(integration_service.build_integration(paths))
    harness.check("a paragraph of four words or fewer is a brief reply, and only that one",
                  [entry.get("isBriefReply") for entry in selected] == [False, False, True], str(selected))
    harness.check("a brief reply need not be cited", document_plan_utils.list_required_ids(selected) == ["S0001", "S0003"],
                  str(document_plan_utils.list_required_ids(selected)))
    section = {"heading": "Import job", "topicId": "T01.1", "entries": selected, "suggestions": []}
    request = SimpleNamespace(inputs={"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "Import",
                                      "documentTitle": "Import"}, findings=[])
    text = document_messages.build_chapter_text(request)
    harness.check("the chapter request marks it", "S0005 [speech] (a brief reply) Okay, thanks." in text, text)


def test_a_pdfs_notes_head_each_paragraph_by_its_page_never_by_a_clock():
    """A PDF's page sits at its own minute of a timeline the document does not have: the writer's notes headed its
    paragraphs "05:00–05:15", a time no reader of the document can find."""

    from services import integration_service
    from utils import meeting_notes_utils

    paths = _make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.PDF_TEXT_ONLY_MEDIUM, "items": []})
    speech_file_service.build_speech_path(paths).write_text(GOOD, encoding="utf-8")
    notes = meeting_notes_utils.render_meeting_notes("report.pdf", integration_service.build_integration(paths))
    harness.check("each paragraph is headed by its page", "## Page 1" in notes and "00:08" not in notes
                  and "00:00" not in notes, notes)


def test_a_pdf_has_no_brief_replies():
    """A PDF's short paragraph is a heading or a label of the document, never a reply: NIST's next chapter's heading
    reached the writer marked "(a brief reply)", and the writer set it aside as one."""

    from services import integration_service
    from utils import document_plan_utils

    paths = _make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.PDF_TEXT_ONLY_MEDIUM, "items": []})
    brief = GOOD.replace("Okay, thanks. Then we look at the export format next week.", "Okay, thanks.")
    speech_file_service.build_speech_path(paths).write_text(brief, encoding="utf-8")
    selected = document_plan_utils.select_document_entries(integration_service.build_integration(paths))
    harness.check("no paragraph of a PDF is a brief reply", not any(entry.get("isBriefReply") for entry in selected),
                  str(selected))


def test_a_pdf_chapter_starts_at_the_heading_paragraph_before_its_first_draft_heading():
    """A PDF chapter starts at its first draft heading, and the paragraph holding only the chapter's own heading, just
    before it, was left to the chapter before (NIST's "## Appendix C. Glossary" in Appendix B's chapter)."""

    from utils import document_plan_utils

    parts = [{"number": 1, "title": "Appendix B"}, {"number": 2, "title": "Appendix C. Glossary"}]
    groups = [[{"id": "S0110", "text": "## **Appendix B**"}, {"id": "S0111", "text": "Table 2 shows the tiers."}],
              [{"id": "S0115", "text": "## **Appendix C. Glossary**"}, {"id": "S0116", "text": "**CSF Category** A group."}]]
    drafts = [{"chapter": 1, "headings": [{"title": "Appendix B", "first_entry_id": "S0110"}]},
              {"chapter": 2, "headings": [{"title": "CSF Category", "first_entry_id": "S0116"}]}]
    contents = document_plan_utils.build_table_of_contents(parts, groups, drafts, True)
    harness.check("chapter 2 starts at its heading's paragraph", contents["chapters"][1]["first_entry_id"] == "S0115",
                  str(contents["chapters"]))


def test_a_pdf_chapter_starts_at_the_paragraph_opening_with_its_own_heading():
    """A PDF chapter whose own heading opens a paragraph of its text, its first draft heading a sub-heading further on,
    starts at that paragraph: started at the sub-heading, the chapter's opening paragraphs were written in the chapter
    before (Wikipedia's "Light-dependent reactions", its first draft heading "Z scheme")."""

    from utils import document_plan_utils

    parts = [{"number": 1, "title": "Membranes"}, {"number": 2, "title": "Light-dependent reactions"}]
    groups = [[{"id": "S0021", "text": "## **Membranes**\nThylakoids hold the pigments."}],
              [{"id": "S0027", "text": "## **Light-dependent reactions**\nLight drives two photosystems."},
               {"id": "S0028", "text": "The electrons pass along a chain."},
               {"id": "S0032", "text": "### **Z scheme**\nThe scheme draws the energy levels."}]]
    drafts = [{"chapter": 1, "headings": [{"title": "Membranes", "first_entry_id": "S0021"}]},
              {"chapter": 2, "headings": [{"title": "Z scheme", "first_entry_id": "S0032"}]}]
    contents = document_plan_utils.build_table_of_contents(parts, groups, drafts, True)
    harness.check("chapter 2 starts at the paragraph its heading opens", contents["chapters"][1]["first_entry_id"] == "S0027",
                  str(contents["chapters"]))


def test_a_pdf_s_agenda_names_no_event():
    """A document is no event: NIST's agenda named one, "NIST CSF 2.0 Presentation", and the published document was
    called a presentation, folder `nist-csf-2-0-presentation`. Its title names it; a meeting keeps its event."""

    from services import document_file_service

    paths = _make_paths()
    document_file_service.write_parts(paths, {"points": [], "event_name": "Quarterly review"})
    harness.check("a meeting keeps its event", document_file_service.read_parts(paths)["event_name"] == "Quarterly review", "")
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.PDF_TEXT_ONLY_MEDIUM, "items": []})
    document_file_service.write_parts(paths, {"points": [], "event_name": "NIST CSF 2.0 Presentation"})
    harness.check("a PDF names no event", document_file_service.read_parts(paths)["event_name"] == "",
                  str(document_file_service.read_parts(paths)))


def test_a_diagram_note_too_large_to_draw_need_not_be_cited():
    """A diagram note of more than ten arrows is never drawn in the text, and its frame's cut shows it: the wiki's
    chapter 2 was refused twice for leaving out the 15-arrow diagram of a figure it had placed. A small one is cited."""

    from utils import document_plan_utils

    large = "flowchart LR\n" + "\n".join(f"    N{index} --> N{index + 1}" for index in range(15))
    entries = [{"id": "F4.6", "kind": "fact", "text": "PSII passes electrons to PSI."},
               {"id": "F4.7", "kind": "diagram", "text": large},
               {"id": "F5.8", "kind": "diagram", "text": "flowchart LR\n    A --> B"}]
    harness.check("the large diagram is not required, the small one is",
                  document_plan_utils.list_required_ids(entries) == ["F4.6", "F5.8"],
                  str(document_plan_utils.list_required_ids(entries)))


def test_a_pdf_paragraph_of_a_heading_and_a_few_words_need_not_be_cited():
    """A PDF paragraph holding its section's heading and a figure's short caption teaches nothing: the wiki's
    "### **Z scheme**" with "The "Z scheme"" under it was refused as uncovered. A heading over a sentence is cited."""

    from utils import document_plan_utils

    entries = [{"id": "S0032", "kind": "speech", "text": '### **Z scheme**\nThe "Z scheme"'},
               {"id": "S0033", "kind": "speech", "text": "### **Cycle**\nIn plants the reactions occur in the thylakoids."},
               # A bold label read as its own paragraph, "**Chloroplast ultrastructure** :" (the wiki's second run)
               {"id": "S0023", "kind": "speech", "text": "**Chloroplast ultrastructure** :"},
               {"id": "S0024", "kind": "speech", "text": "**Note:** the stroma is an aqueous fluid inside the inner membrane."}]
    harness.check("a heading or bold label with a caption is optional, the heading or label over a sentence is not",
                  document_plan_utils.list_required_ids(entries) == ["S0033", "S0024"],
                  str(document_plan_utils.list_required_ids(entries)))


def test_a_diagram_note_of_a_pdf_figure_need_not_be_cited():
    """A PDF figure is always placed as its cut, so its diagram note is the figure the reader sees: the wiki's second
    run's chapter 3 was checked again for F4.7, the diagram note of the thylakoid figure it had placed."""

    from utils import document_plan_utils

    entries = [{"id": "F4.6", "kind": "fact", "text": "PSII passes electrons to PSI.", "frames": [4]},
               {"id": "F4.7", "kind": "diagram", "text": "flowchart LR\n    A --> B", "frames": [4]},
               {"id": "F9.2", "kind": "diagram", "text": "flowchart LR\n    C --> D", "frames": [9]}]
    harness.check("the figure's diagram note is optional, a meeting frame's is not",
                  document_plan_utils.list_required_ids(entries, figure_frames={4}) == ["F4.6", "F9.2"],
                  str(document_plan_utils.list_required_ids(entries, figure_frames={4})))


def test_a_resume_takes_an_earlier_answer_that_passes_the_checks_as_they_are_now_defect():
    # An audio talk was asked again on a resume after its checks were mended, and the call, told the last refusal,
    # summarised the speech; an earlier attempt's paragraphs that pass the checks as they are now are taken, no call
    model = fakes.scripted(fakes.says(GOOD.split("## S0005")[0]))
    ctx = _make_context(model)
    run_tags_service.write_tags(ctx.paths.output_root, ["chat"])
    earlier = ctx.paths.output_root / "logs" / "t" / "frames_preparing" / output_contract.SPEECH_SLUG / "attempt-1"
    (earlier / "output-snapshot").mkdir(parents=True)
    (earlier / "output-snapshot" / output_contract.SPEECH_FILE).write_text(GOOD, encoding="utf-8")
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phase 2 is complete with no call", state["phases"].get("frames_preparing") == "complete"
                  and model.call_count == 0, f"{state} · calls {model.call_count}")
    harness.check("the earlier paragraphs are the speech", speech_file_service.read_speech(ctx.paths) is not None)
