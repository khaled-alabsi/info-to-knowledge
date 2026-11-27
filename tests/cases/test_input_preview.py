"""What the run form's Range editor shows of an inbox recording before any run, made by the real
ffmpeg and PyMuPDF on made media: a frame of `tests/resources/scenes/loop-slide-table.mp4`, the waveform of
`tests/resources/audio/two-voices.wav`, a page of a PDF built here."""

import tempfile
from pathlib import Path

import cv2
import pymupdf

from support import harness

from services import input_preview_service

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
VIDEO = RESOURCES / "scenes" / "loop-slide-table.mp4"
AUDIO = RESOURCES / "audio" / "two-voices.wav"


class _PreviewsIn:
    """Previews written into a folder of the test's own, the module's folder put back after."""

    def __init__(self) -> None:
        self.folder = Path(tempfile.mkdtemp())

    def __enter__(self):
        self.saved = input_preview_service.PREVIEWS_DIR
        input_preview_service.PREVIEWS_DIR = self.folder

        return self.folder

    def __exit__(self, *exc):
        input_preview_service.PREVIEWS_DIR = self.saved


def test_a_video_frame_preview_is_a_small_picture_made_once():
    if not VIDEO.is_file():
        print("scene video not built: skipped")
        return
    with _PreviewsIn() as folder:
        first = input_preview_service.build_frame_preview(VIDEO, 12.0)
        made_at = first.stat().st_mtime_ns
        again = input_preview_service.build_frame_preview(VIDEO, 12.0)
    image = cv2.imread(str(first))
    harness.check("a picture 240 wide", image is not None and image.shape[1] == 240, str(None if image is None else image.shape))
    harness.check("kept in the previews folder", first.parent == folder)
    harness.check("asked again, the same file, not made again", again == first and again.stat().st_mtime_ns == made_at)


def test_a_waveform_holds_one_loudness_a_bar_from_0_to_1():
    with _PreviewsIn():
        peaks = input_preview_service.compute_waveform(AUDIO, 50)
    harness.check("50 bars", len(peaks) == 50, str(len(peaks)))
    harness.check("from 0 to 1, the loudest at 1", all(0.0 <= peak <= 1.0 for peak in peaks) and max(peaks) == 1.0,
                  str(peaks))
    harness.check("speech is louder than its quietest stretch", min(peaks) < max(peaks), str(peaks))


def test_a_pdf_page_preview_is_its_page_small_and_a_missing_page_is_refused():
    pdf = Path(tempfile.mkdtemp()) / "guide.pdf"
    document = pymupdf.open()
    for _ in range(3):
        document.new_page(width=595, height=842)
    document.save(pdf)
    document.close()
    with _PreviewsIn():
        page = input_preview_service.build_page_preview(pdf, 2)
        try:
            input_preview_service.build_page_preview(pdf, 4)
            refused = ""
        except ValueError as error:
            refused = str(error)
    image = cv2.imread(str(page))
    harness.check("a picture 160 wide, a page's shape", image is not None and image.shape[1] == 160
                  and image.shape[0] > image.shape[1], str(None if image is None else image.shape))
    harness.check("page 4 of 3 refused by name", "no page 4" in refused, refused)
