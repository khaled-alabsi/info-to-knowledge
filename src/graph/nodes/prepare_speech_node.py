"""Phase 2 for an audio recording: the transcript turned into paragraphs of clean speech in ONE model call, and the run's
ingesting profile chosen.

An audio recording has no video, so there are no frames to take: this node writes `02_frames.json` with no item
(`medium: audio`). Phase 3 then prepares the notes when the run's ingesting profile declares preparation steps
(`prepare_notes_node`); otherwise this node records phase 3 complete with nothing to read, and phase 4 writes the
document from the paragraphs. The call gets every line of `01_transcript.json` with its id and time and gives back
`02_speech.md`, every line in exactly one paragraph, each one speaker's turn on one point.

The profile is the PROGRAM's when a tag of the run names one (`ingesting_profile_service.find_profile_in_tags`); only
when none does is the call asked to pick it too, from the list, and its pick is written with `chosenBy: model`. A
profile the person chose (the run form, `run --ingesting-profile`) or a conversion chose is never changed.

A takeaway run (a one-call profile) makes no call here: its phase 4 reads the transcript
lines itself, so this node writes the empty frame list and closes phase 3.

A PDF read as text only makes no call either: each block of phase 1's text is a paragraph as the
program read it, and each figure phase 1 found is cut as a frame into `02_frames.json` (`medium: pdf-text-only`),
which phase 3's frame loop then reads; with no figure, phase 3 is closed as for an audio recording.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph import routing
from graph.graph_state import RunState
from graph.nodes import prepare_frames_node
from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from graph.nodes.retry_loop import attempt_request_builder
from models import ingesting_profile_vocabulary
from models import output_contract
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from services import call_log_service
from services import chain_dispatch_service
from services import dry_run_service
from services import ingesting_profile_service
from services import office_source_service
from services import pdf_source_service
from services import performance_log_service
from services import phase_file_service
from services import preprocessing_step_service
from services import recording_medium_service
from services import run_tags_service
from services import speech_file_service
from services import validation_service
from utils import chain_response_utils
from utils import speech_markdown_utils
from validators import speech_validator

PHASE = "frames_preparing"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]
FRAMES_PROCESSING_PHASE = app_config.FRAMES_PROCESSING_PHASE
KIND = "speech_structuring"
SLUG = output_contract.SPEECH_SLUG
# Which validator judges the empty frame list. NOT the phase: see `services/validation_service.py`.
FRAMES_PREPARING_KIND = "frames_preparing"


def _build_phase_result(status: str,
                        closed: dict[str, str] | None = None) -> dict:
    """The node's answer to the graph: phase 2's status, the later phases this end closed, and no frame queued."""

    return {"phases": {PHASE: status, **(closed or {})}, "queue": []}


def _mark_phase_and_empty_queue(ctx: RunContext,
                                status: str,
                                handles: TimingHandles | None,
                                closed: dict[str, str] | None = None) -> dict:
    """Phase 2's end in the run record; `closed` holds the later phases this end also closed."""

    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return _build_phase_result(status, closed)


def _is_profile_settled_before_call(ctx: RunContext,
                                is_dry_run: bool) -> bool:
    """Whether the run's profile is settled before the call, so the call need not pick it: the person or a conversion
    chose one, or a tag of the run names one, which is written then (a dry run writes nothing)."""

    folder = ctx.paths.output_root
    recorded = ingesting_profile_service.read_profile(folder)
    if recorded and recorded["chosenBy"] in ingesting_profile_vocabulary.FIXED_CHOOSERS:
        ctx.log.info(f"phase {PHASE_NUMBER}: ingesting profile {recorded['profile']}, chosen by {recorded['chosenBy']}")

        return True
    picked = ingesting_profile_service.find_profile_in_tags(run_tags_service.read_tags(folder))
    if picked is None:
        return False
    if not is_dry_run:
        ingesting_profile_service.write_profile(folder, picked, "tag")
    ctx.log.info(f"phase {PHASE_NUMBER}: ingesting profile {picked}, from the run's tags")

    return True


def _close_phase_3(ctx: RunContext) -> dict[str, str]:
    """Phase 3 recorded complete with nothing to read, unless the run's ingesting profile declares preparation steps
    for it: then it is left to phase 3. A text-only PDF's figures never
    reach here: both callers hand them to the frame loop first."""

    if preprocessing_step_service.list_due_steps(ctx.paths):
        return {}
    ctx.store.set_phase(FRAMES_PROCESSING_PHASE, "complete")

    return {FRAMES_PROCESSING_PHASE: "complete"}


def _write_frames_file(ctx: RunContext,
                       medium: str,
                       items: list[dict] | None = None,
                       placed_figures: list[dict] | None = None) -> None:
    """`02_frames.json` with its `medium` (an audio recording's, or a PDF read as its text) and its `items`: none for an
    audio recording, a text-only PDF's figures phase 3 reads: how a resume and the page know phase 2 is done. A PDF's
    figures placed unread stand apart, under `placedFigures`, where no phase 3 step reads them."""

    frames_file = {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION, "medium": medium, "items": items or [],
                   "droppedFrames": []}
    if placed_figures:
        frames_file[output_contract.PLACED_FIGURES_KEY] = placed_figures
    phase_file_service.write_phase_file(ctx.paths, PHASE_NUMBER, frames_file)


def _mark_place_before(figures: list[dict],
                       lines: list[dict]) -> list[dict]:
    """Each figure placed unread with `placeBefore`, the id of the first paragraph after it in the text (`S0415`), or
    empty when it stands after the last: where the published document puts it."""

    marked = []
    for figure in figures:
        following = [line["id"] for line in lines if line["start"] > figure["timestamp"]]
        marked.append({**figure, "placeBefore": following[0] if following else ""})

    return marked


def _write_pdf_paragraphs(ctx: RunContext,
                          lines: list[dict]) -> int:
    """A PDF read as text only: each block of phase 1's cleaned text one paragraph, as the program read it. Its text
    is the document's own, so nothing is asked of a model, and nobody speaks it: no speaker. The paragraph count."""

    paragraphs = [{"first": line["id"], "last": line["id"], "speaker": "", "text": line["text"]}
                  for line in lines]
    speech_file_service.write_speech(ctx.paths, {"profile": None, "paragraphs": paragraphs})

    return len(paragraphs)


def _finish(ctx: RunContext,
            handles: TimingHandles | None) -> dict:
    """The frame list judged from disk (empty, or a text-only PDF's figures), the figures handed to the frame loop or
    phase 3 closed when nothing is to be prepared, and phase 2 done."""

    report = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, FRAMES_PREPARING_KIND))
    if not report.ok:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {report.render_summary()}")

        return _mark_phase_and_empty_queue(ctx, "failed", handles)
    # A text-only PDF's figures are frames phase 3 reads: they go into the run record and the frame loop's queue
    if phase_file_service.has_run_frames(ctx.paths):
        performance_log_service.record_phase_end(ctx, PHASE, "complete", handles)

        return prepare_frames_node.finish_phase(ctx)
    closed = _close_phase_3(ctx)

    return _mark_phase_and_empty_queue(ctx, "complete", handles, closed)


def _run_speech_call(ctx: RunContext,
                     inputs: dict) -> bool:
    """The one call that structures the speech, up to `budgets.maxAttempts` attempts; whether it was accepted."""

    job = JobRetryContext(phase=PHASE, job_kind=KIND,
                          snapshot_targets=[speech_file_service.build_speech_path(ctx.paths),
                                            call_log_service.build_call_log_path(ctx.paths, SLUG)],
                          inputs=inputs)
    # The call is the phase's one step: its line counts the calls and tokens the phase line sums (the phase line said
    # "no model call" with no step of its own)
    call_handles = performance_log_service.start_unit(ctx, PHASE, SLUG, label="structuring the speech into paragraphs")
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=SLUG,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    performance_log_service.record_unit(ctx, call_handles, performance_log_service.PART_LEVEL,
                                        "accepted" if outcome.is_accepted else "blocked", [])

    return outcome.is_accepted


def _take_earlier_answer(ctx: RunContext,
                         lines: list[dict],
                         is_profile_asked: bool) -> bool:
    """Whether an earlier attempt's paragraphs, newest first, pass the checks as they are now: written and taken, with
    no call. A resume asked again after its checks were mended, and the call, told the last refusal, summarised the
    speech; the answer before it passed the mended checks."""

    snapshots = sorted(ctx.paths.output_root.glob(f"logs/*/{PHASE}/{SLUG}/attempt-*/output-snapshot/"
                                                  f"{output_contract.SPEECH_FILE}"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    for snapshot in snapshots:
        parsed, _why = speech_markdown_utils.parse_speech(snapshot.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        speech_file_service.write_speech(ctx.paths, speech_markdown_utils.build_settled_paragraphs(parsed, lines))
        if speech_validator.validate_speech_record(ctx.paths, is_profile_asked).ok:
            ctx.log.info(f"phase {PHASE_NUMBER}: the paragraphs of {snapshot.parents[1].name} pass the checks as they are "
                         f"now: taken, with no call")

            return True

    return False


def _prepare_pdf_text(ctx: RunContext,
                      transcript: dict,
                      lines: list[dict]) -> dict:
    """Phase 2 of a PDF read as text only: its blocks written as the paragraphs, and each figure phase 1 found cut as
    a frame of its own, which phase 3 reads and phase 4 places, redraws or leaves out."""

    handles = performance_log_service.start_unit(ctx, PHASE, None)
    count = _write_pdf_paragraphs(ctx, lines)
    frames_dir = ctx.paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME
    # A Word or OpenDocument file's pictures are files pandoc wrote out; a PDF's are cut
    # from its pages
    if office_source_service.is_office(ctx.repository / ctx.module):
        figures = office_source_service.render_figures(transcript, ctx.paths.output_root, frames_dir, ctx.log)
        what = "an office document"
    else:
        figures = pdf_source_service.render_figures(ctx.repository / ctx.module, transcript, frames_dir)
        what = "a PDF read as text only"
    ctx.log.info(f"phase {PHASE_NUMBER}: {what}, {count} paragraph(s) from its text and {len(figures)} figure(s) as "
                 f"frames, no model call")

    # Which figures phase 3 reads; the others the published document places where they stood
    # A PDF's markdown conversion reads none of them
    settings = app_config.settings.pdf
    medium = recording_medium_service.read_chosen_medium(ctx.paths.output_root) or output_contract.PDF_TEXT_ONLY_MEDIUM
    if medium == output_contract.PDF_MARKDOWN_CONVERSION_MEDIUM:
        read, placed = [], figures
        ctx.log.info(f"phase {PHASE_NUMBER}: a markdown conversion, every figure ({len(placed)}) placed where it stood")
    else:
        read, placed = pdf_source_service.split_figures_to_read(items=figures,
                                                                figure_reading=settings.figure_reading,
                                                                read_limit=settings.figure_read_limit)
        if placed:
            ctx.log.info(f"phase {PHASE_NUMBER}: {len(read)} figure(s) to read, {len(placed)} placed unread where they "
                         f"stood (pdf.figureReading {settings.figure_reading}, limit {settings.figure_read_limit})")
    _write_frames_file(ctx, medium, read, _mark_place_before(figures=placed, lines=lines))

    return _finish(ctx, handles)


def prepare_speech_node(state: RunState,
                        config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    is_dry_run = bool(state.get("is_dry_run"))

    # STEP 1: a run that does not ask for phase 2, or finished it, reads it from disk, as a video's phase 2 does; a
    # takeaway run has no paragraphs to read
    is_takeaway_run = ingesting_profile_service.is_one_call_profile(ctx.paths.output_root)
    # A run that ends before this phase (`--to 1`) has no use for the paragraphs: it recorded phase 2 failed for a file
    # it was never asked to write (DEFECTS 488). A run asking for a later phase alone still reads them from disk below
    if state.get("last_phase", routing.LAST_PHASE_NUMBER) < PHASE_NUMBER:
        return _build_phase_result("complete")
    if not routing.is_phase_selected(state, PHASE_NUMBER) or ctx.store.get_phase(PHASE) == "complete":
        has_figures = phase_file_service.has_run_frames(ctx.paths)
        if not is_takeaway_run and not has_figures and speech_file_service.read_speech(ctx.paths) is None:
            ctx.log.error(f"phase {PHASE_NUMBER} failed: {output_contract.SPEECH_FILE} is missing or unreadable. It is "
                          f"written by phase {PHASE_NUMBER}: run `--phase {PHASE_NUMBER}` first.")

            return _mark_phase_and_empty_queue(ctx, "failed", None)
        ctx.log.info(f"phase {PHASE_NUMBER}: reading {output_contract.SPEECH_FILE} from disk")
        # A text-only PDF's figures are frames: the frame loop gets those still to read, as a video's resume does
        if has_figures:
            return prepare_frames_node.finish_phase(ctx)

        return _build_phase_result("complete", _close_phase_3(ctx))

    # STEP 2: the profile, by the program when a tag names one; else the call picks it
    ctx.log.info("")
    is_profile_asked = not _is_profile_settled_before_call(ctx, is_dry_run)

    # STEP 2b: a takeaway run's phase 4 reads the transcript lines itself, so the speech is not structured: no call, the
    # frame list written empty and phase 3 closed
    if is_takeaway_run:
        ctx.log.info(f"phase {PHASE_NUMBER}: a takeaway run, no speech structuring and no model call")
        if is_dry_run:
            return _build_phase_result("complete")
        handles = performance_log_service.start_unit(ctx, PHASE, None)
        _write_frames_file(ctx, output_contract.AUDIO_MEDIUM)

        return _finish(ctx, handles)

    # STEP 2c: the transcript's numbered lines
    transcript = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"])
    lines = speech_markdown_utils.list_numbered_lines(transcript)

    # STEP 2d: a PDF read as text only: its blocks are the paragraphs, with no call; its figures are its frames
    # A PDF of figures alone has no line and is still read
    if recording_medium_service.read_chosen_medium(ctx.paths.output_root) in output_contract.PDF_TEXT_ONLY_MEDIUMS:
        if not lines and not transcript.get("figures"):
            ctx.log.error(f"phase {PHASE_NUMBER} failed: phase 1 found no text and no figure in the PDF")

            return _mark_phase_and_empty_queue(ctx, "failed", None)

        return _build_phase_result("complete") if is_dry_run else _prepare_pdf_text(ctx, transcript, lines)

    # STEP 2e: an audio recording: the call's inputs, from its lines
    if not lines:
        ctx.log.error(f"phase {PHASE_NUMBER} failed: the transcript holds no speech to structure")

        return _mark_phase_and_empty_queue(ctx, "failed", None)
    ctx.log.info(f"phase {PHASE_NUMBER}: an audio recording, structuring its speech")
    inputs = {"lines": lines, "tags": run_tags_service.read_tags(ctx.paths.output_root),
              "is_profile_asked": is_profile_asked, "recording": ctx.module}

    # STEP 3: on a dry run, render what the call WOULD be sent, and call no model
    if is_dry_run:
        messages = chain_dispatch_service.build_messages(
            attempt_request_builder.build_standalone_request(ctx, KIND, inputs=inputs))
        system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
        dry_run_service.describe_dry_run(ctx, PHASE, SLUG, system_text, human_text)

        return _build_phase_result("complete")

    # STEP 4: an earlier attempt's paragraphs that pass the checks as they are now, or one call per attempt, up to
    # `budgets.maxAttempts`; paragraphs not accepted block the phase
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    if not (_take_earlier_answer(ctx, lines, is_profile_asked) or _run_speech_call(ctx, inputs)):
        return _mark_phase_and_empty_queue(ctx, "blocked", handles)
    speech = speech_file_service.read_speech(ctx.paths) or {"paragraphs": []}
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(speech['paragraphs'])} paragraph(s) from {len(lines)} line(s), "
                 f"{len({paragraph['speaker'] for paragraph in speech['paragraphs']})} speaker(s)")

    # STEP 5: no frames to take: the frame list is written empty and says why, only now that the paragraphs are
    # accepted. Its file is how a resume and the page know phase 2 is done; written before the call, a run killed or
    # blocked mid-call was read as past phase 2, and the page showed phase 3 running during the speech call
    _write_frames_file(ctx, output_contract.AUDIO_MEDIUM)

    # STEP 6: the profile the call picked, when it was asked
    if is_profile_asked:
        ingesting_profile_service.write_profile(ctx.paths.output_root, speech["profile"], "model")
        ctx.log.info(f"phase {PHASE_NUMBER}: ingesting profile {speech['profile']}, picked by the model")

    return _finish(ctx, handles)
