"""Phase 7's knowledge pass (the PDF clean-up workflow's second pass): a worker writes the facts of each part of the
published document into one knowledge document of one section per subject, and the program applies each edit or
refuses it with its reason."""

import base64
import io
import logging
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from PIL import Image
from pydantic import ValidationError

from support import fakes, harness

import config as app_config
from models import output_contract as output_model_contract
from models.run_paths import RunPaths
from models.web_api_request import StartJobsRequest
from prompts import system_prompt_builder
from services import document_reshaping_service
from services import document_folder_service
from services import document_knowledge_service
from services import profile_version_service
from services import recording_medium_service
from services import run_artifact_service
from utils import cleanup_block_utils
from utils import mermaid_text_utils

FIGURE = "![the gateway](resources/figure-01.png)"
DOCUMENT = f"""# Gateway guide

ACME-101 (Sprint 4, done, Jonas): the account opens after the identity check.

{FIGURE}
*Figure 1: the gateway*

```
# install the gateway
gateway install --tenant 7
```
"""


def _build_state() -> dict:
    """A knowledge pass's state as `start_knowledge` makes it, before any section."""

    blocks = cleanup_block_utils.list_blocks(DOCUMENT)
    return {"pass": "knowledge", "wishes": "", "front": "", "blocks": blocks, "next_id": len(blocks) + 1,
            "batches": [[block["id"] for block in blocks]], "closed": {}, "title": "# Gateway guide",
            "sections": [], "next_section": 1,
            "size": {"chars": len(DOCUMENT), "tokens": 1, "blocks": len(blocks), "figures": 1}}


def test_a_look_at_a_text_block_says_it_holds_no_figure_defect():
    # Defect: a look at a table or a bold line was refused as "holds no figure whose image the document has", which
    # read as a missing image. Fix: the refusal says the block is text with no figure line
    state = _build_state()
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    text_block = next(block["id"] for block in state["blocks"] if block["text"].startswith("ACME"))
    reply, image = document_knowledge_service._answer_look_figure(paths=paths, state=state,
                                                                 args={"block": f"B{text_block}"}, read_ids={text_block})
    harness.check("named as text with no figure line, no image", image is None and "is text and holds no figure line"
                  in reply, reply)


def test_a_new_subject_becomes_a_section_and_its_heading_is_never_taken_twice():
    state = _build_state()
    read: set[int] = set()
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=read, edits=[
        {"action": "new_section", "heading": "## Account opening",
         "text": "The account opens after the identity check."},
        {"action": "new_section", "heading": "account opening", "text": "The identity check needs a passport."}])
    harness.check("one section, its heading without the marks", [section["heading"] for section in state["sections"]]
                  == ["Account opening"], str(state["sections"]))
    harness.check("a second section with the same heading is refused, naming the first",
                  len(refused) == 1 and "K1 already has the heading" in refused[0], str(refused))
    harness.check("a section made in this part counts as read", read == {1}, str(read))


def test_a_reader_gets_a_sentence_out_of_a_mermaid_fence_defect():
    # Defect: a sentence marked as Mermaid, and a sentence a diagram's unclosed fence ran into, reached the reader as "This
    # diagram could not be drawn". Fix: for a finished document the sentence stands as text, the diagram stays a diagram
    sentence = "The token service renews each session once an hour, for every partner that asks."
    harness.check("a fence holding a sentence is opened",
                  mermaid_text_utils.open_prose_fences(f"```mermaid\n{sentence}\n```") == sentence)
    opened = mermaid_text_utils.open_prose_fences(f"```mermaid\nsequenceDiagram\n    A->>B: Hi\n\n{sentence}\n```")
    harness.check("the diagram keeps its fence", opened.startswith("```mermaid\nsequenceDiagram\n    A->>B: Hi\n```\n"), opened)
    harness.check("the sentence stands after it", opened.endswith(sentence), opened)
    whole = "```mermaid\nflowchart TD\n    A --> B\n```"
    harness.check("a whole diagram stays as it is", mermaid_text_utils.open_prose_fences(whole) == whole)


def test_the_common_mermaid_mistakes_are_mended_and_each_mended_diagram_passes_the_check_defect():
    # Defect: a mistake the program could mend was refused or reached the reader as an error. Fix: `repair_diagram` mends
    # each one, and the check that would refuse it then finds nothing
    cases = [('flowchart TD\n    K[Info page (optional)] --> L["Kept"]', 'K["Info page (optional)"]'),
             ('flowchart TD\n    A -->|Sends invoice (paper)| B', '|"Sends invoice (paper)"|'),
             ('flowchart TD\n    Poll["Poll"] -->|returns "OK"| Wait["Wait"]', "|returns OK|"),
             ('flowchart TD\n    Poll["Poll"] -->|"returns"| Wait["Wait"]', '|"returns"|'),
             ('flowchart TD\n    S(()) --> A["Go"]', 'S(("S"))'),
             ('flowchart TD\n    S{{}} --> A["Go"]', 'S{{"S"}}'),
             ('flowchart TD\n    A["Go"] --> end["Done"]', 'End["Done"]'),
             ('flowchart TD\n    direction XY\n    A["Go"] --> B["Stop"]', 'A["Go"]'),
             ('flowchart TD\n    F[Check: first\n    second line]\n    F --> G["Go"]', 'F[Check: first second line]'),
             ('flowchart TD\n    K["Page open] --> L["Kept"]', 'K["Page open"]'),
             ('flowchart TD\n    K["The "open" page & "closed" page"] --> L["Kept"]', 'K["The open page & closed page"]'),
             ('flowchart TD\n    O1 --> V1["[0.5, 0.4]"]', 'V1["[0.5, 0.4]"]'),
             # DEFECTS 716: a stray quote inside a box label that also holds brackets
             ('flowchart TD\n    Hub --> Q["Queue\\n(Accounts [Topic"], Cards [Topic])"]',
              'Q["Queue\\n(Accounts [Topic], Cards [Topic])"]'),
             ('flowchart TD\n    A["One"] --> B<br/>  C["Two"] --> B', '--> B\nC["Two"]'),
             ('flowchart LR A["One"] -->|"Go"| B["Two"] B -->|"On"| C["Three"]', 'B["Two"]\nB -->'),
             ('flowchart TD\n    A --> B\n    B -->|Yes|\n    C{Done}', 'B -->|Yes| C{Done}'),
             ('flowchart TD\n    subgraph G_a["Group"]\n    Direction lr\n    A["Go"]\n    end', "    direction LR\n"),
             # DEFECTS 701: the real diagram wrapped in a fence, a "(flowchart TD)" label, a note and a second fence
             ('```mermaid\n(flowchart TD)\n```mermaid\nflowchart TD\n    A["Go"] --> B["Stop"]\n```\n*Note: drawn from the '
              'slide*\n```', 'flowchart TD\n    A["Go"] --> B["Stop"]'),
             # A label opening as a list item, drawn by Mermaid as "Unsupported markdown: list"
             ('flowchart LR\n    B -->|"2. generate embedding"| M["AI model"]', '|"(2) generate embedding"|'),
             ('flowchart LR\n    B -->|3) return| M["- Layer 1"]', '|"(3) return"| M["Layer 1"]'),
             ('flowchart LR\n    A[1. Install] --> B["1.5 GB kept"]', 'A[(1) Install] --> B["1.5 GB kept"]'),
             ('flowchart TD\n    subgraph G["Group"]\n        A["The end here"]\n    end\n    A --> B["Stop"]', "    end\n")]
    for source, expected in cases:
        mended = mermaid_text_utils.repair_diagram(source)
        harness.check(f"mended: {source.splitlines()[-1].strip()}", expected in mended, mended)
        harness.check(f"then drawable: {source.splitlines()[-1].strip()}",
                      not mermaid_text_utils.find_syntax_problems(mended), mended)
        harness.check(f"mended once: {source.splitlines()[-1].strip()}",
                      mermaid_text_utils.repair_diagram(mended) == mended, mended)
    harness.check("the words in quotes keep their `end`", "The end here" in mermaid_text_utils.repair_diagram(cases[-1][0]), "")
    other = 'sequenceDiagram\n    A->>B: Hello (there)'
    harness.check("another kind of diagram stays", mermaid_text_utils.repair_diagram(other) == other, "")
    fenced = 'Before.\n\n```mermaid\nflowchart TD\n    K[Info (x)]\n```\n\nAfter.'
    harness.check("a document's fence stays a fence, its words around it stay",
                  mermaid_text_utils.repair_fenced_blocks(fenced) == 'Before.\n\n```mermaid\nflowchart TD\n    K["Info (x)"]\n```\n\nAfter.',
                  mermaid_text_utils.repair_fenced_blocks(fenced))


def test_every_worker_that_writes_a_diagram_is_sent_the_mermaid_rules_defect():
    # Defect: the Mermaid rules stood only in phases 3 and 4's instructions and named no box label, so a knowledge part's
    # flowchart reached the reader as an error. Fix: one shared file, in every instruction that can write a diagram
    for workflow in ("video", "audio", "pdf"):
        previous = system_prompt_builder.RUN_WORKFLOW
        system_prompt_builder.set_run_workflow(workflow)
        try:
            for step in ("frames_processing", "second_pass", "write", "section", "review", "document_cleanup", "document_chat"):
                text = system_prompt_builder.build_system_prompt(step)
                harness.check(f"{workflow} {step} gets the rules", "Write every label in double quotes" in text
                              and 'Info["Info page (optional)"]' in text, text[:100])
        finally:
            system_prompt_builder.set_run_workflow(previous)
    knowledge = system_prompt_builder.build_system_prompt("document_knowledge", {"workflow": "pdf"})
    harness.check("the knowledge pass gets the rules", "Write every label in double quotes" in knowledge, knowledge[:100])
    harness.check("the rules' own diagrams pass the program's check", not mermaid_text_utils.find_syntax_problems(
        'flowchart TD\n    Order["Sends invoice (paper)"] -->|"Pays (by card)"| Bill["Billing"]'), "")


def test_a_section_read_a_third_time_unchanged_is_refused_and_refusals_say_what_to_do_defect():
    # Defect: a worker read one section five times in a row, unchanged (about 50 minutes of calls), sent 15 removes of
    # blocks it had removed before, and six replaces with no text, each refused with no hint what to do. Fix: a third
    # unchanged read is refused; a missing block names the blocks the section holds; an empty text says to send remove
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    state = _build_state()
    document_knowledge_service.apply_section_edits(state=state, read_sections=set(), edits=[
        {"action": "new_section", "heading": "Account opening", "text": "The account opens after the identity check."}])
    read: dict[str, set[int]] = {"blocks": set(), "sections": set()}
    call = {"name": "read_section", "args": {"section": "K1"}, "id": "c"}
    answers = [document_knowledge_service._answer_tool(paths=paths, state=state, number=1, call=call, read=read)[0]
               for _ in range(3)]
    harness.check("read twice whole, the third read refused",
                  "identity check" in answers[0] and "identity check" in answers[1]
                  and answers[2].startswith("Refused: you read K1 twice"), str(answers))
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections={1}, edits=[
        {"action": "remove", "block": "K1.7"}, {"action": "replace", "block": "K1.1", "text": ""}])
    harness.check("a block the section no longer holds names what it holds; an empty text names remove",
                  "which holds K1.1 now" in refused[0] and '"action": "remove"' in refused[1], str(refused))
    document_knowledge_service.apply_section_edits(state=state, read_sections={1}, edits=[
        {"action": "add", "section": "K1", "text": "The identity check needs a passport."}])
    changed = document_knowledge_service._answer_tool(paths=paths, state=state, number=1, call=call, read=read)[0]
    harness.check("once it changed, it is read whole again", "needs a passport" in changed, changed)


def test_a_section_is_changed_only_after_it_was_read_in_this_part():
    state = _build_state()
    document_knowledge_service.apply_section_edits(state=state, read_sections=set(), edits=[
        {"action": "new_section", "heading": "Account opening", "text": "The account opens after the identity check."}])
    next_part_read: set[int] = set()
    add = {"action": "add", "section": "K1", "text": "The identity check needs a passport."}
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=next_part_read,
                                                                     edits=[add])
    harness.check("an add to a section the part never read is refused", refused and "read K1" in refused[0],
                  str(refused))
    next_part_read.add(1)
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=next_part_read, edits=[
        add, {"action": "replace", "block": "K1.1", "text": "The account opens once the identity check passed."},
        {"action": "rename", "section": "K1", "heading": "Account opening and identity check"}])
    texts = [block["text"] for block in state["sections"][0]["blocks"]]
    harness.check("read, the add, the replace and the rename apply", not refused and texts == [
        "The account opens once the identity check passed.", "The identity check needs a passport."]
                  and state["sections"][0]["heading"] == "Account opening and identity check", str(refused) + str(texts))
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=next_part_read, edits=[
        {"action": "remove", "block": "K1.2"}, {"action": "remove", "block": "K1.2"}])
    harness.check("a block removed once is gone, a second remove refused by name",
                  len(state["sections"][0]["blocks"]) == 1 and len(refused) == 1 and "K1.2" in refused[0], str(refused))


def test_a_figure_placed_with_its_own_caption_keeps_the_document_s_line_defect():
    # Defect: a worker placed a figure the document holds under a caption of its own ("Figure on page 1024"), was refused
    # as "in no block of the document", and spent a turn finding the line. Fix: the file names the figure, its line is used
    state = _build_state()
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=set(), edits=[
        {"action": "new_section", "heading": "Gateway", "text": "The gateway.\n![Figure on page 3](resources/figure-01.png)"}])
    written = "\n".join(block["text"] for block in state["sections"][0]["blocks"]) if state["sections"] else ""
    harness.check("the figure is placed with no refusal", refused == [], str(refused))
    harness.check("it keeps the document's own caption", FIGURE in written and "Figure on page 3" not in written, written)


def test_a_figure_line_must_be_the_document_s_own_and_a_mermaid_block_a_diagram():
    state = _build_state()
    read: set[int] = set()
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=read, edits=[
        {"action": "new_section", "heading": "Gateway", "text": f"{FIGURE}\n*Figure 1: the gateway*"},
        {"action": "new_section", "heading": "Routing", "text": "![a router](resources/figure-09.png)"},
        {"action": "new_section", "heading": "Flow", "text": "```mermaid\nthe router sends the request\n```"},
        {"action": "new_section", "heading": "Flow chart",
         "text": "```mermaid\nflowchart LR\n  A[client] --> B[gateway]\n```"}])
    headings = [section["heading"] for section in state["sections"]]
    harness.check("the document's figure and the real diagram are kept", headings == ["Gateway", "Flow chart"],
                  str(headings))
    harness.check("an invented figure line and a Mermaid block that is no diagram are refused, each saying why",
                  len(refused) == 2 and "figure-09.png" in refused[0] and "no Mermaid diagram" in refused[1],
                  str(refused))


def test_a_top_heading_in_a_section_s_text_is_refused_but_a_code_comment_is_not():
    state = _build_state()
    refused, _notes = document_knowledge_service.apply_section_edits(state=state, read_sections=set(), edits=[
        {"action": "new_section", "heading": "Setup", "text": "## Install\n\nRun the installer."},
        {"action": "new_section", "heading": "Install", "text": "```\n# install the gateway\ngateway install\n```"},
        {"action": "new_section", "heading": "Limits", "text": "### Per tenant\n\nTen requests a second."}])
    headings = [section["heading"] for section in state["sections"]]
    harness.check("a level 2 heading inside the text is refused; a code comment and a ### heading are kept",
                  len(refused) == 1 and "level 1 or 2" in refused[0] and headings == ["Install", "Limits"],
                  str(refused) + str(headings))


def test_the_knowledge_document_is_the_title_then_each_section_under_its_heading():
    state = _build_state()
    document_knowledge_service.apply_section_edits(state=state, read_sections=set(), edits=[
        {"action": "new_section", "heading": "Account opening", "text": "The account opens after the identity check."},
        {"action": "new_section", "heading": "Gateway", "text": f"{FIGURE}\n*Figure 1: the gateway*"}])
    harness.check("rendered in order", document_knowledge_service.render_knowledge(state) == (
        "# Gateway guide\n\n## Account opening\n\nThe account opens after the identity check.\n\n## Gateway\n\n"
        f"{FIGURE}\n*Figure 1: the gateway*\n"), document_knowledge_service.render_knowledge(state))


def test_a_worker_merges_splits_moves_and_removes_what_earlier_parts_wrote_defect():
    # Defect: a worker could only add, replace or remove a block and rename a heading; a merge done by hand left the
    # emptied heading behind, and no edit took a section out. Fix: merge, split, move and remove_section, and a section
    # left with no block is taken out
    state = _build_state()
    read: set[int] = set()
    document_knowledge_service.apply_section_edits(state=state, read_sections=read, edits=[
        {"action": "new_section", "heading": "Refunds", "text": "A refund is paid within ten days."},
        {"action": "new_section", "heading": "Refund limits", "text": "A refund is at most 500 euros."},
        {"action": "new_section", "heading": "Gateway",
         "text": "The gateway routes each call.\n\nA refund needs the account owner's approval.\n\nTenants have quotas."},
        {"action": "new_section", "heading": "Old notes", "text": "Nothing true about the business."}])
    refused, notes = document_knowledge_service.apply_section_edits(state=state, read_sections=read, edits=[
        {"action": "merge", "section": "K2", "into": "K1"},
        {"action": "move", "block": "K3.2", "section": "K1"},
        {"action": "split", "block": "K3.3", "heading": "Tenants"},
        {"action": "remove_section", "section": "K4"},
        {"action": "merge", "section": "K1", "into": "K1"}])
    shape = [(section["heading"], [block["text"] for block in section["blocks"]]) for section in state["sections"]]
    harness.check("one Refunds section with all three refund facts, the gateway and its tenants apart, old notes gone",
                  shape == [("Refunds", ["A refund is paid within ten days.", "A refund is at most 500 euros.",
                                         "A refund needs the account owner's approval."]),
                            ("Gateway", ["The gateway routes each call."]),
                            ("Tenants", ["Tenants have quotas."])], str(shape))
    harness.check("each moved block's new name is told", "K2 merged into K1 as K1.2 to K1.2" in notes
                  and "K3.2 is now K1.3" in notes and any(note.startswith("K3.3 onward is now K5 Tenants")
                                                         for note in notes), str(notes))
    harness.check("a merge of a section into itself is refused", len(refused) == 1 and "itself" in refused[0],
                  str(refused))
    harness.check("no merged heading is left in the document",
                  "Refund limits" not in document_knowledge_service.render_knowledge(state))
    refused, notes = document_knowledge_service.apply_section_edits(state=state, read_sections=read, edits=[
        {"action": "remove", "block": "K5.1"}])
    harness.check("a section whose last block is removed is taken out, and said so",
                  [section["heading"] for section in state["sections"]] == ["Refunds", "Gateway"]
                  and notes == ["K5 Tenants had no block left and was removed"], str(notes))


def test_the_worker_gets_its_part_in_its_first_message_and_closes_it_without_reading_it_again_defect():
    # Defect: the first message named the part's blocks and the worker had to read them with a tool, one call per
    # part for text the program held; closing before that read was refused. Fix: the part is in the first message
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    state = document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})
    model = fakes.scripted(fakes.tool_call("finish_batch", summary="nothing to write"))
    is_closed, calls, reason = document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model,
                                                                    log=logging.getLogger("test"))
    first = str(model.calls[0][1].content)
    harness.check("the part's text is in the worker's first message",
                  "the account opens after the identity check" in first and "{B1}" in first, first[-400:])
    harness.check("the part closes on the first call, with no read", is_closed and calls == 1, f"{calls} {reason}")


def test_a_stopped_part_goes_on_with_its_saved_conversation_defect():
    # Defect: a run stopped mid-part began the part again from its first message, losing hours of a large document's
    # part. Fix: the conversation is saved after every turn and a resume goes on with it, its calls counted on
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    state = document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})
    document_reshaping_service.save_conversation(paths, 1, [SystemMessage(content="the instructions"),
                                                          HumanMessage(content="the part"),
                                                          AIMessage(content="", tool_calls=[{"name": "read_knowledge_outline",
                                                                                             "args": {}, "id": "c1"}]),
                                                          ToolMessage(content="(no section)", tool_call_id="c1")],
                                               {"blocks": set(), "sections": set()})
    is_closed, calls, reason = document_knowledge_service.run_batch(paths=paths, state=state, number=1,
                                                                    model=fakes.scripted(fakes.tool_call(
                                                                        "finish_batch", summary="x")),
                                                                    log=logging.getLogger("test"))
    saved =document_reshaping_service.read_conversation(paths, 1)
    harness.check("the part closes on its second call, going on from the saved turn",
                  is_closed and calls == 2 and str(saved[0][1].content) == "the part", f"{calls} {reason}")


def test_the_tools_are_the_outline_the_reads_a_figure_the_writes_the_close_and_the_report():
    names = [spec["function"]["name"] for spec in document_knowledge_service.build_tool_specs()]
    harness.check("eight tools", names == ["read_knowledge_outline", "read_blocks", "read_section", "look_figure",
                                           "write_sections", "finish_batch", "prune_results", "report_problem"], str(names))


def test_a_document_pass_worker_reports_an_instruction_to_improve_at_no_cost_of_its_calls_defect():
    # Defect: phase 7's workers had no way to tell the people building the program which instruction or tool could be
    # better. Fix: both passes offer report_problem, its instructions ask for improvements, and a report costs no call
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    state = document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})
    model = fakes.scripted(fakes.tool_call("report_problem", kind="insufficient_tool",
                                           summary="read_section could take several sections at once"),
                           fakes.tool_call("finish_batch", summary="nothing to write"))
    settings = app_config.settings
    saved = settings.document_reshaping
    settings.document_reshaping = saved.model_copy(update={"batch_calls": 1})
    try:
        is_closed, calls, reason = document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model,
                                                                        log=logging.getLogger("test"))
    finally:
        settings.document_reshaping = saved
    feedback = (folder / output_model_contract.WORKER_FEEDBACK_FILE).read_text(encoding="utf-8")
    instructions = str(model.calls[0][0].content)
    harness.check("the report is filed where the detail view reads it", "several sections at once" in feedback
                  and "phase 7 · part 1" in feedback, feedback)
    harness.check("a report costs the part none of its one call", is_closed, f"{calls} {reason}")
    harness.check("the worker is asked for what would make its work better",
                  "Report what would make your work better" in instructions, instructions[-600:])
    cleanup_names = [spec["function"]["name"] for spec in document_reshaping_service.build_tool_specs()]
    harness.check("the clean up and the rewrite offer it too", "report_problem" in cleanup_names, str(cleanup_names))


def test_a_knowledge_pass_is_a_pass_the_job_request_takes_defect():
    # Defect: the page's "Queue the knowledge pass" was refused, "422: Input should be '', 'cleanup' or 'rewrite'"
    # Fix: the job request checks the pass against the program's one list of passes, not its own copy of it
    items = [{"video": "/inbox/a.pdf", "output": "/output/a"}]
    request = StartJobsRequest(items=items, only_phase=7, document_pass="knowledge")
    harness.check("knowledge is taken", request.document_pass == "knowledge", str(request))
    try:
        StartJobsRequest(items=items, only_phase=7, document_pass="polish")
        refused = ""
    except ValidationError as error:
        refused = str(error)
    harness.check("a pass the program has not is still refused, naming every pass", "knowledge" in refused
                  and "polish" in refused, refused)


def test_the_versions_leave_out_the_drafts_after_each_part_and_name_the_original_by_its_processing_defect():
    # Defect: the Open document menu and the download dialog listed a draft after every part of a knowledge pass, and
    # the first version only as "original". Fix: a pass's drafts are no version (a running part's is read in its live
    # panel), and the original is named by how it was made: "markdown conversion (original)"
    folder = Path(tempfile.mkdtemp())
    pass_dir = folder / "07_reshaping"
    (pass_dir / "parts").mkdir(parents=True)
    (pass_dir / "before.md").write_text(DOCUMENT, encoding="utf-8")
    (pass_dir / "state.json").write_text('{"pass": "knowledge", "batches": [[1], [2], [3]], "closed": {"1": "a", "2": "b"}}',
                                         encoding="utf-8")
    document = folder / "guide" / "guide.md"
    document.parent.mkdir()
    document.write_text("# Guide\n", encoding="utf-8")
    for number in (1, 2):
        (pass_dir / "parts" / f"part-{number:03d}.md").write_text(f"draft {number}", encoding="utf-8")
    running = profile_version_service.list_document_versions(folder, document)
    harness.check("while it runs: the original by its profile, then the document as it stands, no draft", [
        (version["number"], version["label"], version["path"]) for version in running] == [
        (1, "knowledge (original)", "07_reshaping/before.md"),
        (2, "after knowledge reshaping (not finished)", "guide/guide.md")], str(running))
    recording_medium_service.write_chosen_medium(folder, output_model_contract.PDF_MARKDOWN_CONVERSION_MEDIUM)
    (pass_dir / "report.md").write_text("# Document knowledge\n", encoding="utf-8")
    finished = profile_version_service.list_document_versions(folder, document)
    harness.check("a PDF's original by its processing, once the pass ends",
                  [version["label"] for version in finished] == ["markdown conversion (original)",
                                                                 "after knowledge reshaping"], str(finished))


def test_every_document_version_opens_in_the_reader_defect():
    # Defect: the Open document menu listed the original and each pass's document, yet clicking one opened nothing
    # Fix: the run's file list, the only files the reader shows, holds every version the menu lists
    folder = Path(tempfile.mkdtemp())
    pass_dir = folder / "07_reshaping"
    (pass_dir / "parts").mkdir(parents=True)
    (pass_dir / "before.md").write_text(DOCUMENT, encoding="utf-8")
    (pass_dir / "state.json").write_text('{"pass": "knowledge", "batches": [[1], [2]]}', encoding="utf-8")
    (pass_dir / "parts" / "part-001.md").write_text("draft 1", encoding="utf-8")
    document = folder / "guide" / "guide.md"
    document.parent.mkdir()
    document.write_text("# Guide\n", encoding="utf-8")
    groups = {group["key"]: [item["path"] for item in group["items"]]
              for group in run_artifact_service.list_artifacts(folder)}
    harness.check("the Document group holds the published document first, then every other version", groups.get(
        "document") == ["guide/guide.md", "07_reshaping/before.md", "07_reshaping/parts/part-001.md"], str(groups))


def test_a_run_with_one_document_lists_it_as_its_one_version():
    # The Open document menu showed only "Download document" until a pass ran; it now lists the one document as a
    # version, with its download button on its row, the same rows as after a pass
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    versions = profile_version_service.list_document_versions(folder, document)
    harness.check("one version, the published document", versions == [
        {"number": 1, "label": "knowledge (original)", "path": document.relative_to(folder).as_posix()}], str(versions))
    harness.check("no version while no document is published", profile_version_service.list_document_versions(
        folder, None) == [])


def test_a_part_s_figures_reach_the_worker_as_images_in_its_first_message_defect():
    # Defect: a part's figures reached the worker as lines of text, their images only through a tool it never called
    # Fix: each figure of the part whose image the document has follows as an image in the part's first message
    from PIL import Image
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text("# Gateway\n", encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    (document.parent / "resources").mkdir(parents=True)
    Image.new("RGB", (40, 30), "white").save(document.parent / "resources" / "figure-01.png")
    state = {"size": {"chars": 90, "tokens": 20, "blocks": 4, "figures": 2}, "sections": [], "batches": [[1, 2, 3], [4]],
             "blocks": [{"id": 1, "text": "# Gateway"}, {"id": 2, "text": "![the router](resources/figure-01.png)"},
                        {"id": 3, "text": "![a figure with no image](resources/figure-09.png)"},
                        {"id": 4, "text": "The router picks the shard."}]}

    first = document_knowledge_service._build_batch_message(paths=paths, state=state, number=1).content
    harness.check("the part's text, then its one figure whose image exists, under its block",
                  isinstance(first, list) and [part["type"] for part in first] == ["text", "text", "image_url"]
                  and first[1]["text"] == "The figure of B2 (figure-01.png):", str(first)[:300])
    second = document_knowledge_service._build_batch_message(paths=paths, state=state, number=2).content
    harness.check("a part with no figure is its text alone", isinstance(second, str) and "B4" in second, str(second)[:200])
    # a part of many large figures: those past the token budget are named for look_figure, never sent past the window
    for index in range(2, 42):
        Image.new("RGB", (1600, 2400), "white").save(document.parent / "resources" / f"figure-{index:02d}.png")
    many = {**state, "batches": [list(range(1, 42))], "blocks": [
        {"id": index, "text": f"![figure {index}](resources/figure-{index:02d}.png)"} for index in range(1, 42)]}
    crowded = document_knowledge_service._build_batch_message(paths=paths, state=many, number=1).content
    images = sum(1 for part in crowded if part["type"] == "image_url")
    harness.check("the images sent stay within the budget, the others named for look_figure",
                  0 < images < 41 and "did not fit in this message: look at each with look_figure" in crowded[0]["text"]
                  and "B41" in crowded[0]["text"], str(images))


def test_a_small_figure_is_counted_by_its_size_not_as_a_whole_frame_defect():
    # Defect: each figure in a part's conversation counted 1,000 tokens, so a part of 148 small figures read as 187,635
    # tokens where the host counted 93,639, and was compacted on every turn. Fix: a figure counts by its pixels
    buffer = io.BytesIO()
    Image.new("RGB", (280, 280), "white").save(buffer, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    message = HumanMessage(content=[{"type": "text", "text": ""}, {"type": "image_url", "image_url": {"url": url}}])
    count = document_reshaping_service.estimate_conversation_tokens([message])
    harness.check("a 280 by 280 figure counts 100 tokens, one per 28 by 28 pixels", count == 100, str(count))


def test_a_part_s_worker_is_warned_and_prunes_what_it_chooses_and_nothing_else_changes_defect():
    # Defect: the program cleared a part's earlier reads on its own past the size limit, each clear made the model host
    # read the whole prompt again, and it dropped what the worker still needed. Fix: warned, the worker prunes what it names
    limit = app_config.settings.document_reshaping.conversation_size_limit_tokens
    chars = app_config.settings.document_reshaping.chars_per_token
    figure = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [SystemMessage(content="instructions"), HumanMessage(content=[{"type": "text", "text": "part 1"}, figure])]
    read_text = "The ledger shard holds the tenant's postings. " * int(limit * 0.36 * chars / 46)
    read: dict = {}
    for turn in range(2):
        # the first read alone fills about 36%, under the first warning (the owner: "Make first at 70")
        if turn == 1:
            harness.check("under 70% no warning", document_reshaping_service.warn_conversation_size(messages, read) == "", "")
        messages.append(AIMessage(content="", tool_calls=[{"name": "read_section", "args": {"section": "K1"}, "id": f"r{turn}"}]))
        messages.append(ToolMessage(content=document_reshaping_service.mark_result(messages, read_text), tool_call_id=f"r{turn}"))
    warning = document_reshaping_service.warn_conversation_size(messages, read)
    harness.check("past 70% the worker is told how full it is and to prune", "Context check" in warning
                  and document_reshaping_service.PRUNE_TOOL in warning and "not a request to finish" in warning, warning)
    harness.check("each warning comes once", document_reshaping_service.warn_conversation_size(messages, read) == "", "")
    harness.check("each result carries its mark", messages[3].content.startswith("[M3] "), messages[3].content[:20])
    before = [message.content for message in messages]
    answer = document_reshaping_service.prune_results(messages, ["M3", "M0", "M9"])
    harness.check("the result named is pruned to one line", messages[3].content.startswith("(M3 pruned by you"),
                  messages[3].content[:60])
    harness.check("every other message is left as it was",
                  [message.content for index, message in enumerate(messages) if index != 3]
                  == [content for index, content in enumerate(before) if index != 3], "")
    harness.check("the instructions and an unknown mark are refused by name", "Pruned M3" in answer
                  and "'M0' names no result" in answer and "'M9' names no result" in answer, answer)
    harness.check("full at the limit, not before", not document_reshaping_service.is_conversation_full(messages[:3]), "")
