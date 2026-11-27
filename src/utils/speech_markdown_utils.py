"""`02_speech.md`, phase 2's answer for an audio recording: the transcript's lines as
paragraphs of clean speech, each one speaker's turn on one point, under a heading naming the lines it covers. Pure
text work: the transcript's numbered lines, the reader of the model's answer, and the file's own rendering, which the
program reads back.

    # Speech

    Profile: conversation

    ## S0001-S0004 · Speaker A
    Welcome to the weekly call. …

The `Profile:` line is there only when phase 2's call was asked to pick the ingesting profile.
"""

from __future__ import annotations

import math
import re
from typing import Any

from utils import clock_utils

# A derived span is taken when the text fits it no worse than this, on the log scale, than the span written: a line's
# few words moved to the paragraph before or after barely change the fit, a run of lines whose speech was left out does
_FIT_TOLERANCE = 0.15

__all__ = ["describe_transcript_span", "SPEECH_TITLE", "SPEAKER_PATTERN", "list_numbered_lines", "list_speaker_turns",
           "parse_speech", "render_speech", "list_paragraph_spans", "render_span", "name_turn_speakers",
           "close_paragraph_spans", "build_settled_paragraphs"]

SPEECH_TITLE = "Speech"
# A PDF's paragraphs, written by the program from its text: no speech and no speaker (the owner: "there are no speaker
# in pdf, the speaker are for the audio flow")
PARAGRAPHS_TITLE = "Paragraphs"
SPEAKER_PATTERN = re.compile(r"Speaker [A-Z]")
_HEADING = re.compile(r"^##\s+(S\d{4,})(?:\s*[-–]\s*(S\d{4,}))?(?:\s*·\s*(.+?))?\s*$")
_PROFILE = re.compile(r"^Profile:\s*(\S.*?)\s*$", re.IGNORECASE)


def render_span(first: str,
                last: str) -> str:
    """The ids a paragraph or turn covers: `S0004` for one line, `S0004-S0009` for several."""

    return first if first == last else f"{first}-{last}"


def describe_transcript_span(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The fields a request's header gives about its transcript lines: the length as a clock, the count, and the first and
    last line ids (`S0001` for both when there is no line)."""

    return {"LENGTH": clock_utils.render_clock(float(lines[-1]["end"]) if lines else 0.0),
            "COUNT": len(lines),
            "FIRST": lines[0]["id"] if lines else "S0001",
            "LAST": lines[-1]["id"] if lines else "S0001"}


def list_numbered_lines(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """The transcript's spoken lines in clock order, each `{"id", "start", "end", "text", "speaker"}`: `S0001` on,
    numbered as the merge numbers them (`frame_merge_utils`), an empty line left out; `speaker` is phase 1's
    `Speaker <letter>`, or None when diarization was off or placed no speaker on the line."""

    spoken = sorted((segment for segment in transcript.get("segments", []) if str(segment.get("text", "")).strip()),
                    key=lambda segment: segment["start"])

    return [{"id": f"S{number:04d}", "start": segment["start"], "end": segment["end"], "text": segment["text"].strip(),
             "speaker": segment.get("speaker")}
            for number, segment in enumerate(spoken, start=1)]


def list_speaker_turns(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines grouped into speaker turns, each `{"first", "last", "speaker", "lines"}`, when phase 1 placed speakers:
    lines one after another of one speaker are one turn, and a line with no speaker (speech said at once) belongs to
    the turn before it, or to the first turn when it opens the recording. Empty when no line has a speaker, so a
    transcript without diarization is sent and judged line by line as before."""

    if not any(line.get("speaker") for line in lines):
        return []
    opening = next(line["speaker"] for line in lines if line.get("speaker"))
    turns: list[dict[str, Any]] = []
    for line in lines:
        speaker = line.get("speaker") or (turns[-1]["speaker"] if turns else opening)
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["last"] = line["id"]
            turns[-1]["lines"].append(line)
        else:
            turns.append({"first": line["id"], "last": line["id"], "speaker": speaker, "lines": [line]})

    return turns


def _compute_misfit(paragraph: dict[str, Any],
                    lines: list[dict[str, Any]],
                    first: int,
                    last: int) -> float:
    """How far a paragraph's words are from the words spoken on lines `first` to `last`, as the distance of their ratio
    from 1 on a log scale: 0 for as many words as were said."""

    spoken = sum(len(str(line.get("text") or "").split()) for line in lines[first:last + 1])
    written = len(str(paragraph.get("text") or "").split())

    return abs(math.log(max(written, 1) / max(spoken, 1)))


def close_paragraph_spans(parsed: dict[str, Any],
                          lines: list[dict[str, Any]]) -> dict[str, Any]:
    """`parsed` with each paragraph's last line set to the line before the next paragraph's first (the last paragraph's
    to the transcript's last line), when its text fits that span at least as well as the span its heading names: a
    model counting 448 lines wrote a heading's last line short of its text and started 7 paragraphs on the line the one
    before ended on, and the run blocked. Lines whose speech the answer left out fit the longer span worse, so the
    check still names them. The first lines are the model's, in order; when one is unknown or out of order, the
    paragraphs are left as written for the check to name."""

    order = {line["id"]: position for position, line in enumerate(lines)}
    paragraphs = parsed.get("paragraphs") or []
    starts = [order.get(paragraph.get("first")) for paragraph in paragraphs]
    if not lines or not paragraphs or None in starts or starts != sorted(set(starts)):
        return parsed
    closed = []
    for paragraph, start, end in zip(paragraphs, starts, [start - 1 for start in starts[1:]] + [len(lines) - 1]):
        written = order.get(paragraph.get("last"))
        if written is None or written == end or _compute_misfit(paragraph, lines, start, end) <= (
                _compute_misfit(paragraph, lines, start, written) + _FIT_TOLERANCE):
            closed.append({**paragraph, "last": lines[end]["id"]})
        else:
            closed.append(paragraph)

    return {**parsed, "paragraphs": closed}


def name_turn_speakers(parsed: dict[str, Any],
                       lines: list[dict[str, Any]]) -> dict[str, Any]:
    """`parsed` with each paragraph that lies inside one speaker turn named by that turn's speaker: the program knows it
    from the voices, and a model copying the letter wrote the next turn's for a one-line turn, which blocked the run.
    A paragraph running across turns, or a transcript with no turns, keeps the letter written."""

    turn_of = {line["id"]: turn for turn in list_speaker_turns(lines) for line in turn["lines"]}
    paragraphs = []
    for paragraph in parsed.get("paragraphs") or []:
        first, last = turn_of.get(paragraph.get("first")), turn_of.get(paragraph.get("last"))
        is_one_turn = first is not None and first is last
        paragraphs.append({**paragraph, "speaker": first["speaker"]} if is_one_turn else paragraph)

    return {**parsed, "paragraphs": paragraphs}


def _drop_outer_fence(text: str) -> str:
    """`text` without the fence a model's answer is wrapped in, when its first and last lines are one; a code block
    inside a paragraph keeps its fences."""

    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])

    return text


def parse_speech(text: str) -> tuple[dict[str, Any] | None, str]:
    """`({"profile", "paragraphs"}, "")` from the answer or the file, each paragraph `{"first", "last", "speaker",
    "text"}`; `(None, why)` when it holds no paragraph or text stands outside one."""

    profile: str | None = None
    paragraphs: list[dict[str, Any]] = []
    for number, line in enumerate(_drop_outer_fence(text).splitlines(), start=1):
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        if heading:
            first, last, speaker = heading.groups()
            paragraphs.append({"first": first, "last": last or first, "speaker": speaker or "", "lines": []})
        # A fence inside a paragraph is its code block's, which a PDF's paragraph holds whole; one before any paragraph
        # opens a model's answer
        elif paragraphs:
            paragraphs[-1]["lines"].append(line)
        elif stripped.startswith("```"):
            continue
        elif profile_match := _PROFILE.match(stripped):
            profile = profile_match.group(1)
        elif stripped and not stripped.startswith("# "):
            return None, f'line {number} stands before the first "## S<first>-S<last> · Speaker <letter>" heading'
    if not paragraphs:
        return None, 'no "## S<first>-S<last> · Speaker <letter>" paragraph heading'

    return {"profile": profile,
            "paragraphs": [{"first": paragraph["first"], "last": paragraph["last"], "speaker": paragraph["speaker"],
                            "text": "\n".join(paragraph["lines"]).strip()} for paragraph in paragraphs]}, ""


def render_speech(parsed: dict[str, Any]) -> str:
    """The file's text: the title, the profile line when there is one, then each paragraph under its heading."""

    is_spoken = any(paragraph.get("speaker") for paragraph in parsed["paragraphs"])
    lines = [f"# {SPEECH_TITLE if is_spoken else PARAGRAPHS_TITLE}", ""]
    if parsed.get("profile"):
        lines += [f"Profile: {parsed['profile']}", ""]
    for paragraph in parsed["paragraphs"]:
        speaker = f" · {paragraph['speaker']}" if paragraph.get("speaker") else ""
        lines += [f"## {render_span(paragraph['first'], paragraph['last'])}{speaker}", paragraph["text"], ""]

    return "\n".join(lines).rstrip() + "\n"


def list_paragraph_spans(paragraphs: list[dict[str, Any]],
                         lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each paragraph as a span of speech no frame read, `{"start", "end", "why", "speaker", "text", "lines"}`: from its
    first line's start to the next paragraph's, the last one running to the recording's end (`end` None). A paragraph
    naming a line the transcript does not hold is left out, which the validator refused before."""

    start_of = {line["id"]: float(line["start"]) for line in lines}
    known = [paragraph for paragraph in paragraphs if paragraph["first"] in start_of and paragraph["last"] in start_of]
    starts = [start_of[paragraph["first"]] for paragraph in known]

    return [{"start": start, "end": following, "why": "a paragraph of the speech file", "speaker": paragraph["speaker"],
             "text": paragraph["text"], "lines": [paragraph["first"], paragraph["last"]]}
            for paragraph, start, following in zip(known, starts, [*starts[1:], None])]


def build_settled_paragraphs(parsed: dict[str, Any],
                      lines: list[dict[str, Any]]) -> dict[str, Any]:
    """An answer's paragraphs as the program writes them: each ending where the next begins when its text fits, then
    each inside one speaker turn named by that turn's speaker."""

    return name_turn_speakers(close_paragraph_spans(parsed, lines), lines)
