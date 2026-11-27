"""The tools phase 5's worker reads the sources and the document with, and keeps its conversation small with.
REQUIREMENTS.md R16.

The worker decides what to read. Every read result starts with a short id (`[r7]`) it can name. A read of a
source spends one of the follow-up's reads; reading the document under review spends none, since checking a todo
against it is the job and not a search. A note is not a read: it is written to disk, within its size limit, and
survives every compaction of the conversation.
"""

from __future__ import annotations

import bisect
import json
import re


from typing import Any

from utils import document_heading_utils
from models.reading_errors import ReadArgumentError
from models.review_reading_context import ReviewReadingContext
from models.write_read_result import WriteReadResult
from models.write_reading_state import WriteReadingState
from schemas import write_actions
from services import document_file_service
from services import illustration_action_service
from services import phase_file_service
from services import review_file_service
from utils import document_edit_utils
from utils import frame_markdown_utils
from utils import document_text_utils
from utils import clock_utils
from utils import reading_tool_utils

__all__ = ["run_read"]

# The reads that cost nothing: the document under review and a note.
_FREE_TOOLS = ("read_document", "note")
# The notes on a frame that `find` searches, as phase 3's record names them.
_NOTE_FIELDS = ("third_person_description", "facts", "screen", "diagram", "speech_visual_alignment", "open_questions",
                "contradicts_earlier", "action_items", "questions_asked", "urls")


def _read_frame_time(item: dict) -> float:
    """When a frame was taken, in seconds; 0.0 for an item that carries no time."""

    return float(item.get("timestamp", 0.0))


def _read_time(entry: dict[str, Any]) -> float:
    return float(entry["start"]) if entry.get("start") is not None else float((entry.get("timestamps") or [0.0])[0])


def _render_entry(entry: dict[str, Any]) -> str:
    frames = entry.get("frames") or []
    where = f", {'frame' if len(frames) == 1 else 'frames'} {', '.join(str(frame) for frame in frames)}" if frames else ""

    return (f"{entry['id']} ({entry.get('kind', '')}, {clock_utils.render_clock(_read_time(entry))}{where}): "
            f"{entry.get('text', '')}")


def _render_line(entry: dict[str, Any]) -> str:
    """A transcript line with its time and, when phase 1 placed one, its speaker: the reviewer read who said what in
    the notes but not in the transcript it checks them against."""

    speaker = f"{entry['speaker']}: " if entry.get("speaker") else ""

    return f"{entry['id']} {clock_utils.render_clock(_read_time(entry))} {speaker}{entry.get('text', '')}"


def _read_document_body(context: ReviewReadingContext) -> str:
    """The document as the review has it now, without the front matter it opens with."""

    _front_matter, body = document_edit_utils.split_front_matter(context.document_path.read_text(encoding="utf-8"))

    return body


def _list_note_texts(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Each note on a frame as (its field, its text), a list field one text per item."""

    texts = []
    for field_name in _NOTE_FIELDS:
        value = record.get(field_name)
        for text in (value if isinstance(value, list) else [value]):
            if str(text or "").strip():
                texts.append((field_name, str(text).strip()))

    return texts


def _is_match(text: str,
              wanted: list[str]) -> bool:
    folded = text.casefold()

    return all(reading_tool_utils.is_word_in(word, folded) for word in wanted)


def _find_words(context: ReviewReadingContext,
                words: str,
                chunk: int) -> str:
    wanted = [word.casefold() for word in words.split() if word.strip()]
    if not wanted:
        return "Give the words to look for."
    blocks = [block for block, _ in _find_blocks(context, lambda text: _is_match(text, wanted))]
    if blocks:
        return reading_tool_utils.select_chunk(blocks, context.read_chars, chunk)
    # No line holds every word: the lines holding the most of them, since a worker searching five words at a time
    # read "No line holds all 5 words" again and again and never narrowed its search
    # Two of the words, not half: a worker meant "latency delay fail error retry" as alternatives
    # Each block's own words are counted, never its label: "transcript", a time or a field name matched every line
    every_block = _find_blocks(context, lambda text: True)
    ordered = reading_tool_utils.list_partial_lines([block for block, _ in every_block], wanted,
                                                    [text for _, text in every_block])
    if not ordered:
        return reading_tool_utils.explain_no_line(wanted, language=phase_file_service.read_transcript_language(context.paths))

    return (_render_partial_heading(wanted)
            + reading_tool_utils.select_chunk(ordered, context.read_chars, chunk))


def _render_partial_heading(wanted: list[str]) -> str:
    return f"No line holds all {len(wanted)} words; the lines holding at least 2 of them, the most first:\n\n"


def _find_blocks(context: ReviewReadingContext,
                 is_match) -> list[tuple[str, str]]:
    """Every transcript line, note, frame field and document line `is_match` accepts: each labelled with where it is,
    beside its own text."""

    blocks = [(f"transcript {_render_line(entry)}", str(entry.get("text", ""))) for entry in context.transcript
              if is_match(entry.get("text", ""))]
    blocks += [(f"entry {_render_entry(entry)}", str(entry.get("text", ""))) for entry in context.entries
               if entry.get("kind") != "speech" and is_match(str(entry.get("text", "")))]
    for number, record in sorted(context.frame_records.items()):
        time = _read_frame_time(context.frame_items.get(number, record))
        blocks += [(f"frame {number} at {clock_utils.render_clock(time)}, {field_name}: {text}", text)
                   for field_name, text in _list_note_texts(record) if is_match(text)]
    heading = ""
    for line in _read_document_body(context).split("\n"):
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        elif line.strip() and is_match(line):
            blocks.append((f'document, section "{heading}": {line.strip()}', line.strip()))

    return blocks


def _add_sentence_ends(transcript: list[dict[str, Any]],
                       found: list[int]) -> list[int]:
    """The lines found by words, each followed by the lines that finish its sentence (up to three): a line that stops
    mid-sentence hid the half of a time range said on the next one (the minutes of a range's end stood on the next
    line, and the range was read as ending on the hour)."""

    kept: list[int] = []
    for index in found:
        kept.append(index)
        follow = index
        while (follow + 1 < len(transcript) and follow - index < 3
               and not str(transcript[follow].get("text") or "").rstrip().endswith((".", "!", "?"))):
            follow += 1
            kept.append(follow)

    return list(dict.fromkeys(kept))


def _read_transcript(context: ReviewReadingContext,
                     args: dict[str, Any]) -> str:
    start = clock_utils.parse_clock(str(args.get("start") or ""))
    end = clock_utils.parse_clock(str(args.get("end") or ""))
    wanted = [word.casefold() for word in str(args.get("words") or "").split()]
    found = [index for index, entry in enumerate(context.transcript)
             if (start is None or _read_time(entry) >= start) and (end is None or _read_time(entry) <= end)
             and _is_match(entry.get("text", ""), wanted)]
    lines = [_render_line(context.transcript[index]) for index in _add_sentence_ends(context.transcript, found)] \
        if wanted else [_render_line(context.transcript[index]) for index in found]

    if not lines:
        entries = [entry for entry in context.transcript
                   if (start is None or _read_time(entry) >= start) and (end is None or _read_time(entry) <= end)]
        in_range = [_render_line(entry) for entry in entries]
        partial = reading_tool_utils.list_partial_lines(in_range, wanted, [str(entry.get("text") or "") for entry in entries])
        if partial:
            return (_render_partial_heading(wanted)
                    + reading_tool_utils.select_chunk(partial, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1)))
        # Within a time range, any one of the words is worth a line: a reviewer asked for three words between two
        # times, no line held two, and it was told only to ask again
        single = [line for line, entry in zip(in_range, entries)
                  if (start is not None or end is not None) and len(wanted) >= 2
                  and any(reading_tool_utils.is_word_in(word, str(entry.get("text") or "").casefold()) for word in wanted)]
        if single:
            return (f"No line holds 2 of the {len(wanted)} words in that time; the lines holding one of them:\n\n"
                    + reading_tool_utils.select_chunk(single, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1)))
        return reading_tool_utils.explain_no_line(wanted, max((_read_time(entry) for entry in context.transcript), default=None),
                                                  phase_file_service.read_transcript_language(context.paths))

    return reading_tool_utils.select_chunk(lines, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1))


def _read_moments(context: ReviewReadingContext,
                  args: dict[str, Any]) -> str:
    """Each frame of the range with what happened on it, the notes taken on it, and the lines spoken while it was
    shown; the lines spoken before the range's first frame come first."""

    start = clock_utils.parse_clock(str(args.get("start") or "")) or 0.0
    end = clock_utils.parse_clock(str(args.get("end") or ""))
    until = float("inf") if end is None else end
    items = sorted(context.frame_items.values(), key=_read_frame_time)
    shown = [item for item in items if start <= _read_frame_time(item) <= until]
    first = _read_frame_time(shown[0]) if shown else until
    before = [_render_line(entry) for entry in context.transcript if start <= _read_time(entry) < min(first, until)]
    blocks = ["said before the next frame:\n" + "\n".join(before)] if before else []
    times = [_read_frame_time(item) for item in items]
    for item in shown:
        number, time = int(item["index"]), _read_frame_time(item)
        following = bisect.bisect_right(times, time)
        shown_until = min(times[following], until + 1e-9) if following < len(times) else until + 1e-9
        record = context.frame_records.get(number, {})
        lines = [f"frame {number} at {clock_utils.render_clock(time)}"]
        if str(record.get("third_person_description") or "").strip():
            lines.append(str(record["third_person_description"]).strip())
        lines += [f"  {entry['id']} ({entry.get('kind', '')}) {entry.get('text', '')}" for entry in context.entries
                  if entry.get("kind") != "speech" and number in (entry.get("frames") or [])]
        lines += [_render_line(entry) for entry in context.transcript if time <= _read_time(entry) < shown_until]
        blocks.append("\n".join(lines))

    return reading_tool_utils.select_chunk(blocks, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1))


def _list_section_times(context: ReviewReadingContext,
                        body: str) -> dict[str, tuple[float, float]]:
    """Each written heading of the document -> the first and last time its phase 4 section cites. The published text
    has no ids, so its headings are paired with phase 4's section files in order (`heading-NNN-part`); empty when the
    two counts differ. A reviewer read the transcript at the wrong minutes for a section, guessing."""

    closing = min((index for index in (body.find(f"## {heading}\n") for heading in document_heading_utils.list_closing_headings())
                   if index >= 0), default=-1)
    titles = [line[4:].strip() for line in (body[:closing] if closing >= 0 else body).splitlines() if line.startswith("### ")]
    folder = document_file_service.build_sections_dir(context.paths)
    prefixes = sorted({path.stem.rsplit("-", 1)[0] for path in folder.glob("heading-*.md")}) if folder.is_dir() else []
    if len(titles) != len(prefixes):
        return {}
    times_by_id = {entry["id"]: _read_time(entry) for entry in [*context.entries, *context.transcript] if entry.get("id")}
    spans = {}
    for title, prefix in zip(titles, prefixes):
        text = " ".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob(f"{prefix}-*.md")))
        times = [times_by_id[entry_id] for entry_id in document_text_utils.find_entry_ids(text) if entry_id in times_by_id]
        if times:
            spans[" ".join(title.split())] = (min(times), max(times))

    return spans


def _read_document(context: ReviewReadingContext,
                   heading: str,
                   chunk: int) -> str:
    plain = _read_document_body(context)
    # Numbered as the critique read it, so a todo and a resolution name a block by the same number (DEFECTS 610)
    body = document_edit_utils.render_numbered_document(plain)
    if heading.strip():
        sections = document_edit_utils.read_sections(body, heading)
        if not sections:
            return f'There is no section "{heading}". The headings: {"; ".join(document_edit_utils.list_headings(body))}'
        times = _list_section_times(context, plain).get(" ".join(heading.strip().lstrip("#").split()))

        return ((f"Said between {clock_utils.render_clock(times[0])} and "
                 f"{clock_utils.render_clock(times[1])}: read the transcript and the moments there.\n\n"
                 if times else "") + "\n\n".join(sections))
    blocks: list[list[str]] = [[]]
    for line in body.split("\n"):
        if line.startswith("#") and blocks[-1]:
            blocks.append([])
        blocks[-1].append(line)

    return reading_tool_utils.select_chunk(["\n".join(block).strip() for block in blocks if "".join(block).strip()],
                                          context.read_chars, chunk)


def _read_frame_record(context: ReviewReadingContext,
                       frame: int) -> str:
    item = context.frame_items.get(frame)
    if item is None:
        return f"There is no frame {frame}; a moment or `find` names the frames there are."
    record = context.frame_records.get(frame, {})

    return (f"frame {frame} at {clock_utils.render_clock(_read_frame_time(item))}. Its notes:\n"
            + (frame_markdown_utils.render_stored_notes(record) if record else "(no notes were kept for this frame)"))


def _read_entries(context: ReviewReadingContext,
                  ids: object) -> str:
    """The entries `ids` names: a list, or one or more ids written as text ("F12.1, S0415")."""

    if isinstance(ids, str):
        ids = [entry_id for entry_id in re.split(r"[,\s]+", ids.strip()) if entry_id]
    if not isinstance(ids, list):
        return f"`ids` must be a list of entry ids, such as [\"F12.1\", \"S0415\"]; it was {ids!r}."
    by_id = {entry["id"]: entry for entry in context.entries}
    lines = [_render_entry(by_id[str(entry_id)]) if str(entry_id) in by_id else f"{entry_id}: no entry has this id"
             for entry_id in ids]

    return "\n".join(lines) if lines else "Give the ids of the entries to read."


def _show_frame(context: ReviewReadingContext,
                frame: int,
                result_id: str) -> WriteReadResult:
    item = context.frame_items.get(frame)
    if item is None:
        return WriteReadResult(text=f"{result_id} There is no frame {frame}; a moment or `find` names the frames "
                                    f"there are.")
    time = clock_utils.render_clock(_read_frame_time(item))

    return WriteReadResult(text=f"{result_id} frame {frame} at {time}: its frame follows.",
                           frame_path=context.paths.output_root / item["path"])


def _record_note(context: ReviewReadingContext,
                 state: WriteReadingState,
                 text: str,
                 replaces: int | None) -> str:
    notes, answer = reading_tool_utils.update_notes(state.notes, text, replaces, context.notes_chars)
    if notes is not None:
        state.notes = notes
        review_file_service.write_notes(context.paths, notes)

    return answer


def _run_reader(context: ReviewReadingContext,
                state: WriteReadingState,
                name: str,
                args: dict[str, Any],
                key: str,
                result_id: str) -> WriteReadResult:
    readers = {"read_document": lambda: _read_document(context, str(args.get("heading") or ""), reading_tool_utils.read_whole_number(args, "chunk", 1)),
               "read_transcript": lambda: _read_transcript(context, args),
               "find": lambda: _find_words(context, str(args.get("words") or ""), reading_tool_utils.read_whole_number(args, "chunk", 1)),
               "read_moments": lambda: _read_moments(context, args),
               "read_frame_record": lambda: _read_frame_record(context, reading_tool_utils.read_whole_number(args, "frame", None)),
               "read_entries": lambda: _read_entries(context, args.get("ids") or [])}
    if name not in readers:
        return WriteReadResult(text=f"{result_id} There is no tool called {name}.")

    text = readers[name]()
    state.seen_reads[key] = (result_id, " ".join(text.split())[:300])

    return WriteReadResult(text=f"{result_id} {text}")


def run_read(context: ReviewReadingContext,
             state: WriteReadingState,
             name: str,
             args: dict[str, Any]) -> WriteReadResult:
    """Carry out one reading tool. A read of a source mints its result id and spends one of the follow-up's reads;
    reading the document mints an id and spends none; a note spends nothing."""

    if name == "note":
        return WriteReadResult(text=_record_note(context, state, str(args.get("text") or ""), args.get("replaces")))
    # A figure todo recuts with phase 4's frame tools, over phase 4's frame store: no read, its own result id
    if name in write_actions.FRAME_TOOLS:
        result_id = f"[r{state.next_result}]"
        state.next_result += 1
        if context.illustration is None:
            return WriteReadResult(text=f"{result_id} Frames cannot be cut in this run: the recording is not known.")
        if not state.image_store:
            state.image_store.update(illustration_action_service.build_empty_store([]))
        result = illustration_action_service.run_action(context.illustration, state.image_store, name, args)
        document_file_service.write_image_store(context.paths, state.image_store)

        return WriteReadResult(text=f"{result_id} {result.text}", frame_path=result.frame_path,
                               is_frame_tool_refused=result.is_refused)
    # A read made word for word earlier in this todo is named, not made again, and spends nothing
    key = f"{name} {json.dumps(args, sort_keys=True)}"
    if key in state.seen_reads:
        earlier, opening = state.seen_reads[key]
        return WriteReadResult(text=f"This read was already made as {earlier} in this todo; it began: {opening} ... Read "
                                    f"something else, note what you found, or submit.")
    if name not in _FREE_TOOLS:
        if state.reads_used >= context.read_budget:
            return WriteReadResult(text=f"The reading budget of this follow-up ({context.read_budget} reads) is spent: "
                                        f"submit your answer with what you found.")
        state.reads_used += 1
    result_id = f"[r{state.next_result}]"
    state.next_result += 1
    try:
        if name == "look_at_frame":
            looked = _show_frame(context, reading_tool_utils.read_whole_number(args, "frame", None), result_id)
            state.seen_reads[key] = (result_id, " ".join(looked.text.split())[:300])
            return looked
        return _run_reader(context, state, name, args, key, result_id)
    except ReadArgumentError as exc:
        return WriteReadResult(text=f"{result_id} {exc}")
