"""What each numbered phase does, in words a person reads in the UI's phase help. `FLOW.md` is the full account; this
is its short form, one entry per phase number of `output_contract.PHASE_FILES`. An audio recording runs the same six
phases, two of them as other work: its own titles and guides replace the video's where they
differ."""

from __future__ import annotations

from models import output_contract

__all__ = ["PHASE_GUIDES", "PHASE_TITLES", "KNOWLEDGE_PASS_SUMMARY","AUDIO_PHASE_TITLES", "AUDIO_PHASE_GUIDES", "PDF_PHASE_TITLES",
           "PDF_TEXT_ONLY_PHASE_TITLES", "PDF_PHASE_GUIDES", "PDF_TEXT_ONLY_PHASE_GUIDES", "PDF_MARKDOWN_CONVERSION_PHASE_TITLES",
           "PDF_MARKDOWN_CONVERSION_PHASE_GUIDES"]

PHASE_GUIDES = {
    1: {"summary": "Turns the speech of the recording into a timed transcript, and tells the speakers apart by their "
                   "voices when speaker diarization is on.",
        "reads": "the video",
        "writes": f"{output_contract.TRANSCRIPT_FILE}, {output_contract.AUDIO_FILE}",
        "is_model_used": False},
    2: {"summary": "Picks the frames in four stages. Rules: a frame each time the shared screen changes, cropped to "
                   "the shared screen (the faces panel and the bars around it cut away). Classifier: drops the frames "
                   "that show only people. Redundancy model: a small vision model drops the frames another kept frame "
                   "already shows whole, a few neighbouring frames at a time. Similarity recheck: frames that look alike "
                   "but were never shown together are shown to the same model again, side by side.",
        "reads": "the video",
        "writes": (f"{output_contract.FRAME_IMAGES_DIR_NAME}/*.jpg, {output_contract.FRAME_IMAGES_DIR_NAME}/dropped/, "
                   f"{output_contract.LOOP_ITEMS_FILE}"),
        "is_model_used": True},
    3: {"summary": "A vision model reads the frames in batches with the words spoken over them, and writes each frame's "
                   "notes: take aways, facts, a diagram, an outline of the screen, tags. Each batch then gets a second "
                   "look, its open questions and their answers.",
        "reads": f"{output_contract.LOOP_ITEMS_FILE}, the frames, {output_contract.TRANSCRIPT_FILE}",
        "writes": (f"each frame's notes in {output_contract.LOOP_ITEMS_FILE}, {output_contract.ITEMS_DIR_NAME}/, "
                   f"{output_contract.BATCH_SUMMARIES_FILE}, {output_contract.NOTES_FILE}"),
        "is_model_used": True},
    4: {"summary": "One model conversation writes the document from the notes: an executive summary draft, the agenda, "
                   "the headings of each chapter, each chapter's text with its frames, then the final summary and title. "
                   "The program then publishes the folder a reader opens.",
        "reads": "the notes, the speech, the frames and the video",
        "writes": f"{output_contract.SUMMARY_FILE}, {output_contract.WRITING_DIR_NAME}/ (the draft, requests, sections), "
                  "<meeting name>/<meeting name>.md with its figures",
        "is_model_used": True},
    5: {"summary": "A critic that did not write the document lists what a reader still cannot know, answers each point "
                   "from the transcript, the notes and the frames, and edits the published document. It waits (held) "
                   "while an earlier phase or a frame is blocked.",
        "reads": "the published document, its figures, the notes, the speech and the frames",
        "writes": (f"{output_contract.REVIEW_DIR_NAME}/before.md, {output_contract.REVIEW_DIR_NAME}/"
                   f"{output_contract.REVIEW_REPORT_FILE}, edits in the published document"),
        "is_model_used": True},
    6: {"summary": "Collects every batch's critique of this run into one set of anti-pattern rules: the program groups "
                   "the rules that repeat and counts the batches and frames each hit, and one model call words each "
                   "group as a rule for any meeting. It waits (held) while an earlier phase or a frame is blocked.",
        "reads": f"{output_contract.ITEMS_DIR_NAME}/*-critique.md",
        "writes": f"{output_contract.CRITIQUE_COLLECTION_DIR_NAME}/{output_contract.CRITIQUE_COLLECTION_FILE}",
        "is_model_used": True},
    7: {"summary": "Off unless the run asked for it. A model reshapes the published document: cleans it up (takes "
                   "out what the export left that a reader does not need), rewrites it (cleans up and improves its "
                   "structure and writing), or reshapes it into a knowledge-focused document, in parts of at least "
                   "30,000 tokens (the setting documentReshaping.batchTokens), following the wishes given with the "
                   "run.",
        "reads": "the published document",
        "writes": (f"{output_contract.DOCUMENT_RESHAPING_DIR_NAME}/before.md, "
                   f"{output_contract.DOCUMENT_RESHAPING_DIR_NAME}/{output_contract.DOCUMENT_RESHAPING_FILE}, edits in "
                   "the published document"),
        "is_model_used": True},
}

# The title a phase card shows in every workflow, where it is not the phase's own name with spaces: phase 4 writes the
# document, which "finalize" did not say
PHASE_TITLES = {4: "document writing", 7: "final document reshaping"}
# Phase 7's help for a knowledge pass, which writes a new document where the other two edit the one there
KNOWLEDGE_PASS_SUMMARY = ("A model reads the published document part by part and writes a new document of the business "
                          "facts, one section per subject, following the wishes given with the run.")
# The title a phase card shows for an audio recording, where it is not the phase's own name with spaces
AUDIO_PHASE_TITLES = {2: "speech preparing", 3: "notes preparing"}
# A PDF's phases 2 and 3 by what they do with a document: read as pages, its pages are the frames; read as text only,
# phase 2 writes its paragraphs and cuts its figures and phase 3 reads the figures (the owner: "what is frames in pdf?")
PDF_PHASE_TITLES = {1: "text reading", 2: "pages preparing", 3: "pages reading"}
PDF_TEXT_ONLY_PHASE_TITLES = {1: "text reading", 2: "paragraphs and figures preparing", 3: "figures reading"}

AUDIO_PHASE_GUIDES = {
    1: {"reads": "the audio recording"},
    2: {"summary": "One model call turns the transcript into clean paragraphs, each one speaker's turn on one point, and "
                   "picks the ingesting profile when no tag names one.",
        "reads": f"{output_contract.TRANSCRIPT_FILE}, the run's tags",
        "writes": (f"{output_contract.SPEECH_FILE}, {output_contract.LOOP_ITEMS_FILE} with no frame, "
                   f"{output_contract.INGESTING_PROFILE_FILE}"),
        "is_model_used": True},
    3: {"summary": "Prepares the notes in steps when the ingesting profile declares them: each step lists or details the "
                   "items the paragraphs hold. A profile that declares no step has nothing to prepare.",
        "reads": f"{output_contract.SPEECH_FILE}, the profile's steps in agent-context/preprocessing/",
        "writes": f"{output_contract.PREPARED_DIR}/",
        "is_model_used": True},
    4: {"summary": "One model conversation writes the document from the speech paragraphs, or from the prepared notes: "
                   "an executive summary draft, the agenda, the headings of each chapter, each chapter's text, then the "
                   "final summary and title. The program then publishes the folder a reader opens.",
        "reads": f"{output_contract.SPEECH_FILE}, {output_contract.PREPARED_DIR}/, {output_contract.TRANSCRIPT_FILE}",
        "writes": (f"{output_contract.SUMMARY_FILE}, {output_contract.WRITING_DIR_NAME}/ (the draft, requests, sections), "
                   "<meeting name>/<meeting name>.md")},
    5: {"summary": "A critic that did not write the document lists what a reader still cannot know, answers each point "
                   "from the transcript and the speech paragraphs, and edits the published document. It waits (held) "
                   "while an earlier phase is blocked.",
        "reads": "the published document, the speech paragraphs and the transcript"},
    6: {"summary": "Collects the critiques of phase 3's frame batches into anti-pattern rules. An audio recording has no "
                   "frames, so it has no critique to collect."},
}

# A PDF's phase cards say what each phase does with a document: told the audio or video help, a PDF's detail view said
# its phase 1 "tells the speakers apart" (the owner: "there are no speaker in pdf")
_PDF_DOCUMENT_PHASES = {
    1: {"summary": "Reads the document's text page by page, and takes out what a reader does not need: the lines and "
                   "blocks repeated on most pages, the export's chrome, its contents and its back matter.",
        "reads": "the PDF",
        "writes": f"{output_contract.TRANSCRIPT_FILE}, {output_contract.PDF_CLEANUP_FILE}",
        "is_model_used": False},
    4: {"summary": "One model conversation writes the document from the notes: an executive summary draft, the "
                   "agenda from the document's own headings, the headings of each chapter, each chapter's text with "
                   "its figures, then the final summary and title. The program then publishes the folder a reader opens.",
        "reads": "the notes, the paragraphs and the figures",
        "writes": f"{output_contract.SUMMARY_FILE}, {output_contract.WRITING_DIR_NAME}/ (the draft, requests, sections), "
                  "<document name>/<document name>.md with its figures"},
    5: {"summary": "A critic that did not write the document lists what a reader still cannot know, answers each point "
                   "from the document's text and notes, and edits the published document.",
        "reads": "the published document, its figures and the notes"},
    6: {"summary": "Collects the critiques of phase 3's batches into anti-pattern rules for any document."},
}
PDF_PHASE_GUIDES = {
    **_PDF_DOCUMENT_PHASES,
    2: {"summary": "Renders every page as an image, none dropped, each given its page's text.",
        "reads": f"the PDF, {output_contract.TRANSCRIPT_FILE}",
        "writes": f"{output_contract.FRAME_IMAGES_DIR_NAME}/*.jpg, {output_contract.LOOP_ITEMS_FILE}",
        "is_model_used": False},
    3: {"summary": "A vision model reads the pages in batches with their text, and writes each page's notes: facts, "
                   "a diagram, an outline of the page, its tables as markdown, tags.",
        "reads": f"{output_contract.LOOP_ITEMS_FILE}, the page images, {output_contract.TRANSCRIPT_FILE}",
        "writes": f"each page's notes in {output_contract.LOOP_ITEMS_FILE}, {output_contract.ITEMS_DIR_NAME}/, "
                  f"{output_contract.NOTES_FILE}",
        "is_model_used": True},
}
PDF_TEXT_ONLY_PHASE_GUIDES = {
    **_PDF_DOCUMENT_PHASES,
    2: {"summary": "Writes each block of the document's text as a paragraph, and cuts each figure from its page as an "
                   "image with its caption, with no model call.",
        "reads": f"{output_contract.TRANSCRIPT_FILE}, the PDF",
        "writes": (f"{output_contract.SPEECH_FILE}, {output_contract.FRAME_IMAGES_DIR_NAME}/*.jpg, "
                   f"{output_contract.LOOP_ITEMS_FILE}"),
        "is_model_used": False},
    3: {"summary": "A vision model reads the figures with their captions and writes each one's notes: what it shows, "
                   "a flow as Mermaid, code or a table as it stands, and whether a reader needs the picture itself.",
        "reads": f"{output_contract.LOOP_ITEMS_FILE}, the figure images",
        "writes": f"each figure's notes in {output_contract.LOOP_ITEMS_FILE}, {output_contract.ITEMS_DIR_NAME}/",
        "is_model_used": True},
}
# A PDF's markdown conversion: no phase calls a model, and the document is the PDF's
# own text
PDF_MARKDOWN_CONVERSION_PHASE_TITLES = {**PDF_TEXT_ONLY_PHASE_TITLES, 3: "nothing to read", 4: "markdown conversion"}
PDF_MARKDOWN_CONVERSION_PHASE_GUIDES = {
    **PDF_TEXT_ONLY_PHASE_GUIDES,
    3: {"summary": "Nothing is read: no figure goes to a model.", "reads": "nothing", "writes": "nothing",
        "is_model_used": False},
    4: {"summary": "The program writes the document from the paragraphs as they stand, each figure where it stood in "
                   "the PDF, and publishes the folder a reader opens, with no model call.",
        "reads": f"{output_contract.SPEECH_FILE}, the figures in {output_contract.LOOP_ITEMS_FILE}",
        "writes": f"{output_contract.SUMMARY_FILE}, <document name>/<document name>.md with its figures",
        "is_model_used": False},
}
