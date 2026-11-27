"""Build a PDF of the shapes a wiki export draws that are no figure, beside figures that are (DEFECTS 667), every word
invented: page 1 a table of rounded cells with a tick icon in one, page 2 a grey file placeholder in its rounded frame
beside a real picture, page 3 a table of rounded cells holding one small screenshot, page 4 a bar chart.

Used by `tests/cases/test_pdf_source.py` and `tests/integration/test_pdf_boxed_text_figures.py`, which build it into a
temporary folder.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy
import pymupdf

WIDTH, HEIGHT = 595, 842
CELL_FILL = (0.96, 0.96, 0.96)
CELL_BORDER = (0.82, 0.82, 0.82)
ROWS = [["Owner", "Jonas Berg"], ["Area", "Gateway tokens"], ["Cluster", "Partner onboarding"], ["Cell", "AcmeSync core"]]


def _draw_rounded_table(page: pymupdf.Page,
                        top: float,
                        rows: list[list[str]],
                        row_height: float = 40) -> list[pymupdf.Rect]:
    """A table whose every cell is a rounded box, its fill and its border two paths as the wiki draws them, its text
    inside; returns the cells. Corners of a tenth of a row keep pymupdf's table finder from seeing a table."""

    shape = page.new_shape()
    cells = []
    for row_number, row in enumerate(rows):
        for column_number, _text in enumerate(row):
            cell = pymupdf.Rect(70 + column_number * 150, top + row_number * row_height,
                                220 + column_number * 150, top + (row_number + 1) * row_height)
            shape.draw_rect(cell, radius=0.1)
            shape.finish(color=None, fill=CELL_FILL)
            shape.draw_rect(cell, radius=0.1)
            shape.finish(color=CELL_BORDER, fill=None, width=1)
            cells.append(cell)
    shape.commit()
    for row_number, row in enumerate(rows):
        for column_number, text in enumerate(row):
            page.insert_text((80 + column_number * 150, top + 24 + row_number * row_height), text, fontsize=10)

    return cells


def _draw_tick(page: pymupdf.Page,
               corner: pymupdf.Point) -> None:
    shape = page.new_shape()
    shape.draw_polyline([corner + (0, 5), corner + (4, 9), corner + (11, 0)])
    shape.finish(color=(0.2, 0.5, 0.2), width=1.5, closePath=False)
    shape.commit()


def _picture_bytes(height: int,
                   width: int,
                   is_placeholder: bool) -> bytes:
    """A grey field with one small file icon in its middle (a placeholder), or a screen with a header, lines of
    text and a button."""

    if is_placeholder:
        picture = numpy.full((height, width, 3), 168, dtype=numpy.uint8)
        cv2.rectangle(picture, (width // 2 - 8, height // 2 - 10), (width // 2 + 8, height // 2 + 10), (60, 70, 80), -1)
    else:
        picture = numpy.full((height, width, 3), 250, dtype=numpy.uint8)
        cv2.rectangle(picture, (0, 0), (width, height // 8), (90, 60, 30), -1)
        for line in range(6):
            y = height // 4 + line * height // 10
            cv2.line(picture, (width // 10, y), (width * 8 // 10, y), (40, 40, 40), 2)
        cv2.rectangle(picture, (width * 7 // 10, height * 8 // 10), (width * 9 // 10, height * 9 // 10), (0, 200, 240), -1)

    return cv2.imencode(".png", picture)[1].tobytes()


def build(path: Path) -> Path:
    document = pymupdf.open()

    # 1. a table of rounded cells, a tick icon in one of them
    page = document.new_page(width=WIDTH, height=HEIGHT)
    page.insert_text((70, 90), "Properties of the onboarding page", fontsize=14)
    cells = _draw_rounded_table(page=page, top=120, rows=ROWS)
    _draw_tick(page=page, corner=cells[1].top_left + (130, 14))

    # 2. a grey placeholder in a rounded frame, then a real screenshot
    page = document.new_page(width=WIDTH, height=HEIGHT)
    page.insert_text((70, 90), "Swagger documentation", fontsize=14)
    frame = pymupdf.Rect(70, 110, 330, 300)
    shape = page.new_shape()
    shape.draw_rect(frame, radius=0.03)
    shape.finish(color=CELL_BORDER, fill=(0.66, 0.66, 0.66))
    shape.commit()
    page.insert_image(frame + (2, 2, -2, -2), stream=_picture_bytes(height=190, width=260, is_placeholder=True))
    page.insert_text((70, 330), "Before the overview, the partner opens the gateway's start page.", fontsize=11)
    page.insert_image(pymupdf.Rect(70, 360, 400, 560), stream=_picture_bytes(height=200, width=330, is_placeholder=False))
    page.insert_text((70, 580), "The partner signs in and sees the tenant overview.", fontsize=11)

    # 3. a table of rounded cells holding one small screenshot
    page = document.new_page(width=WIDTH, height=HEIGHT)
    rows = [["Step", "Screen"], ["Sign in", ""], ["Overview", "Tenant list"], ["Logout", "Start page"]]
    cells = _draw_rounded_table(page=page, top=120, rows=rows, row_height=80)
    page.insert_image(cells[3] + (5, 5, -5, -5), stream=_picture_bytes(height=70, width=140, is_placeholder=False))

    # 4. a bar chart: bars apart from each other, an axis, the months under it
    page = document.new_page(width=WIDTH, height=HEIGHT)
    shape = page.new_shape()
    shape.draw_line((100, 400), (500, 400))
    shape.finish(color=(0, 0, 0))
    for step, height in enumerate([120, 180, 90, 200, 150, 110, 170, 60]):
        shape.draw_rect(pymupdf.Rect(110 + step * 48, 400 - height, 140 + step * 48, 400))
        shape.finish(fill=(0.2, 0.4, 0.8))
    shape.commit()
    for step, month in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]):
        page.insert_text((114 + step * 48, 414), month, fontsize=9)

    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)

    return path
