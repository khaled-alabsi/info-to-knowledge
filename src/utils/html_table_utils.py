"""An HTML table, as Unlimited-OCR-2 writes a scanned page's table, turned into a markdown table. Pure text work."""

from __future__ import annotations

from html.parser import HTMLParser

__all__ = ["render_markdown_table"]


class _TableReader(HTMLParser):
    """The rows of a table, each a list of its cells' text; a cell spanning N columns is written once and N - 1 empty
    cells follow it, so every row keeps the columns of the header."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._span = 1

    def handle_starttag(self,
                        tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
            span = dict(attrs).get("colspan") or "1"
            self._span = int(span) if span.isdigit() else 1

    def handle_endtag(self,
                      tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1] += [" ".join("".join(self._cell).split())] + [""] * (self._span - 1)
            self._cell = None

    def handle_data(self,
                    text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)


def render_markdown_table(html: str) -> str:
    """`html`'s table as a markdown table, its first row the header; "" when it holds no row. A `|` in a cell is
    escaped, so a cell never splits."""

    reader = _TableReader()
    reader.feed(html)
    rows = [row for row in reader.rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    lines = ["| " + " | ".join(cell.replace("|", "\\|") for cell in row + [""] * (width - len(row))) + " |"
             for row in rows]

    return "\n".join([lines[0], "|" + "---|" * width, *lines[1:]])
