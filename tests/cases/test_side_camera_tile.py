"""A lecture's camera inset at mid height on a side of the screen is found from the person in it
(`screen_crop_utils.find_side_tile`) and kept for the seconds its person is not found
(`screen_crop_utils.is_side_tile_kept`, `frame_extraction_service._apply_layout`), tested on drawn frames."""

from unittest import mock

import cv2
import numpy

from support import harness

from models.broadcast_layout_state import BroadcastLayoutState
from models.shared_screen_result import SharedScreenResult
from services import frame_extraction_service
from utils import screen_crop_utils

SCREEN = (0, 0, 1280, 688)
TILE = (940, 237, 1262, 418)
PERSON = (1030, 262, 1170, 418)
TOLERANCE_PX = 6


def _draw_inset(is_inset: bool = True) -> numpy.ndarray:
    """A 1280 x 688 dark terminal with a few lines of text, and when `is_inset` a room camera's inset at the middle of
    its right edge: a dark chalkboard at its top, barely lighter than the terminal, a lit lectern below, a person."""

    gray = numpy.full((688, 1280), 27, numpy.uint8)
    for row in range(40, 640, 34):
        gray[row:row + 14, 30:30 + 20 * (row % 7 + 20)] = 200
    if is_inset:
        x0, y0, x1, y1 = TILE
        gray[y0:y1, x0:x1] = 90
        gray[y0:y0 + 55, x0:x1] = 34
        cv2.ellipse(gray, (1100, 340), (55, 80), 0, 0, 360, 150, -1)

    return gray


def _is_near(found, expected) -> bool:
    return found is not None and all(abs(a - b) <= TOLERANCE_PX for a, b in zip(found, expected))


def test_a_camera_inset_at_mid_height_on_a_side_is_found_from_its_person_defect():
    # A lecture's camera inset at the middle of the screen's right edge was left on every frame: the cover looked only
    # in the screen's corners, by a face. It is now found from the person detector's box, on either side
    gray = _draw_inset()
    found = screen_crop_utils.find_side_tile(gray, SCREEN, [PERSON])
    harness.check("the inset on the right is found", _is_near(found, TILE), f"found {found}, expected {TILE}")
    mirrored = numpy.ascontiguousarray(gray[:, ::-1])
    person = (1280 - PERSON[2], PERSON[1], 1280 - PERSON[0], PERSON[3])
    expected = (1280 - TILE[2], TILE[1], 1280 - TILE[0], TILE[3])
    found = screen_crop_utils.find_side_tile(mirrored, SCREEN, [person])
    harness.check("the same inset on the left is found", _is_near(found, expected), f"found {found}, expected {expected}")


def test_a_person_with_no_inset_round_them_is_no_tile():
    gray = _draw_inset(is_inset=False)
    cv2.ellipse(gray, (1100, 340), (55, 80), 0, 0, 360, 150, -1)
    found = screen_crop_utils.find_side_tile(gray, SCREEN, [PERSON])
    harness.check("no tile without the inset's edges", found is None, str(found))
    middle = (560, 262, 700, 418)
    found = screen_crop_utils.find_side_tile(_draw_inset(), SCREEN, [middle])
    harness.check("a person in the middle of the screen is no side tile", found is None, str(found))


def test_a_side_tile_is_kept_for_the_seconds_its_person_is_missed_defect():
    # The person detector missed the lecturer behind the lectern on 2 of 8 frames, and those frames kept the inset.
    # A tile found is now kept while its edges still stand, and dropped once they are gone
    layout = BroadcastLayoutState()
    frame_extraction_service._apply_layout(SharedScreenResult(SCREEN, TILE), _draw_inset(), layout)
    kept = frame_extraction_service._apply_layout(SharedScreenResult(SCREEN, None), _draw_inset(), layout)
    harness.check("a second with no person found keeps the tile", kept.tile == TILE, str(kept))
    gone = frame_extraction_service._apply_layout(SharedScreenResult(SCREEN, None), _draw_inset(is_inset=False), layout)
    harness.check("a second without the inset has no tile", gone.tile is None, str(gone))


def test_an_inset_s_inner_side_is_its_border_never_its_person_s_shirt_defect():
    # Over a dark terminal a lecture's inset was found with its inner side on the lecturer's shirt, its edge stronger
    # than the inset's own border, so the face stayed on the frame; the inner side is now the passing edge furthest out
    gray = _draw_inset()
    gray[PERSON[1]:PERSON[3], PERSON[0] + 20:PERSON[2] - 20] = 170
    found = screen_crop_utils.find_side_tile(gray, SCREEN, [PERSON])
    harness.check("the inset's border is its inner side", _is_near(found, TILE), f"found {found}, expected {TILE}")


def test_a_person_standing_far_past_a_closed_box_is_no_inset_defect():
    # A room shot's lectern and panelling closed a box round the middle of a lecturer standing beside them, and the
    # cover hid the lectern; a tile now holds its person but for a quarter of its height above or below
    tall = (PERSON[0], TILE[1] - 150, PERSON[2], TILE[3] + 120)
    with mock.patch.object(screen_crop_utils, "_find_right_side_tile", lambda region, person: TILE):
        found = screen_crop_utils.find_side_tile(_draw_inset(), SCREEN, [tall])
        harness.check("a person running far past the box has no tile", found is None, str(found))
        held = (PERSON[0], TILE[1] + 10, PERSON[2], TILE[3] + 20)
        found = screen_crop_utils.find_side_tile(_draw_inset(), SCREEN, [held])
        harness.check("a person inside the box still has its tile", found == TILE, str(found))


def test_a_side_strip_with_no_person_found_is_read_alone_defect():
    # Lecture 7's inset kept its lecturer, about 30 pixels at the detector's 416-pixel reading of the whole frame,
    # unfound on 4 frames, so the inset stayed; each side's strip with nobody found on it is now read alone
    frame = numpy.zeros((1080, 1920, 3), numpy.uint8)
    strip_person = (100, 400, 160, 600)

    def detect(image):
        return [] if image.shape[1] == 1920 else [strip_person]

    with mock.patch.object(frame_extraction_service.person_detection_service, "detect_people", detect):
        people = frame_extraction_service._detect_side_people(frame)
    right = 1920 - round(screen_crop_utils.PANEL_SIDE * 1920)
    harness.check("each strip's person is placed in the frame's pixels",
                  people == [strip_person, (100 + right, 400, 160 + right, 600)], str(people))
    standing = (1700, 400, 1800, 600)
    with mock.patch.object(frame_extraction_service.person_detection_service, "detect_people",
                           lambda image: [standing] if image.shape[1] == 1920 else [strip_person]):
        people = frame_extraction_service._detect_side_people(frame)
    harness.check("a side with a person found is not read again", people == [standing, strip_person], str(people))


def test_the_look_ahead_hands_its_first_camera_tile_to_the_loop_defect():
    # Lecture 7's first frames came before the loop's first person found, and kept the inset; the look ahead of the
    # loop now keeps the first tile it finds, which the loop keeps while its edges stand
    class Capture:
        def move_to_frame(self, number):
            return True

        def read_frame(self):
            return True, numpy.zeros((688, 1280, 3), numpy.uint8)

        def release(self):
            pass

    found = iter([SharedScreenResult(SCREEN, None), SharedScreenResult(SCREEN, TILE),
                  SharedScreenResult(SCREEN, (10, 237, 330, 418))])
    with mock.patch.object(frame_extraction_service.recording_parts_service, "open_recording", lambda path: Capture()), \
            mock.patch.object(frame_extraction_service, "_find_shared_screen", lambda frame, gray: next(found)):
        layout = frame_extraction_service._find_layout_ahead(None, {"durationS": 90, "frameTimes": list(range(90))},
                                                             (0.0, 90.0))
    harness.check("the first tile found ahead is kept", layout.tile == TILE, str(layout.tile))
    kept = frame_extraction_service._apply_layout(SharedScreenResult(SCREEN, None), _draw_inset(), layout)
    harness.check("the loop's first second, its person not found, has the tile", kept.tile == TILE, str(kept))
