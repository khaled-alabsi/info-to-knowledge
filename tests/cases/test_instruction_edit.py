"""A person's instruction edit from the page is saved only when every worker's message can still be built from it, and
the page's tree names each workflow's steps and the files each is built from."""

import time
from contextlib import contextmanager
from pathlib import Path
import tempfile

from support import harness

import config
from models import instruction_catalogue_vocabulary as vocabulary
from models.instruction_errors import InstructionRefusedError
from prompts import system_prompt_builder
from services import instruction_catalogue_service
from services import instruction_edit_service
from services import instruction_store_service

TAKEAWAY = "instructions/audio/takeaway.md"
CHAPTER_SUMMARY = "compaction/conversation/chapter/summary.md"
ADDED_RULE = "Write the Gateway's retry count as AcmeSync states it."


@contextmanager
def _own_store():
    original = instruction_store_service.STORE_DIR
    with tempfile.TemporaryDirectory() as folder:
        instruction_store_service.set_store_dir(Path(folder) / "instructions")
        try:
            yield
        finally:
            instruction_store_service.set_store_dir(original)


def _find_refusal(file_id: str,
                  text: str) -> str:
    """The refusal's words, or "" when the edit was saved."""

    try:
        instruction_edit_service.save_instruction(file_id, text, account="jonas")
    except InstructionRefusedError as error:
        return str(error)
    return ""


def test_a_compaction_edit_keeps_every_placeholder_the_program_fills():
    with _own_store():
        default = instruction_store_service.read_default(CHAPTER_SUMMARY)
        dropped = _find_refusal(CHAPTER_SUMMARY, default.replace("{messages}", "the history"))
        harness.check("dropping {messages} is refused, naming it", "{messages}" in dropped, dropped)
        unknown = _find_refusal(CHAPTER_SUMMARY, default + "\nName {the_gateway} too.\n")
        harness.check("a placeholder the program does not fill is refused", "{the_gateway}" in unknown, unknown)
        brace = _find_refusal(CHAPTER_SUMMARY, default + '\nWrite it as {"retries": 3.\n')
        harness.check("a single brace is refused with how to write one", "{{ or }}" in brace, brace)
        harness.check("nothing refused was saved", not instruction_store_service.is_edited(CHAPTER_SUMMARY))
        harness.check("a sentence added is saved", _find_refusal(CHAPTER_SUMMARY, default + f"\n{ADDED_RULE}\n") == ""
                      and ADDED_RULE in instruction_store_service.read_active(CHAPTER_SUMMARY))


def test_an_instruction_edit_is_refused_only_when_a_prompt_cannot_be_built_from_it():
    with _own_store():
        takeaway = instruction_store_service.read_default(TAKEAWAY)
        missing = _find_refusal(TAKEAWAY, takeaway + "\n{{SHARED:gateway-rules}}\n")
        harness.check("an include naming no guideline is refused, naming it", "gateway-rules" in missing, missing)
        write = instruction_store_service.read_default("instructions/_shared/write.md")
        anchor = _find_refusal("instructions/_shared/write.md", write.replace("{{INJECT:base}}", ""))
        harness.check("dropping the anchor the profile rules go at is refused", "{{INJECT:base}}" in anchor, anchor)
        frames = instruction_store_service.read_default("instructions/_shared/frames-processing.md")
        sample = _find_refusal("instructions/_shared/frames-processing.md", frames.replace("~~~markdown", "~~~text"))
        video_write = _find_refusal("instructions/video/write.md", "Write in the Gateway team's words.\n")
        harness.check("a workflow's own file may replace its shared guideline whole, the anchors it needs then missing",
                      "{{INJECT:base}}" not in video_write, video_write)
        harness.check("phase 3's file losing its sample answer is refused", "~~~markdown" in sample, sample)
        started = time.monotonic()
        saved = _find_refusal(TAKEAWAY, takeaway + f"\n{ADDED_RULE}\n")
        seconds = time.monotonic() - started
        harness.check("a rule added is saved", saved == "" and ADDED_RULE in instruction_store_service.read_active(TAKEAWAY),
                      saved)
        harness.check("the check of a save takes under 5 seconds", seconds < 5, f"{seconds:.1f} s")
        harness.check("the full prompt of the step holds a draft",
                      "Only the draft." in instruction_edit_service.build_preview("takeaway", "audio", TAKEAWAY,
                                                                                  "Only the draft."))
        instruction_edit_service.reset_instruction(TAKEAWAY, account="jonas")
        harness.check("a reset puts the default back", instruction_store_service.read_active(TAKEAWAY) == takeaway)


def _find_step(catalogue: dict,
               workflow: str,
               step: str) -> dict:
    """One step of one workflow in the tree, or an empty one when the workflow does not send it."""

    phases = next(entry for entry in catalogue["workflows"] if entry["key"] == workflow)["phases"]

    return next((found for phase in phases for found in phase["steps"] if found["key"] == step), {})


def test_every_step_of_the_context_map_has_a_title_and_a_workflow_that_sends_it():
    # A step the context map declares that the tree's table leaves out is an instruction the page never shows
    steps = set(system_prompt_builder.list_phase_specifications(config.AGENT_CONTEXT_DIR))
    harness.check("every step has a title and senders, and nothing else is listed",
                  set(vocabulary.STEP_TITLES) == steps == set(vocabulary.STEP_SENDERS),
                  f"{sorted(steps ^ set(vocabulary.STEP_SENDERS))}")
    harness.check("each sender names a workflow and its own profiles or modes",
                  all(set(variants) <= set(vocabulary.WORKFLOW_VARIANTS[workflow])
                      for senders in vocabulary.STEP_SENDERS.values() for workflow, variants in senders.items()))


def test_the_tree_names_each_workflows_steps_and_the_files_each_is_built_from():
    with _own_store():
        catalogue = instruction_catalogue_service.build_catalogue()
        harness.check("the three workflows", [entry["key"] for entry in catalogue["workflows"]] == ["video", "audio", "pdf"])
        critique = _find_step(catalogue, "video", "critique")
        harness.check("the video critique is built from its file, its guidelines and its compaction text",
                      critique.get("files", [])[:2] == ["instructions/video/critique.md", "instructions/_shared/critique.md"]
                      and "instructions/_shared/known-terms.md" in critique["files"]
                      and "compaction/cut-thinking/critique.md" in critique["files"], str(critique.get("files")))
        harness.check("an audio recording has no critique", _find_step(catalogue, "audio", "critique") == {})
        video_frames = _find_step(catalogue, "video", "frames_processing")["files"]
        pdf_frames = _find_step(catalogue, "pdf", "frames_processing")["files"]
        harness.check("the PDF rules go only to the PDF workflow's frame notes",
                      "base/medium-pdf.md" in pdf_frames and "base/medium-pdf.md" not in video_frames, str(video_frames))
        chapter = _find_step(catalogue, "audio", "write")["files"]
        harness.check("the writing conversation lists its compaction prompts and the profile rules",
                      CHAPTER_SUMMARY in chapter and "base/ingesting-profile-meeting.md" in chapter, str(chapter))
        listed = {entry["id"] for entry in catalogue["files"]}
        harness.check("every editable file is in the tree's file list",
                      listed == set(instruction_store_service.list_editable_files()))
        orphans = sorted(entry["id"] for entry in catalogue["files"] if not entry["used_by"])
        harness.check("every editable file is sent by a step", orphans == [], str(orphans))
        instruction_store_service.save(TAKEAWAY, instruction_store_service.read_default(TAKEAWAY) + ADDED_RULE,
                                       account="jonas")
        edited = next(entry for entry in instruction_catalogue_service.build_catalogue()["files"] if entry["id"] == TAKEAWAY)
        harness.check("an edited file is marked edited, with its step's title", edited["is_edited"]
                      and edited["title"] == "Takeaway" and edited["used_by"] == [{"workflow": "audio", "step": "takeaway"}],
                      str(edited))
