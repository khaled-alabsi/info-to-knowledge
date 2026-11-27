"""A PDF's figures placed unread where they stood, on a PDF made for it
(tests/resources/pdf/build_boxed_text_pdf.py): phase 1 reads it, phase 2 renders its figures and keeps them apart from
phase 3 when there are more than the limit, and publishing places each before the paragraph that followed it."""

import importlib.util
import tempfile
from pathlib import Path

from support import harness

from graph.nodes import prepare_speech_node
from services import pdf_source_service
from services import publication_service
from utils import document_block_utils
from utils import speech_markdown_utils

BUILDER = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "build_boxed_text_pdf.py"


def _build_pdf(folder: Path) -> Path:
    spec = importlib.util.spec_from_file_location("build_boxed_text_pdf", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    return builder.build(folder / "boxed-text.pdf")


def test_a_pdf_s_figures_above_the_limit_are_placed_where_they_stood():
    folder = Path(tempfile.mkdtemp())
    pdf = _build_pdf(folder)

    # 1. phase 1 reads the PDF; phase 2 renders its figures and, above the limit, keeps every one from phase 3
    transcript, _removed = pdf_source_service.build_page_transcript(pdf, is_read_as_pages=False)
    items = pdf_source_service.render_figures(pdf, transcript, folder / "frames")
    read, placed = pdf_source_service.split_figures_to_read(items=items, figure_reading="place-above-limit", read_limit=1)
    harness.check("three figures, none to read", (len(items), len(read), len(placed)) == (3, 0, 3), str(items))
    lines = speech_markdown_utils.list_numbered_lines(transcript)
    marked = prepare_speech_node._mark_place_before(figures=placed, lines=lines)

    # 2. a chapter of the PDF's paragraphs, each citing its own id, as phase 4 writes one
    paragraphs = [line["text"] for line in lines]
    markdown = "# Onboarding\n\n## Executive summary\n\nThe partner signs in.\n\n## Onboarding\n\n" + "\n\n".join(paragraphs)
    index = {" ".join(line["text"].split()): [line["id"]] for line in lines}
    index["The partner signs in."] = [lines[0]["id"]]
    blocks = document_block_utils.split_into_blocks(markdown, index)

    # 3. publishing places each figure
    placed_blocks, cuts = publication_service._add_placed_figures(output_root=folder, blocks=blocks, placed_figures=marked)
    order = [block["text"][:40] if block["kind"] != "image" else f"FIGURE p{marked[int(block['id'][1:]) - 1]['page']}"
             for block in placed_blocks if block["kind"] in ("paragraph", "image")]
    screenshot = order.index("FIGURE p2")
    harness.check("the page 2 screenshot stands before the sentence under it, in the chapter, not in the summary",
                  order[screenshot + 1].startswith("The partner signs in and sees") and screenshot > 1, str(order))
    harness.check("the bar chart, after the last paragraph, closes the chapter", order[-1] == "FIGURE p4", str(order))
    harness.check("each figure's file is written as a PNG",
                  all((folder / cut["file"]).suffix == ".png" and (folder / cut["file"]).is_file() for cut in cuts.values()),
                  str(cuts))
