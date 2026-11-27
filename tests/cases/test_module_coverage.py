"""Every module under `src/` is reached by this suite, or says in one line why it is not.

The hole this closes is the one nothing else can see. A suite is judged by what it asserts, and a
module no test ever names asserts nothing - it does not fail, it does not appear in a summary, and it
looks exactly like a module that is covered. Counting tests, lines or assertions will never find it,
because the gap is in what was never written.

`REACHED_THROUGH` is what keeps this honest rather than a rubber stamp. A module in it is not
excused - it is claimed to be exercised through something else, and the claim is written where a
reader can check it. **Every module this playbook adds needs a case file or an entry here**, and an
entry that no longer holds is a failure too: a module listed here that a test now imports directly
must lose its entry, or the map slowly becomes a list of things nobody looked at.

Parsed with `ast`, never grepped: an import inside a docstring or a comment is not an import, and a
coverage check that counts one is the same class of lie it exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

from support.harness import check

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TESTS = ROOT / "tests"

# module -> how it is exercised without any test importing it. One line, checkable by a reader.
REACHED_THROUGH = {
    "models.illustration_action_result":
        "what illustration_action_service.run_action answers every frame tool with; its text checked in test_illustration_actions",
    "models.critique_parse_result":
        "what critique_markdown_utils.parse_critique gives its three callers; checked in test_batch_critique",
    "models.frame_dispositions_result":
        "what document_validator reads of a chapter's left-out and replaced frames; checked in test_chapter_images",
    "models.pending_box_state":
        "the crop box waiting to replace the one in use, from screen_crop_utils.choose_crop_box; checked in "
        "test_change_capture",
    "models.screen_outline_result":
        "what frame_markdown_utils.read_screen_parts gives its three callers; checked in test_frame_markdown",
    "models.text_repair_result":
        "what document_audit_utils.repair_text gives, read by review_conversation_service; checked in test_review_phase",
    "models.worker_feedback_outcome":
        "what record_report gives its two callers, the worker's reply and the console lines; checked in "
        "test_worker_feedback and test_write_follow_up_loop",
    "models.setting_choice_vocabulary":
        "the names of the lists a setting's choices come from, read by settings_service.describe_settings; checked in "
        "test_settings_files (every model selection is offered as a list)",
    "models.speed_setting_vocabulary":
        "the Workflow speed settings and their keys, read by run_speed_service and config's preset check; checked in "
        "test_workflow_speed",
    "models.reading_errors":
        "the reading tools' argument error, raised by reading_tool_utils.read_whole_number and answered in words by "
        "both reading services; checked in test_review_phase and test_write_follow_up_loop",
    "schemas.chapter_draft_edits":
        "the arguments of edit_chapter_draft, read by write_conversation_service; the tool's edits are checked in "
        "test_write_follow_up_loop",
    "utils.asked_questions_utils":
        "the questions another speaker asks over a frame, named in the frame's message by frames_processing_messages; "
        "checked in test_board_frames (DEFECTS 538)",
    "models.worker_feedback_answer":
        "one answer of worker-feedback-answers.md, read by worker_feedback_service for the page and the watching "
        "session's hook; checked in test_worker_feedback",
    "models.worker_feedback_report":
        "one entry of worker-feedback.md, built and read by worker_feedback_service and read by run_status_service; "
        "checked in test_worker_feedback and test_ui_services",
    "utils.upload_name_utils":
        "an uploaded file's kept name, read by the upload, upload bundle and inbox merge services; their numbered names and refusals "
        "are checked in test_duplicate_recordings and test_ui_services",
    "utils.process_utils":
        "whether a process is alive, read by the job queue and the recording compression; their live-job and running "
        "compression checks run in test_ui_services and test_compress_recording",
    "models.chapter_reset_spec":
        "what a chapter reset removes, built and read by chapter_reset_service only; its listing and its reset are "
        "checked in test_chapter_reset",
    "graph.nodes.retry_loop.nodes.capture_node":
        "a node of the chain subgraph; the call log it captures is asserted in test_chain_attempt_loop",
    "graph.nodes.retry_loop.nodes.record_node":
        "a node of the chain subgraph; the store rows it writes are asserted in test_chain_attempt_loop",
    "graph.nodes.retry_loop.nodes.snapshot_node":
        "a node of the chain subgraph; it claims the attempt directory every attempt writes into",
    "graph.nodes.retry_loop.nodes.validate_node":
        "a node of the chain subgraph; every acceptance and rejection goes through it",
    "services.artifact_snapshot_service":
        "snapshot_node takes one on every attempt test_chain_attempt_loop runs",

    # Nothing in the suite drives these. Each one needs a case file: an entry here is a note that it
    # does not have one yet, never a decision that it does not need a test.
    "services.dry_run_service": "driven by the work node's dry run in test_phase_nodes",
}


def _module_names() -> list[str]:
    """Every module under `src/` as a dotted name. A package with an empty `__init__` is not one."""

    names: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        parts = path.relative_to(SRC).with_suffix("").parts
        if any(part.startswith(".") or part == "__pycache__" for part in parts):
            continue
        if parts[-1] == "__init__":
            body = ast.parse(path.read_text(encoding="utf-8")).body
            code = [node for node in body
                    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
            if not code:
                continue                    # a namespace file with nothing in it to test
            parts = parts[:-1]
        if parts:
            names.append(".".join(parts))

    return names


def _imported_by_tests() -> set[str]:
    """Every module name the suite imports, however it spells the import."""

    found: set[str] = set()
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")     # `from services import x`
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)

    return found


def test_no_module_ships_without_a_test_or_a_stated_reason():
    modules = _module_names()
    imported = _imported_by_tests()
    check("there are modules to check", len(modules) > 10, str(len(modules)))

    unreached = [name for name in modules
                 if name not in imported and name not in REACHED_THROUGH]
    check("every module is imported by a test or listed with the reason it is not",
          unreached == [], "\n".join(f"          {name}" for name in unreached))


def test_the_reasons_are_kept_current():
    modules = set(_module_names())
    imported = _imported_by_tests()

    gone = sorted(name for name in REACHED_THROUGH if name not in modules)
    check("no reason is given for a module that no longer exists", gone == [], str(gone))

    now_direct = sorted(name for name in REACHED_THROUGH if name in imported)
    check("a module a test now imports has lost its entry", now_direct == [],
          f"these are tested directly and no longer need an excuse: {now_direct}")

    empty = sorted(name for name, why in REACHED_THROUGH.items() if len(why.strip()) < 20)
    check("every reason says something a reader can check", empty == [], str(empty))
