"""A page as Unlimited-OCR-2 reads it, turned into the page's markdown, its chrome and its figures. The model writes
each region of the page as one block, `<BLOCK>label[x0, y0, x1, y1]<FORK>text` (a title `title<LEVELn>`), its box on
a 0 to 1000 grid of the page; a table is HTML, a formula LaTeX. A header, a footer or a page number is the page's
running chrome; a picture, a chart or a figure is boxed with no text, its caption the `image_caption` or
`image_footnote` blocks after it."""

from __future__ import annotations

import re

from utils import html_table_utils

__all__ = ["CHROME_LABELS", "GRID", "MIN_FIGURE_SHARE", "read_page"]

_BLOCK = re.compile(r"<BLOCK>\s*([A-Za-z_-]+)\s*(?:<LEVEL(\d)>)?\s*\[([^\]]*)\]\s*<FORK>(.*?)(?=<BLOCK>|<PAGE>|$)", re.S)
GRID = 1000                                     # the model writes a box on this grid of the page, both ways
CHROME_LABELS = ("header", "footer", "page_number")
_PICTURE_LABELS = ("image", "chart", "figure")
_CAPTION_LABELS = ("image_caption", "image_footnote")
_TABLE_LABELS = ("table", "append_table")
_MAX_HEADING_LEVEL = 6
# a picture box under this share of the page is an icon or an avatar, never a figure (a like button, an
# avatar), the share phase 1's own figure finder sets (`pdf_source_service._FIGURE_MIN_SHARE`)
MIN_FIGURE_SHARE = 0.01


def _read_box(box: str) -> list[float] | None:
    values = [value.strip() for value in box.split(",")]
    if len(values) != 4 or not all(re.fullmatch(r"\d+(?:\.\d+)?", value) for value in values):
        return None

    return [float(value) for value in values]


def _render_block(label: str,
                  level: str,
                  text: str) -> str:
    """One block as markdown: a title as a heading of its level, a table as a markdown table, any other text as it
    stands."""

    if label == "title" and text and "\n" not in text:
        return "#" * min(int(level or 1), _MAX_HEADING_LEVEL) + " " + text
    if label in _TABLE_LABELS:
        return html_table_utils.render_markdown_table(text) or text

    return text


def read_page(raw: str) -> tuple[str, list[str], list[dict]]:
    """`raw`, the model's reading of one page, as `(markdown, chrome, figures)`: the page's markdown, the text of each
    header, footer and page number block left out of it, and each picture of 1% of the page or more as
    `{"box": [x0, y0, x1, y1] on the 0 to 1000 grid, "caption"}`. A reading in no block shape is its own markdown."""

    blocks = _BLOCK.findall(raw)
    if not blocks:
        return raw.replace("<PAGE>", "").strip(), [], []
    parts: list[str] = []
    chrome: list[str] = []
    figures: list[dict] = []
    for label, level, box_text, text in blocks:
        label, text = label.lower(), text.strip()
        if label in CHROME_LABELS:
            if text:
                chrome.append(text)
            continue
        box = _read_box(box_text)
        if label in _PICTURE_LABELS:
            is_figure = box is not None and (box[2] - box[0]) * (box[3] - box[1]) >= MIN_FIGURE_SHARE * GRID * GRID
            if is_figure:
                figures.append({"box": box, "caption": text})
            continue
        if label in _CAPTION_LABELS and figures:
            figures[-1]["caption"] = " ".join(part for part in (figures[-1]["caption"], text) if part)
            continue
        rendered = _render_block(label, level, text)
        if rendered:
            parts.append(rendered)

    return "\n\n".join(parts), chrome, figures
