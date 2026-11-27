"""The PPT workflow's phases 1 and 2, run through `i2k run --to 2` on a deck made here by pandoc
from invented slides with speaker notes: LibreOffice draws it into `01_slides.pdf`, every slide becomes one page and one
frame, and what is said over a slide is its speaker notes, never the slide's own words (the owner: "only image to
text")."""

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from support import harness

import cli
from services import recording_hash_service
from services import slides_source_service

_SLIDES = """---
title: AcmeSync gateway review
---

# Shards

- Each tenant has its own ledger shard
- A shard moves only on Tuesdays

::: notes
Jonas Weber reviews every shard move before it happens.
:::

# Retry rules

| Case | First retry |
|---|---|
| Timeout | after 30 seconds |
| Refused | after 5 seconds |

# Open points

- Who owns the refund queue?
"""


def test_a_deck_s_slides_become_pages_and_frames_with_their_notes_as_speech():
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    (root / "slides.md").write_text(_SLIDES, encoding="utf-8")
    deck = root / "AcmeSync gateway review.pptx"
    subprocess.run(["pandoc", "slides.md", "-o", deck.name], cwd=root, check=True)
    shown = len(slides_source_service.read_slide_notes(deck))

    run = root / "run"
    code = cli.main(["run", "--force", "--to", "2", "--video", str(deck), "--output", str(run)])
    harness.check("phases 1 and 2 end well", code == 0, str(code))
    harness.check("the deck is drawn into the run's PDF, one page a slide",
                  (run / slides_source_service.SLIDES_PDF_NAME).is_file(), str(list(run.iterdir())))
    frames = json.loads((run / "02_frames.json").read_text(encoding="utf-8"))
    harness.check("every slide is a frame read as a picture", len(frames["items"]) == shown and frames["medium"] == "pdf",
                  f"{len(frames['items'])} frames, {shown} slides, medium {frames.get('medium')}")
    spoken = " ".join(item.get("transcriptCurrent", "") for item in frames["items"])
    harness.check("the slide's speaker notes are what is said over it",
                  "Jonas Weber reviews every shard move" in spoken, spoken[:300])
    harness.check("the slide's own words are never handed over as speech",
                  "ledger shard" not in spoken and "after 30 seconds" not in spoken, spoken[:300])
    tags = (run / "run-tags.json").read_text(encoding="utf-8") if (run / "run-tags.json").is_file() else ""
    harness.check("the run is tagged from-ppt", "from-ppt" in tags, tags)
    shutil.rmtree(root, ignore_errors=True)


def test_a_hidden_slide_is_left_out_and_named():
    root = Path(tempfile.mkdtemp())
    (root / "slides.md").write_text(_SLIDES, encoding="utf-8")
    made = root / "made.pptx"
    subprocess.run(["pandoc", "slides.md", "-o", made.name], cwd=root, check=True)
    # the deck's third slide hidden, as PowerPoint writes it: show="0" on the slide's root
    deck = root / "AcmeSync hidden.pptx"
    with zipfile.ZipFile(made) as source, zipfile.ZipFile(deck, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "ppt/slides/slide3.xml":
                data = data.replace(b"<p:sld ", b'<p:sld show="0" ', 1)
            target.writestr(entry, data)
    slides = slides_source_service.read_slide_notes(deck)
    harness.check("the third slide reads hidden", [slide["hidden"] for slide in slides] == [False, False, True, False],
                  str(slides))
    out = root / "out"
    out.mkdir()
    transcript, removed = slides_source_service.build_slide_transcript(deck, out)
    harness.check("the PDF holds the three shown slides, the hidden one named as removed",
                  len(transcript["slides"]) == 3 and removed == [{"page": 0, "rule": "hidden slide", "text": "slide 3"}],
                  f"{transcript['slides']} {removed}")
    shutil.rmtree(root, ignore_errors=True)
