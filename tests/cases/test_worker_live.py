"""A phase's worker, live, in every workflow, and what the person writes to it, on files laid out as a run lays them out."""

import json
import tempfile
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from support import harness

from models import output_contract
from services import conversation_message_service
from services import document_pass_service
from services import run_status_service
from services import web_api_service
from services import worker_live_service
from services import worker_message_service
from utils import mermaid_text_utils


def _write_attempt(root: Path,
                   phase: str,
                   step: str,
                   thinking: str) -> None:
    attempt = root / "logs" / "session-1" / phase / step / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "thinking-live.md").write_text(thinking, encoding="utf-8")


def test_a_phase_has_a_worker_only_when_one_left_something_to_show_defect():
    # A card opened "404: Not Found" on a phase no model ran in; it now offers a worker only when one left a call or a
    # conversation behind.
    root = Path(tempfile.mkdtemp())
    has_worker = run_status_service._has_phase_worker
    harness.check("nothing written: no worker", not has_worker(root, 3, "frames_processing"))
    harness.check("no conversation: no worker in phase 4", not has_worker(root, 4, "finalize"))
    _write_attempt(root, "frames_processing", "frames-1-4", "I read the slide.")
    harness.check("an attempt is a worker", has_worker(root, 3, "frames_processing"))
    harness.check("in its own phase only", not has_worker(root, 2, "frames_preparing"))


def test_a_document_written_with_a_broken_diagram_is_sent_mended_and_its_file_stays_defect():
    # Documents written before the repair kept reaching the reader as "This diagram could not be drawn"; the route now
    # sends each diagram mended, and the file on disk stays as the run wrote it.
    output = Path(tempfile.mkdtemp())
    run = output / "made-run"
    run.mkdir()
    written = ("# Flow\n\n```mermaid\nflowchart LR\n    Doc -->|Approval (TAN/Sign)| Conf[\"Done\"]\n"
               "    A[Info page (optional)] --> Doc\n```\n")
    (run / "made.md").write_text(written, encoding="utf-8")
    (run / "plain.md").write_text("# No diagram\n", encoding="utf-8")
    saved = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = output
    try:
        sent = web_api_service.send_run_file("output", "made-run", "made.md").body.decode("utf-8")
        plain = web_api_service.send_run_file("output", "made-run", "plain.md")
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved)
    harness.check("the box's label is quoted", 'A["Info page (optional)"]' in sent, sent)
    harness.check("the arrow's label has no bracket", "(TAN/Sign)|" not in sent, sent)
    harness.check("what is sent passes the check", mermaid_text_utils.list_fenced_problems(sent) == [], sent)
    harness.check("the file stays as written", (run / "made.md").read_text(encoding="utf-8") == written)
    harness.check("a file with nothing to mend is sent as a file", Path(plain.path).name == "plain.md", str(plain))


def test_each_turn_of_a_part_keeps_its_own_thinking_and_whole_inputs():
    # The panel showed only the thinking of the call under way, each earlier call's lost from view, and a write's inputs
    # cut mid-JSON; each turn now carries the thinking its saved reply holds, and inputs stay whole JSON, shortened.
    output = Path(tempfile.mkdtemp())
    pass_dir = output / "07_reshaping"
    (pass_dir / "conversations").mkdir(parents=True)
    rows = [{"at": 1.0, "call": 1, "text": "", "tools": [{"name": "read_knowledge_outline", "args": {}}]},
            {"at": 1.5, "call": 1, "tool": "read_knowledge_outline", "result": "No section yet."},
            {"at": 2.0, "call": 2, "text": "(thinking stopped: it repeated itself)", "tools": []},
            {"at": 3.0, "call": 3, "text": "I add the router.", "tools": [
                {"name": "write_sections", "args": {"sections": [{"heading": "Router", "text": "x" * 900}] * 12}}]}]
    (pass_dir / "batch-001.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    replies = [{"type": "AIMessageChunk", "data": {"additional_kwargs": {"reasoning_content": thinking}}}
               for thinking in ("I read the outline first.", "I write the router.")]
    saved = {"messages": [{"type": "system", "data": {}}, *replies]}
    (pass_dir / "conversations" / "batch-001.json").write_text(json.dumps(saved), encoding="utf-8")
    turns = document_pass_service.describe_part_activity(output, 1, 0.0)["turns"]
    harness.check("one turn a call", [turn["call"] for turn in turns] == [1, 2, 3], str(turns))
    harness.check("each turn its own thinking, a looped one none",
                  [turn["thinking"] for turn in turns] == ["I read the outline first.", "", "I write the router."], str(turns))
    harness.check("a turn's words", turns[2]["text"] == "I add the router.", str(turns[2]))
    harness.check("a turn's answer", turns[0]["steps"][0]["result"] == "No section yet.", str(turns[0]))
    args = json.loads(turns[2]["steps"][0]["args"])
    harness.check("inputs stay whole JSON, each text and list shortened",
                  len(args["sections"]) == 11 and args["sections"][0]["text"].endswith("…"), str(args)[:300])


def test_a_call_step_shows_its_newest_thinking_and_its_calls_and_offers_no_question():
    root = Path(tempfile.mkdtemp())
    _write_attempt(root, "frames_processing", "frames-1-4", "I read the slide.")
    shown = worker_live_service.describe_phase_worker(root, 3, 0.0)
    harness.check("its thinking", shown["thinking"] == "I read the slide.", str(shown))
    harness.check("its step", shown["step"] == "frames-1-4", shown["step"])
    harness.check("its calls", [step["tool"] for step in shown["steps"]] == ["frames-1-4"], str(shown["steps"]))
    harness.check("no question: its reply is read in a set shape", shown["can_ask"] is False, "")


def test_a_conversation_shows_its_turns_with_their_answers_and_offers_a_question():
    root = Path(tempfile.mkdtemp())
    log = root / output_contract.WRITE_CONVERSATION_LOG
    log.parent.mkdir(parents=True)
    rows = [{"followUp": "chapter-01", "at": 1.0, "said": "", "calls": [{"name": "read_notes", "args": {"chapter": 1}}]},
            {"followUp": "chapter-01", "at": 2.0, "tool": "read_notes", "result": "Chapter 1 read whole."}]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (log.parent / "thinking-live.md").write_text("I draft the chapter.", encoding="utf-8")
    shown = worker_live_service.describe_phase_worker(root, 4, 0.0)
    harness.check("its thinking", shown["thinking"] == "I draft the chapter.", str(shown))
    harness.check("its step with its answer", shown["steps"][0]["tool"] == "read_notes"
                  and shown["steps"][0]["result"] == "Chapter 1 read whole.", str(shown["steps"]))
    harness.check("a question is offered", shown["can_ask"] is True, "")


def test_a_message_reaches_the_conversation_once_and_a_question_gets_its_answer():
    path = worker_message_service.build_phase_messages_path(Path(tempfile.mkdtemp()), "finalize")
    worker_message_service.queue_message(path, {"text": "Why so long?", "role": "user", "is_question": True})
    worker_message_service.queue_message(path, {"text": "Use short headings.", "role": "system", "is_question": False})
    messages = [SystemMessage(content="Your instructions.")]
    harness.check("both are added", conversation_message_service.add_waiting_to_conversation(path, messages) == 2, str(messages))
    harness.check("the user message goes last", isinstance(messages[-1], HumanMessage)
                  and "Why so long?" in messages[-1].content and "no tool call" in messages[-1].content, str(messages))
    harness.check("the system message joins the first message", "Use short headings." in messages[0].content, str(messages))
    harness.check("nothing is added twice", conversation_message_service.add_waiting_to_conversation(path, messages) == 0, "")
    worker_message_service.record_reply(path, "The chapter is long.")
    shown = worker_message_service.list_messages(path)
    harness.check("the question has its answer, the message none",
                  shown[0]["answer"] == "The chapter is long." and shown[1]["answer"] == "" and shown[1]["is_sent"], str(shown))


def test_each_message_names_when_the_worker_took_it_and_one_waiting_names_none():
    path = worker_message_service.build_phase_messages_path(Path(tempfile.mkdtemp()), "document_reshaping")
    worker_message_service.queue_message(path, {"text": "Be faster.", "role": "system", "is_question": False})
    before = time.time()
    worker_message_service.take_waiting(path)
    worker_message_service.queue_message(path, {"text": "Keep the tables.", "role": "user", "is_question": False})
    shown = worker_message_service.list_messages(path)
    harness.check("the message taken names when", (shown[0]["sent_at"] or 0) >= before, str(shown))
    harness.check("the message still waiting names no time", shown[1]["sent_at"] is None and not shown[1]["is_sent"], str(shown))
