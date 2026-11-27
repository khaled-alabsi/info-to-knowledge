"""A knowledge part run whole through `document_knowledge_service.run_batch` on a made document (every word invented), its
stand-in model repeating what the real worker repeated: one section read three times unchanged, a remove of a block already
gone, a replace with no text. Then the part stops mid-way and a second start goes on with its saved conversation."""

import logging
import re
import tempfile
from pathlib import Path

from langchain_core.messages import ToolMessage

from support import fakes, harness

from models.run_paths import RunPaths
from services import document_folder_service
from services import document_knowledge_service
from services import document_reshaping_service

DOCUMENT = "# Gateway guide\n\nThe router picks the ledger shard from the tenant id.\n"


def _start_part() -> tuple[RunPaths, dict]:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")

    return paths, document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})


def test_a_figure_placed_under_the_worker_s_own_caption_reaches_the_document_with_its_own_defect():
    # Defect: a knowledge part placed a figure the document holds under a caption it wrote ("Figure on page 1024"), the
    # write was refused as "in no block of the document", and a turn went on finding the line. Fix: the figure's file names it
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    figured = f"{DOCUMENT}\n![Figure 2: Ledger shards](resources/figure-266.png)\n"
    paths.summary.write_text(figured, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(figured, encoding="utf-8")
    state = document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})
    model = fakes.scripted(fakes.tool_call("write_sections", edits=[{"action": "new_section", "heading": "Shards",
        "text": "The router picks the ledger shard.\n![Figure on page 1024](resources/figure-266.png)"}]),
                           fakes.tool_call("finish_batch", summary="shards written"))
    document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
    written = document_knowledge_service.render_knowledge(state)
    answers = [str(message.content) for message in model.calls[-1] if isinstance(message, ToolMessage)]
    harness.check("the write is not refused", not any("in no block" in answer for answer in answers), str(answers))
    harness.check("the figure stands under the document's own caption",
                  "![Figure 2: Ledger shards](resources/figure-266.png)" in written and "Figure on page 1024" not in written, written)


def test_a_diagram_with_a_bracket_in_a_box_label_reaches_the_document_drawable_defect():
    # Defect: a knowledge part wrote `K[Info page (conditional)]` into a flowchart, nothing checked it, and the reader got a
    # parse error in place of the diagram. Fix: such a label is wrapped in quotes as it is written; an unclosed box is refused
    paths, state = _start_part()
    diagram = "```mermaid\nflowchart TD\n    K[Info page (conditional)]\n    L[\"Kept (quoted)\"]\n    K --> L\n```"
    broken = "```mermaid\nflowchart TD\n    K[Info page\n    K --> L\n```"
    model = fakes.scripted(fakes.tool_call("write_sections", edits=[{"action": "new_section", "heading": "Routing", "text": diagram}]),
                           fakes.tool_call("write_sections", edits=[{"action": "new_section", "heading": "Other", "text": broken}]),
                           fakes.tool_call("finish_batch", summary="routing written"))
    document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"))
    written = document_knowledge_service.render_knowledge(state)
    harness.check("the label is quoted, once", 'K["Info page (conditional)"]' in written, written)
    harness.check("a quoted label stays as it was", 'L["Kept (quoted)"]' in written, written)
    harness.check("the fence stays a fence", "```mermaid\nflowchart TD" in written, written)
    answers = [str(message.content) for message in model.calls[-1] if isinstance(message, ToolMessage)]
    harness.check("a box left open is refused with what is wrong", "Mermaid cannot draw" in answers[1], answers[1])
    harness.check("and never reaches the document", "Other" not in written, written)


def test_a_part_that_repeats_itself_is_told_so_and_goes_on_after_a_stop_defect():
    # Defect: a worker read one section five times unchanged, sent 15 removes of blocks already gone and six replaces
    # with no text, and a run stopped mid-part began the part again. Fix: each is answered with what to do; a resume goes on
    paths, state = _start_part()
    read_section = fakes.tool_call("read_section", section="K1")
    model = fakes.scripted(
        fakes.tool_call("write_sections", edits=[{"action": "new_section", "heading": "Routing",
                                                  "text": "The router picks the ledger shard."}]),
        read_section, read_section, read_section,
        fakes.tool_call("write_sections", edits=[{"action": "remove", "block": "K1.9"},
                                                 {"action": "replace", "block": "K1.1", "text": ""}]),
        fakes.tool_call("finish_batch", summary="routing written"))
    is_closed, calls, reason = document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model,
                                                                    log=logging.getLogger("test"))
    harness.check("the part closes", is_closed and calls == 6, f"{calls} {reason}")
    # each result opens with its mark, `[M<n>] `, which the worker names to prune it
    answers = [re.sub(r"^\[M\d+\] ", "", str(message.content)) for message in model.calls[-1]
               if isinstance(message, ToolMessage)]
    harness.check("read twice whole, the third refused with what to do instead",
                  "ledger shard" in answers[1] and "ledger shard" in answers[2]
                  and answers[3].startswith("Refused: you read K1 twice"), str(answers))
    harness.check("the block already gone names what the section holds, the empty text names remove",
                  "which holds K1.1 now" in answers[4] and '"action": "remove"' in answers[4], answers[4])

    # the same part started again goes on from its saved conversation, its section's reads remembered
    resumed = fakes.scripted(read_section, fakes.tool_call("finish_batch", summary="closed again"))
    is_closed, calls, reason = document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=resumed,
                                                                    log=logging.getLogger("test"))
    first_call = resumed.calls[0]
    harness.check("the resume sends the saved conversation, not the first message alone",
                  is_closed and len(first_call) > 2 and calls == 8, f"{len(first_call)} {calls} {reason}")
    again = [re.sub(r"^\[M\d+\] ", "", str(message.content)) for message in resumed.calls[-1]
             if isinstance(message, ToolMessage)][-1]
    harness.check("what it read before the stop still counts", again.startswith("Refused: you read K1 twice"), again)
    harness.check("the conversation is on disk",
                  document_reshaping_service.build_conversation_path(paths, 1).is_file(), "")
