"""Phase 1: the recording's speech, as text with times, written to `01_transcript.json`: a video's or an audio
file's sound transcribed, or a PDF's text read page by page, what its cleanup removed written to `01_cleanup.json`.

Plain code, no model call and no retry: a transcription that failed would fail the same way again,
so a failure is reported as a failed phase and the run stops there. Every later phase that needs the
transcript reads this file from disk, and refuses for itself when it is missing. So when this phase
was not asked for it does nothing at all, and `--phase 3` never touches the audio.

A video with no audio stream, or with no speech in it, still gets a transcript file. It says
`hasAudio: false` or carries an empty segment list, and every frame is then analysed with nothing
said around it.

With `audio.diarization.isOn`, pyannote is run once over the whole meeting's audio, every part at its offset, and
each word and segment gets its speaker (`Speaker A` …), a segment split where its speaker changes; the file records
how it went under `diarization`. Diarization that is not installed or that fails leaves the transcript as Whisper wrote
it, with one console line saying why: the speakers are an addition, never a reason to lose the transcript.

When the meeting tool's own transcript lies beside the recording, the file also records it: its size, how far it
agrees with phase 1, and the speakers it names. It never replaces phase 1's text: measured in
`research/transcript-sources/`, it is worse on names and terms (REQUIREMENTS.md R14).
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph import routing
from graph.graph_state import RunState
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models.diarization_errors import DiarizationFailed, DiarizationUnavailable
from models.input_range_spec import InputRangeSpec
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from services import audio_extraction_service
from services import diarization_service
from services import frames_preparing_progress_service as progress_service
from services import inbox_service
from services import ingesting_profile_service
from services import input_range_service
from services import office_source_service
from services import pdf_source_service
from services import performance_log_service
from services import phase_file_service
from services import provided_transcript_service
from services import recording_medium_service
from services import recording_parts_service
from services import slides_source_service
from services import transcription_service
from services import unlimited_ocr_reader_service
from services import validation_service
from utils import json_store_utils
from utils import speaker_assignment_utils

PHASE = "transcribe"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]
# A PDF step's console line comes every this many pages: about one line a minute or two at a few seconds a page
_PAGES_A_LINE = 100

# Which validator judges the file this phase writes. NOT the phase: see `services/validation_service.py`.
TRANSCRIBE_KIND = "transcribe"


def _build_empty_transcript() -> dict:
    """The transcript of a recording with no speech, fresh for each caller to add to."""

    return {"language": None, "languageProbability": None, "engine": "", "model": "", "text": "", "segments": []}


def _report_pages(run_dir: Path,
                  log: logging.Logger,
                  read: int,
                  total: int,
                  step: str) -> None:
    """Phase 1's stages count a PDF's pages step by step (`progress_service.PDF_STAGES`), and the console says how far a
    step is every `_PAGES_A_LINE` pages and at its last: a 6,761-page PDF stood at 6,761 of 6,761 for hours while its
    reader went over the pages a second time."""

    stage = progress_service.report_step(run_dir=run_dir, name=step, done=read, total=total,
                                         file_name=progress_service.TRANSCRIBE_PROGRESS_FILE)
    if read and (read % _PAGES_A_LINE == 0 or read == total):
        pace = (progress_service.CLOCK() - stage["startedAtS"]) / read
        log.info(f"  {step}: page {read:,} of {total:,}, {pace:.2f} s a page")


def _read_pdf_transcript(ctx: RunContext,
                         recording: Path) -> dict:
    """A PDF's speech: its pages' cleaned text, each page at its minute; what the cleanup removed is written beside it
    (`01_cleanup.json`)."""

    ocr_engine = pdf_source_service.read_chosen_ocr_engine(ctx.paths.output_root)
    # Not installed, scans are read by Tesseract and the console says so: the run is not stopped for a better reader
    if ocr_engine == pdf_source_service.UNLIMITED_OCR_ENGINE and not unlimited_ocr_reader_service.is_installed():
        ctx.log.warning("  Unlimited-OCR-2 is not installed (tools/unlimited-ocr/setup.sh --download, or the setup "
                        "wizard's Download): scanned pages are read by Tesseract")
        ocr_engine = pdf_source_service.TESSERACT_ENGINE
    is_read_as_pages = (recording_medium_service.read_chosen_medium(ctx.paths.output_root)
                        not in output_contract.PDF_TEXT_ONLY_MEDIUMS)
    page_count = pdf_source_service.count_pages(recording)
    input_range = input_range_service.read_range(folder=ctx.paths.output_root)
    page_indexes = input_range_service.list_page_indexes(input_range=input_range, page_count=page_count)
    if input_range is not None:
        ctx.log.info(f"  {input_range_service.describe_range(input_range=input_range)} of {page_count}, as the run "
                     f"asked")
    page_counter = functools.partial(_report_pages, ctx.run_dir, ctx.log)
    transcript, removed = pdf_source_service.build_page_transcript(pdf=recording,
                                                                   is_read_as_pages=is_read_as_pages,
                                                                   ocr_engine=ocr_engine,
                                                                   on_pages=page_counter,
                                                                   page_indexes=page_indexes)
    json_store_utils.write_json(ctx.paths.output_root / output_contract.PDF_CLEANUP_FILE, {"removed": removed})
    ctx.log.info(f"  {len(page_indexes)} page(s) read, {len(removed)} line(s) of no knowledge removed "
                 f"({output_contract.PDF_CLEANUP_FILE})")
    if transcript.get("ocrPages"):
        ctx.log.info(f"  scanned page(s) {', '.join(map(str, transcript['ocrPages']))} read by Unlimited-OCR-2")

    return transcript


def _read_office_transcript(ctx: RunContext,
                            recording: Path) -> dict:
    """A Word or OpenDocument file's text, turned into markdown by pandoc and cut into blocks, its pictures beside it;
    what was removed (its table of contents) written beside it (`01_cleanup.json`), as for a PDF."""

    transcript, removed = office_source_service.build_document_transcript(recording, ctx.paths.output_root, ctx.log)
    json_store_utils.write_json(ctx.paths.output_root / output_contract.PDF_CLEANUP_FILE, {"removed": removed})
    ctx.log.info(f"  {len(removed)} block(s) of the document's own table of contents removed "
                 f"({output_contract.PDF_CLEANUP_FILE})")

    return transcript


def _read_slides_transcript(ctx: RunContext,
                            recording: Path) -> dict:
    """A deck drawn into `01_slides.pdf` by LibreOffice, its speaker notes as what is said over each slide; its hidden
    slides written beside it (`01_cleanup.json`)."""

    transcript, removed = slides_source_service.build_slide_transcript(recording, ctx.paths.output_root, ctx.log)
    json_store_utils.write_json(ctx.paths.output_root / output_contract.PDF_CLEANUP_FILE, {"removed": removed})

    return transcript


def _build_parts_transcript(ctx: RunContext,
                            recording: Path) -> tuple[dict, list[tuple[Path, float]]]:
    """A recording in parts transcribed part by part, each part's times moved on by the parts before it, and joined
    into one transcript of the meeting, with each part's WAV and its offset. Whisper finds
    each part's language alone: the meeting's language, engine and model are those of the part with the most speech,
    so a short last part of small talk never decides them."""

    durations = recording_parts_service.list_part_durations(recording)
    parts = recording_parts_service.list_recording_parts(recording)
    segments: list[dict] = []
    longest, longest_speech_s = _build_empty_transcript(), -1.0
    audio_parts, offset_s = [], 0.0
    for number, (part, duration_s) in enumerate(zip(parts, durations), start=1):
        ctx.log.info(f"  part {number} of {len(parts)}: {part.name}")
        audio_name = f"{Path(output_contract.AUDIO_FILE).stem}-part-{number:02d}.wav"
        audio_path = audio_extraction_service.extract_audio(part, ctx.paths.output_root / audio_name)
        if audio_path is not None:
            shifted = recording_parts_service.move_transcript_times(
                transcription_service.produce_transcript(audio_path, app_config.settings.audio), offset_s)
            speech_s = sum(float(segment["end"]) - float(segment["start"]) for segment in shifted["segments"])
            segments += shifted["segments"]
            if speech_s > longest_speech_s:
                longest, longest_speech_s = shifted, speech_s
            audio_parts.append((audio_path, offset_s))
        offset_s += duration_s

    return ({**longest, "segments": segments, "text": " ".join(segment["text"].strip() for segment in segments)},
            audio_parts)


def _build_window_transcript(ctx: RunContext,
                             recording: Path,
                             input_range: InputRangeSpec) -> tuple[dict, list[tuple[Path, float]]]:
    """The transcript of the run's time window alone, one file or a recording in parts: each part
    the window crosses has that stretch of its audio transcribed, its times moved to where the stretch stands on the
    meeting clock, so a segment said at 1:02:10 is kept at 1:02:10. The language, engine and model are those of the part
    with the most speech, as for the whole recording."""

    parts = recording_parts_service.list_recording_parts(recording)
    windows = input_range_service.list_part_windows(durations=recording_parts_service.list_part_durations(recording),
                                                    input_range=input_range)
    ctx.log.info(f"  {input_range_service.describe_range(input_range=input_range)}, as the run asked")
    segments: list[dict] = []
    longest, longest_speech_s = _build_empty_transcript(), -1.0
    audio_parts = []
    for number, window in enumerate(windows, start=1):
        audio_name = f"{Path(output_contract.AUDIO_FILE).stem}-window-{number:02d}.wav"
        audio_path = audio_extraction_service.extract_audio(parts[window.part_index],
                                                            ctx.paths.output_root / audio_name,
                                                            start_s=window.start_s, end_s=window.end_s)
        if audio_path is None:
            continue
        shifted = recording_parts_service.move_transcript_times(
            transcription_service.produce_transcript(audio_path, app_config.settings.audio), window.offset_s)
        speech_s = sum(float(segment["end"]) - float(segment["start"]) for segment in shifted["segments"])
        segments += shifted["segments"]
        if speech_s > longest_speech_s:
            longest, longest_speech_s = shifted, speech_s
        audio_parts.append((audio_path, window.offset_s))

    return ({**longest, "segments": segments, "text": " ".join(segment["text"].strip() for segment in segments)},
            audio_parts)


def _build_recording_transcript(ctx: RunContext) -> tuple[dict, list[tuple[Path, float]]]:
    """The recording's transcript and its audio, each WAV with its offset on the meeting clock (empty when it has no
    audio): a PDF's pages, the run's time window, one file, or a recording in parts."""

    recording = ctx.repository / ctx.module
    if pdf_source_service.is_pdf(recording):
        return _read_pdf_transcript(ctx, recording), []
    if office_source_service.is_office(recording):
        return _read_office_transcript(ctx, recording), []
    if slides_source_service.is_slides(recording):
        return _read_slides_transcript(ctx, recording), []
    input_range = input_range_service.read_range(folder=ctx.paths.output_root)
    if input_range is not None and not input_range.is_pages:
        return _build_window_transcript(ctx, recording, input_range)
    if not recording_parts_service.is_multi_part(recording):
        audio_path = audio_extraction_service.extract_audio(recording,
                                                            ctx.paths.output_root / output_contract.AUDIO_FILE)
        if audio_path is None:
            return _build_empty_transcript(), []

        return transcription_service.produce_transcript(audio_path, app_config.settings.audio), [(audio_path, 0.0)]

    return _build_parts_transcript(ctx, recording)


def _find_why_no_diarization(ctx: RunContext) -> str:
    """Why phase 1 tells no speakers apart, empty when it does: diarization is off, the recording is a PDF, which has no
    voice, or a takeaway run, the fast profile whose facts need what was agreed and not who said it, has it off."""

    if not app_config.settings.audio.diarization.is_on:
        return "audio.diarization.isOn is off"
    if inbox_service.is_document(ctx.repository / ctx.module):
        return "a document has no voice to tell apart"
    if (ingesting_profile_service.is_one_call_profile(ctx.paths.output_root)
            and not app_config.settings.audio.takeaway.is_diarization_on):
        return "a takeaway run (audio.takeaway.isDiarizationOn is off)"

    return ""


def _is_diarization_due(ctx: RunContext) -> bool:
    return not _find_why_no_diarization(ctx)


def _move_turns(diarization: dict,
                offset_s: float) -> dict:
    """Diarization's turns, each `{"start", "end", "label"}`, moved on by `offset_s` seconds."""

    if not offset_s:
        return diarization
    moved = {key: [{**turn, "start": turn["start"] + offset_s, "end": turn["end"] + offset_s} for turn in diarization[key]]
             for key in ("turns", "exclusiveTurns")}

    return {**diarization, **moved}


def _build_transcript_with_speakers(ctx: RunContext,
                                    transcript: dict,
                                    audio_parts: list[tuple[Path, float]]) -> dict:
    """The transcript with a speaker on every segment and word, and a `diarization` record saying how it went: `off`,
    `unavailable` or `failed` keep Whisper's segments as they were."""

    settings = app_config.settings.audio.diarization
    why_not = _find_why_no_diarization(ctx)
    if why_not:
        # Diarization switched off is said by the run's header; any other reason is said here
        if settings.is_on:
            ctx.log.info(f"phase {PHASE_NUMBER}: no speakers told apart: {why_not}")

        return {**transcript, "diarization": {"status": "off"}}
    if not audio_parts or not transcript["segments"]:
        return {**transcript, "diarization": {"status": "done", "model": settings.model, "speakers": []}}
    # A run's window starts later than the meeting: its audio is diarized from the window's start, never after an hour
    # of silence put before it, and its turns moved back onto the meeting clock
    start_s = min(offset_s for _, offset_s in audio_parts)
    try:
        diarization = diarization_service.compute_speaker_turns(
            [(path, offset_s - start_s) for path, offset_s in audio_parts], settings)
        diarization = _move_turns(diarization, start_s)
    # either leaves the transcript Whisper wrote: the speakers are an addition, never a reason to lose the transcript
    except (DiarizationUnavailable, DiarizationFailed) as exc:
        ctx.log.warning(f"phase {PHASE_NUMBER}: no speakers: {exc}")
        status = "unavailable" if isinstance(exc, DiarizationUnavailable) else "failed"

        return {**transcript, "diarization": {"status": status, "model": settings.model, "reason": str(exc)}}
    segments = speaker_assignment_utils.add_speakers(transcript["segments"], diarization, settings.overlap_share)
    turns = speaker_assignment_utils.name_turns(transcript["segments"], diarization, settings.overlap_share)
    speakers = speaker_assignment_utils.list_speakers(segments)
    unplaced = sum(1 for segment in segments if segment["speaker"] is None)
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(speakers)} speaker(s) on {diarization['device']}, "
                 f"{unplaced} of {len(segments)} segment(s) with no speaker")

    return {**transcript, "segments": segments,
            # pyannote's own turns are kept, labelled as the segments are: why a segment has no speaker (speech two
            # people said at once, or speech no turn covers) is read from them without running the model again
            "diarization": {"status": "done", "model": settings.model, "device": diarization["device"],
                            "speakers": speakers, **turns}}


def _mark_phase(ctx: RunContext,
                status: str,
                handles: TimingHandles) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def produce_transcript_node(state: RunState,
                            config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    is_asked_for = routing.is_phase_selected(state, PHASE_NUMBER)

    # STEP 1: a phase that was not asked for, already ran in this run, or is only being rehearsed
    # does nothing. Whoever needs the transcript reads the file, and refuses when it is not there.
    if not is_asked_for or ctx.store.get_phase(PHASE) == "complete" or state.get("is_dry_run"):
        return {"phases": {PHASE: "complete"}}

    ctx.log.info("")
    ctx.log.info(f"phase {PHASE_NUMBER}: transcribing {ctx.module}")
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    # the phase's stages, each with its time, for the page's list of stages and the console: a PDF's steps page by
    # page, or a recording's transcription
    is_pdf = pdf_source_service.is_pdf(ctx.repository / ctx.module)
    reading_stages = list(progress_service.PDF_STAGES) if is_pdf else [progress_service.TRANSCRIPTION]
    stages = list(reading_stages)
    has_speaker_stage = _is_diarization_due(ctx)
    if has_speaker_stage:
        stages.append(progress_service.SPEAKERS)
    progress_service.open_progress(ctx.run_dir, stages, progress_service.TRANSCRIBE_PROGRESS_FILE)
    try:
        # STEP 2: the speech, with a time on every segment and every word, from the recording's audio; a recording in
        # parts gives one transcript on the meeting's time
        progress_service.start_stage(ctx.run_dir, reading_stages[0], None, progress_service.TRANSCRIBE_PROGRESS_FILE)
        transcript, audio_parts = _build_recording_transcript(ctx)
        has_audio = bool(audio_parts)
    except Exception as exc:  # noqa: BLE001 (reported as a failed phase, never a bare traceback)
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {type(exc).__name__}: {exc}")
        # The traceback goes to the run's log at debug level, so the console keeps one line and the cause is kept
        ctx.log.debug("phase %s's traceback", PHASE_NUMBER, exc_info=True)
        progress_service.mark_running_stage_failed(ctx.run_dir, progress_service.TRANSCRIBE_PROGRESS_FILE)

        return _mark_phase(ctx, "failed", handles)
    progress_service.finish_transcribe_stage(ctx.run_dir, reading_stages[-1], ctx.log,
                                             f"{len(transcript['segments'])} segment(s)")

    # STEP 3: who spoke when, when diarization is on; the transcript stays as Whisper wrote it when it cannot run. A
    # pause asked for meanwhile is waited out first: the transcription cannot be paused inside, this stage can wait
    if ctx.control_check is not None:
        ctx.control_check()
    if has_speaker_stage:
        progress_service.start_stage(ctx.run_dir, progress_service.SPEAKERS, None,
                                     progress_service.TRANSCRIBE_PROGRESS_FILE)
    transcript = _build_transcript_with_speakers(ctx, transcript, audio_parts)
    if has_speaker_stage:
        progress_service.finish_transcribe_stage(ctx.run_dir, progress_service.SPEAKERS, ctx.log,
                                                 f"diarization {transcript['diarization']['status']}")

    # STEP 4: the meeting tool's own transcript, when one lies beside the recording: recorded, never used as the text
    provided = provided_transcript_service.describe_provided_transcript(ctx.repository / ctx.module,
                                                                        transcript["segments"])
    if provided is not None:
        agreement = ("holds no word to compare" if provided["agreement"] is None
                     else f"agrees on {provided['agreement']:.0%} of its words")
        ctx.log.info(f"phase {PHASE_NUMBER}: {provided['file']} beside the recording {agreement}; "
                     f"{len(provided['speakers'])} speaker name(s)")

    # STEP 5: write the numbered file, and judge it from disk before anything is built on it
    phase_file_service.write_phase_file(ctx.paths, PHASE_NUMBER,
                                        {"schemaVersion": 1, "hasAudio": has_audio, **transcript,
                                         **({"providedTranscript": provided} if provided is not None else {})})
    report = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, TRANSCRIBE_KIND))
    if not report.ok:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {report.render_summary()}")

        return _mark_phase(ctx, "failed", handles)
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(transcript['segments'])} segment(s), "
                 f"language {transcript['language'] or 'none'}")

    return _mark_phase(ctx, "complete", handles)
