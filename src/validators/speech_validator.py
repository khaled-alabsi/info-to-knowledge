"""Phase 2's paragraphs of an audio recording, judged from `02_speech.md` on disk.

Every transcript line belongs to exactly one paragraph, in order; a speaker is written `Speaker <letter>`, and is the
speaker phase 1 placed on the paragraph's lines when it placed one; a paragraph
keeps the speech it cleans, so one far shorter than its lines is a summary and one far longer holds what nobody said;
and a profile asked for is one of the list.
"""

from __future__ import annotations

from typing import Any

from models import ingesting_profile_vocabulary
from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from services import call_log_service
from services import phase_file_service
from services import speech_file_service
from utils import speech_markdown_utils
from validators import chain_response_validator

__all__ = ["validate_speech", "validate_speech_record"]

# A paragraph's words against its lines' words: cleaning drops filler and repeats, never half of what was said, and
# adds nothing of its own. Not measured yet: set from the instruction's own rule, to be tuned on the first runs
_MIN_WORD_SHARE = 0.5
_MAX_WORD_SHARE = 1.5
# Lines this short are mostly filler ("yeah, yeah, okay"), so a share of their words says nothing
_MIN_WORDS_FOR_SHARE = 8
_SHAPE = "## S<first>-S<last> · Speaker <letter>"


def _count_words(text: str) -> int:
    return len(text.split())


def _check_coverage(report: ValidationReport,
                    paragraphs: list[dict[str, Any]],
                    order: dict[str, int],
                    ids: list[str]) -> None:
    """Every line in exactly one paragraph, the paragraphs in the lines' order."""

    report.mark_ran("SPEECH_LINE_UNKNOWN")
    report.mark_ran("SPEECH_LINE_MISSING")
    report.mark_ran("SPEECH_LINE_TWICE")
    report.mark_ran("SPEECH_LINE_BACKWARDS")
    unknown = [line for paragraph in paragraphs for line in (paragraph["first"], paragraph["last"]) if line not in order]
    if unknown:
        report.add("SPEECH_LINE_UNKNOWN", f"no line {', '.join(dict.fromkeys(unknown))} in the transcript: it runs from "
                   f"{ids[0]} to {ids[-1]}", fix="name only the lines you were given")

        return
    expected = 0
    for paragraph in paragraphs:
        first, last = order[paragraph["first"]], order[paragraph["last"]]
        if last < first:
            report.add("SPEECH_LINE_BACKWARDS", f"{paragraph['first']}-{paragraph['last']} runs backwards",
                       fix="write a paragraph's lines from its first to its last")
            continue
        if first > expected:
            report.add("SPEECH_LINE_MISSING", f"{_describe_missing(ids[expected:first])} to no paragraph",
                       fix=f"put {ids[expected]} into the paragraph it belongs to, or one of its own")
        elif first < expected:
            # A paragraph repeating lines after the one that ends at the last line has no line left to start at
            fix = (f"start the paragraph at {ids[expected]}, the line after the paragraph before it" if expected < len(ids)
                   else f"leave the paragraph out: the paragraph before it already ends at the last line, {ids[-1]}")
            report.add("SPEECH_LINE_TWICE", f"{', '.join(ids[first:expected])} are in two paragraphs", fix=fix)
        expected = max(expected, last + 1)
    if expected < len(ids):
        report.add("SPEECH_LINE_MISSING", f"{_describe_missing(ids[expected:])} to no paragraph",
                   fix=f"go on to the last line, {ids[-1]}")


def _describe_missing(missing: list[str]) -> str:
    """The lines no paragraph holds, with their verb: "S0004 belongs", "S0004 to S0009 belong"."""

    if len(missing) == 1:
        return f"{missing[0]} belongs"

    return f"{missing[0]} to {missing[-1]} belong"


def _render_turn(turn: dict[str, Any]) -> str:
    return f"turn {speech_markdown_utils.render_span(turn['first'], turn['last'])} · {turn['speaker']}"


def _check_turn(report: ValidationReport,
                span: str,
                speaker: str,
                first_turn: dict[str, Any] | None,
                last_turn: dict[str, Any] | None) -> None:
    """A paragraph of a transcript in speaker turns lies inside one turn and names its speaker; with no turns, nothing."""

    if first_turn is None or last_turn is None:
        return
    if first_turn is not last_turn:
        report.add("SPEECH_SPEAKER_NOT_HEARD", f"{span} runs across {_render_turn(first_turn)} and "
                   f"{_render_turn(last_turn)}", fix=f"end the paragraph at {first_turn['last']}, the last line of its "
                   f"turn, and start the next turn's lines in a paragraph of their own")
    elif speaker != first_turn["speaker"]:
        report.add("SPEECH_SPEAKER_NOT_HEARD", f"{span} is written as {speaker}, inside {_render_turn(first_turn)}",
                   fix=f"name the turn's speaker, {first_turn['speaker']}")


def _is_kept(written: int,
             spoken: int) -> bool:
    return spoken * _MIN_WORD_SHARE <= written <= spoken * _MAX_WORD_SHARE


def _is_kept_with_neighbour(paragraphs: list[dict[str, Any]],
                            counts: list[tuple[int, int] | None],
                            index: int) -> bool:
    """Whether the paragraph and its neighbour of the same speaker together keep their lines' words: a sentence the
    transcript cut across lines is joined in one of the two paragraphs, which moves its words (a talk's S0357 ended
    "aber auch wir als", and its words were written in S0358's paragraph, 24 for 12 spoken)."""

    written, spoken = counts[index]
    for other in (index - 1, index + 1):
        if 0 <= other < len(paragraphs) and counts[other] is not None \
                and paragraphs[other]["speaker"] == paragraphs[index]["speaker"] \
                and _is_kept(written + counts[other][0], spoken + counts[other][1]):
            return True

    return False


def _check_word_counts(report: ValidationReport,
                       paragraphs: list[dict[str, Any]],
                       counts: list[tuple[int, int] | None]) -> None:
    """That each paragraph keeps the speech of its lines, alone or with its neighbour of the same speaker."""

    for index, (paragraph, count) in enumerate(zip(paragraphs, counts)):
        if count is None or count[1] < _MIN_WORDS_FOR_SHARE or _is_kept(*count) \
                or _is_kept_with_neighbour(paragraphs, counts, index):
            continue
        written, spoken = count
        span = speech_markdown_utils.render_span(paragraph['first'], paragraph['last'])
        if written < spoken * _MIN_WORD_SHARE:
            report.add("SPEECH_PARAGRAPH_SHORTENED", f"{span}: {written} words for {spoken} spoken, a summary of them",
                       fix="keep every statement of the lines; drop only filler, false starts and repeated words")
        else:
            report.add("SPEECH_PARAGRAPH_ADDED", f"{span}: {written} words for {spoken} spoken",
                       fix="write only what the lines say, nothing of your own")


def _check_paragraphs(report: ValidationReport,
                      paragraphs: list[dict[str, Any]],
                      lines: list[dict[str, Any]],
                      order: dict[str, int]) -> None:
    """Each paragraph's speaker and turn, and that it keeps the speech of its lines."""

    turn_of = {line["id"]: turn for turn in speech_markdown_utils.list_speaker_turns(lines) for line in turn["lines"]}

    report.mark_ran("SPEECH_SPEAKER_SHAPE")
    report.mark_ran("SPEECH_PARAGRAPH_EMPTY")
    report.mark_ran("SPEECH_PARAGRAPH_SHORTENED")
    report.mark_ran("SPEECH_PARAGRAPH_ADDED")
    report.mark_ran("SPEECH_SPEAKER_NOT_HEARD")
    counts: list[tuple[int, int] | None] = []
    for paragraph in paragraphs:
        span = speech_markdown_utils.render_span(paragraph['first'], paragraph['last'])
        if not speech_markdown_utils.SPEAKER_PATTERN.fullmatch(paragraph["speaker"]):
            report.add("SPEECH_SPEAKER_SHAPE", f"{span}: the speaker is written {paragraph['speaker']!r}",
                       fix="write the speaker as `Speaker <capital letter>`, such as `Speaker A`")
        if not paragraph["text"]:
            report.add("SPEECH_PARAGRAPH_EMPTY", f"{span} has no text under its heading",
                       fix="write the paragraph's clean speech under its heading")
            counts.append(None)
            continue
        if paragraph["first"] not in order or paragraph["last"] not in order:
            counts.append(None)
            continue
        covered = lines[order[paragraph["first"]]:order[paragraph["last"]] + 1]
        _check_turn(report, span, paragraph["speaker"], turn_of.get(paragraph["first"]), turn_of.get(paragraph["last"]))
        counts.append((_count_words(paragraph["text"]), sum(_count_words(line["text"]) for line in covered)))
    _check_word_counts(report, paragraphs, counts)


def validate_speech_record(paths: RunPaths,
                           is_profile_asked: bool) -> ValidationReport:
    """`02_speech.md` as the program wrote it from the answer, against `01_transcript.json`."""

    report = ValidationReport(subject=output_contract.SPEECH_FILE)
    path = speech_file_service.build_speech_path(paths)
    report.mark_ran("SPEECH_MISSING")
    if not path.is_file():
        report.add("SPEECH_MISSING", f"{path.name} was not written", fix=f'answer with "# Speech" and "{_SHAPE}" paragraphs')

        return report
    parsed, why = speech_markdown_utils.parse_speech(path.read_text(encoding="utf-8"))
    report.mark_ran("SPEECH_UNREADABLE")
    if parsed is None:
        report.add("SPEECH_UNREADABLE", f"{path.name} could not be read: {why}", fix=f'start every paragraph with "{_SHAPE}"')

        return report

    # 1. the lines, numbered as the merge numbers them
    lines = speech_markdown_utils.list_numbered_lines(
        phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE]))
    ids = [line["id"] for line in lines]
    order = {line_id: position for position, line_id in enumerate(ids)}

    # 2. every line once, in order; each paragraph's speaker and its speech
    _check_coverage(report, parsed["paragraphs"], order, ids)
    _check_paragraphs(report, parsed["paragraphs"], lines, order)

    # 3. the profile, when one was asked for
    report.mark_ran("SPEECH_PROFILE_UNKNOWN")
    if is_profile_asked and parsed.get("profile") not in ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES:
        report.add("SPEECH_PROFILE_UNKNOWN", f"the profile is {parsed['profile']!r}" if parsed.get("profile")
                   else "there is no Profile: line",
                   fix="write one line `Profile: <name>` before the first paragraph, the name one of: "
                       + ", ".join(ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES))

    return report


def validate_speech(paths: RunPaths,
                    call_slug: str,
                    is_profile_asked: bool) -> ValidationReport:
    """Phase 2's call for an audio recording, then the paragraphs it left."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report

    return validate_speech_record(paths, is_profile_asked)
