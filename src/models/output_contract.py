"""The output contract, which is NOT overridable. The status vocabulary, the numbered phase
files, the folder names, the words the document may not contain, and the placeholders every
instruction renders them through.

A validator, a schema and a prompt all state these, and changing one alone produces a run where
every frame fails a check the records actually satisfy. Settings (budgets, hosts, model choices)
live in `config.py` and are overridable; nothing here is.
"""

from __future__ import annotations

ARTIFACT_DIR_NAME = ".agents-artifacts"
# REQUIREMENTS.md R4 puts every call's log under `<output>/logs/`. A run's directory is
# `<output>/logs/<run id>/`, and each attempt's prompt, reply and decision are inside it.
RUNS_DIR_NAME = "logs"
# The run header's line naming its log file, inside the run folder: once a job's console holds it, the run made its folder
RUN_LOG_LINE = "  log   : "
# Each frame after it was read, `03_processed-frames/frame-005.md`, beside `frames/`, which holds the frames
# themselves, so the two names must differ. `services/record_file_service.py` derives the path.
ITEMS_DIR_NAME = "03_processed-frames"
# The newest model call of each step of phases 3 to 6. The capture node copies it into every attempt's directory.
CALLS_DIR_NAME = "calls"
# The JPEGs phase 2 cuts, `frames/frame-005.jpg`.
FRAME_IMAGES_DIR_NAME = "frames"
AUDIO_FILE = "audio.wav"

TRANSCRIPT_FILE = "01_transcript.json"
LOOP_ITEMS_FILE = "02_frames.json"
# REQUIREMENTS.md R15: each batch's third-person account of it, beside the frame records and never inside them.
BATCH_SUMMARIES_FILE = "03_batch_summaries.json"
# The merge as one Markdown file for the agents that write, illustrate and review:
# one section per frame, then the speech no frame read. Phase 3 writes it after the frame notes are complete.
NOTES_FILE = "03_notes.md"
# Phase 4's document: the text, before any frame.
SUMMARY_FILE = "04_document.md"
# Phase 4's working folder: the scaffold `draft.md`, the replies of its follow-ups, and its conversation's log.
WRITING_DIR_NAME = "04_writing"
WRITE_CONVERSATION_LOG = f"{WRITING_DIR_NAME}/logs/conversation.jsonl"
# REQUIREMENTS.md R10 and R13: the folder a reader opens, moved as one piece, is named after the meeting,
# `<event>-<speakers>-<date>/<event>-<speakers>-<date>.md` (`services/document_folder_service.build_document_dir`).
# Phase 4 publishes the document, its images and its provenance straight into it, with no unnamed copy; the looks and
# cuts its chapters made are kept in `04_writing/images/`.
RESOURCES_DIR_NAME = "resources"
DOCUMENT_PROVENANCE_FILE = "provenance.json"
IMAGE_WORK_DIR_NAME = f"{WRITING_DIR_NAME}/images"   # the looks and the cuts its chapters made
# Phase 6's folder and the one file it writes: every batch's critique of one run, grouped into one rule set a person
# folds into the worker's instructions. The rules are generic by the check that accepted each critique, so this file
# carries no meeting content.
CRITIQUE_COLLECTION_DIR_NAME = "06_critique-collection"
CRITIQUE_COLLECTION_FILE = "critique-collection.md"
# Phase 6 makes ONE call for the whole run, so its call log and its snapshot are under one slug, not one per batch.
CRITIQUE_COLLECTION_SLUG = "critique_collection"
# Where a run made before phase 6 was renamed from "lessons" to "critique collection" keeps its rule set and its call
# log; read, and taken back by a reset, as the names above
RETIRED_CRITIQUE_COLLECTION_DIR_NAME = "06_lessons"
RETIRED_CRITIQUE_COLLECTION_FILE = "anti-patterns.md"
RETIRED_CRITIQUE_COLLECTION_SLUG = "lessons"
# Phase 7's folder, document reshaping (the owner's name): a worker cleans up, rewrites or reshapes into knowledge the
# published document, part by part. It keeps the document as it was before (`before.md`),
# the pass's state, each part's conversation, and the report, written last, which is the phase's file. A run made
# before the rename keeps its folder under the old name, which every reader still finds
# (`document_pass_service.build_reshaping_dir`).
DOCUMENT_RESHAPING_DIR_NAME = "07_reshaping"
RETIRED_DOCUMENT_RESHAPING_DIR_NAME = "07_cleanup"
DOCUMENT_RESHAPING_FILE = "report.md"
# The pass a run asked phase 7 for and the person's own wishes for it, written by `run --document-pass` or the page's
# Change this document menu: {"pass": "cleanup" | "rewrite" | "knowledge", "wishes": "..."}. No file: phase 7 is off.
RUN_DOCUMENT_PASS_FILE = "run-document-pass.json"
DOCUMENT_PASS_CLEANUP = "cleanup"
DOCUMENT_PASS_REWRITE = "rewrite"
# the business facts written into one section per subject, in one pass over the document
DOCUMENT_PASS_KNOWLEDGE = "knowledge"
DOCUMENT_PASSES = (DOCUMENT_PASS_CLEANUP, DOCUMENT_PASS_REWRITE, DOCUMENT_PASS_KNOWLEDGE)

# REQUIREMENTS.md R16: phase 5's folder, and its report. The report is written last, so it is the phase's file; a copy
# goes beside the published document, which links it.
REVIEW_DIR_NAME = "05_review"
# Inside REVIEW_DIR_NAME: the image files of figures the review's audit took out of the published document, kept so a
# reset to phase 4 puts them back
REVIEW_REMOVED_FIGURES_DIR = "removed-figures"
REVIEW_REPORT_FILE = "review.md"
# Every call of phase 5's conversation. Its checks read which tools a follow-up called from it.
REVIEW_CONVERSATION_LOG = f"{REVIEW_DIR_NAME}/logs/conversation.jsonl"
# What the workers of phases 4 and 5 reported through `report_problem`: a check that rejected a right attempt, an unclear
# instruction, a missing tool. One file for the whole run, kept by a reset (`services/worker_feedback_service.py`).
WORKER_FEEDBACK_FILE = "worker-feedback.md"
# The answers to those reports, one line each, written beside them by whoever watches the run (never by the run):
# `- <the report's When> · times <its count> · <the decision>`. The page shows a report answered or open from it, and a
# hook of the watching session refuses to end a turn while a report has no answer (`worker_feedback_service`).
WORKER_FEEDBACK_ANSWERS_FILE = "worker-feedback-answers.md"
# Every paragraph of the document, with the merged entries it rests on. Beside the markdown,
# because the markdown itself carries no timestamp and no frame number.
DRAFT_PROVENANCE_FILE = "04_document.provenance.json"
RUN_STATE_FILE = "run.json"
# The tags given to a run, from the detail view, `i2k tags` or `run --tag`: phase 2 picks the ingesting profile
# from them, and phases 3 to 5 are handed them as a hint of what the recording is (`services/run_tags_service.py`).
RUN_TAGS_FILE = "run-tags.json"
# The name given to a run in place of its file name (its AI name, from the Manager's Rename); no phase reads it
# and no file is renamed by it (`services/run_name_service.py`).
RUN_NAME_FILE = "run-name.json"
# The ingesting profile the run's document is written in, and who chose it: written at phase 2 from the tags (or by
# phase 2's model call for an audio recording, or by a conversion), read by phases 4 and 5
# (`services/ingesting_profile_service.py`).
INGESTING_PROFILE_FILE = "ingesting-profile.json"
# Phase 2's file for an audio recording: the transcript as paragraphs of clean speech, each one speaker's turn on one
# point (`utils/speech_markdown_utils.py`). A video's run has none.
SPEECH_FILE = "02_speech.md"
# The call log slug of phase 2's one call for an audio recording
SPEECH_SLUG = "speech-structuring"
# The call log slug of phase 4's one call of a takeaway run
TAKEAWAY_SLUG = "takeaway"
# `02_frames.json`'s `medium` for an audio recording: no frame is taken, and the empty item list is right
AUDIO_MEDIUM = "audio"
VIDEO_MEDIUM = "video"
# A PDF read as a recording, each page a frame and its text what is said
PDF_MEDIUM = "pdf"
# A PDF page's place on the timeline the document does not have: page 3 is minute 2
PDF_PAGE_SECONDS = 60.0
# A PDF read as its text only: no frame, phase 2's paragraphs written by the
# program from phase 1's text, and phases 4 to 6 as for an audio recording
PDF_TEXT_ONLY_MEDIUM = "pdf-text-only"
# A PDF's markdown conversion: read as text only, every figure placed unread, and the
# document written from phase 1's text by the program; no phase calls a model
PDF_MARKDOWN_CONVERSION_MEDIUM = "pdf-markdown-conversion"
# An item of a PDF read as its text only: one figure cut from its page, its caption said over it
PDF_FIGURE_MEDIUM = "pdf-figure"
# The key of `02_frames.json` holding a PDF's figures placed unread where they stood, never read by phase 3; the
# published document places each before the paragraph its `placeBefore` names
PLACED_FIGURES_KEY = "placedFigures"
# The names a medium had before, read as the medium they became: runs made before the rename hold them
RETIRED_MEDIUMS = {"pdf-markdown": PDF_TEXT_ONLY_MEDIUM, "pdf-no-model": PDF_MARKDOWN_CONVERSION_MEDIUM}
# A PDF read as its text only, under its name now or before
PDF_TEXT_ONLY_MEDIUMS = (PDF_TEXT_ONLY_MEDIUM, PDF_MARKDOWN_CONVERSION_MEDIUM, *RETIRED_MEDIUMS)
# The media with no frame, whose empty item list is right
FRAMELESS_MEDIUMS = (AUDIO_MEDIUM, *PDF_TEXT_ONLY_MEDIUMS)
# How a PDF is read, as the person picks it (`run --pdf-mode`): each page shown to the model as a picture, or its text
# only. The owner named them by what the model reads ("pages" and "markdown" before)
PDF_MODE_PAGES = "pages-as-images"
PDF_MODE_TEXT = "text-only"
# The markdown conversion is named by what it gives, since no model reads anything ("no model" before, the owner: a
# bad name)
PDF_MODE_MARKDOWN_CONVERSION = "markdown-conversion"
PDF_MODES = (PDF_MODE_PAGES, PDF_MODE_TEXT, PDF_MODE_MARKDOWN_CONVERSION)
# The medium each mode chooses before phase 1; pages as images chooses none, the recording's suffix deciding
PDF_MODE_MEDIUMS = {PDF_MODE_TEXT: PDF_TEXT_ONLY_MEDIUM, PDF_MODE_MARKDOWN_CONVERSION: PDF_MARKDOWN_CONVERSION_MEDIUM}
# Every line, block and picture text phase 1 took out of a PDF's pages, with its page and the rule that took it
PDF_CLEANUP_FILE = "01_cleanup.json"
# The workflows a run can take, as the person chooses one: a video as audio (`run --as-audio`), a PDF as its text only
# (`run --pdf-mode text-only`)
RUN_MEDIUMS = (VIDEO_MEDIUM, AUDIO_MEDIUM, *PDF_TEXT_ONLY_MEDIUMS)
# The medium the person chose for the run, written before phase 1: a video processed as audio takes the audio workflow,
# as if it had no picture (`services/recording_medium_service.py`). No file: the recording's suffix decides.
RUN_MEDIUM_FILE = "run-medium.json"
# The part of the input the run processes, a time window or a span of pages, written before phase 1 by `run --start/--end`
# or `run --pages` (`services/input_range_service.py`). No file: the whole input.
RUN_INPUT_RANGE_FILE = "run-input-range.json"
# The engine a PDF run's phase 1 reads its scanned pages with (`tesseract` or `unlimited-ocr`), written before phase 1
# by `run --pdf-ocr-engine` (`services/pdf_source_service.py`). No file: `pdf.ocrEngine` decides.
RUN_PDF_OCR_ENGINE_FILE = "run-pdf-ocr-engine.json"
# The model a run's phase 2 drops frames with no knowledge by (`google-SigLIP-2` or `liquidai-d1-2B`), written before
# phase 1 by `run --knowledge-filter-model` (`services/decision_filter_service.py`). No file:
# `frames.knowledgeFilterModel` decides.
RUN_KNOWLEDGE_FILTER_FILE = "run-knowledge-filter.json"
# The video workflow the run takes (quality or fast-processing), written before phase 1 by the person's choice
# (`services/video_workflow_service.py`). No file: `video.workflow` decides.
RUN_WORKFLOW_FILE = "run-workflow.json"
# The Workflow speed chosen for the run, its preset and the values it sets, written when a job names one and laid over
# the settings at every start of the run (`services/run_speed_service.py`). No file: the settings decide.
RUN_SPEED_FILE = "run-speed.json"
# The worker set the run was started on (`{"name": "<name>"}`), written once nothing can refuse the run and laid over the
# settings at every start of the run (`services/run_worker_set_service.py`). No file: the settings'
# `defaultWorkerSet` decides.
RUN_WORKER_SET_FILE = "run-worker-set.json"
# A fast-processing run's phase 3: one section per frame, its number, time, speech and the description its worker
# submitted, appended as each frame is described; phase 4 reads it whole
FRAME_DESCRIPTIONS_FILE = "03_frame-descriptions.md"
# The call log slug of a fast-processing run's phase 3 frame notes (`fast-note-<frame>`) and of its phase 4 call
FAST_NOTE_SLUG_PREFIX = "fast-note-"
FAST_DOCUMENT_SLUG = "fast-document"
# A fast-processing run's turns, one file per phase 3 session (`fast-notes-000-004.jsonl`) and one for phase 4's call
# (`fast-document.jsonl`), under the run folder's logs: each model call's thinking, words and tool call, for the page's
# worker panels (`services/fast_turn_service.py`)
FAST_TURNS_DIR = "fast-turns"
# A direct-draft run's phase 3: the draft document, one marker line and one text per batch of frames, appended as each
# batch is written; phase 4 reads it whole (`services/direct_draft_batch_service.py`)
DRAFT_FILE = "03_draft.md"
# What a direct-draft run's phase 4 call submitted (title, summary, agenda, heading changes), under the run folder's
# `04_writing/`: a resume builds the document from it and asks nothing again (`services/direct_draft_outline_service.py`).
# The call's slug and turns are the fast-processing document call's (`FAST_DOCUMENT_SLUG`), so the page's panel shows them
DIRECT_DRAFT_OUTLINE_FILE = "direct-draft-outline.json"
# Phase 3's folder for an audio recording whose ingesting profile declares preparation steps: one markdown file per
# call, `<step>.md` for a step over the whole speech and `<step>/NN.md` for each item of an earlier step
# (`services/prepared_file_service.py`)
PREPARED_DIR = "03_prepared"
# The slug every call preparing an audio recording's notes starts with: kept here, where nothing is imported, since
# the reset names it and the reset's module is imported while the preparation's is still loading
PREPARED_CALL_SLUG_PREFIX = "prepared"
# What a prepared section holds when the recording says nothing about it: the one text that needs no paragraph ids
PREPARED_NOT_SAID = "Not said."

# THE NUMBERED PHASE FILES. number -> the file that phase writes, and the node PHASE that writes it. The merge runs in
# memory inside the writing phase (`services/integration_service.py`), and the phases are numbered 1 to 6 with no gap.
# A phase reads the file of the phase before it FROM DISK, which is what lets `--phase N` run one
# phase alone against files another run produced. `services/phase_file_service.py` is the only
# module that turns a number into a path.
TRANSCRIBE_PHASE = "transcribe"
FRAMES_PREPARING_PHASE = "frames_preparing"
FRAMES_PROCESSING_PHASE = "frames_processing"
FINALIZE_PHASE = "finalize"
REVIEW_PHASE = "review"
CRITIQUE_COLLECTION_PHASE = "critique_collection"
DOCUMENT_RESHAPING_PHASE = "document_reshaping"
# Phase 7's name in a run made before it was renamed document reshaping
RETIRED_DOCUMENT_RESHAPING_PHASE = "document_cleanup"
PHASE_FILES = {
    1: {"file": TRANSCRIPT_FILE, "phase": TRANSCRIBE_PHASE},
    2: {"file": LOOP_ITEMS_FILE, "phase": FRAMES_PREPARING_PHASE},
    # Phase 3 writes each frame's notes into that frame's item of phase 2's file (`services/frame_records_service.py`).
    3: {"file": LOOP_ITEMS_FILE, "phase": FRAMES_PROCESSING_PHASE},
    4: {"file": SUMMARY_FILE, "phase": FINALIZE_PHASE},
    5: {"file": f"{REVIEW_DIR_NAME}/{REVIEW_REPORT_FILE}", "phase": REVIEW_PHASE},
    6: {"file": f"{CRITIQUE_COLLECTION_DIR_NAME}/{CRITIQUE_COLLECTION_FILE}", "phase": CRITIQUE_COLLECTION_PHASE},
    # Off unless the run asked for a clean up, a rewrite or a knowledge reshaping (RUN_DOCUMENT_PASS_FILE); a run made
    # before the rename has it in the retired folder, which `phase_file_service` finds
    7: {"file": f"{DOCUMENT_RESHAPING_DIR_NAME}/{DOCUMENT_RESHAPING_FILE}", "phase": DOCUMENT_RESHAPING_PHASE},
}
PHASE_NUMBERS = {row["phase"]: number for number, row in PHASE_FILES.items()}
# The last phase the READER's document depends on. Phase 6 writes rules for this program's own instructions, not part
# of the meeting's output, and it is switchable, so a run whose phase 6 is held or off is still a finished run: what
# "done" means is every phase up to here complete (`services/run_recording_service.is_run_done`).
LAST_DOCUMENT_PHASE = PHASE_NUMBERS[REVIEW_PHASE]
# The critics an audio recording's flow leaves out: the review and the critique collection, which learns from phase 3's
# critiques that an audio recording never writes. Each is recorded "off", its card is not shown, and the run is done
# once phase 4 is.
AUDIO_OFF_PHASES = (PHASE_NUMBERS[REVIEW_PHASE], PHASE_NUMBERS[CRITIQUE_COLLECTION_PHASE])
# A PDF's markdown conversion reads nothing in phase 3 either, so the page shows no card for it
MARKDOWN_CONVERSION_OFF_PHASES = (PHASE_NUMBERS[FRAMES_PROCESSING_PHASE], *AUDIO_OFF_PHASES)
# Names a run written before the rename used for a phase or a kind of step -> the name now. A run record, its attempts
# and its performance log are read through this, so an earlier run resumes and shows its times under the new names.
RETIRED_STEP_NAMES = {"enumerate": FRAMES_PREPARING_PHASE, "work": FRAMES_PROCESSING_PHASE, "work_group": "frame_batch",
                      "work_record": "frame_record", RETIRED_CRITIQUE_COLLECTION_SLUG: CRITIQUE_COLLECTION_PHASE,
                      RETIRED_DOCUMENT_RESHAPING_PHASE: DOCUMENT_RESHAPING_PHASE}

ITEM_STATUSES = ("pending", "analyzed", "written", "skipped", "blocked")
# The statuses after which an item is not read again
TERMINAL_ITEM_STATUSES = ("written", "skipped")
# The fields phase 3 writes onto a frame's item in the run record (process_frames_node), forgotten when phase 3 is
# taken back: a new field written there is added here
PHASE_3_ITEM_FIELDS = ("status", "attempts", "lastRefusal", "blockedReason", "blockedFindings", "answeredIn",
                       "additionsFrom")
# A phase 4 or 5 follow-up's status in its record, read by the resume, the detail view and `i2k blocks`
FOLLOW_UP_ACCEPTED = "accepted"
FOLLOW_UP_BLOCKED = "blocked"
FOLLOW_UP_WAITING = "waiting"

LOOP_ITEMS_SCHEMA_VERSION = 1
RUN_STATE_SCHEMA_VERSION = 1

# The document, REQUIREMENTS.md R3. What it may never contain, stated once: the
# instruction tells the writer, and `validators/document_validator.py` checks the file.
DOCUMENT_BANNED_PHRASES = ("frame", "video shows", "the segment", "on screen")
# `1:23`, `01:23` and `1:02:33`, minutes and seconds below 60. A clock time in the document is provenance that leaked;
# `80:80` is not one.
DOCUMENT_TIMESTAMP_PATTERN = r"\b\d{1,2}:[0-5]\d(?::[0-5]\d)?\b"
# The closing section, present in every document (REQUIREMENTS.md R9): the open points always, the decisions and
# the action items only when the meeting stated any (an empty list adds nothing for a reader). Its title
# names the lists it holds; with the open points alone it is "## Open points" with no list heading under it.
DECISIONS_HEADING = "Decisions"
ACTION_ITEMS_HEADING = "Action items"
OPEN_POINTS_HEADING = "Open points"
# The closing section's title names the lists it holds; with the open points alone it is `OPEN_POINTS_HEADING`
CLOSING_HEADING_ALL = "Decisions, action items and open points"
CLOSING_HEADING_DECISIONS = "Decisions and open points"
CLOSING_HEADING_ACTIONS = "Action items and open points"
# The one line of the open points when nothing was left open; a question the review adds replaces it (R16).
NOTHING_OPEN = "Nothing was left open."
# The two sections above the first chapter, REQUIREMENTS.md R11, and the agenda's line for what was announced and
# never reached. With the closing, they are the only text a claim may be said in a second time.
EXECUTIVE_SUMMARY_HEADING = "Executive summary"
AGENDA_HEADING = "Agenda"
CONTENTS_HEADING = "Contents"
ANNOUNCED_NOT_COVERED = "Announced, not covered"

# Why a cut was set aside (`discarded`): `undo_cut` undid it, or `i2k reset-chapter` set it aside; only an undone cut
# counts toward a frame's undos
UNDO_DISCARD_REASON = "undone"
RESET_DISCARD_REASON = "reset"

# The ONLY values an instruction, contract or template may interpolate. An unknown placeholder
# raises in prompts/system_prompt_builder.py, so a misspelling fails loudly.
PROMPT_PLACEHOLDERS = {
    # Each also in its plural and in any use: a worker read "frame" as allowing "frames" as a verb
    "DOCUMENT_BANNED_PHRASES": ", ".join(f'"{phrase}"' for phrase in DOCUMENT_BANNED_PHRASES)
                               + " (each also in its plural and in any use, a verb included; the subject's own term, a stack or data frame or a "
                               "label quoted from a tool's screen in backticks or double quotes, may stay)",
    "PREPARED_NOT_SAID": PREPARED_NOT_SAID,
}
