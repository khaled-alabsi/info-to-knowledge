"""Phase 3 for an audio recording whose ingesting profile declares preparation steps: the items first, then one call per item, then a view of the whole;
each answer checked and kept under `03_prepared/`, a resume asking only what is missing, and phase 4 writing from the
prepared notes. Every name is invented."""

import argparse
import json
import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx
from support import harness

import config as app_config

from support import graph_inspection
from graph import routing
from graph.nodes import prepare_notes_node
from graph.nodes import prepare_speech_node
from models import output_contract
from models.pipeline_errors import PreprocessingDeclarationError
from models.preprocessing_step_spec import EACH_INPUT_PREFIX, SPEECH_INPUT
from models.run_context import RunContext
from models.run_identity import RunIdentity
from prompts import preprocessing_messages
from prompts import system_prompt_builder
from graph.nodes.retry_loop import attempt_request_builder
from services import ingesting_profile_service
from services import phase_file_service
from services import prepared_file_service
from services import preprocessing_step_service
from services import speech_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import prepared_markdown_utils
from validators import prepared_validator

# Eight lines of an invented sprint planning: two items, the second taken up again at the end
SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "first the export to the new format for the AcmeSync finance team"},
    {"start": 4.0, "end": 8.0, "text": "I would say five points Jonas takes it"},
    {"start": 8.0, "end": 12.0, "text": "then the login page redesign with single sign on"},
    {"start": 12.0, "end": 16.0, "text": "that is eight points and it waits on the Gateway team"},
    {"start": 16.0, "end": 20.0, "text": "so we commit the export and leave the login page out"},
    {"start": 20.0, "end": 24.0, "text": "our capacity is twenty points this sprint"},
]
SPEECH = """# Speech

Profile: sprint-planning

## S0001-S0002 · Speaker A
First, the export to the new format for the AcmeSync finance team. I would say 5 points; Jonas takes it.

## S0003-S0004 · Speaker B
Then the login page redesign with single sign-on. That is 8 points, and it waits on the Gateway team.

## S0005-S0006 · Speaker A
So we commit the export and leave the login page out. Our capacity is twenty points this sprint.
"""
ITEMS = """# The items discussed

## Export to the new format

### What it is
Exporting in the new format for the finance team.
[S0001, S0005]

## Login page redesign

### What it is
A new login page with single sign-on.
[S0003-S0005]
"""
DETAIL_SECTIONS = ["Goal", "Estimate", "Owner", "Dependencies and risks", "Decision", "Open points"]


def _detail(title: str, estimate: str, cited: str) -> str:
    lines = [f"# Each item in detail", "", f"## {title}", ""]
    for name in DETAIL_SECTIONS:
        text = {"Estimate": f"The team estimates it at {estimate} points.", "Open points": "Not said."}.get(name,
                                                                                                            f"{name} of {title}.")
        lines += [f"### {name}", text] + ([] if name == "Open points" else [f"[{cited}]"]) + [""]

    return "\n".join(lines)


SPRINT = """# The sprint as committed

## The sprint

### Sprint goal
Not said.

### Committed items
- Export to the new format, 5 points.
[S0001, S0005]

### Capacity
Twenty points.
[S0005]

### Left out
The login page redesign.
[S0005]
"""


def _make_paths(profile: str | None = "sprint-planning"):
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="Jonas-planning.mp3")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": SEGMENTS})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.AUDIO_MEDIUM, "items": []})
    speech_file_service.build_speech_path(paths).write_text(SPEECH, encoding="utf-8")
    if profile:
        ingesting_profile_service.write_profile(paths.output_root, profile, "tag")

    return paths


def _make_context(model, profile: str | None = "sprint-planning") -> RunContext:
    paths = _make_paths(profile)
    run_dir = paths.output_root / "logs" / "t"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=model)


def _paragraphs(paths) -> list[dict]:
    return speech_file_service.read_speech(paths)["paragraphs"]


def _items_step() -> dict:
    return preprocessing_step_service.describe_steps(preprocessing_step_service.read_plan("sprint-planning").steps)[0]


def _codes(paths, text: str, step: dict | None = None, item_number: int | None = None, paragraphs=None,
           is_every_paragraph_due: bool = True) -> set[str]:
    step = step or _items_step()
    path = prepared_file_service.build_prepared_path(paths, step["id"], item_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    return harness.codes(prepared_validator.validate_prepared_record(
        paths, step, item_number, paragraphs if paragraphs is not None else _paragraphs(paths), is_every_paragraph_due))


def _run_node(ctx, state=None) -> dict:
    return prepare_notes_node.prepare_notes_node(state or {"phases": {}}, {"configurable": {"ctx": ctx}})


def test_sprint_planning_is_a_profile_its_tags_pick_and_its_document_gets_its_file():
    harness.check("by its alias", ingesting_profile_service.find_profile_in_tags(["Sprint Planning"]) == "sprint-planning")
    prompt = system_prompt_builder.build_system_prompt("write", {"ingesting_profile": "sprint-planning"})
    harness.check("phase 4 reads its file", "Ingesting profile: sprint planning" in prompt, prompt[-600:])


def test_each_profile_declares_its_steps_and_a_profile_without_a_file_has_none():
    plans = {name: preprocessing_step_service.read_plan(name) for name in ingesting_profile_service.list_profile_names()}
    harness.check("the steps of the plan's table", {name: [step.step_id for step in plan.steps] for name, plan in plans.items()} == {
        "knowledge": [], "presentation": ["topics", "topic_details"], "conversation": [],
        "meeting": ["items", "item_details"], "language-lesson": ["units", "unit_details"],
        "sprint-change": ["changes", "change_details"], "sprint-planning": ["items", "item_details", "sprint"], "takeaway": []},
        str({name: [step.step_id for step in plan.steps] for name, plan in plans.items()}))
    harness.check("a long-meeting and a long-lecture profile run only past their paragraphs",
                  plans["meeting"].min_paragraphs > 0 and plans["presentation"].min_paragraphs > 0
                  and plans["sprint-planning"].min_paragraphs == 0)
    harness.check("every step reads the speech or the items of an earlier step",
                  all(step.input == SPEECH_INPUT or step.input.startswith(EACH_INPUT_PREFIX)
                      for plan in plans.values() for step in plan.steps))
    harness.check("item details go through the items", plans["sprint-planning"].steps[1].detailed_step_id == "items"
                  and plans["sprint-planning"].steps[2].detailed_step_id is None)


def test_every_declared_step_reaches_its_call_with_its_own_file():
    for name in ingesting_profile_service.list_profile_names():
        for step in preprocessing_step_service.read_plan(name).steps:
            prompt = system_prompt_builder.build_system_prompt("preprocessing", {"preprocessing_step": f"{name}/{step.step_id}"})
            harness.check(f"{name}/{step.step_id} gets its step's file and every section is taught",
                          "--- BEGIN BASE: preprocessing-" in prompt
                          and all(f"### {section}" in prompt for section in step.sections), prompt[-900:])
            harness.check(f"{name}/{step.step_id}: the not-said text is rendered", "Not said." in prompt
                          and "{{" not in prompt)


def test_a_declaration_that_cannot_be_followed_is_refused_by_name():
    root = Path(tempfile.mkdtemp())
    (root / "preprocessing").mkdir()
    for bad, words in [({"steps": [{"id": "a", "input": "each:b", "sections": ["X"]}]}, "reads 'each:b'"),
                       ({"steps": [{"id": "a", "input": "speech", "sections": []}]}, "names no section"),
                       ({"steps": [{"id": "a", "input": "speech", "sections": ["X"]},
                                   {"id": "a", "input": "speech", "sections": ["X"]}]}, "twice"),
                       ({"steps": [{"id": "a", "input": "speech", "sections": ["X"]},
                                   {"id": "b", "input": "each:a", "sections": ["X"]},
                                   {"id": "c", "input": "each:b", "sections": ["X"]}]}, "reads 'each:b'")]:
        (root / "preprocessing" / "made-up.json").write_text(json.dumps(bad), encoding="utf-8")
        try:
            preprocessing_step_service.read_plan("made-up", root)
            harness.check(f"refused: {words}", False)
        except PreprocessingDeclarationError as error:
            harness.check(f"refused: {words}", words in str(error), str(error))


def test_a_declaration_file_that_is_not_json_or_not_an_object_is_refused_naming_the_file():
    root = Path(tempfile.mkdtemp())
    (root / "preprocessing").mkdir()
    for text, label in (("{", "invalid JSON"), ("[]", "a list"), ('{"steps": ["not an object"]}', "a step that is a string")):
        (root / "preprocessing" / "made-up.json").write_text(text, encoding="utf-8")
        try:
            preprocessing_step_service.read_plan("made-up", root)
            outcome = "read"
        except PreprocessingDeclarationError as error:
            outcome = str(error)
        except (ValueError, AttributeError) as error:
            outcome = f"{type(error).__name__} without the file: {error}"
        harness.check(f"{label} is refused by the file's name", outcome.startswith("made-up.json"), outcome)


def test_the_answer_is_read_and_written_back_the_same():
    parsed, why = prepared_markdown_utils.parse_prepared(ITEMS)
    harness.check("two items, read", parsed is not None and [item["title"] for item in parsed["items"]]
                  == ["Export to the new format", "Login page redesign"], why)
    harness.check("the ids apart from the text", parsed["items"][1]["sections"][0]["cited"] == [("S0003", "S0005")]
                  and parsed["items"][1]["sections"][0]["text"] == "A new login page with single sign-on.")
    harness.check("rendered back the same", prepared_markdown_utils.render_prepared("The items discussed", parsed) == ITEMS,
                  prepared_markdown_utils.render_prepared("The items discussed", parsed))
    harness.check("text before the first item is unreadable",
                  prepared_markdown_utils.parse_prepared("Here are the items:\n## A\n### B\nx [S0001]")[0] is None)
    harness.check("text between an item and its first section is unreadable",
                  "before its first" in prepared_markdown_utils.parse_prepared("## A\nloose\n### B\nx [S0001]")[1])


def test_the_validator_accepts_good_notes_and_names_each_fault():
    paths = _make_paths()
    harness.check("good items pass", _codes(paths, ITEMS) == set(), str(_codes(paths, ITEMS)))
    harness.check("unreadable", _codes(paths, "nothing here") == {"PREP_UNREADABLE"})
    harness.check("a section missing", "PREP_SECTION_MISSING" in _codes(paths, ITEMS.replace("### What it is\nA new", "A new", 1).replace(
        "## Login page redesign\n\nA new", "## Login page redesign\n\n### Wrong\nA new")))
    harness.check("a section naming no paragraph", "PREP_IDS_MISSING" in _codes(paths, ITEMS.replace("[S0001, S0005]\n", "")))
    harness.check("not said needs no id", "PREP_IDS_MISSING" not in _codes(paths, ITEMS.replace(
        "Exporting in the new format for the finance team.\n[S0001, S0005]", "Not said.")))
    harness.check("an id of no paragraph", "PREP_ID_UNKNOWN" in _codes(paths, ITEMS.replace("S0001, S0005", "S0001, S0009")))
    harness.check("a paragraph in no item", _codes(paths, ITEMS.replace("[S0001, S0005]", "[S0001]").replace("[S0003-S0005]", "[S0003]"))
                  == {"PREP_PARAGRAPH_UNCOVERED"})
    harness.check("a number no paragraph says",
                  "PREP_NUMBER_UNSOURCED" in _codes(paths, ITEMS.replace("single sign-on.", "single sign-on, 13 points.")))
    harness.check("a number word counts as its digits", "PREP_NUMBER_UNSOURCED" not in _codes(
        paths, ITEMS.replace("single sign-on.", "single sign-on, 20 points.")))
    german = [{**paragraph, "text": paragraph["text"].replace("twenty", "zwanzig")} for paragraph in _paragraphs(paths)]
    harness.check("a German number word counts as its digits too", "PREP_NUMBER_UNSOURCED" not in _codes(
        paths, ITEMS.replace("single sign-on.", "single sign-on, 20 points."), paragraphs=german))
    umlaut = [{**paragraph, "text": paragraph["text"].replace("twenty", "fünf")} for paragraph in _paragraphs(paths)]
    harness.check("a number word with an umlaut is read", "PREP_NUMBER_UNSOURCED" not in _codes(
        paths, ITEMS.replace("single sign-on.", "single sign-on, 5 points."), paragraphs=umlaut))
    detail_step = preprocessing_step_service.describe_steps(preprocessing_step_service.read_plan("sprint-planning").steps)[1]
    two = _detail("Export to the new format", "5", "S0001") + "\n" + _detail("Other", "5", "S0001").split("\n", 1)[1]
    harness.check("a call about one item answers one", "PREP_ITEM_COUNT" in _codes(paths, two, detail_step, 1,
                                                                                 _paragraphs(paths)[:1], False))
    harness.check("a good detail passes", _codes(paths, _detail("Export to the new format", "5", "S0001"), detail_step, 1,
                                                 _paragraphs(paths)[:1], False) == set())


def test_the_steps_run_in_order_one_call_per_item_and_phase_4_writes_from_them():
    model = fakes.scripted(fakes.says(ITEMS), fakes.says(_detail("Export to the new format", "5", "S0001")),
                           fakes.says(_detail("Login page redesign", "8", "S0003")), fakes.says(SPRINT))
    ctx = _make_context(model)
    state = _run_node(ctx)
    harness.check("phase 3 complete after four calls", state["phases"] == {"frames_processing": "complete"}
                  and model.call_count == 4, f"{state} {model.call_count}")
    harness.check("the files", sorted(path.relative_to(prepared_file_service.build_prepared_dir(ctx.paths)).as_posix()
                                      for path in prepared_file_service.build_prepared_dir(ctx.paths).rglob("*.md"))
                  == ["item_details/01.md", "item_details/02.md", "items.md", "sprint.md"])
    second = model.calls[2][-1].content[0]["text"]
    harness.check("the second item's call holds only its paragraphs and what the first step wrote of it",
                  "## S0003-S0004 · Speaker B" in second and "## S0005-S0006" in second and "## S0001-S0002" not in second
                  and "A new login page with single sign-on." in second and 'item 2 of 2' in second, second[:1500])
    harness.check("each call gets its step's file", "--- BEGIN BASE: preprocessing-sprint-planning-item-details.md"
                  in model.calls[2][0].content)
    last = model.calls[3][-1].content[0]["text"]
    harness.check("the view of the whole reads every earlier answer", "# What the earlier steps wrote" in last
                  and "The team estimates it at 8 points." in last, last[:2000])


def test_a_resume_asks_only_the_calls_not_done():
    first = fakes.scripted(fakes.says(ITEMS), fakes.says(_detail("Export to the new format", "5", "S0001")),
                           fakes.says("nothing readable"), fakes.says("nothing readable"))
    ctx = _make_context(first)
    previous = app_config.settings.budgets.max_attempts
    app_config.settings.budgets.max_attempts = 2
    try:
        state = _run_node(ctx)
        harness.check("blocked on the second item", state["phases"] == {"frames_processing": "blocked"}
                      and state["blocked"] == ["prepared-item_details-02"], str(state))
        ctx.store = RunStore.load(ctx.run_dir / "run.json")
        _run_node(ctx, {"phases": {}, "is_dry_run": True})
        rendered = sorted(path.name for path in (ctx.run_dir / "dry-run" / "frames_processing").iterdir())
        harness.check("a dry run after the block rehearses the call still due, never one already done",
                      rendered == ["prepared-item_details-02"], str(rendered))
        second = fakes.scripted(fakes.says(_detail("Login page redesign", "8", "S0003")), fakes.says(SPRINT))
        ctx.model = second
        ctx.store = RunStore.load(ctx.run_dir / "run.json")
        state = _run_node(ctx)
    finally:
        app_config.settings.budgets.max_attempts = previous
    harness.check("the resume asked only the second item and the view of the whole",
                  state["phases"] == {"frames_processing": "complete"} and second.call_count == 2, str(state))
    third = fakes.scripted()
    ctx.model = third
    ctx.store = RunStore.load(ctx.run_dir / "run.json")
    harness.check("a third life asks nothing", _run_node(ctx)["phases"] == {"frames_processing": "complete"}
                  and third.call_count == 0)


def test_a_profile_with_no_step_closes_phase_3_and_routing_skips_it():
    model = fakes.scripted()
    ctx = _make_context(model, "conversation")
    harness.check("nothing to prepare", _run_node(ctx)["phases"] == {"frames_processing": "complete"}
                  and model.call_count == 0)
    harness.check("routes: steps due go to phase 3", routing.route_after_speech_structuring(
        {"phases": {"frames_preparing": "complete"}}) == "prepare_notes")
    harness.check("routes: phase 3 closed by phase 2 goes on to phase 4", routing.route_after_speech_structuring(
        {"phases": {"frames_preparing": "complete", "frames_processing": "complete"}}) == "finalize")
    harness.check("routes: a blocked preparation ends the run", routing.route_after_prepare_notes(
        {"phases": {"frames_processing": "blocked"}}) == "done")
    drawn = graph_inspection.build_graph_for_inspection().get_graph().draw_mermaid()
    harness.check("the graph holds the step", "prepare_notes" in drawn, drawn)


def test_phase_2_leaves_phase_3_open_only_when_steps_are_due():
    for profile, expected in [("sprint-planning", {"frames_preparing": "complete"}),
                              ("conversation", {"frames_preparing": "complete", "frames_processing": "complete"})]:
        ctx = _make_context(fakes.scripted(), profile)
        state = prepare_speech_node.prepare_speech_node({"phases": {}, "first_phase": 3}, {"configurable": {"ctx": ctx}})
        harness.check(f"{profile}: phase 2 read from disk", state["phases"] == expected, str(state))


def test_a_missing_speech_file_is_recorded_failed_in_the_run_record():
    ctx = _make_context(fakes.scripted())
    speech_file_service.build_speech_path(ctx.paths).unlink()
    state = _run_node(ctx)
    harness.check("phase 3 fails", state["phases"] == {"frames_processing": "failed"}, str(state))
    harness.check("and its run record says so", ctx.store.get_phase("frames_processing") == "failed",
                  str(ctx.store.get_phase("frames_processing")))
    ctx = _make_context(fakes.scripted(), "conversation")
    speech_file_service.build_speech_path(ctx.paths).unlink()
    state = prepare_speech_node.prepare_speech_node({"phases": {}, "first_phase": 3}, {"configurable": {"ctx": ctx}})
    harness.check("phase 2 read from disk fails", state["phases"] == {"frames_preparing": "failed"}, str(state))
    harness.check("and its run record says so", ctx.store.get_phase("frames_preparing") == "failed",
                  str(ctx.store.get_phase("frames_preparing")))


def test_a_dry_run_renders_the_first_call_and_calls_no_model():
    model = fakes.scripted()
    ctx = _make_context(model)
    state = _run_node(ctx, {"phases": {}, "is_dry_run": True})
    harness.check("no call, no file", model.call_count == 0 and state["phases"] == {"frames_processing": "complete"}
                  and not prepared_file_service.build_prepared_dir(ctx.paths).exists(), str(state))


def test_the_message_names_the_step_its_sections_and_the_coverage_rule():
    paths = _make_paths()
    ctx = _make_context(fakes.scripted())
    step = _items_step()
    inputs = {"profile": "sprint-planning", "step": step, "step_number": 1, "step_count": 3, "item_number": None,
              "item_title": None, "paragraphs": _paragraphs(paths), "earlier": "", "is_every_paragraph_due": True,
              "tags": ["sprint planning"], "recording": "Jonas-planning.mp3"}
    messages = preprocessing_messages.build_preprocessing_messages(
        attempt_request_builder.build_standalone_request(ctx, "preprocessing", inputs=inputs))
    text = messages[-1].content[0]["text"]
    harness.check("the step, its sections, the coverage rule and the paragraphs",
                  "## The items discussed: step 1 of 3" in text and "### What it is" in text
                  and "Every paragraph below belongs to at least one item" in text and "## S0005-S0006 · Speaker A" in text,
                  text)


def test_phase_4_writes_from_the_prepared_notes_and_the_paragraphs_they_cite_need_no_citing():
    from services import integration_service
    from utils import document_plan_utils
    from utils import document_text_utils
    from utils import meeting_notes_utils

    model = fakes.scripted(fakes.says(ITEMS), fakes.says(_detail("Export to the new format", "5", "S0001")),
                           fakes.says(_detail("Login page redesign", "8", "S0003")), fakes.says(SPRINT))
    ctx = _make_context(model)
    _run_node(ctx)
    integration = integration_service.build_integration(ctx.paths)
    selected = document_plan_utils.select_document_entries(integration)
    prepared = [entry["id"] for entry in selected if entry["kind"] == "prepared"]
    harness.check("the item details and the sprint are entries; the items step they detail is not, nor a not-said "
                  "section, each item's sections together at its start",
                  prepared == ["P2.1.1", "P2.1.2", "P2.1.3", "P2.1.4", "P2.1.5", "P3.1.2", "P3.1.3", "P3.1.4",
                               "P2.2.1", "P2.2.2", "P2.2.3", "P2.2.4", "P2.2.5"], str(prepared))
    harness.check("each holds its section and the paragraphs it rests on",
                  next(entry for entry in selected if entry["id"] == "P2.2.2")["text"] == "Estimate: The team estimates it at 8 points."
                  and next(entry for entry in selected if entry["id"] == "P2.2.2")["paragraphs"] == ["S0003"])
    required = document_plan_utils.list_required_ids(selected)
    harness.check("a paragraph a prepared note cites need not be cited; one no note cites still must",
                  "S0001" not in required and "S0003" not in required and "S0005" not in required
                  and all(entry_id in required for entry_id in prepared), str(required))
    notes = meeting_notes_utils.render_meeting_notes("Jonas-planning.mp3", integration)
    harness.check("the notes file shows each item before the paragraph it starts at",
                  "## 00:08 · Each item in detail: Login page redesign\n- P2.2.1 Goal: Goal of Login page redesign. "
                  "(paragraphs S0003)" in notes
                  and notes.index("Each item in detail: Login page redesign") < notes.index("S0003 Then the login page"),
                  notes)
    harness.check("a prepared id is an entry id the document's text never shows",
                  document_text_utils.remove_bracketed_entry_ids("Committed [P2.1.5, S0005].") == "Committed."
                  and document_text_utils.find_entry_ids("It is P2.1.5 now.") == ["P2.1.5"])


def test_a_conversion_prepares_the_notes_again_only_when_the_two_profiles_prepare_them_in_other_steps():
    from cli import convert_command
    from services import profile_convert_service

    paths = _make_paths("sprint-planning")
    prepared = prepared_file_service.build_prepared_path(paths, "items", None)
    prepared.parent.mkdir(parents=True)
    prepared.write_text(ITEMS, encoding="utf-8")
    run = paths.output_root
    harness.check("to a profile with other steps: from phase 3",
                  profile_convert_service.select_first_phase(run, "sprint-change") == 3
                  and profile_convert_service.select_first_phase(run, "conversation") == 3)
    harness.check("to a profile whose steps do not run on so few paragraphs: from phase 3, the steps run differ",
                  profile_convert_service.select_first_phase(run, "meeting") == 3)
    target = profile_convert_service.copy_run_for_profile(run, "sprint-change")
    harness.check("phase 3's prepared notes are not copied when phase 3 runs again",
                  not (target / output_contract.PREPARED_DIR).exists() and (target / output_contract.SPEECH_FILE).is_file())
    ingesting_profile_service.write_profile(run, "conversation", "tag")
    harness.check("between two profiles that prepare nothing for it: from phase 4",
                  profile_convert_service.select_first_phase(run, "knowledge") == 4
                  and profile_convert_service.select_first_phase(run, "meeting") == 4)
    harness.check("a video's run: from phase 4", profile_convert_service.select_first_phase(Path(tempfile.mkdtemp()), "meeting") == 4)
    parser = argparse.ArgumentParser()
    convert_command.add_convert_arguments(parser)
    harness.check("the command takes the new profile as --to",
                  parser.parse_args(["--output", str(run), "--to", "sprint-planning"]).ingesting_profile == "sprint-planning")


def test_the_page_counts_each_step_s_calls_done():
    from services import preprocessing_call_service

    paths = _make_paths()
    harness.check("before any call: the item details are not counted yet", preprocessing_call_service.describe_progress(paths) == [
        {"title": "The items discussed", "done": 0, "total": 1}, {"title": "Each item in detail", "done": 0, "total": None},
        {"title": "The sprint as committed", "done": 0, "total": 1}], str(preprocessing_call_service.describe_progress(paths)))
    _codes(paths, ITEMS)
    _codes(paths, _detail("Export to the new format", "5", "S0001"),
           preprocessing_step_service.describe_steps(preprocessing_step_service.read_plan("sprint-planning").steps)[1], 1,
           _paragraphs(paths)[:1], False)
    harness.check("after the items and one detail", [(step["done"], step["total"]) for step in
                                                     preprocessing_call_service.describe_progress(paths)] == [(1, 1), (1, 2), (0, 1)],
                  str(preprocessing_call_service.describe_progress(paths)))
    harness.check("a run that prepares nothing has no steps", preprocessing_call_service.describe_progress(
        _make_paths("conversation")) == [])


def test_a_step_s_files_are_listed_by_their_number_and_a_stray_file_is_no_item():
    paths = _make_paths()
    folder = prepared_file_service.build_prepared_dir(paths) / "details"
    folder.mkdir(parents=True)
    for name in ("9.md", "10.md", "100.md", "11.md", "notes.md"):
        (folder / name).write_text("x", encoding="utf-8")
    listed = [path.name for path in prepared_file_service.list_step_paths(paths, "details")]
    harness.check("numbered files in numeric order, the stray one left out",
                  listed == ["9.md", "10.md", "11.md", "100.md"], str(listed))


def test_a_declaration_whose_sections_or_minimum_are_of_the_wrong_type_is_refused_naming_the_file():
    root = Path(tempfile.mkdtemp())
    (root / "preprocessing").mkdir()
    for text, label in (('{"steps": [{"id": "a", "input": "speech", "sections": "Goal"}]}', "sections as a string"),
                        ('{"minParagraphs": "sixty", "steps": []}', "a minimum that is no number")):
        (root / "preprocessing" / "made-up.json").write_text(text, encoding="utf-8")
        try:
            preprocessing_step_service.read_plan("made-up", root)
            outcome = "read"
        except PreprocessingDeclarationError as error:
            outcome = str(error)
        except (ValueError, TypeError) as error:
            outcome = f"{type(error).__name__} without the file: {error}"
        harness.check(f"{label} is refused by the file's name", outcome.startswith("made-up.json"), outcome)


def test_a_decimal_number_is_held_as_said_and_never_as_another_number():
    """The number check dropped the decimal point: "1.5 days" held "15", and a section saying "15 days" passed."""

    from validators import prepared_validator

    held = prepared_validator._list_held_numbers(["The rollout takes 1.5 days for 1,200 tenants."])
    harness.check("1.5 is held, 15 is not", "1.5" in held and "15" not in held, str(held))
    harness.check("a thousands mark is still dropped", "1200" in held, str(held))


def test_a_dry_run_of_a_profile_with_no_step_records_nothing():
    """A rehearsal of a profile that prepares nothing wrote phase 3 complete into the run record, which a real run
    then read as done."""

    ctx = _make_context(fakes.scripted(), "knowledge")
    _run_node(ctx, {"phases": {}, "is_dry_run": True})
    harness.check("the run record holds no phase 3 after a dry run", ctx.store.get_phase("frames_processing") != "complete",
                  str(ctx.store.get_phase("frames_processing")))
