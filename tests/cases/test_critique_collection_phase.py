"""Phase 6, the critique collection: every batch's critique of one run, collected into one set of anti-pattern rules.

The owner asked for a phase at the end that collects every critique to learn from. What is checked here is the split
of work: the PROGRAM groups the rules that repeat and counts the batches and frames each hit, and the model is asked
for wording only. A group it drops is left out, a rule it writes about a meeting is refused, and the phase is held
while anything earlier is blocked, because a rule learned from a run with a hole teaches the hole.
"""

import logging

from langchain_core.messages import AIMessage

import config as app_config
from support import fakes
from support import frame_batch_fixtures
from support import harness

from graph import routing
from graph.nodes import collect_critiques_node
from models import output_contract
from prompts import critique_collection_messages
from services import critique_collection_service
from services import performance_log_service
from services import phase_hold_service
from services import record_file_service
from services import run_recording_service
from utils import finding_text_utils
from validators import item_validator


ARROW = {"name": "An arrow is kept from the frame before without checking its head",
         "what_it_is": "the newest diagram is handed over in full, and a frame that only adds a box keeps an arrow "
                       "whose direction its own frame contradicts.",
         "how_to_avoid": "read every arrow's head on the frame in front of you before you keep it.",
         "frames": [0]}
TABLE = {"name": "A table is outlined without its row labels",
         "what_it_is": "the outline names the table and its columns and stops there.",
         "how_to_avoid": "quote every row's label, in the order the rows stand.",
         "frames": [1]}


def _write_critiques(ctx,
                     *batches: tuple[str, list[dict]]) -> None:
    """One critique file per batch, as phase 3 would have left them."""

    for slug, rules in batches:
        record_file_service.write_critique_record(ctx.paths, f"{slug}-critique",
                                                  {"is_nothing_to_criticise": not rules, "rules": rules})


def _load_context():
    return frame_batch_fixtures.load_run_context(fakes.scripted(), count=2)


def test_the_program_groups_the_rules_and_counts_them_not_the_model():
    """Two batches wording one anti-pattern differently are one group, with both their frames counted."""

    ctx = _load_context()
    _write_critiques(ctx,
                     ("frames-000-001", [ARROW, TABLE]),
                     ("frames-002-003", [{**ARROW, "name": "An arrow is kept from the previous frame without "
                                                            "checking its head", "frames": [2, 3]}]))
    groups = critique_collection_service.group_rules(critique_collection_service.read_run_rules(ctx.paths))
    harness.check("the two wordings of one anti-pattern are one group, most frequent first",
                  [group["name"] for group in groups] == [ARROW["name"], TABLE["name"]],
                  str([group["name"] for group in groups]))
    harness.check("with both batches and every frame of both counted",
                  (len(groups[0]["batches"]), groups[0]["frames"]) == (2, 3), str(groups[0]))
    harness.check("and every wording reaches the model, so the rule it writes covers both",
                  len(groups[0]["wordings"]) == 2, str(groups[0]["wordings"]))
    rendered = critique_collection_messages._render_groups(groups)
    harness.check("the request names each group with its counts and its wordings",
                  "Group 1" in rendered and "Seen in 2 batches, 3 frames." in rendered
                  and "Wording 2:" in rendered, rendered)


def test_the_rule_set_keeps_the_programs_counts_and_the_models_wording():
    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", [ARROW, TABLE]))
    groups = critique_collection_service.group_rules(critique_collection_service.read_run_rules(ctx.paths))
    worded = {ARROW["name"]: {"name": "An arrow is kept without checking its head",
                              "what_it_is": "a diagram handed over keeps an arrow this frame contradicts.",
                              "how_to_avoid": "read each arrow's head on the frame before keeping it."}}
    text = critique_collection_service.render_rule_set(groups, worded)
    harness.check("the kept rule carries the model's wording", "read each arrow's head on the frame" in text, text)
    harness.check("and the program's own count", "Seen in: 1 batch, 1 frame" in text, text)
    harness.check("the group the model dropped is left out, and the file says how many",
                  TABLE["name"] not in text and "1 dropped as one meeting's content" in text, text)


def test_a_rule_the_model_names_after_no_group_is_dropped():
    """The instruction asks for each name to stay close to its group's; a rule answering no group is not a rule of
    this run, and is never written into the file."""

    groups = critique_collection_service.group_rules([{**ARROW, "batch": "frames-000-001"}])
    harness.check("a name close to the group's matches it",
                  (critique_collection_service.match_group(groups, "An arrow is kept from the frame before without checking its "
                                                       "head") or {}).get("name") == ARROW["name"])
    harness.check("a name about something else matches none",
                  critique_collection_service.match_group(groups, "The agenda is written before the chapters") is None)


def test_the_rule_set_is_refused_when_a_rule_is_short_or_about_a_meeting():
    ctx = _load_context()
    path = critique_collection_service.build_rule_set_path(ctx.paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Critique\n\n## An arrow is kept without checking its head\n"
                    "What it is: frame 7 kept the arrow it was handed.\n"
                    "How to avoid it: read each arrow's head.\n", encoding="utf-8")
    codes = harness.codes(item_validator.validate_critique_collection_record(ctx.paths, [ARROW["name"]]))
    harness.check("a rule naming a frame is refused: the file is read into the instructions",
                  "CRITIQUE_COLLECTION_RULE_NOT_GENERIC" in codes, str(codes))
    path.write_text("# Critique\n\n## An arrow is kept without checking its head\n"
                    "What it is: a diagram handed over keeps an arrow this frame contradicts.\n", encoding="utf-8")
    short = harness.codes(item_validator.validate_critique_collection_record(ctx.paths, [ARROW["name"]]))
    harness.check("a rule missing how to avoid it is refused by that line's name",
                  "CRITIQUE_COLLECTION_RULE_SHAPE" in short, str(short))
    path.unlink()
    missing = harness.codes(item_validator.validate_critique_collection_record(ctx.paths, [ARROW["name"]]))
    harness.check("and no file at all is refused by its name", "CRITIQUE_COLLECTION_MISSING" in missing, str(missing))


def test_the_phase_is_held_by_its_switch_and_by_anything_blocked_before_it():
    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", [ARROW]))
    previous = app_config.settings.critique_collection.is_on
    app_config.settings.critique_collection.is_on = False
    try:
        state = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    finally:
        app_config.settings.critique_collection.is_on = previous
    harness.check("switched off the phase is off and writes no rule set, so a Balanced run ends without it",
                  state["phases"]["critique_collection"] == "off" and not critique_collection_service.build_rule_set_path(ctx.paths).is_file(),
                  str(state))
    ctx.store.set_phase("frames_processing", "blocked")
    held = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("and held while an earlier phase is blocked", held["phases"]["critique_collection"] == "held", str(held))
    reasons = phase_hold_service.list_hold_reasons(ctx.store, collect_critiques_node.PHASE_NUMBER)
    harness.check("which names the blocked phase as the reason", reasons == ["phase 3 (frames_processing) blocked"],
                  str(reasons))


def test_the_phase_completes_having_written_nothing_when_no_critique_holds_a_rule():
    """The critique switched off, or every batch finding nothing: there is nothing to learn from, and that is no
    failure."""

    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", []))
    state = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("complete, with no rule set written",
                  state["phases"]["critique_collection"] == "complete"
                  and not critique_collection_service.build_rule_set_path(ctx.paths).is_file(), str(state))


def test_the_phase_line_counts_the_call_that_worded_the_rules():
    """The phase line said "no model call" after a ten-minute call: the call's unit was never recorded (lecture-1,
    2026-10-15)."""

    reply = AIMessage(content=f"# Critique\n\n## {ARROW['name']}\nWhat it is: {ARROW['what_it_is']}\n"
                              f"How to avoid it: {ARROW['how_to_avoid']}\n")
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(reply), count=2)
    _write_critiques(ctx, ("frames-000-001", [ARROW]))
    state = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    phase = [row for row in performance_log_service.read_rows(ctx.run_dir)
             if row.get("level") == "phase" and row.get("phase") == "critique_collection"]
    harness.check("the phase completes", state["phases"]["critique_collection"] == "complete", str(state))
    harness.check("and its line counts the one call", phase and phase[-1].get("calls") == 1, str(phase))


def test_phase_6_is_the_last_phase_and_no_run_waits_for_it_to_be_done():
    harness.check("phase 6 comes before phase 7, the last, and writes its rule set",
                  output_contract.PHASE_NUMBERS["critique_collection"] == 6
                  and max(output_contract.PHASE_FILES) == output_contract.PHASE_NUMBERS["document_reshaping"] == 7
                  and output_contract.PHASE_FILES[6]["file"].endswith(output_contract.CRITIQUE_COLLECTION_FILE))
    harness.check("the review routes into it when it was asked for, and to the end when it was not",
                  routing.route_after_review({"last_phase": 6}) == "critique_collection"
                  and routing.route_after_review({"last_phase": 5}) == "done")
    done = {"status": "complete", "phases": {name: "complete" for name in output_contract.PHASE_NUMBERS
                                             if name != "critique_collection"}}
    harness.check("a run whose phase 6 never ran is still a finished run: its document is complete",
                  run_recording_service.is_run_done(done), str(done))


def test_a_dry_run_records_nothing_in_the_run_record():
    """A rehearsal over a real run's record (`--dry-run --resume`) must not leave phase 6 looking done."""

    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", [ARROW]))
    state = collect_critiques_node.collect_critiques_node({"phases": {}, "is_dry_run": True}, {"configurable": {"ctx": ctx}})
    harness.check("the graph goes on as if the phase were done", state["phases"]["critique_collection"] == "complete", str(state))
    harness.check("but the run record does not say so", ctx.store.get_phase("critique_collection") != "complete",
                  str(ctx.store.get_phase("critique_collection")))


def test_a_dry_run_starts_no_timed_unit_it_never_ends():
    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", [ARROW]))
    lines = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    ctx.log.addHandler(handler)
    ctx.log.setLevel(logging.INFO)
    collect_critiques_node.collect_critiques_node({"phases": {}, "is_dry_run": True}, {"configurable": {"ctx": ctx}})
    ctx.log.removeHandler(handler)
    harness.check("the console shows no unit with no outcome after it", not any(line.endswith(" …") for line in lines),
                  str(lines))


def test_a_dry_run_with_no_critique_records_nothing_either():
    ctx = _load_context()
    state = collect_critiques_node.collect_critiques_node({"phases": {}, "is_dry_run": True}, {"configurable": {"ctx": ctx}})
    harness.check("the graph goes on", state["phases"]["critique_collection"] == "complete", str(state))
    harness.check("and the run record is left as it was", ctx.store.get_phase("critique_collection") != "complete",
                  str(ctx.store.get_phase("critique_collection")))


def test_a_finding_with_no_fix_is_rendered_without_one():
    text = finding_text_utils.render_retry_section([{"code": "CRITIQUE_COLLECTION_RULE_EMPTY", "detail": "rule 2 holds no words"}])
    harness.check("no None stands in for the fix", "None" not in text and "CRITIQUE_COLLECTION_RULE_EMPTY" in text, text)


def test_a_retry_is_told_every_finding_not_the_console_s_first_two():
    text = finding_text_utils.render_retry_section([{"code": f"CRITIQUE_COLLECTION_CODE_{n}", "detail": f"problem {n}", "fix": "do it"}
                                                    for n in (1, 2, 3)])
    harness.check("the third finding reaches the model", "CRITIQUE_COLLECTION_CODE_3" in text and "more)" not in text, text)


def test_an_audio_recording_s_flow_has_no_critique_collection():
    """An audio run's phase 6 showed "complete · 0s" with nothing learned: an audio recording writes no critique, and
    its flow leaves the critics out (the owner)."""

    from services import phase_file_service
    ctx = _load_context()
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    phase_file_service.write_phase_file(ctx.paths, frames_number, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                                   "medium": output_contract.AUDIO_MEDIUM, "items": []})
    state = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phase 6 is recorded off, in the state and in the run record",
                  state["phases"]["critique_collection"] == "off" and ctx.store.get_phase("critique_collection") == "off", str(state))


def test_a_rule_worded_with_letters_of_another_script_keeps_only_its_english_defect():
    # Defect: a collected rule read "The notes截取 (cut off) a direct quotation", two Chinese letters inside its English.
    # Fix: letters of another script are dropped, and the English gloss in brackets after them stands in their place.
    ctx = _load_context()
    _write_critiques(ctx, ("frames-000-001", [ARROW, TABLE]))
    groups = critique_collection_service.group_rules(critique_collection_service.read_run_rules(ctx.paths))
    worded = {ARROW["name"]: {"name": "A quote is 截取 cut short",
                              "what_it_is": "The notes截取 (cut off) a direct quotation before its end.",
                              "how_to_avoid": "quote each sentence whole."}}
    text = critique_collection_service.render_rule_set(groups, worded)
    harness.check("no letter of another script is left, and the gloss stands where the letters were",
                  "截" not in text and "The notes cut off a direct quotation before its end." in text
                  and "## A quote is cut short" in text, text)


def test_a_rule_worded_afresh_is_matched_by_its_group_number_defect():
    # Defect: phase 6 worded every group afresh from all its wordings, no name read like its group's, and 0 rules were kept.
    # Fix: each answer heading opens with its group's number, which the program matches, and the number is left out.
    reply = AIMessage(content="# Critique\n\n## Group 2: Rows of a table lose their labels\n"
                              f"What it is: {TABLE['what_it_is']}\nHow to avoid it: {TABLE['how_to_avoid']}\n")
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(reply), count=2)
    _write_critiques(ctx, ("frames-000-001", [ARROW, TABLE]))
    state = collect_critiques_node.collect_critiques_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    text = critique_collection_service.build_rule_set_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the phase completes", state["phases"]["critique_collection"] == "complete", str(state))
    harness.check("the rule is kept under its own name, without the number",
                  "## Rows of a table lose their labels" in text and "Group 2" not in text, text)


def test_groups_the_model_names_together_are_one_rule_with_their_counts_added_defect():
    # One anti-pattern described in other words stood as four groups, the names reading too little alike to be grouped.
    # Now a rule headed with several group numbers merges them, and the program adds their batches and frames
    quoted = {**TABLE, "name": "A label on the screen nobody quoted", "frames": [4, 5]}
    groups = critique_collection_service.group_rules([{**TABLE, "batch": "frames-000-001"}, {**ARROW, "batch": "frames-000-001"},
                                                      {**quoted, "batch": "frames-004-005"}])
    numbers = {group["name"]: position for position, group in enumerate(groups, start=1)}
    rule = {"name": f"Groups {numbers[TABLE['name']]}, {numbers[quoted['name']]}: Visible text is left unquoted",
            "what_it_is": "screen text is left out.", "how_to_avoid": "quote every line the screen shows."}
    merged, worded = critique_collection_service.merge_worded_groups(groups, [rule])
    joined = next(group for group in merged if group["name"] in worded)
    harness.check("the two groups are one", len(merged) == 2 and len(worded) == 1, str(merged))
    harness.check("their batches joined and frames added", (len(joined["batches"]), joined["frames"]) == (2, 3), str(joined))
    harness.check("the rule keeps the model's name without the numbers", worded[joined["name"]]["name"]
                  == "Visible text is left unquoted", str(worded))
    text = critique_collection_service.render_rule_set(merged, worded)
    harness.check("the rule set counts them together", "Seen in: 2 batches, 3 frames" in text, text)
