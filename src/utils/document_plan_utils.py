"""Phase 4's plan, decided by the program: what the document is written from, the moments the parts call sees,
which part of the meeting each entry falls in, where a part is cut into calls a local model's context holds, how the
topics of two chunks become one and when a short return is folded back, and which diagram a later one supersedes.
Pure functions over layer 2 and the parsed answers. REQUIREMENTS.md R3 and R9.

THE DOCUMENT IS WRITTEN FROM KNOWLEDGE. Each frame's call was handed its frame and the words spoken while it was
on screen, and returned what they teach; those `facts`, `diagram` and `open_question` entries are the claims. The `speech_visual_alignment`
notes are entries too (the owner, 2026-09-29: nothing is left out on the assumption that the facts hold it), so the writer cites
them like the rest. A `speech` segment a frame's call was handed stays in layer 2 as provenance. The speech of the spans no model read (`unreadSpeech`, from the layer-2 merge) is written from as it is.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from models import output_contract
from utils import document_text_utils
from utils import frame_markdown_utils
from utils import mermaid_text_utils

__all__ = ["list_required_ids", "keep_document_headings", "split_long_parts", "map_line_owners", "map_start_owners", "resolve_set_aside",
           "build_table_of_contents", "flatten_table_of_contents", "group_topics_by_chapter",
           "select_document_entries", "split_into_chunks", "list_superseded_diagrams", "list_moments",
           "split_by_parts", "merge_part_topics", "list_entry_statuses", "DOCUMENT_KINDS", "NEVER_SET_ASIDE_KINDS", "is_kept_note",
           "build_inputs_hash_path", "compute_inputs_hash", "is_inputs_hash_current", "expand_topic_starts"]

# `prepared` is a section of phase 3's prepared notes of an audio recording
DOCUMENT_KINDS = ("facts", "diagram", "markdown", "speech_visual_alignment", "open_question", "url", "action_item", "question",
                  "prepared")
# What a reader of the document must always find: an outline may never set these aside. REQUIREMENTS.md R8.
NEVER_SET_ASIDE_KINDS = ("url", "action_item", "question")
# A web address note holding a person's e-mail is the one exception: the text may not write a person's address
# (DOCUMENT_PERSONAL_IN_TEXT), so kept it could only be cited where it never stands (38c3 talk 797, DEFECTS 542)
# An audio recording's paragraph of this many words or fewer is a brief reply ("Yes.", "Okay, thanks."), which a section
# may cite and need not: one run's 51 paragraphs held 16 of 1 to 4 words, the next shortest 7, and a chapter's dry run
# was refused for one such reply left out
BRIEF_REPLY_WORDS = 4
# A line that is a markdown heading: "## **Appendix C. Glossary**"
_HEADING_ONLY = re.compile(r"^\s*#{1,6}\s+\S")
# A line that is one bold label, with at most a colon after it: a heading a PDF's reader wrote as bold text
_BOLD_LABEL = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")
# A heading's emphasis marks, and a link's address after its text, which its agenda title does not carry.
_HEADING_WORDS = re.compile(r"[*_]+")
_LINK_ADDRESS = re.compile(r"\s*<https?://[^>]*>")
# A frame's note id, `F<frame>.<note>`: its frame's own id is the part before the dot.
_FRAME_NOTE_ID = re.compile(r"^(F\d+)\.\d+$")
# A transcript line's id with leading zeros (S0999)
_PADDED_LINE_ID = re.compile(r"^S0\d+$")


def _normalise_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _list_diagram_lines(source: str) -> frozenset[str]:
    lines = [line.strip() for line in source.strip().splitlines()]

    return frozenset(line for line in lines if line and not line.startswith("```"))


def _find_span(start: float,
               spans: list[dict[str, Any]]) -> int | None:
    return next((index for index, span in enumerate(spans)
                 if span["start"] <= start and (span["end"] is None or start < span["end"])), None)


def _find_speech_group(start: float,
                       spans: list[dict[str, Any]],
                       frame_starts: list[tuple[float, int]]) -> tuple[str, int] | None:
    """Which entry a transcript line joins: the span no frame read that holds it, else the frame on screen."""

    span = _find_span(start, spans)
    if span is not None:
        return "span", span
    shown = [index for time, index in frame_starts if time <= start]

    return ("frame", shown[-1]) if shown else None


def _list_moment_starts(integration: dict[str, Any]) -> list[tuple[float, str]]:
    """`(start, screen kind)` of every frame, in clock order. The merge lists the frames with their kind; a
    layer 2 with no frame list gives each distinct time a frame entry has, kind unknown."""

    frames = integration.get("frames") or []
    if frames:
        starts = {float(frame["timestamp"]): str(frame.get("screenKind") or "") for frame in frames}
    else:
        starts = {float(entry["timestamps"][0]): "" for entry in integration.get("entries", [])
                  if entry.get("source") == "frame" and entry.get("timestamps")}
    ordered = sorted(starts.items())
    speech_starts = [float(entry["start"]) for entry in integration.get("entries", []) if entry["kind"] == "speech"]
    # A recording with no frame (an audio recording) has its speech paragraphs as its moments: with the frames' starts
    # alone it was one moment of the opening words, and the summary and the agenda were asked from that
    if not ordered:
        return [(start, "") for start in sorted(set(speech_starts))]
    # A text-only PDF's figures stand among its paragraphs, which are its moments as an audio recording's are: with the
    # figures' starts alone, a page of text and no figure would be no moment
    if all(frame.get("medium") == output_contract.PDF_FIGURE_MEDIUM for frame in frames) and frames:
        return sorted({**{start: "" for start in speech_starts}, **dict(ordered)}.items())
    if speech_starts and min(speech_starts) < ordered[0][0]:
        ordered.insert(0, (min(0.0, min(speech_starts)), ""))

    return ordered


def _shorten_text(text: str,
                  limit: int) -> str:
    flat = " ".join(text.split())

    return flat if len(flat) <= limit else flat[:limit] + " ..."


def _decide_folds(outlines: list[dict[str, Any]],
                  outline_parts: list[int],
                  fold_entries: int) -> dict[tuple[int, str], int]:
    """(part, title) -> the earlier part a return folds into. Decided per part over all of its chunks, so a return
    split over two chunks is folded whole or not at all."""

    sizes: dict[tuple[int, str], int] = {}
    for outline, part in zip(outlines, outline_parts):
        for topic in outline.get("topics", []):
            if topic.get("entry_ids"):
                key = (part, _normalise_title(topic.get("title", "")))
                sizes[key] = sizes.get(key, 0) + len(topic["entry_ids"])
    first_parts: dict[str, int] = {}
    titles_by_part: dict[int, set[str]] = {}
    for part, title in sizes:
        first_parts[title] = min(part, first_parts.get(title, part))
        titles_by_part.setdefault(part, set()).add(title)

    return {(part, title): first_parts[title] for (part, title), size in sizes.items()
            if first_parts[title] < part and size <= fold_entries and len(titles_by_part[part]) > 1}


def _read_start_id(item: dict[str, Any]) -> str:
    """Where a chapter or a heading starts, as its `first_entry_id`; "" when it names none."""

    return str(item.get("first_entry_id") or "")


def is_kept_note(entry: dict[str, Any]) -> bool:
    """Whether an entry is one a section may never set aside: a web address, an action item or a question, but not a
    web address note holding a person's e-mail, which the text may not write."""

    if entry.get("kind") not in NEVER_SET_ASIDE_KINDS:
        return False

    return entry.get("kind") != "url" or not document_text_utils.PERSONAL_EMAIL.search(str(entry.get("text") or ""))


def select_document_entries(integration: dict[str, Any]) -> list[dict[str, Any]]:
    """The layer-2 entries the document is written from, in layer 2's order. The speech is written from too, since no
    separate step looks for what was said and never noted: the lines one frame heard are ONE entry, and so is each
    span no frame read, under its first line's id with `segments` naming every line in it. The lines stay in
    layer 2 for the review to cite."""

    spans = integration.get("unreadSpeech", [])
    prepared_paragraphs = set(integration.get("preparedParagraphs") or [])
    # A PDF page's text heard by its frame is the page itself, which the frame's notes hold whole, tables written as
    # markdown: as one entry it held every section of the page and crossed its chapter border (DEFECTS 138)
    is_pdf_pages = any(frame.get("medium") == "pdf" for frame in integration.get("frames") or [])
    is_pdf = bool(integration.get("isPdf"))
    frame_starts = sorted((float(frame["timestamp"]), int(frame["index"])) for frame in integration.get("frames") or [])
    selected: list[dict[str, Any]] = []
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in integration.get("entries", []):
        if entry["kind"] in DOCUMENT_KINDS:
            selected.append(entry)
            continue
        key = _find_speech_group(float(entry["start"]), spans, frame_starts) if entry["kind"] == "speech" else None
        if key is None or (is_pdf_pages and key[0] == "frame"):
            continue
        # An audio recording's paragraph holds its own clean text and speaker, which stand for its lines
        paragraph = spans[key[1]] if key[0] == "span" and spans[key[1]].get("text") else None
        if key in groups:
            group = groups[key]
            group["text"] = paragraph["text"] if paragraph else f"{group['text']} {entry['text']}"
            group["end"] = entry["end"]
            group["segments"].append(entry["id"])
        else:
            # The lines a frame heard fed that frame's notes already; a span no frame read is only here
            groups[key] = {**entry, "segments": [entry["id"]], "heardByFrame": key[0] == "frame"}
            if paragraph:
                groups[key].update({"text": paragraph["text"], "speaker": paragraph["speaker"],
                                    "isPrepared": paragraph["lines"][0] in prepared_paragraphs,
                                    # A PDF's short paragraph is a heading or a label, never a reply
                                    "isBriefReply": not is_pdf and len(paragraph["text"].split()) <= BRIEF_REPLY_WORDS})
            selected.append(groups[key])

    return selected


def _is_heading_with_few_words(entry: dict[str, Any]) -> bool:
    """Whether a PDF paragraph is its heading and at most `BRIEF_REPLY_WORDS` words beside it, such as a figure's short
    caption: the wiki's "### **Z scheme**" over "The "Z scheme"" was refused as uncovered, and it teaches nothing."""

    lines = [line for line in str(entry.get("text") or "").splitlines() if line.strip()]
    # a line wholly bold is a heading too: "**Chloroplast ultrastructure** :" stood as a paragraph of its own
    rest = [line for line in lines if _HEADING_ONLY.match(line) is None and _BOLD_LABEL.match(line) is None]

    return len(rest) < len(lines) and len(" ".join(rest).split()) <= BRIEF_REPLY_WORDS


def list_required_ids(entries: list[dict[str, Any]],
                      figure_frames: set[int] | frozenset[int] = frozenset()) -> list[str]:
    """The entries a section must cite: all but the speech a frame heard, whose content that frame's notes already
    state (the owner, 2026-09-28: filler speech cost whole retries), and the speech visual alignment notes, which say
    how the speech and the screen relate and teach nothing about the subject (2026-10-01: every one of a chapter's six
    read "the speaker explains what the slide shows", and covering them made the worker write sentences about the
    speaker, rejected three chapters in a row; a difference between the two is a fact of its own). Both may be cited
    and never have to be; so may an audio recording's paragraph that a prepared note cites, whose content that note
    states, and one that is a brief reply (`BRIEF_REPLY_WORDS`), which teaches nothing alone."""

    # A diagram note of more than ten arrows is never drawn in the text, and its frame's cut shows it: the wiki's
    # chapter 2 was refused twice for leaving out the id of a 15-arrow diagram whose figure it had placed
    return [entry["id"] for entry in entries if not (entry["kind"] == "speech" and (entry.get("heardByFrame")
                                                                                    or entry.get("isPrepared")
                                                                                    or entry.get("isBriefReply")))
            and entry["kind"] != "speech_visual_alignment"
            and not entry.get("isOutlineCopy")
            and not (entry["kind"] == "speech" and _is_heading_with_few_words(entry))
            and not (entry["kind"] == "diagram"
                     and mermaid_text_utils.count_arrows(str(entry.get("text") or "")) > mermaid_text_utils.MOST_TRUSTED_ARROWS)
            # and so is a diagram note of a PDF figure (`figure_frames`), which is always placed as its cut: the wiki's
            # second run was checked again for one whose figure stood in the section
            and not (entry["kind"] == "diagram" and (entry.get("frames") or [None])[0] in figure_frames)]


def split_into_chunks(entries: list[dict[str, Any]],
                      max_entries: int,
                      max_characters: int) -> list[list[dict[str, Any]]]:
    """Consecutive chunks of at most `max_entries` entries and `max_characters` of text. A cut falls only between
    two moments, never inside one: the entries of one frame belong together, so a moment larger than the limits is a
    chunk of its own."""

    moments: list[list[dict[str, Any]]] = []
    for entry in entries:
        if moments and moments[-1][0]["timestamps"][0] == entry["timestamps"][0]:
            moments[-1].append(entry)
        else:
            moments.append([entry])
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for moment in moments:
        moment_size = sum(len(entry["text"]) for entry in moment)
        if current and (len(current) + len(moment) > max_entries or size + moment_size > max_characters):
            chunks.append(current)
            current, size = [], 0
        current += moment
        size += moment_size
    if current:
        chunks.append(current)

    return chunks


def list_moments(integration: dict[str, Any],
                 speech_chars: int) -> list[dict[str, Any]]:
    """The meeting as the parts call sees it: `{"number", "start", "screenKind", "title", "happens", "facts", "speech"}` per
    frame, in clock order, numbered from 1, with a moment of its own for speech before the first frame. `title`
    is the quoted text of the part the frame's screen outline names a title, empty when none is; `facts` the
    first thing it established, `happens` the frame's own account of the moment (where a section opens shows there
    when the outline names no title: one run's outlines never did, and its agenda started every chapter late), and
    `speech` what was said until the next moment. `speech_chars` cuts both `speech` and `happens`; at 0 the speech is
    left out and the account kept whole. A recording with no frame has a moment per speech paragraph, its speech whole:
    it is all the moment holds."""

    entries = integration.get("entries", [])
    starts = _list_moment_starts(integration)
    is_speech_only = not integration.get("frames") and not any(entry.get("source") == "frame" for entry in entries)
    screens = {float(frame["timestamp"]): str(frame.get("screen") or "") for frame in integration.get("frames") or []}
    happenings = {float(frame["timestamp"]): str(frame.get("third_person_description") or "").strip()
                  for frame in integration.get("frames") or []}
    moments = []
    for number, ((start, screen_kind), following) in enumerate(zip(starts, [*starts[1:], None]), start=1):
        end = following[0] if following else None
        at_start = [entry for entry in entries if start in entry.get("timestamps", [])]
        spoken = " ".join(entry["text"] for entry in entries if entry["kind"] == "speech"
                          and start <= entry["start"] and (end is None or entry["start"] < end))
        happens = happenings.get(start, "")
        moments.append({"number": number, "start": start, "screenKind": screen_kind,
                        "title": frame_markdown_utils.find_screen_title(screens.get(start, "")),
                        "happens": _shorten_text(happens, speech_chars) if speech_chars else happens,
                        "facts": next((entry["text"] for entry in at_start if entry["kind"] == "facts"), ""),
                        "speech": " ".join(spoken.split()) if is_speech_only
                        else _shorten_text(spoken, speech_chars) if speech_chars else ""})

    return moments


def split_by_parts(entries: list[dict[str, Any]],
                   part_starts: list[float]) -> list[list[dict[str, Any]]]:
    """The entries of each part, one list per start in `part_starts`, each entry in the last part that started at or
    before it. With no part, every entry is one group."""

    if not part_starts:
        return [list(entries)]
    groups: list[list[dict[str, Any]]] = [[] for _ in part_starts]
    for entry in entries:
        started = [index for index, start in enumerate(part_starts) if start <= entry["timestamps"][0]]
        groups[started[-1] if started else 0].append(entry)

    return groups


def merge_part_topics(outlines: list[dict[str, Any]],
                      outline_parts: list[int],
                      fold_entries: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The topics of every chunk's outline, per part of the meeting, in the order they first came up:
    `{"number", "id", "title", "part", "parts"}`, each part `{"chunk", "entryIds"}`, and the folds made. Within a part a
    title named again, whatever its case and spaces, is one topic. A topic whose title an EARLIER part used is a short
    return to that subject when, over all of its part's chunks, it holds at most `fold_entries` entries and its part
    holds another topic: it becomes a part of the earlier topic, and each folded chunk is listed as
    `{"title", "fromPart", "toPart", "entryIds"}`. REQUIREMENTS.md R9, tweak 2."""

    fold_targets = _decide_folds(outlines, outline_parts, fold_entries)
    topics: dict[tuple[int, str], dict[str, Any]] = {}
    folds: list[dict[str, Any]] = []
    for chunk_number, (outline, part) in enumerate(zip(outlines, outline_parts), start=1):
        for place, topic in enumerate([topic for topic in outline.get("topics", []) if topic.get("entry_ids")], start=1):
            key = _normalise_title(topic.get("title", ""))
            ids = list(topic["entry_ids"])
            target = fold_targets.get((part, key), part)
            if target != part:
                folds.append({"title": topic.get("title", "").strip(), "fromPart": part, "toPart": target, "entryIds": ids})
            # R12: the id a link names is the outline the topic first came up in and its place there, so redoing a
            # later outline never renames an earlier topic.
            merged = topics.setdefault((target, key), {"number": len(topics) + 1, "id": f"T{chunk_number:02d}.{place}",
                                                       "title": topic.get("title", "").strip(), "part": target,
                                                       "parts": []})
            merged["subjects"] = [*merged.get("subjects", []), *(topic.get("subjects") or [])]
            # One answer naming a title twice, a subject the talk came back to, is one part of that topic
            if merged["parts"] and merged["parts"][-1]["chunk"] == chunk_number:
                merged["parts"][-1]["entryIds"] += ids
            else:
                merged["parts"].append({"chunk": chunk_number, "entryIds": ids})

    return list(topics.values()), folds


def split_long_parts(topics: list[dict[str, Any]],
                     section_entries: int) -> list[dict[str, Any]]:
    """Each topic with every part longer than `section_entries` cut into consecutive parts of even size, none longer:
    one section call each, under the topic's one heading. 30 entries at 12 become 10, 10 and 10, never 12, 12 and 6."""

    split = []
    for topic in topics:
        parts = []
        for part in topic["parts"]:
            ids = part["entryIds"]
            # A part with no ids is kept as it is
            if not ids:
                parts.append(part)
                continue
            size = math.ceil(len(ids) / math.ceil(len(ids) / section_entries))
            parts += [{**part, "entryIds": ids[start:start + size]} for start in range(0, len(ids), size)]
        split.append({**topic, "parts": parts})

    return split


def expand_topic_starts(answer: dict[str, Any],
                        entries: list[dict[str, Any]]) -> dict[str, Any]:
    """An outline or a table of contents whose topics name only where they start, with each topic's `entry_ids` filled
    in: every entry from its start up to the next start, in the order `entries` were handed over, less the ones set
    aside. The program puts the topics in the order their starts come (a start is a place in the list, so its order
    is bookkeeping, never the model's to get right), and the first topic also holds every entry before its start. A
    topic whose start is no entry holds nothing, for the validator to report by name."""

    order = {entry["id"]: position for position, entry in enumerate(entries)}
    set_aside = set(answer.get("set_aside_entry_ids") or [])
    topics = [dict(topic) for topic in answer.get("topics") or []]
    placed = sorted((topic for topic in topics if _read_start_id(topic) in order),
                    key=lambda topic: order[_read_start_id(topic)])
    unplaced = [topic for topic in topics if _read_start_id(topic) not in order]
    starts = [order[_read_start_id(topic)] for topic in placed]
    for index, topic in enumerate(placed):
        start = 0 if index == 0 else starts[index]
        following = starts[index + 1] if index + 1 < len(placed) else len(entries)
        topic["entry_ids"] = [entry["id"] for entry in entries[start:following] if entry["id"] not in set_aside]
    for topic in unplaced:
        topic["entry_ids"] = []

    return {**answer, "topics": placed + unplaced}


def map_line_owners(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Every transcript line id a speech entry holds -> that entry's id, and every entry id -> itself: the notes file
    shows the line ids, so each one the agent names is filed under the entry that holds it (the owner, 2026-09-28)."""

    owners = {line: entry["id"] for entry in entries for line in entry.get("segments") or []}

    return {**owners, **{entry["id"]: entry["id"] for entry in entries}}


def map_start_owners(entries: list[dict[str, Any]]) -> dict[str, str]:
    """`map_line_owners`, plus each frame's own id (`F<frame>`) -> the frame's first note: a heading or chapter said to
    start at a frame starts at its first note, with no rejection and no retry for the program to settle."""

    firsts: dict[str, str] = {}
    for entry in entries:
        frame = _FRAME_NOTE_ID.match(str(entry["id"]))
        if frame:
            firsts.setdefault(frame.group(1), entry["id"])
    # A later line of a frame's heard speech starts at the moment after it: filed under its entry, a heading started
    # there took the whole entry, said over the slide before, while the slide's notes stayed with the heading before,
    # and the writer told that content twice (Lecture 7 chapter 3, DEFECTS 607). The frame's moment stays whole
    later = {line: after["id"] for entry, after in zip(entries, entries[1:]) if entry.get("heardByFrame")
             for line in entry.get("segments") or [] if line != entry["id"]}
    owners = {**map_line_owners(entries), **later}
    # A transcript line's id written without its leading zeros names the same line: a worker started a heading at
    # "S999" for S0999, and the chapter's headings were blocked (Lecture 9 chapter 10)
    unpadded = {f"S{int(line[1:])}": owner for line, owner in owners.items() if _PADDED_LINE_ID.match(line)}
    # Speech a frame heard is that frame's moment, listed after its notes: a heading said to start at its first line
    # starts at the frame's first note, or the frame's notes fill the heading before (38c3 talk 797 chapter 4, DEFECTS
    # 544)
    heard = {entry["id"]: firsts[frame.group(1)] for before, entry in zip(entries, entries[1:])
             if entry.get("heardByFrame") and (frame := _FRAME_NOTE_ID.match(str(before["id"])))}

    return {**unpadded, **firsts, **owners, **heard}


def resolve_set_aside(ids: list[str],
                      entries: list[dict[str, Any]]) -> list[str]:
    """The entries set aside when the agent named `ids`: an entry id as given; a transcript line id sets aside the speech
    entry holding it only when every line of that entry was named, since part of a block may still say something."""

    owners = map_line_owners(entries)
    lines_of = {entry["id"]: set(entry.get("segments") or [entry["id"]]) for entry in entries}
    named = set(ids)
    resolved = [entry_id for entry_id in ids if entry_id in lines_of]
    # A span is set aside by its lines only when all of them were named: its first line's id is its own id, never
    # counted as named when only later lines were
    resolved += [owner for owner in dict.fromkeys(owners.get(line) for line in ids if line not in lines_of and line in owners)
                 if lines_of[owner] <= named]

    return list(dict.fromkeys(resolved))


def _is_heading_only(entry: dict[str, Any]) -> bool:
    """Whether an entry's text is one heading line and nothing else, as a PDF's paragraph of its own heading is."""

    lines = [line for line in str(entry.get("text") or "").splitlines() if line.strip()]

    return len(lines) == 1 and _HEADING_ONLY.match(lines[0]) is not None


def _is_opened_by_title(entry: dict[str, Any],
                        title: str) -> bool:
    """Whether an entry's first line is a heading reading `title`, its marks, bold and a link's address left out."""

    lines = [line for line in str(entry.get("text") or "").splitlines() if line.strip()]
    if not lines or _HEADING_ONLY.match(lines[0]) is None:
        return False
    heading = _HEADING_WORDS.sub(" ", _LINK_ADDRESS.sub("", lines[0].lstrip().lstrip("#")))

    return " ".join(heading.split()).casefold() == " ".join(_HEADING_WORDS.sub(" ", title).split()).casefold()


def _build_title_key(title: str) -> str:
    """A title as a heading is matched to the document's own: its marks, a closing full stop and letter case left out."""

    return " ".join(_HEADING_WORDS.sub(" ", title).split()).rstrip(".").casefold()


def _is_same_title(first: str,
                   second: str) -> bool:
    """Whether two titles read the same, their marks, a closing full stop and letter case left out."""

    return _build_title_key(first) == _build_title_key(second)


def keep_document_headings(draft: dict[str, Any],
                           titles: list[str]) -> dict[str, Any]:
    """A PDF chapter's draft with only the headings titled as one of the document's own (`titles`), when it holds any:
    told to add none of its own, the writer still opened four of nine chapters with one (wiki run 3). With no title
    given, or none matching, the draft is kept as it came."""

    headings = draft.get("headings") or []
    kept = [heading for heading in headings
            if any(_is_same_title(str(heading.get("title") or ""), title) for title in titles)]

    return {**draft, "headings": kept} if titles and kept else draft


# A heading started after its subject's first notes: its `what` sentence fits the notes of the heading before it (60%
# of its words of five letters or more there, under 40% in its own). Over the public runs' 173 headings with a heading
# before them, 2 did, both froscon 3211's: a radio noise survey whose notes all fell under the heading before it, its
# own span holding the next subject (DEFECTS 304)
_HEADING_WORD = re.compile(r"[a-z][a-z0-9-]{4,}")
_HEADING_STOP = frozenset("about their there which these those where while other between using shows presenter speaker "
                          "explains describes demonstrates including includes provides".split())
_LATE_START_OWN_SHARE = 0.4
_LATE_START_BEFORE_SHARE = 0.6
_HEADING_LEAST_WORDS = 3


def _count_share_held(words: set[str],
                entries: list[dict[str, Any]]) -> float:
    text = " ".join(str(entry.get("text") or "") for entry in entries).lower()

    return sum(word in text for word in words) / len(words)


def move_late_heading_starts(draft: dict[str, Any],
                             entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The draft with each heading that starts after its subject's first notes started where they begin: the latest
    entry of the heading before it from which on that heading's notes still hold 60% of this heading's `what` words,
    the heading before keeping at least its first entry."""

    owners = map_start_owners(entries)
    position = {str(entry["id"]): index for index, entry in enumerate(entries)}
    headings = [dict(heading) for heading in draft.get("headings") or []]
    starts = [position.get(owners.get(str(heading.get("first_entry_id") or ""), ""), -1) for heading in headings]
    moved = False
    for index in range(1, len(headings)):
        before, own = starts[index - 1], starts[index]
        after = starts[index + 1] if index + 1 < len(headings) and starts[index + 1] > own else len(entries)
        words = {word for word in _HEADING_WORD.findall(str(headings[index].get("what") or "").lower())
                 if word not in _HEADING_STOP}
        if before < 0 or own <= before or len(words) < _HEADING_LEAST_WORDS:
            continue
        if (_count_share_held(words, entries[own:after]) >= _LATE_START_OWN_SHARE
                or _count_share_held(words, entries[before:own]) < _LATE_START_BEFORE_SHARE):
            continue
        start = next((at for at in range(own - 1, before, -1)
                      if _count_share_held(words, entries[at:own]) >= _LATE_START_BEFORE_SHARE), None)
        if start is None:
            continue
        headings[index]["first_entry_id"] = entries[start]["id"]
        starts[index] = start
        moved = True

    return {**draft, "headings": headings} if moved else draft


def build_table_of_contents(parts: list[dict[str, Any]],
                            groups: list[list[dict[str, Any]]],
                            drafts: list[dict[str, Any]],
                            is_started_at_heading: bool = False) -> dict[str, Any]:
    """The table of contents the PROGRAM builds (the owner, 2026-09-29: "this can be done programmatically"): one chapter
    per agenda point, titled as the agenda titles it and starting at its first entry, and under it the draft headings
    the chapter's own follow-up found, each with its start. A chapter whose draft headings were blocked gets one heading
    under its own title. Nothing is set aside and nothing is left unestablished here: no model is asked.
    `is_started_at_heading` (a PDF) starts each chapter at its first draft heading, the entries before it staying with
    the chapter before."""

    # A PDF's moment is a whole page, and a page that ends one section's table and opens the next section put the
    # table's last rows in the next chapter (pdf-02)

    by_chapter = {int(draft["chapter"]): draft for draft in drafts}
    chapters = []
    for part, group in zip(parts, groups):
        if not group:
            continue
        draft = by_chapter.get(int(part["number"]))
        headings = [{"title": str(heading.get("title") or "").strip(), "first_entry_id": _read_start_id(heading)}
                    for heading in (draft or {}).get("headings") or [] if str(heading.get("title") or "").strip()]
        start = group[0]["id"]
        if is_started_at_heading and headings:
            position = {entry["id"]: index for index, entry in enumerate(group)}
            named = [heading["first_entry_id"] for heading in headings if heading["first_entry_id"] in position]
            start = min(named, key=position.__getitem__) if named else start
            # A paragraph its own heading opens, before the first draft heading, starts the chapter: started at its
            # sub-heading "Z scheme", Wikipedia's "Light-dependent reactions" left its opening text to the chapter before
            own = [entry["id"] for entry in group[:position[start]] if _is_opened_by_title(entry, part["title"])]
            start = own[-1] if own else start
            # The paragraph holding only the chapter's own heading, just before its first draft heading, opens the
            # chapter: left before it, NIST's "## Appendix C. Glossary" stood in Appendix B's chapter
            while position[start] > 0 and _is_heading_only(group[position[start] - 1]):
                start = group[position[start] - 1]["id"]
            # A draft heading titled as its chapter, beside the document's own headings, only repeats the chapter's title
            # over its opening text: the chapter's first heading is moved back to the chapter's start anyway
            # (`flatten_table_of_contents`). The writer added one before four of nine chapters (wiki run 3)
            own_headings = [heading for heading in headings if not _is_same_title(heading["title"], part["title"])]
            headings = own_headings or headings
        chapters.append({"title": part["title"], "first_entry_id": start,
                         "headings": headings or [{"title": part["title"], "first_entry_id": start}]})

    return {"chapters": chapters, "set_aside_entry_ids": [], "not_established": []}


def _merge_headings_by_start(headings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The headings with each start once: a later heading starting in the same entry as an earlier one is folded into
    it, its title kept as one of the earlier heading's `subjects`. An audio recording's entry is a whole paragraph: a
    writer started five headings at five lines of one paragraph, and four were dropped as empty while the one heading
    kept a title naming a single one of the paragraph's five subjects."""

    kept: dict[str, dict[str, Any]] = {}
    for heading in headings:
        start = _read_start_id(heading)
        if start in kept:
            title = str(heading.get("title") or "").strip()
            if title:
                kept[start]["subjects"] = [*kept[start].get("subjects", []), title]
            continue
        kept[start] = dict(heading)

    return list(kept.values())


def flatten_table_of_contents(answer: dict[str, Any],
                              entries: list[dict[str, Any]]) -> dict[str, Any]:
    """A whole table of contents as the program uses it in memory: its chapters in the order their starts come, the first at the
    first entry, and every heading of every chapter in one list, each filled in by `expand_topic_starts` and numbered
    with the chapter its start falls in. Each chapter's start also starts a heading: the chapter's first heading is moved
    back to it, or, with no heading, one under the chapter's title holds it. So a heading listed under the wrong
    chapter lands in the chapter its entries belong to, and none crosses a chapter's start."""

    # 1. Every start named as an entry id, and the set-aside ids resolved; a chapter's headings under `topics` are
    # the files older runs left
    order = {entry["id"]: position for position, entry in enumerate(entries)}
    owners = map_start_owners(entries)
    answer = {**answer, "set_aside_entry_ids": resolve_set_aside(list(answer.get("set_aside_entry_ids") or []), entries),
              "chapters": [{**chapter, "first_entry_id": owners.get(_read_start_id(chapter),
                                                                    _read_start_id(chapter)),
                            "headings": _merge_headings_by_start([{**heading, "first_entry_id": owners.get(
                                _read_start_id(heading), _read_start_id(heading))}
                                for heading in chapter.get("headings", chapter.get("topics")) or []])}
                           for chapter in answer.get("chapters") or []]}

    # 2. The chapters in the order their starts come, the first at the first entry
    chapters = sorted((dict(chapter) for chapter in answer.get("chapters") or []
                       if _read_start_id(chapter) in order),
                      key=lambda chapter: order[_read_start_id(chapter)])
    if chapters and entries:
        chapters[0]["first_entry_id"] = entries[0]["id"]
    starts = [order[chapter["first_entry_id"]] for chapter in chapters]

    # 3. Each chapter's start opens a heading
    headings = [dict(topic) for chapter in answer.get("chapters") or [] for topic in chapter.get("headings") or []]
    for index, chapter in enumerate(chapters):
        following = starts[index + 1] if index + 1 < len(starts) else len(entries)
        inside = [topic for topic in headings if starts[index] <= order.get(_read_start_id(topic), -1) < following]
        if not any(order[_read_start_id(topic)] == starts[index] for topic in inside):
            if inside:
                min(inside, key=lambda topic: order[_read_start_id(topic)])["first_entry_id"] = chapter["first_entry_id"]
            else:
                headings.append({"title": chapter.get("title", ""), "first_entry_id": chapter["first_entry_id"]})

    # 4. Every heading filled in and numbered with its chapter
    expanded = expand_topic_starts({**answer, "topics": headings}, entries)
    for topic in expanded["topics"]:
        position = order.get(_read_start_id(topic))
        topic["chapter"] = (max((number for number, start in enumerate(starts, start=1) if start <= position), default=0)
                            if position is not None else 0)

    return {"chapters": [{"title": chapter.get("title", ""), "first_entry_id": chapter["first_entry_id"]} for chapter in chapters],
            "topics": expanded["topics"], "set_aside_entry_ids": list(answer.get("set_aside_entry_ids") or []),
            "not_established": list(answer.get("not_established") or [])}


def group_topics_by_chapter(table: dict[str, Any]) -> list[dict[str, Any]]:
    """A flattened table of contents as one outline per chapter, `{"topics": [{"title", "entry_ids"}]}`, in chapter
    order: what the merge of topics by title reads."""

    outlines: list[dict[str, Any]] = [{"topics": []} for _ in table.get("chapters") or []]
    for topic in table.get("topics") or []:
        if 1 <= topic.get("chapter", 0) <= len(outlines) and topic.get("entry_ids"):
            outlines[topic["chapter"] - 1]["topics"].append({"title": topic.get("title", ""), "entry_ids": topic["entry_ids"],
                                                             **({"subjects": topic["subjects"]} if topic.get("subjects") else {})})

    return outlines


def list_superseded_diagrams(sources: list[str]) -> set[int]:
    """The positions of the diagrams another one contains line for line. A diagram built up over several frames is
    extended with its lines kept (frames_processing.md), so the extension holds every line of each earlier version and only the
    extension needs drawing; of two identical diagrams the first is kept."""

    lines = [_list_diagram_lines(source) for source in sources]
    superseded = {index for index, own in enumerate(lines)
                  if own and any(other_index != index and own <= other and (own < other or other_index < index)
                                 for other_index, other in enumerate(lines))}

    return superseded


def list_entry_statuses(integration: dict[str, Any],
                        cited: set[str],
                        set_aside: set[str],
                        held: set[str] = frozenset()) -> dict[str, str]:
    """What became of every merged entry, by id, for the coverage table (REQUIREMENTS.md R8): `cited` by the
    document, `setAside` by an outline, `provenance` when no section has to cite it (`list_required_ids`: the speech a
    frame heard, the speech visual alignment notes; `held`: a screen's block the document writes from another note),
    and `missing` for an
    entry the document is written from that reached none of these. A transcript line takes its speech entry's status."""

    document_entries = select_document_entries(integration)
    # Only an entry a section must cite is missing when none does; one that may be cited and never has to be is kept
    # beside the document by design
    required_ids = set(list_required_ids(document_entries))
    # A line of a span of unread speech takes what became of its entry
    group_of = map_line_owners(document_entries)
    statuses = {}
    for entry in integration.get("entries", []):
        entry_id = group_of.get(entry["id"], entry["id"])
        if entry_id in cited:
            statuses[entry["id"]] = "cited"
        elif entry_id in set_aside:
            statuses[entry["id"]] = "setAside"
        elif entry_id in required_ids and entry_id not in held:
            statuses[entry["id"]] = "missing"
        else:
            statuses[entry["id"]] = "provenance"

    return statuses


def build_inputs_hash_path(written_path: Path) -> Path:
    """Where the hash of what a part was written from is kept, beside the part."""

    return written_path.with_name(f"{written_path.stem}.inputs.sha256")


def is_inputs_hash_current(written_path: Path,
                           inputs: dict[str, Any]) -> bool:
    """Whether the hash kept beside the part is the hash of `inputs`: the part was written from these inputs."""

    hash_path = build_inputs_hash_path(written_path)

    return hash_path.is_file() and hash_path.read_text(encoding="utf-8") == compute_inputs_hash(inputs)


def compute_inputs_hash(inputs: dict[str, Any]) -> str:
    """The SHA-256 of `inputs` as sorted JSON: equal inputs give one hash across runs, so a part written from the same
    inputs is known as written."""

    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
