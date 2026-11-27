"""Phase 1 of a PDF read as text only, on a PDF made for it (tests/resources/pdf/build_boxed_text_pdf.py): a wiki
export's tables of rounded cells, its grey file placeholders and a table holding one small screenshot were 1245
figures of one real PDF, most of them no figure (DEFECTS 667). The real reader reads the made PDF through
`build_page_transcript`, the step phase 1 runs, and lists its figures."""

import importlib.util
import tempfile
from pathlib import Path

from support import harness

from services import pdf_source_service

BUILDER = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "build_boxed_text_pdf.py"


def _build_pdf() -> Path:
    spec = importlib.util.spec_from_file_location("build_boxed_text_pdf", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    return builder.build(Path(tempfile.mkdtemp()) / "boxed-text.pdf")


def test_phase_1_lists_the_screenshots_and_the_chart_and_no_table_or_placeholder_defect():
    # A wiki export's tables, placeholders and a table round a small screenshot reached phase 3 as figures, 1245 of them
    # on one PDF. Now phase 1 lists only the real screenshot, the screenshot inside the table and the bar chart
    transcript, _removed = pdf_source_service.build_page_transcript(_build_pdf(), is_read_as_pages=False)
    listed = [(figure["page"], [round(value) for value in figure["box"]]) for figure in transcript.get("figures", [])]
    harness.check("three figures: the screenshot, the screenshot alone out of its table, the chart",
                  listed == [(2, [70, 360, 400, 560]), (3, [225, 205, 365, 275]), (4, [100, 200, 500, 417])], str(listed))
    table_text = " ".join(segment["text"] for segment in transcript["segments"])
    harness.check("the table's words stay in the page's text", "Gateway tokens" in table_text, table_text[:400])
