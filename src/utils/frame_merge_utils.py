"""Layer 2, REQUIREMENTS.md R3: order, merge and de-duplicate layer 1. Pure functions, no model.

A model asked to merge JSON is usually right, which passes review and is wrong in one record. So
this is code, and it writes no prose and states no claim: every entry is a piece of text that came
from the transcript or from one or more frames, with where it came from beside it.

THE CONFLICT RULE, stated: identical text of the same kind, compared without case and extra spaces,
is ONE entry that keeps its first spelling and lists every frame that had it. Nothing else is merged.
A disagreement between frames is never resolved here: it is REPORTED, from the `contradicts_earlier`
notes the frames themselves made while the frame was open.

A diagram built up over several frames arrives as one `diagram` entry per version, each later one an
extension of the one before; the entry with the latest frame is the most complete.

A WEB ADDRESS a frame could only read in part ends in "…" (REQUIREMENTS.md R8). It is completed by one rule and
never guessed: when the text before the "…" starts exactly one longer address another frame read, the two are one
entry, under the longer address. One that starts two different longer addresses, one cut in the middle, and one not
cut at all stay as they are.

THE SPEECH NO MODEL READ is listed as `unreadSpeech` spans. A frame's call was handed the words spoken while
it was on screen, so its facts already carries them; the words before the first frame, while a frame had no
accepted record, and while a repeat reused its original's notes reached no model. Phase 4 writes the document
from the frames' facts and from the speech of these spans.
"""

from __future__ import annotations

import re
from typing import Any

from models import output_contract
from utils import addition_utils
from utils import frame_markdown_utils
from utils import mermaid_text_utils
from utils import moment_label_utils
from utils import screenshot_suggestion_utils
from utils import section_markdown_utils

__all__ = ["merge_frame_records", "NOTE_KINDS"]

# Within one moment of the video: what was said, what it taught, the diagram on screen, how the speech related to the
# frame, and what it left open. The screen itself is no entry: it stays on its frame, whole, as the outline phase 3
# wrote (`screen`).
# Every kind of note `_list_frame_entries` makes from a frame, in this order; `03_notes.md` prints a frame's notes in it.
NOTE_KINDS = ("facts", "diagram", "markdown", "speech_visual_alignment", "open_question", "url", "action_item", "question")
_KIND_ORDER = {kind: rank for rank, kind in enumerate(("speech", *NOTE_KINDS))}
_CUT_MARKS = ("…", "...")
_NO_RECORD = "no accepted record"
# What phase 2 and phase 3 wrote on a frame's item beside its notes, carried onto the merged frame for the notes file.
_ITEM_FIELDS = ("path", "frameNumber", "score", "box", "screenBox", "repeatOf", "stepOf", "builtFrom", "medium")


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _list_speech_entries(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per spoken segment, with the speaker phase 1 placed on it when it placed one."""

    return [{"kind": "speech", "text": segment["text"].strip(), "start": segment["start"], "end": segment["end"],
             "timestamps": [segment["start"]], "source": "transcript",
             **({"speaker": segment["speaker"]} if segment.get("speaker") else {})}
            for segment in transcript.get("segments", []) if str(segment.get("text", "")).strip()]


# A value a fact and a block of a page share: a number, a date or a code holding a digit
_BLOCK_VALUE = re.compile(r"(?<![\w-])[\w-]*\d[\w.,-]*(?<![.,])")
# A headed part shares at least this many values with the fact it is placed after
_PART_FACT_MIN_VALUES = 2


def _split_at_headings(markdown: str) -> list[str]:
    """`markdown` cut where each heading line starts, and where a table starts after a blank line below another table,
    the text before the first heading the first part; empty parts left out. A page's notes wrote the last rows of one
    section's table and the first rows of the next section's with no heading between them, and both were filed under
    the next section (pdf-02)."""

    parts: list[list[str]] = [[]]
    is_after_blank = is_in_fence = False
    for line in markdown.splitlines():
        is_table = line.lstrip().startswith("|")
        is_new_table = (is_table and is_after_blank
                        and any(earlier.lstrip().startswith("|") for earlier in parts[-1]))
        # A "#" line inside a code fence is the code's comment ("# gateway.yaml"), never a heading (pdf-05)
        if not is_in_fence and (line.lstrip().startswith("#") or is_new_table) and parts[-1]:
            parts.append([])
        parts[-1].append(line)
        is_in_fence ^= line.lstrip().startswith(("```", "~~~"))
        is_after_blank = not line.strip()

    return [text for text in ("\n".join(part).strip() for part in parts) if text]


def _list_page_texts(facts: list[str],
                     markdown: str,
                     is_mid_section: bool = False) -> list[tuple[str, str]]:
    """A PDF page's facts and its `## As markdown` in the order its sections stand: the markdown cut at its headings,
    the part before the first heading (a table continued from the page before) ahead of every fact, each headed part
    after the facts of its section. A section's facts start at the first fact naming its heading ("In January ..."),
    or else at the fact sharing most values with the part. One note for the page put the last rows of one section's
    table and the next sections' tables under the page's last heading, so a chapter starting at a heading of the page
    was handed the tables of the sections around it (pdf-02, DEFECTS 140)."""

    parts = _split_at_headings(markdown)
    texts: list[tuple[str, str]] = [("facts", fact) for fact in facts]
    if not parts:
        return texts
    # What stands before the page's first heading is first only on a page that opens mid-section, its text not opening
    # with a heading (put after the facts, March's last weeks went under the next page's Conclusions, pdf-02); on a
    # page opening with its heading it is the page's own, after its facts (put first, a slide's bullets were filed
    # before them, pdf-03, and a page's own table and commands became the first notes of its chapter, pdf-05)
    if len(parts) == 1 and not parts[0].lstrip().startswith("#"):
        if is_mid_section and parts[0].lstrip().startswith("|"):
            return [("markdown", markdown.strip()), *texts]
        return [*texts, ("markdown", markdown.strip())]
    leading = ([] if parts[0].lstrip().startswith("#") or not is_mid_section
               else [("markdown", parts.pop(0))])
    starts = [_find_section_start(facts, part) for part in parts]
    after: dict[int, list[str]] = {}
    for index, part in enumerate(parts):
        later = [start for start in starts[index + 1:] if start is not None and start > (starts[index] or -1)]
        end = (later[0] - 1) if later else len(facts) - 1
        after.setdefault(max(end, starts[index] or -1), []).append(part)
    placed: list[tuple[str, str]] = list(leading) + [("markdown", part) for part in after.get(-1, [])]
    for index, fact in enumerate(texts):
        placed.append(fact)
        placed += [("markdown", part) for part in after.get(index, [])]

    return placed


def _find_section_start(facts: list[str],
                        part: str) -> int | None:
    """The fact a headed part's section starts at: the first naming its heading, else the one sharing most values
    with the part (two at least); None with neither."""

    heading = part.splitlines()[0].lstrip("#").replace("*", "").strip().casefold()
    named = [index for index, fact in enumerate(facts) if heading and heading in fact.casefold()]
    if named:
        return named[0]
    values = {value.casefold() for value in _BLOCK_VALUE.findall(part)}
    shared = [len(values & {value.casefold() for value in _BLOCK_VALUE.findall(fact)}) for fact in facts]
    best = max(shared, default=0)

    return shared.index(best) if best >= _PART_FACT_MIN_VALUES else None


# A PDF page's facts and markdown parts keep the order `_list_page_texts` gives them, under one rank: sorted by kind,
# every markdown part came after every fact, and page 7's January table was filed under March (DEFECTS 140)
_PAGE_ORDER = "pageOrder"


def _find_entry_rank(entry: dict[str, Any]) -> tuple:
    """Where an entry stands: its time, then its kind's rank, a PDF page's fact or markdown part by its place on the page."""

    if _PAGE_ORDER in entry:
        return entry["timestamps"][0], _KIND_ORDER["facts"], entry[_PAGE_ORDER]

    return entry["timestamps"][0], _KIND_ORDER[entry["kind"]], 0


def _list_mid_section_frames(frames_file: dict[str, Any],
                             transcript: dict[str, Any]) -> frozenset[int]:
    """The PDF frames whose page opens mid-section: its text's first block is no heading (a table continued from the
    page before, a paragraph run on)."""

    first_blocks: dict[int, str] = {}
    for segment in transcript.get("segments") or []:
        if segment.get("page") is not None:
            first_blocks.setdefault(int(segment["page"]), str(segment.get("text") or ""))
    mid_pages = {page for page, text in first_blocks.items() if not text.lstrip().startswith("#")}

    return frozenset(int(item["index"]) for item in frames_file.get("items", [])
                     if item.get("medium") == "pdf" and int(item.get("page") or 0) in mid_pages)


_QUOTED_OUTLINE_TEXT = re.compile(r'"([^"]+)"')


def _list_outline_texts(record: dict[str, Any]) -> set[str]:
    """The texts the frame's own outline quotes, normalised: the lines of a terminal, a bullet, a label."""

    return {_normalise(text) for text in _QUOTED_OUTLINE_TEXT.findall(str(record.get("screen") or ""))}


def _list_frame_entries(records: list[dict[str, Any]],
                        tags: dict[int, list[str]],
                        pdf_frames: frozenset[int] = frozenset(),
                        mid_section_frames: frozenset[int] = frozenset(),
                        figure_frames: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """One entry per distinct text per kind, keeping the first spelling, every frame that had it, and the tags of
    those frames. A PDF page's (`pdf_frames`) markdown is cut at its headings and placed among its facts."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        outline_texts = _list_outline_texts(record)
        markdown = frame_markdown_utils.strip_markdown_fence(str(record.get("as_markdown") or ""))
        if record["index"] in pdf_frames:
            # A part of only a heading and sentences repeats the page's facts, which hold every sentence of the page:
            # handed over, a writer wrote "The wiki page presents these rules verbatim" for it under every heading (pdf-04)
            texts = [(kind, text) for kind, text in
                     _list_page_texts([str(text) for text in record.get("facts", [])], markdown,
                                      record["index"] in mid_section_frames)
                     if kind != "markdown" or not section_markdown_utils.is_prose_block(text)]
        else:
            texts = [("facts", text) for text in record.get("facts", [])]
        # A PDF figure is placed whole as its cut, so a diagram of it beside its facts only redraws it, and a writer
        # placed both, the diagram wrong where the cut was right (wiki run 2); with no fact it is the figure's one note
        if record["index"] not in figure_frames or not texts:
            texts += [("diagram", mermaid_text_utils.name_bare_nodes(str(record.get("diagram", ""))))]
        # The screen's code, table or list as phase 3 wrote it in markdown: a document writes the block, never a picture
        if record["index"] not in pdf_frames:
            texts += [("markdown", markdown)]
        texts += [("speech_visual_alignment", record.get("speech_visual_alignment", ""))]
        # A PDF figure's open questions ask what its picture leaves unlabelled ("the peaks' values are not marked on
        # the axis"): nobody can answer a picture, and the document's text answers the subject, so they are no open
        # point of the document (wiki run 2's five open points were all such)
        if record["index"] not in figure_frames:
            texts += [("open_question", text) for text in record.get("open_questions", [])]
        texts += [_sort_address(_render_url(entry)) for entry in record.get("urls", [])]
        texts += [("action_item", text) for text in record.get("action_items", [])]
        texts += [("question", text) for text in record.get("questions_asked", [])]
        for position, (kind, text) in enumerate(texts):
            if not str(text).strip():
                continue
            entry = merged.setdefault((kind, _normalise(str(text))),
                                      {"kind": kind, "text": str(text).strip(), "frames": [], "timestamps": [],
                                       "source": "frame"})
            if record["index"] in pdf_frames and kind in ("facts", "markdown") and _PAGE_ORDER not in entry:
                entry[_PAGE_ORDER] = position
            # A fact that is a text its own frame's outline quotes word for word (a terminal's line, a slide's bullet)
            # restates the screen: the writer may cite it and need not (release test 2's frame 71 held ten such lines
            # as facts, and each was a finding of the writer's check until written)
            if kind == "facts" and record["index"] not in pdf_frames:
                is_copy = _normalise(str(text).strip().strip("`").strip()) in outline_texts
                entry["isOutlineCopy"] = is_copy if "isOutlineCopy" not in entry else (entry["isOutlineCopy"] and is_copy)
            if record["index"] not in entry["frames"]:
                entry["frames"].append(record["index"])
                entry["timestamps"].append(record["timestamp"])
            _add_tags(entry, tags.get(record["index"], []))

    return list(merged.values())


def _add_tags(entry: dict[str, Any],
              tags: list[str]) -> None:
    """R12: an entry carries the tags of every frame it came from; none when no frame had any."""

    if tags:
        entry["tags"] = [*entry.get("tags", []), *[tag for tag in tags if tag not in entry.get("tags", [])]]


def _clean_record_tags(records: list[dict[str, Any]]) -> tuple[dict[int, list[str]], int]:
    """Each record's tags, cleaned, a subject in the spelling the meeting uses first; and how many tags were dropped
    (R12)."""

    cleaned = {record["index"]: moment_label_utils.clean_tags(record.get("tags") or []) for record in records}
    spellings = moment_label_utils.build_tag_spellings([tag for index in sorted(cleaned) for tag in cleaned[index][0]])
    tags = {index: list(dict.fromkeys(spellings[moment_label_utils.normalise_tag(tag)] for tag in kept))
            for index, (kept, _) in cleaned.items()}

    return tags, sum(len(dropped) for _, dropped in cleaned.values())


def _copy_tags_to_speech(entries: list[dict[str, Any]],
                         records: list[dict[str, Any]],
                         tags: dict[int, list[str]]) -> None:
    """Every transcript line takes the subject tags of the frame on screen when it was said, marked inherited (R12).
    The kinds stay with the frame: they say what its notes are, not what was said."""

    on_screen = sorted(records, key=lambda record: record["timestamp"])
    for entry in entries:
        if entry["kind"] != "speech":
            continue
        shown = [record for record in on_screen if record["timestamp"] <= entry["timestamps"][0]]
        subjects = moment_label_utils.list_subject_tags(tags.get(shown[-1]["index"], [])) if shown else []
        if subjects:
            entry["tags"], entry["tagsInherited"] = list(subjects), True


# A path on the presenter's machine is no web address: a frame's notes listed an editor's run line,
# a Windows user folder's `.../lec3.py`, among its addresses, and every address is written into the text as a link, the user's
# folder with it (2026-10-12). It is kept as a fact, its user folder hidden.
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|file://|/(?:Users|home)/)")
_USER_FOLDER = re.compile(r"((?:[A-Za-z]:[\\/]|file:///?(?:[A-Za-z]:/)?|/)(?:Users|home)[\\/])[^\\/|\s]+", re.IGNORECASE)


def _sort_address(text: str) -> tuple[str, str]:
    """`("url", text)` for a web address, `("facts", text)` with the user folder hidden for a local file path."""

    if not _LOCAL_PATH.match(text.strip()):
        return "url", text

    return "facts", _USER_FOLDER.sub(r"\1[user]", text)


def _render_url(entry: Any) -> str:
    """One address as the one line every later step reads: `URL | what it is | how it appears` (R8). An older run's
    plain string is already that line, and is passed through."""

    if not isinstance(entry, dict):
        return str(entry)
    parts = [str(entry.get("url") or "").strip(), str(entry.get("what_it_is") or "").strip(),
             str(entry.get("how_it_appears") or "").strip()]

    return " | ".join(part for part in parts if part)


def _read_address(text: str) -> str:
    """The web address of a `url` entry, written `URL | what the page is`."""

    return text.split(" | ", 1)[0].strip()


def _read_cut_prefix(address: str) -> str | None:
    """The part of a cut-off address before its "…", or None when it is not cut at its end, is cut inside too,
    or has no readable start."""

    mark = next((mark for mark in _CUT_MARKS if address.endswith(mark)), "")
    prefix = address[:-len(mark)] if mark else None
    is_cut_inside = prefix is not None and any(other in prefix for other in _CUT_MARKS)

    # An address with no readable start matches every other address, so it is never folded into one
    return None if is_cut_inside or not prefix else prefix


def _join_cut_off_addresses(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries with each cut-off web address that starts exactly one longer address folded into that one's entry,
    its frames and times added."""

    addresses = {id(entry): _read_address(entry["text"]) for entry in entries if entry["kind"] == "url"}
    joined: set[int] = set()
    for entry in entries:
        prefix = _read_cut_prefix(addresses[id(entry)]) if entry["kind"] == "url" else None
        if prefix is None:
            continue
        longer: dict[str, dict[str, Any]] = {}
        for other in entries:
            address = addresses.get(id(other), "")
            if (other is not entry and address.startswith(prefix) and len(address) > len(prefix)
                    and _read_cut_prefix(address) is None):
                longer.setdefault(address, other)
        if len(longer) != 1:
            continue
        target = next(iter(longer.values()))
        for frame, timestamp in zip(entry["frames"], entry["timestamps"]):
            if frame not in target["frames"]:
                target["frames"].append(frame)
                target["timestamps"].append(timestamp)
        # The folded entry's frames bring their tags (R12: an entry carries the tags of every frame it came from)
        _add_tags(target, entry.get("tags") or [])
        pairs = sorted(zip(target["frames"], target["timestamps"]))
        target["frames"], target["timestamps"] = [frame for frame, _ in pairs], [timestamp for _, timestamp in pairs]
        joined.add(id(entry))

    return [entry for entry in entries if id(entry) not in joined]


def _add_entry_ids(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries with their ids: a transcript line is `S0001` on, in clock order; a frame's note is
    `F<frame>.<n>`, counted within the first frame that had it, so an id says where to look."""

    numbered = []
    speech_count = 0
    frame_counts: dict[int, int] = {}
    for entry in entries:
        if entry["kind"] == "speech":
            speech_count += 1
            entry_id = f"S{speech_count:04d}"
        else:
            frame = int(entry["frames"][0])
            frame_counts[frame] = frame_counts.get(frame, 0) + 1
            entry_id = f"F{frame}.{frame_counts[frame]}"
        numbered.append({"id": entry_id, **entry})

    return numbered


def _list_gaps(frames_file: dict[str, Any],
               records: list[dict[str, Any]],
               blocked_reasons: dict[str, str]) -> list[dict[str, Any]]:
    accepted = {record["slug"] for record in records}

    return [{"slug": item["slug"], "index": item["index"], "timestamp": item["timestamp"],
             "reason": blocked_reasons.get(item["slug"], _NO_RECORD)}
            for item in frames_file.get("items", []) if item["slug"] not in accepted]


def _is_copied_record(item: dict[str, Any]) -> bool:
    """Whether a repeat's notes are a copy of its original's, made with no model call by an earlier version of phase 3:
    built from the original alone and saying so."""

    builds_on = " ".join(str(line) for line in (item.get("record") or {}).get("builds_on") or [])

    return item.get("builtFrom") == [item.get("repeatOf")] and "its notes are reused" in builds_on


def _list_unread_spans(frames_file: dict[str, Any],
                       records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`{"start", "end", "why"}` for each span whose speech no frame's call was handed; `end` None runs to the
    end of the recording. A return to an earlier screen is read like any other frame and hears its own speech; only a
    copy an earlier version of phase 3 made of its original's notes left that speech unread."""

    items = sorted(frames_file.get("items", []), key=lambda item: item["timestamp"])
    if not items:
        return [{"start": 0.0, "end": None, "why": "no frame"}]
    accepted = {record["slug"] for record in records}
    spans = []
    if items[0]["timestamp"] > 0:
        spans.append({"start": 0.0, "end": items[0]["timestamp"], "why": "before the first frame"})
    # A frame dropped after the frame before it was read: its words were never handed to any call
    for dropped in frames_file.get("droppedFrames", []):
        if dropped.get("isSpeechUnheard"):
            later = [item["timestamp"] for item in items if item["timestamp"] > dropped["timestamp"]]
            spans.append({"start": dropped["timestamp"], "end": later[0] if later else None,
                          "why": f"{dropped['slug']} was dropped after the frame before it was read"})
    for item, following in zip(items, [*items[1:], None]):
        end = following["timestamp"] if following else None
        if item["slug"] not in accepted:
            spans.append({"start": item["timestamp"], "end": end, "why": f"{item['slug']} has {_NO_RECORD}"})
        elif item.get("repeatOf") in accepted and _is_copied_record(item):
            spans.append({"start": item["timestamp"], "end": end,
                              "why": f"{item['slug']} reuses the notes of {item['repeatOf']}"})

    return sorted(spans, key=lambda span: span["start"])


def merge_frame_records(transcript: dict[str, Any],
                        frames_file: dict[str, Any],
                        records: list[dict[str, Any]],
                        blocked_reasons: dict[str, str]) -> dict[str, Any]:
    """`{"schemaVersion", "entries", "frames", "contradictions", "gaps", "unreadSpeech", "counts"}`. `frames` lists each accepted
    frame's index, time and screen kind. `blocked_reasons` is slug -> why a frame has no accepted record, for the
    frames the run blocked."""

    records = sorted(records, key=lambda record: record["index"])

    # STEP 1: every piece of text the video established, in clock order, each with a stable id
    tags, dropped_tags = _clean_record_tags(records)
    pdf_frames = frozenset(int(item["index"]) for item in frames_file.get("items", [])
                           if item.get("medium") == "pdf" and "index" in item)
    figure_frames = frozenset(int(item["index"]) for item in frames_file.get("items", [])
                              if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM and "index" in item)
    entries = _list_speech_entries(transcript) + _join_cut_off_addresses(
        _list_frame_entries(records, tags, pdf_frames, _list_mid_section_frames(frames_file, transcript), figure_frames))
    _copy_tags_to_speech(entries, records, tags)
    entries.sort(key=_find_entry_rank)
    for entry in entries:
        entry.pop(_PAGE_ORDER, None)
    entries = _add_entry_ids(entries)

    # STEP 2: what the frames said contradicts an earlier frame, collected and never resolved
    contradictions = [{"frame": record["index"], "timestamp": record["timestamp"], "text": text}
                      for record in records
                      for text in record.get("contradicts_earlier", []) if str(text).strip()]

    # STEP 3: the frames that returned nothing, so a reader knows what the entries do not cover
    gaps = _list_gaps(frames_file, records, blocked_reasons)
    kinds = [entry["kind"] for entry in entries]
    counts = {"frames": len(frames_file.get("items", [])), "framesAccepted": len(records), "gaps": len(gaps),
              "entries": len(entries), "droppedTags": dropped_tags, **{kind: kinds.count(kind) for kind in _KIND_ORDER}}

    # STEP 4: the spans whose speech no model read, which phase 4 writes from as they are
    unread = _list_unread_spans(frames_file, records)

    # STEP 5: every frame with what its screen showed, for the moments phase 4's parts call sees (R9), what happened
    # on it (R15), the screen's outline (R17) and how phase 3 read the moment (`takeAways`): each kept on
    # the frame, never an entry
    item_of = {item["slug"]: item for item in frames_file.get("items", [])}
    frames = [{**{key: item_of.get(record["slug"], {}).get(key) for key in _ITEM_FIELDS},
               "index": record["index"], "timestamp": record["timestamp"], "screenKind": str(record.get("screen_kind") or ""),
               "third_person_description": str(record.get("third_person_description") or "").strip(),
               "screen": str(record.get("screen") or "").rstrip(),
               # A line written again with only "→ nothing on screen" added is kept once (lecture-1 frames 22-23)
               "takeAways": addition_utils.list_distinct_entries([frame_markdown_utils.unwrap_code_entry(str(line))
                                                                  for line in record.get("take_aways") or []
                                                                  if str(line).strip()]),
               "tags": tags.get(record["index"], []),
               "screenshots": screenshot_suggestion_utils.read_suggestions(record),
               "buildsOn": [str(text) for text in record.get("builds_on") or [] if str(text).strip()]}
              for record in records]

    return {"schemaVersion": 1, "entries": entries, "frames": frames, "contradictions": contradictions, "gaps": gaps,
            "unreadSpeech": unread, "counts": counts}
