"""The merge written as one Markdown file, `03_notes.md`, for the agents that write, illustrate and review the document.

One section per frame in the meeting's order, holding what was said while it was on screen and what it taught; a
section of its own for each span of speech no frame read. Only what a paragraph or a review cites carries a number.
"""

from support import fixtures as fx
from support import harness

from services import batch_summaries_service
from schemas import frame_record
from utils import frame_merge_utils
from utils import meeting_notes_utils

DIAGRAM = 'flowchart LR\n    A["Portal"] --> B["Token"]'
TRANSCRIPT = {"segments": [{"text": "Welcome to the portal walkthrough.", "start": 0.0, "end": 3.0},
                           {"text": "The token has three parts.", "start": 10.0, "end": 12.0},
                           {"text": "Here is the payload.", "start": 21.0, "end": 23.0}]}
FRAMES = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 8.0},
                    {"slug": "frame-001", "index": 1, "timestamp": 20.0},
                    {"slug": "frame-002", "index": 2, "timestamp": 30.0, "repeatOf": "frame-001"}]}
RECORDS = [{"slug": "frame-000", "index": 0, "timestamp": 8.0, "screen_kind": "slide",
            "facts": ["A token has a header, a payload and a signature."],
            "screen": '- slide: white, the whole frame\n  - title "Token anatomy": top\n  - text "Header, payload, signature": centre',
            "diagram": DIAGRAM, "speech_visual_alignment": "The speaker says cookie, the screen says token.",
            "open_questions": ["who rotates the signing key"]},
           {"slug": "frame-001", "index": 1, "timestamp": 20.0, "screen_kind": "slide",
            "facts": ["The payload carries the user's roles."], "screen": '- slide: white, the whole frame\n  - text "Header, payload, signature": centre',
            "tags": ["Tokens", "concept"], "screenshot": "needed",
            "screenshot_why": "the layout of the three parts is the point", "screenshot_part": "the token diagram",
            "builds_on": ["frame 0: the same token"], "contradicts_earlier": ["frame 0 named it a cookie"]}]


def _render() -> str:
    merged = frame_merge_utils.merge_frame_records(TRANSCRIPT, FRAMES, RECORDS, {})

    return meeting_notes_utils.render_meeting_notes("portal.mp4", merged)


def test_a_frame_s_line_names_it_as_the_frame_tools_take_it_defect():
    # Defect: the notes showed "File: frames/frame-000.jpg · frame number 1"; the writer cut "frame-1", the cut was
    # refused and the strict run stopped (DEFECTS 500). Fix: the line opens with the name the frame tools take, and the
    # number says it is the recording's
    lines = meeting_notes_utils._render_frame_fields({"slug": "frame-000", "path": "frames/frame-000.jpg", "frameNumber": 1})
    harness.check("the tools' name first, the recording's number labelled",
                  lines[0].startswith("Frame tools name it frame-000") and "the recording's frame number 1" in lines[0]
                  and " frame number 1" not in lines[0].replace("recording's frame number 1", ""), str(lines))


def test_every_frame_tool_asks_for_a_frame_by_its_three_digit_name_defect():
    # Defect: the frame tools' own descriptions asked for "frame-<n>", and a writer cut "frame-1" for frame-000
    # (DEFECTS 500). Fix: each names three digits, as the notes do
    from schemas import illustration_actions

    for action in (illustration_actions.LookAction, illustration_actions.CutAction, illustration_actions.LeaveOutAction,
                   illustration_actions.ReplaceWithDiagramAction):
        described = action.model_fields["frame"].description
        harness.check(f"{action.__name__} names the frame with three digits", "frame-000" in described
                      and "frame-<n>" not in described, described)


def test_a_frames_section_carries_every_field_its_record_has():
    """Everything phase 3 recorded about a frame reaches its section: its tags, whether it wants a
    screenshot and of what, what it builds on, what it contradicts, and when it is a repeat."""

    section = _render().split("## F1 ")[1].split("\n## ")[0]
    harness.check("its tags", "Tags: Tokens, concept" in section and "Topics" not in section, section)
    harness.check("the screenshot it asks for, why and of what",
                  "Screenshot: needed, of the token diagram, because the layout of the three parts is the point" in section,
                  section)
    harness.check("what it builds on", "Builds on: frame 0: the same token" in section, section)
    harness.check("what it contradicts", "- Contradicts earlier: frame 0 named it a cookie" in section, section)


def test_each_frame_is_one_section_with_its_speech_and_what_it_taught():
    notes = _render()
    harness.check("the file opens with the recording, its length and its frame count",
                  notes.startswith("# portal.mp4 · 00:23 · 2 frames"), notes[:80])
    harness.check("the speech before the first frame is a section of its own, its lines numbered",
                  "## 00:00–00:08 · speech no frame read (before the first frame)\nS0001 Welcome to the portal walkthrough."
                  in notes, notes)
    harness.check("a frame's section names its number, time, kind and the text its screen calls a title",
                  '## F0 · 00:08 · slide "Token anatomy"' in notes, notes)
    harness.check("what was said while it was on screen, with its line numbers",
                  "Said: S0002 The token has three parts." in notes, notes)
    harness.check("each fact carries the number a paragraph cites",
                  "- F0.1 A token has a header, a payload and a signature." in notes, notes)
    harness.check("the open question is numbered too", "- F0.4 Open: who rotates the signing key" in notes, notes)
    harness.check("each frame's screen is printed whole, as its outline",
                  'The screen:\n- slide: white, the whole frame\n  - title "Token anatomy": top' in notes
                  and notes.count("The screen:") == 2, notes)
    harness.check("how the speech and the screen relate is written plainly, under its number",
                  "Speech visual alignment: The speaker says cookie, the screen says token." in notes, notes)
    harness.check("the diagram is drawn as Mermaid under its number",
                  f"- F0.2 Diagram:\n```mermaid\n{DIAGRAM}\n```" in notes, notes)
    harness.check("no JSON reaches the file", '"id"' not in notes and '"kind"' not in notes, notes)


# Fields a frame's section shows in another form: `frame` is the number in its heading, and the transcript slices phase
# 2 attached are the `Said:` lines, taken from the transcript itself.
_SHOWN_OTHERWISE = {"frame", "transcriptBefore", "transcriptCurrent"}


def test_every_field_of_a_batch_s_own_answer_reaches_the_batch_store():
    """The batch-level fields have no frame to sit under, so their coverage is the batch store, which phase 4 reads.
    A section added to `# Batch` fails here until the program stores it (R15, R18)."""


    paths = fx.make_paths()
    batch_fields = list(frame_record.BATCH_SECTIONS)
    # the store keeps `batch_summary` under the key `summary`
    batch = {"slug": "frames-000-001", "frames": [0, 1], "start": 0.0, "end": 12.0, "source": "batch",
             **{("summary" if name == "batch_summary" else name): f"value-of-{name}" for name in batch_fields}}
    batch_summaries_service.save_batch_summary(paths, batch)
    stored = batch_summaries_service.read_batch_summaries(paths)[0]
    missing = [name for name in batch_fields
               if f"value-of-{name}" not in " ".join(str(value) for value in stored.values())]
    harness.check("every batch-level field the answer carries is in `03_batch_summaries.json`", not missing,
                  f"missing {missing}\n{stored}")


def _fill_from_contract(prefix: str = "value-of") -> dict:
    """Every field of the frame record with its own marker value, a labelled entry's details included: a field added to
    the record, or to a web address or a screenshot, fails the coverage check below until the notes show it (the
    owner, 2026-09-28: "make it smart so it counts the field")."""

    filled: dict = {"frame": 0, "screen_kind": f"{prefix}-screen_kind"}
    for field, (_, shape) in frame_record.FRAME_SECTIONS.items():
        if shape == "labelled":
            main, labels = frame_record.LABELLED_SECTIONS[field]
            filled[field] = [{name: f"{prefix}-{field}-{name}" for name in (main, *labels.values())}]
        elif shape in ("list", "tags"):
            filled[field] = [f"{prefix}-{field}"]
        else:
            filled[field] = f"{prefix}-{field}"

    return filled


def test_every_field_of_a_frame_record_and_of_its_item_reaches_the_notes():
    """Built from the schema, so a field added to the frame record later fails here until the notes show it: the
    owner, after the labels and the screenshot fields were left out of the file."""

    record = {"slug": "frame-000", "index": 0, "timestamp": 8.0, **_fill_from_contract()}
    # `importance` is read as a word, not shown as a marker: phase 4 and phase 5 act on it.
    record["screenshots"] = [{**record["screenshots"][0], "importance": "needed"}]
    # A tag of one word, so the merge keeps it as it is
    record["tags"] = [frame_record.KIND_TAGS[0]]
    item = {"slug": "frame-000", "index": 0, "timestamp": 8.0, "path": "frames/value-of-path.jpg", "box": [1, 2, 3, 4],
            "screenBox": [5, 6, 7, 8], "frameNumber": 4242, "score": 0.9876, "repeatOf": "frame-value-of-repeat",
            "builtFrom": ["frame-value-of-builtfrom"], "transcriptBefore": "", "transcriptCurrent": ""}
    merged = frame_merge_utils.merge_frame_records({"segments": []}, {"items": [item]}, [{**record, **{
        "builtFrom": item["builtFrom"]}}], {})
    notes = meeting_notes_utils.render_meeting_notes("fields.mp4", merged)
    missing = [name for name in frame_record.FRAME_FIELDS
               if name not in _SHOWN_OTHERWISE and name not in (*frame_record.LABELLED_SECTIONS, "tags")
               and f"value-of-{name}" not in notes]
    # A labelled entry is checked detail by detail: every one of `screenshots`' and `urls`' own fields must be there.
    for holder, (main, labels) in frame_record.LABELLED_SECTIONS.items():
        missing += [f"{holder}.{name}" for name in (main, *labels.values())
                    if name != "importance" and f"value-of-{holder}-{name}" not in notes]
    missing += [] if f"Tags: {frame_record.KIND_TAGS[0]}" in notes else ["tags"]
    harness.check("every field the frame record defines is in the notes, nested fields included",
                  not missing and "Screenshot: needed" in notes, f"missing {missing}\n{notes}")
    shown = {"path": "frames/value-of-path.jpg", "box": "1, 2, 3, 4", "screenBox": "5, 6, 7, 8", "frameNumber": "4242",
             "score": "0.9876", "repeatOf": "frame-value-of-repeat", "builtFrom": "frame-value-of-builtfrom"}
    harness.check("every field of the frame's item is in the notes",
                  set(item) - _SHOWN_OTHERWISE - {"slug", "index", "timestamp"} == set(shown)
                  and all(value in notes for value in shown.values()),
                  f"{[name for name, value in shown.items() if value not in notes]}\n{notes}")


def test_a_page_s_paragraph_keeps_its_lines_and_speech_stays_one_line_defect():
    # A PDF's code block of two commands was printed on one line of the notes, and read as one command (DEFECTS 509).
    # Now a page's paragraph keeps each line, indented under its id; an audio recording's paragraph stays one line
    span = {"start": 600.0, "end": 660.0, "lines": ("S0016", "S0016"),
            "text": "acmesync tenant go-live partner-b\nacmesync tenant go-live partner-b --rollback"}
    page = meeting_notes_utils._render_unread(span, [], 700.0, True)
    spoken = meeting_notes_utils._render_unread(span, [], 700.0, False)
    harness.check("the page's two commands on two lines", page[2:] == ["S0016 acmesync tenant go-live partner-b",
                                                                       "  acmesync tenant go-live partner-b --rollback"],
                  str(page))
    harness.check("speech stays one line", spoken[2] == "S0016 acmesync tenant go-live partner-b acmesync tenant "
                  "go-live partner-b --rollback" and len(spoken) == 3, str(spoken))
