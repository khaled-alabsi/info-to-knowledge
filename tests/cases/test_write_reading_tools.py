"""The tools phase 4's agent reads more of the meeting with. REQUIREMENTS.md R12.

The unit a lookup returns is the moment, because tags are set per frame: an entry "tagged X" is an entry
of a moment tagged X. Every read result starts with a short id the agent can name, and every read counts
against one budget for the whole phase. Notes are kept on disk, within a size limit.
"""

from support import fixtures as fx
from support import harness

from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from services import document_file_service
from services import write_reading_service

ENTRIES = [
    {"id": "E001", "kind": "facts", "text": "A refund can be split into at most three parts.", "timestamps": [10.0],
     "frames": [0], "tags": ["refund split limit", "reference"]},
    {"id": "E002", "kind": "facts", "text": "Max. 3 parts", "timestamps": [10.0], "frames": [0],
     "tags": ["refund split limit", "reference"]},
    {"id": "E003", "kind": "facts", "text": "Each part is booked as its own payment.", "timestamps": [70.0],
     "frames": [1], "tags": ["refund booking", "procedure"]},
    {"id": "E004", "kind": "facts", "text": "The team decided to keep the limit at three.", "timestamps": [130.0],
     "frames": [2], "tags": ["refund split limit", "decision"]},
    {"id": "E005", "kind": "speech", "text": "Any questions on refunds?", "timestamps": [140.0], "start": 140.0,
     "end": 142.0}]
ITEMS = {0: {"slug": "frame-000", "index": 0, "timestamp": 10.0, "path": "frames/frame-000.jpg"},
         1: {"slug": "frame-001", "index": 1, "timestamp": 70.0, "path": "frames/frame-001.jpg"},
         2: {"slug": "frame-002", "index": 2, "timestamp": 130.0, "path": "frames/frame-002.jpg"}}
RECORDS = {1: {"frame": 1, "screen_kind": "web application", "facts": ["Each part is booked as its own payment."]}}


TRANSCRIPT = [
    {"id": "E010", "kind": "speech", "text": "Refunds can be split, at most three times.", "start": 12.0, "end": 15.0},
    {"id": "E011", "kind": "speech", "text": "Each split is its own payment.", "start": 72.0, "end": 75.0},
    {"id": "E005", "kind": "speech", "text": "Any questions on refunds?", "start": 140.0, "end": 142.0}]


def _build_context(paths,
                   read_chars: int = 4000,
                   read_budget: int = 10) -> WriteReadingContext:
    return WriteReadingContext(paths=paths, moments=write_reading_service.build_reading_moments(ENTRIES),
                               point_starts=[0.0, 60.0, 120.0], frame_items=ITEMS, frame_records=RECORDS,
                               read_chars=read_chars, notes_chars=120, read_budget=read_budget, transcript=TRANSCRIPT)


def _read(context,
          state,
          name: str,
          **args) -> str:
    return write_reading_service.run_read(context, state, name, args).text


def test_moments_group_entries_by_their_first_time_and_carry_their_labels():
    moments = write_reading_service.build_reading_moments(ENTRIES)
    harness.check("four moments, one per first time", [moment["time"] for moment in moments] == [10.0, 70.0, 130.0, 140.0])
    harness.check("a moment's tags are the union of its entries'", moments[0]["tags"] == ["refund split limit", "reference"]
                  and "topics" not in moments[0])
    harness.check("speech with no frame is a moment with no tags", moments[3]["tags"] == [] and moments[3]["frames"] == [])


def test_list_tags_counts_per_step_sorted_alphabetically():
    context, state = _build_context(fx.make_paths()), WriteReadingState()
    text = _read(context, state, "list_tags")
    harness.check("the result starts with its id", text.startswith("[r1]"), text)
    harness.check("each tag with its moments per step", "decision: chapter 3 (1)" in text and "reference: chapter 1 (1)" in text,
                  text)
    harness.check("a subject tag is counted in every step it occurs in",
                  "refund split limit: chapter 1 (1), chapter 3 (1)" in text, text)
    harness.check("alphabetical", text.index("refund booking") < text.index("refund split limit"), text)
    harness.check("one step only", "refund split limit" not in _read(context, state, "list_tags", chapter=2))
    harness.check("there is no topic tool", "no tool called list_topics" in _read(context, state, "list_topics"))


def test_read_moments_by_tag_returns_whole_moments():
    context, state = _build_context(fx.make_paths()), WriteReadingState()
    text = _read(context, state, "read_moments", tag="Refund Split Limit")
    harness.check("every entry of each matching moment, on-screen text included", "E001" in text and "E002" in text
                  and "E004" in text, text)
    harness.check("not the other subject's moment, and case is ignored", "E003" not in text, text)
    tagged = _read(context, state, "read_moments", tag="decision")
    harness.check("a tag narrows to its moments", "E004" in tagged and "E001" not in tagged, tagged)
    by_id = _read(context, state, "read_moments", ids=["E003"])
    harness.check("ids return the moment that holds them", "E003" in by_id and "refund booking" in by_id, by_id)


def test_a_long_result_comes_a_page_at_a_time():
    context, state = _build_context(fx.make_paths(), read_chars=150), WriteReadingState()
    first = _read(context, state, "read_moments", tag="refund split limit")
    second = _read(context, state, "read_moments", tag="refund split limit", chunk=2)
    harness.check("page 1 holds the first moment, and says there is more", "E001" in first and "E004" not in first
                  and "chunk 2" in first, first)
    harness.check("page 2 the next", "E004" in second and "E001" not in second, second)


def test_read_notes_pages_the_notes_file_a_step_at_a_time():
    """The table of contents reads the meeting in full, step by step, in the notes file's own sections."""

    context = WriteReadingContext(paths=fx.make_paths(), moments=[], point_starts=[0.0, 60.0, 120.0], frame_items={},
                                  frame_records={}, read_chars=4000, notes_chars=120, read_budget=10,
                                  note_sections=[(10.0, "## F0 · 00:10\nWhat happened: the limit is shown"),
                                                 (70.0, "## F1 · 01:10\nWhat happened: a refund is booked"),
                                                 (130.0, "## F2 · 02:10\nWhat happened: the limit is kept")])
    state = WriteReadingState()
    step_two = _read(context, state, "read_notes", chapter=2)
    harness.check("one step holds only its own sections", "## F1" in step_two and "## F0" not in step_two
                  and "## F2" not in step_two, step_two)
    whole = _read(context, state, "read_notes")
    harness.check("with no step, the whole meeting in order", whole.index("## F0") < whole.index("## F1")
                  < whole.index("## F2"), whole)
    harness.check("a step with no section says so", "No section" in _read(context, state, "read_notes", chapter=9))
    harness.check("each read counts against the budget", state.reads_used == 3, str(state.reads_used))


def test_find_needs_every_word_and_ignores_case():
    text = _read(_build_context(fx.make_paths()), WriteReadingState(), "find", words="REFUND split")
    harness.check("the entry with both words", "E001" in text, text)
    harness.check("not the ones with one of them", "E003" not in text and "E005" not in text, text)


def test_read_frame_returns_the_frame_and_the_record():
    paths = fx.make_paths()
    result = write_reading_service.run_read(_build_context(paths), WriteReadingState(), "read_frame", {"frame": 1})
    harness.check("the frame's frame", result.frame_path == paths.output_root / "frames/frame-001.jpg",
                  str(result.frame_path))
    harness.check("and its record", "web application" in result.text, result.text)
    missing = write_reading_service.run_read(_build_context(paths), WriteReadingState(), "read_frame", {"frame": 9})
    harness.check("an unknown frame is refused by name", missing.frame_path is None and "no frame 9" in missing.text,
                  missing.text)


def test_the_read_budget_is_one_for_the_whole_phase():
    context, state = _build_context(fx.make_paths(), read_budget=2), WriteReadingState()
    _read(context, state, "list_batches")
    _read(context, state, "find", words="refund")
    spent = _read(context, state, "list_tags")
    harness.check("the third read is refused and says to carry on", "budget" in spent and "carry on" in spent, spent)
    harness.check("a note is not a read", "noted" in _read(context, state, "note", text="Limits are in T01.1.").casefold())


def test_notes_are_kept_on_disk_within_their_limit():
    paths = fx.make_paths()
    context, state = _build_context(paths), WriteReadingState()
    _read(context, state, "note", text="The refund limit is explained in T01.1.")
    harness.check("a note is written to disk", document_file_service.read_agent_notes(paths) == [
        "The refund limit is explained in T01.1."])
    refused = _read(context, state, "note", text="x" * 200)
    harness.check("a note that does not fit is refused with the list", "does not fit" in refused
                  and "1. The refund limit" in refused, refused)
    _read(context, state, "note", text="Booking is explained in T02.1.", replaces=1)
    harness.check("replaces swaps a note", document_file_service.read_agent_notes(paths) == ["Booking is explained in T02.1."])


def test_a_section_is_told_where_its_subjects_also_occur():
    context = _build_context(fx.make_paths())
    text = write_reading_service.describe_tags_elsewhere(context, ["E001"])
    harness.check("the section's subject, the other step it occurs in, with its moments and kinds",
                  "refund split limit also occurs in chapter 3 (1 moment, tagged decision)" in text, text)
    harness.check("never the section's own chapter", "chapter 1" not in text, text)
    harness.check("a subject nowhere else is not listed, and a kind is never a subject",
                  write_reading_service.describe_tags_elsewhere(context, ["E003"]) == "", "")


def test_the_transcript_is_read_whole_by_time_range_or_by_words():
    """The agent reads what was actually said, including the lines a frame heard, which no moment holds."""

    context, state = _build_context(fx.make_paths()), WriteReadingState()
    whole = _read(context, state, "read_transcript")
    harness.check("every line, in order, with its id and time", whole.startswith("[r1]")
                  and whole.index("E010 00:12") < whole.index("E011 01:12") < whole.index("E005 02:20"), whole)
    ranged = _read(context, state, "read_transcript", start="01:00", end="2:00")
    harness.check("a time range keeps only the lines said in it", "E011" in ranged and "E010" not in ranged
                  and "E005" not in ranged, ranged)
    found = _read(context, state, "read_transcript", words="SPLIT payment")
    harness.check("words keep only the lines holding all of them", "E011" in found and "E010" not in found, found)
    harness.check("each read spends one of the phase's reads", state.reads_used == 3, str(state.reads_used))


def test_a_transcript_line_names_its_speaker_when_phase_1_placed_one():
    """The writer read who said what in the notes, but the transcript tool printed each line with no speaker."""

    spoken = [{**line, "speaker": "Speaker B"} if line["id"] == "E010" else line for line in TRANSCRIPT]
    context = WriteReadingContext(paths=fx.make_paths(), moments=write_reading_service.build_reading_moments(ENTRIES),
                                  point_starts=[0.0, 60.0, 120.0], frame_items=ITEMS, frame_records=RECORDS,
                                  read_chars=4000, notes_chars=120, read_budget=10, transcript=spoken)
    whole = _read(context, WriteReadingState(), "read_transcript")
    harness.check("a line with a speaker names it after its time; a line with none is as before",
                  "E010 00:12 Speaker B: Refunds can be split" in whole and "E011 01:12 Speaker" not in whole, whole)
