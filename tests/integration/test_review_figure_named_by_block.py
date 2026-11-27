"""A review resolution recut a figure and named the figure's description block, copying that block's first words as
`replaces`; the validator refused it as giving no figure line (RESOLUTION_FIGURE_ACTION, DEFECTS 713). Through the real
block filling of a resolution against a review's working document written in a run folder, and the real figure
replacement the validator asks, on an invented document: the resolution acts on the figure's own line."""

import tempfile
from pathlib import Path

from support import harness
from models.run_paths import RunPaths
from services import review_file_service
from utils import document_edit_utils

DOCUMENT = ("# Gateway review\n\n## Gateway console\n\n![The Gateway console](resources/figure-01.png)\n\n"
            "*Figure 1. The Gateway console*\n\n> **What the figure shows:** the console lists three queues.\n\n"
            "## Refunds\n\nThe Gateway refunds an order within a day.\n")


def test_a_recut_naming_the_figure_s_description_block_acts_on_the_figure_defect():
    # The program takes the figure line above the block the resolution names, so the figure check finds it
    run = Path(tempfile.mkdtemp()) / "run"
    paths = RunPaths(repository=run, module="gateway.mp4", output_root_override=run)
    working = review_file_service.build_working_document_path(paths)
    working.parent.mkdir(parents=True)
    working.write_text(DOCUMENT, encoding="utf-8")
    numbered = document_edit_utils.render_numbered_document(DOCUMENT)
    harness.check("the description is block 3 of the document", "{block 3}\n> **What the figure shows:**" in numbered,
                  numbered)
    answer = {"status": "resolved", "figure_action": "recut", "block": 3, "cut": "cut-01",
              "replaces": "> **What the figure shows:** the console lists"}
    placed = review_file_service._fill_from_blocks(paths, answer)
    harness.check("the resolution names the figure's line", placed["replaces"] == "![The Gateway console]"
                  "(resources/figure-01.png)", str(placed))
    harness.check("which the figure check finds in the document",
                  document_edit_utils.replace_figure(DOCUMENT, placed["replaces"], "") is not None, "")


def test_a_sentence_copied_word_for_word_under_a_wrong_block_number_is_found_where_it_stands_defect():
    # A resolution copied the refund sentence and named the console's block; the program places it under Refunds,
    # where the edit then finds it (DEFECTS 714)
    run = Path(tempfile.mkdtemp()) / "run"
    paths = RunPaths(repository=run, module="gateway.mp4", output_root_override=run)
    working = review_file_service.build_working_document_path(paths)
    working.parent.mkdir(parents=True)
    working.write_text(DOCUMENT, encoding="utf-8")
    answer = {"status": "resolved", "block": 3, "replaces": "The Gateway refunds an order within a day."}
    placed = review_file_service._fill_from_blocks(paths, answer)
    harness.check("the sentence is placed under its own heading", placed["section"] == "Refunds", str(placed))
    harness.check("where the edit finds it",
                  document_edit_utils.find_quote(DOCUMENT, placed["section"], placed["replaces"]) is not None, "")
