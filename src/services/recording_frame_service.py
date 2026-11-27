"""One frame of the recording, decoded at its own resolution. REQUIREMENTS.md R10.

Phase 2 stores each frame it took cut to the shared screen and at most 1280 pixels wide, which is what a model reads
well. A screenshot in the document is cut from the recording itself, at the same frame number phase 2 took, so a
small region of a large screen stays readable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy

from models.video_errors import UnreadableVideo
from services import pdf_source_service
from services import recording_parts_service

__all__ = ["read_figure_parts", "read_recording_frame"]

_LOG = logging.getLogger(__name__)


def read_recording_frame(video_path: Path,
                         frame_number: int) -> numpy.ndarray | None:
    """The frame, as a BGR array, or None when the recording cannot be opened or has no such frame. A recording in
    parts is read as one video: the frame number runs on from one part into the next."""

    # A PDF's frame is its page, rendered at the cut's resolution
    if pdf_source_service.is_pdf(video_path):
        return pdf_source_service.read_page_image(video_path, frame_number)
    try:
        capture = recording_parts_service.open_recording(video_path)
    except UnreadableVideo as error:
        _LOG.warning("frame %d: the recording could not be read, so it is cut from the stored frame (%s)", frame_number,
                     error)
        return None
    try:
        if not capture.is_open():
            return None
        # A seek that failed leaves the reader where it was: the frame read after it is another moment's
        if not capture.move_to_frame(frame_number):
            return None
        is_read, frame = capture.read_frame()
    finally:
        capture.release()

    return frame if is_read else None


def read_figure_parts(pdf: Path,
                      parts: list[dict]) -> numpy.ndarray | None:
    """A PDF figure the page break cut in two, as a BGR array: its parts read at the cut's resolution and stacked as its
    frame is. None when the file is no PDF or lacks a part's page."""

    return pdf_source_service.read_figure_image(pdf, parts) if pdf_source_service.is_pdf(pdf) else None
