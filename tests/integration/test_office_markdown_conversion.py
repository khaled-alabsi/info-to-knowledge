"""The office workflow, run whole through `i2k run` on a Word file and its OpenDocument copy,
both made here from invented HTML by pandoc and LibreOffice: phase 1 turns each into markdown, phase 2 makes its picture
a frame, phase 4 publishes the document with no model, its merged table cell kept and its picture where it stood."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness

import cli
from services import office_source_service
from services import recording_hash_service

_HTML = """<html><body>
<h1>AcmeSync gateway guide</h1>
<p>The gateway routes each tenant to its own ledger shard before any request is answered.</p>
<p><img src="gateway.png" alt="gateway"></p>
<p>Figure 1: the AcmeSync gateway and its shards</p>
<p>Jonas Weber reviews every shard change on Tuesdays.</p>
<h2>Retry rules</h2>
<table>
<tr><th>Case</th><th>First retry</th><th>Last retry</th></tr>
<tr><td>Timeout</td><td colspan="2">after 30 seconds, three times</td></tr>
<tr><td>Refused</td><td>after 5 seconds</td><td>after 60 seconds</td></tr>
</table>
<p>A refused request never retries more than twice an hour.</p>
</body></html>"""


def _build_documents(folder: Path) -> tuple[Path, Path]:
    """A Word file and its OpenDocument copy, holding a heading, a picture with its caption and a table with a merged
    cell."""

    picture = numpy.full((240, 400, 3), 255, numpy.uint8)
    cv2.rectangle(picture, (40, 60), (160, 180), (200, 120, 40), -1)
    cv2.rectangle(picture, (240, 60), (360, 180), (40, 160, 90), -1)
    cv2.imwrite(str(folder / "gateway.png"), picture)
    (folder / "guide.html").write_text(_HTML, encoding="utf-8")
    word = folder / "AcmeSync gateway guide.docx"
    subprocess.run(["pandoc", "guide.html", "-o", word.name], cwd=folder, check=True)
    subprocess.run([office_source_service.find_libreoffice(), "--headless", "--convert-to", "odt", "--outdir",
                    str(folder / "odt"), str(word)], check=True, capture_output=True)

    return word, folder / "odt" / f"{word.stem}.odt"


def _run_and_read(document: Path,
                  root: Path) -> tuple[int, dict, str, Path]:
    run = root / f"run-{document.suffix.lstrip('.')}"
    code = cli.main(["run", "--force", "--video", str(document), "--output", str(run)])
    frames = json.loads((run / "02_frames.json").read_text(encoding="utf-8")) if (run / "02_frames.json").is_file() else {}
    published = [path for path in run.glob("*/*.md")]

    return code, frames, published[0].read_text(encoding="utf-8") if published else "", run


def test_a_word_file_and_its_opendocument_copy_publish_their_text_table_and_picture():
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    word, odt = _build_documents(root)
    for document in (word, odt):
        code, frames, text, run = _run_and_read(document, root)
        kind = document.suffix
        harness.check(f"{kind}: the run ends well", code == 0, str(code))
        harness.check(f"{kind}: its picture is placed, no figure is read by a model",
                      frames.get("items") == [] and len(frames.get("placedFigures", [])) == 1, str(frames.keys()))
        harness.check(f"{kind}: nothing is sent to a model",
                      not (run / "calls").exists() or not any((run / "calls").iterdir()))
        harness.check(f"{kind}: the document's own sentences",
                      "routes each tenant to its own ledger shard" in text and "Jonas Weber reviews" in text, text[:600])
        harness.check(f"{kind}: the merged cell stays merged", 'colspan="2"' in text and "after 30 seconds" in text,
                      text[text.find("Retry"):][:600])
        figure_at = text.find("](resources/figure-01.png)")
        harness.check(f"{kind}: the picture stands between the sentence before it and the one after",
                      text.find("ledger shard") < figure_at < text.find("Jonas Weber"), text[:900])
        harness.check(f"{kind}: the picture file is published",
                      any(run.glob("*/resources/figure-01.png")), str(list(run.glob("*/resources/*"))))
        tags = json.loads((run / "run-tags.json").read_text(encoding="utf-8")) if (run / "run-tags.json").is_file() else {}
        harness.check(f"{kind}: the run is tagged from-office", "from-office" in json.dumps(tags), json.dumps(tags))
    shutil.rmtree(root, ignore_errors=True)
