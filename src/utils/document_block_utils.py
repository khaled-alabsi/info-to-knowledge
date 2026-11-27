"""The document phase 5 edits, as numbered blocks. REQUIREMENTS.md R10. Pure functions.

Phase 4 writes a markdown file. Phase 5's agent places images in it, moves and removes them, deletes a sentence an
image now shows, and adds the sentence that points to a figure; to name where, it needs a document it can address.
So the markdown is split into blocks, each with an id: the title, a heading, a paragraph with the entries it rests
on, a diagram, a list, an image. An image is rendered with the figure number of its place in the document; a sentence
may point to an image by its id, `{{figure:I02}}`, rendered as that number, though phase 4 writes the number itself.
"""

from __future__ import annotations

import re
from typing import Any

from utils import document_text_utils

__all__ = ["convert_placed_images", "split_into_blocks", "render_blocks", "map_figure_numbers", "list_chapters",
           "FIGURE_REFERENCE"]

# How a sentence names a figure in the stored blocks, by its image's id, `{{figure:I02}}`: rendered as its number
FIGURE_REFERENCE = re.compile(r"\{\{figure:(I\d+)\}\}")
# A nested item is indented: matched without its indent, the contents' headings became one paragraph each and the
# published contents lost its nesting (2026-10-01)
_LIST_LINE = re.compile(r"^\s*(- |\d+\. )")

# A frame phase 4 placed: `![alt](04_writing/images/cuts/cut-03.png)`, then `*caption*` as its own paragraph.
_PLACED_IMAGE = re.compile(r"^!\[(.*)\]\((\S*/cuts/(cut-\d+)\.png)\)$")
_PLACED_CAPTION = re.compile(r"^\*(.+)\*$")


def _read_heading(line: str) -> tuple[int, str] | None:
    marks = len(line) - len(line.lstrip("#"))

    return (marks, line[marks:].strip()) if 0 < marks <= 6 and line[marks:marks + 1] == " " else None


def _read_line_kind(line: str,
                    heading: tuple[int, str] | None) -> str:
    """What a line outside a fenced block starts: a fenced block (kept as kind `diagram`), a heading, a list, a blank
    line or a paragraph."""

    if line.startswith("```"):
        return "diagram"
    if heading:
        return "heading"
    if _LIST_LINE.match(line):
        return "list"

    return "blank" if not line.strip() else "paragraph"


def _render_block_id(blocks: list[dict[str, Any]]) -> str:
    """The id the next block gets: `B001`, `B002`, in document order."""

    return f"B{len(blocks) + 1:03d}"


def _close_run(blocks: list[dict[str, Any]],
               run: list[str],
               kind: str,
               section: tuple[str, str],
               entry_ids: dict[str, list[str]]) -> None:
    if not run:
        return
    text = "\n".join(run).strip()
    blocks.append({"id": _render_block_id(blocks), "kind": kind, "text": text, "section": section[0],
                   "sectionId": section[1],
                   "entryIds": list(entry_ids.get(" ".join(text.split()), [])) if kind == "paragraph" else []})


def split_into_blocks(markdown: str,
                      entry_ids: dict[str, list[str]]) -> list[dict[str, Any]]:
    """`markdown` as blocks: `{"id", "kind", "text", "section", "sectionId"}` plus `level` for a heading and `entryIds`
    for a paragraph, looked up in `entry_ids` by the paragraph's text with its spaces collapsed. `section` is the text
    of the second-level heading the block falls under and `sectionId` that heading's id, so two parts with one title
    stay two chapters; both are empty before the first."""

    blocks: list[dict[str, Any]] = []
    section = ("", "")
    run: list[str] = []
    run_kind = ""
    is_in_diagram = False
    for line in markdown.splitlines():
        if is_in_diagram:
            run.append(line)
            if line.strip() == "```":
                _close_run(blocks, run, "diagram", section, entry_ids)
                run, run_kind, is_in_diagram = [], "", False
            continue
        heading = _read_heading(line)
        kind = _read_line_kind(line, heading)
        # The lines of one paragraph stay one block, a table's rows and a quote's lines with them: a block a line, a
        # table was published as one paragraph a row. A placed frame's line is a block of its own, as its caption is
        is_placed_frame = bool(_PLACED_IMAGE.match(line)) or bool(run and _PLACED_IMAGE.match(run[-1]))
        if kind != run_kind or kind in ("heading", "diagram") or is_placed_frame:
            _close_run(blocks, run, run_kind, section, entry_ids)
            run, run_kind = [], kind
        if kind == "heading":
            level, text = heading
            block_id = _render_block_id(blocks)
            section = (text, block_id) if level == 2 else section
            blocks.append({"id": block_id, "kind": "title" if level == 1 else "heading", "level": level, "text": text,
                           "section": section[0], "sectionId": section[1], "entryIds": []})
            run_kind = ""
        elif kind == "diagram":
            run, is_in_diagram = [line], True
        elif kind != "blank":
            run.append(line)
    _close_run(blocks, run, run_kind if not is_in_diagram else "diagram", section, entry_ids)

    return blocks


def map_figure_numbers(blocks: list[dict[str, Any]]) -> dict[str, int]:
    """Image id -> its figure number, in document order from 1."""

    return {block["id"]: number for number, block in enumerate([block for block in blocks if block["kind"] == "image"], start=1)}


def render_blocks(blocks: list[dict[str, Any]],
                  image_paths: dict[str, str]) -> str:
    """The markdown of `blocks`: each image as its frame, linked at `image_paths[image id]`, over its numbered
    caption, and every reference to an image as that image's figure number."""

    numbers = map_figure_numbers(blocks)
    lines = []
    for block in blocks:
        if block["kind"] == "title":
            lines.append(f"# {block['text']}")
        elif block["kind"] == "heading":
            lines.append(f"{'#' * block['level']} {block['text']}")
        elif block["kind"] == "image":
            # The description under the caption is what a reader who cannot see the frame, and an agent reading the
            # document as text, read in its place (the owner, 2026-10-06).
            described = (f"\n\n{document_text_utils.FIGURE_DESCRIPTION_MARK} {block['description']}"
                         if block.get("description") else "")
            lines.append(f"![{block['alt']}]({image_paths[block['id']]})\n\n"
                         f"{document_text_utils.FIGURE_CAPTION_START}{numbers[block['id']]}. {block['caption']}*{described}")
        else:
            lines.append(FIGURE_REFERENCE.sub(
                lambda match: document_text_utils.render_figure_mention(numbers.get(match.group(1), "?")), block["text"]))

    return "\n\n".join(lines) + "\n"


def list_chapters(blocks: list[dict[str, Any]],
                  exempt_headings: list[str]) -> list[str]:
    """The ids of the headings of the document's chapters, in order: every second-level heading but the exempt sections
    (the executive summary, the agenda, the closing)."""

    return [block["id"] for block in blocks
            if block["kind"] == "heading" and block["level"] == 2 and block["text"] not in exempt_headings]


# The description line under a placed figure's caption, as a chapter writes it and as the document renders it.
_PLACED_DESCRIPTION = re.compile(rf"^(?:{document_text_utils.FIGURE_DESCRIPTION_START}|"
                                 rf"{re.escape(document_text_utils.FIGURE_DESCRIPTION_LABEL)})\s*(.+)$", re.DOTALL)


def convert_placed_images(blocks: list[dict[str, Any]],
                          cuts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """`blocks` with each frame phase 4 placed (a `![alt](.../cuts/cut-NN.png)` paragraph and the `*caption*` paragraph
    after it) turned into an image block citing the entries of the paragraph it follows, and how many were converted_blocks.
    A frame line whose cut `cuts` does not know stays as it is (the owner, 2026-09-28: a chapter places its images)."""

    converted_blocks: list[dict[str, Any]] = []
    adopted_count = 0
    index = 0
    while index < len(blocks):
        block = blocks[index]
        match = _PLACED_IMAGE.match(block.get("text", "")) if block["kind"] == "paragraph" else None
        caption_block = blocks[index + 1] if index + 1 < len(blocks) else {}
        caption = _PLACED_CAPTION.match(caption_block.get("text", "")) if caption_block.get("kind") == "paragraph" else None
        if match and match.group(3) in cuts and caption:
            adopted_count += 1
            # The description line under the caption travels with the figure, so phase 5 keeps what a reader who
            # cannot see the frame reads in its place.
            after_caption = blocks[index + 2] if index + 2 < len(blocks) else {}
            described = _PLACED_DESCRIPTION.match(after_caption.get("text", "")) \
                if after_caption.get("kind") == "paragraph" else None
            # The last paragraph that cites entries: an `> **Open:**` line between it and the figure cites none here,
            # and four figures of one document were published citing nothing (2026-10-03)
            before = {}
            for earlier in reversed(converted_blocks):
                # A heading ends the search: a figure right under a chapter's heading cites nothing of the chapter before
                if earlier["kind"] in ("heading", "title"):
                    break
                if earlier["kind"] == "paragraph" and earlier.get("entryIds"):
                    before = earlier
                    break
            converted_blocks.append({"id": f"I{adopted_count:02d}", "kind": "image", "cut": match.group(3),
                            "frame": cuts[match.group(3)].get("frame", ""), "caption": caption.group(1).strip(),
                            "alt": match.group(1).strip(), "entryIds": list(before.get("entryIds") or []),
                            "description": described.group(1).strip() if described else "",
                            "section": block["section"], "sectionId": block["sectionId"], "text": ""})
            index += 3 if described else 2
            continue
        converted_blocks.append(block)
        index += 1

    return converted_blocks, adopted_count
