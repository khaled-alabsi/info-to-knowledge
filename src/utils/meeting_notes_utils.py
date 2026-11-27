"""Layer 2 written as one Markdown file, `03_notes.md`, for the agents that write, illustrate and review the document.

The owner's order (2026-09-27): the merge's output is one readable file, never JSON. One section per frame, in the
meeting's order: what was said while it was on screen, then what it taught. A span of speech no frame read is a
section of its own, and so is each batch, after its last frame. Only what a paragraph or a review cites carries its number: a fact, a diagram, an open question,
a web address, a question asked (`F154.2`), and a transcript line (`S0415`).
Each frame's screen is printed whole, as the outline phase 3 wrote: every part with where it sits and its text quoted.

Pure functions: the program builds the entries in memory (`services/integration_service.py`) and this renders them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import output_contract
from utils import frame_markdown_utils
from utils import frame_merge_utils
from utils import clock_utils

__all__ = ["render_meeting_notes", "list_note_sections"]

# How a numbered note is labelled in its frame's list; a fact is the plain case and carries no label.
_LABELS = {"facts": "", "speech_visual_alignment": "Speech visual alignment: ", "open_question": "Open: ",
           "url": "Web: ",
           "action_item": "Action: ", "question": "Question: "}


def _collapse_spaces(text: object) -> str:
    """`text` on one line of the notes file: every run of spaces and line breaks one space."""

    return " ".join(str(text).split())


def _is_in_span(start: float,
                span_start: float,
                span_end: float | None) -> bool:
    """Does `start` fall in the span, an end of None running to the recording's end?"""

    return span_start <= start and (span_end is None or start < span_end)


def _list_speech(integration: dict[str, Any]) -> list[dict[str, Any]]:
    """The transcript's lines among the integration's entries."""

    return [entry for entry in integration.get("entries", []) if entry["kind"] == "speech"]


def _render_place(seconds: float,
                  is_pdf: bool) -> str:
    """Where a section stands: its time in the recording, or a PDF's page, "page 6"."""

    if is_pdf:
        return f"page {int(seconds // output_contract.PDF_PAGE_SECONDS) + 1}"

    return clock_utils.render_clock(seconds)


def _find_last_moment(integration: dict[str, Any]) -> float:
    """The recording's last moment the notes know: the end of its last speech line or its last frame's time."""

    speech = _list_speech(integration)
    frames = integration.get("frames") or []

    return max([float(line["end"]) for line in speech] + [float(frame["timestamp"]) for frame in frames] or [0.0])


def _order_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The notes in `NOTE_KINDS` order, a kind the merge does not make refused by name."""

    unknown = sorted({note["kind"] for note in notes} - set(frame_merge_utils.NOTE_KINDS))
    if unknown:
        raise ValueError(f"a note of kind {', '.join(unknown)} has no place in 03_notes.md: add it to "
                         f"frame_merge_utils.NOTE_KINDS")

    return sorted(notes, key=lambda note: frame_merge_utils.NOTE_KINDS.index(note["kind"]))


def _render_spoken_line(line: dict[str, Any]) -> str:
    """A transcript line as the notes print it: its id, its speaker when phase 1 placed one, and its words."""

    speaker = f"{line['speaker']}: " if line.get("speaker") else ""

    return f"{line['id']} {speaker}{_collapse_spaces(line['text'])}"


def _render_note(entry: dict[str, Any]) -> list[str]:
    kind = entry["kind"]
    text = _collapse_spaces(entry["text"])
    if kind == "diagram":
        return [f"- {entry['id']} Diagram:", "```mermaid", str(entry["text"]).strip(), "```"]
    if kind == "markdown":
        return [f"- {entry['id']} As markdown:", *frame_markdown_utils.strip_markdown_fence(str(entry["text"])).splitlines()]

    return [f"- {entry['id']} {_LABELS[kind]}{text}"]


def _render_frame_fields(frame: dict[str, Any]) -> list[str]:
    """Every field phase 2 and phase 3 recorded about the frame beside its notes: its tags, its frame and where the
    screen sits in it, whether it repeats an earlier frame, whose notes it was shown, the screenshot it asks for, and
    what it builds on."""

    tags_line = f"Tags: {', '.join(frame.get('tags') or [])}" if frame.get("tags") else ""
    # The name the frame tools take comes first, and the recording's frame number says whose number it is: shown
    # "frame number 1" beside frames/frame-000.jpg, a writer cut "frame-1" and the strict run stopped (DEFECTS 500)
    # the merge's frame holds its path, not always its slug: the file's name is the slug
    tool_name = frame.get("slug") or (Path(str(frame["path"])).stem if frame.get("path") else "")
    frame_line = " · ".join(part for part in [
        f"Frame tools name it {tool_name}" if tool_name else "",
        f"File: {frame['path']}" if frame.get("path") else "",
        f"the recording's frame number {frame['frameNumber']}" if frame.get("frameNumber") is not None else "",
        f"change score {frame['score']}" if frame.get("score") is not None else "",
        f"crop box {', '.join(str(value) for value in frame['box'])}" if frame.get("box") else "",
        f"screen box {', '.join(str(value) for value in frame['screenBox'])}" if frame.get("screenBox") else ""] if part)
    lines = [line for line in [tags_line, frame_line] if line]
    if frame.get("repeatOf"):
        lines.append(f"Repeat of: {frame['repeatOf']}")
    if frame.get("stepOf"):
        lines.append(f"A step of the screen built up from: {frame['stepOf']}")
    if frame.get("builtFrom"):
        lines.append(f"Shown the notes of: {', '.join(frame['builtFrom'])}")
    for suggestion in frame.get("screenshots") or []:
        lines.append(f"Screenshot: {suggestion['importance']}"
                     + (f", of {suggestion['part']}" if suggestion.get("part") else "")
                     + (f", because {suggestion['why']}" if suggestion.get("why") else ""))
    lines += [f"Builds on: {text}" for text in frame.get("buildsOn") or []]

    return lines


def _render_frame(frame: dict[str, Any],
                  notes: list[dict[str, Any]],
                  spoken: list[dict[str, Any]],
                  contradictions: list[str],
                  is_pdf: bool) -> list[str]:
    title = frame_markdown_utils.find_screen_title(str(frame.get("screen") or ""))
    kind_and_title = " ".join(part for part in [frame.get("screenKind") or "", f'"{title}"' if title else ""] if part)
    heading = " · ".join(part for part in [f"## F{frame['index']}", _render_place(frame["timestamp"], is_pdf),
                                           kind_and_title] if part)
    lines = ["", heading]
    if frame.get("third_person_description"):
        lines.append(f"What happened: {_collapse_spaces(frame['third_person_description'])}")
    # R17: the screen's outline, so a step that has no frame to look at can still say what was on screen and where
    if str(frame.get("screen") or "").strip():
        lines += ["The screen:", *str(frame["screen"]).rstrip().splitlines()]
    # How phase 3 read the moment: what was said tied to the part of the screen it is about, then what it leaves.
    if frame.get("takeAways"):
        # The one field where phase 3 may guess, so it says so: a take-away's guess was written into a chapter as a
        # fact the page never states (DEFECTS 514)
        lines += ["Take aways (phase 3's reading, which may guess; write as fact only what the notes' facts or the "
                  "source state):", *[f"- {_collapse_spaces(line)}" for line in frame["takeAways"]]]
    lines += _render_frame_fields(frame)
    lines += [f"Said: {_render_spoken_line(line)}" for line in spoken]
    for note in _order_notes(notes):
        lines += _render_note(note)
    lines += [f"- Contradicts earlier: {_collapse_spaces(text)}" for text in contradictions]

    return lines


def _render_unread(span: dict[str, Any],
                   spoken: list[dict[str, Any]],
                   last: float,
                   is_pdf: bool) -> list[str]:
    end = last if span["end"] is None else span["end"]
    # A PDF's paragraph by its page: the next paragraph's start, its end, may be on the next page
    clock = (f"Page {int(span['start'] // output_contract.PDF_PAGE_SECONDS) + 1}" if is_pdf
             else f"{clock_utils.render_clock(span['start'])}–{clock_utils.render_clock(end)}")
    # An audio recording's paragraph: its clean text under its first line's id, which stands for every line in it
    if span.get("text"):
        first, final = span["lines"]
        lines_note = f" (lines {first} to {final})" if first != final else ""

        speaker = f" · {span['speaker']}" if span.get("speaker") else ""
        if is_pdf:
            # A page's paragraph keeps its lines, each after the first indented under its id: run together on one
            # line, two commands of a code block read as one (DEFECTS 509)
            page_lines = [_collapse_spaces(line) for line in str(span["text"]).splitlines() if line.strip()]
            return ["", f"## {clock}{speaker}", f"{first} {page_lines[0] if page_lines else ''}{lines_note}",
                    *[f"  {line}" for line in page_lines[1:]]]
        return ["", f"## {clock}{speaker}", f"{first} {_collapse_spaces(span['text'])}{lines_note}"]

    return ["", f"## {clock} · speech no frame read ({span['why']})",
            *[_render_spoken_line(line) for line in spoken]]


def _render_prepared_item(item: dict[str, Any],
                          is_pdf: bool) -> list[str]:
    """An item of phase 3's prepared notes of an audio recording: its step and title, then each section with its id
    and the paragraphs it rests on."""

    lines = ["", f"## {_render_place(item['start'], is_pdf)} · {item['step']}: {item['title']}"]

    return lines + [f"- {entry['id']} {_collapse_spaces(entry['text'])} (paragraphs {', '.join(entry['paragraphs'])})"
                    for entry in item["entries"]]


def _render_batch_summary(batch: dict[str, Any],
                          is_pdf: bool) -> list[str]:
    """A batch's own section, after its last frame: its title, the story, the mood, the questions it left open and the answers found."""

    frames = sorted(int(number) for number in batch["frames"])
    heading = (f"## F{frames[0]} to F{frames[-1]} · {_render_place(float(batch['start']), is_pdf)}"
               " · the batch")
    title = _collapse_spaces(batch.get("title") or "")
    story = _collapse_spaces(batch.get("summary") or "")
    mood = _collapse_spaces(batch.get("atmosphere") or "")

    return ["", heading,
            *([f"Title: {title}"] if title else []),
            *([f"Story of the batch: {story}"] if story else []),
            *([f"Atmosphere: {mood}"] if mood else []),
            *[f"Open: {_collapse_spaces(text)}" for text in batch.get("openQuestions") or []],
            *[f"Answered: {_collapse_spaces(text)}" for text in batch.get("answeredQuestions") or []]]


def _list_frame_sections(integration: dict[str, Any]) -> list[tuple[float, list[str]]]:
    """`(start, lines)` of every frame's section: the speech it heard, then everything it recorded."""

    entries = integration.get("entries", [])
    speech = _list_speech(integration)
    spans = integration.get("unreadSpeech", [])
    frames = sorted(integration.get("frames") or [], key=lambda frame: frame["timestamp"])
    notes_of: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("frames"):
            notes_of.setdefault(entry["frames"][0], []).append(entry)
    contradictions_of: dict[int, list[str]] = {}
    for item in integration.get("contradictions", []):
        contradictions_of.setdefault(item["frame"], []).append(str(item["text"]))
    # A line in a span no frame read is that span's own section, never a frame's: decided once for the transcript
    read_speech = [line for line in speech if not any(_is_in_span(line["start"], span["start"], span["end"])
                                                      for span in spans)]
    sections = []
    for frame, following in zip(frames, [*frames[1:], None]):
        end = following["timestamp"] if following else None
        heard = [line for line in read_speech if _is_in_span(line["start"], frame["timestamp"], end)]
        sections.append((frame["timestamp"], _render_frame(frame, notes_of.get(frame["index"], []), heard,
                                                           contradictions_of.get(frame["index"], []),
                                                           bool(integration.get("isPdf")))))

    return sections


def list_note_sections(integration: dict[str, Any]) -> list[tuple[float, str]]:
    """`(start, text)` of every section of the notes file, in the order they happened: a frame with the speech it
    heard, a span of speech no frame read, or a batch after its last frame. What phase 4's `read_notes` reads a chunk
    at a time."""

    speech = _list_speech(integration)
    last = _find_last_moment(integration)
    is_pdf = bool(integration.get("isPdf"))
    sections = _list_frame_sections(integration)
    for span in integration.get("unreadSpeech", []):
        unread = [line for line in speech if _is_in_span(line["start"], span["start"], span["end"])]
        if unread:
            sections.append((span["start"], _render_unread(span, unread, last, is_pdf)))
    # A batch's questions come after its last frame and before the next frame's section, which starts at the batch's end
    for batch in integration.get("batchSummaries", []):
        sections.append((float(batch["end"]) - 0.001, _render_batch_summary(batch, is_pdf)))
    # A prepared item comes before the paragraph it starts at
    for item in integration.get("prepared") or []:
        sections.append((float(item["start"]) - 0.0005, _render_prepared_item(item, is_pdf)))
    ordered = sorted(sections, key=lambda pair: pair[0])

    return [(start, "\n".join(lines).strip("\n")) for start, lines in ordered]


def render_meeting_notes(recording_name: str,
                         integration: dict[str, Any]) -> str:
    """The notes file: a heading with the recording, its length and its frame count, then every frame, every
    span of speech no frame read and each batch after its last frame, in the order they happened."""

    last = _find_last_moment(integration)
    length = (f"{int(last // output_contract.PDF_PAGE_SECONDS) + 1} page(s)" if integration.get("isPdf")
              else clock_utils.render_clock(last))
    heading = f"# {recording_name} · {length} · {len(integration.get('frames') or [])} frames"

    return "\n\n".join([heading] + [text for _, text in list_note_sections(integration)]) + "\n"
