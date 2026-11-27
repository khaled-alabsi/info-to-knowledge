"""Build `out/07-hard-figures.pdf`: nine pages, each a hard case for finding a PDF's figures, made of public-domain
NASA photographs of people (`../public_domain_faces/SOURCES.md`) and invented text.

Run: `.venv/bin/python tests/resources/pdf/build_hard_figures_pdf.py` from the playbook folder. The test that reads it
(`test_pdf_source.test_the_hard_figures_are_found_one_each`) builds it again when it is missing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
FACES = HERE.parent / "public_domain_faces"
OUT = HERE / "out" / "07-hard-figures.pdf"
WIDTH, HEIGHT = 595, 842
TEXT = ("The gateway team reviewed the launch plan with the partners. Each partner signs its tokens before they leave, "
        "and the gateway checks the audience of every token it receives.")


def _paragraph(page: pymupdf.Page,
               box: pymupdf.Rect,
               text: str = TEXT) -> None:
    page.insert_textbox(box, text, fontsize=11)


def _photo(page: pymupdf.Page,
           box: pymupdf.Rect,
           name: str) -> None:
    page.insert_image(box, filename=str(FACES / name), keep_proportion=False)


def build(path: Path = OUT) -> Path:
    """The nine pages, written to `path`; the path."""

    document = pymupdf.open()

    # 1: a group photo of people with its "Figure 1" line under it
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _paragraph(page, pymupdf.Rect(60, 60, 535, 120))
    _photo(page, pymupdf.Rect(60, 130, 535, 503), "Apollo_11_Crew.jpg")
    page.insert_text((60, 520), "Figure 1. The launch team before the first partner went live", fontsize=9)
    _paragraph(page, pymupdf.Rect(60, 560, 535, 640))

    # 2: two portraits side by side, each with its own caption: two figures, never one
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _paragraph(page, pymupdf.Rect(60, 60, 535, 120))
    _photo(page, pymupdf.Rect(60, 140, 280, 415), "Neil_Armstrong_pose.jpg")
    _photo(page, pymupdf.Rect(315, 140, 535, 415), "Buzz_Aldrin.jpg")
    page.insert_text((60, 432), "Figure 2. The gateway's first owner", fontsize=9)
    page.insert_text((315, 432), "Figure 3. Its second owner", fontsize=9)

    # 3: a photo with the page's title written over it
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _photo(page, pymupdf.Rect(60, 60, 535, 653), "Mae_Carol_Jemison.jpg")
    page.insert_text((80, 100), "Partner onboarding", fontsize=28, color=(1, 1, 1))
    _paragraph(page, pymupdf.Rect(60, 680, 535, 760))

    # 4: a small headshot beside a biography, about 3% of the page
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _photo(page, pymupdf.Rect(60, 60, 160, 180), "Sally_Ride_1984.jpg")
    _paragraph(page, pymupdf.Rect(175, 60, 535, 200), "Mara Keller leads the gateway's partner team. " + TEXT)

    # 5: a directory of people, a headshot in each row of a table
    page = document.new_page(width=WIDTH, height=HEIGHT)
    for row, (name, face) in enumerate((("Jonas Weber", "Neil_Armstrong_pose.jpg"), ("Mara Keller", "Sally_Ride_1984.jpg"),
                                        ("Lena Brandt", "Buzz_Aldrin.jpg"))):
        top = 80 + row * 110
        for left, right in ((60, 200), (200, 535)):
            page.draw_rect(pymupdf.Rect(left, top, right, top + 110), color=(0, 0, 0))
        _photo(page, pymupdf.Rect(80, top + 8, 180, top + 102), face)
        page.insert_text((215, top + 50), f"{name}, partner onboarding", fontsize=11)

    # 6: a bar chart drawn as vectors, with its axis, labels and legend, and a "Figure 4" line under it
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _paragraph(page, pymupdf.Rect(60, 60, 535, 120))
    shape = page.new_shape()
    shape.draw_line((100, 400), (500, 400))
    shape.draw_line((100, 400), (100, 160))
    for step in range(6):
        shape.draw_line((95, 400 - step * 40), (100, 400 - step * 40))
    shape.finish(color=(0, 0, 0))
    for index, value in enumerate((120, 180, 90, 220, 150)):
        shape.draw_rect(pymupdf.Rect(130 + index * 70, 400 - value, 170 + index * 70, 400))
        shape.finish(fill=(0.2, 0.4, 0.8), color=(0.2, 0.4, 0.8))
    shape.draw_rect(pymupdf.Rect(420, 170, 432, 182))
    shape.finish(fill=(0.2, 0.4, 0.8))
    shape.commit()
    for index, month in enumerate(("Jan", "Feb", "Mar", "Apr", "May")):
        page.insert_text((135 + index * 70, 415), month, fontsize=9)
    page.insert_text((437, 180), "calls (thousands)", fontsize=8)
    page.insert_text((100, 440), "Figure 4. Calls through the gateway by month", fontsize=9)

    # 7: a poster page: one photo filling the page, its title over it, the page's text layer present
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _photo(page, pymupdf.Rect(0, 0, WIDTH, HEIGHT), "Mae_Carol_Jemison.jpg")
    page.insert_text((40, 780), "The partner programme 2026", fontsize=30, color=(1, 1, 1))

    # 8: one photo stored as two strips, top and bottom, as some exporters tile a picture: one figure
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _paragraph(page, pymupdf.Rect(60, 60, 535, 120))
    crew = Image.open(FACES / "Apollo_11_Crew.jpg")
    half = crew.height // 2
    for strip, box in ((crew.crop((0, 0, crew.width, half)), pymupdf.Rect(60, 140, 535, 326)),
                       (crew.crop((0, half, crew.width, crew.height)), pymupdf.Rect(60, 326, 535, 513))):
        stream = io.BytesIO()
        strip.save(stream, format="JPEG", quality=90)
        page.insert_image(box, stream=stream.getvalue(), keep_proportion=False)
    page.insert_text((60, 530), "Figure 5. The team, stored in two strips", fontsize=9)

    # 9: a photo in a framed box with its caption inside the frame, as a wiki lays one out
    page = document.new_page(width=WIDTH, height=HEIGHT)
    _paragraph(page, pymupdf.Rect(60, 60, 300, 400), TEXT + " " + TEXT)
    page.draw_rect(pymupdf.Rect(320, 60, 535, 380), color=(0.7, 0.7, 0.7))
    _photo(page, pymupdf.Rect(328, 68, 527, 318), "Sally_Ride_1984.jpg")
    page.insert_textbox(pymupdf.Rect(328, 324, 527, 376), "Mara Keller at the partner kick-off", fontsize=9)

    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)

    return path


if __name__ == "__main__":
    print(build())
