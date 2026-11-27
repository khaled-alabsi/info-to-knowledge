"""Three things a frame's record gains for R8 and R10: the action items and the questions asked at that moment, and
which parts of the screen a reader should be SHOWN.

Each is a section of the frame's notes (`## Action items`, `## Questions asked`, `## Screenshots`) that a frame with
none of them leaves out, so it costs no output. The merge turns the first two into entries of their own; phase 4's chapter
requests and phase 5 read the third as hints.

Since 2026-09-28 the third is `screenshots`, a list of objects (`part`, `why`, `importance`), replacing the three
parallel fields `screenshot`, `screenshot_why` and `screenshot_part` (the owner: "just could be an object or a list of
object each object contain the screenshot and the reason and maybe the part"). Two reasons: one screen often holds two
parts worth showing, which one triple cannot say, and `screenshot: "no"` beside a filled reason said two things at
once. An empty list is the whole answer for a frame that needs no frame, and every run before that date wrote the
old shape, which `utils/screenshot_suggestion_utils.py` still reads.
"""

from support import fixtures as fx
from support import harness

from pathlib import Path

from services import call_log_service
from services import record_file_service
from utils import frame_markdown_utils
from utils import screenshot_suggestion_utils
from validators import item_validator

RECORD = {"frame": 5, "screen_kind": "web application",
          "facts": ["A refund can be split into at most three parts",
                        "Each part is booked as its own payment in the books"],
          "diagram": "", "screen": '- slide: white, the whole frame\n  - text "Refund": centre\n  - text "Split into parts": centre\n  - text "Max. 3": centre',
          "speech_visual_alignment": "the speech says why the limit is three: the payment provider allows no more",
          "builds_on": [], "contradicts_earlier": [], "open_questions": [],
          "tags": ["refund policy", "reference"],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [],
          "third_person_description": "The presenter points at the figure on the slide and reads it out."}
ANSWERED = {"outcome": "completed", "parsed": RECORD, "text": "", "thinking": "", "finishReason": "stop",
            "parsingError": ""}
SHOT = {"part": "the refund form on the right",
        "why": "the form shows where the split is entered, which the text cannot place", "importance": "needed"}
SECOND_SHOT = {"part": "the red error line under the form", "why": "it names the message a reader will see",
               "importance": "helps"}
SUGGESTED = {**RECORD, "action_items": ["Finance checks the split limit with the payment provider by Friday"],
             "questions_asked": ["Can a refund be split after it is booked? Unanswered."],
             "screenshots": [SHOT]}
# The shape every run before 2026-09-28 wrote, which the reader must still understand.
OLD_SHAPE = {**RECORD, "screenshot": "needed", "screenshot_why": SHOT["why"], "screenshot_part": SHOT["part"]}
FRAMES_PROCESSING_INSTRUCTION = Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "frames-processing.md"


def _write_attempt(paths,
                   record: dict) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frame-005"),
                                    {"response": {**ANSWERED, "parsed": record}})
    record_file_service.write_frame_record(paths, "frame-005", record)


def _list_codes(paths) -> set[str]:
    return harness.codes(item_validator.validate_item(paths, "frame-005"))


def _list_notes(paths) -> list[str]:
    return [note.code for note in item_validator.validate_item(paths, "frame-005").notes]


def _read_back(record: dict) -> dict:
    """A record as the model would write it, read by the program."""

    return frame_markdown_utils.parse_answer(frame_markdown_utils.render_frame_document(record),
                                             is_addition=False)["frames"][0]


def test_a_section_left_out_is_an_empty_answer():
    parsed = frame_markdown_utils.parse_answer("# Frame 5 · web application\n\n## Facts\n- A fact.\n",
                                               is_addition=False)["frames"][0]
    harness.check("no section is an empty list for each", (parsed["action_items"], parsed["questions_asked"],
                                                         parsed["screenshots"], parsed["urls"]) == ([], [], [], []))


def test_a_record_with_the_three_fields_keeps_them_as_written():
    parsed = _read_back(SUGGESTED)
    harness.check("the action item is kept", parsed["action_items"] == SUGGESTED["action_items"])
    harness.check("the question and its answer are kept", parsed["questions_asked"] == SUGGESTED["questions_asked"])
    harness.check("the suggestion keeps its part, its reason and its importance", parsed["screenshots"] == [SHOT],
                  str(parsed["screenshots"]))
    two = _read_back({**SUGGESTED, "screenshots": [SHOT, SECOND_SHOT]})
    harness.check("one screen can suggest two parts, which the old three fields could not say",
                  [entry["part"] for entry in two["screenshots"]] == [SHOT["part"], SECOND_SHOT["part"]])


def test_the_frame_instruction_teaches_each_section_by_its_heading():
    text = " ".join(FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8").split())
    for heading in ("## Action items", "## Questions asked", "## Screenshots", "- importance:", "- why:"):
        harness.check(f"frames_processing.md teaches `{heading}`", heading in text)
    harness.check("and names the two words an importance may take",
                  all(f"`{word}`" in text for word in screenshot_suggestion_utils.list_importance_words()))
    harness.check("and shows a good suggestion and a bad one", "Good (invented)" in text and "Bad (invented)" in text)


def test_a_suggestion_without_a_reason_is_noted_and_the_record_still_stands():
    """The record is usable without the reason, so it is never re-asked for one (the owner, 2026-09-28: retries cost
    minutes per frame)."""

    paths = fx.make_paths()
    _write_attempt(paths, SUGGESTED)
    harness.check("a complete suggestion raises no finding", _list_codes(paths) == set(), str(_list_codes(paths)))

    _write_attempt(paths, {**SUGGESTED, "screenshots": [{**SHOT, "why": "  "}]})
    harness.check("a suggestion with no reason does not reject the record", _list_codes(paths) == set(),
                  str(_list_codes(paths)))
    harness.check("it is noted as FRAME_SCREENSHOT_UNEXPLAINED", _list_notes(paths) == ["FRAME_SCREENSHOT_UNEXPLAINED"],
                  str(_list_notes(paths)))

    _write_attempt(paths, {**SUGGESTED, "screenshots": []})
    harness.check("an empty list is a complete answer", _list_codes(paths) == set() and _list_notes(paths) == [])
    _write_attempt(paths, RECORD)
    harness.check("a record without the field is fine", _list_codes(paths) == set(), str(_list_codes(paths)))


def test_an_importance_outside_the_two_words_is_rejected():
    """This word IS the contract with phase 4 and phase 5, which read it letter for letter, so an entry they cannot
    act on is lost work rather than a style slip."""

    paths = fx.make_paths()
    for value in ("maybe", "Needed", "yes", "no"):
        _write_attempt(paths, {**SUGGESTED, "screenshots": [{**SHOT, "importance": value}]})
        harness.check(f"{value!r} is FRAME_SCREENSHOT_VALUE", _list_codes(paths) == {"FRAME_SCREENSHOT_VALUE"},
                      str(_list_codes(paths)))
    _write_attempt(paths, {**SUGGESTED, "screenshots": [{"part": "the form on the right"}]})
    harness.check("a part with no importance line is rejected too",
                  _list_codes(paths) == {"FRAME_SCREENSHOT_VALUE"}, str(_list_codes(paths)))


def test_the_reader_understands_a_record_written_before_the_field_became_a_list():
    """Every run before 2026-09-28 wrote the three parallel fields, and a run resumed across the change reads both."""

    harness.check("the old three fields read as one suggestion",
                  screenshot_suggestion_utils.read_suggestions(OLD_SHAPE)
                  == [{"part": SHOT["part"], "why": SHOT["why"], "importance": "needed"}],
                  str(screenshot_suggestion_utils.read_suggestions(OLD_SHAPE)))
    harness.check("an old record that suggested nothing reads as no suggestion",
                  screenshot_suggestion_utils.read_suggestions({**RECORD, "screenshot": "no"}) == [])
    harness.check("the new list wins where both are present",
                  screenshot_suggestion_utils.read_suggestions({**OLD_SHAPE, "screenshots": [SECOND_SHOT]})
                  == [SECOND_SHOT])


def test_phase_4_and_5_are_shown_each_suggestion_and_the_frames_a_reader_needs():
    harness.check("the line phase 4 and phase 5 are shown names the frame, the importance, the reason and the part",
                  screenshot_suggestion_utils.render_suggestion_lines([{**SUGGESTED, "slug": "frame-005"}])
                  == [f"frame-005 needed: {SHOT['why']} ({SHOT['part']})"],
                  str(screenshot_suggestion_utils.render_suggestion_lines([{**SUGGESTED, "slug": "frame-005"}])))
    harness.check("and the frames a reader cannot do without are listed by slug",
                  screenshot_suggestion_utils.list_needed_slugs(
                      [{**SUGGESTED, "slug": "frame-005"}, {**RECORD, "slug": "frame-006"},
                       {**RECORD, "slug": "frame-007", "screenshots": [SECOND_SHOT]}]) == ["frame-005"])


def test_a_code_screen_its_notes_hold_as_a_block_is_written_never_needed_as_a_picture_defect():
    # Defect: phase 3 marked an editor's code and a terminal's diff "needed", and the chapter cut them as pictures,
    # though their notes held the code whole as a fenced block (Lecture 7's chapter 1, DEFECTS 583). Fix: such a frame is
    # no needed frame, and its suggestion line says to write the block and leave the frame out
    code = {**SUGGESTED, "slug": "frame-016", "screen_kind": "code editor",
            "as_markdown": "```python\ndef refund(parts):\n    return parts[:3]\n```"}
    slide = {**SUGGESTED, "slug": "frame-017", "screen_kind": "slide", "as_markdown": "```text\nMax. 3\n```"}
    harness.check("the code screen is no needed frame, the slide still is",
                  screenshot_suggestion_utils.list_needed_slugs([code, slide]) == ["frame-017"],
                  str(screenshot_suggestion_utils.list_needed_slugs([code, slide])))
    line = screenshot_suggestion_utils.render_suggestion_lines([code])[0]
    harness.check("its line says to write the block and leave the frame out", "`leave_out` this frame as written" in line,
                  line)
