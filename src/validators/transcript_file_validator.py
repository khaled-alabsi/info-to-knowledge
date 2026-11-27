"""Validate the file phase 1 writes. A pure function over bytes on disk.

Two later steps read this file: phase 2 cuts each frame's speech out of its segments, and the merge
(`services/integration_service.py`) turns its segments into speech entries. Both index into `segments` by
`start` and `end`, so that is the shape judged here, separately from the code that reads it. A transcript with no
segments is valid: it is what a video with no audio, or audio with no speech, produces. A `speaker`, on a segment or a
word, is `Speaker <letter>` or null (no speaker for overlapping speech); a file with no speakers, written before
diarization or with it off, is valid.
"""

from __future__ import annotations

import json

from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from services import phase_file_service
from utils import speech_markdown_utils

__all__ = ["validate_transcript_file"]

_TRANSCRIBE_PHASE = output_contract.PHASE_NUMBERS["transcribe"]


def _is_timed_segment(segment: object) -> bool:
    if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
        return False
    start, end = segment.get("start"), segment.get("end")

    # `True` is an int to Python and no time
    is_number = all(isinstance(time, (int, float)) and not isinstance(time, bool) for time in (start, end))

    return is_number and 0 <= start <= end


def _is_named_speaker(entry: object) -> bool:
    speaker = entry.get("speaker") if isinstance(entry, dict) else None

    return speaker is None or (isinstance(speaker, str) and bool(speech_markdown_utils.SPEAKER_PATTERN.fullmatch(speaker)))


def validate_transcript_file(paths: RunPaths) -> ValidationReport:
    report = ValidationReport(subject=output_contract.TRANSCRIPT_FILE)
    transcript_file = phase_file_service.build_phase_file_path(paths, _TRANSCRIBE_PHASE)

    # --- existence: always first, and always an early return ---------------------------------
    report.mark_ran("T_MISSING")
    if not transcript_file.is_file() or not transcript_file.stat().st_size:
        report.add("T_MISSING", f"{transcript_file.name} was not written, or is empty",
                   fix=f"run phase {_TRANSCRIBE_PHASE} first, with `--phase {_TRANSCRIBE_PHASE}`")

        return report

    report.mark_ran("T_UNPARSABLE")
    try:
        transcript = json.loads(transcript_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.add("T_UNPARSABLE", f"{transcript_file.name} is not valid JSON: {exc}",
                   fix=f"run phase {_TRANSCRIBE_PHASE} again, or repair the file by hand")

        return report

    report.mark_ran("T_SEGMENTS")
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        report.add("T_SEGMENTS", f"{transcript_file.name} has no `segments` list",
                   fix="the top level is an object with `hasAudio` and `segments`")

        return report

    report.mark_ran("T_SEGMENT_TIMES")
    broken = [index for index, segment in enumerate(transcript["segments"]) if not _is_timed_segment(segment)]
    if broken:
        report.add("T_SEGMENT_TIMES", f"{len(broken)} segment(s), first {broken[:5]}, lack a `text`, or a numeric "
                   f"`start` and `end` with 0 <= start <= end",
                   fix="every segment carries `text`, `start` and `end`, in seconds, with start <= end")

    report.mark_ran("T_SPEAKER_SHAPE")
    misnamed = [index for index, segment in enumerate(transcript["segments"])
                if isinstance(segment, dict) and not all(_is_named_speaker(entry)
                                                         for entry in [segment, *(segment.get("words") or [])])]
    if misnamed:
        report.add("T_SPEAKER_SHAPE", f"{len(misnamed)} segment(s), first {misnamed[:5]}, name a speaker not written "
                   f"`Speaker <capital letter>`", fix="a speaker is `Speaker A`, `Speaker B` …, or null")

    return report
