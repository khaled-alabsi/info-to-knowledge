"""Every batch reads its own notes again, whether or not anything failed. REQUIREMENTS.md R19.

The owner, 2026-09-28: "this follow-up is hard-coded. So even if the agent give a good response, we will ask it
anyway ... to push the agent to give his best." So the call is unconditional, and everything else here exists to make
that safe: a second look may ADD to a record and may never lose what the first pass got right. Lists grow and are
de-duplicated, the narration and the screen's outline may not get shorter, a diagram must keep every line it had,
every filled in record is judged again by the validator that accepted it, and a record that would fail goes back byte
for byte.
"""

from support import fakes
from support import frame_batch_fixtures
from support import fixtures as fx
from support import harness

from graph.nodes import process_frames_node
from models import output_contract
from models.chain_call_spec import ChainCallSpec
from prompts import frames_processing_messages
from prompts import system_prompt_builder
from schemas import frame_record
from services import batch_summaries_service
from services import chain_dispatch_service
from services import frame_records_service
from services import phase_file_service
from services import record_file_service
from services.ignore_list_service import IgnoreList
from utils import addition_utils
from utils import frame_markdown_utils
from validators import item_validator


ACCEPTED = {"frame": 0, "screen_kind": "slide", "facts": ["A refund can be split into at most three parts."],
            "diagram": 'flowchart LR\n    A["Shop"] --> B["Billing"]', "screen": '- slide: white, the whole frame\n  - text "Refunds": centre',
            "speech_visual_alignment": "the speech gives the reason for the limit", "builds_on": [],
            "contradicts_earlier": [], "open_questions": [],
            "third_person_description": "The presenter opens the refund slide and reads out the three-part limit.",
            "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}


def _addition(**fields) -> dict:
    return {"frame": 0, "what_changed": "the reason for the limit was missing", **fields}


def test_two_edits_writing_one_text_are_neither_applied_defect():
    # Defect: a repair wrote "replace F5.2" and "[F5.3]" with the same new fact; both lines became it and F5.2's own fact
    # was lost. Fix: two replaces of one frame writing one text name their line ambiguously, and neither is applied
    held = {**ACCEPTED, "frame": 5, "facts": ["The export finishes in 36 seconds.", "The agent keeps working for 44 seconds.",
                                             "The prompt shows the whole instruction."]}
    repair = _addition(frame=5, edits=[{"action": "replace", "id": "F5.2", "text": "The prompt shows half the instruction."}],
                       added_facts=["[F5.3] The prompt shows half the instruction."])
    record, _, refusals = addition_utils.apply_addition(held, repair)
    harness.check("the facts stay as they were", record["facts"] == held["facts"], str(record["facts"]))
    harness.check("both edits are named as not applied", sum("writes the same text" in refusal for refusal in refusals) == 2,
                  str(refusals))


def test_a_diagram_the_critique_named_wrong_may_be_redrawn_defect():
    # Defect: a critique named a frame's diagram wrong (a box skipped, two steps swapped), the repair drew it right
    # without listing each old line as wrong, and the redraw was refused, so the wrong diagram reached a chapter.
    # Fix: a diagram the critique names on the frame is opened to the repair, as the narration and the outline are
    rules = [{"name": "A diagram runs otherwise than the frame's arrows", "frames": [9],
              "where": ['frame 9: the diagram line `E -- No --> F["Copy"]` skips the "Lock" box']}]
    critiqued = addition_utils.list_critiqued_fields(rules, 9)
    harness.check("the critique names the diagram", "diagram" in critiqued, str(critiqued))
    held = {**ACCEPTED, "frame": 9, "diagram": 'flowchart TD\n  A["Start"] --> E{"Flag?"}\n  E -- No --> F["Copy"]'}
    redraw = _addition(frame=9, diagram='flowchart TD\n  A["Start"] --> E{"Flag?"}\n  E --> G["Lock"]\n  G --> F["Copy"]')
    record, _, refusals = addition_utils.apply_addition(held, redraw, critiqued)
    harness.check("the redrawn diagram is taken", "G --> F" in record["diagram"], str(refusals))


def test_a_fact_the_repair_adds_about_the_speaker_is_dropped_defect():
    # Defect: a repair added "The speaker usually cannot attend conferences due to family reasons" and two more facts
    # about the person talking, which no critique rule asked for and phase 4 refuses as sentences. Fix: such an added
    # fact is dropped; a fact about the subject stays
    held = {**ACCEPTED, "frame": 1, "facts": ["The tracker was released in 2019."]}
    repair = _addition(frame=1, added_facts=["[F1.5] The speaker mentions this is their first visit.",
                                             "The presenter usually cannot attend conferences due to family reasons.",
                                             "The tracker predates the competitor's by three years."])
    record, _, _ = addition_utils.apply_addition(held, repair)
    harness.check("only the fact about the subject is added",
                  record["facts"] == ["The tracker was released in 2019.",
                                      "The tracker predates the competitor's by three years."], str(record["facts"]))


def test_a_line_written_with_a_held_line_s_id_replaces_that_line_defect():
    # Defect: a repair wrote its take-aways again in full with the ids in front and a corrected suffix on one line; the
    # program dropped the ids and appended the corrected line beside the old one, so the notes held both. Fix: a line
    # written with the id of a held line of this frame is that line, replaced
    held = {**ACCEPTED, "frame": 5, "take_aways": ['"a" → not on screen: one', '"b" → text on the slide: two']}
    rewritten = _addition(frame=5, added_take_aways=['[T5.1] "a" → nothing on screen: one', '[T5.2] "b" → text on the slide: two',
                                                     '[T5.3] a new line with an id past the notes'])
    record, changes, _ = addition_utils.apply_addition(held, rewritten)
    harness.check("the corrected line takes the old one's place and the unchanged one stays",
                  record["take_aways"][:2] == ['"a" → nothing on screen: one', '"b" → text on the slide: two'], str(record["take_aways"]))
    harness.check("a line with an id past the notes is a new line, appended", record["take_aways"][2:] == ["a new line with an id past the notes"],
                  str(record["take_aways"]))
    other_frame, _, _ = addition_utils.apply_addition(held, _addition(frame=5, added_facts=["[F9.1] a fact written with another frame's id"]))
    harness.check("an id of another frame is no name of a line here", other_frame["facts"][-1] == "a fact written with another frame's id",
                  str(other_frame["facts"]))


def test_a_list_grows_and_never_shrinks_and_never_gains_a_duplicate():
    record, changes, refusals = addition_utils.apply_addition(
        ACCEPTED, _addition(added_facts=["A refund can be split into at most three parts.",
                                              "The payment provider allows no more than three parts."]))
    harness.check("the new fact is appended", record["facts"][-1].startswith("The payment provider"), str(record["facts"]))
    harness.check("the entry the record already held is not repeated", len(record["facts"]) == 2, str(record["facts"]))
    harness.check("and the change is named for the console", changes == ["facts +1"], str(changes))
    harness.check("nothing was refused", refusals == [])
    kept, _, _ = addition_utils.apply_addition(ACCEPTED, _addition(added_facts=[]))
    harness.check("an addition with nothing in it leaves the record as it was", kept == ACCEPTED)


def test_an_outline_replaced_without_the_programs_reading_is_not_shorter_for_it():
    """The fix pass is shown the outline without the program's part (the text reader's lines), so its replacement is
    measured against the outline without that part; the program adds the lines again as the notes are written."""

    held = {**ACCEPTED, "screen": ACCEPTED["screen"] + "\n- text the program read on the frame that the parts above "
            'leave out (its reader may misread a letter):\n  - text "Refunds are split into at most three parts by '
            'the billing service"\n  - text "Each part is booked on its own day"'}
    fuller = ACCEPTED["screen"] + '\n  - button "Save": lower right corner'
    record, changes, refusals = addition_utils.apply_addition(held, _addition(screen=fuller))
    harness.check("the worker's fuller outline is taken", changes == ["screen"] and not refusals, f"{changes} {refusals}")


def test_the_narration_and_the_screen_outline_may_not_get_shorter():
    fuller = ACCEPTED["screen"] + '\n  - button "Save": lower right corner, grey'
    record, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(screen=fuller))
    harness.check("a fuller outline is taken", record["screen"] == fuller and changes == ["screen"], str(changes))
    harness.check("nothing was refused", refusals == [])
    thinner = "- slide: white"
    kept, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(screen=thinner))
    harness.check("a thinner one is refused", kept["screen"] == ACCEPTED["screen"] and changes == [], str(changes))
    harness.check("and the refusal says why, with both word counts",
                  refusals and "shorter" in refusals[0] and "words" in refusals[0], str(refusals))
    short_narration = "They read the limit."
    kept, _, refusals = addition_utils.apply_addition(ACCEPTED, _addition(third_person_description=short_narration))
    harness.check("the narration is guarded the same way",
                  kept["third_person_description"] == ACCEPTED["third_person_description"] and refusals)


def test_a_diagram_is_extended_and_never_redrawn():
    extended = ACCEPTED["diagram"] + '\n    B --> C["Ledger"]'
    record, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(diagram=extended))
    harness.check("a diagram that keeps every line and adds one is taken", record["diagram"] == extended
                  and changes == ["diagram"], str(changes))
    redrawn = 'flowchart LR\n    X["Shop"] --> Y["Ledger"]'
    kept, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(diagram=redrawn))
    harness.check("one that drops a line is refused", kept["diagram"] == ACCEPTED["diagram"] and changes == [])
    harness.check("and the refusal says it would be redrawn", refusals and "redrawn" in refusals[0], str(refusals))
    not_mermaid = "The shop sends orders to billing."
    kept, _, refusals = addition_utils.apply_addition(ACCEPTED, _addition(diagram=not_mermaid))
    harness.check("and so is one that is not Mermaid at all", kept["diagram"] == ACCEPTED["diagram"] and refusals)


def test_a_diagram_corrected_in_place_keeping_every_box_and_arrow_is_taken():
    """Three second looks in one run read a box label again or turned an arrow round in place, without naming the lines
    under "Wrong in the diagram", and each was refused as redrawn, so the misreading stayed. A flowchart that keeps every
    box by its id and at least as many arrows is corrected, never thinned."""

    accepted = {**ACCEPTED, "diagram": 'flowchart LR\n    A["Shop"] --> B["Biling"]\n    C["Ledger"] --> B'}
    corrected = 'flowchart LR\n    A["Shop"] --> B["Billing"]\n    B --> C["Ledger"]'
    record, changes, refusals = addition_utils.apply_addition(accepted, _addition(diagram=corrected))
    harness.check("a label read again and an arrow turned round are taken", record["diagram"] == corrected
                  and "diagram" in changes, str((changes, refusals)))
    renamed = 'flowchart LR\n    A["Shop"] --> B["Billing"]\n    D["Ledger"] --> B\n    A --> D\n    E["Archive"] --> A'
    accepted_larger = {**accepted, "diagram": accepted["diagram"] + '\n    A --> C\n    E["Archive"] --> A'}
    record, changes, _ = addition_utils.apply_addition(accepted_larger, _addition(diagram=renamed))
    harness.check("a box given a new id with its corrected label is taken", record["diagram"] == renamed, str(changes))
    thinned = 'flowchart LR\n    A["Shop"] --> B["Billing"]'
    kept, _, refusals = addition_utils.apply_addition(accepted, _addition(diagram=thinned))
    harness.check("one that loses a box is still refused", kept["diagram"] == accepted["diagram"] and refusals, str(refusals))


WITH_A_LATER_PART = {**ACCEPTED,
                     "screen": '- slide: white, the whole frame\n  - box "Shop": left\n  - box "Archive": right\n'
                               '    - arrow to "Shop": pointing left\n  - text "Refunds": centre',
                     "diagram": 'flowchart LR\n    A["Shop"] --> B["Billing"]\n    C["Archive"] --> A'}


def test_a_wrong_diagram_line_named_by_the_second_look_is_replaced():
    """An accepted diagram drew an arrow backwards (the slide's arrowhead touched the other box), and the second look,
    allowed only to extend a diagram, could not correct it (2026-10-06). A line named under "Wrong in the diagram" may
    be left out of the new diagram; every other line must still be kept."""

    accepted = {**ACCEPTED, "diagram": 'flowchart LR\n    A["Shop"] --> B["Billing"]\n    C["Ledger"] --> B'}
    corrected = 'flowchart LR\n    A["Shop"] --> B["Billing"]\n    B --> C["Ledger"]'
    record, changes, refusals = addition_utils.apply_addition(
        accepted, _addition(wrong_diagram_lines=['C["Ledger"] --> B'], diagram=corrected))
    harness.check("the named line is replaced by the corrected one", record["diagram"] == corrected and "diagram" in changes,
                  str((changes, refusals)))
    kept, _, refusals = addition_utils.apply_addition(
        accepted, _addition(wrong_diagram_lines=['C["Ledger"] --> B'], diagram='flowchart LR\n    B --> C["Ledger"]'))
    harness.check("a line not named may still not be dropped", kept["diagram"] == accepted["diagram"] and refusals, str(refusals))
    text = ('# Frame 0\n\n## What changed\nan arrow was backwards\n\n## Wrong in the diagram\n- C["Ledger"] --> B\n\n'
            '## Diagram\n```mermaid\n' + corrected + '\n```\n')
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)["frames"][0]
    harness.check("the section is read", parsed.get("wrong_diagram_lines") == ['C["Ledger"] --> B'], str(parsed))
    harness.check("and written back", "## Wrong in the diagram" in frame_markdown_utils.render_addition_document(parsed))


def test_a_part_named_not_on_this_frame_is_taken_out_of_the_outline_and_the_diagram():
    corrected_screen = '- slide: white, the whole frame\n  - box "Shop": left\n  - text "Refunds": centre'
    corrected_diagram = 'flowchart LR\n    A["Shop"] --> B["Billing"]'
    record, changes, refusals = addition_utils.apply_addition(
        WITH_A_LATER_PART, _addition(wrong_parts=["Archive"], screen=corrected_screen, diagram=corrected_diagram))
    harness.check("a shorter outline without the named part is taken", record["screen"] == corrected_screen, str(refusals))
    harness.check("and so is the diagram without its node", record["diagram"] == corrected_diagram, str(refusals))
    harness.check("nothing was refused", refusals == [] and changes == ["screen", "diagram"], str(changes))
    thinned = '- slide: white, the whole frame\n  - text "Refunds": centre'
    kept, _, refusals = addition_utils.apply_addition(WITH_A_LATER_PART, _addition(wrong_parts=["Archive"], screen=thinned))
    harness.check("an outline that also drops a part still on the frame is refused",
                  kept["screen"] == WITH_A_LATER_PART["screen"] and refusals, str(refusals))
    kept, _, refusals = addition_utils.apply_addition(WITH_A_LATER_PART, _addition(wrong_parts=["slide"], screen=thinned))
    harness.check("a part the outline does not quote excuses no line",
                  kept["screen"] == WITH_A_LATER_PART["screen"] and refusals, str(refusals))


def test_a_part_with_no_text_is_named_by_its_kind_when_one_line_alone_has_it():
    """A first pass outlined an arrow the frame does not show; an arrow quotes no text, so naming it under "Not on this
    frame" excused nothing and the corrected, shorter outline was refused. A part is also named by its kind (the words
    before the colon) when exactly one line has that kind, quotes nothing and holds no part under it."""

    accepted = {**ACCEPTED, "screen": '- slide: white, the whole frame\n  - text "Refunds": centre\n'
                                      '  - arrow: pointing from the title to the text, red'}
    corrected = '- slide: white, the whole frame\n  - text "Refunds": centre'
    record, _, refusals = addition_utils.apply_addition(accepted, _addition(wrong_parts=["arrow"], screen=corrected))
    harness.check("the outline without the arrow is taken", record["screen"] == corrected, str(refusals))
    kept, _, refusals = addition_utils.apply_addition(accepted, _addition(wrong_parts=["slide"], screen=corrected))
    harness.check("a kind whose line holds other parts excuses nothing", kept["screen"] == accepted["screen"] and refusals,
                  str(refusals))
    twice = {**ACCEPTED, "screen": accepted["screen"] + "\n  - arrow: from the text to the footer"}
    kept, _, refusals = addition_utils.apply_addition(twice, _addition(wrong_parts=["arrow"], screen=corrected))
    harness.check("a kind two lines share excuses neither", kept["screen"] == twice["screen"] and refusals, str(refusals))


def test_a_part_named_inside_a_described_line_counts_by_the_texts_it_quotes():
    """A second pass named the wrong part as a whole described line and as diagram lines (`box 'Archive' ...`,
    `C["Archive"] --> A`), never as the bare text, and its right correction was refused as shorter and redrawn."""

    corrected_screen = '- slide: white, the whole frame\n  - box "Shop": left\n  - text "Refunds": centre'
    corrected_diagram = 'flowchart LR\n    A["Shop"] --> B["Billing"]'
    named = ["`\"box 'Archive': right, with an arrow to 'Shop'\"`", '`C["Archive"] --> A`']
    record, changes, refusals = addition_utils.apply_addition(
        WITH_A_LATER_PART, _addition(wrong_parts=named, screen=corrected_screen, diagram=corrected_diagram))
    harness.check("the texts quoted inside the named lines name the part: outline and diagram taken",
                  record["screen"] == corrected_screen and record["diagram"] == corrected_diagram and refusals == [],
                  str(refusals))


def test_a_camera_part_an_addition_brings_is_dropped_and_changes_nothing_alone():
    with_camera = ACCEPTED["screen"] + "\n- video feed: Jonas, top right\n  - name label: lower edge"
    kept, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(screen=with_camera))
    harness.check("an outline that only adds the camera changes nothing",
                  kept["screen"] == ACCEPTED["screen"] and changes == [] and refusals == [], str(changes))
    fuller = with_camera + '\n  - button "Save": lower right corner, grey'
    record, changes, _ = addition_utils.apply_addition(ACCEPTED, _addition(screen=fuller.replace(
        "\n- video feed: Jonas, top right\n  - name label: lower edge", "")))
    harness.check("a fuller outline without a camera is still taken", changes == ["screen"], str(changes))
    mixed = ACCEPTED["screen"] + '\n  - button "Save": lower right corner, grey\n- camera tile: Jonas, top right'
    record, changes, _ = addition_utils.apply_addition(ACCEPTED, _addition(screen=mixed))
    harness.check("a real addition is kept and its camera part is not", "Save" in record["screen"]
                  and "Jonas" not in record["screen"], record["screen"])
    slide_photo = ACCEPTED["screen"] + '\n  - image: a camera on a tripod, left'
    record, _, _ = addition_utils.apply_addition(ACCEPTED, _addition(screen=slide_photo))
    harness.check("a camera the slide shows is content, and stays", "tripod" in record["screen"], record["screen"])


def test_a_narration_sentence_said_over_another_frame_may_be_taken_out_and_the_narration_shortened():
    """A first pass wrote into a frame's narration what only the next frame's words say; the critique named it, and the
    second look's shorter narration was refused as thinner. A sentence quoted whole under
    "Said over another frame" may go; every other sentence must still be kept."""

    accepted = {**ACCEPTED, "third_person_description": "The presenter opens the refund slide and reads out the three-part "
                                                        "limit. The presenter then explains the approval rule for refunds."}
    shorter = "The presenter opens the refund slide and reads out the three-part limit."
    record, changes, refusals = addition_utils.apply_addition(accepted, _addition(
        wrong_narration=["The presenter then explains the approval rule for refunds."], third_person_description=shorter))
    harness.check("the narration without the named sentence is taken",
                  record["third_person_description"] == shorter and "third_person_description" in changes, str(refusals))
    kept, _, refusals = addition_utils.apply_addition(accepted, _addition(third_person_description=shorter))
    harness.check("without the sentence named, the shorter narration is refused",
                  kept["third_person_description"] == accepted["third_person_description"] and refusals, str(refusals))
    text = ('# Frame 0\n\n## What changed\nA sentence said later.\n\n## Said over another frame\n'
            '- The presenter then explains the approval rule for refunds.\n')
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)["frames"][0]
    harness.check("the section is read and written back",
                  parsed.get("wrong_narration") == ["The presenter then explains the approval rule for refunds."]
                  and "## Said over another frame" in frame_markdown_utils.render_addition_document(parsed), str(parsed))


def test_the_not_on_this_frame_section_reads_back_and_writes_again():
    text = '# Frame 0\n\n## What changed\nA box from a later frame.\n\n## Not on this frame\n- "Archive"\n'
    addition = frame_markdown_utils.parse_answer(text, is_addition=True)["frames"][0]
    harness.check("the part is read without its quotes", addition["wrong_parts"] == ["Archive"], str(addition))
    harness.check("and written back under the same heading",
                  '## Not on this frame\n- "Archive"' in frame_markdown_utils.render_addition_document(addition))


def test_the_answer_must_say_something_and_name_frames_of_this_batch():
    paths = fx.make_paths()

    def codes(answer: dict) -> set[str]:
        record_file_service.write_second_pass_record(paths, "frames-000-001-second-pass", answer)

        return harness.codes(item_validator.validate_second_pass_record(paths, "frames-000-001-second-pass", [0, 1]))

    harness.check("an addition for a frame of the batch passes",
                  codes({"is_nothing_to_change": False, "additions": [_addition(added_facts=["x"])]}) == set())
    harness.check("nothing to change is an answer too",
                  codes({"is_nothing_to_change": True, "additions": []}) == set())
    harness.check("neither is SECOND_PASS_EMPTY",
                  codes({"is_nothing_to_change": False, "additions": []}) == {"SECOND_PASS_EMPTY"})
    harness.check("a frame outside the batch is SECOND_PASS_FRAME_UNKNOWN",
                  codes({"is_nothing_to_change": False, "additions": [{**_addition(), "frame": 9}]})
                  == {"SECOND_PASS_FRAME_UNKNOWN"})
    harness.check("an addition that does not say what it changed is noted, never rejected",
                  codes({"is_nothing_to_change": False, "additions": [{"frame": 0, "what_changed": " "}]}) == set())
    harness.check("and the note names it",
                  [note.code for note in
                   item_validator.validate_second_pass_record(paths, "frames-000-001-second-pass", [0, 1]).notes]
                  == ["SECOND_PASS_CHANGE_UNSAID"])


def test_the_call_is_made_even_when_every_frame_and_the_account_passed():
    """The owner's point: the pass is not a repair, so a good answer earns it too."""

    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    second = fakes.second_pass(is_nothing_to_change=False, additions=[
        {"frame": 0, "what_changed": "the payment provider's limit was missing",
         "added_facts": ["The payment provider allows no more than three parts."]}])
    ctx = frame_batch_fixtures.run_two_frame_batch(group, second, fakes.nothing_open())
    harness.check("the batch call, its critique, then the repair", ctx.model.call_count == 4,
                  f"calls={ctx.model.call_count}")
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the addition is in the frame's notes in `02_frames.json`",
                  any("payment provider" in entry for entry in records[0]["facts"]), str(records[0]["facts"]))
    harness.check("and the frame it did not name is untouched",
                  records[1]["facts"] == fakes.own_facts(1, frame_batch_fixtures.FRAME["facts"]), str(records[1]["facts"]))
    harness.check("the store says which call filled in it",
                  ctx.store.get_item("frame-000").get("additionsFrom") == "frames-000-001-second-pass",
                  str(ctx.store.get_item("frame-000")))


def test_nothing_to_change_leaves_every_record_and_the_batch_as_they_were():
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    second = fakes.second_pass(is_nothing_to_change=True, additions=[])
    ctx = frame_batch_fixtures.run_two_frame_batch(group, second, fakes.nothing_open())
    records = frame_records_service.read_frame_records(ctx.paths)
    harness.check("both frames keep exactly the notes the batch call produced",
                  [entry["facts"] for entry in records]
                  == [frame_batch_fixtures.FRAME["facts"], fakes.own_facts(1, frame_batch_fixtures.FRAME["facts"])])
    stored = batch_summaries_service.read_batch_summaries(ctx.paths)
    harness.check("and the batch keeps its accepted account",
                  [entry["summary"] for entry in stored] == [frame_batch_fixtures.SUMMARY], str(stored))


def test_a_repair_that_changed_nothing_past_its_critique_s_rules_is_asked_to_answer_them_defect():
    # Defect: a repair planned the edit a held critique rule asked for and answered "Nothing to change", which was
    # accepted with the mistake left (Lecture 7, frames 139-144, DEFECTS 576). Fix: such an answer is asked once more in
    # the same attempt to answer each rule, and its second answer stands
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    rule = {"name": "A limit said and never written", "what_it_is": "a limit the speaker states is left out.",
            "how_to_avoid": "write each limit said as a fact.", "frames": [0],
            "where": ["frame 0: `## Facts` lacks the limit the speaker states"]}
    fixed = fakes.second_pass(is_nothing_to_change=False, additions=[
        {"frame": 0, "what_changed": "the payment provider's limit was missing",
         "added_facts": ["The payment provider allows no more than three parts."]}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(
        group, fakes.critique([rule]), fakes.second_pass(is_nothing_to_change=True), fixed, fakes.nothing_open()), count=2)
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    harness.check("the batch, its critique, the repair, the repair asked again, the open questions",
                  ctx.model.call_count == 5, f"calls={ctx.model.call_count}")
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the second answer's fix is in the frame's notes",
                  any("payment provider" in entry for entry in records[0]["facts"]), str(records[0]["facts"]))


def test_an_addition_that_would_not_pass_leaves_the_accepted_record_alone():
    """A second look can never turn a written frame into a blocked one."""

    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    second = fakes.second_pass(is_nothing_to_change=False, additions=[
        {"frame": 0, "what_changed": "a better screen kind", "screen_kind": ""}])
    ctx = frame_batch_fixtures.run_two_frame_batch(group, second, fakes.nothing_open())
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("an addition that changes nothing leaves the record as it was",
                  records[0]["facts"] == frame_batch_fixtures.FRAME["facts"]
                  and records[0]["screen_kind"] == frame_batch_fixtures.FRAME["screen_kind"], str(records[0]))


def test_an_outline_addition_is_kept_though_the_fuller_outline_asks_for_facts_the_frame_never_had_defect():
    # A repair added two lines a terminal showed ("/exit", "Goodbye!") to a frame with no facts; with them the outline
    # quoted enough text to ask for facts, and the whole addition was dropped (Lecture 7 frame 30, DEFECTS 555)
    bare = {**frame_batch_fixtures.FRAME, "frame": 0, "facts": [],
            "screen": '- terminal: the whole frame\n  - line "refund gateway"'}
    group = frame_batch_fixtures.build_group(bare, {**frame_batch_fixtures.FRAME, "frame": 1})
    fuller = bare["screen"] + '\n  - line "exit the session"\n  - line "goodbye and thanks"\n  - line "session closed now"'
    second = fakes.second_pass(is_nothing_to_change=False, additions=[_addition(screen=fuller)])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, fakes.critique([]), second, fakes.nothing_open()),
                                                count=2)
    # More than a transition's words over the frame, so the facts check judges it
    frames_file = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    for item in frames_file["items"]:
        item["transcriptCurrent"] = "I close the agent here and start it again so the next demo begins from a clean session"
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"], frames_file)
    with fx.critique_switches():
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the outline lines the repair added are kept", "goodbye and thanks" in records[0]["screen"],
                  records[0]["screen"])


def test_the_kind_is_registered_like_every_other_and_its_instruction_is_on_disk():
    harness.check("the second pass has a message builder", "second_pass" in chain_dispatch_service.MESSAGE_BUILDERS)
    harness.check("and a reader of its markdown answer", "second_pass" in chain_dispatch_service.MARKDOWN_READERS)
    harness.check("and a record writer", "second_pass" in chain_dispatch_service.RECORD_WRITERS)
    addition = frame_markdown_utils.parse_answer("# Frame 0\n\n## What changed\nx\n", is_addition=True)["frames"][0]
    record = frame_markdown_utils.parse_answer("# Frame 0\n", is_addition=False)["frames"][0]
    harness.check("an addition read from markdown has every field it may append to",
                  all(f"added_{name}" in addition for name in addition_utils.APPENDED_FIELDS),
                  str(sorted(addition)))
    harness.check("and every field it may replace is a field the record holds",
                  all(name in record and name in addition for name in addition_utils.REPLACED_FIELDS))


def test_the_second_look_is_sent_its_own_instruction():
    """Until 2026-09-28 it was sent the first pass's `frames_processing.md`, and `second_pass.md` reached no model."""

    batch = {"frames": [0], "start": 0.0, "end": 6.0, "accepted": [{**ACCEPTED, "slug": "frame-000", "index": 0,
                                                                       "timestamp": 0.0, "builtFrom": []}],
               "summary": "", "atmosphere": "", "items": [], "spoken": [{"index": 0, "timestamp": 0.0}]}
    paths = fx.make_paths()
    request = chain_dispatch_service.AttemptRequest(job_kind="second_pass", attempt=1, repository=paths.repository,
                                                    module=paths.module, paths=paths, ignore=IgnoreList(),
                                                    inputs={"slug": "frames-000-000", "batch": batch})
    messages = frames_processing_messages.build_second_pass_messages(request)
    harness.check("its system message is second_pass.md", "You wrote the notes of a batch of frames" in messages[0].content
                  and "You extract the facts one moment" not in messages[0].content, messages[0].content[:200])
    harness.check("its notes are shown as the documents it adds to", "# Frame 0 · slide" in messages[1].content[0]["text"]
                  and "## Screen" in messages[1].content[0]["text"], messages[1].content[0]["text"][:400])


def test_the_second_look_keeps_a_speakers_doubt_in_a_fact_it_adds_defect():
    # A repair added a fact the speaker said with "I think" as certain: the doubt rule was only in the first pass's
    # instruction, which the second look is never sent. Its own instruction now says a fact it adds keeps the doubt
    prompt = " ".join(system_prompt_builder.build_system_prompt("second_pass").split())
    harness.check("the second look is told a fact it adds keeps the speaker's doubt",
                  "A fact you add keeps the certainty the speech gives" in prompt and '"I think"' in prompt, prompt[:200])


def test_a_batch_follow_up_may_write_up_to_the_room_its_window_has_left():
    """The follow-ups' own lower limits (24,576 for the critique) cut long but useful thinking: 9 of 100 critique
    attempts were cut and the next attempt of the same batch passed at 6,000 to 12,422 tokens (2026-10-12)."""

    from langchain_core.messages import HumanMessage
    messages = [HumanMessage(content=[{"type": "text", "text": "x" * 30_000}, {"type": "image_url", "image_url": {"url": "u"}}])]
    harness.check("the prompt is estimated by its text and a fixed cost a frame",
                  chain_dispatch_service.estimate_prompt_tokens_by_characters(messages) == 10_000 + 2_000)
    harness.check("the room is the window less the prompt and a margin",
                  chain_dispatch_service.compute_window_room(messages, 183_040) == 183_040 - 12_000 - 4_000)
    harness.check("an unknown window leaves the role's own limit",
                  chain_dispatch_service.compute_window_room(messages, None) is None)
    kinds = {process_frames_node.SECOND_PASS_KIND, process_frames_node.OPEN_QUESTIONS_KIND, process_frames_node.ANSWER_QUESTIONS_KIND,
             "critique"}
    harness.check("every follow-up gets the room", kinds <= set(chain_dispatch_service.PHASE3_FOLLOW_UPS))


def test_a_follow_up_cut_while_thinking_is_compacted_for_its_next_attempt():
    """Phase 3's compaction: what a cut reply's thinking settled reaches the next attempt, in the step's own sections,
    and the attempt's log folder holds it (the owner, 2026-10-12)."""

    import tempfile
    from pathlib import Path
    from langchain_core.messages import AIMessage, HumanMessage
    from prompts import compaction_prompt_loader
    from services import thinking_compaction_service
    harness.check("every follow-up has its own compaction prompt",
                  set(compaction_prompt_loader.CUT_THINKING_STEPS) == set(chain_dispatch_service.PHASE3_FOLLOW_UPS))
    cut_prompts = [compaction_prompt_loader.read_cut_thinking_prompt(step) for step in compaction_prompt_loader.CUT_THINKING_STEPS]
    harness.check("each prompt names what to keep and what was not checked yet",
                  all("NOT CHECKED YET" in prompt and "{thinking}" in prompt and "{allowance}" in prompt
                      for prompt in cut_prompts))
    harness.check("only a cut follow-up with thinking is compacted",
                  thinking_compaction_service.is_compacted("critique", True, "frame 3 checked")
                  and not thinking_compaction_service.is_compacted("critique", False, "frame 3 checked")
                  and not thinking_compaction_service.is_compacted("frame", True, "frame 3 checked")
                  and not thinking_compaction_service.is_compacted("critique", True, "  "))

    sent = []

    class _Summariser:
        def invoke(self, messages):
            sent.append(messages[0].content)
            return AIMessage(content="## MISTAKES FOUND\nA table without its row labels: frame 3\n\n## NOT CHECKED YET\nframe 4")

        def stream(self, messages):
            # The compaction call is streamed (2026-10-13); a model that cannot stream yields its whole reply
            yield self.invoke(messages)

    from langchain_openai import ChatOpenAI
    bound = thinking_compaction_service.bind_summary_call(
        ChatOpenAI(model="local", base_url="http://127.0.0.1:9/v1", api_key="none"), 4_000)
    payload = bound.bound._get_request_payload([HumanMessage(content="Summarise.")], **bound.kwargs)
    harness.check("the summary call is sent with no thinking of its own and at most the window's room",
                  payload.get("reasoning_effort") == "none" and payload.get("max_completion_tokens") == 4_000, str(payload))
    harness.check("and with the model card's sampling for no thinking: thinking's own set made it ramble (2026-10-15)",
                  payload.get("temperature") == 0.7 and payload.get("top_p") == 0.8, str(payload))

    compaction = thinking_compaction_service.summarise_cut_thinking(
        _Summariser(), ChainCallSpec("frames_processing", "critique", 183_040), "Frame 3: the table has no row labels. " * 3)
    harness.check("the summary call is handed the thinking under the critique's sections",
                  "Frame 3: the table has no row labels." in sent[0] and "## MISTAKES FOUND" in sent[0], sent[0][:300])
    folder = Path(tempfile.mkdtemp())
    path = thinking_compaction_service.write_compaction(folder, "frames-003-004-critique", 24_576, compaction)
    harness.check("the attempt's log folder holds the compaction",
                  path.name == "compaction.md" and "24576 tokens" in path.read_text() and "frame 4" in path.read_text())

    paths = fx.make_paths()
    request = chain_dispatch_service.AttemptRequest(job_kind="critique", attempt=2, repository=paths.repository,
                                                    module=paths.module, paths=paths, ignore=IgnoreList(),
                                                    findings=[{"code": "CRITIQUE_EMPTY", "detail": "no rule", "fix": ""}],
                                                    carry_over=compaction.summary)
    note = frames_processing_messages.render_previous_findings(request)
    harness.check("the next attempt opens with the summary and is told to check only what is left",
                  "A table without its row labels" in note and "NOT CHECKED YET" in note and "CRITIQUE_EMPTY" in note, note)


def test_a_corrected_outline_a_word_or_two_shorter_is_taken():
    """Three corrections one or two words shorter were refused in one batch, one of them a screen the notes called
    "loading" that showed a dialog (2026-10-01)."""

    accepted = {**ACCEPTED, "screen": '- page: the editor is loading, a spinner in the centre\n  - text "Refunds": top left'}
    corrected = '- page: an "Open" dialog over the editor\n  - text "Refunds": top left'
    record, _, refusals = addition_utils.apply_addition(accepted, _addition(screen=corrected))
    harness.check("a correction two words shorter is taken", record["screen"] == corrected and refusals == [], str(refusals))


def test_an_empty_wrong_diagram_line_names_nothing_and_refuses_nothing():
    extended = ACCEPTED["diagram"] + '\n\n    B --> C["Ledger"]'
    record, _, refusals = addition_utils.apply_addition(ACCEPTED, _addition(diagram=extended, wrong_diagram_lines=[""]))
    harness.check("the extended diagram, with a blank line in it, is taken", record["diagram"] == extended and not refusals,
                  str(refusals))


def test_a_part_named_not_on_the_frame_excuses_only_itself():
    accepted = {**ACCEPTED, "screen": '- slide: white, the whole frame\n  - panel "Log": right\n'
                                      '  - button "Login": top\n    - text "user name": inside\n    - text "password": inside'}
    shorter = '- slide: white, the whole frame\n  - button "Login": top'
    _, changes, refusals = addition_utils.apply_addition(accepted, _addition(screen=shorter, wrong_parts=["Log"]))
    harness.check("an outline that lost the Login button's lines is refused", "screen" not in changes and refusals,
                  f"{changes} {refusals}")


def test_a_fix_outline_indented_four_spaces_is_folded_to_two():
    """A fix indented its outline four spaces under its first part; the record check refused it and the whole addition
    was dropped, facts too (lecture-1 frames 35 and 36, 2026-10-13). A first answer's outline was already folded."""

    proposed = ('- slide: white, the whole frame\n    - text "Refunds": centre\n    - table "Parts": below\n'
                '        - row "1" | "first part"')
    record, changes, refusals = addition_utils.apply_addition(ACCEPTED, _addition(screen=proposed))
    harness.check("the outline is taken, two spaces a level",
                  record["screen"] == '- slide: white, the whole frame\n  - text "Refunds": centre\n  - table "Parts": below\n'
                                     '    - row "1" | "first part"', str((record["screen"], changes, refusals)))


def test_an_outline_repeated_with_its_camera_part_is_no_change():
    accepted = {**ACCEPTED, "screen": ACCEPTED["screen"] + "\n- Camera tile: top right"}
    _, changes, _ = addition_utils.apply_addition(accepted, _addition(screen=accepted["screen"]))
    harness.check("nothing changed", changes == [], str(changes))


def test_a_fact_written_again_with_words_taken_out_is_not_added():
    """A fix pass wrote a held fact again without its last three words, and both stood (38c3-fault-injection frame 21,
    2026-10-15). A fact that adds a word is new and is kept."""

    held = 'The AcmeSync test bench holds two scopes and a probe in a tangled pile ("Kabelsalat" or cable mess).'
    shorter = 'The AcmeSync test bench holds two scopes and a probe in a tangled pile ("Kabelsalat").'
    longer = 'The AcmeSync test bench holds two scopes and a probe in a tangled pile on one desk.'
    accepted = {**ACCEPTED, "facts": [held]}
    record, _, _ = addition_utils.apply_addition(accepted, _addition(added_facts=[shorter, longer]))
    harness.check("the shorter copy is not added, the fact with a new word is",
                  record["facts"] == [held, longer], str(record["facts"]))


def test_a_capital_frame_or_a_rule_with_no_where_line_still_names_the_outline():
    """"Frame 58, Screen outline" was not read as naming frame 58, and a rule with only a Seen on line named nothing, so
    each fix's corrected outline was refused as shorter (lecture-1 frames 58 and 151, 2026-10-14)."""

    capital = [{"name": "a console prompt misread", "frames": [4], "where": ["Frame 4, Screen outline, the prompt line"]}]
    harness.check("a Where line opening with a capital names its frame",
                  addition_utils.list_critiqued_fields(capital, 4) == {"screen"})
    unplaced = [{"name": "a position stated wrong", "what_it_is": "a part placed top left in the screen outline",
                 "frames": [4, 6], "where": []}]
    harness.check("a rule with no Where line names the outline by its words, on its frames only",
                  addition_utils.list_critiqued_fields(unplaced, 6) == {"screen"}
                  and not addition_utils.list_critiqued_fields(unplaced, 5))
    loose = [{"name": "a fact saying the screen shows a value only the speaker says", "frames": [4], "where": []}]
    harness.check("a rule naming the screen but no outline names no text",
                  not addition_utils.list_critiqued_fields(loose, 4))


def test_a_where_line_naming_the_outline_by_its_word_lets_the_fix_shorten_it_defect():
    # A critique's Where line said "frame 4: Outlines old help lines ... but misses the new output", naming no heading,
    # and the fix's outline holding the new output was refused as shorter. The field's own word names it now
    rules = [{"name": "a step outlines old text instead of new", "frames": [4, 5],
              "where": ["frame 4: Outlines old help lines (`<command>: <help>`) but misses the new output"]}]
    harness.check("the Where line's 'Outlines' names frame 4's outline",
                  addition_utils.list_critiqued_fields(rules, 4) == {"screen"},
                  str(addition_utils.list_critiqued_fields(rules, 4)))
    facts_only = [{"name": "a value stated twice", "frames": [4], "where": ["frame 4: the fact repeats a value"]}]
    harness.check("a Where line naming neither text names none", not addition_utils.list_critiqued_fields(facts_only, 4))


def test_a_text_the_critique_named_on_the_frame_may_come_back_shorter():
    """A fix took a sentence the critique found out of a frame's account, 47 words against 56, and the length guard
    kept the sentence (lecture-1 frame 135, 2026-10-14). A text the critique did not name keeps its guard."""

    held = ("The refund slide is shown with its three steps. The presenter explains how each part is booked and why "
            "the bank sees three bookings, then moves to the exercise.")
    shorter = "The refund slide is shown with its three steps. The presenter moves to the exercise."
    rules = [{"name": "an explanation credited to the speaker", "where": ['frame 4: "What happens" section claiming '
                                                                           "the presenter explains the steps"]}]
    accepted = {**ACCEPTED, "third_person_description": held}
    critiqued = addition_utils.list_critiqued_fields(rules, 4)
    harness.check("the critique's Where line names the account of frame 4, and nothing of frame 5",
                  critiqued == {"third_person_description"} and not addition_utils.list_critiqued_fields(rules, 5),
                  str(critiqued))
    record, changes, refusals = addition_utils.apply_addition(
        accepted, _addition(third_person_description=shorter), critiqued)
    harness.check("the shorter account the critique asked for is taken",
                  record["third_person_description"] == shorter, str((changes, refusals)))
    record, _, refusals = addition_utils.apply_addition(accepted, _addition(third_person_description=shorter))
    harness.check("unnamed, the shorter account is still refused", record["third_person_description"] == held,
                  str(refusals))


def test_a_second_look_adds_its_take_aways():
    accepted = {**ACCEPTED, "take_aways": ['- "three parts" -> the limit: a refund splits in three']}
    record, changes, _ = addition_utils.apply_addition(accepted, _addition(added_take_aways=['- "the bank" -> the box: it books each part']))
    harness.check("the new take away is appended", len(record["take_aways"]) == 2 and "take_aways +1" in changes,
                  f"{record['take_aways']} {changes}")


def test_a_take_away_written_again_with_nothing_on_screen_is_no_addition():
    """A fix pass wrote each closing take-away again with "→ nothing on screen" added, and both were kept (2026-10-13)."""

    closing = "Take aways: the Gateway retries a failed call three times."
    accepted = {**ACCEPTED, "take_aways": [closing]}
    record, changes, _ = addition_utils.apply_addition(accepted, _addition(added_take_aways=[closing + " → nothing on screen"]))
    harness.check("nothing is appended", record["take_aways"] == [closing] and changes == [], f"{record['take_aways']} {changes}")
    harness.check("a stored repeat is listed once", addition_utils.list_distinct_entries(
        [closing, closing + " → nothing on screen"]) == [closing])


def test_every_list_section_a_second_look_writes_is_appended():

    listed = [field for field, (_heading, shape) in frame_record.FRAME_SECTIONS.items()
              if shape in ("list", "labelled", "tags")]
    missing = [field for field in listed if field not in addition_utils.APPENDED_FIELDS]
    harness.check("none is parsed and then dropped", missing == [], str(missing))


def test_a_wrong_fact_goes_when_the_outline_part_that_showed_it_goes_too():
    """A fact and its outline line both gave an output not printed yet; the screen guard kept the fact because the
    outline showed it, and the outline's line, quoted in backticks, could not be named (lecture-1 frame 103, 2026-10-13)."""

    screen = "- console: right half\n  - `In [11]: retry(13)` followed by `Out[11]: 0.5`\n  - prompt `In [12]:`: bottom"
    accepted = {**ACCEPTED, "frame": 4, "screen": screen,
                "facts": ["The console displays the output `Out[11]: 0.5`.", "The Gateway retries each call."]}
    proposed = "- console: right half\n  - prompt `In [11]: retry(13)`: typed, not yet run\n  - prompt line waiting: bottom"
    record, _, refusals = addition_utils.apply_addition(accepted, _addition(
        frame=4, edits=[{"action": "remove", "id": "F4.1", "text": ""}],
        wrong_parts=["`In [11]: retry(13)` followed by `Out[11]: 0.5`"], screen=proposed))
    harness.check("the wrong fact goes", record["facts"] == ["The Gateway retries each call."], str((record["facts"], refusals)))
    harness.check("and the outline without the named line is taken", record["screen"] == proposed, str((record["screen"], refusals)))


def test_a_participant_tile_nested_in_the_outline_is_dropped_too():
    """A fix pass added the call's tile beside a slide as a nested part, "participant video box", with the person's
    name label quoted, and it was kept (lecture-1 frame 109, 2026-10-13)."""

    nested = ACCEPTED["screen"] + '\n  - participant video box: top right, dark overlay with name label "Talking - Jonas"'
    fuller = nested + '\n  - button "Save": lower right corner, grey'
    record, _, _ = addition_utils.apply_addition(ACCEPTED, _addition(screen=fuller))
    harness.check("the nested tile and its name go, the real addition stays",
                  "Jonas" not in record["screen"] and "Save" in record["screen"], record["screen"])


def test_a_question_asked_written_again_with_its_answer_replaces_the_held_one():
    """A fix wrote a question again with how the audience answered it, and the old "unanswered" one stood beside it
    (lecture-1 frame 185, 2026-10-14). A question opening with another question is added."""

    held = "Is the refund in three parts? Unanswered."
    answered = "Is the refund in three parts? Or in two? Answered by the room: three."
    other = "Who books the parts? Unanswered."
    accepted = {**ACCEPTED, "questions_asked": [held]}
    record, changes, _ = addition_utils.apply_addition(accepted, _addition(added_questions_asked=[answered, other]))
    harness.check("the answered question replaces the held one, the other is added",
                  record["questions_asked"] == [answered, other], str((record["questions_asked"], changes)))


def test_labels_the_outline_quotes_and_no_reader_finds_are_named_to_the_repair_defect():
    # An outline quoted a connector the frame does not show, taken from the speech, and the repair kept it (DEFECTS 278);
    # now the critique and the repair are told each quoted label the text reader finds nowhere on the frame
    import cv2
    import numpy
    from services import apple_text_service
    from services import outline_reread_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-004.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((90, 160, 3), 255, numpy.uint8))
    screen = ('- schematic: the board\n  - connector "K7B2": right, pins 1 and 2\n  - chip "MAX3232"\n'
              '  - label "Power supply"\n  - pin "GND"')
    read = [{"text": "MAX3232", "box": [0, 0, 1, 1], "line": 0}, {"text": "GND", "box": [0, 0, 1, 1], "line": 1}]
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: read
    try:
        harness.check("labels are the quoted names with a digit or two capitals",
                      outline_reread_service.list_quoted_labels(screen) == ["K7B2", "MAX3232", "GND"],
                      str(outline_reread_service.list_quoted_labels(screen)))
        harness.check("a label the reader finds is not named, one it misses is",
                      outline_reread_service.list_unread_labels(image_path, screen) == ["K7B2"])
        many = "\n".join(f'- pin "P{number}X"' for number in range(10))
        harness.check("a reader that misses the frame's labels wholesale names none",
                      outline_reread_service.list_unread_labels(image_path, many) == [])
        record_path = record_file_service.build_record_path(paths, "frame-004")
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(f"# Frame 4 · schematic\n\n## Screen\n{screen}\n", encoding="utf-8")

        class Request:
            pass
        request = Request()
        request.paths = paths
        item = {"slug": "frame-004", "path": "frames/frame-004.jpg"}
        note = frames_processing_messages.render_outline_reread_note(request, item,
                                                                    frames_processing_messages.SECOND_PASS_PHASE)
        harness.check("the repair's frame message names the label and where it goes",
                      '"K7B2"' in note and "## Not on this frame" in note, note)
        harness.check("the critique is told it is a mistake to report", "mistake to report" in
                      frames_processing_messages.render_outline_reread_note(request, item,
                                                                           frames_processing_messages.CRITIQUE_PHASE))
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_words_the_two_text_readers_read_differently_are_named_with_both_readings_defect():
    # The frame's message listed one reader's line as the screen's text, and its misread digit reached the notes ("Read
    # 42 lines" for 12, DEFECTS 579; the reader circle). Now each word the second reader reads otherwise is named, both ways
    import cv2
    import numpy
    from services import apple_text_service
    from services import outline_reread_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-004.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((90, 160, 3), 255, numpy.uint8))
    read = [{"text": text, "box": [index, 0, index + 1, 1], "line": line}
            for line, words in enumerate((["L", "Read", "12", "lines"], ["•", "Added", "29", "lines"],
                                          ["Die", "Angaben", "sind", "ohne", "Gewähr"]))
            for index, text in enumerate(words)]
    listed = ["| Read 42 lines", "- Added 29 lines", "Die Angaben sind chne Gewahr"]
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: read
    try:
        pairs = outline_reread_service.list_differing_words(image_path, listed)
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved
    harness.check("the misread digit and words are named both ways, marks and bullets are not",
                  pairs == [("42", "12"), ("chne Gewahr", "ohne Gewähr")], str(pairs))


def test_a_quoted_word_a_letter_off_the_earlier_frames_spelling_is_named_to_the_repair_defect():
    # A slide's small link was quoted a letter off on one frame while the frames before spelled it right, and nothing
    # compared the two (DEFECTS 276); now the critique and the repair are told each such word to check on the image
    import argparse

    import cv2
    import numpy

    from services import apple_text_service
    from services import phase_file_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-004.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((90, 160, 3), 255, numpy.uint8))
    items = [{"slug": f"frame-00{number}", "index": number, "path": f"frames/frame-00{number}.jpg",
              "record": {"screen": '- slide: the board\n  - link "acmesync-gateway.png": foot'}} for number in (1, 2, 3)]
    screen = '- slide: the board\n  - link "acmesynk-gateway.png": foot\n  - title "Remote ledger"'
    items.append({"slug": "frame-004", "index": 4, "path": "frames/frame-004.jpg", "record": {"screen": screen}})
    phase_file_service.write_phase_file(paths, 2, {"items": items})
    record_path = record_file_service.build_record_path(paths, "frame-004")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(f"# Frame 4 · slide\n\n## Screen\n{screen}\n", encoding="utf-8")
    saved = apple_text_service.is_available
    apple_text_service.is_available = lambda: False
    try:
        request = argparse.Namespace(paths=paths)
        item = {"slug": "frame-004", "path": "frames/frame-004.jpg"}
        note = frames_processing_messages.render_outline_reread_note(request, item,
                                                                    frames_processing_messages.SECOND_PASS_PHASE)
        harness.check("the repair is told the word and the earlier spelling, and nothing of the other words",
                      '"acmesynk" (earlier "acmesync")' in note and "ledger" not in note, note)
        harness.check("the first frame quoting the word is told nothing",
                      "acmesync" not in frames_processing_messages.render_outline_reread_note(
                          request, {"slug": "frame-001", "path": "frames/frame-001.jpg"},
                          frames_processing_messages.CRITIQUE_PHASE))
    finally:
        apple_text_service.is_available = saved


def test_the_columns_of_text_lines_the_reader_counts_are_named_to_every_pass_defect():
    # On a small terminal an outline left a third of a table's rows out, and no reader read them well enough to name
    # them (DEFECTS 333); now the first pass, the critique and the repair are told how many lines each column holds
    import argparse

    import cv2
    import numpy

    from services import apple_text_service
    from services import outline_reread_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-004.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((396, 545, 3), 20, numpy.uint8))
    rows = [{"text": f"acmesync_{row}", "box": [180, 200 + 60 * row, 600, 240 + 60 * row], "line": row} for row in range(9)]
    pane = [{"text": "owner Jonas", "box": [1700, 200, 2000, 240], "line": 20}]
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: rows + pane
    try:
        columns = outline_reread_service.list_text_columns(image_path)
        harness.check("one column of nine lines, a lone line no column",
                      len(columns) == 1 and columns[0][3] == 9, str(columns))
        request = argparse.Namespace(paths=paths)
        item = {"slug": "frame-004", "path": "frames/frame-004.jpg"}
        first = frames_processing_messages.render_text_columns_note(request, item)
        harness.check("the first pass is told the column and asked for every line", "9 lines starting" in first
                      and "every row" in first, first)
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_column_the_outline_quotes_whole_is_not_named_to_a_follow_up_defect():
    # The repair was told a column of 5 lines its outline already quoted whole, and spent its thinking looking for the
    # line it lacked (DEFECTS 508); now a follow-up is told only a column holding more lines than the outline quotes
    from pathlib import Path

    from services import outline_reread_service

    saved = outline_reread_service.list_text_columns
    outline_reread_service.list_text_columns = lambda image_path: [(0.03, 0.14, 0.79, 5)]
    whole = "\n".join(f'  - line "row {row}"' for row in range(5))
    short = "\n".join(f'  - line "row {row}"' for row in range(3))
    try:
        notes = [frames_processing_messages._render_text_columns(Path("frame.jpg"), frames_processing_messages.SECOND_PASS_PHASE,
                                                                 screen) for screen in (whole, short)]
    finally:
        outline_reread_service.list_text_columns = saved
    harness.check("no note for an outline quoting the column whole", notes[0] == "", notes[0])
    harness.check("the note for an outline quoting fewer lines", "5 lines starting" in notes[1], notes[1])


def test_a_program_added_line_is_shown_to_the_repair_and_kept_out_once_named_defect():
    # The program added a misreading of a small editor to an outline as a line, and no pass saw the program's lines
    # (DEFECTS 319); now the critique and the repair are told them, and a line the repair names is never added again
    import argparse

    import cv2
    import numpy

    from services import apple_text_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-004.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((90, 160, 3), 255, numpy.uint8))
    outline = '- editor: the Gateway module\n  - line "fetch the token from AcmeSync"'
    screen = f'{outline}\n{item_validator.SCREEN_COMPLETION_PART}\n  - text "Sorc Cade Jonas"'
    record_path = record_file_service.build_record_path(paths, "frame-004")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(f"# Frame 4 · editor\n\n## Screen\n{screen}\n", encoding="utf-8")
    saved = (apple_text_service.is_available, item_validator.read_screen_lines)
    apple_text_service.is_available = lambda: False
    item_validator.read_screen_lines = lambda paths, slug: ["fetch the token from AcmeSync", "Sorc Cade Jonas"]
    try:
        harness.check("the program's lines are read from the outline",
                      item_validator.list_program_screen_lines(screen) == ["Sorc Cade Jonas"])
        note = frames_processing_messages.render_outline_reread_note(
            argparse.Namespace(paths=paths), {"slug": "frame-004", "path": "frames/frame-004.jpg"},
            frames_processing_messages.SECOND_PASS_PHASE)
        harness.check("the repair is told the program's line and where to name it",
                      '"Sorc Cade Jonas"' in note and "## Not on this frame" in note, note)
        again = item_validator.add_unquoted_screen_lines(paths, "frame-004", {"screen": outline})
        harness.check("the reader's line is added to an outline that leaves it out", "Sorc Cade Jonas" in again["screen"])
        named = item_validator.add_unquoted_screen_lines(paths, "frame-004", {"screen": outline}, ['"Sorc Cade Jonas"'])
        harness.check("a line the repair named not on the frame is left out", "Sorc Cade Jonas" not in named["screen"],
                      named["screen"])
    finally:
        apple_text_service.is_available, item_validator.read_screen_lines = saved


def test_a_reader_line_the_repair_corrected_is_not_added_back_defect():
    # The outline copied the reader's misread "Read 434 lines"; the repair replaced it with the frame's "Read 131 lines",
    # and the program added the reader's line back beside it (DEFECTS 566); now a quote the repair edited out stays out
    from services import apple_text_service

    paths = fx.make_paths()
    before = {"screen": '- terminal: the whole frame\n  - line "Read(static/css/syntax.css)"\n  - line "Read 434 lines"'}
    after = {"screen": '- terminal: the whole frame\n  - line "Read(static/css/syntax.css)"\n  - line "Read 131 lines"'}
    saved = (apple_text_service.is_available, item_validator.read_screen_lines)
    apple_text_service.is_available = lambda: False
    item_validator.read_screen_lines = lambda paths, slug: ["Read(static/css/syntax.css)", "Read 434 lines"]
    try:
        edited_out = process_frames_node._list_edited_out_quotes(before, after)
        harness.check("the repair's replaced quote is named", edited_out == ["Read 434 lines"], str(edited_out))
        again = item_validator.add_unquoted_screen_lines(paths, "frame-095", after, edited_out)
        harness.check("the reader's misread line is not added back", "434" not in again["screen"], again["screen"])
    finally:
        apple_text_service.is_available, item_validator.read_screen_lines = saved


def test_the_program_s_reader_lines_are_never_counted_as_edited_out_defect():
    # A repair wrote an outline again whole; the reader lines the program had added under the old one counted as
    # edited out, were not added back, and the right outline was refused (DEFECTS 571)
    before = {"screen": '- room: lecture hall\n  - board "Agent harness"\n'
                        f'{item_validator.SCREEN_COMPLETION_PART}\n  - text "Ignore the lib directory"'}
    after = {"screen": '- terminal: the whole frame\n  - line "Found 1 renaming import"'}
    edited_out = process_frames_node._list_edited_out_quotes(before, after)
    harness.check("only the worker's own replaced quote is named", edited_out == ["Agent harness"], str(edited_out))


def test_a_label_inside_a_longer_quote_is_named_with_its_quote_defect():
    # The critique was told the reader finds no "PKCS" on the frame, a piece of the quote "sig_id: PKCS#7" the frame
    # shows; it looked for a label of its own until its thinking looped (DEFECTS 335). The label now comes with its quote,
    # and the critique is told a label the image shows is right
    from prompts import frames_processing_messages as messages

    screen = '- panel: the module\n  - field "sig_id: K7B2#7"\n  - connector "J5A1": right'
    harness.check("a label inside a longer quote is named with the quote",
                  messages._name_label_in_quote("K7B2", screen) == '"K7B2" (in "sig_id: K7B2#7")')
    harness.check("a label that is its whole quote is named alone", messages._name_label_in_quote("J5A1", screen) == '"J5A1"')
    harness.check("the critique is told a label the image shows is nothing to report",
                  "shows is right and nothing to report" in messages.UNREAD_LABELS_NOTE[messages.CRITIQUE_PHASE])


def test_two_unread_labels_are_each_judged_alone_defect():
    # Told of two labels "one the frame shows is right; one it does not show is a mistake", the critique read it as one
    # of the two being wrong and thought over an hour on which, while the frame showed both. Each is now judged alone
    from prompts import frames_processing_messages as messages

    for phase in (messages.CRITIQUE_PHASE, messages.SECOND_PASS_PHASE):
        note = messages.UNREAD_LABELS_NOTE[phase]
        harness.check(f"the {phase} note asks for each label alone, all of them possibly shown",
                      "judge each alone" in note and "even when all of them are shown" in note and "one the frame" not in note,
                      note)


def test_a_quoted_line_read_with_other_numbers_is_named_to_the_repair_defect():
    # A terminal's table was outlined with the numbers of other rows and passed every check (DEFECTS 284); now the
    # critique and the repair are told each quoted line the reader reads on the same row with other numbers
    import cv2
    import numpy
    from services import apple_text_service
    from services import outline_reread_service

    paths = fx.make_paths()
    image_path = paths.output_root / "frames" / "frame-006.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), numpy.full((90, 1600, 3), 255, numpy.uint8))
    screen = ('- terminal: black background\n  - line "PORT 01 LOAD: 41 PEAK: 77 IDLE: 12"\n'
              '  - line "PORT 02 LOAD: 38 PEAK: 70 IDLE: 19"\n  - line "uptime 4 days, 3 users, load 0.52"')
    read = [{"text": text, "box": [index * 10, 0, index * 10 + 9, 9], "line": line}
            for line, words in enumerate(["PQRT 01 LQAD: 41 PEAK: 71 IDLE: 12", "PORT 02 LOAD: 38 PEAK: 70 IDLE: 19",
                                          "uptime 4 days, 3 users, load 0.5"])
            for index, text in enumerate(words.split())]
    saved = (apple_text_service.is_available, apple_text_service.find_words_in_image)
    apple_text_service.is_available = lambda: True
    apple_text_service.find_words_in_image = lambda image: read
    try:
        pairs = outline_reread_service.list_misread_numbers(image_path, screen)
        harness.check("the row read with another number is named, letters garbled or not; a number read a digit "
                      "short is the reader's", [quote for quote, _line in pairs] == ["PORT 01 LOAD: 41 PEAK: 77 IDLE: 12"],
                      str(pairs))
        record_path = record_file_service.build_record_path(paths, "frame-006")
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(f"# Frame 6 · terminal\n\n## Screen\n{screen}\n", encoding="utf-8")

        class Request:
            pass
        request = Request()
        request.paths = paths
        note = frames_processing_messages.render_outline_reread_note(
            request, {"slug": "frame-006", "path": "frames/frame-006.jpg"}, frames_processing_messages.SECOND_PASS_PHASE)
        harness.check("the repair is told the line, the reading, and to write the outline again",
                      '"PORT 01 LOAD: 41 PEAK: 77 IDLE: 12"' in note and "PEAK: 71" in note and "## Screen" in note, note)
    finally:
        apple_text_service.is_available, apple_text_service.find_words_in_image = saved


def test_a_where_line_quoting_an_outline_line_names_the_outline_defect():
    # A critique named misread outline lines by quoting them, not by the word "outline"; the fix's corrected outline,
    # shorter, was refused and the misreads stayed (DEFECTS 286); now quoting a line of the outline names it
    rules = [{"name": "A label is quoted wrong", "frames": [4],
              "where": ['frame 4: the `- entry "Gateway stats": visible below` line (image shows `gateway status`)']}]
    screen = '- terminal: dark\n  - entry "Gateway stats": visible below\n  - entry "Port list": below'
    harness.check("the quoted line names the outline", "screen" in addition_utils.list_critiqued_fields(rules, 4, screen))
    harness.check("without the frame's outline nothing is named by quotes",
                  "screen" not in addition_utils.list_critiqued_fields(rules, 4))
    harness.check("a quote the outline does not hold names nothing",
                  "screen" not in addition_utils.list_critiqued_fields(rules, 4, '- terminal: dark\n  - entry "Other": below'))


def test_an_outline_line_corrected_by_the_fix_is_corrected_in_the_markdown_too_defect():
    # A fix added a missing part of a slide's line to the outline and the markdown copy kept the old line (DEFECTS 287);
    # now the line the outline corrects is corrected in the markdown when the fix wrote no markdown
    accepted = {**ACCEPTED, "screen": '- slide: white\n  - entry "export: Write the ledger to a file"\n  - entry "help: This list"',
                "as_markdown": "export: Write the ledger to a file\nhelp: This list"}
    addition = {"frame": 0, "what_changed": "the export entry misses [csv|json]",
                "screen": '- slide: white\n  - entry "export: Write the ledger [csv|json] to a file"\n  - entry "help: This list"'}
    record, changes, _refusals = addition_utils.apply_addition(accepted, addition, frozenset({"screen"}))
    harness.check("the markdown line takes the correction, the other line stays",
                  record["as_markdown"] == "export: Write the ledger [csv|json] to a file\nhelp: This list"
                  and "as_markdown" in changes, record["as_markdown"])


def test_a_part_named_not_on_the_frame_leaves_the_markdown_copy_too_defect():
    # A fix named a later frame's output lines not on this frame; they left the outline and stayed in the markdown
    # copy, which the document then cited for this frame (DEFECTS 294)
    accepted = {**ACCEPTED, "screen": '- terminal: dark\n  - line "help: This list"\n  - line "select 10"',
                "as_markdown": "help: This list\nselect 10"}
    addition = {"frame": 0, "what_changed": "the select line is a later frame's", "wrong_parts": ['"select 10"'],
                "screen": '- terminal: dark\n  - line "help: This list"'}
    record, changes, _refusals = addition_utils.apply_addition(accepted, addition, frozenset({"screen"}))
    harness.check("the named line leaves the markdown, the frame's own stays",
                  record["as_markdown"] == "help: This list" and "as_markdown" in changes, record["as_markdown"])


def test_lines_the_first_reader_missed_are_named_for_the_frame_defect():
    # A file manager's names on a low-resolution frame were read by no Tesseract pass and the outline quoted none of
    # them; the frame's message now names the lines Apple's recognizer reads that the listed lines leave out
    import tempfile
    from pathlib import Path

    import cv2
    import numpy

    from services import apple_text_service
    from services import outline_reread_service

    if not apple_text_service.is_available():
        print("Apple's text recognizer not on this machine: skipped")
        return
    image = numpy.full((300, 900, 3), 255, numpy.uint8)
    cv2.putText(image, "PALETTES", (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    cv2.putText(image, "Gateway refunds", (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    cv2.putText(image, "42", (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    path = Path(tempfile.mkdtemp()) / "frame-001.jpg"
    cv2.imwrite(str(path), image)
    missed = outline_reread_service.list_missed_lines(path, ["Gateway refunds"])
    harness.check("a line the listed lines lack is named", any("PALETTES" in line for line in missed), str(missed))
    harness.check("a listed line is not named again", not any("Gateway" in line for line in missed), str(missed))
    harness.check("a line of no letter is no text to quote", not any(line.strip() == "42" for line in missed),
                  str(missed))


def test_the_second_look_keeps_an_outline_quoted_in_as_markdown_too_defect():
    # A critique asked a README's outline to drop its quotes because As markdown held the text, against its own
    # instruction; the repair's instruction answered only the reverse rule. It now answers both by nothing
    import config

    instruction = " ".join((config.PLAYBOOK_ROOT / "agent-context" / "instructions" / "_shared" / "second-pass.md")
                           .read_text(encoding="utf-8").split())
    harness.check("a rule asking the outline to drop quotes As markdown holds is answered by nothing",
                  "So is a rule asking `## Screen` to drop quotes because `## As markdown` holds them" in instruction
                  and "the outline keeps its parts" in instruction, "")


def test_the_second_look_removes_a_fact_about_the_talk_and_never_widens_it_defect():
    # A repair told a fact about the talk is no fact rewrote "the training takes two half days" as a claim about all
    # trainings of its kind. Its instruction now says such a fact is removed, never made a general claim
    import config

    instruction = " ".join((config.PLAYBOOK_ROOT / "agent-context" / "instructions" / "_shared" / "second-pass.md")
                           .read_text(encoding="utf-8").split())
    harness.check("a fact about the talk already in the notes is removed by its id",
                  "Such a fact already in the notes is removed (`- remove F<frame>.<note>`)" in instruction, "")
    harness.check("and never rewritten as a claim about everything of its kind",
                  "never rewritten as a claim about everything of its kind" in instruction, "")


def test_the_second_look_is_told_a_fact_s_subject_is_never_the_speaker_defect():
    # 110 of 552 facts second passes added had the speaker as their subject, many a remark about the talk a critique
    # rule asked for; its instruction now gives the first pass's rule, a fact is about the thing taught
    paths = fx.make_paths()
    request = chain_dispatch_service.AttemptRequest(job_kind="second_pass", attempt=1, repository=paths.repository,
                                                    module=paths.module, paths=paths, ignore=IgnoreList(),
                                                    inputs={"slug": "frames-000-000", "batch": {
                                                        "frames": [0], "start": 0.0, "end": 6.0, "summary": "",
                                                        "accepted": [{**ACCEPTED, "slug": "frame-000", "index": 0,
                                                                      "timestamp": 0.0, "builtFrom": []}],
                                                        "atmosphere": "", "items": [],
                                                        "spoken": [{"index": 0, "timestamp": 0.0}]}})
    system = frames_processing_messages.build_second_pass_messages(request)[0].content
    harness.check("a fact added has the thing taught as its subject, never the speaker",
                  "never the speaker or the talk" in system, system[:300])
    harness.check("and a remark about the talk is no fact, whatever a critique rule asks",
                  "is no fact, whatever a critique" in system)


def test_a_remark_on_the_notes_is_never_added_as_a_fact_defect():
    # a second look added "The notes are complete." twice as facts, which the merge would carry as knowledge; such a
    # remark is now left out of what an addition adds, and a real fact beside it is added
    addition = {"frame": 0, "added_facts": ["The notes are complete regarding the commit details.",
                                            "The Gateway retries a refund three times."]}
    record, _changes, _refusals = addition_utils.apply_addition({**ACCEPTED, "facts": ["Refunds settle in three days."]},
                                                                addition)
    harness.check("the remark is left out, the fact added",
                  "The Gateway retries a refund three times." in record["facts"]
                  and not any("notes are complete" in fact for fact in record["facts"]), str(record["facts"]))


def test_the_first_pass_and_the_second_look_quote_a_hidden_line_as_far_as_it_shows_defect():
    # a second look completed two .gitignore lines a picture laid over the slide hid, from what such lines usually
    # say; both passes are now told a line hidden in part is written as far as it shows, never completed
    paths = fx.make_paths()
    request = chain_dispatch_service.AttemptRequest(job_kind="second_pass", attempt=1, repository=paths.repository,
                                                    module=paths.module, paths=paths, ignore=IgnoreList(),
                                                    inputs={"slug": "frames-000-000", "batch": {
                                                        "frames": [0], "start": 0.0, "end": 6.0, "summary": "",
                                                        "accepted": [{**ACCEPTED, "slug": "frame-000", "index": 0,
                                                                      "timestamp": 0.0, "builtFrom": []}],
                                                        "atmosphere": "", "items": [],
                                                        "spoken": [{"index": 0, "timestamp": 0.0}]}})
    second = " ".join(frames_processing_messages.build_second_pass_messages(request)[0].content.split())
    first = " ".join(system_prompt_builder.build_system_prompt("frames_processing", {}).split())
    for name, text in (("the second look", second), ("the first pass", first)):
        harness.check(f"{name} writes a hidden line as far as it shows, never completed",
                      "hidden in part under something laid over" in text and "never completed from what such" in text)


def test_a_slashed_zero_is_read_as_zero_by_the_first_pass_and_the_repair_defect():
    # A terminal font's slashed zero was read as 8 by the critique and both readers, and the repair copied the
    # critique's misread chart labels into the outline (DEFECTS 344); both instructions now say it is 0, and the repair
    # reads a value the critique quotes again on the image
    from prompts import system_prompt_builder

    first = system_prompt_builder.build_system_prompt("frames_processing", {})
    repair = system_prompt_builder.build_system_prompt(frames_processing_messages.SECOND_PASS_PHASE, {})
    harness.check("the first pass is told a slashed zero is 0", "zero with a slash or a dot through it: that digit is 0" in first)
    harness.check("the repair reads the critique's values again, a slashed zero being 0",
                  "A value the critique quotes is read again on the image" in repair and "slashed or dotted zero is 0" in repair)


def test_a_repair_edits_the_line_its_id_names_defect():
    # Defect: the repair could only add, so its corrected line stood beside the old one whenever the program's guess
    # of the replaced line missed (DEFECTS 357). Fix: an edit names its line by id, and the program applies it there.
    record = {"frame": 7, "facts": ["The gateway retries twice.", "Retries are capped at five."],
              "take_aways": ['"two retries" -> the table: the gateway retries', "Take aways: the gateway retries."]}
    edits = [{"action": "replace", "id": "F7.1", "text": "The gateway retries three times."},
             {"action": "remove", "id": "T7.1", "text": ""}]
    changed, changes, refusals = addition_utils.apply_addition(record, _addition(frame=7, edits=edits))
    harness.check("the named fact is replaced in its place, the other kept",
                  changed["facts"] == ["The gateway retries three times.", "Retries are capped at five."], str(changed["facts"]))
    harness.check("the named take-away is removed", changed["take_aways"] == ["Take aways: the gateway retries."],
                  str(changed["take_aways"]))
    harness.check("nothing refused", refusals == [], str(refusals))
    _, _, refused = addition_utils.apply_addition(record, _addition(frame=7, edits=[{"action": "remove", "id": "F7.9", "text": ""}]))
    harness.check("an id the notes do not show changes nothing and is named", refused and "F7.9" in refused[0], str(refused))
    added, _, _ = addition_utils.apply_addition(record, _addition(frame=7, added_facts=["The gateway retries three times."]))
    harness.check("an added line never replaces a held one", added["facts"][:2] == record["facts"], str(added["facts"]))


def test_a_repair_edits_a_line_of_the_outline_by_its_id_defect():
    # Defect: an outline line had no id, so a repair correcting one word of it wrote "replace screen: <line>" and was
    # refused (DEFECTS 420). Fix: each line of the outline is shown with an id, and an edit names it like a fact
    screen = ('- slide: white, the whole frame\n  - title "Gateway limits": top left\n  - logo "AcmeSync": upper right\n'
              '    - caption "beta": under it\n  - footer "Jonas": bottom')
    record = {"frame": 7, "facts": ["The gateway retries twice."], "take_aways": [], "screen": screen}
    shown = frame_markdown_utils.render_notes_to_review(record, True)
    harness.check("the repair is shown each outline line with its id",
                  '  - [O7.3] logo "AcmeSync": upper right' in shown and "- [O7.1] slide" in shown, shown)
    edits = [{"action": "replace", "id": "O7.3", "text": 'logo "AcmeSync": upper left'},
             {"action": "remove", "id": "O7.5", "text": ""}]
    changed, _, refusals = addition_utils.apply_addition(record, _addition(frame=7, edits=edits))
    harness.check("the named line is replaced in its place and indent, the removed one gone, the rest kept",
                  changed["screen"] == ('- slide: white, the whole frame\n  - title "Gateway limits": top left\n'
                                        '  - logo "AcmeSync": upper left\n    - caption "beta": under it'),
                  repr(changed["screen"]))
    harness.check("nothing refused", refusals == [], str(refusals))
    answer = frame_markdown_utils.parse_answer('# Frame 7\n\n## What changed\nThe logo sits left.\n\n## Edits\n'
                                               '- replace O7.3: logo "AcmeSync": upper left\n', is_addition=True)
    harness.check("the answer reader takes an outline id", not answer["errors"]
                  and answer["frames"][0]["edits"][0]["id"] == "O7.3", str(answer["errors"]))


def test_a_part_added_under_an_outline_line_lands_inside_it_defect():
    # Defect: a repair that found a part missing from the outline wrote "add under `[O126.2]`: line ...", no edit had
    # that shape, and its corrections were ignored (Lecture 7, DEFECTS 572). Fix: "add under O<frame>.<n>: <part>"
    # writes the part after the named line and every line inside it, one level deeper
    screen = ('- slide: white, the whole frame\n  - logo "AcmeSync": upper right\n    - caption "beta": under it\n'
              '  - footer "Jonas": bottom')
    record = {"frame": 7, "facts": ["The gateway retries twice."], "take_aways": [], "screen": screen}
    answer = frame_markdown_utils.parse_answer('# Frame 7\n\n## What changed\nThe logo has a second caption.\n\n'
                                               '## Edits\n- add under `[O7.2]`: caption "v2": under the first\n'
                                               '- add under O7.4: line "page 3"\n', is_addition=True)
    edits = answer["frames"][0]["edits"]
    harness.check("the reader takes both add-under lines, the backticked id too",
                  [(edit["action"], edit["id"]) for edit in edits] == [("add under", "O7.2"), ("add under", "O7.4")],
                  str(edits))
    changed, _, refusals = addition_utils.apply_addition(record, _addition(frame=7, edits=edits))
    harness.check("each part lands after its line's own parts, one level deeper",
                  changed["screen"] == ('- slide: white, the whole frame\n  - logo "AcmeSync": upper right\n'
                                        '    - caption "beta": under it\n    - caption "v2": under the first\n'
                                        '  - footer "Jonas": bottom\n    - line "page 3"'), repr(changed["screen"]))
    harness.check("nothing refused", refusals == [], str(refusals))
    _, _, refusals = addition_utils.apply_addition(record, _addition(frame=7, edits=[
        {"action": "add under", "id": "F7.1", "text": "The gateway waits a second."}]))
    harness.check("an add under a fact is refused by name, never written over the fact",
                  any("outline line only" in refusal for refusal in refusals), str(refusals))


def test_a_word_corrected_in_the_outline_is_corrected_in_every_field_defect():
    # Defect: a run-together misread fixed by the repair in the outline stayed in the markdown block and the narration,
    # which have no ids to edit (DEFECTS 483). Fix: the exact words a replace corrects are corrected in every field
    screen = '- slide: white, the whole frame\n  - title "Gatewayretries": top left\n  - logo "AcmeSync": upper right'
    record = {"frame": 33, "screen": screen, "facts": ["The Gatewayretries slide names two limits."],
              "take_aways": ['"the gateway retries twice" → title "Gatewayretries": the limit'],
              "third_person_description": "The slide titled Gatewayretries lists the limits.",
              "as_markdown": "# Gatewayretries\n\n- twice a call"}
    edits = [{"action": "replace", "id": "O33.2", "text": 'title "Gateway retries": top left'}]
    changed, _, refusals = addition_utils.apply_addition(record, _addition(frame=33, edits=edits))
    harness.check("the markdown block and the narration take the correction",
                  changed["as_markdown"].startswith("# Gateway retries")
                  and "titled Gateway retries" in changed["third_person_description"],
                  repr((changed["as_markdown"], changed["third_person_description"])))
    harness.check("the fact takes it too", changed["facts"] == ["The Gateway retries slide names two limits."],
                  str(changed["facts"]))
    harness.check("a take-away's quote of the speech stays as said, its screen part corrected",
                  changed["take_aways"] == ['"the gateway retries twice" → title "Gateway retries": the limit'],
                  str(changed["take_aways"]))
    harness.check("nothing refused", refusals == [], str(refusals))


def test_a_part_moved_in_the_outline_is_no_correction_of_other_fields_defect():
    # Defect guarded: carrying corrections across fields must not spread a moved part's position word ("right" to
    # "left") into facts that say "right" of something else (DEFECTS 483's fix)
    screen = '- slide: white, the whole frame\n  - logo "AcmeSync": upper right'
    record = {"frame": 7, "screen": screen, "facts": ["The arrow points right, to the partner."], "take_aways": []}
    edits = [{"action": "replace", "id": "O7.2", "text": 'logo "AcmeSync": upper left'}]
    changed, _, _ = addition_utils.apply_addition(record, _addition(frame=7, edits=edits))
    harness.check("a fact's own word stays", changed["facts"] == ["The arrow points right, to the partner."],
                  str(changed["facts"]))


def test_a_repair_removes_an_open_question_by_its_id_defect():
    # Defect: open questions had no id, so a repair told to take one out wrote "remove O19.10", an outline line's id,
    # was refused, and the question stayed beside the fact answering it (DEFECTS 480). Fix: each open question is
    # shown with an id "Q<frame>.<n>", and an edit names it like a fact
    record = {"frame": 19, "facts": ["Cookiecutter makes project templates."], "take_aways": [],
              "open_questions": ["cookie cutter (spoken, not shown)", "which layout suits data projects"],
              "screen": '- slide: white\n  - title "General Hints": top left'}
    shown = frame_markdown_utils.render_notes_to_review(record, True)
    harness.check("the repair is shown each open question with its id", "[Q19.1] cookie cutter" in shown, shown)
    answer = frame_markdown_utils.parse_answer('# Frame 19\n\n## What changed\nThe tool is a fact, not a question.\n\n'
                                               '## Edits\n- remove Q19.1\n', is_addition=True)
    harness.check("the answer reader takes an open question's id", not answer["errors"]
                  and answer["frames"][0]["edits"][0]["id"] == "Q19.1", str(answer["errors"]))
    changed, _, refusals = addition_utils.apply_addition(record, _addition(frame=19, edits=answer["frames"][0]["edits"]))
    harness.check("the named question is gone, the other stays, nothing refused",
                  changed["open_questions"] == ["which layout suits data projects"] and refusals == [],
                  f"{changed['open_questions']} {refusals}")


def test_a_name_a_replace_corrects_is_corrected_in_every_fact():
    # The outline shows the name as corrected: a correction is carried only to what the screen shows (DEFECTS 483)
    record = {"frame": 3, "facts": ["The QX5 gateway retries twice.", "The QX5 gateway logs every retry."],
              "screen": '- slide: white\n  - title "QXS gateway": top left',
              "take_aways": [], "open_questions": [], "action_items": [], "questions_asked": [], "tags": []}
    edits = [{"action": "replace", "id": "F3.1", "text": "The QXS gateway retries twice."}]
    updated, _, _ = addition_utils.apply_addition(record, _addition(frame=3, edits=edits))
    harness.check("the other fact gets the corrected name too", "The QXS gateway logs every retry." in updated["facts"],
                  str(updated["facts"]))


def test_an_added_line_loses_the_id_it_copied_defect():
    # Defect: a repair shown each line's id wrote its new fact as "[F1.5] <fact>", and the id stood in the notes.
    # Fix: an added fact or take-away is stored without an id in front.
    record = {"frame": 1, "facts": ["The talk covers terminals."], "take_aways": []}
    changed, _, _ = addition_utils.apply_addition(record, _addition(frame=1, added_facts=["[F1.2] The tool is open source."]))
    harness.check("the added fact has no id", changed["facts"][-1] == "The tool is open source.", str(changed["facts"]))

