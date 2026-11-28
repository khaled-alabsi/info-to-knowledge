"""Which workflow sends which instruction, in which of its profiles or modes, for the page's Instructions view.
The phase a step belongs to and the sentence saying who gets it are
`worker_view_service.INSTRUCTION_PHASE_NUMBERS` and `INSTRUCTION_USED_BY`; this file adds the workflows and the titles.
"""

from __future__ import annotations

from models import output_contract
from models import phase_guide
from models import video_workflow_vocabulary

__all__ = ["VIDEO", "AUDIO", "PDF", "WORKFLOW_TITLES", "WORKFLOW_VARIANTS", "PHASE_TITLES", "VARIANT_PHASE_TITLES", "STEP_TITLES",
           "STEP_SENDERS", "COMPACTION_WORKER_STEPS", "COMPACTION_WORKER_TITLES", "CUT_THINKING_SHARED_FILES",
           "CONVERSATION_SHARED_FILES"]

VIDEO = "video"  # the video workflow
AUDIO = "audio"  # the audio workflow
PDF = "pdf"      # the PDF workflow
WORKFLOW_TITLES = {VIDEO: "Video", AUDIO: "Audio", PDF: "PDF"}
_TAKEAWAY = "takeaway"                      # the audio workflow's fast profile, one call after the transcript
_OTHER_AUDIO_PROFILES = "ingesting-profiles"  # every other ingesting profile of an audio recording
# Each workflow's profiles or modes, in the order the run form lists them, with the names it shows
WORKFLOW_VARIANTS = {
    VIDEO: {video_workflow_vocabulary.QUALITY: "quality", video_workflow_vocabulary.DIRECT_DRAFT: "direct draft",
            video_workflow_vocabulary.FAST_PROCESSING: "fast processing"},
    AUDIO: {_OTHER_AUDIO_PROFILES: "ingesting profiles", _TAKEAWAY: "takeaway"},
    PDF: {output_contract.PDF_MODE_PAGES: "pages as images", output_contract.PDF_MODE_TEXT: "text only",
          output_contract.PDF_MODE_MARKDOWN_CONVERSION: "markdown conversion"},
}
# Each phase that calls a model, by its number, as the page names it (the owner's names); a workflow's own name wins
PHASE_TITLES = {
    VIDEO: {2: "frames preparing", 3: "frames processing", 4: "document writing", 5: "review", 6: "critique collection",
            7: "clean up or rewrite"},
    AUDIO: {2: "speech preparing", 3: "notes preparing", 4: "document writing", 7: "clean up or rewrite"},
    PDF: {3: "pages or figures reading", 4: "document writing", 5: "review", 6: "critique collection",
          7: "clean up, rewrite or knowledge"},
}
# The titles a workflow's variant gives a phase in place of the workflow's own (a direct-draft run's phases 3 and 4)
VARIANT_PHASE_TITLES = {
    VIDEO: {video_workflow_vocabulary.DIRECT_DRAFT: dict(phase_guide.DIRECT_DRAFT_PHASE_TITLES)},
}
# Each step of `agent-context/context_map.json`, as the page names it
STEP_TITLES = {
    "speech_structuring": "Speech into paragraphs", "frame_redundancy": "Redundancy filter",
    "preprocessing": "Preparing the notes", "frames_processing": "Frame notes", "critique": "Critique",
    "second_pass": "Second look", "open_questions": "Open questions", "answer_questions": "Answers to the open questions",
    "fast_frame_note": "Frame description", "takeaway": "Takeaway", "write": "Writing conversation",
    "parts": "Agenda", "draft_headings": "Draft headings", "table_of_contents": "Table of contents",
    "section": "Chapter text", "summary": "Final title, summary and agenda", "contents": "Title", "outline": "Outline",
    "fast_document": "Document", "direct_draft_batch": "Draft batch",
    "direct_draft_outline": "Draft title, summary and pictures", "review": "Review", "critique_collection": "Critique collection",
    "document_cleanup": "Clean up or rewrite", "document_knowledge": "Knowledge", "document_chat": "Chat on the document",
}
_VIDEO_QUALITY = {VIDEO: (video_workflow_vocabulary.QUALITY,)}
_WRITING = {VIDEO: (video_workflow_vocabulary.QUALITY,), AUDIO: (_OTHER_AUDIO_PROFILES,),
            PDF: (output_contract.PDF_MODE_PAGES, output_contract.PDF_MODE_TEXT)}
_PDF_FOLLOW_UPS = (output_contract.PDF_MODE_PAGES, output_contract.PDF_MODE_TEXT)
# Which workflows send each step, in which profiles or modes: a step a setting switches off is still listed
STEP_SENDERS = {
    "speech_structuring": {AUDIO: (_OTHER_AUDIO_PROFILES,)},
    "frame_redundancy": {VIDEO: tuple(video_workflow_vocabulary.WORKFLOWS)},
    "preprocessing": {AUDIO: (_OTHER_AUDIO_PROFILES,)},
    "frames_processing": {**_VIDEO_QUALITY, PDF: _PDF_FOLLOW_UPS},
    "critique": {**_VIDEO_QUALITY, PDF: _PDF_FOLLOW_UPS},
    "second_pass": {**_VIDEO_QUALITY, PDF: _PDF_FOLLOW_UPS},
    "open_questions": {**_VIDEO_QUALITY, PDF: (output_contract.PDF_MODE_PAGES,)},
    "answer_questions": {**_VIDEO_QUALITY, PDF: (output_contract.PDF_MODE_PAGES,)},
    "fast_frame_note": {VIDEO: (video_workflow_vocabulary.FAST_PROCESSING,)},
    "takeaway": {AUDIO: (_TAKEAWAY,)},
    "write": _WRITING, "parts": _WRITING, "draft_headings": _WRITING, "table_of_contents": _WRITING,
    "section": _WRITING, "summary": _WRITING, "contents": _WRITING, "outline": _WRITING,
    "fast_document": {VIDEO: (video_workflow_vocabulary.FAST_PROCESSING,)},
    "direct_draft_batch": {VIDEO: (video_workflow_vocabulary.DIRECT_DRAFT,)},
    "direct_draft_outline": {VIDEO: (video_workflow_vocabulary.DIRECT_DRAFT,)},
    "review": {**_VIDEO_QUALITY, PDF: (output_contract.PDF_MODE_PAGES,)},
    "critique_collection": {**_VIDEO_QUALITY, PDF: (output_contract.PDF_MODE_PAGES,)},
    "document_cleanup": {workflow: tuple(variants) for workflow, variants in WORKFLOW_VARIANTS.items()},
    "document_knowledge": {PDF: tuple(WORKFLOW_VARIANTS[PDF])},
    "document_chat": {workflow: tuple(variants) for workflow, variants in WORKFLOW_VARIANTS.items()},
}
# The step whose conversation each compaction worker summarises (`compaction_prompt_loader.WORKER_OF_SUBMIT_TOOL`)
COMPACTION_WORKER_STEPS = {"executive_summary_draft": "write", "parts": "write", "draft_headings": "write",
                           "chapter": "write", "summary": "write", "review_critique": "review",
                           "review_resolution": "review"}
# Each compaction worker as the page names it
COMPACTION_WORKER_TITLES = {"executive_summary_draft": "executive summary draft", "parts": "agenda",
                            "draft_headings": "draft headings", "chapter": "chapter text",
                            "summary": "final title, summary and agenda", "review_critique": "review critique",
                            "review_resolution": "resolving a review point"}
# Phase 3's cut-thinking texts every one of its four follow-ups is sent, beside its own prompt
CUT_THINKING_SHARED_FILES = ("handoff.md", "finish.md")
# The conversation's compaction texts every worker of phases 4 and 5 is sent, beside its own prompt and handoff
CONVERSATION_SHARED_FILES = ("tail-line.md", "write-shorter.md")
