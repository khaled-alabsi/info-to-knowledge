"""No entry from phase 3 is lost on its way to the notes and to the document (the owner, 2026-09-29: "we are not doing that for fun").

Phase 3 leaves each frame's notes in `02_frames.json` and each batch's summary in `03_batch_summaries.json`. From them the program
builds the entries the model reads. These tests are the guarantee at the two places an entry could disappear: on its way into the
notes text, and on its way to the document, where every entry must be placed in a section or set aside.
"""

from support import harness

from services import document_assembly_service
from utils import frame_merge_utils
from utils import document_plan_utils
from utils import meeting_notes_utils

from cases import test_meeting_notes as fixtures

BATCH = {"frames": [0, 1], "start": 8.0, "end": 30.0, "source": "batch",
         "summary": "The presenter explains the parts of a token and who rotates its signing key.",
         "atmosphere": "Plain, nobody speaks but the presenter.",
         "openQuestions": ["Who rotates the signing key?"], "answeredQuestions": ["What is in a token? → A header, a payload and a signature."]}


def _flatten(value) -> list[str]:
    if value is None or value == "" or value == [] or value == {}:
        return []
    if isinstance(value, (list, tuple)):
        return [text for element in value for text in _flatten(element)]
    if isinstance(value, dict):
        return [text for element in value.values() for text in _flatten(element)]

    return [" ".join(str(value).split())]


def _merge() -> dict:
    merged = frame_merge_utils.merge_frame_records(fixtures.TRANSCRIPT, fixtures.FRAMES, fixtures.RECORDS, {})
    merged["batchSummaries"] = [BATCH]

    return merged


def test_every_entry_of_the_merge_is_in_the_notes_text():
    merged = _merge()
    text = " ".join(meeting_notes_utils.render_meeting_notes("portal.mp4", merged).split())
    lost = [entry["id"] for entry in merged["entries"] if " ".join(str(entry.get("text") or "").split()) not in text]
    harness.check(f"all {len(merged['entries'])} entries are in the notes text", lost == [], f"lost: {lost}")


def test_every_text_a_frame_record_holds_is_in_the_notes_text():
    merged = _merge()
    text = " ".join(meeting_notes_utils.render_meeting_notes("portal.mp4", merged).split())
    skipped = {"slug", "index", "timestamp", "frame"}
    lost = [f"{record['slug']}.{field}: {piece[:50]}" for record in fixtures.RECORDS for field, value in record.items()
            if field not in skipped for piece in _flatten(value) if piece not in text]
    harness.check("every text of every frame record is in the notes text", lost == [], "; ".join(lost))


def test_every_part_of_a_batch_summary_is_in_the_notes_text():
    text = " ".join(meeting_notes_utils.render_meeting_notes("portal.mp4", _merge()).split())
    lost = [piece[:50] for key in ("summary", "atmosphere", "openQuestions", "answeredQuestions")
            for piece in _flatten(BATCH[key]) if piece not in text]
    harness.check("the story, the atmosphere, the questions and the answers are in the notes text", lost == [], "; ".join(lost))


def test_every_entry_is_accounted_for_when_the_document_is_built():
    merged = _merge()
    ids = [entry["id"] for entry in merged["entries"]]
    cited, set_aside = set(ids[:1]), set(ids[1:2])
    statuses = document_plan_utils.list_entry_statuses(merged, cited, set_aside)
    harness.check("each entry has a status: none is missing from the coverage table", set(statuses) == set(ids),
                  f"missing: {sorted(set(ids) - set(statuses))}")
    harness.check("and none has no status", all(statuses.get(entry_id) for entry_id in ids), str(statuses))


def test_a_screen_block_written_from_another_note_is_not_counted_missing_defect():
    # Defect: a screen shown twice had its block written once, and the other showing's note was counted missing,
    # though the chapter request took it off the section's list. Fix: a note whose block the document holds is provenance
    notes = [{"id": "F3.2", "kind": "markdown", "text": "```bash\ngateway sync --all\nsynced 12 tenants\n```"},
             {"id": "F7.4", "kind": "markdown", "text": "```bash\ngateway purge --dry-run\nwould purge 4 tenants\n```"}]
    written = ["```bash\ngateway sync --all\nsynced 12 tenants\n```\n[F2.5]"]
    held = document_assembly_service.list_held_screen_ids(notes, set(), written)
    statuses = document_plan_utils.list_entry_statuses({"entries": notes}, set(), set(), held)
    harness.check("the block written from another note is provenance, the block written nowhere missing",
                  statuses == {"F3.2": "provenance", "F7.4": "missing"}, str(statuses))


def test_every_kind_the_merge_can_make_is_decided_so_a_new_kind_cannot_be_dropped_unseen():
    """Dynamic (the owner, 2026-09-29): the kinds are read from the merge's own list, so a kind added tomorrow fails here until it is
    handed to the writer and printed in the notes. A note left out on the assumption that other notes hold it was the smell."""

    kinds = set(frame_merge_utils._KIND_ORDER)
    not_handed = sorted(kinds - set(document_plan_utils.DOCUMENT_KINDS) - {"speech"})
    harness.check("every entry kind is handed to the writer as an entry, or is speech (which is joined into speech entries)",
                  not not_handed, f"kinds the writer is never handed: {not_handed}")
    not_printed = sorted(kinds - set(frame_merge_utils.NOTE_KINDS) - {"speech"})
    harness.check("every entry kind is printed in the notes file", not not_printed, f"kinds the notes never print: {not_printed}")


def test_every_entry_the_merge_makes_reaches_the_writer_whatever_the_fixture_holds():
    """Dynamic: the entries are whatever the merge of the fixture makes, not a list written here, so a new entry a frame's record
    adds is counted with no edit to this test. Each is handed over as an entry or joined into a speech entry's segments."""

    # Every kind of entry a record can make: the first record gets a web address, an action item and a question added
    records = [{**fixtures.RECORDS[0], "urls": ["https://example.test/portal"], "action_items": ["The team writes the guide."],
                "questions_asked": ["Who owns the guide?"],
                "as_markdown": "```python\nretries = 3\n```"}, *fixtures.RECORDS[1:]]
    merged = frame_merge_utils.merge_frame_records(fixtures.TRANSCRIPT, fixtures.FRAMES, records, {})
    selected = document_plan_utils.select_document_entries(merged)
    reached = {entry["id"] for entry in selected} | {segment for entry in selected for segment in entry.get("segments") or []}
    dropped = [entry["id"] for entry in merged["entries"] if entry["id"] not in reached]
    harness.check("no entry of the merge is dropped on its way to the writer", not dropped,
                  f"dropped: {dropped} of {len(merged['entries'])} entries")
    handed_kinds = {entry["kind"] for entry in merged["entries"]}
    harness.check("the fixture holds every kind the merge can make, so the check above covers them all",
                  handed_kinds >= set(frame_merge_utils._KIND_ORDER),
                  f"kinds the fixture lacks: {sorted(set(frame_merge_utils._KIND_ORDER) - handed_kinds)}")
