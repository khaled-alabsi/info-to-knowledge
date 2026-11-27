"""A knowledge part run whole through `document_knowledge_service.run_batch` on a made document (every word invented), with
what the person writes from the part's live panel: a question, a message as user and one as system. The part must not
end on the worker's answer in words, and the messages must cost it none of its calls."""

import logging
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from support import fakes, harness

import config
from models.batch_run_request import BatchRunRequest
from models.run_paths import RunPaths
from services import document_folder_service
from services import document_knowledge_service
from services import document_pass_service
from services import worker_message_service

DOCUMENT = "# Gateway guide\n\nThe router picks the ledger shard from the tenant id.\n"
QUESTION = "How far are you with the router?"
MESSAGE = "Keep the tenant id in the first sentence."
SYSTEM_TEXT = "Write every heading in sentence case."


def _start_part() -> tuple[RunPaths, dict]:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")

    return paths, document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})


def test_a_question_to_a_running_part_is_answered_and_the_part_goes_on():
    paths, state = _start_part()
    root = paths.output_root
    messages_path = document_pass_service.build_part_messages_path(root, 1)
    worker_message_service.queue_message(messages_path, {"text": QUESTION, "role": "user", "is_question": True})
    worker_message_service.queue_message(messages_path, {"text": SYSTEM_TEXT, "role": "system", "is_question": False})
    model = fakes.scripted(AIMessage(content="Half of the part is written."),
                           fakes.tool_call("finish_batch", summary="routing written"))
    budget = config.settings.document_reshaping.batch_calls
    config.settings.document_reshaping.batch_calls = 1
    try:
        request = BatchRunRequest(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
        is_closed, calls, reason = document_knowledge_service.run_batch(request=request)
    finally:
        config.settings.document_reshaping.batch_calls = budget
    harness.check("an answer in words does not close the part", is_closed and calls == 2, f"{is_closed} {calls} {reason}")
    first_call = model.calls[0]
    harness.check("the question reaches the first call, as a user message", isinstance(first_call[-1], HumanMessage)
                  and QUESTION in str(first_call[-1].content) and "no tool call" in str(first_call[-1].content),
                  str(first_call[-1].content)[:200])
    harness.check("the system message joins the first message", isinstance(first_call[0], SystemMessage)
                  and SYSTEM_TEXT in str(first_call[0].content), "")
    harness.check("the worker is sent back to work after its answer",
                  "Answer with a tool call" in str(model.calls[1][-1].content), str(model.calls[1][-1].content)[:200])
    shown = document_pass_service.describe_part_activity(root, 1, 0.0)["messages"]
    harness.check("the panel shows the question sent with its answer", shown[0]["is_sent"]
                  and shown[0]["answer"] == "Half of the part is written.", str(shown))
    harness.check("nothing waits any more", worker_message_service.list_waiting(messages_path) == [], "")


def test_a_message_written_during_a_part_reaches_its_next_call_once():
    paths, state = _start_part()
    root = paths.output_root
    read_section = fakes.tool_call("read_outline")
    model = fakes.scripted(read_section, fakes.tool_call("finish_batch", summary="routing written"))
    real_stream = document_knowledge_service.document_reshaping_service.stream_reply

    def stream_and_write(*args):
        reply = real_stream(*args)
        if len(model.calls) == 1:
            worker_message_service.queue_message(document_pass_service.build_part_messages_path(root, 1),
                                                 {"text": MESSAGE, "role": "user", "is_question": False})
        return reply

    document_knowledge_service.document_reshaping_service.stream_reply = stream_and_write
    try:
        request = BatchRunRequest(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
        is_closed, _calls, reason = document_knowledge_service.run_batch(request=request)
    finally:
        document_knowledge_service.document_reshaping_service.stream_reply = real_stream
    harness.check("the part closes", is_closed, reason)
    harness.check("the call under way did not hear it", MESSAGE not in str(model.calls[0]), "")
    sent = [message for message in model.calls[1] if MESSAGE in str(message.content)]
    harness.check("the next call has it once", len(sent) == 1, str(len(sent)))
