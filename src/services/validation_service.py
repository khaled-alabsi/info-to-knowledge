# <!-- sync: seed/services.validation_service v1 -->
"""Every job's validation, in one place, keyed by the kind of job.

The only thing that decides whether work was done, and it decides it from bytes on disk after the
worker is gone. Keyed by KIND for the same reason the prompt service is.

`snapshot` is the pre-attempt copy of whatever the job may overwrite. A validator that compares an
artifact against the scaffold that was there before this attempt reads it; it is absent outside a
run, which is why every caller may pass `None`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from models import output_contract
from models.attempt_request import AttemptRequest
from models.section_judging_context import SectionJudgingContext
from models.run_context import RunContext
from models.validation_report import ValidationReport
from services import document_file_service
from services import phase_file_service
from services import batch_summaries_service
from services.artifact_snapshot_service import Snapshot
from utils import document_plan_utils
from utils import document_text_utils
from validators import document_folder_validator
from validators import item_validator
from validators import document_validator
from validators import loop_items_validator
from validators import review_validator
from validators import prepared_validator
from validators import speech_validator
from validators import takeaway_validator
from validators import transcript_file_validator


# kind -> (ctx, request, snapshot) -> ValidationReport
#
# Every adapter below takes the same three arguments because the TABLE does, not because each
# validator wants them: a validator is a pure function over bytes on disk, so it takes the paths it
# reads and nothing else, and an adapter that ignores the request or the snapshot is saying exactly
# that.
def _validate_frames_preparing(ctx: RunContext,
                               request: AttemptRequest,
                               snapshot: Snapshot | None) -> ValidationReport:
    """The item list phase 2 wrote (`02_frames.json`), judged by the shape every later phase indexes into."""

    return loop_items_validator.validate_loop_items(ctx.paths)


def _validate_transcript_file(ctx: RunContext,
                              request: AttemptRequest,
                              snapshot: Snapshot | None) -> ValidationReport:
    """The file phase 1 wrote: segments with a text, a start and an end."""

    return transcript_file_validator.validate_transcript_file(ctx.paths)


def _validate_document(ctx: RunContext,
                       request: AttemptRequest,
                       snapshot: Snapshot | None) -> ValidationReport:
    """The finished facts file and its provenance, as the program assembled them."""

    return document_validator.validate_document(ctx.paths)


def _validate_outline(ctx: RunContext,
                      request: AttemptRequest,
                      snapshot: Snapshot | None) -> ValidationReport:
    """One batch's outline call, or R12's table of contents over every entry of the document: how it ended, then
    whether every entry it was handed is placed exactly once."""

    entries = request.inputs.get("entries") or []

    return document_validator.validate_outline(ctx.paths, str(request.inputs.get("slug") or ""),
                                                [entry["id"] for entry in entries],
                                                [entry["id"] for entry in entries
                                                 if document_plan_utils.is_kept_note(entry)])


def _validate_table_of_contents(ctx: RunContext,
                                request: AttemptRequest,
                                snapshot: Snapshot | None) -> ValidationReport:
    """R12's whole table of contents: the outline's checks over every entry, and one chapter per agenda point."""

    entries = request.inputs.get("entries") or []

    return document_validator.validate_table_of_contents(ctx.paths, str(request.inputs.get("slug") or ""), entries,
                                                         [entry["id"] for entry in entries
                                                          if document_plan_utils.is_kept_note(entry)],
                                                         len(request.inputs.get("points") or []))


def _validate_draft_headings(ctx: RunContext,
                             request: AttemptRequest,
                             snapshot: Snapshot | None) -> ValidationReport:
    """One agenda point read before the table of contents: every chunk read, and subjects that start at real entries."""

    return document_validator.validate_draft_headings(ctx.paths, str(request.inputs.get("slug") or ""),
                                                       int(request.inputs.get("chapter") or 0),
                                                       int(request.inputs.get("chunkCount") or 0),
                                                       list(request.inputs.get("entries") or []))


def _validate_contents(ctx: RunContext,
                       request: AttemptRequest,
                       snapshot: Snapshot | None) -> ValidationReport:
    """The title call: how it ended, then whether it wrote a title the prose rules allow."""

    return document_validator.validate_contents(ctx.paths)


def _validate_parts(ctx: RunContext,
                    request: AttemptRequest,
                    snapshot: Snapshot | None) -> ValidationReport:
    """The parts call: how it ended, then whether the parts open the meeting and follow it in order."""

    return document_validator.validate_parts(ctx.paths, len(request.inputs.get("moments") or []))


def _validate_summary(ctx: RunContext,
                      request: AttemptRequest,
                      snapshot: Snapshot | None) -> ValidationReport:
    """The summary call: how it ended, then its sources, its length and one agenda line per part."""

    return document_validator.validate_summary(ctx.paths, len(request.inputs.get("parts") or []),
                                                list(request.inputs.get("knownEntryIds") or []),
                                                int(request.inputs.get("maxWords") or 0))


def _validate_executive_summary_draft(ctx: RunContext,
                                      request: AttemptRequest,
                                      snapshot: Snapshot | None) -> ValidationReport:
    """The conversation's first reply, R12: a working title and three to eight sentences within the word limit."""

    return document_validator.validate_executive_summary_draft(ctx.paths, int(request.inputs.get("maxWords") or 0))


def _validate_section(ctx: RunContext,
                      request: AttemptRequest,
                      snapshot: Snapshot | None) -> ValidationReport:
    """One section part's call: how it ended, whether it uses its entries and only those, and whether it says again
    what an earlier section said."""

    report = document_validator.validate_section(ctx.paths, str(request.inputs.get("slug") or ""),
                                                  list(request.inputs.get("topicEntryIds") or []),
                                                  list(request.inputs.get("earlierSlugs") or []))
    # R12: a section written in the conversation may link to other topics, and every link must resolve.
    if "topicIds" in request.inputs:
        links = document_validator.validate_section_links(ctx.paths, str(request.inputs.get("slug") or ""),
                                                           list(request.inputs["topicIds"]))
        report.merge(links)

    return report


def _list_cited_ids(ctx: RunContext,
                    section_slug: str) -> set[str]:
    """The note ids a written section cites in its blocks; none when it was not written."""

    if not document_file_service.build_section_path(ctx.paths, section_slug).is_file():
        return set()

    return {entry_id for block in document_file_service.read_section(ctx.paths, section_slug).get("blocks") or []
            for entry_id in block.get("entry_ids") or []}


def _validate_chapter(ctx: RunContext,
                      request: AttemptRequest,
                      snapshot: Snapshot | None) -> ValidationReport:
    """One whole chapter's call: one section per heading, and each section judged as a section is, against its own
    entries, against everything written before it (earlier chapters and this chapter's earlier headings), and for its
    links."""

    slug = str(request.inputs.get("slug") or "")
    sections = request.inputs.get("sections") or []
    report = document_validator.validate_chapter_count(ctx.paths, slug, [f"### {section['heading']} ({section['topicId']})"
                                                                          for section in sections])
    if not report.ok:
        return report
    earlier = list(request.inputs.get("earlierSlugs") or [])
    # Within one request a note is judged against all its headings: the headings' starts are an earlier answer's
    # choice, and a note that falls in one heading may be about the next one's subject, where the worker writes it
    #. Cited under any heading of the request it is covered; only a note outside the request is foreign.
    request_ids = list(dict.fromkeys(entry_id for section in sections for entry_id in section["entryIds"]))
    owners = document_plan_utils.map_line_owners([entry for section in sections for entry in section.get("entries") or []])
    cited = {section["slug"]: {owners.get(entry_id, entry_id) for entry_id in _list_cited_ids(ctx, section["slug"])}
             for section in sections}
    for section in sections:
        elsewhere = set().union(*(ids for other, ids in cited.items() if other != section["slug"]))
        scope = SectionJudgingContext(entry_ids=request_ids, line_owners=owners,
                             speech_texts=[str(entry.get("text") or "") for entry in section.get("entries") or []
                                           if entry.get("kind") == "speech"],
                             screen_texts=[str(entry.get("text") or "") for entry in section.get("entries") or []
                                           if entry.get("kind") == "markdown"],
                             note_texts=[str(entry.get("text") or "") for entry in section.get("entries") or []],
                             required_ids=[entry_id for entry_id in section["requiredIds"] if entry_id not in elsewhere],
                             decision_ids=[entry["id"] for entry in section.get("entries") or []
                                           if document_text_utils.is_decision_note(str(entry.get("text") or ""))],
                             kept_ids=[entry["id"] for entry in section.get("entries") or []
                                       if document_plan_utils.is_kept_note(entry)],
                             code_blocks={entry["id"]: blocks for entry in section.get("entries") or []
                                          if entry.get("kind") == "speech"
                                          and (blocks := document_text_utils.list_code_blocks(str(entry.get("text") or "")))})
        part = document_validator.judge_section(ctx.paths, section["slug"], scope, earlier)
        if "topicIds" in request.inputs:
            links = document_validator.validate_section_links(ctx.paths, section["slug"], list(request.inputs["topicIds"]))
            part.merge(links)
        images = document_validator.validate_section_images(ctx.paths, section["slug"])
        part.merge(images)
        needed = document_validator.validate_needed_screenshots(
            ctx.paths, section["slug"], list(section.get("neededFrames") or []), slug)
        part.merge(needed)
        for finding in part.findings:
            finding.detail = f"under \"{section['heading']}\": {finding.detail}"
        report.merge(part)
        earlier.append(section["slug"])

    return report


def _validate_document_folder(ctx: RunContext,
                              request: AttemptRequest,
                              snapshot: Snapshot | None) -> ValidationReport:
    """The folder phase 5 wrote: every link resolves, every frame is linked, every caption cites known entries."""

    return document_folder_validator.validate_document_folder(ctx.paths, list(request.inputs.get("knownEntryIds") or []))


def _validate_review_todos(ctx: RunContext,
                           request: AttemptRequest,
                           snapshot: Snapshot | None) -> ValidationReport:
    """R16: the critique's todo list, each todo's sentence found in its section."""

    return review_validator.validate_review_todos(ctx.paths, str(request.inputs.get("slug") or ""),
                                                  int(request.inputs.get("maxTodos") or 0))


def _validate_resolution(ctx: RunContext,
                         request: AttemptRequest,
                         snapshot: Snapshot | None) -> ValidationReport:
    """R16: one todo's resolution: its edit and its sources, or its searches and its question."""

    return review_validator.validate_resolution(ctx.paths, str(request.inputs.get("slug") or ""),
                                                str(request.inputs.get("todoKind") or ""),
                                                str(request.inputs.get("todoQuote") or ""))


def _validate_reviewed_document(ctx: RunContext,
                                request: AttemptRequest,
                                snapshot: Snapshot | None) -> ValidationReport:
    """R16: the published document once the review changed it, judged as phase 5's folder is."""

    return document_folder_validator.validate_document_file(Path(str(request.inputs.get("folder") or "")),
                                                            str(request.inputs.get("document") or ""),
                                                            list(request.inputs.get("knownEntryIds") or []),
                                                            document_folder_validator.list_note_times(ctx.paths))


def _validate_frames_processing(ctx: RunContext,
                                request: AttemptRequest,
                                snapshot: Snapshot | None) -> ValidationReport:
    """One item's artifact, judged by the validator this playbook shipped for its work kind."""

    # An adapter rather than a validator with this signature: a validator is a PURE function over
    # bytes on disk, so it takes the paths and the slug and knows nothing about a request or a run.
    # The table is what maps one to the other, and it is the only place that mapping exists.

    return item_validator.validate_item(ctx.paths, str(request.inputs.get("slug") or ""))


def _validate_frame_batch(ctx: RunContext,
                          request: AttemptRequest,
                          snapshot: Snapshot | None) -> ValidationReport:
    """Several frames answered in one call: how the call ended, then each frame's record."""

    return item_validator.validate_frame_group(ctx.paths, str(request.inputs.get("slug") or ""),
                                               [item["slug"] for item in request.inputs.get("items") or []])


def _validate_frame_record(ctx: RunContext,
                           request: AttemptRequest,
                           snapshot: Snapshot | None) -> ValidationReport:
    """One frame of a group, after the group's call was judged: its record alone, and when it has none, why, from the
    log of the call that answered it."""

    return item_validator.validate_record(ctx.paths, str(request.inputs.get("slug") or ""),
                                          str(request.inputs.get("callSlug") or ""))


def _validate_batch_summary(ctx: RunContext,
                            request: AttemptRequest,
                            snapshot: Snapshot | None) -> ValidationReport:
    """R15: the call that asked for a batch's account alone, then the account."""

    slug = str(request.inputs.get("slug") or "")

    return item_validator.validate_batch_summary(ctx.paths, slug, batch_summaries_service.build_summary_slug(slug),
                                                 len(request.inputs.get("items") or []))


def _validate_batch_summary_record(ctx: RunContext,
                                   request: AttemptRequest,
                                   snapshot: Snapshot | None) -> ValidationReport:
    """R15: a batch's account, after the call that wrote it was judged."""

    return item_validator.validate_batch_summary_record(
        ctx.paths, batch_summaries_service.build_summary_slug(str(request.inputs.get("slug") or "")),
        len(request.inputs.get("items") or []))


def _validate_critique_collection(ctx: RunContext,
                                  request: AttemptRequest,
                                  snapshot: Snapshot | None) -> ValidationReport:
    """Phase 6's call, then the rule set it left."""

    names = [str(group.get("name") or "") for group in request.inputs.get("groups") or []]

    return item_validator.validate_critique_collection(ctx.paths, output_contract.CRITIQUE_COLLECTION_SLUG, names)


def _validate_speech(ctx: RunContext,
                     request: AttemptRequest,
                     snapshot: Snapshot | None) -> ValidationReport:
    """Phase 2's call for an audio recording, then the paragraphs it left."""

    return speech_validator.validate_speech(ctx.paths, output_contract.SPEECH_SLUG,
                                            bool(request.inputs.get("is_profile_asked")))


def _validate_takeaway(ctx: RunContext,
                       request: AttemptRequest,
                       snapshot: Snapshot | None) -> ValidationReport:
    """Phase 4's one call of a takeaway run, then the takeaway it left."""

    return takeaway_validator.validate_takeaway(ctx.paths, output_contract.TAKEAWAY_SLUG,
                                                [line["id"] for line in request.inputs.get("lines") or []])


def _validate_prepared(ctx: RunContext,
                       request: AttemptRequest,
                       snapshot: Snapshot | None) -> ValidationReport:
    """One preparation call of an audio recording's phase 3, then the file it left."""

    inputs = request.inputs

    return prepared_validator.validate_prepared(ctx.paths, inputs["step"], inputs.get("item_number"),
                                                inputs.get("paragraphs") or [],
                                                bool(inputs.get("is_every_paragraph_due")))


def _validate_critique(ctx: RunContext,
                       request: AttemptRequest,
                       snapshot: Snapshot | None) -> ValidationReport:
    """The call that asked a batch to criticise its own notes, then the rules it left. The notes' own outlines are
    handed over as the screens' words, so a rule that quotes this meeting's screen is caught where it is written."""

    slug = str(request.inputs.get("slug") or "")
    batch = request.inputs.get("batch") or {}
    frames = [int(number) for number in (batch.get("frames") or [])]
    screens = [str((entry or {}).get("screen") or "") for entry in batch.get("accepted") or []]

    return item_validator.validate_critique(ctx.paths, slug, batch_summaries_service.build_critique_slug(slug), frames, screens)


def _validate_second_pass(ctx: RunContext,
                          request: AttemptRequest,
                          snapshot: Snapshot | None) -> ValidationReport:
    """R19: the call that asked a batch to read its own notes again, then the answer."""

    slug = str(request.inputs.get("slug") or "")
    frames = [int(number) for number in ((request.inputs.get("batch") or {}).get("frames") or [])]

    return item_validator.validate_second_pass(ctx.paths, slug, batch_summaries_service.build_second_pass_slug(slug), frames)


def _validate_open_questions(ctx: RunContext,
                             request: AttemptRequest,
                             snapshot: Snapshot | None) -> ValidationReport:
    """The call that asked a batch for the questions it leaves open, then what it left."""

    return item_validator.validate_batch_follow_up(ctx.paths, str(request.inputs.get("slug") or ""), None)


def _validate_answer_questions(ctx: RunContext,
                               request: AttemptRequest,
                               snapshot: Snapshot | None) -> ValidationReport:
    """The call that asked a batch to answer its open questions, then what it left: every question in one section."""

    questions = (request.inputs.get("batch") or {}).get("questions") or []

    return item_validator.validate_batch_follow_up(ctx.paths, str(request.inputs.get("slug") or ""), len(questions))


# Keyed by KIND, never by phase: two steps of one phase validate different files. A kind with no row
# here RAISES rather than defaulting - see `validate`.
VALIDATORS: dict[str, Callable[[RunContext, AttemptRequest, Snapshot | None], ValidationReport]] = {
    "transcribe": _validate_transcript_file,
    "frames_preparing": _validate_frames_preparing,
    "frames_processing": _validate_frames_processing,
    "frame_batch": _validate_frame_batch,
    "frame_record": _validate_frame_record,
    "batch_summary": _validate_batch_summary,
    "batch_summary_record": _validate_batch_summary_record,
    "critique": _validate_critique,
    "critique_collection": _validate_critique_collection,
    "speech_structuring": _validate_speech,
    "takeaway": _validate_takeaway,
    "preprocessing": _validate_prepared,
    "second_pass": _validate_second_pass,
    "open_questions": _validate_open_questions,
    "answer_questions": _validate_answer_questions,
    "parts": _validate_parts,
    "outline": _validate_outline,
    "table_of_contents": _validate_table_of_contents,
    "draft_headings": _validate_draft_headings,
    "contents": _validate_contents,
    "section": _validate_section,
    "chapter": _validate_chapter,
    "summary": _validate_summary,
    "executive_summary_draft": _validate_executive_summary_draft,
    "finalize": _validate_document,
    "publish": _validate_document_folder,
    "review_todos": _validate_review_todos,
    "review_resolution": _validate_resolution,
    "review": _validate_reviewed_document,
}

# A crashed check, reported as a finding so it reads as a playbook bug rather than the artifact's
# fault. `retry_loop_runner._UNREPAIRABLE` carries it: no repair of the artifact fixes a broken check.
CRASH_CODE = "ITEMS_VALIDATOR_CRASHED"

__all__ = ["validate", "validate_or_report_crash", "CRASH_CODE", "VALIDATORS"]


def validate(ctx: RunContext,
             request: AttemptRequest,
             snapshot: Snapshot | None = None) -> ValidationReport:
    """Judge what is on disk for one attempt, launching nothing."""

    # An unknown kind raises rather than defaulting: a job wired to a validator that does not exist
    # would otherwise report every attempt as clean, which is the one failure this whole design exists
    # to prevent.

    validator = VALIDATORS.get(request.job_kind)
    if validator is None:
        raise KeyError(f"no validator for job kind {request.job_kind!r}")

    return validator(ctx, request, snapshot)


# Two rules of a recording's text that a PDF's text breaks rightly. A clock time in a recording's text is a position in
# the recording, which the reader cannot use; a PDF has none, so a time in its text is its own content: a guide's retry
# example ("retried at 10:00:02") was refused, and the writer dropped its seconds. Speech copied into the text is filler
# and garble; a PDF's "speech" is its own sentences, and refused for quoting one, the writer rephrased it and added
# claims the guide does not make (pdf-01)
_SPEECH_ONLY_CODES = ("DOCUMENT_TIMESTAMP", "DOCUMENT_TRANSCRIPT_COPIED")


def _drop_speech_only_findings(request: AttemptRequest,
                                  report: ValidationReport) -> ValidationReport:
    """The report with its clock-time and copied-speech findings taken out when the run reads a PDF."""

    if request.paths is not None and phase_file_service.is_pdf_run(request.paths):
        report.findings = [finding for finding in report.findings if finding.code not in _SPEECH_ONLY_CODES]

    return report


def validate_or_report_crash(ctx: RunContext,
                             request: AttemptRequest,
                             snapshot: Snapshot | None = None) -> ValidationReport:
    """`validate`, with a crashing validator turned into a REJECTION under `CRASH_CODE`."""

    # A validator sees whatever a model chose to write, so it will eventually meet a shape its author
    # did not imagine - and an exception raised inside a node propagates out of the graph and discards
    # every item still queued behind the one that tripped it. Every call from a node comes through
    # here; `validate` itself is for a caller that WANTS the exception, such as a test.

    try:
        return _drop_speech_only_findings(request, validate(ctx, request, snapshot))
    except Exception as exc:  # noqa: BLE001 - a broken validator must not end the run
        ctx.log.exception(f"the validator for {request.job_kind!r} crashed: {type(exc).__name__}: {exc}")
        report = ValidationReport(subject=str(request.inputs.get("slug") or request.job_kind))
        report.add(CRASH_CODE, f"{type(exc).__name__}: {exc}",
                   fix="this is a playbook bug, not an artifact problem")

        return report
