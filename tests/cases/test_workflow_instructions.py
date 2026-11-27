"""Each workflow has its own instruction file for a step, and text several workflows share is a guideline each file
includes, which may itself include another."""

import json
import tempfile
from pathlib import Path

from support import harness

from models.run_errors import PromptBuildError
from prompts import system_prompt_builder

# An invented step sent by two workflows: each its own file, both including the shared guideline
_FILES = {
    "context_map.json": json.dumps({"phases": {"write": {
        "instruction": "_shared/write.md", "workflows": {"video": "video/write.md", "audio": "audio/write.md"},
        "base": [], "contracts": [], "templates": []}}}),
    "instructions/video/write.md": "# Video\n\n{{SHARED:write}}\n",
    "instructions/audio/write.md": "{{SHARED:write}}\n\nWrite the AcmeSync speaker's own words.\n",
    "instructions/_shared/write.md": "Name the Gateway's retries.\n\n{{SHARED:known-terms}}\n",
    "instructions/_shared/known-terms.md": "- **Gateway**: the payment gateway.\n",
    "instructions/_shared/loop-a.md": "{{SHARED:loop-b}}\n",
    "instructions/_shared/loop-b.md": "{{SHARED:loop-a}}\n",
}


def _build_root() -> Path:
    root = Path(tempfile.mkdtemp()) / "agent-context"
    for relative, text in _FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _find_error(phase: str,
                item: dict,
                root: Path) -> str:
    try:
        system_prompt_builder.build_system_prompt(phase, item, root)
    except PromptBuildError as error:
        return str(error)
    return ""


def test_each_workflow_gets_its_own_file_with_the_shared_guideline_inside():
    root = _build_root()
    audio = system_prompt_builder.build_system_prompt("write", {"workflow": "audio"}, root)
    harness.check("the audio workflow's file, its guideline and the guideline the guideline includes",
                  "Write the AcmeSync speaker's own words." in audio and "Name the Gateway's retries." in audio
                  and "**Gateway**" in audio and "{{" not in audio, audio)
    video = system_prompt_builder.build_system_prompt("write", {"workflow": "video"}, root)
    harness.check("the video workflow's file holds the guideline and not the audio rule",
                  video.startswith("# Video") and "Name the Gateway's retries." in video
                  and "AcmeSync speaker" not in video, video)


def test_a_call_naming_no_workflow_takes_the_run_s_own():
    root = _build_root()
    original = system_prompt_builder.RUN_WORKFLOW
    try:
        system_prompt_builder.set_run_workflow("audio")
        harness.check("the run's workflow picks the file", "AcmeSync speaker" in system_prompt_builder.build_system_prompt(
            "write", {}, root))
        system_prompt_builder.set_run_workflow("")
        shared = system_prompt_builder.build_system_prompt("write", {}, root)
        harness.check("with no workflow, as a process started on code that knows no map, the step's shared text is sent",
                      shared.startswith("Name the Gateway's retries.") and "**Gateway**" in shared, shared)
    finally:
        system_prompt_builder.set_run_workflow(original)
    pdf = system_prompt_builder.build_system_prompt("write", {"workflow": "pdf"}, root)
    harness.check("a workflow with no file of its own gets the step's shared text", pdf == shared, pdf)


def test_guidelines_that_include_each_other_are_refused():
    root = _build_root()
    (root / "instructions/video/write.md").write_text("{{SHARED:loop-a}}\n", encoding="utf-8")
    error = _find_error("write", {"workflow": "video"}, root)
    harness.check("a guideline including itself through another is refused, never sent", "loop-" in error, error)
