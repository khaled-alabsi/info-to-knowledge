"""One frame's message, one frame's record, and the file the records collect in.

The message is REQUIREMENTS.md R2's prompt: the notes on earlier frames, what was spoken in the minutes before it,
the frame number and timestamp it is TOLD, what is spoken while it is on screen, and the frame.
The record is R3's layer 1. The records are R4's notes in each item of `02_frames.json`, which `--worker N`
must update in place.
"""

import base64

from support import fixtures as fx
from support import harness

import config
from models import output_contract
from models.attempt_request import AttemptRequest
from models.validation_report import ValidationReport
from prompts import frames_processing_messages
from schemas import frame_record
from services import phase_file_service
from services import call_log_service
from services import frame_records_service
from services import record_file_service
from services.ignore_list_service import IgnoreList
from utils import frame_markdown_utils
from utils import instruction_example_utils
from validators import item_validator

JPEG_BYTES = b"\xff\xd8\xff\xe0 not a real frame, and the builder must not care"
RECORD = {"frame": 5, "screen_kind": "slide",
          "take_aways": ['"89 Prozent" → the figure "89 %": most voters want cheaper fuel'],
          "facts": ["89 % of voters want cheaper fuel, according to the poll labelled WAHL 2026",
                        "The poll is the basis for the fuel-price argument that follows"],
          "diagram": "", "screen": '- slide: white, the whole frame\n  - text "WAHL 2026": centre\n  - text "89 %": centre',
          "speech_visual_alignment": "the speech reads out the 89 % figure the slide shows",
          "builds_on": [], "contradicts_earlier": [], "open_questions": ["who ran the poll"],
          "tags": ["refund policy", "reference"],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [],
          "third_person_description": "The presenter shows a poll slide and reads out the 89 % figure as the basis of the fuel-price argument."}
DIAGRAM = 'flowchart LR\n    A["Partner portal"] --> B["Gateway OIDC proxy"]'

ANSWERED = {"outcome": "completed", "parsed": RECORD, "text": "", "thinking": "", "finishReason": "stop",
            "parsingError": ""}


def _build_item(paths,
                index: int = 5) -> dict:
    frame = paths.output_root / "frames" / f"frame-{index:03d}.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(JPEG_BYTES)

    item = {"slug": f"frame-{index:03d}", "index": index, "timestamp": 30.04, "path": f"frames/frame-{index:03d}.jpg",
            "transcriptBefore": "Guten Abend.", "transcriptCurrent": "89 Prozent wollen billigeren Sprit."}
    # Phase 2 lists every frame in the frames file, whose items then hold each frame's notes
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    listed = (phase_file_service.read_phase_file(paths, frames_number).get("items", [])
              if phase_file_service.has_phase_file(paths, frames_number) else [])
    if all(existing["slug"] != item["slug"] for existing in listed):
        phase_file_service.write_phase_file(paths, frames_number, {"items": [*listed, item]})

    return item


def _build_request(paths,
                   previous: list[dict] | None = None,
                   findings: list[dict] | None = None,
                   latest_diagram: dict | None = None) -> AttemptRequest:
    item = _build_item(paths)

    return AttemptRequest(job_kind="frames_processing", attempt=2 if findings else 1, repository=paths.repository,
                          module=paths.module, paths=paths, ignore=IgnoreList(),
                          inputs={"slug": item["slug"], "item": item, "previousRecords": previous or [],
                                  "latestDiagram": latest_diagram},
                          findings=findings or [])


def _write_attempt(paths,
                   record: dict | None,
                   **response) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frame-005"),
                                    {"response": {**ANSWERED, **response}})
    if record is not None:
        record_file_service.write_frame_record(paths, "frame-005", record)


def _list_codes(paths) -> set[str]:
    return harness.codes(item_validator.validate_item(paths, "frame-005"))


def _list_note_codes(paths) -> set[str]:
    return {note.code for note in item_validator.validate_item(paths, "frame-005").notes}


def test_the_message_tells_the_frame_its_number_its_time_and_the_speech_around_it():
    paths = fx.make_paths()
    messages = frames_processing_messages.build_frame_messages(_build_request(paths))
    text = messages[1].content[0]["text"]
    harness.check("the frame is TOLD its number, it does not count", "frame 5" in text, text)
    harness.check("and its timestamp", "30.0 s" in text, text)
    harness.check("what was spoken before it", "Guten Abend." in text, text)
    harness.check("what is spoken while it is on screen", "89 Prozent wollen billigeren Sprit." in text, text)
    harness.check("the first frame of a run is told there are no earlier notes", "no earlier frame" in text, text)


def test_the_frame_travels_as_an_image_part_and_is_the_file_on_disk():
    paths = fx.make_paths()
    parts = frames_processing_messages.build_frame_messages(_build_request(paths))[1].content
    harness.check("the human message has a text part and an image part",
                  [part["type"] for part in parts] == ["text", "image_url"], str([part["type"] for part in parts]))
    encoded = parts[1]["image_url"]["url"].split(",", 1)[1]
    harness.check("the image part is the frame's own JPEG", base64.b64decode(encoded) == JPEG_BYTES)


def test_earlier_notes_are_rendered_from_the_accepted_records_it_is_handed():
    paths = fx.make_paths()
    earlier = [{"slug": "frame-004", "index": 4, "timestamp": 24.0, **RECORD, "frame": 4}]
    text = frames_processing_messages.build_frame_messages(_build_request(paths, previous=earlier))[1].content[0]["text"]
    harness.check("the earlier frame is named by its number", "Frame 4" in text, text)
    harness.check("with what it saw on screen", "WAHL 2026" in text, text)


def test_the_operators_question_is_rendered_where_the_instruction_puts_it():
    paths = fx.make_paths()
    previous = config.settings.prompt.user_prompt
    config.settings.prompt.user_prompt = "Which parties are named?"
    try:
        text = frames_processing_messages.build_frame_messages(_build_request(paths))[1].content[0]["text"]
    finally:
        config.settings.prompt.user_prompt = previous
    harness.check("the operator's question reaches the frame", "Which parties are named?" in text, text)


def test_a_retry_is_told_what_was_rejected():
    paths = fx.make_paths()
    # The keys a ValidationReport finding really carries: `code`, `detail`, `scope`, `fix`. A fixture with a
    # `message` key once kept this test green while every real retry lost its explanation.
    finding = {"code": "FRAME_RECORD_EMPTY", "detail": "every field was empty", "scope": "target",
               "fix": "state what the moment teaches"}
    text = frames_processing_messages.build_frame_messages(_build_request(paths, findings=[finding]))[1].content[0]["text"]
    harness.check("the retry names the finding", "FRAME_RECORD_EMPTY" in text and "state what the moment teaches" in text,
                  text)
    harness.check("and says what was wrong, in the validator's own words", "every field was empty" in text, text)


def test_the_record_is_the_layer_1_record_the_requirements_state():
    fields = set(frame_record.FRAME_FIELDS)
    harness.check("facts, a diagram, the screen, the links to earlier notes, and the fields the merge reads",
                  fields == {"frame", "screen_kind", "take_aways", "facts", "diagram", "as_markdown", "screen", "speech_visual_alignment",
                             "builds_on", "contradicts_earlier", "open_questions", "urls", "action_items",
                             "questions_asked", "screenshots", "tags", "third_person_description"}, str(fields))


def test_an_address_keeps_its_three_parts_and_none_is_an_answer():
    """R8: an address alone cannot say what the page is or why it is in the notes."""

    harness.check("no `## Web addresses` section is no address",
                  frame_markdown_utils.parse_answer("# Frame 5\n", is_addition=False)["frames"][0]["urls"] == [])
    url = {"url": "https://docs.example.com/guide/onboarding…", "what_it_is": "the onboarding guide",
           "how_it_appears": "linked from the page on screen; cut off at the window's edge"}
    text = frame_markdown_utils.render_frame_document({**RECORD, "urls": [url]})
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)["frames"][0]["urls"]
    harness.check("the address, what it is and how it appears are all kept", parsed == [url], text)


def test_a_good_record_is_accepted():
    paths = fx.make_paths()
    _write_attempt(paths, RECORD)
    harness.check("a complete record raises no finding", _list_codes(paths) == set(), str(_list_codes(paths)))


def test_a_reply_that_was_only_thinking_is_rejected_and_never_reads_a_stale_record():
    """The defect this project was rebuilt over: 17 empty answers, reported as success."""

    paths = fx.make_paths()
    _write_attempt(paths, RECORD, parsed=None, thinking="a thousand characters of reasoning")
    harness.check("thinking with no answer is rejected, whatever record an earlier attempt left",
                  _list_codes(paths) == {"RESPONSE_THINKING_ONLY"}, str(_list_codes(paths)))


def test_a_reply_cut_at_the_limit_is_rejected():
    paths = fx.make_paths()
    _write_attempt(paths, None, parsed=None, finishReason="length")
    harness.check("`length` is a limit, not an answer", _list_codes(paths) == {"RESPONSE_CUT_AT_LIMIT"})


def test_each_record_code_fires_on_its_own_defect():
    paths = fx.make_paths()
    _write_attempt(paths, None)
    harness.check("no record on disk is FRAME_RECORD_MISSING", _list_codes(paths) == {"FRAME_RECORD_MISSING"})

    _write_attempt(paths, RECORD)
    record_file_service.build_record_path(paths, "frame-005").write_text("[1, 2]", encoding="utf-8")
    harness.check("a record that is not an object is FRAME_RECORD_UNPARSEABLE",
                  _list_codes(paths) == {"FRAME_RECORD_UNPARSEABLE"})

    _write_attempt(paths, {**RECORD, "frame": 9})
    harness.check("a record for another frame is FRAME_INDEX_MISMATCH", _list_codes(paths) == {"FRAME_INDEX_MISMATCH"})

    empty = {**RECORD, "facts": [], "diagram": " ", "screen": '', "speech_visual_alignment": ""}
    _write_attempt(paths, empty)
    harness.check("a record that says nothing is FRAME_RECORD_EMPTY", _list_codes(paths) == {"FRAME_RECORD_EMPTY"})

    # The lookalike that must pass: a moment with facts but no text worth quoting and nothing to draw.
    _write_attempt(paths, {**RECORD, "screen": "- terminal: black, the whole frame\n  - cursor: blinking, top left",
                           "diagram": ""})
    harness.check("facts with no text on screen is still a record", _list_codes(paths) == set())

    _write_attempt(paths, {**RECORD, "facts": [], "screen": '- slide: white, the whole frame\n  - text "Partner Portal": centre\n  - text "Gateway OIDC Proxy": centre\n  - text "LDAP": centre'})
    harness.check("a screen full of text with no facts drawn from it is FRAME_FACTS_MISSING",
                  _list_codes(paths) == {"FRAME_FACTS_MISSING"}, str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "facts": [], "screen": '- slide: white, the whole frame\n  - text "AcmeSync": centre'})
    harness.check("a title card with one line of text needs no facts", _list_codes(paths) == set())
    popup = '- popup "Keep signed in": centre\n  - text "Close this window": below\n  - button "OK": bottom right'
    _write_attempt(paths, {**RECORD, "facts": [], "screen": popup, "questions_asked": ["Why does the request need the IDE?"]})
    harness.check("a participant's question over a popup needs no facts", _list_codes(paths) == set(), str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "facts": [], "screen": popup})
    harness.check("the same popup with no question still does", _list_codes(paths) == {"FRAME_FACTS_MISSING"},
                  str(_list_codes(paths)))

    _write_attempt(paths, {**RECORD, "diagram": "Partner portal -> Gateway -> OIDC provider"})
    harness.check("a diagram that is not Mermaid is FRAME_DIAGRAM_NOT_MERMAID",
                  _list_codes(paths) == {"FRAME_DIAGRAM_NOT_MERMAID"}, str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "diagram": "(none) - the same flow as the last frame"})
    # The empty mark followed by any reason is no entry (DEFECTS 645, "Circle fix: the empty mark followed by any
    # reason is no entry"): no diagram, so nothing to refuse
    harness.check("the empty mark with a reason after it is no diagram, and refused for nothing",
                  _list_codes(paths) == set(), str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "diagram": DIAGRAM})
    harness.check("a Mermaid flowchart is a diagram", _list_codes(paths) == set(), str(_list_codes(paths)))
    broken = 'flowchart LR\n  A["Shop"] --> B["Billing"]\n  subgraph B\n    direction RB\n    B1["Invoice"] --> B2["Ledger"]\n  end'
    _write_attempt(paths, {**RECORD, "diagram": broken})
    harness.check("a flowchart Mermaid cannot draw is FRAME_DIAGRAM_SYNTAX, where it is written",
                  _list_codes(paths) == {"FRAME_DIAGRAM_SYNTAX"}, str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "diagram": '```mermaid\npie\n    title Costs\n    "Build" : 60\n    "Run" : 40\n```'})
    harness.check("a chart drawn as Mermaid (a pie) is refused: `## Diagram` is for a flow, never plotted values",
                  _list_codes(paths) == {"FRAME_DIAGRAM_NOT_MERMAID"}, str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "diagram": '```mermaid\ngantt\n    dateFormat YYYY-MM-DD\n    Build : 2025-01-01, 2025-02-28\n```'})
    harness.check("a gantt is a chart too: bars over time, its days read off a slide of months",
                  _list_codes(paths) == {"FRAME_DIAGRAM_NOT_MERMAID"}, str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "diagram": f"```mermaid\n{DIAGRAM}\n```"})
    harness.check("and so is one inside a mermaid fence", _list_codes(paths) == set(), str(_list_codes(paths)))

    # Knowledge that narrates is NOTED, never rejected: `frames_processing.md` teaches it with good and bad entries, and a second
    # call over a style slip costs minutes per frame (the owner, 2026-09-28: "this is costing us too much").
    def _list_notes(paths) -> list[str]:
        return [note.code for note in item_validator.validate_item(paths, "frame-005").notes]

    _write_attempt(paths, {**RECORD, "facts": ["The slide shows a poll", "The screen displays 89 %"]})
    harness.check("facts that describes the screen does not reject the record", _list_codes(paths) == set(),
                  str(_list_codes(paths)))
    harness.check("it is noted as FRAME_FACTS_DESCRIBE_SCREEN",
                  _list_notes(paths) == ["FRAME_FACTS_DESCRIBE_SCREEN"], str(_list_notes(paths)))
    for wording in (["The diagram shows a flow from the portal to the proxy"], ["The presenter is initiating a login"],
                    ["The user is logging in with an employee account"], ["The presenter has logged into the portal"]):
        _write_attempt(paths, {**RECORD, "facts": wording})
        harness.check(f"{wording[0]!r} is noted, not rejected: it says what is on screen or what the presenter does",
                      _list_codes(paths) == set() and _list_notes(paths) == ["FRAME_FACTS_DESCRIBE_SCREEN"],
                      str(_list_codes(paths)) + str(_list_notes(paths)))
    narration = "The token is pasted into the editor and decoded."
    _write_attempt(paths, {**RECORD, "facts": [narration], "third_person_description": narration})
    harness.check("facts that repeats the narration word for word is noted, not rejected",
                  _list_codes(paths) == set()
                  and _list_notes(paths) == ["FRAME_FACTS_REPEAT_THIRD_PERSON_DESCRIPTION"],
                  str(_list_codes(paths)) + str(_list_notes(paths)))
    _write_attempt(paths, {**RECORD, "facts": ["The user account is created by the service desk"]})
    harness.check("a fact whose subject is a user account is facts", _list_codes(paths) == set(), str(_list_codes(paths)))
    _write_attempt(paths, {**RECORD, "facts": ["The slide deck is kept in Confluence", "89 % want cheaper fuel",
                                                   "The screen shows the poll's source"]})
    harness.check("one screen-describing entry among facts is still facts", _list_codes(paths) == set(),
                  str(_list_codes(paths)))


def test_a_fresh_pass_starts_the_records_file_empty_and_each_accepted_frame_joins_it():
    paths = fx.make_paths()
    frame_records_service.save_frame_record(paths, _build_item(paths, 9), {**RECORD, "frame": 9}, [])
    frame_records_service.clear_all_frame_records(paths)
    harness.check("a fresh pass does not inherit the last run's records",
                  frame_records_service.read_frame_records(paths) == [])
    frame_records_service.save_frame_record(paths, _build_item(paths, 1), {**RECORD, "frame": 1}, ["frame-000"])
    frame_records_service.save_frame_record(paths, _build_item(paths, 0), {**RECORD, "frame": 0}, [])
    slugs = [entry["slug"] for entry in frame_records_service.read_frame_records(paths)]
    harness.check("entries are kept in frame order, whatever order they arrived in",
                  slugs == ["frame-000", "frame-001"], str(slugs))


def test_rewriting_one_frame_changes_only_its_entry_and_names_the_frames_built_on_it():
    paths = fx.make_paths()
    for index, built_from in ((0, []), (1, ["frame-000"]), (2, ["frame-000", "frame-001"])):
        frame_records_service.save_frame_record(paths, _build_item(paths, index), {**RECORD, "frame": index},
                                                  built_from)
    before = frame_records_service.read_frame_records(paths)
    frame_records_service.save_frame_record(paths, _build_item(paths, 1),
                                              {**RECORD, "frame": 1, "screen_kind": "rewritten"}, ["frame-000"])
    after = frame_records_service.read_frame_records(paths)
    harness.check("only entry 1 changed",
                  (after[0], after[2]) == (before[0], before[2]) and after[1]["screen_kind"] == "rewritten", str(after))
    harness.check("the frames built on the rewritten one are named as stale",
                  frame_records_service.list_stale_slugs(paths, "frame-001") == ["frame-002"])


def test_a_frame_is_handed_only_the_records_of_frames_before_it_newest_last():
    paths = fx.make_paths()
    for index in range(4):
        frame_records_service.save_frame_record(paths, _build_item(paths, index), {**RECORD, "frame": index}, [])
    every = frame_records_service.list_previous_records(frame_records_service.read_frame_records(paths), 3, None)
    harness.check("frame 3 sees frames 0 to 2", [entry["index"] for entry in every] == [0, 1, 2], str(every))
    newest = frame_records_service.list_previous_records(frame_records_service.read_frame_records(paths), 3, 2)
    harness.check("a limit keeps the NEWEST whole records", [entry["index"] for entry in newest] == [1, 2], str(newest))


def test_a_diagram_older_than_the_newest_notes_is_still_handed_to_the_frame_to_extend():
    paths = fx.make_paths()
    for index in range(5):
        diagram = DIAGRAM if index == 1 else ""
        frame_records_service.save_frame_record(paths, _build_item(paths, index),
                                                {**RECORD, "frame": index, "diagram": diagram}, [])
    latest = frame_records_service.find_latest_diagram(frame_records_service.read_frame_records(paths), 5)
    harness.check("the newest earlier diagram is found however far back it is",
                  latest is not None and latest["index"] == 1 and latest["diagram"] == DIAGRAM, str(latest))
    newest = frame_records_service.list_previous_records(frame_records_service.read_frame_records(paths), 5, 2)
    text = frames_processing_messages.build_frame_messages(_build_request(paths, previous=newest, latest_diagram=latest))[1]
    text = text.content[0]["text"]
    harness.check("the message carries that diagram in full, to be extended", DIAGRAM in text and "frame 1" in text, text)
    harness.check("and the facts of the newest notes", RECORD["facts"][0] in text, text)
    harness.check("no earlier diagram, no diagram block", "latest diagram" not in frames_processing_messages.build_frame_messages(
        _build_request(paths, previous=newest))[1].content[0]["text"])


def test_a_fact_another_accepted_frame_holds_word_for_word_is_named_and_never_refused_defect():
    # A meeting's frame whose three facts frame 19 also held was refused and blocked phase 3 under zero repair
    # (DEFECTS 702); now the copy is named on the console and the frame passes
    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 5)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4}, [])
    _write_attempt(paths, RECORD)
    harness.check("the copied facts refuse nothing", "FRAME_FACTS_COPIED" not in _list_codes(paths),
                  str(_list_codes(paths)))
    harness.check("and are named on the console", "FRAME_FACTS_COPIED" in _list_note_codes(paths),
                  str(_list_note_codes(paths)))


def test_a_fact_another_accepted_frame_holds_word_for_word_is_named():
    """A frame asked again alone wrote most of the next frame's facts as its own, and a repair may only add, so the
    copies stayed: a fact an accepted frame already holds belongs to that frame."""

    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 5)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4}, [])
    _write_attempt(paths, RECORD)
    harness.check("the facts of accepted frame 4, written again for frame 5, are FRAME_FACTS_COPIED",
                  "FRAME_FACTS_COPIED" in _list_note_codes(paths), str(_list_note_codes(paths)))
    frame_records_service.save_frame_record(paths, items[1], RECORD, [])
    harness.check("a frame already accepted, judged again, is not refused for its old facts",
                  "FRAME_FACTS_COPIED" not in _list_note_codes(paths), str(_list_note_codes(paths)))
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4}, [])
    own = {**RECORD, "facts": ["The refund limit rises to 500 euros for every reseller from next month"]}
    _write_attempt(paths, own)
    harness.check("facts of its own are not", "FRAME_FACTS_COPIED" not in _list_note_codes(paths), str(_list_note_codes(paths)))


def test_a_copied_fact_is_dropped_while_a_fact_of_its_own_remains():
    """A build step repeated its first frame's fact about a box scrolled out of view, and blocked its batch: the copy
    adds nothing, and frame 4 keeps it."""

    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 5)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4}, [])
    own_fact = "The refund limit rises to 500 euros for every reseller from next month"
    mixed = {**RECORD, "facts": [*RECORD["facts"], own_fact]}
    kept = item_validator.drop_copied_facts(paths, "frame-005", mixed)
    harness.check("the copies go and the fact of its own stays", kept["facts"] == [own_fact], str(kept["facts"]))
    harness.check("every fact a copy: left as it is, to be refused",
                  item_validator.drop_copied_facts(paths, "frame-005", RECORD)["facts"] == RECORD["facts"])


def test_a_build_step_outlining_the_whole_slide_again_keeps_only_what_it_adds_defect():
    """A step of a slide built up step by step outlined the whole slide again with its one new bullet, 43 of 192 public
    steps outlining 70% or more of it again, though its message says to outline only what it adds (DEFECTS 434). The
    lines its build's earlier frames hold word for word go, a line over a kept line stays."""

    paths = fx.make_paths()
    items = [{"slug": "frame-004", "index": 4, "timestamp": 4.0, "path": "frames/frame-004.jpg"},
             {"slug": "frame-005", "index": 5, "timestamp": 5.0, "path": "frames/frame-005.jpg", "stepOf": "frame-004"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    first = ('- slide: white background\n  - title "Gateway limits": top left\n  - section "Retries"\n'
             '    - bullet "Three tries per call"\n  - footer "AcmeSync": bottom')
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "frame": 4, "screen": first})
    step = ('- step 2 of the screen of frame 4: none\n' + first.replace('  - footer "AcmeSync": bottom',
            '  - bullet "The client cannot change it": under the section\n  - footer "AcmeSync": bottom'))
    kept = item_validator.drop_build_repeated_lines(paths, "frame-005", {**RECORD, "screen": step})["screen"].splitlines()
    harness.check("the step line, the line over the new bullet and the new bullet stay, the repeats go",
                  kept == ['- step 2 of the screen of frame 4: none', '- slide: white background',
                           '  - bullet "The client cannot change it": under the section'], str(kept))
    harness.check("a frame that is no step is left as it is",
                  item_validator.drop_build_repeated_lines(paths, "frame-004", {**RECORD, "screen": first})["screen"] == first)
    harness.check("a step with no step line is left as it is, never emptied",
                  item_validator.drop_build_repeated_lines(paths, "frame-005", {**RECORD, "screen": first})["screen"] == first)
    harness.check("a step repeating everything keeps its step line alone",
                  item_validator.drop_build_repeated_lines(paths, "frame-005", {**RECORD, "screen": "- step 2 of the screen of frame 4: none\n" + first})["screen"]
                  == "- step 2 of the screen of frame 4: none")


def test_a_build_step_under_the_program_s_step_line_with_places_reworded_keeps_only_what_it_adds_defect():
    """A step under the program's own step line ("- a step of the screen of frame N: the parts below") outlined its
    whole slide again with each part's place written shorter, and the cut kept every line: it knew only "- step " and
    compared whole lines (DEFECTS 479). The step line in both forms counts, and a quoting line is its part up to its
    last quote."""

    paths = fx.make_paths()
    items = [{"slug": "frame-004", "index": 4, "timestamp": 4.0, "path": "frames/frame-004.jpg"},
             {"slug": "frame-005", "index": 5, "timestamp": 5.0, "path": "frames/frame-005.jpg", "stepOf": "frame-004"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    first = ('- slide: white background\n  - title "Gateway limits": top left, large bold font\n'
             '  - bullet "Three tries per call": main bullet')
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "frame": 4, "screen": first})
    step = ('- a step of the screen of frame 4: the parts below\n  - slide: white background\n'
            '    - title "Gateway limits": top left\n    - bullet "Three tries per call"\n'
            '    - bullet "The client cannot change it": new main bullet')
    kept = item_validator.drop_build_repeated_lines(paths, "frame-005", {**RECORD, "screen": step})["screen"].splitlines()
    harness.check("the step line, the slide over the new bullet and the new bullet stay",
                  kept == ['- a step of the screen of frame 4: the parts below', '  - slide: white background',
                           '    - bullet "The client cannot change it": new main bullet'], str(kept))
    highlighted = step.replace('"Three tries per call"', '"Three tries per call": now highlighted red')
    kept = item_validator.drop_build_repeated_lines(paths, "frame-005", {**RECORD, "screen": highlighted})["screen"]
    harness.check("a part the step only marks, highlighted, stays", "now highlighted red" in kept, kept)


def test_a_copy_is_judged_on_the_outline_as_it_is_stored_defect():
    # Defect: a copy excused by a line of the frame's outline was refused once a later step took that line out (froscon
    # 3147 frame 151, FRAME_FACTS_COPIED). Fix: copies are judged last, on the outline as it is stored.
    from unittest import mock

    from services import chain_dispatch_service

    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 5)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    copied = 'The header names the page "Refund helpers for the gateway" above the list.'
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4, "facts": [copied]}, [])
    own_fact = "The refund limit rises to 500 euros for every reseller from next month"
    record = {**RECORD, "facts": [copied, own_fact],
              "screen": '- page: the whole frame\n  - title "Refund helpers for the gateway": top\n'
                        '  - text "name: build and test": below'}
    screens = {"frame-004": ["Refund helpers for the gateway"], "frame-005": ["name: build and test"]}
    request = AttemptRequest(job_kind="frame", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs={"slug": "frame-005"})
    with mock.patch.object(item_validator, "read_screen_lines", side_effect=lambda _paths, slug: screens.get(slug, [])):
        finished = chain_dispatch_service._finish_frame_record(request, "frame-005", record)
    harness.check("the title only the frame before shows goes", "Refund helpers" not in finished["screen"],
                  finished["screen"])
    harness.check("and the fact it excused is dropped as a copy, the frame's own fact kept",
                  finished["facts"] == [own_fact], str(finished["facts"]))


def test_a_fact_of_the_same_screen_shown_again_is_no_copy():
    """A slide shown again was left unmarked by phase 2 (its screen box moved by a pixel); the frame restated the
    slide's facts, was refused as copying them, and could state nothing else its screen showed (lecture-1 frames 134
    and 144, 2026-10-14). An outline quoting the same texts as the earlier frame's shows that frame's screen."""

    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 9)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    fact = "Naming each refund step lets the Gateway team reuse it and read the flow later."
    slide = ('- slide: white, the whole frame\n  - title "REFUND STEPS"\n  - bullet "Name each step"\n'
             '  - bullet "Reuse names, not values"\n  - bullet "Read it next year"')
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4, "facts": [fact], "screen": slide}, [])
    _write_attempt(paths, {**RECORD, "frame": 9, "facts": [fact], "screen": slide + "\n  - footer \"Lecture 1\""})
    harness.check("the slide's fact on the same slide shown again is not refused as copied",
                  "FRAME_FACTS_COPIED" not in _list_note_codes(paths), str(_list_note_codes(paths)))
    other = ('- slide: white, the whole frame\n  - title "REFUND LIMITS"\n  - bullet "500 euros"\n'
             '  - bullet "Per reseller"\n  - bullet "From next month"')
    _write_attempt(paths, {**RECORD, "frame": 9, "facts": [fact], "screen": other})
    harness.check("the same fact on another slide is still named", "FRAME_FACTS_COPIED" in _list_note_codes(paths),
                  str(_list_note_codes(paths)))


def test_a_line_copied_from_the_instructions_sample_answer_is_refused():
    """A frame's notes carried the sample answer's invented question and answer, about a person never in the meeting."""

    paths = fx.make_paths()
    copied = {**RECORD, "facts": [*RECORD.get("facts", []),
                                  "Jonas decides each client's retry budget; clients cannot change it themselves."]}
    _write_attempt(paths, copied)
    harness.check("the copied example line is FRAME_EXAMPLE_COPIED", "FRAME_EXAMPLE_COPIED" in _list_codes(paths),
                  str(_list_codes(paths)))
    placeholder = sorted(instruction_example_utils.list_placeholders())[0]
    prefixed = {**RECORD, "speech_visual_alignment": f"<{placeholder}> The presenter says the export runs nightly."}
    _write_attempt(paths, prefixed)
    harness.check("a placeholder of the instructions written in front of the worker's own sentence is refused too",
                  "FRAME_EXAMPLE_COPIED" in _list_codes(paths), str(_list_codes(paths)))
    _write_attempt(paths, RECORD)
    harness.check("a record of its own is not", "FRAME_EXAMPLE_COPIED" not in _list_codes(paths), str(_list_codes(paths)))
    harness.check("the sample answer itself holds no placeholder to copy",
                  not any(instruction_example_utils.find_placeholder(line) for line in instruction_example_utils.list_example_lines()))


def test_a_fact_this_frame_s_own_screen_shows_is_its_own_though_an_earlier_frame_holds_it():
    """A slide's answers were said over one frame and the console printed them on the next; the next frame's fact,
    word for word the first's, was refused as copied though its own screen shows it (lecture-1 frames 105 and 107,
    2026-10-13). A fact whose every quoted text this frame's outline holds is this frame's own."""

    paths = fx.make_paths()
    items = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": float(index), "path": f"frames/frame-{index:03d}.jpg"}
             for index in (4, 5)]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    fact = "The expression `retry(4*3)` on the Gateway evaluates to `int`."
    frame_records_service.save_frame_record(paths, items[0], {**RECORD, "frame": 4, "facts": [fact]}, [])
    shown = {**RECORD, "facts": [fact], "screen": '- console: right half\n  - input "In [12]: retry(4*3)"\n  - output "Out[12]: int"'}
    _write_attempt(paths, shown)
    harness.check("a fact the frame's own console shows is not refused as copied",
                  "FRAME_FACTS_COPIED" not in _list_note_codes(paths), str(_list_note_codes(paths)))
    _write_attempt(paths, {**shown, "screen": '- console: right half\n  - prompt "In [13]:": waiting'})
    harness.check("the same fact on a frame that does not show it is still refused",
                  "FRAME_FACTS_COPIED" in _list_note_codes(paths), str(_list_note_codes(paths)))


def test_a_note_reaches_the_attempt_s_validation_record():
    """A warning is kept in the report beside the findings; the attempt's `validation.json`, written from `as_dict`,
    held none of them."""

    report = ValidationReport(subject="frame-000")
    report.note("FRAME_SCREENSHOT_UNEXPLAINED", "the screenshot is not explained")
    harness.check("the record holds the note", [note["code"] for note in report.as_dict().get("notes", [])]
                  == ["FRAME_SCREENSHOT_UNEXPLAINED"], str(report.as_dict()))
