"""A PDF's markdown conversion followed by a model's clean up, run whole through
`i2k run --pdf-mode markdown-conversion --document-pass cleanup` on the made wiki export
(tests/resources/pdf/out/01-confluence-export.pdf, every word invented). Phase 4 publishes the converted document; phase 7
cuts it into numbered blocks and batches, and a worker reads its batch, removes the block the person's wish names, and
closes the batch. The model is a stand-in that answers each turn from what it was sent, so the real phase runs: the
tools, the edits written into the published document, the document kept before, the report and the phase's status."""

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from support import harness

import cli
from services import chat_model_service
from services import recording_hash_service

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "01-confluence-export.pdf"
# The sentence the person's wish asks the worker to take out
TARGET = "Responses are cached"
_BATCH = re.compile(r"blocks (B\d+) to (B\d+)")
_BLOCK = re.compile(r"^\{(B\d+)\}\n", re.MULTILINE)


class CleaningModel(BaseChatModel):
    """Reads its batch whole, removes the block holding `TARGET`, then closes the batch."""

    seen_system: list[str] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CleaningModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        self.seen_system.append(str(messages[0].content))
        last = messages[-1]
        if isinstance(last, HumanMessage):
            first, end = _BATCH.search(str(last.content)).groups()
            reply = _call("read_blocks", first=first, last=end)
        elif isinstance(last, ToolMessage) and _BLOCK.search(str(last.content)):
            pieces = _BLOCK.split(str(last.content))[1:]
            blocks = dict(zip(pieces[0::2], pieces[1::2]))
            target = next((block for block, text in blocks.items() if TARGET in text), None)
            reply = (_call("edit_blocks", edits=[{"action": "remove", "block": target}]) if target
                     else _call("finish_batch", summary="nothing to remove"))
        else:
            reply = _call("finish_batch", summary=f"removed the block holding {TARGET!r}")

        return ChatResult(generations=[ChatGeneration(message=reply)])

    @property
    def _llm_type(self) -> str:
        return "cleaning"


def _call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}", "type": "tool_call"}])


def test_a_markdown_conversion_s_clean_up_removes_what_the_worker_edits_and_keeps_the_document_before():
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    pdf = root / PDF.name
    shutil.copy(PDF, pdf)
    run = root / "run"
    model = CleaningModel()
    model.seen_system = []
    original = chat_model_service.get_model
    chat_model_service.get_model = lambda phase: model
    try:
        code = cli.main(["run", "--force", "--pdf-mode", "markdown-conversion", "--document-pass", "cleanup",
                         "--document-wishes", "remove the sentence about cached responses",
                         "--video", str(pdf), "--output", str(run)])
    finally:
        chat_model_service.get_model = original
    harness.check("the run ends well", code == 0, str(code))

    record = json.loads(next((run / "logs").glob("*/run.json")).read_text(encoding="utf-8"))
    harness.check("phase 7 is complete", record["phases"].get("document_reshaping") == "complete", str(record["phases"]))
    documents = [path for path in run.glob("*/*.md") if path.parent.name != "07_reshaping"]
    harness.check("one published document", len(documents) == 1, str(documents))
    text = documents[0].read_text(encoding="utf-8")
    harness.check("the block the worker removed is gone", TARGET not in text, text[:600])
    harness.check("the rest stays, its figure with it",
                  "The router then picks the ledger shard" in text and "](resources/figure-01.png)" in text, text[:600])
    before = (run / "07_reshaping" / "before.md").read_text(encoding="utf-8")
    harness.check("the document before the clean up is kept whole", TARGET in before)
    harness.check("the report is written", (run / "07_reshaping" / "report.md").is_file())
    harness.check("the worker was told the clean up pass and the wish",
                  "## The clean up pass" in model.seen_system[0] and "## The rewrite pass" not in model.seen_system[0]
                  and "cached responses" in model.seen_system[0], model.seen_system[0][-600:])

    # A resume of the finished run leaves the document alone; a rewrite asked of the done run runs over it, the first
    # pass's files kept, and a reset to phase 6 puts back the document as phase 4 published it
    calls = len(model.seen_system)
    chat_model_service.get_model = lambda phase: model
    try:
        # --force: a run folder of the repository may hold this made PDF too
        resumed = cli.main(["run", "--force", "--resume", "--video", str(pdf), "--output", str(run)])
        harness.check("a resume calls no model for a finished pass", len(model.seen_system) == calls,
                      str(len(model.seen_system) - calls))
        rewritten = cli.main(["run", "--force", "--resume", "--phase", "7", "--document-pass", "rewrite", "--video", str(pdf),
                              "--output", str(run)])
    finally:
        chat_model_service.get_model = original
    harness.check("both end well", resumed == 0 and rewritten == 0, f"{resumed} {rewritten}")
    harness.check("the rewrite was told the rewrite pass", "## The rewrite pass" in model.seen_system[-1])
    harness.check("the first pass is kept", (run / "07_reshaping" / "earlier" / "1" / "report.md").is_file())
    code = cli.main(["reset-to", "6", "--yes", "--output", str(run)])
    harness.check("the reset ends well", code == 0, str(code))
    harness.check("the document is back as phase 4 published it", documents[0].read_text(encoding="utf-8") == before)
    harness.check("phase 7's folder is gone", not (run / "07_reshaping").exists())
