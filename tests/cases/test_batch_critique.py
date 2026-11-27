"""The critique reports and changes nothing; the repair that follows fixes the notes from what it reported.

The owner: the self-critique must not find and fix in one reply. It writes the mistakes to a file first, as generic
anti-pattern rules, which is an artifact the monitor validates and phase 6 collects; a second call then repairs the
frames from those rules. Each of the two has its own switch, and a rule about this meeting - a frame number in the
rule itself, a quote off these screens - is refused where it is written, since the file is carried into the worker's
instructions.
"""

import json

from langchain_core.messages import AIMessage

import config as app_config
from support import fakes
from support import fixtures as fx
from support import frame_batch_fixtures
from support import harness

from graph.nodes.retry_loop import attempt_request_builder
from schemas import critique_record
from services import frame_records_service
from services import call_log_service
from services import phase_file_service
from models import output_contract
from prompts import compaction_prompt_loader
from services import thinking_compaction_service
from services import record_file_service
from prompts import frames_processing_messages
from utils import critique_markdown_utils
from validators import item_validator


RULE = {"name": "An arrow is kept from the frame before without checking its head",
        "what_it_is": "the newest diagram is handed over in full, and a frame that only adds a box keeps an arrow "
                      "whose direction its own frame contradicts.",
        "how_to_avoid": "read every arrow's head on the frame in front of you before you keep it.",
        "frames": [0]}

# The frames of the one batch these tests run
_BATCH_SIZE = 2

def _batch_answer():
    """Phase 3's own answer for the two frames these tests run: the batch's notes, its account and its mood."""

    return fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY,
                             atmosphere=frame_batch_fixtures.ATMOSPHERE,
                             frames=[{**frame_batch_fixtures.RECORD, "frame": 0},
                                     {**frame_batch_fixtures.RECORD, "frame": 1}])


def _repair_request(ctx,
                    rules: list[dict]):
    """A repair's request carrying the rules its critique reported, for the one function that renders them."""

    return attempt_request_builder.build_standalone_request(ctx, "second_pass",
                                                            inputs={"batch": {"critiqueRules": rules}})


def _run(*script):
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(*script), count=_BATCH_SIZE)
    state = frame_batch_fixtures.run_frames_processing(ctx, [f"frame-{index:03d}" for index in range(_BATCH_SIZE)],
                                                         frames_per_call=_BATCH_SIZE)

    return ctx, state


def test_the_critique_writes_its_rules_and_touches_no_frame():
    """The point of the split: the critique is an artifact, and the notes are unchanged by it."""

    group = _batch_answer()
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False):
        ctx, state = _run(group, fakes.critique([RULE]), fakes.nothing_open())
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("the rule is in the critique file, with its two lines and its frame",
                  [rule["name"] for rule in record["rules"]] == [RULE["name"]]
                  and record["rules"][0]["how_to_avoid"] == RULE["how_to_avoid"]
                  and critique_markdown_utils.list_rule_frames(record["rules"][0]) == [0], str(record))
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("and no frame's notes changed: the critique repairs nothing",
                  records[0]["facts"] == frame_batch_fixtures.RECORD["facts"], str(records[0]["facts"]))
    harness.check("both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"],
                  str(state["written"]))


def test_the_repair_is_handed_the_rules_the_critique_reported():
    """The repair's message opens with the rules, so the fix answers the critique instead of starting afresh."""

    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=_BATCH_SIZE)
    rendered = frames_processing_messages.render_critique_rules(_repair_request(ctx, [RULE]))
    harness.check("the rule's name, both its lines and its frames reach the repair",
                  RULE["name"] in rendered and RULE["how_to_avoid"] in rendered and "frame 0" in rendered, rendered)
    harness.check("and nothing is rendered when the critique found none",
                  not frames_processing_messages.render_critique_rules(_repair_request(ctx, [])))


def test_a_rule_about_this_meeting_is_refused_where_it_is_written():
    """The file is carried into `frames_processing.md`, so a frame number in the rule itself, or a quote off this
    meeting's screen, is the one thing that must not pass."""

    group = _batch_answer()
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False):
        ctx, _state = _run(group, fakes.critique([RULE]), fakes.nothing_open())
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False,
        "rules": [{**RULE, "what_it_is": "frame 0 kept the arrow it was handed."}]})
    codes = harness.codes(item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1]))
    harness.check("a frame number in the rule itself is refused", "CRITIQUE_RULE_NOT_GENERIC" in codes, str(codes))

    screen = '- slide: white\n  - text "the nightly export window": centre'
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False,
        "rules": [{**RULE, "how_to_avoid": 'quote "the nightly export window" whenever it is shown.'}]})
    quoted = harness.codes(item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1],
                                                                   [screen]))
    harness.check("a rule quoting this meeting's screen is refused too", "CRITIQUE_RULE_NOT_GENERIC" in quoted,
                  str(quoted))
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False,
        "rules": [{**RULE, "how_to_avoid": "quote \u201cthe nightly export window\u201d whenever it is shown."}]})
    curly = harness.codes(item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1], [screen]))
    harness.check("a quote in curly marks is refused the same way", "CRITIQUE_RULE_NOT_GENERIC" in curly, str(curly))
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False,
        "rules": [{**RULE, "what_it_is": 'the outline says "the grid scrolls to the right" where only a cell changed.'}]})
    own_words = harness.codes(item_validator.validate_critique_record(
        ctx.paths, "frames-000-001-critique", [0, 1], ['- grid: the grid scrolls to the right, "Orders" header on top']))
    harness.check("a quote of the outline's own words, never of the screen's text, is generic",
                  "CRITIQUE_RULE_NOT_GENERIC" not in own_words, str(own_words))
    harness.check("and the same rule passes without that quote",
                  "CRITIQUE_RULE_NOT_GENERIC" not in harness.codes(
                      item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1])))


def test_a_rule_quoting_the_screen_is_written_with_the_quote_masked_and_passes():
    """Critiques were refused again and again for quoting a label off the screen, and each refusal blocked frames whose
    notes had passed: the program masks the screen's words where it writes the rule, so the meeting's words never
    reach the rule file and the critique is no longer refused for them."""

    screen = '- toolbar: top\n  - button "Export the nightly report": right'
    rule = {**RULE, "what_it_is": 'a button labelled "Export the nightly report" is listed in the wrong group.'}
    masked = critique_markdown_utils.hide_screen_quotes(rule, [screen])
    harness.check("the screen's quote is masked, the rest of the line kept",
                  masked["what_it_is"] == f"a button labelled {critique_markdown_utils.SCREEN_TEXT_MASK} is listed in "
                                          "the wrong group.", masked["what_it_is"])
    own = {**RULE, "what_it_is": 'the outline says "the grid scrolls to the right" where only a cell changed.'}
    harness.check("a quote of the outline's own words stays as written",
                  critique_markdown_utils.hide_screen_quotes(own, [screen]) == own)
    outlined = {**frame_batch_fixtures.RECORD, "screen": f'{frame_batch_fixtures.RECORD["screen"]}\n{screen}'}
    batch = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY,
                              atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**outlined, "frame": 0}, {**outlined, "frame": 1}])
    quoting = {**RULE, "how_to_avoid": 'list "Export the nightly report" in its own group.'}
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, state = _run(batch, fakes.critique([quoting]), fakes.nothing_open())
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("in a run the critique passes on its one answer and both frames are written",
                  sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))
    harness.check("and the rule file holds the mask, never the screen's words",
                  [rule["how_to_avoid"] for rule in record["rules"]]
                  == [f"list {critique_markdown_utils.SCREEN_TEXT_MASK} in its own group."], str(record["rules"]))



def test_a_ticket_key_the_screen_shows_is_hidden_in_a_rule_s_lines():
    """A rule gave two of a board's ticket keys, unquoted, as its example (board recording, 2026-10-22)."""

    screen = '- card "ACME-1042": left\n  - title "Retry the export": below'
    rule = {"name": "Card details on the wrong card", "what_it_is": "the notes give ACME-1042 the text of another card",
            "how_to_avoid": "check each card's key and text, 3 rows at most", "frames": [2]}
    masked = critique_markdown_utils.hide_screen_keys(rule, [screen])
    harness.check("the key is masked, a plain number stays",
                  "ACME-1042" not in masked["what_it_is"] and critique_markdown_utils.SCREEN_TEXT_MASK in masked["what_it_is"]
                  and "3 rows" in masked["how_to_avoid"], str(masked))

def test_a_frame_number_in_a_rule_s_own_lines_is_written_hidden_and_the_rule_passes():
    """Critiques were refused for a frame number in a rule's own lines, one of them quoting the program's own build-step
    line as its example: the numbers are hidden where the rule is written, and its frames stay on its `Seen on` line."""

    numbered = {**RULE, "what_it_is": 'a step writes "step 2 of the screen of frame 53" and frames 54, 55 and 56 too.'}
    hidden = critique_markdown_utils.hide_frame_numbers(numbered)
    harness.check("every frame number in the line is hidden, a list of them as one",
                  hidden["what_it_is"] == 'a step writes "step 2 of the screen of frame <n>" and frames <n> too.',
                  hidden["what_it_is"])
    harness.check("and the rule's own frames are kept", hidden["frames"] == RULE["frames"], str(hidden["frames"]))
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, state = _run(_batch_answer(), fakes.critique([{**RULE, "how_to_avoid": "check frame 0 again."}]),
                          fakes.nothing_open())
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("in a run the critique passes on its one answer, the number hidden",
                  sorted(state["written"]) == ["frame-000", "frame-001"]
                  and record["rules"][0]["how_to_avoid"] == "check frame <n> again.", str(record["rules"]))


def test_a_refused_critique_is_asked_again_within_the_attempt_budget():
    """A critique changes no frame, so a refused rule (here one naming a frame of another batch) is asked again, told
    why, as long as `budgets.maxAttempts` allows; frames whose notes passed are blocked only when the last answer is
    refused too."""

    group = _batch_answer()
    elsewhere = {**RULE, "frames": [97]}
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(3):
        ctx, state = _run(group, fakes.critique([elsewhere]), fakes.critique([RULE]), fakes.nothing_open())
    harness.check("the retry passes and both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"],
                  str(state["written"]))
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("the critique file holds the retried rule", record["rules"][0]["frames"] == RULE["frames"],
                  str(record))
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, _state = _run(group, fakes.critique([elsewhere]))
    statuses = [ctx.store.get_item(slug)["status"] for slug in ("frame-000", "frame-001")]
    harness.check("with one attempt the refused critique blocks its batch", statuses == ["blocked", "blocked"],
                  str(statuses))


def test_a_critique_stopped_after_checking_everything_is_answered_from_its_summary_in_the_same_attempt():
    """A critique whose thinking had checked both frames looped on after it, and each loop spent an attempt to write an
    answer its summary already held (lecture-1 and 38c3-fault-injection, 2026-10-15)."""

    group = _batch_answer()
    settled = AIMessage(content="## MISTAKES FOUND\nAn arrow kept from the frame before: frame 0\n\n## REJECTED\n"
                                "frame 1: the outline is whole\n\n## NOT CHECKED YET\nNone")
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, state = _run(group, fakes.cut_off("frame 0 checked, frame 1 checked. " * 20), settled,
                          fakes.critique([RULE]), fakes.nothing_open())
    harness.check("one attempt is enough: both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"],
                  str(state["written"]))
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("the critique file holds the rule written from the summary",
                  [rule["name"] for rule in record["rules"]] == [RULE["name"]], str(record))
    harness.check("the call that wrote it was handed the summary",
                  "An arrow kept from the frame before" in str(ctx.model.calls[3][-1].content), str(ctx.model.calls[3][-1]))
    logged = json.loads(call_log_service.build_call_log_path(ctx.paths, "frames-000-001-critique").read_text())
    harness.check("and its call log shows the request it was sent, the summary's message in it",
                  "An arrow kept from the frame before" in logged["request"]["human"], logged["request"]["human"][-300:])
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, _state = _run(group, fakes.cut_off("frame 0 checked, frame 1 checked. " * 20), settled,
                           AIMessage(content="Frame 0 has an arrow kept. Wait, frame 1 too?"))
    kept = list(ctx.run_dir.rglob("finish-reply.md"))
    harness.check("a reply from the summary that does not parse is kept beside the attempt (fault-injection 2026-10-15)",
                  len(kept) == 1 and "Wait, frame 1 too?" in kept[0].read_text(encoding="utf-8"), str(kept))
    rows = call_log_service.read_call_metrics(ctx.run_dir)
    cut_slugs = [row["slug"] for row in rows if row.get("finishReason") == "length"]
    # three calls, each a row: the cut one, the summary of its thinking (once left out) and the reply from the summary
    harness.check("and that call is counted in the call metrics beside the cut one and its summary",
                  len(cut_slugs) == 1 and sum(row["slug"] == cut_slugs[0] for row in rows) == 3,
                  str([(row.get("slug"), row.get("finishReason")) for row in rows]))
    harness.check("settled means NOT CHECKED YET holds only None",
                  thinking_compaction_service.is_settled("## NOT CHECKED YET\nNone.")
                  and not thinking_compaction_service.is_settled("## NOT CHECKED YET\nframe 4")
                  and not thinking_compaction_service.is_settled("## MISTAKES FOUND\nNone"))


def test_a_follow_up_looped_on_its_last_attempt_is_answered_from_its_summary_defect():
    # Under zero repair a repair looped, its summary left something unchecked, and its batch blocked: the next attempt
    # that would read the summary does not exist (38c3-797 frames 50 to 63, DEFECTS 539). The last attempt answers from it
    group = _batch_answer()
    unsettled = AIMessage(content="## MISTAKES FOUND\nNone\n\n## NOT CHECKED YET\nframe 1")
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, state = _run(group, fakes.cut_off("frame 0 checked. " * 20), unsettled, fakes.nothing_open())
    harness.check("the one attempt is answered from its summary and both frames are written",
                  sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))


def test_a_reply_from_the_summary_stopped_for_looping_keeps_its_rules_before_the_loop_defect():
    # Written from a looped critique's summary, the reply gave its rules and then ran through the instruction's list of
    # non-mistakes until the loop check stopped it; refused as looped, its batch blocked (Lecture 7 frames 1 to 13,
    # DEFECTS 552). The rules it gave on the summary's frames stand, and the batch goes on
    group = _batch_answer()
    settled = AIMessage(content="## MISTAKES FOUND\nAn arrow kept from the frame before\nFrames: 0\n\n## REJECTED\n"
                                "None\n\n## NOT CHECKED YET\nNone")
    looped = fakes.critique([RULE])
    looped.response_metadata = {"finish_reason": "thinking_looped"}
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, state = _run(group, fakes.cut_off("frame 0 checked. " * 20), settled, looped, fakes.nothing_open())
    harness.check("both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("and the rule given before the loop is kept", [rule["name"] for rule in record["rules"]] == [RULE["name"]],
                  str(record))


def test_a_critique_whose_summary_names_a_mistake_is_asked_again_from_the_mistakes_alone_defect():
    """A critique stopped for looping had found a mistake on one frame; the call writing its answer from the summary wrote
    rules on other frames, the program dropped every one, and the mistake was answered "Nothing to criticise" (release
    test 2's critiques of frames 63 to 67 and 68 to 73, DEFECTS 437). Asked once more from the mistakes alone."""

    group = _batch_answer()
    settled = AIMessage(content="## MISTAKES FOUND\nAn arrow kept from the frame before: frame 0\n\n## REJECTED\n"
                                "frame 1: the outline is whole\n\n## NOT CHECKED YET\nNone")
    on_other_frame = {**RULE, "name": "A label nobody quoted", "frames": [1]}
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, _state = _run(group, fakes.cut_off("frame 0 checked, frame 1 checked. " * 20), settled,
                           fakes.critique([on_other_frame]), fakes.critique([RULE]), fakes.nothing_open())
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("the rule of the mistake the summary names is in the critique file, not Nothing",
                  [rule["name"] for rule in record["rules"]] == [RULE["name"]], str(record))
    harness.check("the second call was handed the mistakes alone",
                  "found the mistakes below" in str(ctx.model.calls[4][-1].content)
                  and "frame 1: the outline is whole" not in str(ctx.model.calls[4][-1].content),
                  str(ctx.model.calls[4][-1].content)[:300])
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        ctx, _state = _run(group, fakes.cut_off("frame 0 checked, frame 1 checked. " * 20), settled,
                           fakes.critique([on_other_frame]), fakes.critique([on_other_frame]), fakes.nothing_open())
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("when the second answer is on other frames too, it is still Nothing to criticise", record["rules"] == [], str(record))


def test_a_critique_whose_thinking_found_no_mistake_is_answered_by_the_program():
    """Asked to write the answer from a summary saying "None found", a call with no thinking named two mistakes no
    note held, on every frame of the batch (pdf-02, 2026-10-21; DEFECTS 136)."""

    group = _batch_answer()
    clean = AIMessage(content="## MISTAKES FOUND\nNone found. Every outline quotes its rows.\n\n## REJECTED\n"
                              "frame 0: the table is whole\n\n## NOT CHECKED YET\nNone")
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        # Named the critique's, so the fake does not answer the critique itself
        cut = fakes.cut_off("frame 0 checked, frame 1 checked. " * 20)
        cut.name = fakes.CRITIQUE_NAME
        ctx, state = _run(group, cut, clean, fakes.nothing_open())
    harness.check("both frames are written in the one attempt", sorted(state["written"]) == ["frame-000", "frame-001"],
                  str(state["written"]))
    record = record_file_service.read_critique_record(ctx.paths, "frames-000-001-critique")
    harness.check("the critique is Nothing to criticise", record.get("rules") == [], str(record))
    finish_opening = compaction_prompt_loader.read_cut_thinking_finish().split("\n")[0]
    harness.check("no model was asked to write it", not any(finish_opening in str(call[-1].content)
                                                            for call in ctx.model.calls), f"calls={ctx.model.call_count}")
    harness.check("a summary that found a mistake is no such case",
                  not thinking_compaction_service.is_nothing_found("critique", "## MISTAKES FOUND\nAn arrow kept: frame 0")
                  and not thinking_compaction_service.is_nothing_found("second_pass", "## MISTAKES FOUND\nNone"))


def test_a_rule_short_of_a_line_or_naming_no_frame_is_refused():
    group = _batch_answer()
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False):
        ctx, _state = _run(group, fakes.critique([RULE]), fakes.nothing_open())
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False, "rules": [{**RULE, "how_to_avoid": ""}]})
    harness.check("a rule missing a line is refused by that line's name",
                  "CRITIQUE_RULE_SHAPE" in harness.codes(
                      item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1])))
    record_file_service.write_critique_record(ctx.paths, "frames-000-001-critique", {
        "is_nothing_to_criticise": False, "rules": [{**RULE, "frames": [97]}]})
    harness.check("a rule naming a frame of another batch is refused",
                  "CRITIQUE_FRAME_UNKNOWN" in harness.codes(
                      item_validator.validate_critique_record(ctx.paths, "frames-000-001-critique", [0, 1])))


def test_nothing_to_criticise_is_a_real_answer():
    answer, why = critique_markdown_utils.parse_critique(f"# {critique_record.NOTHING_TO_CRITICISE_TITLE}")
    harness.check("the heading alone reads as an answer, not an error",
                  answer == {"is_nothing_to_criticise": True, "rules": []} and not why, f"{answer} {why}")
    empty, reason = critique_markdown_utils.parse_critique("some thinking and no document at all")
    harness.check("and an answer with neither a rule nor that heading says what is missing",
                  empty is None and critique_record.NOTHING_TO_CRITICISE_TITLE in reason, reason)


def test_a_pdf_batch_has_no_critique_unless_its_own_switch_is_on():
    """On the first two PDF runs every critique looped, 5 of its 6 rules were false and the critique with its repair
    took longer than the reading (pdf-01, pdf-02)."""

    for is_pdf_on, has_critique in ((False, False), (True, True)):
        ctx = frame_batch_fixtures.load_run_context(fakes.scripted(_batch_answer(), fakes.critique([RULE]),
                                                                   fakes.nothing_open()), count=_BATCH_SIZE)
        frames = phase_file_service.read_phase_file(ctx.paths, 2)
        phase_file_service.write_phase_file(ctx.paths, 2, {**frames, "medium": output_contract.PDF_MEDIUM})
        with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False, is_pdf_critique_on=is_pdf_on):
            frame_batch_fixtures.run_frames_processing(ctx, [f"frame-{index:03d}" for index in range(_BATCH_SIZE)],
                                                       frames_per_call=_BATCH_SIZE)
        written = record_file_service.build_record_path(ctx.paths, "frames-000-001-critique").is_file()
        harness.check(f"a PDF batch with isPdfCritiqueOn {'on' if is_pdf_on else 'off'} "
                      f"{'is' if has_critique else 'is not'} criticised", written == has_critique, str(written))


def test_a_batch_of_pdf_figures_is_criticised_with_the_pdf_switch_off_defect():
    # Defect: a PDF's figure batch had no critique (isPdfCritiqueOn off), so a chart's misread curve reached the document
    # (DEFECTS 489). Fix: a batch of figures alone is criticised while isPdfFigureCritiqueOn is on
    for is_figure_on, has_critique in ((True, True), (False, False)):
        ctx = frame_batch_fixtures.load_run_context(fakes.scripted(_batch_answer(), fakes.critique([RULE]),
                                                                   fakes.nothing_open()), count=_BATCH_SIZE)
        frames = phase_file_service.read_phase_file(ctx.paths, 2)
        figures = [{**item, "medium": output_contract.PDF_FIGURE_MEDIUM} for item in frames["items"]]
        phase_file_service.write_phase_file(ctx.paths, 2, {**frames, "medium": output_contract.PDF_TEXT_ONLY_MEDIUM,
                                                           "items": figures})
        settings = app_config.settings.frames_processing
        previous, settings.is_pdf_figure_critique_on = settings.is_pdf_figure_critique_on, is_figure_on
        try:
            with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False, is_pdf_critique_on=False):
                frame_batch_fixtures.run_frames_processing(ctx, [f"frame-{index:03d}" for index in range(_BATCH_SIZE)],
                                                           frames_per_call=_BATCH_SIZE)
        finally:
            settings.is_pdf_figure_critique_on = previous
        written = record_file_service.build_record_path(ctx.paths, "frames-000-001-critique").is_file()
        harness.check(f"a figure batch with isPdfFigureCritiqueOn {'on' if is_figure_on else 'off'} "
                      f"{'is' if has_critique else 'is not'} criticised", written == has_critique, str(written))


def test_a_pdf_s_lone_figure_is_criticised_as_a_batch_defect():
    # Defect: a PDF of one figure asked it alone, with no account and no critique, so the fix that criticises figure
    # batches never reached it (DEFECTS 489, the onboarding guide run). Fix: a lone figure goes the batch's way
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(_batch_answer(), fakes.critique([RULE]),
                                                               fakes.nothing_open()), count=1)
    frames = phase_file_service.read_phase_file(ctx.paths, 2)
    figures = [{**item, "medium": output_contract.PDF_FIGURE_MEDIUM} for item in frames["items"]]
    phase_file_service.write_phase_file(ctx.paths, 2, {**frames, "medium": output_contract.PDF_TEXT_ONLY_MEDIUM,
                                                       "items": figures})
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False, is_pdf_critique_on=False):
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000"], frames_per_call=_BATCH_SIZE)
    critiques = sorted(path.name for path in record_file_service.build_record_path(ctx.paths, "x").parent.glob("*critique*"))
    harness.check("the lone figure's batch is criticised", bool(critiques), str(critiques))


def test_each_switch_off_skips_its_own_call():
    group = _batch_answer()
    with fx.critique_switches(is_critique_on=False, is_critique_repair_on=False):
        ctx, _state = _run(group, fakes.nothing_open())
    harness.check("both off: the batch call and its open questions, and no critique file",
                  ctx.model.call_count == 2
                  and not record_file_service.build_record_path(ctx.paths, "frames-000-001-critique").is_file(),
                  f"calls={ctx.model.call_count}")
    with fx.critique_switches(is_critique_on=False, is_critique_repair_on=True):
        ctx, _state = _run(group, fakes.nothing_open())
    harness.check("the repair alone is skipped: there is nothing to repair from",
                  ctx.model.call_count == 2, f"calls={ctx.model.call_count}")


def test_an_unreadable_critique_names_why_never_empty():
    paths = fx.make_paths()
    path = record_file_service.build_record_path(paths, "frames-000-001-critique")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("just some words, no heading at all\n", encoding="utf-8")
    codes = harness.codes(item_validator.validate_critique_record(paths, "frames-000-001-critique", [0, 1]))
    harness.check("an unreadable critique is CRITIQUE_UNREADABLE", codes == {"CRITIQUE_UNREADABLE"}, str(codes))


def test_a_critique_and_its_fix_are_judged_by_their_own_calls():
    """Their checks read the batch's reading call, which ended well, so a critique stopped mid-thought was refused as
    an empty answer and the loop was never named (2026-10-13)."""

    paths = fx.make_paths()
    looped = {"outcome": "completed", "detail": "", "parsed": None, "text": "", "thinking": "x" * 10,
              "finishReason": "thinking_looped", "parsingError": ""}
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frames-000-001"),
                                    {"response": {**looped, "finishReason": "stop", "parsed": {"ok": True}}})
    for slug in ("frames-000-001-critique", "frames-000-001-second-pass"):
        call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug), {"response": looped})
    critique = harness.codes(item_validator.validate_critique(paths, "frames-000-001", "frames-000-001-critique", [0, 1]))
    second = harness.codes(item_validator.validate_second_pass(paths, "frames-000-001", "frames-000-001-second-pass", [0, 1]))
    harness.check("the critique's loop is named a loop", critique == {"RESPONSE_THINKING_LOOPED"}, str(critique))
    harness.check("the fix's loop is named a loop", second == {"RESPONSE_THINKING_LOOPED"}, str(second))


def test_a_rule_names_where_it_was_seen_and_the_fix_is_handed_that():
    """The fix, handed a rule naming no spot, compared line after line to find it and looped (2026-10-13)."""

    text = ("# Critique\n\n## A label is quoted with the wrong casing\nWhat it is: the first letter differs.\n"
            "How to avoid it: copy the casing from the frame.\nSeen on: frames 3, 4\n"
            "Where: frame 3: the take-away quoting \"retry budget\"\nWhere: frame 4: the `## Screen` line of the\n"
            "  Gateway title\n")
    rule = critique_markdown_utils.parse_critique(text).critique["rules"][0]
    harness.check("each Where line is kept, a wrapped one joined", rule["where"] == [
        'frame 3: the take-away quoting "retry budget"', "frame 4: the `## Screen` line of the Gateway title"], str(rule))
    hidden = critique_markdown_utils.hide_screen_quotes(rule, ['- title: "retry budget"'])
    harness.check("the screen's words stay in Where, never hidden", hidden["where"] == rule["where"], str(hidden))
    harness.check("the critique file keeps them", "Where: frame 3:" in critique_markdown_utils.render_critique_document(
        {"rules": [rule]}))
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=_BATCH_SIZE)
    message = frames_processing_messages.render_critique_rules(_repair_request(ctx, [rule]))
    harness.check("the fix is handed them", "Where: frame 4:" in message, message)


def test_a_rule_about_form_is_dropped_and_one_about_knowledge_stays():
    def section(name: str) -> str:
        return (f"## {name}\nWhat it is: it goes wrong.\nHow to avoid it: do it right.\nSeen on: frames 3\n")

    form = ("On-screen text styling (e.g., color, bold) is omitted from the outline",
            "A slide is described as filling the frame when it has surrounding margins",
            "On-screen text is transcribed with incorrect character counts or spacing",
            "A copyright symbol is missed in the screen text",
            "Straight quotes are written as smart/curly quotes",
            "A cursor is quoted as text content",
            "A code block is described as centred when it is left-aligned",
            "A section header is attributed to a line number that contains different content")
    knowledge = ("A table is outlined without its row labels",
                 "An arrow is kept from the frame before without checking its head",
                 "A diagram's arrows follow the boxes' positions instead of their heads",
                 "Visible code text is not quoted")
    mixed = critique_markdown_utils.parse_critique("# Critique\n\n" + "\n".join(map(section, form + knowledge))).critique
    only_form = critique_markdown_utils.parse_critique("# Critique\n\n" + "\n".join(map(section, form))).critique
    harness.check("rules about form are dropped, rules about knowledge stay",
                  [rule["name"] for rule in mixed["rules"]] == list(knowledge), str([rule["name"] for rule in mixed["rules"]]))
    harness.check("a critique whose every rule is about form reads as nothing to criticise, never as an unreadable answer",
                  only_form == {"is_nothing_to_criticise": True, "rules": []}, str(only_form))


def test_seen_on_names_only_the_numbers_after_frame():
    """A worker wrote where on the frame it saw the mistake, "frame 35 (bullet 3)", and its bullet numbers were read as
    frames outside its batch (2026-10-12)."""

    harness.check("numbers after 'frame' are the frames, bullet numbers are not",
                  critique_markdown_utils.list_seen_frames("frame 35 (Take aways bullet 3), frame 36 (bullet 2)") == [35, 36])
    harness.check("one 'frames' before a list names the list",
                  critique_markdown_utils.list_seen_frames("frames 9, 11") == [9, 11])
    harness.check("a bare list still names its numbers", critique_markdown_utils.list_seen_frames("9, 11") == [9, 11])


def test_the_fix_is_handed_whole_each_notes_line_a_where_line_quotes():
    """The fix agreed a line was wrong, wrote its correction and named nothing, so the wrong line stayed (lecture-1
    frames 81, 111, 137, 140, 2026-10-14): the request now hands the line the Where line quotes part of, whole."""

    records = [{"frame": 3, "facts": ["Executing `refund(5)` in the console returns `3 parts`, splitting the order."],
                "take_aways": ['- "the bank books it" → box "Ledger": each part is booked']},
               {"frame": 4, "facts": ["Executing `refund(5)` in the console returns `3 parts`, splitting the order."]}]
    where = "Frame 3, Facts: \"Executing `refund(5)` in the console returns `3 parts`\" (the output is not printed yet)"
    harness.check("the quoted fact of frame 3 is found whole, not frame 4's",
                  critique_markdown_utils.list_named_lines(where, records) == [records[0]["facts"][0]],
                  str(critique_markdown_utils.list_named_lines(where, records)))
    harness.check("a Where line quoting nothing names no line",
                  critique_markdown_utils.list_named_lines("frame 3: the outline is short", records) == [])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=_BATCH_SIZE)
    request = attempt_request_builder.build_standalone_request(ctx, "second_pass", inputs={"batch": {
        "critiqueRules": [{"name": "an output not printed yet", "what_it_is": "x", "how_to_avoid": "y", "frames": [3],
                           "where": [where]}], "accepted": records}})
    message = frames_processing_messages.render_critique_rules(request)
    harness.check("the fix's message carries the whole line under the Where line",
                  "The notes' line it names, whole: Executing `refund(5)` in the console returns `3 parts`, splitting "
                  "the order." in message, message)


def test_a_where_line_quoting_a_line_shortened_with_dots_names_the_whole_line():
    """A Where line quoted "The AcmeSync team... found some issues by design"; no line held it whole, the fix wrote the
    correction without naming the line, and both stood (38c3-fault-injection frame 4, 2026-10-15)."""

    records = [{"frame": 4, "facts": [], "take_aways": [
        'The AcmeSync team was cooperative ("Kudos !") but found some issues by design and others as mistakes.']}]
    where = 'frame 4: the notes state "The AcmeSync team... found some issues by design", whereas Jonas said otherwise'
    harness.check("the shortened quote names the whole line",
                  critique_markdown_utils.list_named_lines(where, records) == records[0]["take_aways"],
                  str(critique_markdown_utils.list_named_lines(where, records)))
    harness.check("pieces out of order name nothing",
                  critique_markdown_utils.list_named_lines('frame 4: "found some issues… The AcmeSync team"', records) == [])


def test_a_missing_critique_s_rules_are_a_new_list_each_time():
    """`dict(_NO_CRITIQUE)` copied the outer dict only: a rule added to one missing critique's list showed in the next."""

    paths = fx.make_paths()
    first = record_file_service.read_critique_record(paths, "frames-000-001-critique")
    first["rules"].append({"name": "invented rule"})
    harness.check("the next missing critique holds no rule",
                  record_file_service.read_critique_record(paths, "frames-002-003-critique")["rules"] == [])


def test_a_pdf_batch_has_no_open_questions_unless_its_own_switch_is_on():
    """A PDF figure batch's open questions and answers took 554 to 930 s over four batches and found nothing the
    document answers (the hard figures PDF, the Wikipedia article): the document's text, which phase 4 reads, is
    the answer."""

    work = app_config.settings.frames_processing
    for is_on, calls in ((False, 1), (True, 2)):
        ctx = frame_batch_fixtures.load_run_context(fakes.scripted(_batch_answer(), fakes.nothing_open()),
                                                    count=_BATCH_SIZE)
        frames = phase_file_service.read_phase_file(ctx.paths, 2)
        phase_file_service.write_phase_file(ctx.paths, 2, {**frames, "medium": output_contract.PDF_MEDIUM})
        previous, work.is_pdf_open_questions_on = work.is_pdf_open_questions_on, is_on
        try:
            with fx.critique_switches(is_critique_on=False, is_critique_repair_on=False):
                frame_batch_fixtures.run_frames_processing(ctx, [f"frame-{index:03d}" for index in range(_BATCH_SIZE)],
                                                           frames_per_call=_BATCH_SIZE)
        finally:
            work.is_pdf_open_questions_on = previous
        harness.check(f"a PDF batch with isPdfOpenQuestionsOn {'on' if is_on else 'off'} makes {calls} call(s)",
                      ctx.model.call_count == calls, str(ctx.model.call_count))


def test_a_video_batch_has_no_open_questions_when_their_switch_is_off_defect():
    # Defect: a Balanced video run asked each batch its open questions (8 minutes) and their answers (4 minutes); only a
    # PDF could switch them off. Fix: framesProcessing.isOpenQuestionsOn, off, asks neither for any batch.
    work = app_config.settings.frames_processing
    for is_on, calls in ((False, 1), (True, 2)):
        ctx = frame_batch_fixtures.load_run_context(fakes.scripted(_batch_answer(), fakes.nothing_open()),
                                                    count=_BATCH_SIZE)
        previous, work.is_open_questions_on = work.is_open_questions_on, is_on
        try:
            with fx.critique_switches(is_critique_on=False, is_critique_repair_on=False):
                frame_batch_fixtures.run_frames_processing(ctx, [f"frame-{index:03d}" for index in range(_BATCH_SIZE)],
                                                           frames_per_call=_BATCH_SIZE)
        finally:
            work.is_open_questions_on = previous
        harness.check(f"a video batch with isOpenQuestionsOn {'on' if is_on else 'off'} makes {calls} call(s)",
                      ctx.model.call_count == calls, str(ctx.model.call_count))


def test_a_critique_written_from_its_summary_keeps_only_the_summary_s_mistakes_defect():
    # Defect: a looped critique's answer, written from a summary of one mistake on frame 4, listed 12 rules from the
    # instructions' list, two naming no frame, and the batch blocked. Fix: only rules seen on the summary's frames stay, once.
    from services import thinking_compaction_service

    summary = ("## MISTAKES FOUND\n\nIn Frame 4 the notes give the presenter's answer to a question asked in frame 5.\n\n"
               "## REJECTED\n\nFrame 3: its outline quotes every label.\n\n## NOT CHECKED YET\n\nNone\n")
    parsed = {"rules": [{"name": "An answer is placed on the frame of its question", "frames": [4]},
                        {"name": "A diagram's arrow is kept from the frame before", "frames": []},
                        {"name": "A label nobody quoted", "frames": [3]},
                        {"name": "An answer is placed on the frame of its question (again)", "frames": [4]}]}
    kept = thinking_compaction_service.keep_summary_rules(parsed, summary)
    harness.check("one rule stays, the one on the summary's frame", [rule["name"] for rule in kept["rules"]]
                  == ["An answer is placed on the frame of its question"], str(kept["rules"]))


def test_a_summary_naming_its_frames_as_a_list_with_notes_keeps_its_rules_defect():
    # Defect: a summary wrote "Frames: 4 (Fact [F4.3]), 7 (Facts [F7.1])", read as naming no frame; every rule of the
    # critique written from it was set aside and its three mistakes became "Nothing to criticise". Fix: that list is read,
    # the note ids in brackets and parentheses not taken for frames
    from services import thinking_compaction_service

    summary = ("## MISTAKES FOUND\n**Talk-as-subject facts**\nA fact about the talk.\nFrames: 4 (Fact [F4.3]), 7 (Facts "
               "[F7.1], [F7.2])\n\n**Incomplete chart outline**\nA legend outlined as an axis label.\nFrames: 8 (Chart "
               "section)\n\n## REJECTED\nFrame 5: its title is right.\n\n## NOT CHECKED YET\nNone\n")
    harness.check("the frames the list names, and no note id's number",
                  thinking_compaction_service.list_found_frames(summary) == {4, 7, 8},
                  str(thinking_compaction_service.list_found_frames(summary)))
    parsed = {"rules": [{"name": "A fact about the talk", "frames": [4, 7]}, {"name": "A legend as an axis", "frames": [8]}]}
    harness.check("both rules stay", len(thinking_compaction_service.keep_summary_rules(parsed, summary)["rules"]) == 2, "")


def test_a_summary_whose_mistakes_name_no_readable_frame_keeps_its_rules_defect():
    # Defect: a summary wrote one mistake a line, one naming a note's id ("F0.3") and two naming no frame; every rule but
    # one was set aside and the batch was stored "Nothing to criticise". Fix: a note's id names its frame, and a mistake
    # naming no frame the program reads sets no rule aside; a summary placing every mistake still filters
    from services import thinking_compaction_service

    summary = ("## MISTAKES FOUND\nA fact restates the screen: F0.3 \"The refresh window is set to 300 seconds.\"\n"
               "A table row is left out of the facts: the facts skip the middle alert.\n\n## REJECTED\nF1 is right.\n\n"
               "## NOT CHECKED YET\nNone\n")
    parsed = {"rules": [{"name": "A fact restates the screen", "frames": [0]}, {"name": "A row left out", "frames": [3]}]}
    harness.check("a note's id names its frame", thinking_compaction_service.list_found_frames(summary) == {0},
                  str(thinking_compaction_service.list_found_frames(summary)))
    harness.check("both rules stay", len(thinking_compaction_service.keep_summary_rules(parsed, summary)["rules"]) == 2, "")
    placed = summary.replace("the facts skip the middle alert.", "the facts skip the middle alert, frame 3.")
    invented = {"rules": [*parsed["rules"], {"name": "A rule the summary never held", "frames": [2]}]}
    harness.check("with every mistake placed, a rule on another frame is still set aside",
                  [rule["frames"] for rule in thinking_compaction_service.keep_summary_rules(invented, placed)["rules"]]
                  == [[0], [3]], "")


def test_a_nothing_to_criticise_section_among_rules_is_no_rule_defect():
    # Defect: a critique named two rules, then a "## Nothing to criticise" section with every line empty, read as a third
    # rule. Fix: that heading among rules is no rule
    from utils import critique_markdown_utils
    answer = ("# Critique\n\n## A total is copied without its unit\nWhat it is: a total has no unit.\nHow to avoid it: "
              "write the unit.\nSeen on: frame 4\nWhere: frame 4: `## Facts` \"The total is 40 for the gateway\"\n\n"
              "## Nothing to criticise\nWhat it is: \nHow to avoid it: \nSeen on: \n")
    parsed = critique_markdown_utils.parse_critique(answer)
    names = [rule["name"] for rule in parsed.critique["rules"]]
    harness.check("the one real rule alone", names == ["A total is copied without its unit"], str(names))


def test_the_checked_section_answers_each_listed_mistake_and_is_no_rule_defect():
    # Defect: looped critiques walked the instruction's list of common mistakes again and again, with no place where each
    # answer landed (12% of critique attempts looped, 0% to 2% of every other step). Fix: a `## Checked` section holds
    # one answer per listed mistake; it is read apart and never as a rule, and all "none" is nothing to criticise
    answer = ("# Critique\n\n## Checked\n- a fact said and never written: frame 4\n- a label nobody quoted: none\n\n"
              "## A total is copied without its unit\nWhat it is: a total has no unit.\nHow to avoid it: write the "
              "unit.\nSeen on: frame 4\nWhere: frame 4: `## Facts` \"The total is 40 for the gateway\"\n")
    parsed = critique_markdown_utils.parse_critique(answer).critique
    harness.check("the one real rule alone", [rule["name"] for rule in parsed["rules"]]
                  == ["A total is copied without its unit"], str(parsed["rules"]))
    harness.check("the answers are kept apart", parsed["checked"] == ["a fact said and never written: frame 4",
                                                                      "a label nobody quoted: none"], str(parsed))
    nothing = critique_markdown_utils.parse_critique(
        "# Critique\n\n## Checked\n- a fact said and never written: none\n- a label nobody quoted: None.\n")
    harness.check("every answer none is nothing to criticise", nothing.critique is not None
                  and nothing.critique["is_nothing_to_criticise"], str(nothing))


def test_the_critique_checks_a_diagram_s_order_against_the_frame_s_arrows_defect():
    # Defect: a diagram drawn from a box of the chart's other branch, a step after one it comes before, passed the
    # critique, whose list named only an arrow kept from the frame before. Fix: its sixth mistake covers every diagram
    # running otherwise than the frame's arrows
    from prompts import system_prompt_builder

    prompt = " ".join(system_prompt_builder.build_system_prompt("critique").split())
    harness.check("a box of another branch and a step out of order are named",
                  "a box of another branch placed in the path, or a step drawn after one it comes before" in prompt, "")


def test_the_critique_instruction_s_example_answers_each_listed_mistake_once_defect():
    # Defect: the instruction listed the common mistakes as prose with no place for each answer, and looped critiques
    # walked the list again and again. Fix: the list is numbered and the example answer opens with one `## Checked` line
    # per item, which the program reads apart from the rules
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "critique.md").read_text(
        encoding="utf-8")
    listed = re.findall(r"(?m)^(\d+)\. ", text)
    example = re.search(r"~~~markdown\n(.*?)~~~", text, re.DOTALL).group(1)
    parsed = critique_markdown_utils.parse_critique(example).critique
    harness.check("the list is numbered 1 to 9", listed == [str(number) for number in range(1, 10)], str(listed))
    harness.check("the example answers each listed mistake once", len(parsed.get("checked") or []) == len(listed)
                  and [line.split()[0] for line in parsed["checked"]] == listed, str(parsed.get("checked")))
    harness.check("and its two rules are read as rules", len(parsed["rules"]) == 2, str(parsed["rules"]))


def test_the_critique_request_asks_for_the_checked_section_and_lists_no_mistakes_of_its_own_defect():
    # Defect: the request repeated an older list of four mistakes and an answer with no `## Checked`, against the
    # instruction's numbered list, and two critiques under the new instruction followed the request and left the
    # section out (Lecture 7 run 10). Fix: the request points at the numbered list and asks for the section first
    from prompts import frames_processing_messages

    template = frames_processing_messages.CRITIQUE_MESSAGE.template
    harness.check("the request asks for the section", '"## Checked"' in template, template[-700:])
    harness.check("and repeats no list of its own", "screens outlined in two lines" not in template, template[:600])


def test_a_rule_keeps_no_remark_of_the_critique_s_own_reasoning_defect():
    # Defect: a critique wrote its doubts into a rule's Where line, "(note: ... Wait, let's check ... I will restrict
    # the rule to frame 6.)", and the rule went to phase 6 with them (Lecture 7, DEFECTS 570). Fix: a bracketed remark
    # holding the writer's own reasoning is dropped from every line of a rule; a bracket that explains stays
    answer = ("# Critique\n\n## A box's text is put inside the box\nWhat it is: a label above a box is outlined in it "
              "(wait, or beside it?).\nHow to avoid it: check where the label stands.\nSeen on: frames 6, 8\n"
              "Where: frame 6: the `## Screen` line `- box with the gateway label` (the label stands above the box)\n"
              "Where: frame 8: the `## Screen` line `- list of limits` (note: the box is also on 8. Wait, let's check "
              "frame 8 (its notes). I will restrict the rule to frame 6.)\n")
    rule = critique_markdown_utils.parse_critique(answer).critique["rules"][0]
    harness.check("the reasoning remark leaves the Where line, its quote stays",
                  rule["where"][1] == "frame 8: the `## Screen` line `- list of limits`", str(rule["where"]))
    harness.check("a bracket that explains stays", rule["where"][0].endswith("(the label stands above the box)"),
                  str(rule["where"]))
    harness.check("a doubt in the rule's own line leaves it", "wait" not in json.dumps(rule).casefold(), json.dumps(rule))
