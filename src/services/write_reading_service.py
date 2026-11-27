"""The tools phase 4's agent reads more of the meeting with, and keeps its conversation small with.
REQUIREMENTS.md R12.

The unit a lookup returns is the moment: tags are set per frame, so "an entry tagged X" is an entry of a moment
tagged X. Every read result starts with a short id (`[r7]`) the agent can name, and every read, the
listings included, counts against one budget for the whole phase. A note is not a read: it is written to disk, within
its size limit, and survives every compaction of the conversation.
"""

from __future__ import annotations

import re

from typing import Any

from utils import document_heading_utils
from models.reading_errors import ReadArgumentError
from models.write_read_result import WriteReadResult
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from schemas import frame_record
from schemas import write_actions
from services import illustration_action_service
from services import document_file_service
from services import phase_file_service
from utils import frame_markdown_utils
from utils import moment_label_utils
from utils import clock_utils
from utils import reading_tool_utils
from utils import screenshot_suggestion_utils

__all__ = ["build_reading_moments", "run_read", "describe_tags_elsewhere", "count_chapter_chunks", "describe_suggestion", "estimate_chapter_read_tokens"]

_NOT_CHAPTERS = (*document_heading_utils.list_every("executive_summary"), *document_heading_utils.list_every("agenda"),
                 *document_heading_utils.list_every("contents"))


def _find_chapter(point_starts: list[float],
                  time: float) -> int | None:
    """The chapter a time falls in, from 1, by where each agenda chapter starts; None before the agenda exists."""

    started = [number for number, start in enumerate(point_starts, start=1) if start <= time]

    return started[-1] if started else (1 if point_starts else None)


def _render_moment(moment: dict[str, Any],
                   point_starts: list[float],
                   entries: list[dict[str, Any]],
                   frame_records: dict[int, dict]) -> str:
    """A moment's header, what happened on its frame's screen (R15) when its record says, then its entries."""

    chapter = _find_chapter(point_starts, moment["time"])
    header = " · ".join(part for part in [
        clock_utils.render_clock(moment["time"]),
        f"frame {moment['frames'][0]}" if moment["frames"] else "speech",
        f"chapter {chapter}" if chapter else "", f"tags: {', '.join(moment['tags'])}" if moment["tags"] else ""] if part)
    frame = moment["frames"][0] if moment["frames"] else None
    description = str(frame_records.get(frame, {}).get("third_person_description") or "").strip() if frame is not None else ""
    if description:
        header += f"\nat {clock_utils.render_clock(moment['time'])}, frame {frame}: {description}"

    return header + "".join(f"\n  {entry['id']} ({entry['kind']}) {entry['text']}" for entry in entries)


def _list_tags(context: WriteReadingContext,
               chapter: int | None) -> str:
    counts: dict[str, dict[int, int]] = {}
    for moment in context.moments:
        moment_chapter = _find_chapter(context.point_starts, moment["time"]) or 0
        if chapter is not None and moment_chapter != chapter:
            continue
        for tag in moment["tags"]:
            counts.setdefault(tag, {})
            counts[tag][moment_chapter] = counts[tag].get(moment_chapter, 0) + 1
    if not counts:
        return "No moment carries a tag here. Use `find` with the words you are looking for."

    return "\n".join(f"{tag}: " + ", ".join(f"chapter {number} ({count})" if number else f"{count} moment(s)"
                                            for number, count in sorted(counts[tag].items()))
                     for tag in sorted(counts, key=str.casefold))


def _read_moments(context: WriteReadingContext,
                  args: dict[str, Any]) -> str:
    tag, chapter = str(args.get("tag") or ""), args.get("chapter")
    ids = {str(entry_id) for entry_id in args.get("ids") or []}
    matching = [moment for moment in context.moments
                if (not tag or tag.casefold() in (known.casefold() for known in moment["tags"]))
                and (chapter is None or _find_chapter(context.point_starts, moment["time"]) == chapter)
                and (not ids or ids & {entry["id"] for entry in moment["entries"]})]
    if not (tag or ids or chapter is not None):
        return "Give a tag, a chapter or entry ids."

    blocks = [_render_moment(moment, context.point_starts, moment["entries"], context.frame_records)
              for moment in matching]

    return reading_tool_utils.select_chunk(blocks, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1))


def _list_chapter_sections(context: WriteReadingContext,
                           chapter: int | None) -> list[str]:
    """The notes file's sections of one chapter, or all of them when `chapter` is None, in order."""

    return [text for start, text in context.note_sections
            if chapter is None or _find_chapter(context.point_starts, start) == chapter]


def _read_notes(context: WriteReadingContext,
                state: WriteReadingState,
                args: dict[str, Any]) -> str:
    """A whole chapter of the notes file in one read, or, with no chapter, one chunk of the whole meeting."""

    chapter = args.get("chapter")
    chunk = reading_tool_utils.read_whole_number(args, "chunk", 1)
    sections = _list_chapter_sections(context, chapter)
    if not sections:
        return f"No section of the notes falls in chapter {chapter}." if chapter is not None else "The notes file is empty."
    if chapter is None:
        return reading_tool_utils.select_chunk(sections, context.read_chars, chunk)
    # A chapter comes back whole, in one read (the owner, 2026-09-29: "no chunking"): a chapter fits the window
    state.chapters_read.add(chapter)

    return "\n\n".join(sections) + f"\n\nChapter {chapter} read whole."


def _render_transcript_line(entry: dict[str, Any]) -> str:
    """A transcript line with its time and, when phase 1 placed one, its speaker, as the notes print it."""

    speaker = f"{entry['speaker']}: " if entry.get("speaker") else ""

    return f"{entry['id']} {clock_utils.render_clock(float(entry['start']))} {speaker}{entry['text']}"


def _read_transcript(context: WriteReadingContext,
                     args: dict[str, Any]) -> str:
    start = clock_utils.parse_clock(str(args.get("start") or ""))
    end = clock_utils.parse_clock(str(args.get("end") or ""))
    wanted = [word.casefold() for word in str(args.get("words") or "").split()]
    entries = [entry for entry in context.transcript
               if (start is None or float(entry["start"]) >= start) and (end is None or float(entry["start"]) <= end)]
    lines = [_render_transcript_line(entry) for entry in entries
             if all(word in entry["text"].casefold() for word in wanted)]

    if not lines:
        in_range = [_render_transcript_line(entry) for entry in entries]
        partial = reading_tool_utils.list_partial_lines(in_range, wanted, [str(entry["text"]) for entry in entries])
        if partial:
            return (f"No line holds all {len(wanted)} words; the lines holding at least 2 of them, the most first:\n\n"
                    + reading_tool_utils.select_chunk(partial, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1)))
        return reading_tool_utils.explain_no_line(wanted, max((float(entry["start"]) for entry in context.transcript), default=None),
                                                  phase_file_service.read_transcript_language(context.paths))

    return reading_tool_utils.select_chunk(lines, context.read_chars, reading_tool_utils.read_whole_number(args, "chunk", 1))


def _find_words(context: WriteReadingContext,
                words: str,
                chunk: int) -> str:
    wanted = [word.casefold() for word in words.split() if word.strip()]
    if not wanted:
        return "Give the words to look for."
    blocks = []
    for moment in context.moments:
        found = [entry for entry in moment["entries"] if all(word in entry["text"].casefold() for word in wanted)]
        if found:
            blocks.append(_render_moment(moment, context.point_starts, found, context.frame_records))

    return reading_tool_utils.select_chunk(blocks, context.read_chars, chunk)


def _list_batches(context: WriteReadingContext) -> str:
    """R15: phase 3's account of each batch, in time order, with its span and its frames."""

    if not context.batches:
        return "No batch was told in phase 3 for this run."
    blocks = []
    for batch in context.batches:
        frames = batch.get("frames") or []
        covered = f"frame {frames[0]}" if len(frames) == 1 else f"frames {frames[0]} to {frames[-1]}"
        span = (f"{clock_utils.render_clock(float(batch['start']))} to "
                f"{clock_utils.render_clock(float(batch['end']))}")
        blocks.append(f"{span}, {covered}: "
                      f"{batch.get('summary', '')}")

    return "\n".join(blocks)


def _read_frame(context: WriteReadingContext,
                frame: int) -> WriteReadResult:
    item = context.frame_items.get(frame)
    if item is None:
        return WriteReadResult(text=f"There is no frame {frame}; a moment's header names the frames there are.")
    record = context.frame_records.get(frame, {})
    text = f"Frame {frame} at {clock_utils.render_clock(float(item.get('timestamp', 0.0)))}. Its record:\n" + (
        frame_markdown_utils.render_stored_notes(record) if record else "(no record was kept for this frame)")

    return WriteReadResult(text=text, frame_path=context.paths.output_root / item["path"])


def _read_document(context: WriteReadingContext,
                   chapter: int | None) -> str:
    path = document_file_service.build_working_draft_path(context.paths)
    if not path.is_file():
        return "The document has not been started yet."
    text = path.read_text(encoding="utf-8")
    if chapter is None:
        return text
    # The heading line is compared whole: an agenda point titled "Contents of the release" is a chapter
    chapters = [chunk for chunk in text.split("\n## ")[1:] if chunk.split("\n", 1)[0].strip() not in _NOT_CHAPTERS]

    return f"## {chapters[chapter - 1]}" if 1 <= chapter <= len(chapters) else f"There is no chapter {chapter} in the document yet."


def _record_note(context: WriteReadingContext,
                 state: WriteReadingState,
                 text: str,
                 replaces: object) -> str:
    notes, answer = reading_tool_utils.update_notes(state.notes, text, replaces, context.notes_chars)
    if notes is not None:
        state.notes = notes
        document_file_service.write_agent_notes(context.paths, notes)

    return answer


def _run_reader(context: WriteReadingContext,
                state: WriteReadingState,
                name: str,
                args: dict[str, Any],
                result_id: str) -> WriteReadResult:
    readers = {"list_batches": lambda: _list_batches(context),
               "list_tags": lambda: _list_tags(context, args.get("chapter")),
               "read_notes": lambda: _read_notes(context, state, args),
               "read_moments": lambda: _read_moments(context, args),
               "find": lambda: _find_words(context, str(args.get("words") or ""), reading_tool_utils.read_whole_number(args, "chunk", 1)),
               "read_document": lambda: _read_document(context, args.get("chapter")),
               "read_transcript": lambda: _read_transcript(context, args)}
    if name not in readers:
        return WriteReadResult(text=f"{result_id} There is no tool called {name}.")

    return WriteReadResult(text=f"{result_id} {readers[name]()}")


def build_reading_moments(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries grouped by their first time, in clock order: `{"time", "frames", "tags", "entries"}`, a moment's
    tags the union of its entries', in the order first met."""

    moments: dict[float, dict[str, Any]] = {}
    for entry in sorted(entries, key=lambda entry: entry["timestamps"][0]):
        moment = moments.setdefault(entry["timestamps"][0], {"time": entry["timestamps"][0], "frames": [],
                                                             "tags": [], "entries": []})
        moment["entries"].append(entry)
        for field_name, values in (("frames", entry.get("frames") or []), ("tags", entry.get("tags") or [])):
            moment[field_name] += [value for value in values if value not in moment[field_name]]

    return list(moments.values())


def _describe_subject_elsewhere(context: WriteReadingContext,
                                subject: str,
                                own_chapters: set) -> str:
    """The chapters other than `own_chapters` whose moments carry `subject`, each with its moments and their kinds of
    facts; "" when no other chapter has it."""

    elsewhere: dict[int, list[dict]] = {}
    for moment in context.moments:
        chapter = _find_chapter(context.point_starts, moment["time"])
        if subject in moment["tags"] and chapter not in own_chapters and chapter is not None:
            elsewhere.setdefault(chapter, []).append(moment)
    places = []
    for chapter, moments in sorted(elsewhere.items()):
        kinds = [tag for tag in dict.fromkeys(tag for moment in moments for tag in moment["tags"])
                 if tag in frame_record.KIND_TAGS]
        places.append(f"chapter {chapter} ({len(moments)} moment{'s' if len(moments) != 1 else ''}"
                      + (f", tagged {', '.join(kinds)})" if kinds else ")"))

    return f"{subject} also occurs in {' and '.join(places)}" if places else ""


def describe_tags_elsewhere(context: WriteReadingContext,
                            entry_ids: list[str]) -> str:
    """For a section written from `entry_ids`: each subject tag of their moments that also occurs in another chapter,
    with how many moments there and their kinds of facts. An empty string when none does."""

    wanted = set(entry_ids)
    own = [moment for moment in context.moments if wanted & {entry["id"] for entry in moment["entries"]}]
    own_chapters = {_find_chapter(context.point_starts, moment["time"]) for moment in own}
    subjects = dict.fromkeys(tag for moment in own for tag in moment_label_utils.list_subject_tags(moment["tags"]))
    lines = [line for line in (_describe_subject_elsewhere(context, subject, own_chapters) for subject in subjects) if line]

    return ("Where this section's subjects also occur: " + "; ".join(lines) + ". Read them only if you need them."
            if lines else "")


def run_read(context: WriteReadingContext,
             state: WriteReadingState,
             name: str,
             args: dict[str, Any]) -> WriteReadResult:
    """Carry out one reading tool. A read mints its result id and spends one of the phase's reads; a note and a frame
    tool spend nothing."""

    if name == write_actions.NOTE_TOOL:
        return WriteReadResult(text=_record_note(context, state, str(args.get("text") or ""), args.get("replaces")))
    # A frame tool is no read: it spends none of the reading budget and gets its own result id
    # Every frame tool the writer is offered, from the one list that offers them: `undo_cut` was offered and answered
    # "There is no tool called undo_cut" when a second list here left it out (2026-10-03)
    if name in write_actions.FRAME_TOOLS:
        result_id = f"[r{state.next_result}]"
        state.next_result += 1
        if context.illustration is None:
            return WriteReadResult(text=f"{result_id} Frames cannot be cut in this run: the recording is not known.")
        if not state.image_store:
            state.image_store.update(illustration_action_service.build_empty_store([]))
        result = illustration_action_service.run_action(context.illustration, state.image_store, name, args)
        document_file_service.write_image_store(context.paths, state.image_store)
        # A look at a cut is labelled by the cut's own frame (2026-09-30: "suggested no screenshot of cut-047" read as
        # phase 3 not wanting a frame it had marked needed)
        target = str(args.get("frame") or "")
        frame = (state.image_store.get("cuts") or {}).get(target, {}).get("frame", target)
        label = describe_suggestion(context, frame) if name in ("look", "cut") else ""

        return WriteReadResult(text=f"{result_id} {result.text}{label}", frame_path=result.frame_path,
                               is_frame_tool_refused=result.is_refused)
    if state.reads_used >= context.read_budget:
        return WriteReadResult(text=f"The reading budget of this phase ({context.read_budget} reads) is spent: carry "
                                    f"on with what the follow-up handed you.")
    state.reads_used += 1
    result_id = f"[r{state.next_result}]"
    state.next_result += 1
    try:
        # A chapter sent as text ("2") is that chapter, an empty one is none; one that is no whole number is answered
        # by name as any argument of a read is, never compared
        if "chapter" in args:
            chapter = None if args["chapter"] in (None, "") else reading_tool_utils.read_whole_number(args, "chapter", None)
            args = {**args, "chapter": chapter}
        if name == "read_frame":
            frame = _read_frame(context, reading_tool_utils.read_whole_number(args, "frame", None))
            return WriteReadResult(text=f"{result_id} {frame.text}", frame_path=frame.frame_path)
        return _run_reader(context, state, name, args, result_id)
    except ReadArgumentError as exc:
        return WriteReadResult(text=f"{result_id} {exc}")


def describe_suggestion(context: WriteReadingContext,
                        slug: str) -> str:
    """What phase 3 marked for this frame, as a line under a `look` or `cut` result: its label, `needed` or `helps`, with
    its reason and part, or that it suggested none (the owner, 2026-09-29: the tool returns the label)."""

    record = next((record for record in context.frame_records.values() if record.get("slug") == slug), None)
    suggestions = screenshot_suggestion_utils.read_suggestions(record) if record else []
    if not suggestions:
        return f"\nPhase 3 suggested no screenshot of {slug}."

    return "".join(f"\nPhase 3 marked {slug} `{entry['importance']}`: {entry['why']}" + (f" ({entry['part']})" if entry["part"] else "")
                   for entry in suggestions)


def _count_chapter_characters(context: WriteReadingContext,
                              chapter: int) -> int:
    """About how many characters `read_notes` returns for one chapter, its sections without the lines joining them and
    the result's id: what its read costs, known before it is made."""

    return sum(len(text) for text in _list_chapter_sections(context, chapter))


def estimate_chapter_read_tokens(context: WriteReadingContext,
                                 chapter: int,
                                 chars_per_token: float) -> int:
    """What reading one chapter whole adds to the conversation, in tokens: the compaction's handoff and the check that
    compacts before a read that would not fit both measure it this way."""

    return round(_count_chapter_characters(context, chapter) / chars_per_token)


_PRINTED_ID = re.compile(r"\b(?:F\d+\.\d+|S\d{4,})\b")


def list_chapter_ids(context: WriteReadingContext,
                     chapter: int) -> list[str]:
    """Every note and speech line id the notes file prints in one chapter, in the order printed, once each: what a
    heading of that chapter may start at."""

    return list(dict.fromkeys(match.group(0) for text in _list_chapter_sections(context, chapter)
                              for match in _PRINTED_ID.finditer(text)))


def count_chapter_chunks(context: WriteReadingContext,
                         chapter: int) -> int:
    """How many reads `read_notes` takes for one chapter: one, since a chapter comes back whole; none for no section."""

    return 1 if _list_chapter_sections(context, chapter) else 0
