"""Layer 2, REQUIREMENTS.md R3: the transcript and the accepted frame records merged into one list of entries, built
in memory from the files phases 1 to 3 wrote (`01_transcript.json`, `02_frames.json` with each frame's notes, a speech
file's paragraphs, `02_speech.md`, of an audio recording or a PDF read as text only, its prepared notes and the batch
summaries), whenever a later phase needs it.

The merge is plain code, takes a few hundredths of a second on an 87-minute meeting, and gives the same entries with
the same ids every time, so a file of it would be a copy of phases 1 and 3 that could drift from them and cost nothing
to rebuild. A missing input is refused by name, as a phase file is.
"""

from __future__ import annotations

from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from services import batch_summaries_service
from services import frame_records_service
from services import phase_file_service
from services import prepared_file_service
from services import preprocessing_step_service
from services import speech_file_service
from utils import frame_merge_utils
from utils import instruction_example_utils
from utils import speech_markdown_utils

__all__ = ["build_integration", "list_transcript_lines"]

# What a note says when its field had nothing to hold
_EMPTY_NOTES = {"(none)", "none", "none."}


def _is_meeting_entry(entry: dict) -> bool:
    """Whether an entry belongs to the meeting: speech always; a note unless it copies the instructions' invented
    sample or only says "(none)"."""

    text = str(entry.get("text") or "")

    return entry.get("kind") == "speech" or not (instruction_example_utils.is_example_line(text)
                                                 or text.strip().casefold() in _EMPTY_NOTES)


def _read_entry_start(entry: dict) -> float:
    """Where an entry stands on the recording's timeline: a speech or prepared entry's `start`, a frame note's first
    `timestamps`. A text-only PDF's figure notes stand among its paragraphs, and the sort read a `start` they lack
    (pdf-figures-05-manual)."""

    if "start" in entry:
        return float(entry["start"])

    return float((entry.get("timestamps") or [0.0])[0])


def _read_entry_order(entry: dict) -> tuple[float, bool]:
    """Where an entry sorts: by its time; a prepared item stands before the paragraph it starts at, so the notes print
    it first."""

    return _read_entry_start(entry), entry["kind"] != prepared_file_service.ENTRY_KIND


def build_integration(paths: RunPaths,
                      blocked_reasons: dict[str, str] | None = None) -> dict[str, Any]:
    """`{"entries", "frames", "contradictions", "gaps", "unreadSpeech", "counts", "batchSummaries", "prepared",
    "preparedParagraphs", "isPdf"}` of this run's folder. `blocked_reasons` is slug -> why this run blocked a frame; a frame
    with no accepted record and no reason is a gap "no accepted record".
    Raises `MissingPhaseInput` for a phase file that is not there."""

    # STEP 1: the transcript and the frames' accepted notes merged into one numbered list of entries
    transcript = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"])
    frames_file = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    integration = frame_merge_utils.merge_frame_records(transcript, frames_file,
                                                        frame_records_service.read_frame_records(paths),
                                                        blocked_reasons or {})

    # STEP 2: the notes that are no part of the meeting dropped: one copying a line of the instructions' invented
    # sample, or one that only says "(none)". Both are dropped after the numbering, so no other note's id moves and the
    # sections that cite them stay valid (an invented question and a "(none)" fact once reached the writer)
    integration["entries"] = [entry for entry in integration["entries"] if _is_meeting_entry(entry)]
    # A frame's own open questions, when its batch asked its open questions with every frame in view: written one frame
    # at a time, they asked what the next frame shows ("the content behind the QR codes is not detailed yet") and filled
    # a 15-minute talk's open points with nine such lines; the batch's list is the one a reader needs
    asked_frames = {int(number) for batch in batch_summaries_service.read_batch_summaries(paths)
                    if batch.get("source") == "batch" and (batch.get("openQuestions") or batch.get("answeredQuestions"))
                    for number in batch.get("frames") or []}
    integration["entries"] = [entry for entry in integration["entries"] if not _is_asked_by_batch(entry, asked_frames)]
    # A PDF's notes are headed by their pages, never by the made-up minute each page sits at
    integration["isPdf"] = phase_file_service.is_pdf_run(paths)

    # STEP 3: the speech file's paragraphs (an audio recording's, or a PDF read as text only) replace the merge's spans
    # of unread speech: each one span, with its speaker and its clean text, in place of the one span no frame read
    speech = speech_file_service.read_speech(paths)
    integration["prepared"], integration["preparedParagraphs"] = [], []
    if speech is not None:
        lines = speech_markdown_utils.list_numbered_lines(transcript)
        integration["unreadSpeech"] = speech_markdown_utils.list_paragraph_spans(speech["paragraphs"], lines)

        # STEP 4: phase 3's prepared notes, when the run's profile declares steps: each section one entry, which the
        # writer cites in place of the paragraphs it covers
        prepared = prepared_file_service.list_prepared_items(
            paths, preprocessing_step_service.list_due_steps(paths), speech["paragraphs"],
            {line["id"]: float(line["start"]) for line in lines})
        entries = [entry for item in prepared for entry in item["entries"]]
        integration["prepared"] = prepared
        integration["preparedParagraphs"] = sorted({paragraph for entry in entries for paragraph in entry["paragraphs"]})
        integration["entries"] = sorted([*entries, *integration["entries"]], key=_read_entry_order)

    # STEP 5: what each batch of frames says of itself: its story, its mood, the questions it left open and the answers
    # found for them. The notes file prints it where the batch ends. A unit of one frame has only its own description,
    # which the frame's section already holds.
    integration["batchSummaries"] = [batch for batch in batch_summaries_service.read_batch_summaries(paths)
                                     if batch.get("source") == "batch"]

    return integration


def _is_asked_by_batch(entry: dict,
                       asked_frames: set[int]) -> bool:
    """Whether `entry` is a frame's open question whose every frame belongs to a batch that asked its own."""

    frames = [int(number) for number in entry.get("frames") or []]

    return entry.get("kind") == "open_question" and bool(frames) and all(number in asked_frames for number in frames)


def list_transcript_lines(integration: dict) -> list[dict]:
    """The merge's speech entries, one per transcript line, in the order they were said: what phases 4 and 5 read the
    transcript as."""

    return sorted((entry for entry in integration.get("entries", []) if entry["kind"] == "speech"),
                  key=lambda entry: float(entry["start"]))
