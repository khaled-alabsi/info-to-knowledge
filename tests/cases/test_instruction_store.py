"""The instructions a person edits on the page: kept in the workspace with every version, the shipped defaults never
written, and the text in force read by every prompt."""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from support import harness

import config
from models.instruction_errors import UnknownInstructionFileError
from prompts import compaction_prompt_loader
from prompts import system_prompt_builder
from services import instruction_store_service

TAKEAWAY = "instructions/audio/takeaway.md"                               # a file one phase sends alone
CHAPTER_SUMMARY = "compaction/conversation/chapter/summary.md"       # a compaction text
ADDED_RULE = "Write the Gateway's retry count as AcmeSync states it."  # an invented rule a person adds


@contextmanager
def _own_store():
    """A store of the test's own, empty, put back afterwards."""

    original = instruction_store_service.STORE_DIR
    with tempfile.TemporaryDirectory() as folder:
        instruction_store_service.set_store_dir(Path(folder) / "instructions")
        try:
            yield Path(folder) / "instructions"
        finally:
            instruction_store_service.set_store_dir(original)


@contextmanager
def _own_defaults():
    """A copy of the shipped instructions the test may change, put back afterwards."""

    original = config.AGENT_CONTEXT_DIR
    with tempfile.TemporaryDirectory() as folder:
        copy_root = Path(folder) / "agent-context"
        shutil.copytree(src=original, dst=copy_root)
        config.AGENT_CONTEXT_DIR = copy_root
        try:
            yield copy_root
        finally:
            config.AGENT_CONTEXT_DIR = original


def _is_refused(file_id: str) -> bool:
    try:
        instruction_store_service.read_active(file_id)
    except UnknownInstructionFileError:
        return True
    return False


def test_an_edit_is_in_force_and_the_shipped_default_stays_as_it_was():
    default_bytes = (config.AGENT_CONTEXT_DIR / TAKEAWAY).read_bytes()
    with _own_store() as store:
        harness.check("a file nobody edited reads its default",
                      instruction_store_service.read_active(TAKEAWAY) == default_bytes.decode("utf-8")
                      and not instruction_store_service.is_edited(TAKEAWAY))
        edited = default_bytes.decode("utf-8") + f"\n{ADDED_RULE}\n"
        saved = instruction_store_service.save(TAKEAWAY, edited, account="jonas", note="retry counts")
        harness.check("the edit is in force", instruction_store_service.read_active(TAKEAWAY) == edited
                      and instruction_store_service.is_edited(TAKEAWAY), str(saved))
        harness.check("it is kept in the workspace store", (store / "edits" / TAKEAWAY).read_text() == edited)
        harness.check("the shipped default is unchanged", (config.AGENT_CONTEXT_DIR / TAKEAWAY).read_bytes() == default_bytes)
        versions = instruction_store_service.list_versions(TAKEAWAY)
        harness.check("one version, with who saved it and why",
                      [(v["kind"], v["account"], v["note"]) for v in versions] == [("edit", "jonas", "retry counts")],
                      str(versions))
        again = instruction_store_service.save(TAKEAWAY, edited, account="jonas")
        harness.check("saving the same text again adds no version",
                      not again["is_changed"] and len(instruction_store_service.list_versions(TAKEAWAY)) == 1)
        harness.check("the edited files are listed", instruction_store_service.list_edited() == [TAKEAWAY])


def test_a_reset_brings_the_default_back_and_keeps_the_edit_as_a_version():
    with _own_store():
        default = instruction_store_service.read_default(TAKEAWAY)
        first = instruction_store_service.save(TAKEAWAY, default + f"\n{ADDED_RULE}\n", account="jonas")
        reset = instruction_store_service.reset(TAKEAWAY, account="jonas")
        harness.check("the default is in force again", instruction_store_service.read_active(TAKEAWAY) == default
                      and not instruction_store_service.is_edited(TAKEAWAY), str(reset))
        versions = instruction_store_service.list_versions(TAKEAWAY)
        harness.check("the edit and the reset are both versions, newest first",
                      [v["kind"] for v in versions] == ["reset", "edit"], str(versions))
        harness.check("the edit can be read back", ADDED_RULE in instruction_store_service.read_version(
            TAKEAWAY, first["at"])["text"])
        harness.check("a reset of a file at its default adds nothing",
                      not instruction_store_service.reset(TAKEAWAY, account="jonas")["is_changed"]
                      and len(instruction_store_service.list_versions(TAKEAWAY)) == 2)
        restored = instruction_store_service.save(TAKEAWAY, default + f"\n{ADDED_RULE}\n", account="jonas",
                                                  note=f"restored {first['at']}", kind="restore")
        harness.check("an earlier version saved again is in force, as a restore",
                      ADDED_RULE in instruction_store_service.read_active(TAKEAWAY)
                      and instruction_store_service.list_versions(TAKEAWAY)[0]["kind"] == "restore", str(restored))
        instruction_store_service.save(TAKEAWAY, default, account="jonas")
        harness.check("saving the default's own text is a reset",
                      not instruction_store_service.is_edited(TAKEAWAY)
                      and instruction_store_service.list_versions(TAKEAWAY)[0]["kind"] == "reset")


def test_a_default_changed_under_an_edit_is_shown_with_the_default_it_was_made_over():
    with _own_defaults() as root, _own_store():
        old_default = instruction_store_service.read_default(TAKEAWAY)
        instruction_store_service.save(TAKEAWAY, old_default + f"\n{ADDED_RULE}\n", account="jonas")
        harness.check("an edit over the default in force has no change to show",
                      not instruction_store_service.describe(TAKEAWAY)["is_default_changed"])
        (root / TAKEAWAY).write_text(old_default + "\nA rule the next release adds.\n", encoding="utf-8")
        described = instruction_store_service.describe(TAKEAWAY)
        harness.check("an upgrade's new default is shown as changed since the edit",
                      described["is_default_changed"] and described["default_at_edit"] == old_default
                      and "A rule the next release adds." in described["default"], str(described)[:300])
        harness.check("the edit stays in force", ADDED_RULE in described["text"])


def test_only_a_shipped_instruction_file_can_be_read_or_saved():
    with _own_store():
        for file_id in ("../src/config.py", "instructions/no-such-step.md", "base/README.md",
                        "instructions/_shared/known-terms.local.md", "context_map.json", "/etc/passwd"):
            harness.check(f"{file_id} is refused", _is_refused(file_id))
        harness.check("a version that does not exist is refused", _is_version_refused(TAKEAWAY, "20260101T000000.000000"))
        harness.check("a stamp holding a path is refused", _is_version_refused(TAKEAWAY, "../../edits"))
        editable = instruction_store_service.list_editable_files()
        harness.check("the editable files hold the instructions and the compaction texts, no README",
                      TAKEAWAY in editable and CHAPTER_SUMMARY in editable
                      and not any(name.endswith("README.md") for name in editable), str(editable[:5]))


def _is_version_refused(file_id: str,
                        stamp: str) -> bool:
    try:
        instruction_store_service.read_version(file_id, stamp)
    except UnknownInstructionFileError:
        return True
    return False


def test_every_prompt_is_built_from_the_text_in_force():
    # A person's edit on the page must reach the worker: the system prompt and the compaction texts are read through
    # the store, and a draft being checked is read in place of both without being saved
    with _own_store():
        default = instruction_store_service.read_default(TAKEAWAY)
        instruction_store_service.save(TAKEAWAY, default + f"\n{ADDED_RULE}\n", account="jonas")
        harness.check("the system prompt holds the edit", ADDED_RULE in system_prompt_builder.build_system_prompt("takeaway"))
        summary = instruction_store_service.read_default(CHAPTER_SUMMARY)
        instruction_store_service.save(CHAPTER_SUMMARY, summary.replace("## NEXT", f"{ADDED_RULE}\n\n## NEXT"),
                                       account="jonas")
        harness.check("the compaction prompt holds the edit", ADDED_RULE in compaction_prompt_loader.read_summary_prompt("chapter"))
        with instruction_store_service.use_drafts({TAKEAWAY: "Only the draft."}):
            drafted = system_prompt_builder.build_system_prompt("takeaway")
        harness.check("a draft is read in place of the edit while a check builds", drafted == "Only the draft.", drafted[:80])
        harness.check("and only inside the check", ADDED_RULE in system_prompt_builder.build_system_prompt("takeaway"))
    harness.check("with the store emptied the default is sent again",
                  ADDED_RULE not in system_prompt_builder.build_system_prompt("takeaway"))
