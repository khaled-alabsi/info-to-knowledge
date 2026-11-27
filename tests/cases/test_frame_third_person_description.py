"""Every frame note carries a `third_person_description` of the moment. REQUIREMENTS.md R15, part 1.

The field is required in both records the frame model answers with, checked by the program for presence and for its
word limit; first or second person outside quotation marks is noted, never refused, and a gendered pronoun is neither.
It is taught by `frames_processing.md`,
written by the program itself for a copied frame, kept on the frame's moment by the merge and shown by phase 4's
`read_moments`. A record an older run wrote without it reads as an empty description.
"""

from pathlib import Path

from support import fakes
from support import frame_batch_fixtures
from support import fixtures as fx
from support import harness

from models.attempt_request import AttemptRequest
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from prompts import frames_processing_messages
from services import call_log_service
from services import frame_records_service
from services import record_file_service
from services import write_reading_service
from services.ignore_list_service import IgnoreList
from utils import frame_merge_utils
from validators import item_validator


FRAMES_PROCESSING_INSTRUCTION = Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "frames-processing.md"
DESCRIPTION = ("The billing settings page of a test account is on screen, with the monthly order limit marked "
               "near the top. Continuing from the account overview, the presenter scrolls down to the limit, clicks "
               "into the field and raises the value, and the page shows it as unsaved. They explain that the limit "
               "caps how much a reseller may order before someone must approve it by hand, and that raising it lets "
               "a reseller place larger orders without waiting.")
RECORD = {"frame": 5, "screen_kind": "web application",
          "facts": ["Raising the monthly limit lets a reseller place larger orders without a manual approval."],
          "diagram": "", "screen": '- slide: white, the whole frame\n  - text "Monthly limit": centre', "speech_visual_alignment": "the speech gives the reason for the limit",
          "builds_on": [], "contradicts_earlier": [], "open_questions": [], "tags": ["reseller limit", "reference"],
          "third_person_description": DESCRIPTION,
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}
ANSWERED = {"outcome": "completed", "parsed": RECORD, "text": "", "thinking": "", "finishReason": "stop",
            "parsingError": ""}


def _write_attempt(paths,
                   record: dict) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "frame-005"), {"response": ANSWERED})
    record_file_service.write_frame_record(paths, "frame-005", record)


def _list_codes(paths,
                record: dict) -> set[str]:
    _write_attempt(paths, record)

    return harness.codes(item_validator.validate_item(paths, "frame-005"))


def _list_notes(paths) -> list[str]:
    return [note.code for note in item_validator.validate_item(paths, "frame-005").notes]


def test_the_instruction_tells_the_model_the_person_rules_and_the_length():
    instruction = " ".join(FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8").split())
    harness.check("frames_processing.md shows the section", "## What happens" in instruction)
    harness.check("and the person rule and the word limit", 'never "I", "we" or "you"' in instruction
                  and f"at most {item_validator._THIRD_PERSON_DESCRIPTION_MAX_WORDS} words" in instruction)
    harness.check("and it shows a good line and a bad one",
                  instruction.count("Good:") >= 2 and instruction.count("Bad:") >= 2, instruction[:0])


def test_the_instruction_states_the_batch_summary_word_limit():
    instruction = " ".join(FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8").split())
    harness.check("frames_processing.md gives the batch summary's word limit",
                  f"at most {item_validator.SUMMARY_MAX_WORDS} words" in instruction)


def test_each_third_person_description_breach_fires_its_own_code():
    paths = fx.make_paths()
    harness.check("a good third-person description passes", _list_codes(paths, RECORD) == set(), str(_list_codes(paths, RECORD)))
    harness.check("an empty one is not refused",
                  _list_codes(paths, {**RECORD, "third_person_description": " "}) == set())
    harness.check("an absent one neither", _list_codes(paths, {name: value for name, value in RECORD.items()
                                                               if name != "third_person_description"}) == set())
    limit = item_validator._THIRD_PERSON_DESCRIPTION_MAX_WORDS
    at_limit = " ".join(("The presenter points at the limit".split() * limit)[:limit]) + "."
    harness.check("a description at the word limit passes",
                  _list_codes(paths, {**RECORD, "third_person_description": at_limit}) == set())
    harness.check("one word past it is FRAME_THIRD_PERSON_DESCRIPTION_TOO_LONG",
                  _list_codes(paths, {**RECORD, "third_person_description": at_limit + " Then"}) == {"FRAME_THIRD_PERSON_DESCRIPTION_TOO_LONG"})
    # First or second person is neither refused nor noted: the person check and its warning were removed (the owner),
    # as the gendered-pronoun check was before it.
    for word, text in (("we", "We open the billing settings."), ("you", "The presenter shows where you raise the limit."),
                       ("our", "The presenter opens our billing settings."),
                       ("he", "The presenter opens the page and he points at the limit.")):
        record = {**RECORD, "third_person_description": text}
        harness.check(f"'{word}' does not reject the record", _list_codes(paths, record) == set(), str(_list_codes(paths, record)))
        harness.check(f"'{word}' is not noted either", _list_notes(paths) == [], str(_list_notes(paths)))


def test_a_frame_with_no_tags_is_noted_and_kept_so_one_cut_reply_no_longer_blocks_the_recording_defect():
    # Defect: a talk's phase 3 stopped on frame 9, whose reply the host cut before `## Tags`; the frame was
    # refused for its empty tags and, with repair off, the whole recording blocked. Fix: empty tags are a note.
    paths = fx.make_paths()
    codes = _list_codes(paths, {**RECORD, "tags": []})

    harness.check("a frame with no tags is kept, and the empty tags are noted",
                  (codes, _list_notes(paths)) == (set(), ["FRAME_TAGS_MISSING"]), f"{codes} {_list_notes(paths)}")


def test_the_single_and_the_batch_message_teach_what_happens():
    paths = fx.make_paths()
    item = {"slug": "frame-005", "index": 5, "timestamp": 30.0, "path": "frames/frame-005.jpg",
            "transcriptBefore": "", "transcriptCurrent": "the limit"}
    frame = paths.output_root / item["path"]
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"frame")
    single = AttemptRequest(job_kind="frames_processing", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                            ignore=IgnoreList(), inputs={"slug": "frame-005", "item": item, "previousRecords": []})
    group = AttemptRequest(job_kind="frame_batch", attempt=1, repository=paths.repository, module=paths.module,
                           paths=paths, ignore=IgnoreList(),
                           inputs={"slug": "frames-005-006", "items": [item, {**item, "slug": "frame-006", "index": 6}],
                                   "previousRecords": []})
    for name, messages in (("single", frames_processing_messages.build_frame_messages(single)),
                           ("group", frames_processing_messages.build_frame_batch_messages(group))):
        system_text = str(messages[0].content)
        harness.check(f"the {name} call's instruction teaches `## What happens`",
                      "- `## What happens`: this moment as ONE story" in system_text
                      and "What someone did (opened, clicked, scrolled, explained) goes under `## What happens`" in system_text,
                      system_text[:200])


def test_the_written_record_holds_the_third_person_description():
    answer = fakes.frame_notes(**{**frame_batch_fixtures.RECORD, "frame": 0, "third_person_description": DESCRIPTION})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(answer), count=1)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000"], frames_per_call=1)
    records = {entry["index"]: entry for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the frame's record keeps its description", records.get(0, {}).get("third_person_description") == DESCRIPTION,
                  str(records.get(0)))


def test_the_merge_keeps_the_third_person_description_on_the_frames_moment_and_an_old_record_passes():
    records = [{**RECORD, "frame": 0, "index": 0, "slug": "frame-000", "timestamp": 10.0},
               {**{name: value for name, value in RECORD.items() if name != "third_person_description"}, "frame": 1, "index": 1,
                "slug": "frame-001", "timestamp": 20.0}]
    merged = frame_merge_utils.merge_frame_records({"segments": []}, {"items": []}, records, {})
    frames = {frame["index"]: frame for frame in merged["frames"]}
    harness.check("the frame's moment carries its description", frames[0].get("third_person_description") == DESCRIPTION)
    harness.check("an older record without one reads as empty", frames[1].get("third_person_description") == "")
    harness.check("it never becomes an entry", not any(DESCRIPTION in entry["text"] for entry in merged["entries"]))


def test_read_moments_shows_the_third_person_description_above_the_entries():
    paths = fx.make_paths()
    entries = [{"id": "E001", "kind": "facts", "text": "Raising the limit skips the approval.",
                "timestamps": [1037.0], "frames": [77], "tags": ["order limit"]},
               {"id": "E002", "kind": "facts", "text": "Only administrators may change it.",
                "timestamps": [1100.0], "frames": [78], "tags": ["order limit"]}]
    context = WriteReadingContext(paths=paths, moments=write_reading_service.build_reading_moments(entries),
                                  point_starts=[], frame_items={}, frame_records={77: {**RECORD, "frame": 77},
                                                                                 78: {"frame": 78}},
                                  read_chars=4000, notes_chars=100, read_budget=10)
    text = write_reading_service.run_read(context, WriteReadingState(), "read_moments", {"tag": "order limit"}).text
    line = f"at 17:17, frame 77: {DESCRIPTION}"
    harness.check("the third-person description line sits above the moment's entries",
                  line in text and text.index(line) < text.index("E001"), text)
    harness.check("a moment whose record has none shows no line", "frame 78:" not in text, text)


def test_a_pdf_figure_or_page_may_have_no_what_happens():
    """Nothing happens on a page or a figure: a worker left the section out of every figure of a batch, and of a page
    read alone, though the request named it three times (pdf-04, pdf-figures-05-manual)."""

    from services import phase_file_service

    paths = fx.make_paths()
    for medium in ("pdf-figure", "pdf"):
        phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": "pdf-text-only", "droppedFrames": [],
                                                       "items": [{"slug": "frame-005", "index": 5, "medium": medium}]})
        codes = _list_codes(paths, {**RECORD, "third_person_description": ""})
        harness.check(f"a {medium} frame with none passes that check", "FRAME_THIRD_PERSON_DESCRIPTION_MISSING" not in codes,
                      str(codes))


def test_a_spoken_frame_without_narration_is_noted_and_not_refused_defect():
    # Defect: a meeting's batch was blocked on 4 frames whose notes left `## What happens` out, speech said over them.
    # Fix (hard fix): the missing narration is a note on the console, never a refusal.
    from models import output_contract
    from services import phase_file_service

    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
        {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg", "transcriptCurrent": "The Gateway caps every order."}]})
    without = {name: value for name, value in RECORD.items() if name != "third_person_description"}
    harness.check("not refused", _list_codes(paths, without) == set(), str(_list_codes(paths, without)))
    harness.check("noted", "FRAME_THIRD_PERSON_DESCRIPTION_MISSING" in _list_notes(paths), str(_list_notes(paths)))


def test_a_frame_nobody_spoke_over_may_have_no_narration_defect():
    # Defect: a slide shown in silence came back without `## What happens`, every other section written, and the batch
    # was blocked. Fix: a frame with no speech over it may leave the section out, as a PDF's page may.
    from models import output_contract
    from services import phase_file_service

    paths = fx.make_paths()
    without = {name: value for name, value in RECORD.items() if name != "third_person_description"}
    for spoken, expected in (("", set()), ("The Gateway caps every order.", set())):
        phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [
            {"slug": "frame-005", "index": 5, "path": "frames/frame-005.jpg", "transcriptCurrent": spoken}]})
        harness.check(f"no narration with speech {spoken!r} gives {expected}", _list_codes(paths, without) == expected,
                      str(_list_codes(paths, without)))
