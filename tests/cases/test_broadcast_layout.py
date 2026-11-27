"""A conference broadcast's slide panel, once found, is kept over phase 2's capture loop while the frame around it
matches (`frame_extraction_service._apply_layout`, `screen_crop_utils.is_same_layout`), tested on drawn frames."""

import numpy

from support import harness

from models import screen_box
from models.broadcast_layout_state import BroadcastLayoutState
from models.shared_screen_result import SharedScreenResult
from services import frame_extraction_service
from utils import screen_crop_utils

PANEL = (0, 0, 400, 300)
FULL = (0, 0, 640, 480)


def _draw(backdrop: int,
          slide: int,
          speaker_at: int = 500) -> numpy.ndarray:
    """A 640 x 480 grey frame: a slide in the top left on a backdrop, and a speaker's camera on the right."""

    gray = numpy.full((480, 640), backdrop, numpy.uint8)
    gray[0:300, 0:400] = slide
    gray[320:440, speaker_at:speaker_at + 80] = 200

    return gray


def test_the_panel_found_once_is_kept_while_the_frame_around_it_matches():
    layout = BroadcastLayoutState()
    first = frame_extraction_service._apply_layout(SharedScreenResult(PANEL, None, True), _draw(40, 90), layout)
    harness.check("a panel found is used and kept", first.box == PANEL and layout.panel == PANEL, str(first))
    moved = frame_extraction_service._apply_layout(SharedScreenResult(FULL, None), _draw(40, 160, speaker_at=520), layout)
    harness.check("a later second with another slide and the speaker moved keeps the panel",
                  moved.box == PANEL and moved.is_panel, str(moved))
    full = frame_extraction_service._apply_layout(SharedScreenResult(FULL, None), _draw(230, 230), layout)
    harness.check("the slide shown in full changes the frame around the panel: the whole frame is kept",
                  full.box == FULL and not full.is_panel, str(full))
    # A dark slide shown in full, nearly the backdrop's grey: at a limit of 24 a talk's slide was cut through (2026-10-15)
    dark = numpy.full((480, 640), 47, numpy.uint8)
    dark_full = frame_extraction_service._apply_layout(SharedScreenResult(FULL, None), dark, layout)
    harness.check("a dark slide in full, seven levels off the backdrop, is not the kept layout",
                  dark_full.box == FULL, str(dark_full))
    none = frame_extraction_service._apply_layout(SharedScreenResult(screen_box.NO_SCREEN, None), _draw(40, 90), layout)
    harness.check("a second with no shared screen stays so", none.box == screen_box.NO_SCREEN, str(none))


def test_no_panel_is_kept_before_one_is_found():
    found = frame_extraction_service._apply_layout(SharedScreenResult(FULL, None), _draw(40, 90), BroadcastLayoutState())
    harness.check("with no panel found yet the box is the one found", found.box == FULL, str(found))
    outside = screen_crop_utils.shrink_outside_panel(_draw(40, 90), PANEL)
    harness.check("the sample blanks the panel's area", numpy.isnan(outside[0, 0]) and outside[-1, 0] == 40,
                  str(outside[0, 0]))
