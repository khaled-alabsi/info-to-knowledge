"""Phase 1 of the office workflow: a Word (`.docx`) or OpenDocument text (`.odt`) file turned
into markdown by pandoc, with its headings, lists and tables kept and its pictures saved as files, then written as the
same transcript a PDF read as text only gives, so every later phase reads it unchanged.

A table with a merged cell is written as an HTML table, which keeps the span (`tools/pandoc/merged_tables_as_html.lua`).
pandoc's OpenDocument reader keeps no merged cell, so an `.odt` is first turned into `.docx` by LibreOffice when it is
installed: on the smoke test's files that kept every merged cell pandoc on the `.odt` lost (22 of them), and took 27 s
where pandoc on the `.odt` took 467 s. Without LibreOffice the `.odt` is read directly and the console says merged cells are lost. Pictures in an
old Windows vector format (`.emf`, `.wmf`), which a browser cannot show, are turned into PNG by LibreOffice, or left out
and named. The document's own table of contents (link lines ending in a page number) is removed, as a PDF's is.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy

import config
from models import output_contract
from models.pipeline_errors import MissingPhaseInput

__all__ = ["OFFICE_SUFFIXES", "MEDIA_DIR_NAME", "BLOCKS_PER_PAGE", "is_office", "find_libreoffice",
           "convert_with_libreoffice", "build_document_transcript", "render_figures"]

OFFICE_SUFFIXES = (".docx", ".odt")
# The pictures pandoc writes out, in the run folder; phase 2 makes each a frame
MEDIA_DIR_NAME = "01_office-media"
# A Word file has no pages: its blocks are given a page of the timeline every BLOCKS_PER_PAGE blocks, as a PDF's page
# is a minute, so the merge and the document's order read them as a PDF's
BLOCKS_PER_PAGE = 20
MERGED_TABLES_FILTER = config.PLAYBOOK_ROOT / "tools" / "pandoc" / "merged_tables_as_html.lua"
# A long standard (5,000 pages) took about 10 minutes on a busy machine
_CONVERT_TIMEOUT_SECONDS = 3600
_VECTOR_SUFFIXES = (".emf", ".wmf")
_FIGURE_LEAD_SECONDS = 0.01
_FRAME_WIDTH = 1600
_FRAME_JPEG_QUALITY = 92
_FIGURE_BLOCKS_BEFORE = 2

# A picture in pandoc's markdown, `![alt](path "title")`, or in a raw HTML block, `<img src="path" …>`
_MARKDOWN_PICTURE = re.compile(r'!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"[^"]*")?\)(?:\{[^}]*\})?')
_HTML_PICTURE = re.compile(r'<img\b[^>]*\bsrc="(?P<path>[^"]+)"[^>]*>')
# A line of a Word table of contents as pandoc writes it: links to a heading and nothing else, whose words end in the
# page number, in three shapes seen on the smoke files: "[1 Introduction 9](#introduction)", "[Foreword [xv](#foreword)]
# (#foreword)" and "**[1 Introduction](#introduction)** [14](#introduction)", the last also quoted ("> ") for a level
_LINK_TO_HEADING = re.compile(r"\]\(#[^)]*\)")
_ONLY_LINKS = re.compile(r"^(?:\[.*\]\(#[^)]*\)\s*)+$")
_ENDS_IN_PAGE = re.compile(r"\s(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
_CONTENTS_HEADING = re.compile(r"^(?:#{1,6}\s+|\*\*)?(?:table of contents|contents|inhaltsverzeichnis|inhalt)(?:\*\*)?$",
                               re.IGNORECASE)
_FIGURE_CAPTION = re.compile(r"^(?:\*\*|_)?(?:figure|fig\.|abbildung|abb\.)\s*\d+", re.IGNORECASE)
_FENCE = re.compile(r"^(```|~~~)")
_HTML_TABLE_OPEN = re.compile(r"(?<!\\)<table[\s>]")
_HTML_TABLE_CLOSE = re.compile(r"(?<!\\)</table>")

_LOG = logging.getLogger(__name__)


def is_office(path: Path) -> bool:
    return path.suffix.lower() in OFFICE_SUFFIXES


def find_libreoffice() -> str | None:
    """The LibreOffice command (`soffice`), or None when it is not installed."""

    return shutil.which("soffice")


def _run(command: list[str],
         what: str) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=_CONVERT_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise MissingPhaseInput(f"{command[0]} is not installed: it is needed to {what}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{command[0]} failed to {what}: {(exc.stderr or exc.stdout or '').strip()[-400:]}") from exc


def convert_with_libreoffice(sources: list[Path],
                              target_format: str,
                              out_dir: Path) -> dict[Path, Path]:
    """Each of `sources` converted by LibreOffice, in one call, into `out_dir` as `target_format` (`docx`, `png`): each
    source to the file it became; {} when LibreOffice is not installed, a source missing when it wrote nothing for it."""

    soffice = find_libreoffice()
    if soffice is None or not sources:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    # Its own profile folder, so a LibreOffice the person has open does not refuse a second instance
    with tempfile.TemporaryDirectory() as profile:
        _run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", target_format,
              "--outdir", str(out_dir), *map(str, sources)], f"convert {len(sources)} file(s) to {target_format}")
    converted = {source: out_dir / f"{source.stem}.{target_format}" for source in sources}

    return {source: target for source, target in converted.items() if target.is_file()}


def _convert_to_markdown(document: Path,
                         media_dir: Path,
                         log: logging.Logger) -> str:
    """The document as GitHub markdown, its pictures written under `media_dir`."""

    # 1. An .odt through LibreOffice to .docx when it is installed, whose merged cells pandoc then keeps
    source = document
    if document.suffix.lower() == ".odt":
        converted = convert_with_libreoffice([document], "docx", media_dir.parent / f"{MEDIA_DIR_NAME}-docx")
        if document in converted:
            source = converted[document]
            log.info("  OpenDocument turned into Word by LibreOffice first, which keeps merged table cells")
        else:
            log.warning("  LibreOffice is not installed: the OpenDocument file is read directly, and merged table "
                        "cells come out unmerged")

    # 2. pandoc, the merged-tables filter, the pictures written out
    markdown_path = media_dir.parent / f"{MEDIA_DIR_NAME}.md"
    _run(["pandoc", "-f", source.suffix.lower().lstrip("."), "-t", "gfm", "--wrap=none",
          f"--extract-media={media_dir}", f"--lua-filter={MERGED_TABLES_FILTER}", "-o", str(markdown_path),
          str(source)], f"convert {document.name} to markdown")

    return markdown_path.read_text(encoding="utf-8")


def _convert_vector_pictures(markdown: str,
                             media_dir: Path,
                             log: logging.Logger) -> str:
    """`markdown` with each `.emf` or `.wmf` picture turned into PNG by LibreOffice, or taken out and named when it
    cannot be: a browser shows neither."""

    vectors = sorted({path for path in [match.group("path") for match in _MARKDOWN_PICTURE.finditer(markdown)]
                      + [match.group("path") for match in _HTML_PICTURE.finditer(markdown)]
                      if Path(path).suffix.lower() in _VECTOR_SUFFIXES})
    existing = [Path(path) for path in vectors if Path(path).is_file()]
    converted = convert_with_libreoffice(existing, "png", media_dir / "converted")
    lost: list[str] = []
    for path in vectors:
        if Path(path) not in converted:
            lost.append(Path(path).name)
            markdown = re.sub(rf'!\[[^\]]*\]\({re.escape(path)}(?:\s+"[^"]*")?\)(?:\{{[^}}]*\}})?|<img\b[^>]*\bsrc="'
                              rf'{re.escape(path)}"[^>]*>', "", markdown)
            continue
        markdown = markdown.replace(path, str(converted[Path(path)]))
    if lost:
        log.warning(f"  {len(lost)} old Windows picture(s) left out, LibreOffice could not turn them into PNG: "
                    + ", ".join(lost))

    return markdown


def _split_blocks(markdown: str) -> list[str]:
    """The markdown's blocks (a heading, a paragraph, a list, a table, a code block), each kept whole: blocks are
    separated by blank lines, except inside a code fence or a raw HTML table."""

    blocks: list[str] = []
    current: list[str] = []
    fence = ""
    html_depth = 0
    for line in markdown.splitlines():
        stripped = line.strip()
        if fence:
            current.append(line)
            if stripped.startswith(fence):
                fence = ""
            continue
        if _FENCE.match(stripped):
            fence = _FENCE.match(stripped).group(1)
            current.append(line)
            continue
        # a real tag only: "\<table\>" in a sentence about HTML is text, and counted it glued the rest of a document
        # into one block
        html_depth += len(_HTML_TABLE_OPEN.findall(line)) - len(_HTML_TABLE_CLOSE.findall(line))
        if not stripped and html_depth <= 0:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            html_depth = 0
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    return [block for block in blocks if block]


def _is_contents_line(line: str) -> bool:
    """A line of the document's own table of contents: links to headings alone, its words ending in a page number."""

    bare = re.sub(r"^(?:>\s*)+", "", line.strip()).replace("**", "")
    words = _LINK_TO_HEADING.sub("", bare).replace("[", " ").replace("]", " ").strip()

    return bool(_ONLY_LINKS.match(bare)) and _ENDS_IN_PAGE.search(f" {words}") is not None


def _is_contents_block(block: str) -> bool:
    """A block of the document's own table of contents: every line a contents line."""

    lines = [line.strip() for line in block.splitlines() if line.strip()]

    return bool(lines) and all(_is_contents_line(line) for line in lines)


def _remove_contents(blocks: list[str],
                     removed: list[dict]) -> list[str]:
    """`blocks` without the document's own table of contents, its heading with it, each removal named in `removed`."""

    kept: list[str] = []
    for index, block in enumerate(blocks):
        is_contents_heading = (_CONTENTS_HEADING.match(block.strip()) is not None and index + 1 < len(blocks)
                               and _is_contents_block(blocks[index + 1]))
        if is_contents_heading or _is_contents_block(block):
            removed.append({"page": index // BLOCKS_PER_PAGE + 1, "rule": "table of contents", "text": block[:200]})
            continue
        kept.append(block)

    return kept


def _take_pictures(block: str) -> tuple[str, list[str]]:
    """The block with its pictures taken out, and their paths in order."""

    paths = [match.group("path") for match in _MARKDOWN_PICTURE.finditer(block)]
    paths += [match.group("path") for match in _HTML_PICTURE.finditer(block)]
    text = _HTML_PICTURE.sub("", _MARKDOWN_PICTURE.sub("", block)).strip()

    return text, paths


def build_document_transcript(document: Path,
                              output_root: Path,
                              log: logging.Logger = _LOG) -> tuple[dict, list[dict]]:
    """The document as phase 1's transcript, one segment per block of its markdown with a page of the timeline every
    BLOCKS_PER_PAGE blocks, its pictures under `figures` (each `{"image", "caption", "page", "start"}`, the image's path
    in the run folder), and every removal `{"page", "rule", "text"}`."""

    # STEP 1: the markdown, its pictures under the run folder, old Windows pictures turned into PNG
    media_dir = output_root / MEDIA_DIR_NAME
    if media_dir.exists():
        shutil.rmtree(media_dir)
    markdown = _convert_vector_pictures(_convert_to_markdown(document, media_dir, log), media_dir, log)

    # STEP 2: its blocks, the document's own table of contents taken out
    removed: list[dict] = []
    blocks = _remove_contents(_split_blocks(markdown), removed)

    # STEP 3: each block on the timeline, each picture placed just before the block it stood in or before; a picture
    # alone in its block stands before the next block, and a "Figure N" block after it is its caption
    segments: list[dict] = []
    figures: list[dict] = []
    waiting: list[str] = []
    step = output_contract.PDF_PAGE_SECONDS / BLOCKS_PER_PAGE
    for block in blocks:
        text, paths = _take_pictures(block)
        waiting += paths
        if not text:
            continue
        place = len(segments)
        page_number = place // BLOCKS_PER_PAGE + 1
        start = round((page_number - 1) * output_contract.PDF_PAGE_SECONDS + (place % BLOCKS_PER_PAGE) * step, 2)
        caption = text if waiting and _FIGURE_CAPTION.match(text) else ""
        page_start = (page_number - 1) * output_contract.PDF_PAGE_SECONDS
        for path in waiting:
            relative = Path(path).resolve().relative_to(output_root.resolve()) if Path(path).is_absolute() else Path(path)
            figures.append({"image": str(relative), "caption": caption, "page": page_number,
                            "start": round(max(start - _FIGURE_LEAD_SECONDS, page_start), 2)})
        waiting = []
        segments.append({"text": text, "start": start, "end": round(start + step, 2), "speaker": None, "words": [],
                         "page": page_number})
    if waiting:
        last_start = segments[-1]["end"] if segments else 0.0
        figures += [{"image": str(Path(path).resolve().relative_to(output_root.resolve())), "caption": "",
                     "page": int(last_start // output_contract.PDF_PAGE_SECONDS) + 1, "start": last_start}
                    for path in waiting]
    log.info(f"  {len(segments)} block(s) and {len(figures)} picture(s) read by pandoc")

    # STEP 4: the transcript, its figures beside its segments
    transcript = {"language": None, "languageProbability": None, "engine": "pandoc", "model": "",
                  "text": "\n\n".join(segment["text"] for segment in segments), "segments": segments}
    if figures:
        transcript["figures"] = figures

    return transcript, removed


def _read_picture(path: Path) -> numpy.ndarray | None:
    """The picture as a BGR array on white (a transparent PNG's background), at most _FRAME_WIDTH wide; None when it
    cannot be read."""

    # An SVG drawn to PNG by rsvg-convert where it is installed: OpenCV reads no SVG
    if path.suffix.lower() == ".svg" and shutil.which("rsvg-convert") and path.is_file():
        drawn = path.with_suffix(".svg.png")
        _run(["rsvg-convert", "--background-color=white", "-o", str(drawn), str(path)], f"draw {path.name}")
        path = drawn
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        alpha = image[:, :, 3:4].astype(numpy.float32) / 255.0
        image = (image[:, :, :3].astype(numpy.float32) * alpha + 255.0 * (1.0 - alpha)).astype(numpy.uint8)
    if image.shape[1] > _FRAME_WIDTH:
        height = round(image.shape[0] * _FRAME_WIDTH / image.shape[1])
        image = cv2.resize(image, (_FRAME_WIDTH, height), interpolation=cv2.INTER_AREA)

    return image


def render_figures(transcript: dict,
                   output_root: Path,
                   frames_dir: Path,
                   log: logging.Logger = _LOG) -> list[dict]:
    """Each picture phase 1 took out of the document, written as `frames/frame-NNN.jpg`, as phase 2's items in the
    shape a PDF's figure has, `transcriptCurrent` its caption and `transcriptBefore` the blocks before it; a picture
    no reader opens (an SVG, a format OpenCV lacks) is left out and named."""

    figures = transcript.get("figures") or []
    if not figures:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    segments = transcript.get("segments", [])
    items: list[dict] = []
    unread: list[str] = []
    for figure in figures:
        image = _read_picture(output_root / figure["image"])
        if image is None:
            unread.append(Path(figure["image"]).name)
            continue
        index = len(items)
        slug = f"frame-{index:03d}"
        cv2.imwrite(str(frames_dir / f"{slug}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, _FRAME_JPEG_QUALITY])
        box = [0, 0, int(image.shape[1]), int(image.shape[0])]
        before = [segment["text"] for segment in segments if segment["start"] < figure["start"]]
        items.append({"slug": slug, "index": index, "frameNumber": figure["page"] - 1, "timestamp": figure["start"],
                      "path": f"{frames_dir.name}/{slug}.jpg", "score": 1.0, "box": box, "screenBox": list(box),
                      "repeatOf": None, "stepOf": None, "page": figure["page"],
                      "medium": output_contract.PDF_FIGURE_MEDIUM, "caption": figure["caption"],
                      "transcriptBefore": "\n\n".join(before[-_FIGURE_BLOCKS_BEFORE:]),
                      "transcriptCurrent": figure["caption"], "kind": ""})
    if unread:
        log.warning(f"  {len(unread)} picture(s) no reader opens were left out: " + ", ".join(unread))

    return items
