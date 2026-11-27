"""A PDF's markdown conversion followed by a model's knowledge pass, run whole
through `i2k run --pdf-mode markdown-conversion --document-pass knowledge` on the made wiki export
(tests/resources/pdf/out/01-confluence-export.pdf, every word invented). Phase 4 publishes the converted document; phase 7
cuts it into parts, and a worker given its part in its first message reads the knowledge outline, looks at the part's
figure, writes one section with a fact and the figure and a second section with a second fact, merges the second into
the first, and closes the part. The model is a
stand-in that answers each turn from what it was sent, so the real phase runs: the tools, the sections written into the
published document, the image sent after the figure's tool, the document kept before, the report and the versions."""

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
from services import profile_version_service
from services import recording_hash_service
from services import run_artifact_service
from services import run_status_service
from services import worker_view_service

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "01-confluence-export.pdf"
# The two facts the stand-in writes, the first with the figure, the second in a section it then merges into the first
FIRST_FACT = "The router picks the ledger shard from the tenant id."
SECOND_FACT = "Responses are cached for one hour."
_BLOCK = re.compile(r"^\{(B\d+)\}\n", re.MULTILINE)
_FIGURE_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$", re.MULTILINE)


class KnowledgeModel(BaseChatModel):
    """Reads the outline, looks at its part's first figure (the part is in its first message), writes two sections and
    merges the second into the first, and closes the part; each step chosen by what the conversation already holds."""

    seen_system: list[str] = []
    seen_images: list[int] = []
    seen_tools: list[str] = []
    seen_results: list[str] = []
    first_images: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> "KnowledgeModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        self.seen_system.append(str(messages[0].content))
        self.seen_images.append(sum(1 for message in messages if isinstance(message.content, list)
                                    for part in message.content if isinstance(part, dict)
                                    and part.get("type") == "image_url"))
        results = [str(message.content) for message in messages if isinstance(message, ToolMessage)]
        self.seen_results = results
        first = messages[1].content
        blocks = first if isinstance(first, str) else "\n".join(str(part.get("text", "")) for part in first)
        # the part's figures it was shown as images in its first message, never asked for with a tool
        self.first_images = 0 if isinstance(first, str) else sum(1 for part in first if part.get("type") == "image_url")
        figure = _FIGURE_LINE.search(blocks)
        if not results:
            reply = _call("read_knowledge_outline")
        elif not any("edit(s) applied" in text for text in results):
            # a figure is kept only when its image was seen: a line of text alone is no figure seen
            section = f"{FIRST_FACT}\n\n{figure.group(0)}" if figure and self.first_images else FIRST_FACT
            reply = _call("write_sections", edits=[
                {"action": "new_section", "heading": "Routing", "text": section},
                {"action": "new_section", "heading": "Caching", "text": SECOND_FACT},
                {"action": "merge", "section": "K2", "into": "K1"}])
        else:
            reply = _call("finish_batch", summary="wrote the routing section")
        self.seen_tools.append(reply.tool_calls[0]["name"])

        return ChatResult(generations=[ChatGeneration(message=reply)])

    @property
    def _llm_type(self) -> str:
        return "knowledge"


def _call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}", "type": "tool_call"}])


def test_a_markdown_conversion_s_knowledge_pass_writes_one_section_per_subject_and_keeps_the_document_before():
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    pdf = root / PDF.name
    shutil.copy(PDF, pdf)
    run = root / "run"
    model = KnowledgeModel()
    model.seen_system, model.seen_images, model.seen_tools, model.seen_results = [], [], [], []
    original = chat_model_service.get_model
    chat_model_service.get_model = lambda phase: model
    try:
        code = cli.main(["run", "--force", "--pdf-mode", "markdown-conversion", "--document-pass", "knowledge",
                         "--document-wishes", "keep the field tables",
                         "--video", str(pdf), "--output", str(run)])
    finally:
        chat_model_service.get_model = original
    harness.check("the run ends well", code == 0, str(code))

    # 1. the phase ran to its end, and the published document is the knowledge document
    record = json.loads(next((run / "logs").glob("*/run.json")).read_text(encoding="utf-8"))
    harness.check("phase 7 is complete", record["phases"].get("document_reshaping") == "complete", str(record["phases"]))
    documents = [path for path in run.glob("*/*.md") if path.parent.name != "07_reshaping"]
    harness.check("one published document", len(documents) == 1, str(documents))
    text = documents[0].read_text(encoding="utf-8")
    harness.check("one section holding both facts, the merged one's after the first, its heading gone",
                  text.count("## ") == 1 and "## Routing" in text and "Caching" not in text
                  and 0 <= text.find(FIRST_FACT) < text.find(SECOND_FACT), text[:800])
    harness.check("the worker was told where the merged block went",
                  any("K2 merged into K1" in result for result in model.seen_results), str(model.seen_results))
    harness.check("the part came in the first message: the worker never read it with a tool",
                  "read_blocks" not in model.seen_tools and model.seen_tools[-1] == "finish_batch",
                  str(model.seen_tools))
    harness.check("the figure the worker kept is in its section", "](resources/figure-01.png)" in text, text[:800])
    # a sentence of the export the worker never wrote into a section
    unwritten = "forwards the call over mutual TLS"
    harness.check("nothing the worker did not write is left", unwritten not in text, text[:800])
    before = (run / "07_reshaping" / "before.md").read_text(encoding="utf-8")
    harness.check("the document before the pass is kept whole", unwritten in before, before[:400])

    # 2. what the worker was sent: the knowledge instruction with the wish, and the figure as an image
    harness.check("the worker was told the knowledge instruction and the wish",
                  "## Goal" in model.seen_system[0] and "one section per subject" in model.seen_system[0]
                  and "keep the field tables" in model.seen_system[0], model.seen_system[0][-600:])
    # Defect: a part's figures reached the worker as lines of text, their images only through a tool it never called,
    # so a 52-figure PDF's first three parts placed no figure. Fix: each figure of the part is an image in its first
    # message, which the worker keeps, drops or replaces as it decides
    harness.check("the part's figure reached the worker as an image in its first message, with no tool asked",
                  model.first_images == 1 and "look_figure" not in model.seen_tools, str((model.first_images,
                                                                                         model.seen_tools)))
    saved = (run / "07_reshaping" / "requests" / "batch-001.md").read_text(encoding="utf-8")
    harness.check("the saved request names the image, never its bytes",
                  "characters of data URI]" in saved and "base64," not in saved, saved[-300:])

    # 3. the report, and the versions the Open document menu lists
    report = (run / "07_reshaping" / "report.md").read_text(encoding="utf-8")
    harness.check("the report names the pass and its one section", "# Document knowledge" in report
                  and "1 sections" in report, report)
    versions = profile_version_service.list_document_versions(run, documents[0])
    harness.check("the original, then after the knowledge reshaping", [version["label"] for version in versions]
                  == ["original", "after knowledge reshaping"], str(versions))
    # Todo 12: the document as each part left it is kept, the page listing every part's draft but the last one's
    draft = run / "07_reshaping" / "parts" / "part-001.md"
    harness.check("the one part's draft is kept, the published document as that part left it",
                  draft.is_file() and draft.read_text(encoding="utf-8") == text, str(draft))
    # DEFECTS 674: every version the menu lists is in the run's file list, so the reader opens it
    listed = {item["path"] for group in run_artifact_service.list_artifacts(run) for item in group["items"]}
    harness.check("every version opens in the reader", all(version["path"] in listed for version in versions),
                  str(sorted(listed)))
    # the reader's list names each version by what it is, not its path alone (the owner: three paths told nothing)
    labels = {item["path"]: item.get("label") for group in run_artifact_service.list_artifacts(run)
              if group["key"] == "document" for item in group["items"]}
    harness.check("each version in the reader's list is named as the menu names it",
                  all(labels.get(version["path"]) == version["label"] for version in versions), str(labels))

    # 4. the run's detail names the pass, which the page's Document pill shows (DEFECTS 670: it showed "Markdown")
    saved_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": run.parent})
    try:
        detail = run_status_service.describe_run_folder("output", run.name)
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved_roots)
    harness.check("the run's detail carries its markdown conversion and the knowledge pass",
                  detail.get("pdf_mode") == "markdown-conversion" and detail.get("document_pass") == "knowledge",
                  str({key: detail.get(key) for key in ("pdf_mode", "document_pass")}))
    # Defect: a conversion's cards offered a worker on phases that call no model, each opening a 404, and its table of
    # steps stood "running" while phase 7 ran. Now no card with no worker offers one, and a conversion has no table.
    harness.check("no phase of the conversion before phase 7 offers a worker",
                  not any(phase["has_worker"] for phase in detail["phases"] if phase["number"] < 7),
                  str([(phase["number"], phase["has_worker"]) for phase in detail["phases"]]))
    harness.check("a conversion lists no document steps", detail["document_stages"] == [], str(detail["document_stages"]))
    # the owner, on the phase 7 card: "rename this phase in ui to finale document reshapping"
    phase_7 = next(phase for phase in detail["phases"] if phase["number"] == 7)
    harness.check("phase 7 is named the final document reshaping", phase_7["title"] == "final document reshaping", str(phase_7))

    # 5. What the worker saw lists the part and shows what it was sent and what it did: it showed nothing of phase 7
    steps = worker_view_service.list_worker_steps(run, 7)
    harness.check("one step, the closed part", [(step["step"], step["attempts"][0]["status"]) for step in steps]
                  == [("batch-001", "accepted")], str(steps))
    seen = worker_view_service.read_attempt(run, steps[0]["attempts"][0]["ref"])
    harness.check("its instructions, its first message and its tool calls are shown",
                  "one section per subject" in (seen["instructions"] or "") and seen["input"]
                  and FIRST_FACT not in seen["input"][0]["text"] and "{B" in seen["input"][0]["text"]
                  and any(turn["kind"] == "result" and turn["tool"] == "finish_batch" for turn in seen["turns"]),
                  str({key: seen[key] for key in ("instructions", "turns")})[:800])
