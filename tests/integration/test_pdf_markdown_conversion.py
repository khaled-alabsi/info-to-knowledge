"""A PDF's markdown conversion, with no model, run whole through
`i2k run --pdf-mode markdown-conversion` on
the made wiki export (tests/resources/pdf/out/01-confluence-export.pdf, every word invented): phases 1 and 2 read it,
phase 4 writes the document from its own text with its figure where it stood, and no phase calls a model."""

import json
import shutil
import tempfile
from pathlib import Path

from support import harness

import cli
from services import recording_hash_service

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "01-confluence-export.pdf"


def test_a_pdf_s_markdown_conversion_publishes_its_own_text_with_its_figure():
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    pdf = root / PDF.name
    shutil.copy(PDF, pdf)
    run = root / "run"
    code = cli.main(["run", "--force", "--pdf-mode", "markdown-conversion", "--video", str(pdf), "--output", str(run)])
    harness.check("the run ends well", code == 0, str(code))

    record = json.loads((run / "02_frames.json").read_text(encoding="utf-8"))
    harness.check("no figure is read", record["items"] == [] and len(record["placedFigures"]) == 1, str(record.keys()))
    harness.check("nothing is sent to a model", not (run / "calls").exists() or not any((run / "calls").iterdir()))
    documents = [path for path in run.glob("*/*.md")]
    harness.check("one published document", len(documents) == 1, str(documents))
    text = documents[0].read_text(encoding="utf-8")
    harness.check("the PDF's own sentences, as written",
                  "The router then picks the ledger shard from the tenant id" in text, text[:400])
    figure_at = text.find("](resources/figure-01.png)")
    harness.check("the figure stands where it stood: after the router's sentence, before the cache's",
                  text.find("The router then picks") < figure_at < text.find("Responses are cached"), text[:1200])
    harness.check("the figure file is published", (documents[0].parent / "resources" / "figure-01.png").is_file())
    harness.check("a figure with no caption is named by its page", "*Figure 1 (page 2)*" in text)
