"""Every frame's notes carry the screen as an outline under `## Screen`. REQUIREMENTS.md R17.

One part of the screen per line, where it sits and how it looks, its text quoted, a part inside another indented under
it: like HTML in words (the owner, 2026-09-28). It replaced a flat list of quotes that never said what a text was or
where it stood, and a prose description that repeated the same quotes. Checked by the program for shape (there,
an outline, more than the screen itself) and for the frame's text, read by Tesseract and looked for among its quotes, taught by `frames_processing.md` with good and bad answers, kept on the frame's moment by
the merge and printed whole in `03_notes.md`. Whether a designer could really redraw the screen from it is read,
never checked.
"""

from pathlib import Path

import cv2
import numpy

from support import fixtures as fx
from support import harness

from models import output_contract
from models.validation_report import ValidationReport
from prompts import frames_processing_messages
from services import call_log_service
from services import phase_file_service
from services import record_file_service
from services import text_detection_service
from utils import frame_markdown_utils
from utils import frame_merge_utils
from utils import meeting_notes_utils
from validators import item_validator

from cases import test_frame_third_person_description as narration

FRAMES_PROCESSING_INSTRUCTION = Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "frames-processing.md"
SCREEN = """- slide: white, the whole frame
  - heading "Order intake": across the top, dark blue bold
  - box "Shop": left of three grey rounded boxes in a row
    - arrow to "Order Service": pointing right, labelled "1 order"
  - box "Order Service": middle of the row"""
RECORD = {**narration.RECORD, "screen": SCREEN}


def _list_codes(paths,
                record: dict) -> set[str]:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frame-005"),
                                   {"response": {**narration.ANSWERED, "parsed": record}})
    record_file_service.write_frame_record(paths, "frame-005", record)

    return harness.codes(item_validator.validate_item(paths, "frame-005"))


def test_the_instruction_states_the_test_and_shows_good_and_bad_outlines():
    instruction = " ".join(FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8").split())
    harness.check("frames_processing.md shows the section", "## Screen" in instruction)
    harness.check("frames_processing.md gives the 80% reconstruction test", "at least 80% of it right" in instruction)
    harness.check("frames_processing.md says a part is one line with its text quoted, and nesting is containment",
                  "One part per line" in instruction and "indented two spaces under it" in instruction)
    harness.check("frames_processing.md keeps window chrome to one line", "Window chrome is ONE line" in instruction)
    harness.check("frames_processing.md shows a good answer and a bad one",
                  "Good (invented), a slide" in instruction and "bare quotes say nothing" in instruction)
    harness.check("frames_processing.md says a screenshot marked needed never replaces the diagram",
                  "A screenshot marked `needed` never replaces the diagram" in instruction, instruction[:0])


def test_an_outline_that_points_to_another_frame_is_refused():
    paths = fx.make_paths()
    for pointer in ('- slide: same as frame 5\n  - box "Retries": centre, blue',
                    '- slide: as in frame 9\n  - image: a cat, right',
                    '- slide: like frames 13 plus an arrow\n  - arrow: to "Billing"',
                    '- editor: identical to frame 7 with line 4 highlighted\n  - prompt "In [9]:": bottom',
                    '- console: unchanged from frame 7\n  - output "Out[3]: 6": bottom'):
        harness.check(f"{pointer.splitlines()[0]!r} points elsewhere",
                      "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(paths, {**RECORD, "screen": pointer}),
                      str(_list_codes(paths, {**RECORD, "screen": pointer})))
    quoted = '- slide: white\n  - title "Same as frame 5, but faster": top left'
    harness.check("a slide that writes the words itself is no pointer",
                  "FRAME_SCREEN_POINTS_ELSEWHERE" not in _list_codes(paths, {**RECORD, "screen": quoted}),
                  str(_list_codes(paths, {**RECORD, "screen": quoted})))
    harness.check("the batch's closing message says it too, last before the answer",
                  "never \"same as frame N\"" in frames_processing_messages.GROUP_CLOSING_MESSAGE.template)
    instruction = " ".join(FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8").split())
    harness.check("frames_processing.md tells the worker never to point to any frame its message does not name, and "
                  "no longer forbids the pointer a screen shown again may write",
                  "Never point to any other frame" in instruction and "even for a screen shown again" not in instruction)
    harness.check("a screen shown again is offered the one pointer the program fills in, as the check accepts, and "
                  "never told both: told both, the worker pointed at the wrong frames",
                  "may be the one line" in frames_processing_messages.RETURN_NOTE.template
                  and "never points to the earlier frame" not in frames_processing_messages.RETURN_NOTE.template)


def test_a_step_of_a_built_slide_outlines_only_what_it_adds():
    paths = fx.make_paths()
    for step in ('- step 2 of the slide of frame 13: adds a picture of a cat over the "Gateway" box',
                 '- a step of the slide of frame 13: adds an arrow\n  - arrow: from "Gateway" to "Billing"',
                 '- step 2 of the slide of frame 13: adds a label\n  - label "Retry": right\n- rest of screen same as frame 13'):
        codes = _list_codes(paths, {**RECORD, "screen": step})
        harness.check(f"{step.splitlines()[0][:40]!r}: a step is neither too thin nor a pointer",
                      not codes & {"FRAME_SCREEN_TOO_THIN", "FRAME_SCREEN_POINTS_ELSEWHERE"}, str(codes))


def test_a_step_may_point_to_its_own_build_in_its_own_words_and_nowhere_else():
    paths = fx.make_paths()
    items = [{"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4, "stepOf": "frame-003"},
             {"slug": "frame-005", "index": 5, "stepOf": "frame-003"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    own = '- slide: same as frame 3\n  - box "Remark": bottom, blue'
    codes = _list_codes(paths, {**RECORD, "screen": own, "diagram": "Same as frame 3"})
    harness.check("a step pointing to its build's first frame, in its own words and in its diagram, passes",
                  not codes & {"FRAME_SCREEN_POINTS_ELSEWHERE", "FRAME_DIAGRAM_NOT_MERMAID"}, str(codes))
    harness.check("one line is enough for a step", "FRAME_SCREEN_TOO_THIN" not in _list_codes(
        paths, {**RECORD, "screen": "- slide: same as frame 4, with an arrow added"}))
    quoted = '- slide: same as frame 3\n' + "".join(f'  - line "{word} text": left\n' for word in "abcdefghij")
    harness.check("a step with nothing new to state may leave its facts empty, however much text it shows",
                  "FRAME_FACTS_MISSING" not in _list_codes(paths, {**RECORD, "screen": quoted, "facts": []}),
                  str(_list_codes(paths, {**RECORD, "screen": quoted, "facts": []})))
    elsewhere = '- slide: same as frame 1\n  - box "Remark": bottom, blue'
    harness.check("a step pointing outside its build is still refused",
                  "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(paths, {**RECORD, "screen": elsewhere}))


def test_text_an_earlier_frame_showed_but_did_not_quote_is_no_new_text_defect():
    # A terminal scrolled back to what earlier frames showed; their outlines had left a tips panel out, so its words read
    # as new and the frame was refused for empty facts (Lecture 7 frame 21). An earlier frame's read lines count too
    paths = fx.make_paths()
    said = "It looks like it did before I edited anything, because the agent is set up this way."
    items = [{"slug": "frame-004", "index": 4}, {"slug": "frame-005", "index": 5, "transcriptCurrent": said}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "screen": '- terminal: the whole frame\n'
                                                                           '  - line "refund gateway ready"'})
    panel = ["Tips for getting started", "Run setup to create a config file"]
    shown = ('- terminal: the whole frame\n  - line "refund gateway ready"\n'
             + "".join(f'  - line "{line}"\n' for line in panel))
    saved = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: panel if slug == "frame-004" else []
    try:
        codes = _list_codes(paths, {**RECORD, "screen": shown, "facts": []})
    finally:
        item_validator.read_screen_lines = saved
    harness.check("a frame showing only what an earlier frame showed may leave its facts empty",
                  "FRAME_FACTS_MISSING" not in codes, str(codes))


def test_text_a_frame_showed_more_than_twelve_frames_before_is_no_new_text_defect():
    # A lecture went back to its terminal after a dozen blackboard frames; the terminal's words read as new and the
    # frame was refused for empty facts (Lecture 7 frame 48). Every earlier frame's outline counts
    paths = fx.make_paths()
    said = "So here I have booted up my agent harness again and we can look at what it does next."
    items = [{"slug": "frame-001", "index": 1}, *({"slug": f"frame-{n:03d}", "index": n} for n in range(2, 20)),
             {"slug": "frame-020", "index": 20, "transcriptCurrent": said}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    terminal = ('- terminal: the whole frame\n  - line "refund gateway ready"\n  - line "ledger closing tonight"\n'
                '  - line "partner payouts queued"')
    record_file_service.write_frame_record(paths, "frame-001", {**RECORD, "screen": terminal})
    saved = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: []
    try:
        call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frame-020"),
                                        {"response": {**narration.ANSWERED, "parsed": {**RECORD, "screen": terminal,
                                                                                      "facts": []}}})
        record_file_service.write_frame_record(paths, "frame-020", {**RECORD, "screen": terminal, "facts": []})
        codes = harness.codes(item_validator.validate_item(paths, "frame-020"))
    finally:
        item_validator.read_screen_lines = saved
    harness.check("a terminal shown nineteen frames before may come back with no facts",
                  "FRAME_FACTS_MISSING" not in codes, str(codes))


def test_a_step_may_point_to_the_screen_its_build_s_first_frame_repeats_defect():
    # Defect: a build started on a screen shown again (frame 3, a repeat of frame 1); its step wrote "same as frame 1",
    # and the run blocked. Fix: the frames a build's frames repeat are the build's screen too, so a step may point there.
    paths = fx.make_paths()
    items = [{"slug": "frame-001", "index": 1}, {"slug": "frame-003", "index": 3, "repeatOf": "frame-001"},
             {"slug": "frame-005", "index": 5, "stepOf": "frame-003"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    codes = _list_codes(paths, {**RECORD, "screen": '- slide: same as frame 1\n  - box "Remark": bottom, blue'})
    harness.check("a step pointing to the screen its build's first frame repeats passes",
                  "FRAME_SCREEN_POINTS_ELSEWHERE" not in codes, str(codes))
    harness.check("and a step pointing outside both is still refused", "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(
        paths, {**RECORD, "screen": '- slide: same as frame 2\n  - box "Remark": bottom, blue'}))


def test_a_part_listing_its_contents_with_a_pointer_beside_it_is_no_pointer_defect():
    # Defect: an outline wrote "diagram: (same as Frame 22)" above every label of the diagram, quoted, and two frames
    # were blocked for pointing. Fix: a part whose contents are listed under it loses the pointer; its contents stand.
    paths = fx.make_paths()
    outline = ('- slide: white, the whole frame\n  - title "Inducing electrons to emit light": top\n'
               '  - diagram: (same as Frame 2)\n    - label "Electron bunches": left\n    - label "X-ray beam": right\n'
               '  - legend: (same as Frame 3, with the value "27 000")\n    - value "27 000": right')
    codes = _list_codes(paths, {**RECORD, "screen": outline})
    harness.check("the outline passes: no pointer is left where the contents stand", "FRAME_SCREEN_POINTS_ELSEWHERE" not in codes,
                  str(codes))
    alone = '- slide: white, the whole frame\n  - title "Inducing electrons to emit light": top\n  - diagram: (same as Frame 2)'
    harness.check("a part that is only the pointer is still refused", "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(
        paths, {**RECORD, "screen": alone}))


def test_a_part_closed_by_a_clause_naming_another_frame_keeps_what_it_said_defect():
    # Defect: a part read 'diagram "Undulator": centre, identical to frame 23', the run before had refused "(same as
    # frame 22)", and the frame was blocked again. Fix: a closing clause naming another frame goes when the part said what it is.
    paths = fx.make_paths()
    outline = ('- slide: white, the whole frame\n  - title "Inducing electrons to emit light": top\n'
               '  - diagram "Undulator": centre, identical to frame 2\n  - legend: bottom right; as on frame 3\n'
               '  - text "See Frame 3, the setup": left')
    codes = _list_codes(paths, {**RECORD, "screen": outline})
    harness.check("the outline passes, the frame's own text kept", "FRAME_SCREEN_POINTS_ELSEWHERE" not in codes, str(codes))
    harness.check("a part that says nothing but the pointer is still refused", "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(
        paths, {**RECORD, "screen": '- slide: white, the whole frame\n  - title "Inducing electrons": top\n  - footer: identical to frame 2'}))


def test_each_screen_breach_fires_its_own_code():
    paths = fx.make_paths()
    harness.check("a good outline passes", _list_codes(paths, RECORD) == set(), str(_list_codes(paths, RECORD)))
    harness.check("an empty one is FRAME_SCREEN_MISSING",
                  _list_codes(paths, {**RECORD, "screen": ""}) == {"FRAME_SCREEN_MISSING"})
    harness.check("the screen alone is FRAME_SCREEN_TOO_THIN",
                  _list_codes(paths, {**RECORD, "screen": "- slide: white"}) == {"FRAME_SCREEN_TOO_THIN"})
    for empty in ("- black screen: the whole frame, nothing shown", "- video feed: one participant, dark background"):
        harness.check(f"{empty!r}: a screen with nothing to read is outlined whole in one line, facts from the speech or not",
                      "FRAME_SCREEN_TOO_THIN" not in _list_codes(paths, {**RECORD, "screen": empty}),
                      str(_list_codes(paths, {**RECORD, "screen": empty})))
    harness.check("the screen and one part passes",
                  _list_codes(paths, {**RECORD, "screen": '- slide: white\n  - heading "Refunds": top'}) == set())
    too_deep = '- slide: white\n      - heading "Refunds": top'
    harness.check("a part indented past the part it is in is FRAME_SCREEN_NOT_AN_OUTLINE",
                  _list_codes(paths, {**RECORD, "screen": too_deep}) == {"FRAME_SCREEN_NOT_AN_OUTLINE"})
    harness.check("and the finding names the line",
                  any("Refunds" in finding.detail for finding in item_validator.validate_item(paths, "frame-005").findings))


def test_three_quoted_texts_with_no_facts_are_refused_and_two_pass():
    paths = fx.make_paths()
    three = '- page: white\n  - title "Partner Portal": top\n  - box "Gateway": left\n  - box "LDAP": right'
    harness.check("three quoted texts and no facts is FRAME_FACTS_MISSING",
                  "FRAME_FACTS_MISSING" in _list_codes(paths, {**RECORD, "facts": [], "screen": three}))
    two = '- page: white\n  - title "Partner Portal": top\n  - box "Gateway": left'
    harness.check("two are a title card, and pass", "FRAME_FACTS_MISSING"
                  not in _list_codes(paths, {**RECORD, "facts": [], "screen": two}))


def test_a_screen_the_earlier_notes_already_outline_needs_no_fact():
    """A file shown again beside a changing terminal is no repeat to phase 2; a frame of it, six words said over it,
    was refused for writing no fact, though none of its 45 quoted words was new (Lecture 9, frame 113)."""

    paths = fx.make_paths()
    said = "So here is the portal again, and we will look at how the gateway reaches the directory."
    items = [{"slug": "frame-004", "index": 4}, {"slug": "frame-005", "index": 5, "transcriptCurrent": said}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    three = '- page: white\n  - title "Partner Portal": top\n  - box "Gateway": left\n  - box "LDAP": right'
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "screen": three})
    harness.check("the same text again, no fact, passes",
                  "FRAME_FACTS_MISSING" not in _list_codes(paths, {**RECORD, "facts": [], "screen": three}))
    other = three + '\n  - box "Retry budget per tenant owner": bottom'
    harness.check("five new words ask for a fact again",
                  "FRAME_FACTS_MISSING" in _list_codes(paths, {**RECORD, "facts": [], "screen": other}))


def test_the_merge_keeps_it_on_the_frame_and_the_notes_print_it_whole():
    merged = frame_merge_utils.merge_frame_records(
        {"segments": []},
        {"items": [{"slug": "frame-005", "index": 5, "timestamp": 8.0}]},
        [{**RECORD, "slug": "frame-005", "index": 5, "timestamp": 8.0}], {})
    harness.check("the frame's moment carries the outline", merged["frames"][0].get("screen") == SCREEN,
                  str(merged["frames"][0]))
    harness.check("and no quote of it becomes an entry of its own",
                  all(entry["kind"] != "on_screen" for entry in merged["entries"]), str(merged["entries"]))
    notes = meeting_notes_utils.render_meeting_notes("clip.mp4", merged)
    harness.check("03_notes.md prints it whole under `The screen:`", "The screen:\n" + SCREEN in notes, notes)
    heading = next((line for line in notes.splitlines() if line.startswith("## F5")), "")
    harness.check("the frame's heading carries the part named a heading", '"Order intake"' in heading, heading)
    older = frame_merge_utils.merge_frame_records(
        {"segments": []},
        {"items": [{"slug": "frame-005", "index": 5, "timestamp": 8.0}]},
        [{name: value for name, value in {**RECORD, "slug": "frame-005", "index": 5, "timestamp": 8.0}.items()
          if name != "screen"}], {})
    harness.check("a record with no outline reads as empty", older["frames"][0].get("screen") == "")


SLIDE_LINES = ("Order intake overview", "Every order reaches billing within one hour", "Refunds are split into three parts",
               "Express orders retry five times", "The books close every night")


def _write_slide(paths) -> None:
    image = numpy.full((720, 1280, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(SLIDE_LINES):
        cv2.putText(image, line, (40, 110 + row * 120), cv2.FONT_HERSHEY_DUPLEX, 1.4, (0, 0, 0), 2, cv2.LINE_AA)
    (paths.output_root / "frames").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-005.jpg"), image)
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"],
                                        {"items": [{"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg"}]})


def test_an_outline_that_sums_up_the_frame_s_text_is_refused_and_told_which_lines():
    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    paths = fx.make_paths()
    _write_slide(paths)
    whole = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in SLIDE_LINES)
    harness.check("an outline quoting every line passes",
                  "FRAME_SCREEN_TEXT_MISSING" not in _list_codes(paths, {**RECORD, "screen": whole}),
                  str(_list_codes(paths, {**RECORD, "screen": whole})))
    summed = '- slide: white, the whole frame\n  - title "Order intake overview": top\n  - bullet points about billing and refunds: below'
    harness.check("an outline summing the lines up is FRAME_SCREEN_TEXT_MISSING",
                  "FRAME_SCREEN_TEXT_MISSING" in _list_codes(paths, {**RECORD, "screen": summed}))
    detail = next((finding.detail for finding in item_validator.validate_item(paths, "frame-005").findings
                   if finding.code == "FRAME_SCREEN_TEXT_MISSING"), "")
    harness.check("and the finding names a line it left out", "Refunds" in detail or "books" in detail, detail)


def test_an_outline_cut_short_or_summed_up_gets_the_lines_it_left_out_from_the_reader():
    """The worker cut a board's list short (7 of 13 rows) at every batch size and when asked again with its refusal;
    the program reads every line, so it adds the ones left out, marked as its reading (daily, five blocks)."""

    if not text_detection_service.is_available():
        harness.check("the text reader is installed, or this check cannot run here", False, "tesseract not found")
        return
    paths = fx.make_paths()
    _write_slide(paths)
    short = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in SLIDE_LINES[:3])
    completed = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": short})
    harness.check("the lines left out are added under the program's part, each quoted",
                  item_validator.SCREEN_COMPLETION_PART in completed["screen"]
                  and all(word in completed["screen"] for word in ("Express", "books")), completed["screen"][-300:])
    harness.check("and the completed outline passes the check",
                  "FRAME_SCREEN_TEXT_MISSING" not in _list_codes(paths, completed), str(_list_codes(paths, completed)))
    whole = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in SLIDE_LINES)
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "frame": 4, "screen": whole})
    beside = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": short})
    harness.check("a neighbour quoting the lines changes nothing: the frame's own outline is completed (a neighbour "
                  "refused later loses its record)", item_validator.SCREEN_COMPLETION_PART in beside["screen"],
                  beside["screen"][-200:])
    summed = '- slide: white, the whole frame\n  - title "Order intake overview": top\n  - bullet points: below'
    harness.check("an outline summing the screen up is completed too, every line it leaves out added",
                  item_validator.SCREEN_COMPLETION_PART
                  in item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": summed})["screen"])
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-004", "index": 4, "path": "frames/frame-005.jpg"},
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg", "stepOf": "frame-004"}]})
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "frame": 4, "screen": short})
    step = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": "- step 2 of the screen of frame 4: a line added"})
    harness.check("a build step, outlining only what it adds, is completed against its build's outlines too",
                  item_validator.SCREEN_COMPLETION_PART in step["screen"] and "books" in step["screen"], step["screen"][-200:])


def test_an_editors_line_number_margin_is_taken_off_the_lines_the_reader_finds():
    """The reader read an editor's code with its gutter ("27 | raise ..."); the program added that line to the outline,
    and a critique called it not on the screen. A number with no margin bar, a slide's "3 steps", stays."""

    reading = [item_validator.ScreenTextItem("line", ["27 | raise ValueError('divide by zero')", "3 steps to deploy"])]
    original = item_validator.list_screen_text_items
    item_validator.list_screen_text_items = lambda paths, slug: reading
    try:
        lines = item_validator.read_screen_lines(fx.make_paths(), "frame-005")
    finally:
        item_validator.list_screen_text_items = original
    harness.check("the gutter goes, a plain leading number stays",
                  lines == ["raise ValueError('divide by zero')", "3 steps to deploy"], str(lines))


def _read_word(text: str,
               line: int,
               top: int) -> dict:
    return {"text": text, "line": line, "box": [40, top, 200, top + 20], "block": 1}


def test_the_header_and_footer_bands_are_form_and_never_checked():
    """A slide's section label and logo at the top and its date, company and page number at the bottom are form; the
    owner: a miss of form never stops the run. The text reader is replaced, so the bands alone decide."""

    paths = fx.make_paths()
    _write_slide(paths)
    words = [_read_word("Section label", 1, 20), _read_word("Order intake overview", 2, 100),
             _read_word("Refunds are split", 3, 320), _read_word("Company footer name", 4, 690),
             _read_word("Confidential page two", 5, 700)]
    words = [{**word, "text": part} for word in words for part in word["text"].split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        body = '- slide: white, the whole frame\n  - title "Order intake overview": top\n  - line "Refunds are split": left'
        codes = _list_codes(paths, {**RECORD, "screen": body})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the body quoted and the header and footer not: no refusal", "FRAME_SCREEN_TEXT_MISSING" not in codes,
                  str(codes))


def test_a_quote_mark_inside_a_quoted_text_never_shifts_the_lines_after_it():
    """A bullet quoting „a phrase" (German opening mark, straight closing one) made the program pair the outline's
    quote marks wrongly, and every bullet after it read as unquoted: two right frames were refused for it."""

    paths = fx.make_paths()
    _write_slide(paths)
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(["What fits on paper", "Express orders retry five times",
                                            "The books close every night"]) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        body = ('- slide: white, the whole frame\n  - bullet "\u201eWhat fits on paper" counts": left\n'
                '  - bullet "Express orders retry five times": left\n  - bullet "The books close every night": left')
        codes = _list_codes(paths, {**RECORD, "screen": body})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("every bullet quoted, one holding a German opening mark: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))


def test_text_quoted_in_backticks_counts_as_quoted():
    """A slide's legend (`XY = Extra yield`) was outlined in backticks, exact, and its lines counted as unquoted."""

    paths = fx.make_paths()
    _write_slide(paths)
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(["XY Extra yield", "Express orders retry five times", "QA Quality assurance",
                                            "OPS Operations team", "DEV Development team"]) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        body = ('- slide: white, the whole frame\n  - legend: bottom left\n    - `XY = Extra yield`\n'
                '    - `QA = Quality assurance`\n    - `OPS = Operations team`\n    - `DEV = Development team`\n'
                '  - bullet "Express orders retry five times": left')
        codes = _list_codes(paths, {**RECORD, "screen": body})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("legend lines in backticks and a bullet in double quotes: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))


def test_a_word_the_reader_cut_at_a_box_edge_counts_when_it_begins_a_quoted_word():
    """Two boxes side by side read as one line, the first word cut where its box ends ("Valid" for "Validator")."""

    paths = fx.make_paths()
    _write_slide(paths)
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(["Order Valid Shippi", "Retry Sched Billi", "Books Archi Repor",
                                            "Express orders retry five times"]) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        body = ('- slide: white, the whole frame\n  - box "Order Validator": left\n  - box "Shipping API": right\n'
                '  - box "Retry Scheduler": left\n  - box "Billing API": right\n  - box "Books Archive": left\n'
                '  - box "Reporting API": right\n  - bullet "Express orders retry five times": left')
        codes = _list_codes(paths, {**RECORD, "screen": body})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("every box quoted whole; the reader's cut words begin them: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))


def test_a_three_letter_word_the_reader_misread_by_one_letter_counts():
    """The reader reads a digit or a small letter wrong in a short label ("Q4X" as "qax", "Neu" as "new"); a
    one-letter slip in three letters is 67% alike, under the 75% the longer words need."""

    paths = fx.make_paths()
    _write_slide(paths)
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(["Exports qax search", "new Sort", "Arc Toolbar", "Lim Panel",
                                            "Express orders retry five times"]) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        body = ('- window: white, the whole frame\n  - address "Exports > Q4X": top\n  - search box "Q4X search": top right\n'
                '  - menu "Neu", "Sort": top\n  - tab "Arx", "Toolbar": top\n  - tab "Lum", "Panel": left\n'
                '  - bullet "Express orders retry five times": left')
        codes = _list_codes(paths, {**RECORD, "screen": body})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("short labels misread by one letter count as quoted: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))


def test_the_browser_developer_tools_own_labels_are_chrome_never_counted():
    """A page beside the browser's developer tools: the tools' own controls ("Disable cache", the request tabs, the
    console's tips) are the browser's chrome, as a meeting tool's participant list is; the page and the requests
    the speaker shows are still counted."""

    paths = fx.make_paths()
    _write_slide(paths)
    texts = ["Preserve log Disable cache", "Headers Cookies Payload Initiator Timing", "More filters Invert",
             "Explain console errors using Copilot", "Transfer summary confirmed", "Release with photo code",
             "Execution within seconds today"]
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(texts) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        quoted = ('- page: left half\n  - title "Transfer summary confirmed": top\n  - section "Release with photo code"\n'
                  '  - note "Execution within seconds today"\n- developer tools: right half, the network tab open')
        summed = '- page: left half, a confirmation page\n- developer tools: right half'
        lines = item_validator.read_screen_lines(paths, "frame-005")
        kept = _list_codes(paths, {**RECORD, "screen": quoted})
        summary = _list_codes(paths, {**RECORD, "screen": summed})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the tools' own labels are not lines to quote", lines == texts[4:], str(lines))
    harness.check("the page quoted, the tools as one line: no refusal", "FRAME_SCREEN_TEXT_MISSING" not in kept, str(kept))
    harness.check("the page summed up is still refused", "FRAME_SCREEN_TEXT_MISSING" in summary, str(summary))


def test_a_line_that_is_only_an_address_is_listed_to_quote_defect():
    # Defect: "@jonas@social.acme.example" counted its dotted host as code members and a log name, so it was never
    # listed, and the worker misread it unchecked. Fix: a line that is only addresses is no code and no log
    paths = fx.make_paths()
    _write_slide(paths)
    texts = ["@jonas@social.acme.example", "mail@acmesync.example", "config.app.name = 3"]
    words = _read_lines([(line, 150 + number * 60) for number, line in enumerate(texts)])
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the handle and the email are listed, the code line is not", lines == texts[:2], str(lines))


def _read_lines(lines: list[tuple[str, int]]) -> list[dict]:
    """The text reader's words of each line, at its top edge."""

    return [{"text": part, "line": number, "box": [40, top, 200, top + 20], "block": number}
            for number, (line, top) in enumerate(lines) for part in line.split()]


def test_a_top_line_standing_on_the_frame_before_is_chrome_and_never_counted():
    """A browser's bookmarks and a document program's ribbon were each read as one line of a few words, so no row of
    short entries matched, and three frames were refused for not quoting them. A line of the top fifth standing at the
    same height on a nearby frame is chrome; one found on this frame alone is still counted."""

    paths = fx.make_paths()
    _write_slide(paths)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-004.jpg"), numpy.full((720, 1280, 3), 255, dtype=numpy.uint8))
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-004", "index": 4, "path": "frames/frame-004.jpg"},
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg"}]})
    chrome = [("Team wiki Release board", 70), ("Insert Design Review Comments", 95), ("Present Record Share", 125)]
    body = ["Refunds are split nightly", "Express orders retry five times", "The books close every night"]

    words_by_frame = {"frame-004.jpg": _read_lines([*chrome, ("Ledger columns are frozen", 300)]),
                      "frame-005.jpg": _read_lines([*chrome, *[(line, 300 + row * 80) for row, line in enumerate(body)]])}
    outline = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in body)
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words_by_frame[Path(path).name]
    text_detection_service.is_available = lambda: True
    try:
        codes = _list_codes(paths, {**RECORD, "screen": outline})
        words_by_frame["frame-005.jpg"] = _read_lines([*chrome, ("Quarterly close checklist owner", 110),
                                                *[(line, 300 + row * 80) for row, line in enumerate(body)]])
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("three top lines standing on the frame before are chrome: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))
    harness.check("a top line on this frame alone is still counted, the standing ones are not",
                  "Quarterly close checklist owner" in lines and not any("Team wiki" in line for line in lines), str(lines))


def test_a_screen_the_frame_before_quoted_whole_may_be_outlined_short():
    """The same terminal screen shown again a moment later, outlined short: every line is in the frame before's notes,
    so no knowledge is lost and the owner's bar lets it pass. The text reader is replaced, so the quotes alone decide."""

    paths = fx.make_paths()
    _write_slide(paths)
    lines = ["Unauthorized access prohibited", "Express orders retry five times", "The books close every night"]
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(lines) for part in line.split()]
    record_file_service.write_frame_record(paths, "frame-004", {
        **RECORD, "frame": 4, "screen": "- terminal: grey, the whole frame\n" + "".join(f'  - line "{line}": left\n'
                                                                                    for line in lines)})
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        short = '- terminal: grey, the whole frame\n  - box with the access warning and the session details: centre'
        codes = _list_codes(paths, {**RECORD, "screen": short})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("every line quoted by the frame before: no refusal", "FRAME_SCREEN_TEXT_MISSING" not in codes, str(codes))


def test_rows_of_a_data_table_may_be_given_as_a_count():
    """frames_processing.md lets a table's rows be counted, with the first and the last named: a row of dates and codes left
    unquoted is no refusal, a sentence left unquoted still is. The text reader is replaced."""

    paths = fx.make_paths()
    _write_slide(paths)
    lines = ["Orders waiting for approval", "WI 05.08.2024 AB12C3 40 NEU", "WI 06.08.2024 DE45F6 40 NEU",
             "WI 07.08.2024 GH78J9 40 NEU", "ACME HOLDING SE", "GLOBEX CORP INTL", "WI 08.08.2024 KLMNOP 40 NEU N",
             '<fld name="Holder">in Shares</fld>', '<fld name="Amount">Dividend value</fld>',
             "Doe, Jordan (extern)", "Roe, Avery", "Miller Casey (extern)"]
    words = [{"text": part, "line": number, "box": [40, 100 + number * 40, 200, 120 + number * 40], "block": 1}
             for number, line in enumerate(lines) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        counted = ('- terminal: grey, the whole frame\n  - heading "Orders waiting for approval": top\n'
                   '  - table of 6 columns and 3 rows: first "WI 05.08.2024", last "WI 07.08.2024"')
        codes = _list_codes(paths, {**RECORD, "screen": counted})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the heading quoted and the data rows counted: no refusal", "FRAME_SCREEN_TEXT_MISSING" not in codes,
                  str(codes))


def test_a_screen_shown_again_with_nothing_new_may_have_no_facts():
    """A demo's closing frame showed an earlier screen again and wrote no facts, rightly: its text is in the earlier
    showing's facts (2026-10-03). A frame shown for the first time still must state what it teaches."""

    paths = fx.make_paths()
    three = '- page: white\n  - title "Partner Portal": top\n  - box "Gateway": left\n  - box "LDAP": right'
    items = [{"slug": "frame-003", "index": 3}, {"slug": "frame-005", "index": 5, "repeatOf": "frame-003"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    harness.check("a repeat with no facts passes", "FRAME_FACTS_MISSING"
                  not in _list_codes(paths, {**RECORD, "facts": [], "screen": three}))
    harness.check("the same notes on a frame shown for the first time are FRAME_FACTS_MISSING", "FRAME_FACTS_MISSING"
                  in _list_codes(fx.make_paths(), {**RECORD, "facts": [], "screen": three}))


def test_the_lines_of_a_log_are_rows_never_each_one_counted():
    """An application log of some forty lines: a frame's outline gives its count, its first and last line and the lines
    the talk points at, as for a data table's rows; outlines quoting half of them were refused three times in a row."""

    paths = fx.make_paths()
    _write_slide(paths)
    texts = ["2025-01-07 09:14:02,118 INFO [main] acme.shop.StartupLogger: Starting shop service using Java",
             "2025-01-07 09:14:05,442 WARN [main] acme.shop.SecurityConfig: all security will be disabled",
             "acme.shop.web.filter.RequestFilter@1a2b, acme.shop.web.session.SessionFilter@3c4d, acme.shop.web.TimingFilter",
             "Listening for transport socket at address",
             "Orders page shows the refund button"]
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(texts) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("a timestamped log line and a line of class names are rows, not lines to quote",
                  lines == texts[3:], str(lines))


def _codes_with_read_lines(paths,
                           lines: list[str],
                           screen: str) -> set[str]:
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(lines) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        return _list_codes(paths, {**RECORD, "screen": screen})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous


def test_a_repeat_of_a_build_step_counts_the_text_its_build_quoted():
    """A frame repeating a later step of a slide built up step by step points at that step, whose outline holds only
    what it added; the slide's text is in the build's first frame, more than three frames away. The repeat was refused
    as quoting none of the slide's lines."""

    lines = ["Order intake overview", "Express orders retry five times", "The books close every night"]
    paths = fx.make_paths()
    _write_slide(paths)
    items = [{"slug": "frame-000", "index": 0}, {"slug": "frame-001", "index": 1, "stepOf": "frame-000"},
             {"slug": "frame-005", "index": 5, "repeatOf": "frame-001", "path": "frames/frame-005.jpg"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    whole = "- slide: white, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in lines)
    record_file_service.write_frame_record(paths, "frame-000", {**RECORD, "frame": 0, "screen": whole})
    record_file_service.write_frame_record(paths, "frame-001", {**RECORD, "frame": 1,
                                                                "screen": "- step 2 of the screen of frame 0: a red box added\n  - red box: around the first line"})
    harness.check("the repeat pointing at the step is not refused for the build's text",
                  "FRAME_SCREEN_TEXT_MISSING" not in _codes_with_read_lines(paths, lines, "- same as frame 1"),
                  str(_codes_with_read_lines(paths, lines, "- same as frame 1")))


def test_an_outline_opening_on_same_as_another_frame_but_is_filled_from_it():
    """Two board frames each opened a ticket's panel over frame 0's board and were outlined "same as frame 0, but with a
    detail panel open", the panel's parts under it; refused for pointing (board recording, 2026-10-22)."""

    paths = fx.make_paths()
    whole = '- board: white, the whole frame\n  - column "To do": left\n  - column "Done": right'
    record_file_service.write_frame_record(paths, "frame-000", {**RECORD, "frame": 0, "screen": whole})
    pointed = ('- same as frame 0, but with a detail panel open on the right side\n'
               '  - panel title "Order 42": top right\n  - field "Estimate": value "8"')
    filled = record_file_service.fill_screen_pointer(paths, "frame-001", {**RECORD, "frame": 1, "screen": pointed})
    harness.check("frame 0's outline, then the difference as a part holding its lines",
                  filled["screen"] == whole + '\n- With a detail panel open on the right side:\n'
                  '  - panel title "Order 42": top right\n  - field "Estimate": value "8"', filled["screen"])
    alone = record_file_service.fill_screen_pointer(paths, "frame-001", {**RECORD, "frame": 1,
                                                                       "screen": '- slide "Same as frame 0, but faster": top'})
    harness.check("a title quoting those words is the screen's text", alone["screen"].startswith('- slide "Same'))


def test_a_part_described_as_another_frame_s_is_filled_from_that_frame():
    """A board frame's outline described its header as "same as Frame 1", refused for pointing (board recording,
    2026-10-22)."""

    paths = fx.make_paths()
    earlier = ('- header: dark bar, top\n  - menu "Boards": left\n  - button "Create": right\n'
               '- board: white, below the header')
    record_file_service.write_frame_record(paths, "frame-001", {**RECORD, "frame": 1, "screen": earlier})
    pointed = '- tab bar: top\n- Header: same as Frame 1\n- panel "Order 42": right'
    filled = record_file_service.fill_screen_pointer(paths, "frame-002", {**RECORD, "frame": 2, "screen": pointed})
    harness.check("the part and its children come from frame 1, the rest stays",
                  filled["screen"] == '- tab bar: top\n- header: dark bar, top\n  - menu "Boards": left\n'
                  '  - button "Create": right\n- panel "Order 42": right', filled["screen"])
    unknown = record_file_service.fill_screen_pointer(paths, "frame-002", {**RECORD, "frame": 2,
                                                                         "screen": "- tab bar: top\n- panel: right\n- footer: same as frame 1"})
    harness.check("a part frame 1 does not have stays as written",
                  unknown["screen"] == "- tab bar: top\n- panel: right\n- footer: same as frame 1", unknown["screen"])


def test_a_screen_a_nearby_frame_quoted_may_be_outlined_short():
    """A dialog opened over an editor: the editor behind it was outlined whole two frames later, so the lines of the
    editor left out here are no loss; a frame four away is too far to count."""

    lines = ["Unauthorized access prohibited", "Express orders retry five times", "The books close every night"]
    whole = "- terminal: grey, the whole frame\n" + "".join(f'  - line "{line}": left\n' for line in lines)
    short = '- terminal: grey, the whole frame\n  - box with the access warning and the session details: centre'
    near = fx.make_paths()
    _write_slide(near)
    record_file_service.write_frame_record(near, "frame-007", {**RECORD, "frame": 7, "screen": whole})
    far = fx.make_paths()
    _write_slide(far)
    record_file_service.write_frame_record(far, "frame-009", {**RECORD, "frame": 9, "screen": whole})
    harness.check("lines quoted by a frame two after: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in _codes_with_read_lines(near, lines, short))
    harness.check("lines quoted only by a frame four after: refused",
                  "FRAME_SCREEN_TEXT_MISSING" in _codes_with_read_lines(far, lines, short))


def test_a_line_the_reader_could_not_read_is_not_counted():
    """Code half covered by a popup reads as fragments ("ntorde eate", "sild advi"): no outline can quote them, so a
    line with no word of the dictionary is not a line to quote; readable lines left out are still refused."""

    paths = fx.make_paths()
    _write_slide(paths)
    garbled = ["Ntorde eate xqv", "Sild advi tcreati", "Vbnq ents zork"]
    readable = ["Express orders retry five times", "The books close every night", "Refunds wait for approval"]
    quoted = "- page: white\n" + "".join(f'  - line "{line}": left\n' for line in readable)
    harness.check("the fragments left out and every readable line quoted: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in _codes_with_read_lines(paths, garbled + readable, quoted))
    harness.check("readable lines left out are still refused",
                  "FRAME_SCREEN_TEXT_MISSING" in _codes_with_read_lines(paths, garbled + readable, "- page: white"))


def test_a_german_line_left_out_is_refused_like_an_english_one_defect():
    # Defect: a line with no English word counted as unreadable, so a German screen's lines were never asked for (a
    # dialog's title among them). Fix: the playbook's German word list is read beside the English one
    paths = fx.make_paths()
    _write_slide(paths)
    german = ["Wirtschaftlichkeit der Anlage berechnen", "Jährliche Stromerzeugung der Solaranlage",
              "Strompreis pro Kilowattstunde eingeben", "Ergebnisse dienen nur als grobe Orientierung"]
    harness.check("German lines left out are refused",
                  "FRAME_SCREEN_TEXT_MISSING" in _codes_with_read_lines(paths, german, "- page: white"))


def test_a_line_read_again_inside_another_counts_once():
    """A ribbon button's label was read twice, once with a letter wrong ("und Links freigeben", "Links freigebon"), and
    the two copies made the third missing line that refused the frame: quoting the label once quotes both copies."""

    paths = fx.make_paths()
    _write_slide(paths)
    lines = ["Export and share links", "share links", "Express orders retry five times", "Refunds wait for approval",
             "The books close every night"]
    quoted = ('- page: white\n  - line "Express orders retry five times": left\n'
              '  - line "The books close every night": left\n')
    harness.check("the label and its copy left out are one missing line, not two: no refusal",
                  "FRAME_SCREEN_TEXT_MISSING" not in _codes_with_read_lines(paths, lines, quoted))
    harness.check("a third different line left out is still refused",
                  "FRAME_SCREEN_TEXT_MISSING" in _codes_with_read_lines(paths, lines, '- page: white\n  - line "The books close every night": left\n'))


def test_a_read_line_of_code_identifiers_is_code_never_a_line_to_quote():
    """An editor's project tree beside its code is read as one line, a folder's name joined with a cut identifier
    ("mappers .getBasicConfigurat"); the code may be given as a block, so such a line is code, not text to quote."""

    paths = fx.make_paths()
    _write_slide(paths)
    texts = ["mappers .getBasicConfigurat", "idea vpEnvironment", "configuration t.getAdvi order =",
             "Express orders retry twice", "Refunds wait for approval"]
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(texts) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("lines made of code identifiers are not lines to quote; plain text lines are",
                  lines == texts[3:], str(lines))


def test_a_debugger_s_own_hints_and_a_declaration_are_not_lines_to_quote():
    """An editor stopped at a breakpoint repeats its own hints on every frame ("Use Ctrl+Shift+Enter to add to Watches",
    "60 hidden frames"), as the browser's developer tools do, and its field declarations are code; plain text stays."""

    paths = fx.make_paths()
    _write_slide(paths)
    texts = ["Use Ctrl+Shift+Enter to add to Watches", "60 hidden frames", "Not showing null elements",
             "private final PaymentService paymentService", "public class OrderController implements",
             "The new process is private and final", "Refunds wait for approval"]
    words = [{"text": part, "line": number, "box": [40, 150 + number * 60, 200, 170 + number * 60], "block": 1}
             for number, line in enumerate(texts) for part in line.split()]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the debugger's hints and the declarations are not lines to quote; prose is",
                  lines == texts[5:], str(lines))


def test_a_line_crossing_a_pane_border_is_read_as_two_lines():
    """An editor's project tree and its code stand side by side, and the reader joins a folder's name with the code
    beside it into one line no outline can quote; a vertical border down most of the frame splits it."""

    paths = fx.make_paths()
    _write_slide(paths)
    image = cv2.imread(str(paths.output_root / "frames" / "frame-005.jpg"))
    image[:, 400] = 120
    cv2.imwrite(str(paths.output_root / "frames" / "frame-005.jpg"), image)
    words = [{"text": text, "line": 0, "box": [x, 300, x + 80, 320], "block": 1}
             for text, x in (("Express", 40), ("orders", 130), ("retry", 220), ("Refunds", 450), ("wait", 540),
                             ("approval", 600))]
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: words
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the words either side of the border are two lines", lines == ["Express orders retry", "Refunds wait approval"],
                  str(lines))


def test_a_line_is_split_where_a_pane_border_runs_between_two_of_its_words():
    """Word boxes are corners (left, top, right, bottom). The split took left + right as a word's right edge, so a
    border between a side panel's entry and the page's text beside it was almost never found, and the two were read as
    one line that neither part of the outline quotes."""

    words = [{"text": "Release", "box": [60, 100, 120, 112]}, {"text": "notes", "box": [124, 100, 170, 112]},
             {"text": "Orders", "box": [300, 100, 360, 112]}, {"text": "today", "box": [364, 100, 410, 112]}]
    pieces = item_validator._split_at_borders(words, [280])
    harness.check("the border at 280 between 'notes' (right edge 170) and 'Orders' (left edge 300) splits the line",
                  [[word["text"] for word in piece] for piece in pieces] == [["Release", "notes"], ["Orders", "today"]],
                  str(pieces))
    harness.check("no border between them keeps the line whole", len(item_validator._split_at_borders(words, [])) == 1)


def test_a_build_step_line_on_a_frame_of_no_build_allows_only_the_frame_it_names():
    """With no build recorded, the step line's own frame may be pointed to, never another."""

    paths = fx.make_paths()
    screen = ("- step 2 of the slide of frame 3: adds the box \"Billing\"\n"
              "  - box \"Billing\": right of \"Order Service\", same as frame 40")
    harness.check("a pointer to frame 40 is refused", "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(
        paths, {**RECORD, "screen": screen}))


def test_a_step_line_on_a_frame_the_frames_file_says_is_no_step_allows_no_pointer():
    """The frames file is the record of builds: a frame it lists with no `stepOf` points to no other frame, whatever
    line its outline opens with."""

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"],
                                        {"items": [{"slug": "frame-005", "index": 5}]})
    screen = ("- step 2 of the slide of frame 4: adds the box \"Billing\"\n"
              "  - box \"Billing\": right of \"Order Service\", same as frame 4")
    harness.check("a pointer to frame 4 is refused", "FRAME_SCREEN_POINTS_ELSEWHERE" in _list_codes(
        paths, {**RECORD, "screen": screen}))


def test_a_note_with_words_in_brackets_like_wait_or_actually_is_never_refused_defect():
    # Defect: a speaker's words written in brackets ("(wait, no ...)") were refused as the worker's own thinking, and a
    # batch blocked. Fix: the thinking-aloud check is removed whole; such a note is judged like any other.
    paths = fx.make_paths()
    bracketed = SCREEN + '\n  - footer: bottom, "Page 4" (wait, the thumbnail says 3. I will use the image text)'
    speech = "the speech gives the reason for the limit (wait, no, the speaker says the gateway signs it)"
    plain_codes = _list_codes(paths, RECORD)
    bracketed_codes = _list_codes(paths, {**RECORD, "screen": bracketed, "speech_visual_alignment": speech})
    harness.check("brackets with wait, hmm or actually add no refusal", bracketed_codes == plain_codes,
                  str(sorted(bracketed_codes - plain_codes)))


def test_a_take_away_quoting_words_said_only_over_the_next_frame_is_found():
    """One run's critique collection named speech from a later frame written under an earlier one in 18 rules, with the rule in
    the instructions twice (2026-10-12): the program knows each frame's words and checks the quotes."""

    own = "So the job retries three times. Then it waits."
    later = "And now we add the backoff, which doubles the delay every time."
    take_aways = ['"the job retries three times" → the retry box: a job is retried three times',
                  '"we add the backoff, which doubles the delay" → the delay field: the delay doubles',
                  '"the backoff" → the label: two words only, too short to judge',
                  "- Take aways: retries are bounded"]
    harness.check("only the quote said over the next frame is named",
                  item_validator._list_quotes_said_later(take_aways, own, later)
                  == ["we add the backoff, which doubles the delay"])
    harness.check("a quote said over both is no fault",
                  item_validator._list_quotes_said_later(take_aways[1:2], own + " " + later, later) == [])


def test_a_take_away_quoting_the_screen_text_is_told_so():
    """A worker quoted a slide's line as a take-away, the speaker read it out only over the next frame, and the
    rejection said only "said over the next frame": the retry kept the quote (2026-10-12)."""

    quotes = ["Retry budgets must be per client", "we add the backoff, which doubles the delay"]
    screen = ['- slide: white', '  - bullet "Retry budgets must be per client!"']
    harness.check("the quote the outline also holds is named as screen text",
                  item_validator._list_quotes_on_screen(quotes, screen) == ["Retry budgets must be per client"])
    harness.check("an outline written as one string is read too",
                  item_validator._list_quotes_on_screen(quotes, "\n".join(screen)) == ["Retry budgets must be per client"])


def test_a_take_away_quoting_screen_text_never_said_over_its_frame_is_found():
    """A take-away quoted two of a slide's bullets the speaker never read out (38c3-fault-injection frame 5): on one
    lecture 30 of 172 frames did it, while the check named only screen text said over the next frame (2026-10-15)."""

    own = "The memory was really tight on that chip, so they skipped some checks."
    screen = ['- slide: dark', '  - bullet "Available memory space was a huge limiting factor"',
              '  - bullet "Retry budgets are per client"']
    take_aways = ['"Available memory space was a huge limiting factor" → the first bullet: memory forced the design',
                  '"so they skipped some checks" → the first bullet: checks were left out',
                  '"Retry budgets" → the second bullet: too short to judge',
                  "- Take aways: memory decided the design"]
    harness.check("the bullet quoted as speech is named, the spoken quote and the short one are not",
                  item_validator._list_screen_quotes_unsaid(take_aways, own, screen)
                  == ["Available memory space was a huge limiting factor"])
    harness.check("a bullet the speaker reads out over the frame is no fault",
                  item_validator._list_screen_quotes_unsaid(take_aways[:1], own + " Available memory space was a huge "
                                                            "limiting factor.", screen) == [])


def test_a_take_away_quoting_unsaid_screen_text_is_filed_as_not_mentioned():
    """A worker reading slides wrote their bullets where the speaker's words belong, on 4 of 10 frames of one batch
    (38c3-fault-injection frames 3, 7, 8, 9, 2026-10-15): what it read is right, its label is not, so the program
    files the line as the part nobody mentions and keeps what it tells."""

    own = "The memory was really tight on that chip, so they skipped some checks."
    screen = ['- slide: dark', '  - bullet "Available memory space was a huge limiting factor"']
    take_aways = ['- "Available memory space was a huge limiting factor" → memory forced the design',
                  '- "so they skipped some checks" → the first bullet: checks were left out',
                  "- Take aways: memory decided the design"]
    filed = frame_markdown_utils.mark_unsaid_screen_quotes(take_aways, own, screen)
    harness.check("the bullet quoted as speech is filed as not mentioned, with what it tells",
                  filed[0] == '- the text "Available memory space was a huge limiting factor" → not mentioned: memory '
                              'forced the design', filed[0])
    harness.check("a spoken quote and the closing line stay as written", filed[1:] == take_aways[1:], str(filed[1:]))
    harness.check("the check then finds nothing to refuse",
                  item_validator._list_screen_quotes_unsaid(filed, own, screen) == [])


def test_a_screen_quote_the_speaker_says_in_other_words_is_tied_to_that_speech():
    """Filed as not mentioned alone, 5 frames whose speaker said the bullet in other words were named unmentioned by
    the critique (38c3-fault-injection frames 11, 13, 14, 17, 18, 2026-10-15)."""

    own = "So our idea was to use power analysis. Then we waited."
    screen = ['- slide: dark', '  - bullet "Power analysis provides a good way to see the chip"']
    take_aways = ['- "Power analysis provides a good way to see the chip" → watching the power shows the chip at work']
    filed = frame_markdown_utils.mark_unsaid_screen_quotes(take_aways, own, screen)
    harness.check("the speech saying it in other words is quoted, tied to the slide's text",
                  filed == ['- "So our idea was to use power analysis." → the text "Power analysis provides a good way '
                            'to see the chip": watching the power shows the chip at work'], str(filed))
    harness.check("the check then finds nothing to refuse",
                  item_validator._list_screen_quotes_unsaid(filed, own, screen) == [])


def test_a_part_the_worker_calls_unmentioned_is_tied_to_the_speech_that_says_it():
    """The worker wrote `the text "Obtained ~44KB of data" → not mentioned` while the speaker said "exactly 44 kilobytes
    of data"; a number joined to its unit ("44KB") shared no word with the speech (38c3-fault-injection frame 20,
    2026-10-15)."""

    own = "We looked at the dump. It is exactly 44 kilobytes of data, the whole RAM."
    take_aways = ['- the text "Obtained ~44KB of data" → not mentioned: the size of the dump',
                  '- the text "Readable strings" → not mentioned: names in the dump']
    filed = frame_markdown_utils.mark_unsaid_screen_quotes(take_aways, own, [])
    harness.check("the size the speaker said is tied to the slide's text",
                  filed[0] == '- "It is exactly 44 kilobytes of data, the whole RAM." → the text "Obtained ~44KB of data": '
                              'the size of the dump', filed[0])
    harness.check("a part nobody says stays unmentioned", filed[1] == take_aways[1], filed[1])


def test_a_take_away_quoting_words_said_over_an_earlier_frame_is_refused():
    """A frame with no speech of its own quoted, inside a sentence, what the speaker had said two frames earlier; the
    next-frame check let it through and the critique named it."""

    take_aways = ['Jonas says "the gateway sends every token to the provider first" while the board is open']
    earlier = "So the gateway sends every token to the provider first, that is settled."
    harness.check("words said over an earlier frame, quoted inside a sentence, are found",
                  item_validator._list_quotes_said_before(take_aways, "", earlier)
                  == ["the gateway sends every token to the provider first"])
    harness.check("not when a run of them is said over this frame too (a sentence said across the two)",
                  item_validator._list_quotes_said_before(take_aways, "sends every token to the provider", earlier) == [])
    harness.check("nor words only this frame's speech holds",
                  item_validator._list_quotes_said_before(take_aways, earlier, "") == [])


def test_an_outline_pointing_at_another_frame_is_filled_with_that_frame_s_outline():
    """"Same as frame N" was refused four times in one run for frames phase 2 had not marked (a scroll, a twin record);
    the program fills it from frame N's notes, then adds the frame's own lines."""

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-004", "index": 4, "path": "frames/frame-004.jpg"},
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg"}]})
    record_file_service.write_frame_record(paths, "frame-004", {**RECORD, "frame": 4, "screen": SCREEN})
    filled = record_file_service.fill_screen_pointer(paths, "frame-005", {**RECORD, "screen": "- same as frame 4"})
    harness.check("the pointer is replaced by frame 4's outline", filled["screen"] == SCREEN, filled["screen"][:200])
    harness.check("a pointer to a frame with no notes stays, to be refused",
                  record_file_service.fill_screen_pointer(paths, "frame-005", {**RECORD, "screen": "- same as frame 9"})["screen"]
                  == "- same as frame 9")
    harness.check("an outline is left as it is",
                  record_file_service.fill_screen_pointer(paths, "frame-005", RECORD)["screen"] == RECORD["screen"])


def test_a_step_without_its_step_line_gets_it_from_the_program():
    """A step's message names the line its outline opens with; a worker left it out on a step of a terminal, and a
    reader of that frame's notes alone saw two lines with no screen they belong to (Lecture 9, frame 54)."""

    paths = fx.make_paths()
    items = [{"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4, "stepOf": "frame-003"},
             {"slug": "frame-005", "index": 5}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    bare = '- terminal output: right side\n  - result: "2 files left unchanged"'
    added = item_validator.add_step_line(paths, "frame-004", {**RECORD, "screen": bare})["screen"]
    harness.check("the line opens the outline, the parts kept under it", added.startswith(
        "- a step of the screen of frame 3: the parts below\n  - terminal output: right side\n    - result:"), added)
    written = '- step 2 of the screen of frame 3: a result line\n  - result: "2 files left unchanged"'
    harness.check("a step that wrote it is left as it is",
                  item_validator.add_step_line(paths, "frame-004", {**RECORD, "screen": written})["screen"] == written)
    harness.check("a frame of no build is left as it is",
                  item_validator.add_step_line(paths, "frame-005", {**RECORD, "screen": bare})["screen"] == bare)


def test_a_step_line_naming_another_frame_is_corrected_by_the_program():
    """A worker copied the step line of one build's step into the 13 frames of the next build, its first frame
    included, all naming the earlier build's first frame (Lecture 9, frames 83 to 95)."""

    paths = fx.make_paths()
    items = [{"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4, "stepOf": "frame-003"},
             {"slug": "frame-008", "index": 8}, {"slug": "frame-009", "index": 9, "stepOf": "frame-008"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    copied = '- a step of the screen of frame 3: the parts below\n  - editor: "def test_pad():"'
    harness.check("a step's line names its own build's first frame",
                  item_validator.add_step_line(paths, "frame-009", {**RECORD, "screen": copied})["screen"]
                  == '- a step of the screen of frame 8: the parts below\n  - editor: "def test_pad():"')
    described = '- step 2 of the screen of frame 3: a test added\n  - editor: "def test_pad():"'
    harness.check("its own words after the number are kept",
                  item_validator.add_step_line(paths, "frame-009", {**RECORD, "screen": described})["screen"]
                  .startswith("- step 2 of the screen of frame 8: a test added"))
    harness.check("a build's first frame names the screen, its parts kept under it",
                  item_validator.add_step_line(paths, "frame-008", {**RECORD, "screen": copied})["screen"]
                  == '- the screen: the parts below\n  - editor: "def test_pad():"')
    harness.check("a step naming its own first frame is left as it is",
                  item_validator.add_step_line(paths, "frame-004", {**RECORD, "screen": copied})["screen"] == copied)


def test_a_take_away_quoting_another_frame_s_words_is_dropped_by_the_program():
    """A build step quoted words said over its first step, two frames before, and the check blocked the batch; a check
    and four instruction changes had not stopped it (Lecture 9, frame 75)."""

    paths = fx.make_paths()
    items = [{"slug": "frame-003", "index": 3, "transcriptCurrent": "I rerun the tests with coverage, I get my report."},
             {"slug": "frame-004", "index": 4, "stepOf": "frame-003", "transcriptCurrent": ""},
             {"slug": "frame-005", "index": 5, "stepOf": "frame-003",
              "transcriptCurrent": "So let's test 95 here. Now the coverage went up to 61%."},
             {"slug": "frame-006", "index": 6, "transcriptCurrent": "And that line is covered."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    own = '- "So let\'s test 95 here" → the test\'s input: it aims at the multiples of five'
    borrowed = '- "rerun the tests with coverage, I get my report" → the terminal: coverage rises to 61%'
    summary = "- Take aways: a new input raises the coverage."
    kept = item_validator.drop_misplaced_take_aways(paths, "frame-005", {**RECORD, "take_aways": [own, borrowed, summary]})
    harness.check("the line quoting the first step's words is dropped, the others kept",
                  kept["take_aways"] == [own, summary], str(kept["take_aways"]))
    report = ValidationReport(subject="frame-005")
    item_validator._check_speech_quotes(report, paths, "frame-005", kept)
    harness.check("the check then finds nothing to refuse", not report.findings, str(report.findings))
    later = '- "And that line is covered" → the report: the line turns green'
    harness.check("a line quoting the next frame's words is dropped too",
                  item_validator.drop_misplaced_take_aways(paths, "frame-005", {**RECORD, "take_aways": [own, later]})
                  ["take_aways"] == [own])


def test_a_frame_whose_only_take_away_is_another_frame_s_words_loses_it_defect():
    # A board step's one take-away quoted the summary said over the next frame; left to the check, it blocked a batch
    # under zero repair (Lecture 7 frame 44). The frame that heard the words writes them: the line is dropped here
    paths = fx.make_paths()
    items = [{"slug": "frame-005", "index": 5, "transcriptCurrent": "It runs the loop and dispatches each tool call."},
             {"slug": "frame-006", "index": 6, "transcriptCurrent": "So these are the main primitives, sampling many times."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    borrowed = '- "So these are the main primitives, sampling many times" → the board: the agent loop'
    kept = item_validator.drop_misplaced_take_aways(paths, "frame-005", {**RECORD, "take_aways": [borrowed]})
    harness.check("the one borrowed line is dropped", kept["take_aways"] == [], str(kept["take_aways"]))
    report = ValidationReport(subject="frame-005")
    item_validator._check_speech_quotes(report, paths, "frame-005", kept)
    harness.check("and the check finds nothing to refuse", not report.findings, str(report.findings))



def test_a_take_away_quoting_the_slide_s_new_label_is_dropped_and_the_summary_naming_it_kept_defect():
    # A build step's take-away quoted the label it adds to the slide, said over an earlier frame and not over this one;
    # the drop kept that line for the check, removed the summary for naming it, and the run blocked. Now a line opening
    # with the screen's text goes, and a quote the outline holds counts as the screen's, never as earlier speech
    paths = fx.make_paths()
    items = [{"slug": "frame-006", "index": 6, "transcriptCurrent": "Imagine someone asks: what is the best refund rule?"},
             {"slug": "frame-007", "index": 7, "transcriptCurrent": "Like if I simplify it."},
             {"slug": "frame-008", "index": 8, "transcriptCurrent": "Think of tickets about refunds and tickets about payouts."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    label = '- "What is the best refund rule?" → adds a query point to the graph'
    later = '- "tickets about refunds and tickets about payouts" → the separation the graph shows'
    summary = '- Take aways: a query like "what is the best refund rule" is a point beside the documents.'
    record = {**RECORD, "take_aways": [label, later, summary],
              "screen": '- step 4 of the screen of frame 5: adds a point\n  - label "What is the best refund rule?": near P1'}
    kept = item_validator.drop_misplaced_take_aways(paths, "frame-007", record)
    harness.check("the label's line and the next frame's line go, the summary stays",
                  kept["take_aways"] == [summary], str(kept["take_aways"]))
    report = ValidationReport(subject="frame-007")
    item_validator._check_speech_quotes(report, paths, "frame-007", kept)
    harness.check("the check then finds nothing to refuse", not report.findings, str(report.findings))


def test_a_quote_announcing_the_frame_s_new_screen_is_not_refused_as_earlier_speech_defect():
    # The speaker named a tool just before the slide showed it, and the take-away of that slide's frame quoting it was
    # refused as said over an earlier frame. A quote whose telling word the frame's outline shows new is about it
    paths = fx.make_paths()
    items = [{"slug": "frame-049", "index": 49, "transcriptCurrent": "And there's a nice tool which can help you, called Syncwell.",
              "record": {"screen": '- title "Add license files"'}},
             {"slug": "frame-050", "index": 50, "transcriptCurrent": "It was made by a foundation."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    announcing = {**RECORD, "take_aways": ['- "a nice tool which can help you, called Syncwell" → the new bullet: the tool'],
                  "screen": '- title "Add license files"\n- bullet "Syncwell: label every file"'}
    report = ValidationReport(subject="frame-050")
    item_validator._check_speech_quotes(report, paths, "frame-050", announcing)
    harness.check("a quote naming what the screen newly shows passes", not report.findings, str(report.findings))
    other = {**announcing, "screen": '- title "Add license files"\n- bullet "Label every file"'}
    report = ValidationReport(subject="frame-050")
    item_validator._check_speech_quotes(report, paths, "frame-050", other)
    harness.check("one naming nothing the screen shows is still refused", bool(report.findings), "")


def test_a_name_the_speech_misheard_is_written_as_the_screen_spells_it_defect():
    # Defect: the slides read "AcmeSync" and the notes wrote "AcmeSink", as heard, on every frame, and phase 5 had to
    # fix the document. Fix: a name said over the frame and on no screen, a letter off a screen word never said, is respelled.
    from unittest import mock

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-001", "index": 1, "path": "frames/frame-001.jpg", "transcriptCurrent": "Welcome to AcmeSink."},
        {"slug": "frame-002", "index": 2, "path": "frames/frame-002.jpg",
         "transcriptCurrent": "AcmeSink keeps every Pages site, and GitHub hosts it."}]})
    screens = {"frame-001": ["Welcome to AcmeSync today"], "frame-002": ["Pads and GitLab hosting"]}
    record = {**RECORD, "facts": ["AcmeSink keeps every site in sync.", "It runs on GitHub with Pages."],
              "take_aways": ['- "AcmeSink keeps every" → title: AcmeSink stores the sites.']}
    with mock.patch.object(item_validator, "read_screen_lines", side_effect=lambda _paths, slug: screens.get(slug, [])):
        respelled = item_validator.use_screen_spelling_of_heard_names(paths, "frame-002", record)
    harness.check("the heard name takes an earlier screen's spelling, a quote of the speech keeps what was said",
                  respelled["facts"][0] == "AcmeSync keeps every site in sync."
                  and respelled["take_aways"] == ['- "AcmeSink keeps every" → title: AcmeSync stores the sites.'],
                  str(respelled["facts"] + respelled["take_aways"]))
    harness.check("a name two letters off a short screen word, or another product, stays",
                  respelled["facts"][1] == "It runs on GitHub with Pages.", respelled["facts"][1])


def test_a_claim_the_worker_corrected_in_brackets_keeps_the_correction_defect():
    # Defect: a frame's notes read "frame 65: step 3 of the screen first shown as frame 63 (actually, this is a new
    # callout ...)", and the frame was blocked as thinking aloud. Fix: the program keeps "<label>: <correction>".
    record = {**RECORD, "builds_on": ["frame 4: step 3 of the screen first shown as frame 2 (actually, this is a new "
                                      "callout for the gateway part)."], "facts": ["Hmm, the slide is blank."]}
    settled = item_validator.replace_corrected_claims(record)
    harness.check("the correction stands in place of the claim it corrects",
                  settled["builds_on"] == ["frame 4: This is a new callout for the gateway part."], str(settled["builds_on"]))


def test_a_step_claim_under_builds_on_that_the_frames_file_does_not_hold_is_dropped_defect():
    # Defect: frames that were no step of any build wrote "step 2 of the screen first shown as frame 73" under Builds on,
    # a photo slide among them, copied from the step lines of the frames before. Fix: a claim the frames file does not hold goes.
    paths = fx.make_paths()
    items = [{"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4, "stepOf": "frame-003"},
             {"slug": "frame-005", "index": 5}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    claim = {**RECORD, "builds_on": ["frame 4: step 2 of the screen first shown as frame 3. Shows the gateway's next step."]}
    harness.check("a frame that is no step loses the claim and keeps the rest of the line",
                  item_validator.drop_false_step_claims(paths, "frame-005", claim)["builds_on"]
                  == ["frame 4: Shows the gateway's next step."],
                  str(item_validator.drop_false_step_claims(paths, "frame-005", claim)["builds_on"]))
    harness.check("a real step keeps its line", item_validator.drop_false_step_claims(paths, "frame-004", claim) == claim)


def test_an_outline_line_quoting_the_frame_before_is_dropped_defect():
    # Defect: a frame's outline quoted the screen of the frame before (a file shown before, an old page's headlines),
    # and the program filled it out with the frame's own lines. Fix: a line the reader finds on the frame before only goes.
    from unittest import mock

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-004", "index": 4}, {"slug": "frame-005", "index": 5}]})
    screens = {"frame-004": ["def split_refund(amount, parts):", "Gateway refund helpers"],
               "frame-005": ["name: build and test", "Gateway refund helpers"]}
    outline = ('- editor: the whole frame\n  - title "Gateway refund helpers": top\n'
               '  - code "def split_refund(amount, parts):": first line\n  - code "name: build and test": below')
    with mock.patch.object(item_validator, "read_screen_lines", side_effect=lambda _paths, slug: screens.get(slug, [])):
        kept = item_validator.drop_neighbour_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
    harness.check("the frame before's line goes; the title both show and the frame's own line stay",
                  "split_refund" not in kept and "Gateway refund helpers" in kept and "build and test" in kept, kept)


def test_a_repeat_keeps_an_outline_line_its_original_shows_defect():
    # Defect: a frame shown again lost its tab line, which the reader read on the frame before and not on it, though
    # it shows the tabs (froscon 3147 frame 151). Fix: the words read on a repeat's original count as its own.
    from unittest import mock

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4},
        {"slug": "frame-005", "index": 5, "repeatOf": "frame-003"}]})
    screens = {"frame-003": ["Inbox Sent Drafts Archive"], "frame-004": ["Inbox Sent Drafts Archive"],
               "frame-005": ["name: build and test"]}
    outline = '- app: the whole frame\n  - tabs "Inbox Sent Drafts Archive": top\n  - text "name: build and test": below'
    with mock.patch.object(item_validator, "read_screen_lines", side_effect=lambda _paths, slug: screens.get(slug, [])):
        kept = item_validator.drop_neighbour_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
        phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
            {"slug": "frame-003", "index": 3}, {"slug": "frame-004", "index": 4}, {"slug": "frame-005", "index": 5}]})
        lone = item_validator.drop_neighbour_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
    harness.check("the repeat keeps the line its original shows", "Inbox Sent Drafts Archive" in kept, kept)
    harness.check("the same frame shown for the first time still loses it", "Inbox Sent Drafts Archive" not in lone, lone)


def test_a_frame_stored_shrunk_is_checked_against_the_words_read_at_full_size_defect():
    # A table's rows in a small code font reached neither the notes nor the completion: read on the frame stored shrunk,
    # the text reader read none of its cells. Phase 2 now reads them on the recording (`screenWords`), and those count
    paths = fx.make_paths()
    _write_slide(paths)
    full_size = [_read_word(word, 1, 300) for word in ("refundLimit", "decimal", "largest", "refund")]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg", "screenWords": full_size}]})
    previous = (text_detection_service.find_words, text_detection_service.is_available)
    text_detection_service.find_words = lambda path, min_confidence=0: []
    text_detection_service.is_available = lambda: True
    try:
        lines = item_validator.read_screen_lines(paths, "frame-005")
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the line read at full size is a line to quote", lines == ["refundLimit decimal largest refund"],
                  str(lines))


def test_a_facts_refusal_quotes_the_take_away_that_holds_the_knowledge_defect():
    # A frame of a circuit diagram kept what its parts tell in the take-away and left `## Facts` empty; refused, the
    # retry was told only to state facts. The refusal now quotes the worker's own reading of the screen
    from validators import item_validator
    from models.validation_report import ValidationReport

    screen = "\n".join(["- image: circuit part", *(f'  - label "{label}"' for label in
                                                       ("REL5OFF", "REL5MAIN", "REL6OFF", "REL6MAIN", "D51", "D61", "PS1"))])
    record = {"facts": [], "screen": screen,
              "take_aways": ['"the relays sit on the left" → the relay outlines: each AcmeSync relay has an OFF and a MAIN '
                             'pin and its own diode', '"we go on" → nothing on screen',
                             "- Take aways: the relay wiring of the board"]}
    report = ValidationReport(subject="frame-014")
    item_validator._check_facts(report, "frame-014", record, spoken_words=60)
    findings = [finding for finding in report.findings if finding.code == "FRAME_FACTS_MISSING"]
    harness.check("refused", len(findings) == 1, str(report.findings))
    harness.check("the fix quotes the take-away's teaching",
                  bool(findings) and "each AcmeSync relay has an OFF and a MAIN pin and its own diode" in findings[0].fix
                  and "nothing on screen" not in findings[0].fix, findings[0].fix if findings else "")


def test_dense_terminal_text_has_no_pane_border_and_a_drawn_panel_line_has_one_defect():
    # A plain terminal's dense text changed on most rows of nearly every column, every column read as a pane border,
    # and the worker was told to outline a side panel that was not there. A border runs unbroken down the frame now
    import cv2
    import numpy

    terminal = numpy.full((360, 520, 3), 250, numpy.uint8)
    commands = ("rssisurvey: Scan all channels for noise [n] times", "setmem: Set memory [1-10] to value",
                "adcinall: Read all ADC input channels", "select: Select module for remote cmd execution")
    for place, row in enumerate(range(14, 360, 13)):
        cv2.putText(terminal, commands[place % 4], (4, row), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (20, 20, 20), 1)
    harness.check("no column of a terminal's text is a border", item_validator._find_pane_borders(terminal) == [],
                  str(item_validator._find_pane_borders(terminal)[:5]))
    panel = terminal.copy()
    panel[:, 450:452] = 120
    harness.check("a panel's line down the frame, clear of the text, is one", any(449 <= border <= 452
                                                              for border in item_validator._find_pane_borders(panel)),
                  str(item_validator._find_pane_borders(panel)))


def test_a_step_s_screenshot_pointing_to_another_frame_is_no_entry_defect():
    # A step wrote "- same as frame 67" under "## Screenshots"; read as a suggestion with no importance word, it was
    # refused and blocked its batch. Such a pointer is now skipped: the frame it names carries the suggestion
    from utils import frame_markdown_utils

    answer = ("# Frame 4 · slide\n\n## Screenshots\n- same as frame 3\n\n## Tags\nGateway\n")
    frames = frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"]
    shots = frames[0].get("screenshots") if frames else None
    harness.check("the pointer leaves the step with no screenshot", shots == [], str(frames))
    kept = frame_markdown_utils.parse_answer("# Frame 4 · slide\n\n## Screenshots\n- the Gateway chart\n  - importance: helps\n"
                                             "  - why: its curve\n", is_addition=False)["frames"]
    harness.check("a real suggestion stays", kept and kept[0]["screenshots"][0]["importance"] == "helps", str(kept))


def test_a_pointer_to_a_frame_not_repeated_is_replaced_by_the_frame_s_own_lines_defect():
    # Defect: a worker wrote "same as frame 4" for a frame showing another slide, and the program filled in frame 4's
    # slide. Fix: such a fill stands only when it quotes half the frame's own lines, else the outline is those lines.
    paths = fx.make_paths()
    items = [{"slug": "frame-004", "index": 4, "repeatOf": None, "stepOf": None},
             {"slug": "frame-008", "index": 8, "repeatOf": "frame-004", "stepOf": None},
             {"slug": "frame-009", "index": 9, "repeatOf": None, "stepOf": None}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    other = {"screen": '- slide\n  - title "Rollout plan for Gateway": top centre'}
    lines = ["Points near the query are similar", "according to the embedding model", "the nearest point is returned"]
    previous = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: lines
    try:
        written = {"screen": "- same as frame 4"}
        kept = item_validator.replace_unfitting_pointed_outline(paths, "frame-009", written, other)
        repeat = item_validator.replace_unfitting_pointed_outline(paths, "frame-008", written, other)
    finally:
        item_validator.read_screen_lines = previous
    harness.check("the frame not repeating 4 gets its own lines", "Rollout plan" not in kept["screen"]
                  and '"according to the embedding model"' in kept["screen"], kept["screen"])
    harness.check("the frame that repeats 4 keeps the fill", repeat == other, str(repeat))


def test_a_take_away_quoting_its_speech_with_a_number_changed_is_dropped_defect():
    # A worker quoted "4 outputs with 4096 steps" where "two outputs" was said, its own doubt written after the quote,
    # and the notes passed. Now the program drops a take-away whose quote is its speech with a number changed.
    paths = fx.make_paths()
    items = [{"slug": "frame-005", "index": 5,
              "transcriptCurrent": "Here we see a converter. The module has two analog outputs with 4096 steps. Then a "
                                   "radio module with 433 MHz."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    right = '- "a radio module with 433 MHz" → the board: wireless networking'
    changed = '- "4 analog outputs with 4096 steps" (two in the speech) → the converter: analog outputs'
    summary = "- Take aways: the board carries a converter and a radio."
    kept = item_validator.drop_misplaced_take_aways(paths, "frame-005", {**RECORD, "take_aways": [right, changed, summary]})
    harness.check("the line with the changed number is dropped, the others kept",
                  kept["take_aways"] == [right, summary], str(kept["take_aways"]))
    said = '- "two analog outputs with 4096 steps" → the converter: 12-bit outputs'
    harness.check("the number written as its digit is no change",
                  item_validator.drop_misplaced_take_aways(paths, "frame-005", {**RECORD, "take_aways": [right, '- "2 analog outputs with 4096 steps" → the converter', said]})
                  ["take_aways"] == [right, '- "2 analog outputs with 4096 steps" → the converter', said])


def test_any_pointing_phrase_outside_the_build_is_repaired_defect():
    # A terminal's outline said "lines identical to frame 4 down to ...", a phrase the repair did not know, and the batch
    # was blocked for pointing. Now every phrase the check refuses is repaired: the outline becomes the frame's own
    # lines when it does not fit them, and a pointer line goes from one that does
    paths = fx.make_paths()
    items = [{"slug": "frame-004", "index": 4, "repeatOf": None, "stepOf": None},
             {"slug": "frame-009", "index": 9, "repeatOf": None, "stepOf": None}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    lines = ["select: Select module for remote cmd", "status: Get RFM status", "Selected Target 0"]
    previous = item_validator.read_screen_lines
    item_validator.read_screen_lines = lambda paths, slug: lines
    try:
        thin = {"screen": '- terminal: white\n  - lines identical to frame 4 down to "Selected Target 0"'}
        rebuilt = item_validator.replace_unfitting_pointed_outline(paths, "frame-009", thin, thin)
        fitting = {"screen": '- terminal: white\n  - lines identical to frame 4\n  - line "select: Select module for remote cmd"\n'
                             '  - line "status: Get RFM status"\n  - line "Selected Target 0"'}
        trimmed = item_validator.replace_unfitting_pointed_outline(paths, "frame-009", fitting, fitting)
    finally:
        item_validator.read_screen_lines = previous
    harness.check("a thin outline becomes the frame's own lines", rebuilt["screen"].startswith(item_validator.POINTED_OUTLINE_PART)
                  and '"status: Get RFM status"' in rebuilt["screen"], rebuilt["screen"])
    harness.check("a fitting outline loses only its pointer line", "frame 4" not in trimmed["screen"]
                  and '"Selected Target 0"' in trimmed["screen"], trimmed["screen"])


def test_a_fact_that_only_tells_a_question_asked_is_dropped_defect():
    # "A participant asks about handling large memory usage" stood as a fact beside the question in its own field, and
    # phase 4 wrote it into the document as a sentence about the meeting. Now such a fact goes as the notes are written
    asked = {**RECORD, "questions_asked": ['"Can the cache grow?" answered later'],
             "facts": ["A participant asks whether the Gateway cache can grow past its memory.",
                       "The Gateway cache holds every refund token in memory."]}
    harness.check("the asking fact goes, the fact about the subject stays",
                  item_validator.drop_question_facts(asked)["facts"] == ["The Gateway cache holds every refund token in memory."])
    unasked = {**asked, "questions_asked": []}
    harness.check("with no question asked nothing goes", item_validator.drop_question_facts(unasked) == unasked)


def test_a_screenshot_pointer_with_an_empty_mark_is_no_entry_defect():
    # A repeat wrote "- same as frame 27: (none)" under "## Screenshots"; read as a suggestion with no importance, it
    # blocked its batch. A pointer with an empty mark after it is skipped like a bare one
    from utils import frame_markdown_utils

    for line in ("- same as frame 3: (none)", "- same as frame 3 - none"):
        frames = frame_markdown_utils.parse_answer(f"# Frame 4 · slide\n\n## Screenshots\n{line}\n\n## Tags\nGateway\n",
                                                   is_addition=False)["frames"]
        harness.check(f"{line!r} leaves no screenshot", frames and frames[0].get("screenshots") == [], str(frames))


def test_a_fact_name_one_letter_off_the_outline_is_written_as_the_outline_quotes_it_defect():
    # A fact named a radio module one letter off the slide's name that the outline quoted right (DEFECTS 276); now the
    # fact takes the outline's spelling, and a name the outline itself holds, or near two names, is left as written
    record = {"screen": '- slide: white\n  - bullet "GW45XT from the Acme store"\n  - label "PORT7A"\n  - label "PORT7B"',
              "facts": ["The GW46XT gateway is sold in the Acme store.", "PORT7C is the spare port.",
                        "The gateway is the GW45XT."]}
    fixed = item_validator.use_outline_spelling_in_facts(record)
    harness.check("the misspelled name takes the outline's spelling, a name near two names stays",
                  fixed["facts"] == ["The GW45XT gateway is sold in the Acme store.", "PORT7C is the spare port.",
                                     "The gateway is the GW45XT."], str(fixed["facts"]))
    harness.check("a record whose facts match the outline is returned as it is",
                  item_validator.use_outline_spelling_in_facts(fixed) is fixed)


def test_a_markdown_word_the_outline_quotes_otherwise_takes_the_outline_spelling_defect():
    # The markdown copy kept a slide's misspelling corrected after the outline quoted it as the slide writes it
    # (DEFECTS 276); now the markdown takes the outline's spelling, letter case aside
    record = {"screen": '- slide: white\n  - bullet "Intagrated receiving desk"\n  - bullet "Shipping weekly"',
              "facts": ["Shipping runs weekly."], "as_markdown": "- Integrated receiving desk\n- shipping weekly"}
    fixed = item_validator.use_outline_spelling_in_facts(record)
    harness.check("the corrected word takes the slide's spelling, a word differing only in case stays",
                  fixed["as_markdown"] == "- Intagrated receiving desk\n- shipping weekly", fixed["as_markdown"])
    harness.check("a note saying the slide has a typo is no part of the outline",
                  frame_markdown_utils.strip_reading_notes('  - bullet "Intagrated receiving desk": left (note typo in original)')
                  == '  - bullet "Intagrated receiving desk": left')


def test_a_heard_model_name_with_a_hyphen_takes_the_screen_s_spelling_defect():
    # Defect: the speech's "QX-200" stood on the screen as "KX200"; the hyphen split the name and its first letter
    # differed, so it was never respelled. Fix: a model name is one token, compared without its hyphen, and a name whose
    # digits are the screen's may differ in its first letter
    from validators import item_validator
    read = {}
    quoted = {"kx200": "KX200"}
    line = item_validator._use_screen_spelling("The QX-200 drives the display.", read, quoted, {"qx200"}, set())
    harness.check("the screen's spelling is written", line == "The KX200 drives the display.", line)
    other = item_validator._use_screen_spelling("The QX-300 drives the display.", read, quoted, {"qx300"}, set())
    harness.check("other digits are no misheard name", other == "The QX-300 drives the display.", other)
    misread = item_validator._use_screen_spelling("The QX-69CW sends.", {}, {"qx6scw": "QX6SCW"}, {"qx69cw"}, set())
    harness.check("a screen word with a misread digit is no spelling", misread == "The QX-69CW sends.", misread)


def test_a_heard_name_after_a_word_joining_hyphen_takes_the_screen_s_spelling_defect():
    # Defect: "Brenner-Kalimor" heard where the slide reads "Brenner-Kalimar": the hyphen before the second name was read
    # as a list's dash, so that word counted as a sentence's first word, never a name. Fix: a word-joining hyphen is no dash
    from validators import item_validator
    line = item_validator._use_screen_spelling("The Brenner-Kalimor method is used.", {"kalimar": "Kalimar"},
                                               {"kalimar": "Kalimar"}, {"kalimor"}, set())
    harness.check("the second name takes the screen's spelling", line == "The Brenner-Kalimar method is used.", line)
    listed = item_validator._use_screen_spelling("- Kalimor runs.", {"kalimar": "Kalimar"}, {}, {"kalimor"}, set())
    harness.check("a list's dash still opens a sentence", listed == "- Kalimor runs.", listed)


def test_a_quoted_spelling_the_reader_finds_on_later_frames_is_still_the_screen_s_defect():
    # Defect: the speech's "QX-200" stood quoted on the frame's outline as "KX200", and the reader found "KX200" on later
    # frames too; that later read, which lends no spelling, replaced the outline's quote, so nothing was respelled
    # (DEFECTS 362, 312 undone). Fix: a word read only on later frames never takes away an outline's quote
    from validators import item_validator
    line = item_validator._use_screen_spelling("The QX-200 drives the display.", {"kx200": None},
                                               {"kx200": "KX200"}, {"qx200"}, set())
    harness.check("the outline's spelling is written", line == "The KX200 drives the display.", line)


def test_the_second_reader_reads_a_small_frame_at_its_own_size_too_defect():
    # Defect: Apple's recognizer read a 545-pixel frame's "DisAssembler" right at its size and twice it, and as
    # "Disfssembler" three times larger, the one size it was read at; the misreading the outline copied went into the
    # facts (DEFECTS 364). Fix: the frame is read at its own size too, and a word either reading finds counts
    import tempfile
    from models.run_paths import RunPaths
    from services import apple_text_service

    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=tmp, module="talk.mp4", output_root_override=tmp)
    (tmp / "frames").mkdir()
    cv2.imwrite(str(tmp / "frames" / "frame-000.jpg"), numpy.full((400, 545, 3), 255, dtype=numpy.uint8))
    saved = apple_text_service.find_words_in_image
    apple_text_service.find_words_in_image = lambda image: [{"text": "AcmeSync" if image.shape[1] == 545 else "AcrneSync"}]
    try:
        read = item_validator._read_second_words(paths, {"path": "frames/frame-000.jpg"})
    finally:
        apple_text_service.find_words_in_image = saved
    harness.check("the word read at the frame's own size counts", "acmesync" in read, str(read))


def test_a_plain_word_only_an_outline_quotes_is_no_screen_spelling_defect():
    # Defect: a heard "Gatewayz" was written as "Gateway", a plain word an outline quoted, and a reader's misreading
    # was taken over a name read right elsewhere. Fix: a spelling only quoted is a name by more than its capital
    from validators import item_validator
    line = item_validator._use_screen_spelling("Run the Linterz first.", {}, {"lintera": "Lintera"}, {"linterz"}, set())
    harness.check("a quoted plain word is no spelling", line == "Run the Linterz first.", line)
    named = item_validator._use_screen_spelling("Run the AcmeSynk first.", {}, {"acmesync": "AcmeSync"}, {"acmesynk"}, set())
    harness.check("a quoted name still is", named == "Run the AcmeSync first.", named)


def test_a_section_card_with_its_year_named_twice_and_a_page_number_needs_no_fact_defect():
    # A section card, a year and one line, was refused for empty facts as a screen of 4 texts: the year counted twice
    # (once as a place) and the page number once. Each text counts once now, and a page number not at all
    from validators import item_validator
    from models.validation_report import ValidationReport

    card = ('- slide: dark background\n  - text "2010": centre\n  - text "Years went by...": below "2010"\n'
            '  - number "41": bottom right corner')
    report = ValidationReport(subject="frame-012")
    item_validator._check_facts(report, "frame-012", {"facts": [], "screen": card}, spoken_words=30)
    harness.check("a section card is not refused", "FRAME_FACTS_MISSING" not in harness.codes(report),
                  str(report.findings))
    slide = card + '\n  - text "Gateway refunds settle nightly"\n  - text "AcmeSync retries twice"'
    report = ValidationReport(subject="frame-013")
    item_validator._check_facts(report, "frame-013", {"facts": [], "screen": slide}, spoken_words=30)
    harness.check("a slide of three texts is still refused", "FRAME_FACTS_MISSING" in harness.codes(report),
                  str(report.findings))


def test_a_name_the_second_reader_reads_as_heard_is_not_respelled_defect():
    # A terminal's title read one letter off by the first reader, and quoted so in the outline, was written into the
    # facts as the screen's spelling; a name the second reader reads as heard on that frame keeps its spelling now
    from validators import item_validator

    read = {"gatewey": "Gatewey"}
    line = "The Gateway relays refunds."
    kept = item_validator._use_screen_spelling(line, read, {}, {"gateway"}, set(), lambda key, spelling: True)
    changed = item_validator._use_screen_spelling(line, read, {}, {"gateway"}, set(), lambda key, spelling: False)
    harness.check("a name the second reader reads as heard is kept", kept == line, kept)
    harness.check("without that reading the screen spelling is written", "Gatewey" in changed, changed)


def test_a_take_away_quote_no_one_said_loses_its_quote_marks_defect():
    # a take-away put the worker's paraphrase in speech quotes, which could reach the document as the speaker's
    # words; now a quote said over no frame near it is written without quote marks, its words kept
    paths = fx.make_paths()
    items = [{"slug": "frame-001", "index": 1, "transcriptCurrent": "The Gateway retries three times."},
             {"slug": "frame-002", "index": 2,
              "transcriptCurrent": "Running a browser per window is not exactly what your laptop battery signed up for."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    said = '- "not exactly what your laptop battery signed up for" → the list: battery cost'
    made_up = '- "drains laptop batteries quickly" → the list: battery cost'
    record = item_validator.drop_quote_marks_of_unsaid_words(paths, "frame-002", {**RECORD, "take_aways": [said, made_up]})
    harness.check("the said quote keeps its marks, the made-up one loses them",
                  record["take_aways"] == [said, '- drains laptop batteries quickly → the list: battery cost'],
                  str(record["take_aways"]))


def test_a_quote_stitched_with_a_piece_no_one_said_loses_its_quote_marks_defect():
    # Defect: a take-away stitched said words and a slide's item into one speech quote, "... or max ... and Min", the
    # speaker having said "and so on" (froscon 3147 frame 156). Fix: a stitched quote lacking a piece loses its marks.
    paths = fx.make_paths()
    items = [{"slug": "frame-001", "index": 1, "transcriptCurrent": "The Gateway retries three times."},
             {"slug": "frame-002", "index": 2,
              "transcriptCurrent": "We have a retry limit, which caps the calls, or a timeout, and so on."}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    stitched = '- "we have a retry limit... or a timeout... and backoff" → the list: three settings'
    said = '- "we have a retry limit... or a timeout... and so on" → the list: three settings'
    record = item_validator.drop_quote_marks_of_unsaid_words(paths, "frame-002", {**RECORD, "take_aways": [stitched, said]})
    harness.check("the quote with a piece no one said loses its marks, the said one keeps them",
                  record["take_aways"] == ['- we have a retry limit... or a timeout... and backoff → the list: three settings', said],
                  str(record["take_aways"]))


def test_a_take_away_naming_the_wrong_side_takes_the_outline_s_side_defect():
    # a take-away called a left column's bullet the right column's item, beside an outline holding it on the left;
    # the line is now written with the side the outline places the item under, and a right side stays
    screen = "\n".join(['- slide: two columns', '  - left column: dark', '    - bullet "Retry Budget": a gear icon',
                        '  - right column: light', '    - bullet "Audit Log": a book icon'])
    wrong = '- "it retries three times" → the right column item "Retry Budget": the Gateway retries'
    right = '- "every call is logged" → the right column item "Audit Log": AcmeSync logs each call'
    record = item_validator.use_outline_side_in_take_aways({**RECORD, "screen": screen, "take_aways": [wrong, right]})
    harness.check("the wrong side is the outline's, the right one kept",
                  record["take_aways"] == [wrong.replace("the right column", "the left column"), right],
                  str(record["take_aways"]))


def test_a_fact_only_remarking_on_the_notes_is_dropped_defect():
    # a first pass wrote "(No new facts.)" as a fact of a repeated frame; a fact or take-away only remarking on the notes
    # is now dropped as the notes are written, while a fact naming notes as its subject stays
    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-003", "index": 3}, {"slug": "frame-005", "index": 5, "repeatOf": "frame-003"}]})
    record = item_validator.drop_remarks_about_notes(paths, "frame-003", {**RECORD, "facts": [
        "(No new facts.)", "The release notes list three fixes for the Gateway."]})
    harness.check("the remark goes, the fact about release notes stays",
                  record["facts"] == ["The release notes list three fixes for the Gateway."], str(record["facts"]))
    remark = "(No new facts; screen is identical to frame 3. Speech reinforces the details.)"
    harness.check("a first showing's lone remark stays, since it would be refused with no fact",
                  item_validator.drop_remarks_about_notes(paths, "frame-003", {**RECORD, "facts": [remark]})["facts"]
                  == [remark], "")


def test_a_repeat_s_lone_remark_on_its_notes_is_dropped_defect():
    # A frame shown again kept "(No new facts; screen is identical to frame 29 ...)" as its only fact, since a remark went
    # only while another fact remained. A repeat passes with no fact, so its lone remark goes now
    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-003", "index": 3}, {"slug": "frame-005", "index": 5, "repeatOf": "frame-003"}]})
    remark = "(No new facts; screen is identical to frame 3. Speech reinforces the details.)"
    written = item_validator.drop_remarks_about_notes(paths, "frame-005", {**RECORD, "facts": [remark]})
    harness.check("the repeat's facts are empty", written["facts"] == [], str(written["facts"]))


def test_a_bar_the_reader_read_for_the_word_i_is_written_i_defect():
    # Tesseract read a comment's "I don't use Rust" as "| don't use Rust", and the notes quoted the bar (DEFECTS 336).
    # A lone bar before a word that follows I is written I; a table's bars stay
    harness.check("a bar before don't and have is I",
                  item_validator._fix_bar_pronoun("the Gateway (for good reasons), | don't use it and | have not")
                  == "the Gateway (for good reasons), I don't use it and I have not")
    harness.check("a table row keeps its bars", item_validator._fix_bar_pronoun("ledger | 16.4 KB | 0 -") == "ledger | 16.4 KB | 0 -")
    harness.check("two lower-case cells keep their bar", item_validator._fix_bar_pronoun("owner | value") == "owner | value")


def test_a_fact_only_reporting_the_speaker_is_written_as_its_clause_defect():
    # Facts "The presenter states that ..." reached documents as sentences about the talk (DEFECTS 267); the clause is
    # the fact, unless it speaks of the speaker
    record = {"facts": ["The presenter states that the Gateway retries a token twice.",
                        "The speaker notes that we need to restart AcmeSync by hand.",
                        "The presenter explains the queue Jonas owns."]}
    harness.check("the reported clause stands alone; one speaking of the speaker, and one with no clause, stay",
                  item_validator.unwrap_speaker_facts(record)["facts"] == [
                      "The Gateway retries a token twice.", "The speaker notes that we need to restart AcmeSync by hand.",
                      "The presenter explains the queue Jonas owns."], str(item_validator.unwrap_speaker_facts(record)))


def test_a_fact_only_telling_the_page_number_is_dropped_defect():
    # Facts "The page number updates to <n>." reached the notes, form and no knowledge (DEFECTS 346); they go as the
    # notes are written when another fact stays, and a longer fact mentioning a page number stays
    record = {"facts": ['The page number updates to "15".', "The Gateway retries a token twice.",
                        "The Gateway's docs list each setting with its page number, from the retry limit to the queue size."]}
    harness.check("the page-number fact goes, the others stay", item_validator.drop_page_number_facts(record)["facts"] == [
        "The Gateway retries a token twice.",
        "The Gateway's docs list each setting with its page number, from the retry limit to the queue size."])
    harness.check("a lone page-number fact stays, the record keeping one fact",
                  item_validator.drop_page_number_facts({"facts": ['The page number is "3".']})["facts"] == ['The page number is "3".'])


def test_an_outline_gets_the_whole_lines_apple_s_recognizer_reads_that_tesseract_confirms_defect():
    # Defect: a 30-line commit log outlined as its first and last line got only Tesseract's fragments ("fix; typo")
    # added (DEFECTS 352). Fix: Apple's whole lines are added too, where Tesseract's reading of the frame confirms them.
    import types
    from unittest import mock

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg"}]})
    fragments = ["feat backend convenience methods", "gateway retries refunds twice", "fix small tweaks gateway",
                 "docs examples paragraph update"]
    items = [types.SimpleNamespace(kind="text", lines=fragments)]
    apple = ["feat(backend): Add convenience methods for setting up terminals", "wold nespoll_goil strat ratpoli mrp"]
    outline = '- terminal: the whole frame\n  - line "chore: release 0.27"'
    with mock.patch.object(item_validator, "list_screen_text_items", lambda _paths, _slug: items), \
            mock.patch.object(item_validator, "_list_recognized_lines", lambda _paths, _slug: apple):
        completed = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
    harness.check("the whole line Tesseract confirms is added", apple[0] in completed, completed[-400:])
    harness.check("the garbled one it reads nothing of is not", "nespoll" not in completed, completed[-400:])
    unread = [types.SimpleNamespace(kind="text", lines=["two words"])]
    with mock.patch.object(item_validator, "list_screen_text_items", lambda _paths, _slug: unread), \
            mock.patch.object(item_validator, "_list_recognized_lines", lambda _paths, _slug: apple):
        bare = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
    harness.check("on a frame Tesseract cannot read, nothing of Apple's is added", apple[0] not in bare, bare[-300:])


def test_a_quoted_word_the_reading_spells_otherwise_at_its_place_is_named_defect():
    # Defect: a repair quoted the screen's own typo corrected ("script" for its "scirpt") and misread "argparse"; a
    # single-word check could not tell, since the reading holds both spellings elsewhere (DEFECTS 573). Fix: each quote
    # is aligned word by word with its reading line, and a word a letter or two off the read word there is named
    words = "Turn the scirpt I wrote into a program Use argparse for parsing".split()
    item = {"screenWords": [{"text": word, "line": 1} for word in words]
            + [{"text": word, "line": 2} for word in "the script runs nightly".split()]}
    screen = ('- terminal: the whole frame\n'
              '  - line "Turn the script I wrote into a program Use argpase for parsing"\n'
              '  - line "the script runs nightly"')
    harness.check("the corrected typo and the misread are named, the right line is not",
                  item_validator.list_reading_spellings(item, screen) == [("script", "scirpt"), ("argpase", "argparse")],
                  str(item_validator.list_reading_spellings(item, screen)))
    harness.check("a frame with no stored reading names nothing",
                  item_validator.list_reading_spellings({}, screen) == [], "")


def test_an_outline_line_copied_from_a_nearby_frame_s_screen_is_named_defect():
    # Defect: a frame showing a page scrolled to its end outlined the command and the page's top that only a later frame
    # shows, and passed every check, its other lines being its own (Lecture 7's frame 131, DEFECTS 574). Fix: a line
    # quoting 4 long words, none read on its frame and most read on a frame within 8, names the outline another screen's
    paths = fx.make_paths()
    own = ["Refund limits per merchant daily", "Gateway retries refunds twice before failing",
           "Jonas reviews every escalated refund manually", "Token expires after one hour exactly"]
    later = ["Fetch service status summary quickly", "Received payload okay from server",
             "Course schedule listing weekly topics", "Location building second floor"]
    readings = {"frame-005": own, "frame-006": later,
                "frame-030": ["Export ledger nightly backups", *later[1:]]}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": slug, "index": int(slug[-3:]), "path": f"frames/{slug}.jpg",
         "screenWords": [{"text": line} for line in lines]} for slug, lines in readings.items()]})
    outline = ('- terminal: the whole frame\n  - line "Refund limits per merchant daily"\n'
               '  - input "Fetch service status summary quickly"')
    named = item_validator.list_borrowed_outline_lines(paths, "frame-005", {"screen": outline})
    far = item_validator.list_borrowed_outline_lines(paths, "frame-005", {
        "screen": outline.replace("Fetch service status summary quickly", "Export ledger nightly backups")})
    asked = item_validator.is_outline_of_another_screen(paths, "frame-005", {"screen": outline})
    harness.check("the line the next frame shows is named, the frame's own line is not",
                  named == ['- input "Fetch service status summary quickly"'], str(named))
    harness.check("a line only a frame 25 away reads is not named", far == [], str(far))
    harness.check("the frame is asked again", asked, str(asked))


def test_a_table_border_read_beside_a_cell_is_left_out_of_the_added_line_defect():
    # Defect: the text reader read a table's left border as a bar, and the program added "| Debugging and Profiling" to
    # the outline, which the critique named a misread (Lecture 7's frame 136, DEFECTS 575). Fix: a bar at a read line's
    # start or end is left out; one inside the line stays
    import types
    from unittest import mock

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg"}]})
    read = ["| Gateway retries refunds twice", "Refund limits per merchant |", "grep refund | sort the lines",
            "Jonas reviews every refund", "the token expires after an hour"]
    items = [types.SimpleNamespace(kind="text", lines=read)]
    outline = '- terminal: the whole frame\n  - line "chore: release 0.27"'
    with mock.patch.object(item_validator, "list_screen_text_items", lambda _paths, _slug: items), \
            mock.patch.object(item_validator, "_list_recognized_lines", lambda _paths, _slug: []):
        completed = item_validator.add_unquoted_screen_lines(paths, "frame-005", {**RECORD, "screen": outline})["screen"]
    harness.check("the border at a line's start and end is gone",
                  '- text "Gateway retries refunds twice"' in completed
                  and '- text "Refund limits per merchant"' in completed, completed[-400:])
    harness.check("a bar inside a line stays", '- text "grep refund | sort the lines"' in completed, completed[-400:])



def test_a_table_word_spelled_unlike_the_frame_s_outline_is_named_defect():
    # A frame's table wrote a long word four letters off its own outline's, and nothing compared the two parts. Now each
    # such pair is named for the critique, which reads the image; a word the outline lacks altogether is not
    record = {"screen": '- panel "About": the row "Description" reads "Refundmanagementsoftware"',
              "as_markdown": "| Description | Refundmanagerialsoftware |\n| Version | Gatewaybuildnumber |"}
    harness.check("the near pair is named, the unrelated word is not",
                  item_validator.list_table_outline_spellings(record)
                  == [("Refundmanagerialsoftware", "refundmanagementsoftware")],
                  str(item_validator.list_table_outline_spellings(record)))


def test_a_question_the_notes_call_rhetorical_is_no_question_asked_defect():
    # A presenter's framing question, labelled rhetorical in its own line, stayed a question asked and became an open
    # point the talk answers. Now the line leaves; a question asked by someone else stays
    record = {"questions_asked": ['"do we need all these arrows for a refund form": rhetorical question by the presenter.',
                                  'Jonas asks whether the Gateway token expires → answered: after one hour']}
    harness.check("only the rhetorical line leaves",
                  item_validator.drop_rhetorical_questions(record)["questions_asked"]
                  == ['Jonas asks whether the Gateway token expires → answered: after one hour'], "")


def test_the_program_s_own_outline_of_reader_lines_reads_back_whole_defect():
    # The program replaced an outline pointing at another frame with the frame's reader lines under "- screen: the
    # text the program read ...", and reading the notes back dropped that part as a summary line, so the program
    # refused the outline it wrote. A labelled line with parts under it is a part now; a lone summary line still goes,
    # since a second look once added one from a misread critique
    outline = f'{item_validator.POINTED_OUTLINE_PART}\n  - text "Gateway limits"\n  - text "AcmeSync retries"'
    answer = f"# Frame 4 · slide\n\n## Screen\n{outline}\n\n## Tags\nGateway\n"
    screen = frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"][0]["screen"]
    harness.check("the program's part reads back whole and is an outline",
                  screen == outline and not frame_markdown_utils.read_screen_parts(screen).problems, repr(screen))
    summary = "# Frame 4 · slide\n\n## Screen\n- slide: white\n  - title \"Gateway\"\n- Screen: a slide about the gateway\n\n## Tags\nGateway\n"
    harness.check("a summary line with nothing under it still goes",
                  "a slide about" not in frame_markdown_utils.parse_answer(summary, is_addition=False)["frames"][0]["screen"])


def test_a_fenced_empty_mark_reads_as_no_diagram_defect():
    # A frame with no flow wrote the empty mark inside a mermaid fence, and the diagram check read it as the mark with
    # a reason after it, refusing a batch of four. A fence holding only the mark now reads as no diagram
    answer = "# Frame 4 · slide\n\n## Diagram\n```mermaid\n(none)\n```\n\n## Tags\nGateway\n"
    record = frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"][0]
    report = ValidationReport("frame-004")
    item_validator._check_diagram(report, "frame-004", record)
    harness.check("the diagram is empty and no diagram finding",
                  not record.get("diagram") and "FRAME_DIAGRAM_NOT_MERMAID" not in str(report.findings),
                  repr(record.get("diagram")) + str(report.findings)[:200])
