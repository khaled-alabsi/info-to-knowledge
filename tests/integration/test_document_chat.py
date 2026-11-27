"""The chat on a final document run whole through `document_chat_service.answer_waiting_messages` on a made document (every
word invented), through the real edit service, with a stand-in model: it finds, edits, adds, removes and answers."""

import argparse
import json
import logging
import tempfile
from pathlib import Path

from support import fakes, harness

from cli import document_chat_command
from models.run_paths import RunPaths
from services import artifact_edit_service
from services import document_chat_service
from services import document_folder_service
from services import job_queue_service

DOCUMENT = "# Gateway guide\n\n## Routing\n\nThe router picks the ledger shard.\n\n## Retired\n\nAn old note.\n"


def _start_run() -> tuple[RunPaths, Path]:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(DOCUMENT, encoding="utf-8")

    return paths, document


def _find_block(rendered: str,
                words: str) -> str:
    return next(line.strip("{}") for line, text in zip(rendered.splitlines(), rendered.splitlines()[1:]) if words in text)


def test_a_chat_edits_adds_and_removes_as_asked_and_answers_in_words():
    paths, document = _start_run()
    root = paths.output_root
    document_chat_service.queue_chat_message(root, "Say tenant shard, add a closing note and remove the retired section.")
    state, _is_changed = document_chat_service._read_document_state(paths)
    rendered = document_chat_service.cleanup_block_utils.render_blocks(state["blocks"])
    routing, retired = _find_block(rendered, "ledger shard"), _find_block(rendered, "## Retired")
    last = _find_block(rendered, "An old note")
    model = fakes.scripted(
        fakes.tool_call("search_document", text="ledger"),
        fakes.tool_call("edit_blocks", edits=[{"action": "replace", "block": routing, "text": "The router picks the tenant shard."},
                                              {"action": "insert_after", "block": last, "text": "A closing note."},
                                              {"action": "remove", "block": retired, "last_block": last}]),
        fakes.says("I changed the routing sentence, added a closing note and removed the retired section."))
    turns = document_chat_service.answer_waiting_messages(paths, model, logging.getLogger("test"))
    written = document.read_text(encoding="utf-8")
    harness.check("one turn answered the message", turns == 1, str(turns))
    harness.check("the block is edited", "tenant shard" in written and "ledger shard" not in written, written)
    harness.check("the rest stays word for word", "## Routing" in written and "# Gateway guide" in written, written)
    harness.check("the new block is added", "A closing note." in written, written)
    harness.check("the section is removed", "Retired" not in written and "An old note" not in written, written)
    shown = document_chat_service.describe_chat(root, 0.0)
    harness.check("the page shows the answer", shown["messages"][0]["answer"].startswith("I changed"), str(shown["messages"]))
    harness.check("the page shows each step", [step["tool"] for step in shown["steps"]]
                  == ["search_document", "edit_blocks"], str(shown["steps"]))
    relative = document.relative_to(root).as_posix()
    kept = artifact_edit_service.list_versions(root, relative)
    harness.check("the version before the edit is kept", len(kept) == 1, str(kept))


def test_a_document_changed_outside_the_chat_gets_new_block_numbers_and_the_worker_is_told():
    paths, document = _start_run()
    root = paths.output_root
    document_chat_service.queue_chat_message(root, "What does the router pick?")
    document_chat_service.answer_waiting_messages(paths, fakes.scripted(fakes.says("The ledger shard.")), logging.getLogger("test"))
    document.write_text(DOCUMENT + "\n## Added by hand\n\nA hand note.\n", encoding="utf-8")
    document_chat_service.queue_chat_message(root, "And now?")
    model = fakes.scripted(fakes.says("Still the ledger shard."))
    document_chat_service.answer_waiting_messages(paths, model, logging.getLogger("test"))
    sent = str(model.calls[0])
    harness.check("the conversation goes on", "What does the router pick?" in sent and "And now?" in sent, sent[:300])
    harness.check("the worker is told its block numbers are new", "block numbers are new" in sent, sent[:300])


def test_the_command_answers_what_waits_and_ends():
    paths, _document = _start_run()
    document_chat_service.queue_chat_message(paths.output_root, "What does the router pick?")
    model = fakes.scripted(fakes.says("The ledger shard."))
    saved = document_chat_command.chat_model_service.get_model
    document_chat_command.chat_model_service.get_model = lambda phase: model
    try:
        code = document_chat_command.cmd_chat_document(argparse.Namespace(output_root=paths.output_root,
                                                                          logger=logging.getLogger("test")))
    finally:
        document_chat_command.chat_model_service.get_model = saved
    shown = document_chat_service.describe_chat(paths.output_root, 0.0)["messages"]
    harness.check("it ends well and the answer is there", code == 0 and shown[0]["answer"] == "The ledger shard.", str(shown))


def test_a_message_starts_one_worker_and_start_again_begins_a_new_conversation():
    paths, _document = _start_run()
    root = paths.output_root
    started = []

    def start(command, console):
        started.append(command)
        return 2 ** 30

    previous = document_chat_service.set_process_starter(start)
    try:
        document_chat_service.queue_chat_message(root, "Hello")
        document_chat_service.start_worker(root)
    finally:
        document_chat_service.set_process_starter(previous)
    harness.check("the worker is this program's own command", started[0][-3:] == ["chat-document", "--output", str(root)]
                  and started[0][:2] == job_queue_service.build_tool_command([]), str(started))
    harness.check("its pid is kept", json.loads((root / document_chat_service.CHAT_DIR / "process.json").read_text())["pid"] == 2 ** 30, "")
    document_chat_service.set_conversation_aside(root)
    harness.check("start again leaves no message", document_chat_service.describe_chat(root, 0.0)["messages"] == [], "")
    harness.check("what was said is kept", (root / document_chat_service.CHAT_DIR / "earlier" / "1" / "messages.jsonl").is_file(), "")
