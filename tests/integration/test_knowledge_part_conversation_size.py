"""A knowledge part run through `document_knowledge_service.run_batch` on a made document (every word invented), resumed
from a saved conversation grown far past the size limit, as a real part's did: part 14 of a knowledge pass sent 359,130
tokens to a model loaded with 183,040, and the run ended there."""

import base64
import io
import logging
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from PIL import Image

from support import fakes, harness

import config as app_config
from models.batch_run_request import BatchRunRequest
from models.run_paths import RunPaths
from services import document_folder_service
from services import document_knowledge_service
from services import document_pass_service
from services import document_reshaping_service

DOCUMENT = "# Gateway guide\n\nThe router picks the ledger shard from the tenant id.\n"
# One earlier read's result: about 20,000 characters, as a long section read whole
READ_RESULT = "The ledger shard holds the tenant's postings. " * 450


def _start_part() -> tuple[RunPaths, dict]:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")

    return paths, document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})


def _grow_saved_conversation(paths: RunPaths,
                             state: dict,
                             reads: int) -> list:
    """The part's first two messages, then `reads` turns each reading a long section, saved as a stopped part's are."""

    system_text, first_text = document_knowledge_service.rebuild_request(state=state, number=1)
    messages = [SystemMessage(content=system_text), HumanMessage(content=first_text)]
    for turn in range(reads):
        call_id = f"read-{turn}"
        messages.append(AIMessage(content="", tool_calls=[{"name": "read_section", "args": {"section": "K1"},
                                                           "id": call_id}]))
        messages.append(ToolMessage(content=READ_RESULT, tool_call_id=call_id))
    document_reshaping_service.save_conversation(paths, 1, messages, {"blocks": {1}, "sections": {1}})

    return messages


def test_a_part_at_its_size_limit_is_compacted_into_the_worker_s_summary_defect():
    # Defect: past the size limit the program cleared a part's earlier reads on its own, each clear making the model host
    # read the whole prompt again. Fix: at the limit the worker writes a summary, and the part goes on from it
    paths, state = _start_part()
    limit = app_config.settings.document_reshaping.conversation_size_limit_tokens
    reads = int(limit * 1.1 * app_config.settings.document_reshaping.chars_per_token / len(READ_RESULT)) + 1
    _grow_saved_conversation(paths=paths, state=state, reads=reads)
    model = fakes.scripted(AIMessage(content="Written: routing into K1. Left: nothing of the part."),
                           fakes.tool_call("finish_batch", summary="closed"))
    request = BatchRunRequest(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
    is_closed, _calls, reason = document_knowledge_service.run_batch(request=request)
    harness.check("the part closes after its compaction", is_closed, reason)
    asked = model.calls[0]
    harness.check("the summary is asked of the whole conversation, nothing cleared first",
                  len(asked) == 2 + 2 * reads + 1 and "is compacted now" in str(asked[-1].content), str(len(asked)))
    sent = model.calls[1]
    harness.check("the conversation goes on from the instructions, the part and the summary",
                  len(sent) == 3 and "router picks the ledger shard" in str(sent[1].content)
                  and "Written: routing into K1" in str(sent[2].content), str([str(m.content)[:60] for m in sent]))
    sent_tokens = sum(len(str(message.content)) for message in sent) / app_config.settings.document_reshaping.chars_per_token
    harness.check("the call after it is well within the size limit", sent_tokens < limit / 2, f"{sent_tokens:,.0f}")


def test_a_part_with_many_small_figures_is_not_compacted_while_it_fits_defect():
    # Defect: a part's 148 small figures counted 1,000 tokens each, its conversation read as twice its size, and it was
    # compacted again on every turn. Fix: each figure counts by its pixels, so a part that fits goes on uncompacted
    paths, state = _start_part()
    limit = app_config.settings.document_reshaping.conversation_size_limit_tokens
    messages = _grow_saved_conversation(paths=paths, state=state, reads=0)
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    figures = [{"type": "image_url", "image_url": {"url": url}} for _ in range(150)]
    messages.append(HumanMessage(content=[{"type": "text", "text": "The part's figures."}, *figures]))
    # text up to about two thirds of the limit: with 150 figures at 1,000 tokens each it would pass the limit
    read_text = "The ledger shard holds the tenant's postings. " * int(limit * 0.6 * app_config.settings.document_reshaping.chars_per_token / 46)
    messages.append(AIMessage(content="", tool_calls=[{"name": "read_section", "args": {"section": "K1"}, "id": "r0"}]))
    messages.append(ToolMessage(content=read_text, tool_call_id="r0"))
    document_reshaping_service.save_conversation(paths, 1, messages, {"blocks": {1}, "sections": {1}})
    model = fakes.scripted(fakes.tool_call("finish_batch", summary="closed"))
    request = BatchRunRequest(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
    is_closed, _calls, reason = document_knowledge_service.run_batch(request=request)
    parts = document_pass_service.describe_parts(output_root=paths.output_root, is_live=False, now=1.0)
    harness.check("the part closes with no compaction, its one call sent the whole conversation",
                  is_closed and parts[0]["compactions"] == 0 and len(model.calls[0]) == len(messages),
                  f"{reason} {parts[0]} {len(model.calls[0])}")


def test_the_parts_table_counts_a_part_s_compactions_defect():
    # Defect: the parts table showed no sign of a part's conversation being compacted, so the owner could not see it
    # Fix: each compaction writes a row into the part's turn log, and the table counts those rows per part
    paths, state = _start_part()
    limit = app_config.settings.document_reshaping.conversation_size_limit_tokens
    reads = int(limit * 1.1 * app_config.settings.document_reshaping.chars_per_token / len(READ_RESULT)) + 1
    _grow_saved_conversation(paths=paths, state=state, reads=reads)
    model = fakes.scripted(AIMessage(content="Written: routing into K1. Left: nothing of the part."),
                           fakes.tool_call("finish_batch", summary="closed"))
    request = BatchRunRequest(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
    document_knowledge_service.run_batch(request=request)
    parts = document_pass_service.describe_parts(output_root=paths.output_root, is_live=False, now=1.0)
    harness.check("part 1 counts its one compaction and its one model call",
                  (parts[0]["compactions"], parts[0]["calls"]) == (1, 1), str(parts[0]))
