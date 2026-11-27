"""Where the shared screen is in a frame of a Teams recording, as plain code over pixels. No I/O: the faces a frame
shows are found by `face_detection_service` and passed in.

Measured in `research/crop-methods/`: exact on 23 real frames and 9 rebuilt layouts, and on screenshots of five other
meetings. The rules below are the ones that held there.

The BARS inside the shared screen (a Mac menu bar, a remote desktop's title bar, a taskbar, an app's status bar) are
found by what they do, not by how they look: they stay the same while the content beside them changes. So they are
found from several frames of one screen box at once (`find_bar_margins`), never from one.
"""

from __future__ import annotations

import re
from typing import Callable

import cv2
import numpy

from models.pending_box_state import PendingBoxState
from models.screen_box import NO_BARS, BarMargins, Box
from utils import person_name_utils

__all__ = ["count_still_edges", "find_framed_share", "is_lone_camera", "find_camera_tile", "find_side_tile", "is_side_tile_kept", "cover_camera_tile", "has_face_inside", "is_call_window", "count_content_words", "find_screen_box", "choose_crop_box", "find_bar_margins", "find_edge_strips", "keep_words_whole",
           "is_blank", "is_box_check_due", "find_slide_panel", "is_outline_standing", "count_words_outside", "clip_panel_to_box",
           "LONE_CAMERA_MAX_WORDS", "SLIDE_PANEL_MAX_OUTSIDE_WORDS"]

STAGE_LEVEL = 4                             # Teams' stage is exactly 0; dark slides and dark apps start near 5
PANEL_COLUMN_STAGE = 0.35                   # a column more stage than this lies outside the screen: a faces panel
LETTERBOX_ROW_STAGE = 0.9                   # a row this much stage is letterbox
# A letterboxed slide: a column with up to LETTERBOXED_COLUMN_STAGE stage is still the screen when its content spans at
# least LETTERBOXED_MIN_CONTENT of the height with at most LETTERBOXED_INSIDE_STAGE stage inside that span. A slide's
# own black corner joined the stage there (measured 0.7% to 4%); a camera panel's gaps between its tiles are far more
# (about 28% to 40%).
LETTERBOXED_COLUMN_STAGE = 0.5              # the most stage a column of a letterboxed slide holds
LETTERBOXED_MIN_CONTENT = 0.5               # the least of the height its content spans
LETTERBOXED_INSIDE_STAGE = 0.1              # the most stage inside that span
MIN_SCREEN_AREA = 0.2                       # less of the frame than this left over is no screen at all
# A crop whose pixels lie this much within BLANK_SPREAD grey levels of its median shows nothing: stage black once the share
# stopped, or a lecture's flat title card. Measured on a lecture's 189 frames: its red opening card 100%, the next
# flattest frame (a slide with little on it) 96.6%.
BLANK_SHARE = 0.99
BLANK_SPREAD = 8
# Static noise is blank too: no 10 x 10 patch flat within 4 grey levels at 320 x 180 (shrunk by area), and a mean edge
# strength over 80 there; a broadcast's noise frame measured 103.6 with no flat patch, while 600 frames of other runs
# all had a flat patch and at most 46.6
NOISE_EDGE = 80.0
NOISE_FLAT_SPREAD = 4.0
GALLERY_MIN_FACES = 2                       # a screen with at least this many faces ...
GALLERY_FACE_AREA = 0.025                   # ... covering this share of it is a gallery of cameras, not content.
                                            # Measured: galleries 3.5% to 6.7%, slides, demos and a web page's
                                            # group photo 0%, where the detector found no face at all
LONE_FACE_AREA = 0.015                      # one face covering this share of the screen ...
LONE_FACE_OTHERS_SHARE = 0.25               # ... with every other face under this share of its size (a self-view
                                            # tile in a corner, which a speaker view keeps) ...
LONE_FACE_MIDDLE = 0.6                      # ... its centre in this middle share of the width is a lone camera, when
LONE_CAMERA_MAX_WORDS = 10                  # the text reader finds fewer words than this on the screen (2026-10-02)
# A call window's tiles each carry a person's name, "<Last>, <First>", at their foot: with a face on the screen, this
# many separate names, one marked as a person (a guest mark or an honorific), or one name and fewer than
# LONE_CAMERA_MAX_WORDS words of content, are a call window, however small its faces (2026-10-10: a browser call's
# gallery, people sitting back, faces 1.9% of it, and a crop of two of its tiles, one face at 1.28% and one of its two
# labels read, were taken as frames). Unmarked pairs alone are no call: a dense slide held two pairs of nouns with
# commas beside a photo. The pairs of one list ("Kauf, Tausch, Sparplan") overlap, sharing a word, and count once; a
# slide introducing people names them "<First> <Last>".
CALL_TILE_NAMES = 2
# Words in the screen's top and bottom bands do not count as content: a call window's title and button bar (camera,
# microphone, share, leave) read 16 words over a speaker view, which was then kept as a slide (2026-10-08). The same
# bands the screen-text check leaves out as form.
# A conference broadcast: the projected slide as a panel of one colour in part of the picture, the speaker's camera
# beside it on a backdrop. The panel is the largest area within SLIDE_PANEL_BAND grey levels of one level (bands
# SLIDE_PANEL_STEP apart, light and dark slides alike), its text gaps closed, filling at least SLIDE_PANEL_FILL of its
# box, the box covering SLIDE_PANEL_MIN_AREA to SLIDE_PANEL_MAX_AREA of the frame, with a face outside it. Measured over
# eleven runs: congress slides fill 99% of their box; a fill of 75% also took parts of classroom boards and half of a
# shared screen, so 90% stays.
SLIDE_PANEL_STEP = 12
# ... and one band for everything light, from this grey level up, of any colour: a light page of many tones (a map)
# fits no 24-wide band, and no backdrop is light
SLIDE_PANEL_LIGHT = 190
# A slide's ground is near neutral (white, grey, a pale tint); a congress backdrop's lines are saturated colour of the
# same grey level, which joined a dark slide to the backdrop. Only pixels under this saturation (HSV, 0 to 255) count:
# the congress backdrop's purple measured about 150 to 180, a pale yellow web page under 120.
SLIDE_PANEL_MAX_SATURATION = 120
# ... or whose colour channels differ by at most this many levels: a dark pixel's saturation comes from a few levels of
# colour, and a talk's dark brown slide (RGB 32, 16, 14) measured 143, so no band counted it and the slide was cut to a
# neutral picture inside it (froscon 3147, DEFECTS 306)
SLIDE_PANEL_MAX_CHROMA = 30
# A projected slide is about 4:3 to 16:9 wide: a panel whose width over height lies outside this is no slide (a strip of
# a classroom's blackboard above its rail measured 2.1 to 4.7; congress slides 1.24 to 1.78, and a web page under its
# own dark caption bar 1.05)
SLIDE_PANEL_MIN_ASPECT = 0.9
SLIDE_PANEL_MAX_ASPECT = 1.9
SLIDE_PANEL_BAND = 12
SLIDE_PANEL_CLOSE = 0.02                     # the closing kernel, as a share of the frame width
SLIDE_PANEL_FILL = 0.9
# A slide's edges are straight: the box keeps the longest run of columns, then of rows, that the panel fills this much,
# so a camera's grey parts joined to it across a narrow gap are left out; the trimmed box must still hold
# SLIDE_PANEL_KEPT of the piece, so a piece that is no rectangle (a gallery's tiles, a call window) is never a panel
SLIDE_PANEL_EDGE_FILL = 0.6
SLIDE_PANEL_KEPT = 0.9
# ... or this share when the trimmed box is a solid rectangle, filled this much: a white slide touching the white clouds
# of a talk's backdrop kept 82% of its piece, the clouds the rest, its own box filled 100% (DEFECTS 189)
SLIDE_PANEL_SOLID_KEPT = 0.8
SLIDE_PANEL_SOLID_FILL = 0.98
# Kept only as such, a face inside its piece is beside it when the face's tile runs this share of the frame's height
# past the box's top or bottom: a camera on white clouds, joined to the slide's piece, ran its edge 114 to 127 px past
# a 576-row slide; a photo inside a slide, and every other still, stopped 6 px or more short (DEFECTS 205). Its edge is
# the column between box and face whose horizontal gradient passes SLIDE_PANEL_TILE_EDGE along the longest run
SLIDE_PANEL_TILE_PAST = 0.05
SLIDE_PANEL_TILE_EDGE = 100
# that talk's slide covered 24% of the frame, its camera and backdrop the rest
SLIDE_PANEL_MIN_AREA = 0.2
SLIDE_PANEL_MAX_AREA = 0.9
# ... and the picture outside it holds fewer words than this: a backdrop holds none (at most 5 on a congress talk's
# camera frames), content beside a light page does (18 to 59 on a shared screen whose browser stood beside a terminal)
SLIDE_PANEL_MAX_OUTSIDE_WORDS = 10
# A slide's own outline: a straight edge along this share of the panel's side, within this share of the frame from it.
# One talk's slide ended 34 px right of its panel (a code block in a darker grey), its outline along every row
SLIDE_PANEL_OUTLINE_SHARE = 0.9
SLIDE_PANEL_OUTLINE_REACH = 0.08
SLIDE_PANEL_OUTLINE_SNAP = 3                # a side this many pixels from an outline lies on it
# A panel side is moved in to the first line, from that side, where SLIDE_FIELD_MIN of the
# pixels hold the slide's own field colour (its median, every channel within SLIDE_FIELD_SPREAD), then out over the
# lines just past it holding SLIDE_FIELD_EDGE, when that colour fills SLIDE_FIELD_MIN of the panel: 38c3-openpv's
# near-black slide and the dark backdrop under it fell in one grey band, and the panel ran to the picture's bottom
# (DEFECTS 619); the field's rows held 71% to 98% of its colour, its text rows 26% to 33%, its edge rows 20% to 52%,
# the backdrop's rows at most 13%
SLIDE_FIELD_SPREAD = 6
SLIDE_FIELD_MIN = 0.5
SLIDE_FIELD_EDGE = 0.2
# A slide of many tones (a photo behind its text) on a flat dark backdrop: the backdrop is the frame's border grey,
# within this many levels, and dark; the slide is the largest part not backdrop, filling its box this share. A 39c3 talk's
# slides lay on a black backdrop 17 levels grey beside the camera and the stream's banner, on 13 of 13 frames found
# at 88% to 99.5% filled; a white page's border grey is its own background, and its pieces filled 56%
SLIDE_BACKDROP_SPREAD = 6
SLIDE_BACKDROP_MAX_LEVEL = 40
SLIDE_BACKDROP_FILL = 0.85
# A slide's header or footer band in another colour than its body: rows above or below the panel, each 60% one grey
# (within 12 levels of the band's own), differing by 15 levels on average from the 3 pixels just outside both sides,
# 3% to 25% of the frame's height: a broadcast's dark green title band over a white body measured 16%, its rows 67% to
# 100% one grey, 25 to 174 levels from the backdrop beside it
SLIDE_BAND_SHARE = 0.6
SLIDE_BAND_SPREAD = 12
SLIDE_BAND_SIDE_JUMP = 15
SLIDE_BAND_FRAMED = 0.8                    # the share of a band's rows it must differ from what stands beside it on
SLIDE_BAND_MIN = 0.03
SLIDE_BAND_MAX = 0.25
# A broadcast's layout is fixed for the talk: the slide panel, once found, is kept while the frame around it matches
# (the panel rule found the slide on 7 of 105 frames of one talk, the same box every time, 2026-10-15). A broadcast's
# backdrop is static: over two talks sampled every 10 s, the same layout differed by 0 to 1 grey levels (one second 15),
# while a slide shown in full, a dark slide in full with black borders, another layout or the speaker alone differed by 6
# or more; at 24, two dark-slide seconds of one talk matched and their slide was cut through
LAYOUT_SAMPLE_WIDTH = 96
LAYOUT_MATCH_LEVELS = 4
CHROME_TOP_SHARE = 0.09
CHROME_BOTTOM_SHARE = 0.08
# A frame whose inside, the chrome bands above left out, lies this much within BLANK_SPREAD of its median shows nothing
# but its window's bars: a cleared terminal under its prompt line and status bar (DEFECTS 558). Over the 772 kept frames
# of nine public video runs, four such terminals measured 99.69% to 99.84%; the next were a one-line terminal (98.9%), a
# terminal of a few lines (98.1%) and a section title slide holding one word (98.0%).
EMPTY_INSIDE_SHARE = 0.995
# A chrome band holds a window's bar when this share of its pixels stands off the band's median by more than BLANK_SPREAD:
# a cleared terminal's prompt corner line covers about 3% of the top band. A white terminal scrolled to its last lines
# holds its text in the bottom band with nothing above, and read as empty without this (DEFECTS 664)
BAR_INK_SHARE = 0.002
PANEL_SIDE = 0.3                            # a face centred in this outer share of the screen may sit in a panel
PANEL_EDGE_SEARCH = 2.5                     # face widths searched, between the faces and the content, for the edge
# A column of faces beside the screen searches further: its tiles are wider than their faces, and in a meeting whose
# participants sat far from their cameras the tile's edge stood 2.7 face widths from the face (2026-10-02). A row on
# top or at the bottom keeps 2.5: further, the faces of a slide's pictures were read as a row and the slide was cut.
PANEL_COLUMN_EDGE_SEARCH = 6.0
# A grid of cameras searches from any face it shows, and the detector may see only the outer column's faces: from there
# the share's border stood 424 pixels, 8 to 10 face widths, inward, past the 6 a column searches, and two such checks
# in a row put the whole call window back as the crop (2026-10-10)
GRID_EDGE_SEARCH = 12.0
EDGE_TIE = 0.05                             # edges this close to the strongest count as equally strong
PANEL_EDGE_ROWS = 0.5                       # the panel's edge runs along at least this share of the screen's height
EDGE_JUMP = 20                              # grey levels across a column that make it an edge in that row
EDGE_STEP_PX = 3                            # pixels either side of a column compared for that jump
# A presenter's camera floating over the shared screen in one of its corners, as a Teams recording draws the speaker on
# a share, is covered before the frame is taken: a face beside the content is no knowledge, and a person's picture
# does not belong in the notes. Its tile is the face's corner of the screen, closed by a straight edge on each inner
# side. On a 1920 x 1080 frame the tile stood 320 x 180, its left edge 3 face widths from the face and along all of
# its rows, its bottom edge along 77% of its columns: a white shirt met a white page (2026-10-10).
TILE_EDGE_SEARCH = 6.0                      # face widths (and heights) searched from the face inward for each edge
TILE_EDGE_SHARE = 0.6                       # each inner edge runs along at least this share of the tile's side
TILE_MAX_AREA = 0.1                         # a tile covers at most this share of the screen
TILE_GAP_FACES = 0.5                        # rows without the left edge, in face heights, that end it: inside one
                                            # tile it had gaps of 16 rows where the tile met a dark menu bar
# A lecture's camera inset at the middle of the screen's right edge has no corner: on MIT's lectures it stood 320 x 180
# on a 1280-wide frame, 15 px from the edge, its top 230 to 270 px down, and the face detector found the lecturer, small
# and looking down, on 1 of 8 frames where the person detector found him on 6 (DEFECTS 91, 103). Its tile is found
# from the person: a column edge on its inner side and a row edge under it, each along SIDE_TILE_EDGE_SHARE of the
# person's rows and of the tile's columns, its outer side the outermost column edge of its lower half or the screen's
# edge, its top a camera's 16:9 above its bottom, since a dark chalkboard met a dark terminal there with no edge. Over
# 39 such frames of three lectures every tile was found; room shots of the same lectures, a student's head in a corner,
# are refused by the person's centre outside the tile or the tile ending short of the screen's edge
SIDE_TILE_EDGE_SHARE = 0.5
SIDE_TILE_EDGE_REACH = 0.03                 # the tile's outer side ends at most this share of the width from the edge
SIDE_TILE_GAP_SHARE = 0.1                   # rows without the inner side, in the person's height, that end it
SIDE_TILE_MAX_AREA = 0.15                   # a notebook frame cropped to 1153 x 499 held its inset at 10.4%
# The person runs at most this share of the tile's height past its top or its bottom: an inset holds its lecturer
# (Lecture 7's insets 9 to 23 px past, 8% of 276), while a room shot's lectern and panelling closed a tile round a
# lecturer standing 219 px above and 153 px below it (DEFECTS 602)
SIDE_TILE_PERSON_PAST_SHARE = 0.25
CAMERA_ASPECT = 16 / 9
TILE_RING_PX = 4                           # the tile is filled with the median colour of this ring of pixels round it
BOX_WOBBLE = 0.005                          # a found box within this share of the frame width is the same box
BOX_WOBBLE_MIN_PX = 3                       # ... and within this many pixels on a frame too narrow for that share
# A box that only widens the one in use on its left or right side, the side its camera panel was cut off, replaces it
# after this many checks in a row (a check a second while it waits): the face detector found one camera beside a share
# for two seconds, the panel was not cut, and the whole call window became the box (2026-10-10)
PANEL_RETURN_CHECKS = 30
BOX_CONFIRM_CHECKS = 2                      # a different box replaces the current one after this many checks in a row
BAR_PIXEL_CHANGE = 25                       # grey levels a pixel must move, past every pixel near it, to have changed
BAR_SHIFT_PX = 2                            # a pixel matching the other frame this far away has not changed: the
                                            # shared screen shifts by a pixel between frames, and every line of text
                                            # would read as changed
BAR_LINE_DIFFERS = 0.05                     # a row or column differs from a frame when this share of it changed
BAR_STATIC_SHARE = 0.1                      # an edge row differing from fewer than this share of the frames is a bar
BAR_SMOOTHING_PX = 9                        # rows or columns averaged before deciding
BAR_MIN_KEPT = 0.5                          # a cut keeping less than this of a side found stillness, not bars
BAR_MIN_NEIGHBOURS = 3                      # fewer frames to compare with than this decide nothing
# A neighbour changed in more of its pixels shows another view: two terminal frames of one lecture differed in 0.4% to
# 8%, a terminal frame and the room camera's view in 59% to 62%
BAR_SAME_VIEW_CHANGE = 0.5
BAR_WORD_OVERLAP_PX = 2                     # a word reaching this far past a cut line on both sides runs across it
SIDE_BAND_LEVELS = 6                        # a letterbox band's pixels lie within this many grey levels of its grey
SIDE_BAND_FLAT_ROW = 0.92                   # ... in this share of each of its columns
SIDE_BAND_MIN_SHARE = 0.03                  # a band covers at least this share of the width
SIDE_BAND_MOST_SHARE = 0.25                 # ... and at most this
SIDE_BAND_RING_PX = 3                       # columns at the very edge passed over (a speaker's coloured outline)
BAR_WORD_GAP_PX = 3                         # a top or bottom cut keeps this many pixels clear of a line of text
# A bar meets the content at a border: a side is cut only where a straight edge runs along this share of the cut line,
# within BAR_SMOOTHING_PX of it. A deck's fixed header and footer stood still too, on the slide's own background with no
# edge, and were cut once the camera panel's moving faces no longer ran through every row (2026-10-02). A bar's border
# runs along the whole side: Lecture 7's terminal bars measured 100%; a congress deck's still right margin with its
# logo was cut at a speech bubble's border and a screenshot's (51% to 80%) once other views stopped counting as
# neighbours (DEFECTS 110); a drawn taskbar encoded into a video reached about 90%
BAR_EDGE_SHARE = 0.85
# A share framed inside a call window shown in a browser (the call's bars above it, its side bar left of it, the
# presenter's name label on its corner) is cut at its outline: the corner where an edge down at least FRAME_SIDE_SHARE of
# the screen's height starts on an edge across at least FRAME_SIDE_SHARE of its width, both starting within
# FRAME_CORNER_PX of each other, enclosing FRAME_MIN_AREA of the screen or more, with only still rows above it and still
# columns left of it. Cutting where the stillness ended instead cut into the shared page's own still header on one
# stretch and nothing on another, and a name label across the outline undid the left cut (DEFECTS.md 66).
FRAME_SIDE_SHARE = 0.6
# Grey levels between the two sides of an outline: a call on the web in a light theme draws its share inside a line 221
# grey between a 232 rail and a 243 share, 22 levels across it, and 40 kept the whole browser window; in the frames held
# 1280 wide that line blurs to 14 or 15 levels across, and 16 still missed it (DEFECTS 99)
FRAME_EDGE_JUMP = 12
FRAME_CORNER_PX = 4
FRAME_MIN_AREA = 0.5
FRAME_SEARCH_SHARE = 0.4                    # the corner lies in this share of the screen from its top and its left
FRAME_GAP_SHARE = 0.05                      # a break this share of the height or shorter (a name label across it) joins
FRAME_EDGE_REACH = 0.03                     # a share runs to within this share of the screen's right or bottom edge
                                            # the edge down the outline's left side
# Outside the frame, above it and left of it, changes stay in one small spot: against each neighbour whose inside
# changed in at least FRAME_INSIDE_CHANGE of its pixels, the changed pixels above it lie in at most FRAME_OUTSIDE_SPREAD
# of the columns, and those on its left in at most that share of its rows. A call's timer changed 1.5% to 2.8% of the
# columns above its share; a deck's title changing with its large table 12% to 13%. Measured as a share of the
# outside's pixels against the inside's, the timer outweighed a share whose inside changed 0.45% (a run kept the call's
# side bar on that frame, and its notes said "same as" the frame before); measured by rows changed past 5%, the title's
# thin letters never counted. A list of eighteen lines replaced by another changed 0.12% of a share's pixels against
# the frame before, counted one way.
FRAME_OUTSIDE_SPREAD = 0.06
FRAME_INSIDE_CHANGE = 0.0005
# The running time a call's bar shows ("07:24"), read on every frame of the light clip. A change in the band was no
# sign: a share's own header changes too (a tab's spinner, the pointer, a new page), and was cut off as a bar on 2 of
# a meeting's 9 frames, while the call's bar measured unchanged against half its neighbours in the loop (DEFECTS 99)
FRAME_TIMER_PATTERN = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
# A neighbour shows the same outline when its own edge runs along this share of the outline's left side: one taken while
# another window filled the screen (a calendar, the desktop) changed everything outside the share and refused its cut
# on the frames before and after it
FRAME_SAME_OUTLINE = 0.8
# A call on the web letterboxes the shared screen on its flat grey stage, the presenter's name label in the band below:
# the outline found is the stage's, and the shared screen lies inside it. The stage's lines above and below it are cut
# off: each holds FRAME_STAGE_FLAT_ROW of its pixels within FRAME_STAGE_LEVELS grey levels of the stage's grey, the
# bottom band's past the name label. A remote window's light tab bar below the stage had lines 86% flat, and at 80% the
# band ran into its titles; a shared screen's own top lines were 46%. Taking
# the screen's own outline instead failed where the name label crossed its left side: that edge ran on to the bottom
# (DEFECTS 109)
FRAME_STAGE_LEVELS = 8
FRAME_STAGE_FLAT_ROW = 0.95
FRAME_STAGE_MIN_WIDTH = 0.02              # a stage left of a share the box starts inside is at least this wide
FRAME_STAGE_LABEL_SHARE = 0.2               # the name label lies in this left share of the bottom band (14% measured)
# The outline cut runs with this many neighbours of the same box: a call window's own view held for three frames gave
# each two, and the bar trim's three kept the call's bars on all of them (DEFECTS 109)
FRAME_MIN_NEIGHBOURS = 2


def _find_longest_run(flags: numpy.ndarray) -> tuple[int, int] | None:
    """The longest run of True in a 1-D array as (start, end), end exclusive, or None if there is none."""

    best = None
    start = None
    for index, flag in enumerate(list(flags) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if best is None or index - start > best[1] - best[0]:
                best = (start, index)
            start = None

    return best


def _compute_area(box: Box) -> int:
    """The area of a box or a face, x0, y0, x1, y1."""

    return (box[2] - box[0]) * (box[3] - box[1])


def _compute_centre_x(box: Box) -> float:
    return (box[0] + box[2]) / 2


def _compute_centre_y(box: Box) -> float:
    return (box[1] + box[3]) / 2


def _list_faces_inside(box: Box,
                       faces: list[Box]) -> list[Box]:
    """The faces whose centre lies in `box`, end exclusive."""

    x0, y0, x1, y1 = box

    return [f for f in faces if x0 <= _compute_centre_x(f) < x1 and y0 <= _compute_centre_y(f) < y1]


def _find_stage(gray: numpy.ndarray) -> numpy.ndarray:
    """Pure-black pixels connected to the top or bottom edge of the frame: Teams' stage, never a dark app inside the
    screen, which the screen's own bars enclose."""

    is_black = (gray < STAGE_LEVEL).astype(numpy.uint8)
    _, labels = cv2.connectedComponents(is_black, connectivity=4)
    edge_labels = numpy.unique(numpy.concatenate([labels[0], labels[-1]]))
    is_stage = numpy.isin(labels, edge_labels[edge_labels != 0])

    return is_stage


def _find_screen_columns(is_stage: numpy.ndarray) -> numpy.ndarray:
    """The columns that lie inside the screen: little stage, or, up to LETTERBOXED_COLUMN_STAGE, content spanning half
    the height with hardly any stage inside that span. A 16:9 slide in a narrower stage had 35.2% stage in every column
    and was read as a camera panel, dropping a whole meeting's slides (2026-10-06); a panel's tiles have stage between
    them. A slide whose photo ended in a few black rows at its edge had those rows join the stage, and its columns past
    them fell out of the box: the box's edge jumped on every look and cut through the slide."""

    height = is_stage.shape[0]
    share = is_stage.mean(axis=0)
    content = ~is_stage
    has_content = content.any(axis=0)
    first = numpy.argmax(content, axis=0)
    last = height - 1 - numpy.argmax(content[::-1], axis=0)
    span = numpy.where(has_content, last - first + 1, 0)
    inside_stage = span - content.sum(axis=0)
    is_mostly_content = content.mean(axis=0) >= LETTERBOXED_MIN_CONTENT
    has_little_stage_inside = inside_stage <= LETTERBOXED_INSIDE_STAGE * span
    is_one_tall_span = has_content & is_mostly_content & has_little_stage_inside

    return (share < PANEL_COLUMN_STAGE) | ((share <= LETTERBOXED_COLUMN_STAGE) & is_one_tall_span)


def _find_screen_rows(is_stage: numpy.ndarray,
                      x0: int,
                      x1: int) -> tuple[int, int] | None:
    """The longest run of rows between columns `x0` and `x1` that are not letterbox, or None."""

    rows = _find_longest_run(is_stage[:, x0:x1].mean(axis=1) < LETTERBOX_ROW_STAGE)

    return rows


def _is_face_column(faces: list[Box]) -> bool:
    """At least two faces stacked one above the other: centres less than one face width apart sideways, boxes apart up
    and down. One face, a photo in a slide or on a web page, is never a panel."""

    is_column = False
    for first_index, first in enumerate(faces):
        for second in faces[first_index + 1:]:
            face_width = max(first[2] - first[0], second[2] - second[0])
            is_beside = abs((first[0] + first[2]) - (second[0] + second[2])) / 2 <= face_width
            is_apart = first[3] <= second[1] or second[3] <= first[1]
            is_column = is_column or (is_beside and is_apart)

    return is_column


def _keep_outer_stack(faces: list[Box],
                      is_right: bool) -> list[Box]:
    """The largest stack of faces, each centre within one face width of the others' (the one nearer the side on a tie).
    A face in a slide's picture that also lay in the outer part moved the panel's inner side onto the slide, and the
    panel's own edge was never searched; and the outermost face alone can be a lone avatar in a corner (2026-10-02)."""

    if not faces:
        return []
    reach = max(f[2] - f[0] for f in faces)
    stacks = [[f for f in faces if abs(_compute_centre_x(f) - _compute_centre_x(one)) <= reach] for one in faces]

    return max(stacks, key=lambda stack: (len(stack), sum(f[0] + f[2] for f in stack) * (1 if is_right else -1)))


def _find_strongest_edge(edge_rows: numpy.ndarray,
                         start: int,
                         end: int,
                         is_middle_at_start: bool = True) -> int | None:
    """The column between `start` and `end` with the strongest edge, when that edge runs along PANEL_EDGE_ROWS of the
    screen's height; None otherwise. Of edges within EDGE_TIE of the strongest, the one nearest the middle of the
    screen: a share's border and a panel's divider line beside it both ran along 78% of the rows, and the cut flipped
    between them from one check to the next, so the crop box never held (2026-10-10)."""

    if start >= end:
        return None
    strengths = edge_rows[start:end]
    strongest = float(strengths.max())
    if strongest < PANEL_EDGE_ROWS:
        return None
    near = numpy.flatnonzero(strengths >= strongest - EDGE_TIE)

    return start + int(near[0] if is_middle_at_start else near[-1])


def _find_grid_edge(edge_rows: numpy.ndarray,
                    inner_sides: list[int],
                    middle: int,
                    search_px: int,
                    side: int) -> int:
    """The panel edge of a grid of cameras on one side, or `side` when there is none: with at least GALLERY_MIN_FACES
    faces on that side, the strongest edge within `search_px` of each face's inner side, toward `middle`, the outermost
    face first. In a call shown in a browser the tiles stood two wide, the face detector found 2 to 5 of 8 cameras and
    no two of them stacked, and the edge search from the outer column ended before the share's border (2026-10-10)."""

    if len(inner_sides) < GALLERY_MIN_FACES:
        return side
    for inner in inner_sides:
        if side > middle:
            edge = _find_strongest_edge(edge_rows, max(middle, inner - search_px), inner)
        else:
            edge = _find_strongest_edge(edge_rows, inner, min(middle, inner + search_px), is_middle_at_start=False)
        if edge is not None:
            return edge

    return side


def _find_panel_edges(gray: numpy.ndarray,
                      box: Box,
                      faces: list[Box],
                      search_widths: float = PANEL_EDGE_SEARCH,
                      is_grid_searched: bool = False) -> tuple[int, int]:
    """The screen's left and right edge once a column of faces packed against it is cut off. A side is cut only where a
    column of at least two faces sits in its outer part AND a straight edge runs beside them along most of the screen's
    height. Called on the transposed frame, the same test finds a row of faces on top or at the bottom."""

    x0, y0, x1, y1 = box
    width = x1 - x0

    # STEP 1: the faces inside the screen; with none, there is no panel to cut
    inside = _list_faces_inside(box, faces)
    if not inside:
        return x0, x1

    # STEP 2: per column, the share of the screen's rows in which the grey level jumps across that column
    region = gray[y0:y1].astype(numpy.int16)
    jumps = numpy.abs(region[:, 2 * EDGE_STEP_PX:] - region[:, :-2 * EDGE_STEP_PX]) > EDGE_JUMP
    edge_rows = numpy.zeros(gray.shape[1])
    edge_rows[EDGE_STEP_PX:gray.shape[1] - EDGE_STEP_PX] = jumps.mean(axis=0)

    # STEP 3: the faces that sit in the screen's outer part on the left or the right
    search_px = round(search_widths * float(numpy.median([f[2] - f[0] for f in inside])))
    side_px = PANEL_SIDE * width
    right_faces = _keep_outer_stack([f for f in inside if _compute_centre_x(f) > x1 - side_px], is_right=True)
    left_faces = _keep_outer_stack([f for f in inside if _compute_centre_x(f) < x0 + side_px], is_right=False)

    # STEP 4: on each side with a column of faces, the strongest edge between the faces and the middle of the screen
    left_edge, right_edge = x0, x1
    if _is_face_column(right_faces):
        inner = min(f[0] for f in right_faces)
        edge = _find_strongest_edge(edge_rows, max(x0 + width // 2, inner - search_px), inner)
        right_edge = right_edge if edge is None else edge
    if _is_face_column(left_faces):
        inner = max(f[2] for f in left_faces)
        edge = _find_strongest_edge(edge_rows, inner, min(x0 + width // 2, inner + search_px),
                                    is_middle_at_start=False)
        left_edge = left_edge if edge is None else edge

    grid_px = round(GRID_EDGE_SEARCH * float(numpy.median([f[2] - f[0] for f in inside])))
    # STEP 5: a grid of cameras, searched from each face of the side, the outermost first; the cut nearer the middle
    # wins. A column found in the outer column of a grid reached only the panel's divider line, the share's border
    # beyond it, and the crop flipped between the two from one check to the next (2026-10-10)
    if is_grid_searched:
        right_side = sorted((f for f in inside if _compute_centre_x(f) > x1 - side_px), key=lambda f: -f[0])
        right_edge = min(right_edge, _find_grid_edge(edge_rows, [f[0] for f in right_side], x0 + width // 2, grid_px, x1))
        left_side = sorted((f for f in inside if _compute_centre_x(f) < x0 + side_px), key=lambda f: f[2])
        left_edge = max(left_edge, _find_grid_edge(edge_rows, [f[2] for f in left_side], x0 + width // 2, grid_px, x0))

    return left_edge, right_edge


def _is_gallery(box: Box,
                faces: list[Box]) -> bool:
    """Whether what is left of the screen is people rather than content: several faces, large enough together that
    they are camera tiles and not a photo on a slide."""

    inside = _list_faces_inside(box, faces)
    face_area = sum(_compute_area(f) for f in inside)
    is_gallery = len(inside) >= GALLERY_MIN_FACES and face_area >= GALLERY_FACE_AREA * max(1, _compute_area(box))

    return is_gallery


def _is_close_box(first: Box,
                  second: Box,
                  tolerance_px: int) -> bool:
    is_close = all(abs(a - b) <= tolerance_px for a, b in zip(first, second))

    return is_close


def _count_leading_true(flags: numpy.ndarray) -> int:
    """How many True values the array starts with."""

    falses = numpy.flatnonzero(~flags)

    return int(falses[0]) if falses.size else len(flags)


def _compute_smoothed_profile(profile: numpy.ndarray) -> numpy.ndarray:
    half = BAR_SMOOTHING_PX // 2
    padded = numpy.pad(profile, half, mode="edge")

    return numpy.convolve(padded, numpy.ones(BAR_SMOOTHING_PX) / BAR_SMOOTHING_PX, mode="valid")


def _find_changed_pixels(own: numpy.ndarray,
                         other: numpy.ndarray) -> numpy.ndarray:
    """Pixels of `own` that match no pixel within BAR_SHIFT_PX of the same place in `other`."""

    kernel = numpy.ones((2 * BAR_SHIFT_PX + 1, 2 * BAR_SHIFT_PX + 1), numpy.uint8)
    brightest = cv2.dilate(other, kernel).astype(numpy.int16)
    darkest = cv2.erode(other, kernel).astype(numpy.int16)
    pixels = own.astype(numpy.int16)

    return (pixels > brightest + BAR_PIXEL_CHANGE) | (pixels < darkest - BAR_PIXEL_CHANGE)


def _has_edge_near(screen: numpy.ndarray,
                   cut: int) -> bool:
    """Whether, for a row within BAR_SMOOTHING_PX of `cut`, the rows EDGE_STEP_PX above and below it differ by more
    than EDGE_JUMP across at least BAR_EDGE_SHARE of the width. A cut of 0 rows has nothing to test."""

    height = screen.shape[0]
    if cut <= 0 or cut >= height:
        return True
    rows = screen.astype(numpy.int16)
    for row in range(max(EDGE_STEP_PX, cut - BAR_SMOOTHING_PX), min(height - EDGE_STEP_PX, cut + BAR_SMOOTHING_PX)):
        if (numpy.abs(rows[row - EDGE_STEP_PX] - rows[row + EDGE_STEP_PX]) > EDGE_JUMP).mean() >= BAR_EDGE_SHARE:
            return True

    return False


def _find_edge_near(screen: numpy.ndarray,
                    cut: int,
                    is_bottom: bool = False) -> int | None:
    """The row of the straight edge nearest `cut`, within BAR_SMOOTHING_PX of it (an edge as `_has_edge_near` finds
    one): the end of the run of rows that see it on the content's side, or None when there is none. The cut is moved onto it: cut at the
    first row that changes, a terminal's first line, a pixel under its tmux bar, lost the top of its letters, and the
    text reader read none of them on half of those frames (a red error line, a lone bracket)."""

    height = screen.shape[0]
    rows = screen.astype(numpy.int16)
    edge_rows = [row for row in range(max(EDGE_STEP_PX, cut - BAR_SMOOTHING_PX), min(height - EDGE_STEP_PX, cut + BAR_SMOOTHING_PX))
                 if (numpy.abs(rows[row - EDGE_STEP_PX] - rows[row + EDGE_STEP_PX]) > EDGE_JUMP).mean() >= BAR_EDGE_SHARE]
    if not edge_rows:
        return None
    runs, start = [], edge_rows[0]
    for previous, row in zip(edge_rows, edge_rows[1:] + [None]):
        if row != previous + 1:
            runs.append((start, previous))
            start = row
    # the run's end on the content's side, so a blurred border leaves a sliver of the bar rather than a row of content
    ends = [last + 1 if is_bottom else first for first, last in runs]

    return min(ends, key=lambda end: abs(end - cut))


def count_content_words(words: list[dict],
                        height: int) -> int:
    """How many of the words read on a screen `height` pixels tall stand outside its top and bottom bands, where a
    window's title, buttons and status bar sit."""

    top, bottom = CHROME_TOP_SHARE * height, (1 - CHROME_BOTTOM_SHARE) * height
    count = sum(1 for word in words if top <= _compute_centre_y(word["box"]) <= bottom)

    return count


def has_face_inside(box: Box,
                    faces: list[Box]) -> bool:
    """Whether a face's centre lies in `box`: only then are the screen's words read for call tiles."""

    return bool(_list_faces_inside(box, faces))


def _is_overlapping(first: list[int],
                    second: list[int]) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def is_call_window(words: list[dict],
                   height: int) -> bool:
    """Whether the words read on a screen `height` pixels tall, holding a face, are a call window's tiles: CALL_TILE_NAMES
    persons named "<Last>, <First>" apart from each other, one of them marked as a person, or one with fewer than
    LONE_CAMERA_MAX_WORDS words of content (`count_content_words`), since the text reader misses a tile's label now and
    then."""

    marked, unmarked = person_name_utils.list_listed_names(words)
    separate: list[list[int]] = []
    for name in marked + unmarked:
        if not any(_is_overlapping(name["box"], box) for box in separate):
            separate.append(name["box"])
    if len(separate) >= CALL_TILE_NAMES and marked:
        return True

    return bool(separate) and count_content_words(words, height) < LONE_CAMERA_MAX_WORDS


def _find_edge_jumps(region: numpy.ndarray) -> numpy.ndarray:
    """Per row of `region` and per column, whether the grey level jumps by EDGE_JUMP across that column; the
    EDGE_STEP_PX columns at each side never do."""

    pixels = region.astype(numpy.int16)
    jumps = numpy.zeros(region.shape, dtype=bool)
    jumps[:, EDGE_STEP_PX:region.shape[1] - EDGE_STEP_PX] = \
        numpy.abs(pixels[:, 2 * EDGE_STEP_PX:] - pixels[:, :-2 * EDGE_STEP_PX]) > EDGE_JUMP

    return jumps


def _find_top_right_tile(region: numpy.ndarray,
                         face: Box) -> tuple[int, int] | None:
    """The left and bottom edge of a camera tile in the top-right corner of `region`, the screen turned so the face's
    corner is there, or None: a column edge left of the face along TILE_EDGE_SHARE of the rows down to the face's
    chin, then a row edge below the face along that share of the columns from that column to the right side, with no
    edge of that length between the face and the top."""

    height, width = region.shape
    face_width, face_height = face[2] - face[0], face[3] - face[1]

    # STEP 1: the tile's inner side, the strongest column edge between the face and TILE_EDGE_SEARCH widths inward
    columns = _find_edge_jumps(region[:face[3]]).mean(axis=0)
    start = max(width // 2, face[0] - round(TILE_EDGE_SEARCH * face_width))
    if start >= face[0]:
        return None
    left = start + int(numpy.argmax(columns[start:face[0]]))
    if columns[left] < TILE_EDGE_SHARE:
        return None

    # STEP 2: the tile's bottom, the row edge below the face across the tile's columns nearest the row where the left
    # edge stops: the two edges meet at the tile's corner. The strongest row edge was a line of the page under the tile
    # (2026-10-10: 357 rows down where the tile ended at 180, and the cover hid two rows of content), and the first one
    # can be inside the tile, where a name label sits on a light shirt
    rows = _find_edge_jumps(region[:, left:].T).mean(axis=0)
    end = min(height // 2, face[3] + round(TILE_EDGE_SEARCH * face_height))
    passing = face[3] + numpy.flatnonzero(rows[face[3]:end] >= TILE_EDGE_SHARE)
    if passing.size == 0:
        return None
    is_left_edge = _find_edge_jumps(region[:, left - 1 - EDGE_STEP_PX:left + 2 + EDGE_STEP_PX]).any(axis=1)
    gap = max(1, round(TILE_GAP_FACES * face_height))
    stops = numpy.flatnonzero(numpy.convolve(~is_left_edge, numpy.ones(gap), mode="valid") >= gap)
    left_end = int(stops[stops >= face[1]][0]) if (stops >= face[1]).any() else height
    nearest = passing[numpy.argmin(numpy.abs(passing - left_end))]
    if abs(int(nearest) - left_end) > face_height:
        return None
    run = numpy.flatnonzero(numpy.abs(passing - nearest) <= EDGE_STEP_PX)
    bottom = int(passing[run][numpy.argmax(rows[passing[run]])])

    # STEP 3: the tile runs out to the screen's edge: no straight edge closes it above the face. A photo on a slide has
    # a margin above it, its top edge along 78% of the columns, where real tiles had at most 46%. The side beside the
    # face tells nothing: a person's shoulders ran along 76% of a real tile's rows, a photo's edge along 69% to 74%
    is_closed_above = rows[EDGE_STEP_PX:face[1]].max(initial=0.0) >= TILE_EDGE_SHARE

    return None if is_closed_above else (left, bottom)


def find_camera_tile(gray: numpy.ndarray,
                     box: Box,
                     faces: list[Box]) -> Box | None:
    """The tile of a camera floating in a corner of the screen in `box`, x0, y0, x1, y1 in the frame, or None: the
    largest face whose centre lies in the outer PANEL_SIDE of both the width and the height, in a tile closed by a
    straight edge on each inner side and covering at most TILE_MAX_AREA of the screen. A face in a panel or a photo in
    the middle of a slide is no tile."""

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    for face in sorted(_list_faces_inside(box, faces), key=_compute_area, reverse=True):
        is_right = _compute_centre_x(face) > x1 - PANEL_SIDE * width
        is_bottom = _compute_centre_y(face) > y1 - PANEL_SIDE * height
        is_side = is_right or _compute_centre_x(face) < x0 + PANEL_SIDE * width
        is_end = is_bottom or _compute_centre_y(face) < y0 + PANEL_SIDE * height
        if not (is_side and is_end):
            continue

        # The screen turned so the face's corner is its top right, and the face with it
        region = gray[y0:y1, x0:x1][::-1 if is_bottom else 1, ::1 if is_right else -1]
        fx0, fx1 = (face[0] - x0, face[2] - x0) if is_right else (x1 - face[2], x1 - face[0])
        fy0, fy1 = (y1 - face[3], y1 - face[1]) if is_bottom else (face[1] - y0, face[3] - y0)
        edges = _find_top_right_tile(region, (fx0, fy0, fx1, fy1))
        if edges is None:
            continue
        # The edge found is the middle of its jump: the tile reaches EDGE_STEP_PX beyond it
        left, bottom = max(0, edges[0] - EDGE_STEP_PX), min(height, edges[1] + EDGE_STEP_PX)
        if (width - left) * bottom > TILE_MAX_AREA * width * height:
            continue
        tile_x0, tile_x1 = (x0 + left, x1) if is_right else (x0, x1 - left)
        tile_y0, tile_y1 = (y1 - bottom, y1) if is_bottom else (y0, y0 + bottom)

        return tile_x0, tile_y0, tile_x1, tile_y1

    return None


def _find_right_side_tile(region: numpy.ndarray,
                          person: Box) -> Box | None:
    """The camera tile round `person` on the right side of `region`, the screen turned so its side is there, x0, y0, x1,
    y1 in the region, or None: the steps of SIDE_TILE_EDGE_SHARE's comment."""

    height, width = region.shape
    x0, y0, x1, y1 = person
    person_width, person_height = x1 - x0, y1 - y0

    # STEP 1: the inner side, the passing column edge furthest from the person, from a person's width left of it to a
    # quarter into it, the strongest column of that run: the tile holds the person, and over a dark terminal the
    # shirt's edge inside the person's box was stronger than the inset's own border (DEFECTS 219)
    columns = _find_edge_jumps(region[y0:y1]).mean(axis=0)
    start, end = max(0, x0 - person_width), x0 + person_width // 4
    if start >= end:
        return None
    passing = start + numpy.flatnonzero(columns[start:end] >= SIDE_TILE_EDGE_SHARE)
    if passing.size == 0:
        return None
    run_end = passing[0]
    while run_end + 1 in passing:
        run_end += 1
    left = int(passing[0]) + int(numpy.argmax(columns[passing[0]:run_end + 1]))

    # STEP 2: the bottom, the passing row edge across the columns from there to the side nearest the row where the
    # inner side stops: the two meet at the tile's corner. A code diff's coloured bars below the tile made stronger
    # rows further down, and a notebook cell's border 19 px under it a row nearer the person's feet
    rows = _find_edge_jumps(region[:, left:].T).mean(axis=0)
    start = y1 - person_height // 4
    passing = start + numpy.flatnonzero(rows[start:min(height, y1 + person_height)] >= SIDE_TILE_EDGE_SHARE)
    if passing.size == 0:
        return None
    is_left_edge = _find_edge_jumps(region[:, max(0, left - 1 - EDGE_STEP_PX):left + 2 + EDGE_STEP_PX]).any(axis=1)
    gap = max(1, round(SIDE_TILE_GAP_SHARE * person_height))
    stops = numpy.flatnonzero(numpy.convolve(~is_left_edge, numpy.ones(gap), mode="valid") >= gap)
    left_end = int(stops[stops >= (y0 + y1) // 2][0]) if (stops >= (y0 + y1) // 2).any() else height
    bottom = int(passing[numpy.argmin(numpy.abs(passing - left_end))])

    # STEP 3: the outer side, over the lower half of the person's height above the bottom, where a room camera shows
    # the lit lectern: above it a dark chalkboard met the dark screen along under half of the rows
    lower = _find_edge_jumps(region[max(0, bottom - person_height // 2):bottom]).mean(axis=0)
    start = x1 - person_width // 4
    outer = start + numpy.flatnonzero(lower[start:] >= SIDE_TILE_EDGE_SHARE)
    right = min(width, int(outer.max()) + EDGE_STEP_PX) if outer.size else width
    left, bottom = max(0, left - EDGE_STEP_PX), min(height, bottom + EDGE_STEP_PX)
    top = round(bottom - (right - left) / CAMERA_ASPECT)

    is_person_inside = left < _compute_centre_x(person) < right and top < _compute_centre_y(person) < bottom
    is_at_edge = width - right <= SIDE_TILE_EDGE_REACH * width
    is_small = (right - left) * (bottom - top) <= SIDE_TILE_MAX_AREA * width * height

    return (left, top, right, bottom) if top >= 0 and is_person_inside and is_at_edge and is_small else None


def find_side_tile(gray: numpy.ndarray,
                   box: Box,
                   people: list[Box]) -> Box | None:
    """The tile of a camera inset on the left or right side of the screen in `box`, at any height, x0, y0, x1, y1 in the
    frame, or None: the largest person whose centre lies in the outer PANEL_SIDE of the width, in a tile found by
    `_find_right_side_tile`."""

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    for person in sorted(_list_faces_inside(box, people), key=_compute_area, reverse=True):
        is_right = _compute_centre_x(person) > x1 - PANEL_SIDE * width
        if not (is_right or _compute_centre_x(person) < x0 + PANEL_SIDE * width):
            continue
        # The screen turned so the person's side is its right
        region = gray[y0:y1, x0:x1][:, ::1 if is_right else -1]
        px0, px1 = (person[0] - x0, person[2] - x0) if is_right else (x1 - person[2], x1 - person[0])
        tile = _find_right_side_tile(region, (max(0, px0), max(0, person[1] - y0), min(width, px1),
                                              min(height, person[3] - y0)))
        if tile is None:
            continue
        left, top, right, bottom = tile
        if max(y0 + top - person[1], person[3] - y0 - bottom) > SIDE_TILE_PERSON_PAST_SHARE * (bottom - top):
            continue
        tile_x0, tile_x1 = (x0 + left, x0 + right) if is_right else (x1 - right, x1 - left)

        return tile_x0, y0 + top, tile_x1, y0 + bottom

    return None


def is_side_tile_kept(gray: numpy.ndarray,
                      tile: Box) -> bool:
    """Whether a side tile found on an earlier second still stands on this frame, for the seconds the person detector
    misses the person (2 of 8 frames of one lecture): its inner side's column edge along SIDE_TILE_EDGE_SHARE of its
    lower half, and its bottom's row edge along that share of its columns."""

    x0, y0, x1, y1 = tile
    inner = x0 if _compute_centre_x(tile) > gray.shape[1] / 2 else x1
    side = gray[(y0 + y1) // 2:y1, max(0, inner - 2 * EDGE_STEP_PX):inner + 2 * EDGE_STEP_PX + 1]
    bottom = gray[max(0, y1 - 3 * EDGE_STEP_PX):y1 + EDGE_STEP_PX + 1, x0:x1].T
    if side.shape[1] <= 2 * EDGE_STEP_PX or bottom.shape[1] <= 2 * EDGE_STEP_PX:
        return False

    return (_find_edge_jumps(side).any(axis=1).mean() >= SIDE_TILE_EDGE_SHARE
            and _find_edge_jumps(bottom).any(axis=1).mean() >= SIDE_TILE_EDGE_SHARE)


def cover_camera_tile(frame: numpy.ndarray,
                      tile: Box) -> numpy.ndarray:
    """A copy of `frame` with `tile` filled with the median colour of the TILE_RING_PX pixels round it."""

    x0, y0, x1, y1 = tile
    height, width = frame.shape[:2]
    outer = frame[max(0, y0 - TILE_RING_PX):min(height, y1 + TILE_RING_PX),
                  max(0, x0 - TILE_RING_PX):min(width, x1 + TILE_RING_PX)]
    is_ring = numpy.ones(outer.shape[:2], dtype=bool)
    is_ring[y0 - max(0, y0 - TILE_RING_PX):y1 - max(0, y0 - TILE_RING_PX),
            x0 - max(0, x0 - TILE_RING_PX):x1 - max(0, x0 - TILE_RING_PX)] = False
    covered = frame.copy()
    if is_ring.any():
        covered[y0:y1, x0:x1] = numpy.median(outer[is_ring], axis=0).astype(frame.dtype)

    return covered


def is_lone_camera(box: Box,
                   faces: list[Box]) -> bool:
    """Whether the screen shows one person's camera rather than content, by its faces alone: one face, or one with only
    faces under LONE_FACE_OTHERS_SHARE of its size beside it, its centre in the middle of the width, covering
    LONE_FACE_AREA of the screen or more. A speaker view was taken as a frame, since the gallery rule needs two faces
    (2026-10-02), and a speaker sitting back, 2.1% of the screen, with a self-view tile beside, was taken (2026-10-08).
    The caller also asks that almost no text is on it, so a slide holding a person's photo is kept."""

    x0, _, x1, _ = box
    inside = _list_faces_inside(box, faces)
    if not inside:
        return False
    # The largest face is the speaker
    face = max(inside, key=_compute_area)
    face_area = _compute_area(face)
    if any(other is not face and _compute_area(other) >= LONE_FACE_OTHERS_SHARE * face_area
           for other in inside):
        return False
    margin = (1 - LONE_FACE_MIDDLE) / 2 * (x1 - x0)
    is_middle = x0 + margin <= _compute_centre_x(face) <= x1 - margin
    is_large = face_area >= LONE_FACE_AREA * max(1, _compute_area(box))

    return is_middle and is_large


def is_blank(gray: numpy.ndarray) -> bool:
    """Whether a crop of the frame shows nothing: one flat colour, or static noise (`_is_noise`). The screen share ended between two looks for the box and the box
    kept from the last look holds stage black, or a recording opens on a plain coloured card. A frame like that was
    taken and sent to the model, which could only say "black screen" or "a red screen"."""

    if gray.size == 0:
        return False
    is_flat = float((numpy.abs(gray.astype(numpy.int16) - int(numpy.median(gray))) <= BLANK_SPREAD).mean()) >= BLANK_SHARE

    return is_flat or _is_noise(gray)


def is_empty_inside(gray: numpy.ndarray) -> bool:
    """Whether the frame shows nothing between its chrome bands: its inside one flat colour though a window's bars stand
    above and below it, as a cleared terminal shows only its prompt line and its status bar (DEFECTS 558), which
    `is_blank` keeps for those bars. Both bands must hold marks: a terminal whose lines stand only in its bottom band
    shows a command and its output there (DEFECTS 664)."""

    height = gray.shape[0]
    top, bottom = int(CHROME_TOP_SHARE * height), int((1 - CHROME_BOTTOM_SHARE) * height)
    inside = gray[top:bottom]
    if inside.size == 0 or not _has_bar(gray[:top]) or not _has_bar(gray[bottom:]):
        return False

    return float((numpy.abs(inside.astype(numpy.int16) - int(numpy.median(inside))) <= BLANK_SPREAD).mean()) >= EMPTY_INSIDE_SHARE


def _has_bar(band: numpy.ndarray) -> bool:
    """Whether a chrome band holds marks, a window's bar or a prompt line, rather than the inside's flat colour going on."""

    if band.size == 0:
        return False

    return float((numpy.abs(band.astype(numpy.int16) - int(numpy.median(band))) > BLANK_SPREAD).mean()) >= BAR_INK_SHARE


def _is_noise(gray: numpy.ndarray) -> bool:
    """Whether a crop is static noise: at 320 x 180, no 10 x 10 patch is flat and edges are everywhere far stronger than
    any screen's. A broadcast's noise frame was taken and sent to phase 3."""

    small = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
    patches = small.reshape(18, 10, 32, 10).swapaxes(1, 2).reshape(18, 32, 100)
    if bool((patches.std(axis=2) < NOISE_FLAT_SPREAD).any()):
        return False

    return float(numpy.abs(cv2.Laplacian(small, cv2.CV_32F)).mean()) > NOISE_EDGE


def _is_inside(box: Box,
               x: float,
               y: float) -> bool:
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def _compute_tile_overhang(gray: numpy.ndarray,
                           box: Box,
                           face: Box) -> int | None:
    """How many rows the face's tile edge runs past the box's top or bottom, negative when it stops short: the column
    between the box and the face whose vertical edge runs longest. None when the face is not left or right of the box."""

    start, end = (box[2], face[0]) if face[0] >= box[2] else (face[2], box[0])
    start, end = max(0, start), min(gray.shape[1], end)
    if start >= end:
        return None
    is_edge = numpy.abs(cv2.Sobel(gray[:, start:end], cv2.CV_32F, 1, 0, ksize=3)) > SLIDE_PANEL_TILE_EDGE
    runs = [run for run in (_find_longest_run(is_edge[:, column]) for column in range(end - start)) if run is not None]
    if not runs:
        return None
    top, bottom = max(runs, key=lambda run: run[1] - run[0])

    return max(box[1] - top, bottom - box[3])


def _is_tile_beside(gray: numpy.ndarray,
                    box: Box,
                    face: Box) -> bool:
    overhang = _compute_tile_overhang(gray, box, face)

    return overhang is not None and overhang >= SLIDE_PANEL_TILE_PAST * gray.shape[0]


def _fill_enclosed(piece: numpy.ndarray) -> numpy.ndarray:
    """The piece with every hole it encloses filled: the parts no path through the background reaches from the edge."""

    padded = numpy.pad((~piece.astype(bool)).astype(numpy.uint8), 1, constant_values=1)
    mask = numpy.zeros((padded.shape[0] + 2, padded.shape[1] + 2), numpy.uint8)
    cv2.floodFill(padded, mask, (0, 0), 2)

    return (padded[1:-1, 1:-1] != 2)


def find_slide_panel(frame: numpy.ndarray,
                     gray: numpy.ndarray,
                     faces: list[Box],
                     is_enclosed_counted: bool = False) -> Box | None:
    """The projected slide of a conference broadcast as x0, y0, x1, y1, end exclusive, or None: the largest panel of one
    grey level with straight edges, filling its box, covering part of the frame, with a face outside it (the speaker's
    camera on the backdrop beside it). A full-screen slide or a panel with no face beside it gives None, and a face in a
    photo inside the slide does not count. The caller also asks that almost no words stand outside it. With none
    found, a slide on a flat dark backdrop is looked for, then the search runs again counting what each grey encloses
    (`is_enclosed_counted`)."""

    # STEP 1: for each band of grey levels, the largest piece of it, its text gaps closed
    height, width = gray.shape
    kernel = numpy.ones((max(3, int(SLIDE_PANEL_CLOSE * width)),) * 2, numpy.uint8)
    levels = gray.astype(numpy.int16)
    channels = frame.astype(numpy.int16)
    is_neutral = ((cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1] < SLIDE_PANEL_MAX_SATURATION)
                  | (channels.max(axis=2) - channels.min(axis=2) <= SLIDE_PANEL_MAX_CHROMA))
    best = None
    bands = [(level - SLIDE_PANEL_BAND + SLIDE_PANEL_STEP // 2, level + SLIDE_PANEL_BAND + SLIDE_PANEL_STEP // 2, is_neutral)
             for level in range(0, 256, SLIDE_PANEL_STEP)] + [(SLIDE_PANEL_LIGHT, 255, True)]
    for low, high, is_counted in bands:
        band = (is_counted & (levels >= low) & (levels <= high)).astype(numpy.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(band, cv2.MORPH_CLOSE, kernel))
        if count < 2:
            continue
        largest = 1 + int(numpy.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, w, h, area = stats[largest]
        whole = (int(x), int(y), int(x + w), int(y + h))

        # STEP 2: its box trimmed to the columns, then the rows, it fills: a slide's edges are straight. On the second
        # search what it encloses counts too, a picture inside the slide being the slide's (release test 2's dark title
        # slide, a quarter of it a turquoise picture, filled 41% of the columns over the picture, DEFECTS 414); never
        # on the first, where white clouds enclosing a camera tile took the camera into the slide
        piece = labels[y:y + h, x:x + w] == largest
        piece = _fill_enclosed(piece) if is_enclosed_counted else piece
        columns = _find_longest_run(piece.mean(axis=0) >= SLIDE_PANEL_EDGE_FILL)
        if columns is None:
            continue
        rows = _find_longest_run(piece[:, columns[0]:columns[1]].mean(axis=1) >= SLIDE_PANEL_EDGE_FILL)
        if rows is None:
            continue
        piece = piece[rows[0]:rows[1], columns[0]:columns[1]]
        x, y, w, h = x + columns[0], y + rows[0], columns[1] - columns[0], rows[1] - rows[0]
        is_solid = piece.sum() >= SLIDE_PANEL_SOLID_FILL * w * h
        is_rectangle = piece.sum() >= (SLIDE_PANEL_SOLID_KEPT if is_solid else SLIDE_PANEL_KEPT) * area
        # kept only as a solid rectangle, the face beside it must stand outside the whole piece too, or in a tile running
        # past the box's rows: a full-screen slide's grey without its photo passed, the photo's face counting as beside
        # it, and the photo was cut away; then a camera on white clouds joined to the slide's piece found no panel
        is_solid_only = piece.sum() < SLIDE_PANEL_KEPT * area

        # STEP 3: a panel: it fills its box, which covers part of the frame, and a face is outside it
        box = (int(x), int(y), int(x + w), int(y + h))
        is_filled = piece.sum() >= SLIDE_PANEL_FILL * w * h
        is_part = SLIDE_PANEL_MIN_AREA * width * height <= w * h <= SLIDE_PANEL_MAX_AREA * width * height
        is_slide_shaped = SLIDE_PANEL_MIN_ASPECT * h <= w <= SLIDE_PANEL_MAX_ASPECT * h
        is_face_beside = any(not _is_inside(box, _compute_centre_x(face), _compute_centre_y(face))
                             and not (is_solid_only and _is_inside(whole, _compute_centre_x(face), _compute_centre_y(face))
                                      and not _is_tile_beside(gray, box, face))
                             for face in faces)
        if is_rectangle and is_filled and is_part and is_slide_shaped and is_face_beside and (best is None or _compute_area(box) > _compute_area(best)):
            best = box

    # STEP 4: each side moved out to the slide's own outline: a part of the slide in another grey (a code block on its
    # right) was left out of the grey level's piece, and the slide was cut through its code (round 1 of the phase 2
    # audit: 6 frames of one talk)
    if best is None:
        if is_enclosed_counted:
            return None
        found = _find_panel_on_backdrop(gray, faces)

        return found if found is not None else find_slide_panel(frame, gray, faces, is_enclosed_counted=True)

    return _trim_backdrop_beyond_field(frame, _add_slide_bands(gray, _move_sides_to_outline(gray, best)))


def _trim_backdrop_beyond_field(frame: numpy.ndarray,
                                box: Box) -> Box:
    """`box` with each side moved in past the lines that hold almost none of the slide's field colour: a slide and a
    backdrop of near grey met with no edge, and the panel took the backdrop under the slide. A slide with no flat
    field is left as it is."""

    height, width = frame.shape[:2]
    x0, y0, x1, y1 = box
    inside = frame[y0:y1, x0:x1].astype(numpy.int16)
    field = numpy.median(inside.reshape(-1, 3), axis=0)
    is_field = numpy.abs(inside - field).max(axis=2) <= SLIDE_FIELD_SPREAD
    if is_field.mean() < SLIDE_FIELD_MIN:
        return box
    rows, columns = is_field.mean(axis=1), is_field.mean(axis=0)
    # every side: the panel stopped at a backdrop line 65 rows under the slide too, not only at the border (38c3-openpv
    # at 776 s, DEFECTS 617)
    y1 = y0 + _count_until_field(rows[::-1], len(rows))
    y0 = y1 - _count_until_field(rows[: y1 - y0], y1 - y0)
    x1 = x0 + _count_until_field(columns[::-1], len(columns))
    x0 = x1 - _count_until_field(columns[: x1 - x0], x1 - x0)

    return x0, y0, x1, y1


def _count_until_field(shares: numpy.ndarray,
                       size: int) -> int:
    """`size` less the lines at the start of `shares` before the slide: before its first line holding SLIDE_FIELD_MIN
    of the field colour and the edge lines next to it holding SLIDE_FIELD_EDGE; all of `size` when no line holds it."""

    held = numpy.flatnonzero(shares >= SLIDE_FIELD_MIN)
    if not held.size:
        return size
    start = int(held[0])
    # the slide's own last lines (its edge, a text line on its field) stand just past the first half-field line
    while start > 0 and shares[start - 1] >= SLIDE_FIELD_EDGE:
        start -= 1
    # a strip of EDGE_LINE_MOST_PX or less is cut later on every frame (`find_edge_strips`): cut here only on a slide
    # with a flat field, froscon talk 3147's slide lost its backdrop strip at 378 s and kept it at 17 s, filled by a
    # picture, and the two showings of one slide no longer matched as one layout
    if start <= EDGE_LINE_MOST_PX:
        return size

    return size - start


def _find_panel_on_backdrop(gray: numpy.ndarray,
                            faces: list[Box]) -> Box | None:
    """The slide of a broadcast whose slide holds many tones, as x0, y0, x1, y1, end exclusive, or None: the largest
    part of the frame that is no flat dark backdrop touching its border, filling its box, slide-shaped, covering part
    of the frame, with a face outside it."""

    height, width = gray.shape
    level = float(numpy.median(numpy.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])))
    if level > SLIDE_BACKDROP_MAX_LEVEL:
        return None
    flat = (numpy.abs(gray.astype(numpy.int16) - level) <= SLIDE_BACKDROP_SPREAD).astype(numpy.uint8)
    _, labels, _, _ = cv2.connectedComponentsWithStats(flat)
    touching = numpy.unique(numpy.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])[
        numpy.concatenate([flat[0], flat[-1], flat[:, 0], flat[:, -1]]) > 0])
    content = (~numpy.isin(labels, touching) | (flat == 0)).astype(numpy.uint8)
    count, pieces, stats, _ = cv2.connectedComponentsWithStats(content)
    if count < 2:
        return None
    largest = 1 + int(numpy.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = (int(value) for value in stats[largest])
    box = (x, y, x + w, y + h)
    is_panel = (area >= SLIDE_BACKDROP_FILL * w * h
                and SLIDE_PANEL_MIN_AREA * width * height <= w * h <= SLIDE_PANEL_MAX_AREA * width * height
                and SLIDE_PANEL_MIN_ASPECT * h <= w <= SLIDE_PANEL_MAX_ASPECT * h)
    is_face_beside = any(not _is_inside(box, _compute_centre_x(face), _compute_centre_y(face)) for face in faces)

    return box if is_panel and is_face_beside else None


def _find_outline_beyond(edges: numpy.ndarray,
                         side: int,
                         step: int,
                         reach: int) -> int | None:
    """The nearest index past `side`, going `step` (1 or -1) up to `reach` away, where `edges` holds an outline; None
    when `side` already lies on one (within SLIDE_PANEL_OUTLINE_SNAP) or none is in reach."""

    if edges[max(0, side - SLIDE_PANEL_OUTLINE_SNAP):side + SLIDE_PANEL_OUTLINE_SNAP + 1].any():
        return None
    beyond = range(side + step * (SLIDE_PANEL_OUTLINE_SNAP + 1), side + step * reach, step)

    return next((index for index in beyond if 0 <= index < len(edges) and edges[index]), None)


def _move_sides_to_outline(gray: numpy.ndarray,
                       box: Box) -> Box:
    """`box` with each side that lies on no outline moved out to the nearest straight edge beyond it, within
    SLIDE_PANEL_OUTLINE_REACH of the frame, that runs along SLIDE_PANEL_OUTLINE_SHARE of that side. A side already on
    an outline stays: past the slide's own edge, a backdrop's straight lines would take it further."""

    height, width = gray.shape
    levels = gray.astype(numpy.int16)
    x0, y0, x1, y1 = box
    is_down_edge = numpy.zeros_like(gray, dtype=bool)
    is_down_edge[:, 1:-1] = numpy.abs(levels[:, 2:] - levels[:, :-2]) > FRAME_EDGE_JUMP
    is_across_edge = numpy.zeros_like(gray, dtype=bool)
    is_across_edge[1:-1, :] = numpy.abs(levels[2:, :] - levels[:-2, :]) > FRAME_EDGE_JUMP
    columns = is_down_edge[y0:y1].mean(axis=0) >= SLIDE_PANEL_OUTLINE_SHARE
    rows = is_across_edge[:, x0:x1].mean(axis=1) >= SLIDE_PANEL_OUTLINE_SHARE
    reach_x, reach_y = round(SLIDE_PANEL_OUTLINE_REACH * width), round(SLIDE_PANEL_OUTLINE_REACH * height)
    left = _find_outline_beyond(columns, x0, -1, reach_x)
    top = _find_outline_beyond(rows, y0, -1, reach_y)
    right = _find_outline_beyond(columns, x1 - 1, 1, reach_x)
    bottom = _find_outline_beyond(rows, y1 - 1, 1, reach_y)

    return (x0 if left is None else left, y0 if top is None else top, x1 if right is None else right + 1,
            y1 if bottom is None else bottom + 1)


def _add_slide_bands(gray: numpy.ndarray,
                     box: Box) -> Box:
    """`box` with the slide's header band above it and its footer band below it, when either is there: the panel is
    one grey level, so a title band in another colour fell outside it, and a broadcast's slides were cut without their
    titles. A band spans exactly the panel's columns, so a backdrop or a stage beside the slide never reads as one."""

    x0, y0, x1, y1 = box
    top = _find_band_end(gray, x0, x1, y0, -1)
    bottom = _find_band_end(gray, x0, x1, y1 - 1, 1)

    return x0, y0 if top is None else top, x1, y1 if bottom is None else bottom + 1


def _find_band_end(gray: numpy.ndarray,
                   x0: int,
                   x1: int,
                   side: int,
                   step: int) -> int | None:
    """The far row of a band of one grey that starts just past `side` (going `step`, 1 or -1) and spans the columns
    `x0` to `x1`, ending at both of them; None when there is no band of SLIDE_BAND_MIN to SLIDE_BAND_MAX of the height."""

    height, width = gray.shape
    if x0 < 3 or x1 > width - 3 or x1 - x0 < 10:
        return None
    levels = gray.astype(numpy.int16)
    panel = int(numpy.median(levels[side, x0:x1]))
    row, band, end, blended = side + step, None, None, 0
    framed: list[bool] = []
    while 0 <= row < height and abs(row - side) <= round(SLIDE_BAND_MAX * height):
        segment = levels[row, x0:x1]
        median = int(numpy.median(segment))
        # Before the band's grey is set: the panel's own rows a few pixels past its side, and up to two rows of a line
        # or a blend between the panel and the band (a row of 169 between a white body and a band of 41) unlike the
        # row two further on, are passed over
        if band is None and abs(median - panel) <= SLIDE_BAND_SPREAD and abs(row - side) <= SLIDE_PANEL_OUTLINE_SNAP:
            row += step
            continue
        ahead = row + 2 * step
        if (band is None and blended < 2 and 0 <= ahead < height
                and abs(median - int(numpy.median(levels[ahead, x0:x1]))) > SLIDE_BAND_SPREAD):
            blended += 1
            row += step
            continue
        band = median if band is None else band
        is_one_grey = (numpy.abs(segment - band) <= SLIDE_BAND_SPREAD).mean() >= SLIDE_BAND_SHARE
        # what stands just outside both sides, three pixels each, differs from the band's grey: a backdrop's coloured line
        # crossing one pixel beside it does not end the band
        is_framed = (numpy.abs(levels[row, x0 - 3:x0] - band).mean() >= SLIDE_BAND_SIDE_JUMP
                     and numpy.abs(levels[row, x1:x1 + 3] - band).mean() >= SLIDE_BAND_SIDE_JUMP)
        if abs(band - panel) <= SLIDE_BAND_SPREAD or not is_one_grey:
            break
        framed.append(is_framed)
        end = row
        row += step
    # framed on most rows, not every row: a backdrop's wavy lines take the band's grey on a few rows beside it
    if end is None or abs(end - side) < round(SLIDE_BAND_MIN * height) or numpy.mean(framed) < SLIDE_BAND_FRAMED:
        return None

    return end


def shrink_outside_panel(gray: numpy.ndarray,
                         panel: Box) -> numpy.ndarray:
    """Shrink the frame to LAYOUT_SAMPLE_WIDTH columns, as floats, with the panel's area blanked (NaN): what stands
    around a broadcast's slide."""

    height, width = gray.shape
    scale = LAYOUT_SAMPLE_WIDTH / width
    sample = cv2.resize(gray, (LAYOUT_SAMPLE_WIDTH, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    sample = sample.astype(numpy.float32)
    x0, y0, x1, y1 = (round(value * scale) for value in panel)
    sample[y0:y1, x0:x1] = numpy.nan

    return sample


# A known slide's side stands when the grey just inside it and just outside differ by this many levels on this share of
# its length: a dark slide on a light backdrop differs by 150 or more, the backdrop where a larger layout's side would
# stand by none
OUTLINE_STEP_LEVELS = 20
OUTLINE_STANDING_SHARE = 0.6
# Each side's band is this many pixels deep; a side within this many pixels of the frame's border cannot be checked
OUTLINE_BAND_PX = 3


def is_outline_standing(gray: numpy.ndarray,
                        box: Box) -> bool:
    """Whether `box`'s outline stands in this moment: every side not on the frame's border, two at least, steps in grey
    from just inside to just outside along most of its length (DEFECTS 307)."""

    height, width = gray.shape
    x0, y0, x1, y1 = box
    levels = gray.astype(numpy.int16)
    band = OUTLINE_BAND_PX
    sides = []
    if x0 >= band:
        sides.append((levels[y0:y1, x0:x0 + band].mean(axis=1), levels[y0:y1, x0 - band:x0].mean(axis=1)))
    if x1 <= width - band:
        sides.append((levels[y0:y1, x1 - band:x1].mean(axis=1), levels[y0:y1, x1:x1 + band].mean(axis=1)))
    if y0 >= band:
        sides.append((levels[y0:y0 + band, x0:x1].mean(axis=0), levels[y0 - band:y0, x0:x1].mean(axis=0)))
    if y1 <= height - band:
        sides.append((levels[y1 - band:y1, x0:x1].mean(axis=0), levels[y1:y1 + band, x0:x1].mean(axis=0)))

    return len(sides) >= 2 and all(
        float((numpy.abs(inside - outside) >= OUTLINE_STEP_LEVELS).mean()) >= OUTLINE_STANDING_SHARE
        for inside, outside in sides)


def is_same_layout(gray: numpy.ndarray,
                   panel: Box,
                   outside: numpy.ndarray) -> bool:
    """Whether the frame around `panel` still looks as it did when the panel was found: the median grey difference of
    the area outside it under LAYOUT_MATCH_LEVELS. The speaker moving in the camera changes a small part of it; a cut to
    the speaker alone or to the slide in full changes all of it."""

    now = shrink_outside_panel(gray, panel)
    if now.shape != outside.shape:
        return False
    difference = numpy.abs(now - outside)

    return bool(numpy.isfinite(difference).any()) and float(numpy.nanmedian(difference)) < LAYOUT_MATCH_LEVELS


def compute_layout_difference(gray: numpy.ndarray,
                              panel: Box,
                              outside: numpy.ndarray) -> float:
    """The mean grey difference of the area outside `panel` from how it looked when the panel was found: a moment of a
    talk's large slide layout matched its small layout too by the median, the large slide covering under half of the
    small layout's outside area (DEFECTS 195)."""

    difference = numpy.abs(shrink_outside_panel(gray, panel) - outside)

    return float(numpy.nanmean(difference)) if numpy.isfinite(difference).any() else float("inf")


def count_words_outside(words: list[dict],
                        box: Box) -> int:
    """How many of the text reader's words stand outside `box`, by their centres."""

    return sum(1 for word in words
               if not _is_inside(box, (word["box"][0] + word["box"][2]) / 2, (word["box"][1] + word["box"][3]) / 2))


def clip_panel_to_box(panel: Box,
                     box: Box,
                     frame_width: int) -> Box | None:
    """`panel` cut to `box` when it lies inside it, a few pixels over its edge allowed (BOX_WOBBLE), else None. The
    screen box of a broadcast started one pixel in from the edge the slide touched, and the slide was refused on every
    second the box did (2026-10-12: 8 of 55 frames of one talk kept the speaker's camera)."""

    tolerance_px = max(BOX_WOBBLE_MIN_PX, round(BOX_WOBBLE * frame_width))
    is_inside = panel[0] >= box[0] - tolerance_px and panel[1] >= box[1] - tolerance_px \
        and panel[2] <= box[2] + tolerance_px and panel[3] <= box[3] + tolerance_px
    if not is_inside:
        return None
    # A broadcast's slide has the speaker's camera beside it inside the box; a panel reaching both sides of the box is
    # the box's own content, such as a call's share once its faces column was cut off, which lost its top 30 to 54 px
    # to the panel (DEFECTS 97)
    if panel[0] <= box[0] + tolerance_px and panel[2] >= box[2] - tolerance_px:
        return None

    return max(panel[0], box[0]), max(panel[1], box[1]), min(panel[2], box[2]), min(panel[3], box[3])


def find_screen_box(gray: numpy.ndarray,
                    faces: list[Box],
                    is_gallery_refused: bool = True) -> Box | None:
    """The shared screen as x0, y0, x1, y1, end exclusive, or None when nothing is shared. The stage fixes the outer
    box; a column of faces packed against its left or right is cut off, then a row of faces on top or at the bottom.
    None when nothing is shared: less than MIN_SCREEN_AREA of the frame is left, or what is left is a gallery of
    camera tiles."""

    height, width = gray.shape

    # STEP 1: the box the black stage leaves
    is_stage = _find_stage(gray)
    columns = _find_longest_run(_find_screen_columns(is_stage))
    if columns is None:
        return None
    rows = _find_screen_rows(is_stage, *columns)
    if rows is None:
        return None

    # STEP 2: a column of faces on the left or right, then the letterbox rows over what is left
    x0, x1 = _find_panel_edges(gray, (columns[0], rows[0], columns[1], rows[1]), faces, PANEL_COLUMN_EDGE_SEARCH,
                               is_grid_searched=True)
    rows = _find_screen_rows(is_stage, x0, x1)
    if rows is None:
        return None

    # STEP 3: a row of faces on top or at the bottom, by the same test on the transposed frame
    y0, y1 = _find_panel_edges(gray.T, (rows[0], x0, rows[1], x1), [(f[1], f[0], f[3], f[2]) for f in faces])
    is_too_small = (x1 - x0) * (y1 - y0) < MIN_SCREEN_AREA * width * height
    is_refused_gallery = is_gallery_refused and _is_gallery((x0, y0, x1, y1), faces)
    if is_too_small or is_refused_gallery:
        return None

    return x0, y0, x1, y1


def _is_widened_on_one_side(found: Box,
                            current: Box,
                            tolerance_px: int) -> bool:
    """Whether `found` is `current` widened on its left or its right side only: the camera panel cut off before, not
    seen on this check."""

    is_same_rows = abs(found[1] - current[1]) <= tolerance_px and abs(found[3] - current[3]) <= tolerance_px
    is_wider_left = found[0] < current[0] - tolerance_px and abs(found[2] - current[2]) <= tolerance_px
    is_wider_right = found[2] > current[2] + tolerance_px and abs(found[0] - current[0]) <= tolerance_px

    return is_same_rows and (is_wider_left or is_wider_right)


def choose_crop_box(current: Box | None,
                    pending: PendingBoxState | None,
                    found: Box,
                    frame_width: int) -> tuple[Box, PendingBoxState | None]:
    """The box to crop with, and the candidate waiting to replace it. The current box survives a found box that moved by
    a few pixels: that shift, scaled, reads as a change of the whole screen, and one slide got captured thirty times. A
    different box takes over once it is found on BOX_CONFIRM_CHECKS checks in a row."""

    tolerance_px = max(BOX_WOBBLE_MIN_PX, round(BOX_WOBBLE * frame_width))
    is_confirmed = pending is not None and _is_close_box(found, pending.box, tolerance_px)
    needed = BOX_CONFIRM_CHECKS
    if current is not None and _is_widened_on_one_side(found, current, tolerance_px):
        needed = PANEL_RETURN_CHECKS
    settled, waiting = current, None
    if current is None or _is_close_box(found, current, tolerance_px):
        settled = current or found
    elif is_confirmed and pending.checks + 1 >= needed:
        settled = found
    elif is_confirmed:
        waiting = PendingBoxState(pending.box, pending.checks + 1)
    else:
        waiting = PendingBoxState(found, 1)

    return settled, waiting


def is_other_box(current: Box,
                 found: Box,
                 frame_width: int) -> bool:
    """Whether `found` is another screen than the box in use: neither its wobble nor the camera panel unseen on this
    check, which `choose_crop_box` keeps the box through."""

    tolerance_px = max(BOX_WOBBLE_MIN_PX, round(BOX_WOBBLE * frame_width))

    return not _is_close_box(found, current, tolerance_px) and not _is_widened_on_one_side(found, current, tolerance_px)


def is_box_check_due(second: int,
                     every_seconds: int,
                     is_new_box_waiting: bool) -> bool:
    """Whether the screen box is looked for at this second: every `every_seconds`, and every second while a different
    box waits for its confirming check. Looked for only every few seconds, the box of a recording's opening card stayed
    in use until the second check at 10 s, and a slide taken at 8 s kept the stage and the camera panel."""

    return is_new_box_waiting or second % every_seconds == 0


# A screen's own menu bar (macOS) at its top: this many of these words read on one line in its top MENU_BAR_SHARE. A deck's
# header or a page's title never reads three of them; the still band under a lecture's menu bar ran on into a terminal's
# still lines and ended on no edge, so the bar trim kept it on 48 of 176 frames (DEFECTS 118)
MENU_BAR_WORDS = frozenset({"file", "edit", "view", "window", "help", "shell", "session", "scripts", "profiles", "go",
                            "history", "bookmarks", "tools", "format", "insert", "navigate", "code", "run", "terminal",
                            "selection", "develop", "debug", "product"})
MENU_BAR_MIN_WORDS = 3
MENU_BAR_SHARE = 0.06
# The cut is the last long straight edge under the menu words within this share of the height: the window's title bar
# under the menu bar goes with it
MENU_BAR_EDGE_SHARE = 0.08
MENU_BAR_EDGE_ROWS = 0.6


def find_menu_bar_cut(screen: numpy.ndarray,
                      words: list[dict]) -> int:
    """The rows to cut off the top of a grey screen frame for its own menu bar and the title bar under it, from the
    words the text reader found in its top band (in the frame's pixels); 0 when no menu bar is read there."""

    height, width = screen.shape
    lines: dict[object, list[dict]] = {}
    for word in words:
        if word["box"][3] <= MENU_BAR_SHARE * height:
            lines.setdefault(word.get("line"), []).append(word)
    menu = [line for line in lines.values()
            if sum(1 for word in line if word["text"].casefold() in MENU_BAR_WORDS) >= MENU_BAR_MIN_WORDS]
    if not menu:
        return 0
    below = max(word["box"][3] for word in menu[0])
    grey = screen.astype(numpy.int16)
    is_edge = (numpy.abs(grey[2:] - grey[:-2]) > FRAME_EDGE_JUMP).mean(axis=1) >= MENU_BAR_EDGE_ROWS
    edges = [row + 1 for row in numpy.flatnonzero(is_edge) if below < row + 1 <= MENU_BAR_EDGE_SHARE * height]

    return int(max(edges)) + 1 if edges else int(below) + 2


def keep_words_whole(margins: BarMargins,
                     words: list[dict],
                     width: int,
                     height: int) -> BarMargins:
    """Each side's cut kept off the text, `words` boxes in the screen's pixels: a bar's edge never has text through it.
    A side a word runs across is not cut (a picture's border inside a slide stood still beside the slide's margin and
    was cut as a bar, through the slide's title, 2026-10-15); a top or bottom cut that a line of text starts on, or
    within BAR_WORD_GAP_PX of, moves out to BAR_WORD_GAP_PX before that line (a terminal's first line began a pixel
    under its tmux bar, and the cut took the top of its letters on 11 frames of a lecture)."""

    top, bottom, left, right = margins
    # A "word" with no letter or digit is a mark the reader made of an edge: a slide's 4 px grey rim was read as "|"
    # 70 px tall and stopped the cut of the 5 px band along the top on 20 of one talk's 50 frames (DEFECTS 447)
    words = [word for word in words if any(character.isalnum() for character in word["text"])]
    # Each side: its cut line, and the two coordinates of a word box that lie across it
    sides = ((top, top, 1, 3), (bottom, height - bottom, 1, 3), (left, left, 0, 2), (right, width - right, 0, 2))
    top, bottom, left, right = tuple(0 if margin and any(word["box"][low] < cut - BAR_WORD_OVERLAP_PX < cut + BAR_WORD_OVERLAP_PX
                                                         < word["box"][high] for word in words) else margin
                                     for margin, cut, low, high in sides)
    if top:
        starting = [word["box"][1] for word in words if word["box"][1] < top + BAR_WORD_GAP_PX < word["box"][3]]
        top = max(0, min([top, *[start - BAR_WORD_GAP_PX for start in starting]]))
    if bottom:
        line = height - bottom
        ending = [word["box"][3] for word in words if word["box"][1] < line - BAR_WORD_GAP_PX < word["box"][3]]
        bottom = max(0, height - max([line, *[end + BAR_WORD_GAP_PX for end in ending]]))

    return top, bottom, left, right


def _list_long_edges(is_edge: numpy.ndarray,
                     min_length: int,
                     max_gap: int) -> list[tuple[int, int, int]]:
    """For each row of `is_edge` whose longest run of edge pixels, breaks up to `max_gap` long joined, is at least
    `min_length`: (row, start, length)."""

    joined = cv2.morphologyEx(is_edge.astype(numpy.uint8), cv2.MORPH_CLOSE, numpy.ones((1, max_gap + 1), numpy.uint8))
    padded = numpy.zeros((is_edge.shape[0], is_edge.shape[1] + 2), dtype=numpy.int8)
    padded[:, 1:-1] = joined
    steps = numpy.diff(padded, axis=1)
    found = []
    for row in range(is_edge.shape[0]):
        starts, ends = numpy.flatnonzero(steps[row] == 1), numpy.flatnonzero(steps[row] == -1)
        if starts.size == 0:
            continue
        longest = int(numpy.argmax(ends - starts))
        if ends[longest] - starts[longest] >= min_length:
            found.append((row, int(starts[longest]), int(ends[longest] - starts[longest])))

    return found


def _expand_unlined_sides(screen: numpy.ndarray,
                         box: Box) -> Box:
    """`box` with its right and its bottom side moved to the screen's edge where no edge runs along FRAME_SAME_OUTLINE
    of that side, within FRAME_CORNER_PX."""

    height, width = screen.shape
    x0, y0, x1, y1 = box
    # `_has_outline_at` looks down a box's left side, so the right side is a box starting there and the bottom one on
    # the screen turned on its side
    if width - x1 > FRAME_CORNER_PX and not _has_outline_at(screen, (x1, y0, width, y1)):
        x1 = width
    if height - y1 > FRAME_CORNER_PX and not _has_outline_at(screen.T, (y1, x0, height, x1)):
        y1 = height

    return x0, y0, x1, y1


def _has_outline_at(screen: numpy.ndarray,
                    box: Box) -> bool:
    """Whether `screen` shows an edge down along FRAME_SAME_OUTLINE of `box`'s left side, within FRAME_CORNER_PX."""

    x0, y0, _, y1 = box
    columns = screen[y0:y1, max(1, x0 - FRAME_CORNER_PX) - 1:x0 + FRAME_CORNER_PX + 2].astype(numpy.int16)
    is_edge = numpy.abs(columns[:, 2:] - columns[:, :-2]) > FRAME_EDGE_JUMP

    return bool(is_edge.any(axis=1).mean() >= FRAME_SAME_OUTLINE) if is_edge.size else False


def _is_still_outside(screen: numpy.ndarray,
                      neighbours: list[numpy.ndarray],
                      box: Box) -> bool:
    """Whether, against every neighbour of the screen's size showing the same outline whose inside of `box` changed in at
    least FRAME_INSIDE_CHANGE of its pixels, the pixels that changed above `box` lie in at most FRAME_OUTSIDE_SPREAD of
    its columns and those on its left in at most that share of its rows, for more than half of them; and at least one
    neighbour's inside changed."""

    x0, y0, x1, y1 = box
    compared, outside_changed = 0, 0
    for other in [other for other in neighbours if other.shape == screen.shape]:
        if not _has_outline_at(other, box):
            continue
        changed = _find_changed_pixels(screen, other)
        if float(changed[y0:y1, x0:x1].mean()) < FRAME_INSIDE_CHANGE:
            continue
        compared += 1
        is_above_changed = y0 > 0 and changed[:y0, :].any(axis=0).mean() > FRAME_OUTSIDE_SPREAD
        is_left_changed = x0 > 0 and changed[y0:y1, :x0].any(axis=1).mean() > FRAME_OUTSIDE_SPREAD
        outside_changed += int(is_above_changed or is_left_changed)

    # A neighbour where a menu of the presenter's own browser lay open over the call's bar changed the outside once;
    # counted against every frame around it, it refused the cut of 21 frames (DEFECTS.md 67)
    return compared > 0 and outside_changed * 2 < compared


def _count_stage_lines(lines: numpy.ndarray) -> int:
    """How many of `lines` (rows, from the outline inward) are the call's flat stage: after the outline's own
    FRAME_CORNER_PX lines, each holding FRAME_STAGE_FLAT_ROW of its pixels within FRAME_STAGE_LEVELS of the first such
    line's median. 0 when fewer than FRAME_CORNER_PX are."""

    if lines.shape[0] <= FRAME_CORNER_PX or lines.shape[1] == 0:
        return 0
    stage = numpy.median(lines[FRAME_CORNER_PX])
    is_flat = (numpy.abs(lines[FRAME_CORNER_PX:].astype(numpy.int16) - stage) <= FRAME_STAGE_LEVELS).mean(axis=1) \
        >= FRAME_STAGE_FLAT_ROW
    flat = _count_leading_true(is_flat)
    end = FRAME_CORNER_PX + flat
    if flat < FRAME_CORNER_PX or end + 1 >= lines.shape[0]:
        return 0
    # The stage ends on the shared screen's border; a share's own header bar is flat in its colour above its title, and
    # its padding ends on letters, not on an edge across
    stage_line = lines[end - 1].astype(numpy.int16)
    is_edge = numpy.maximum(numpy.abs(lines[end] - stage_line), numpy.abs(lines[end + 1] - stage_line)) > FRAME_EDGE_JUMP

    return end if is_edge.mean() >= FRAME_SIDE_SHARE else 0


# How far a call's stage stands off the grey of the share it frames: the edge the stage ends on is 12 levels
FRAME_STAGE_GREY_STEP = 12
# The same with a band above the share and one below, a letterbox: a call's light stage stood 3 and 11 levels off a
# white board's sides, a slide's own margins 0
FRAME_STAGE_PAIR_GREY_STEP = 2
# The share's outer columns its background is read on, a share of its width each side
FRAME_STAGE_SIDE_SHARE = 0.03


def _trim_stage(screen: numpy.ndarray,
                box: Box,
                read_band_texts: Callable[[numpy.ndarray], list[str]] | None) -> Box:
    """`box` with the call's flat stage cut off above and below inside it: a band of flat lines where the text reader
    finds no word, but for the presenter's name label in the bottom band's left FRAME_STAGE_LABEL_SHARE. Flat by its
    pixels alone, a slide's title line on a white page is a band too. The sides are left: a share's own blank margin
    on its left was cut as stage on a scene, and no recording has shown a stage beside the screen."""

    if read_band_texts is None:
        return box
    x0, y0, x1, y1 = box
    inside = screen[y0:y1, x0:x1]
    label_end = round(FRAME_STAGE_LABEL_SHARE * (x1 - x0))
    top, bottom = _count_stage_lines(inside), _count_stage_lines(inside[::-1, label_end:])
    if top + bottom >= y1 - y0:
        return box
    top = top if top and not read_band_texts(inside[:top]) else 0
    bottom = bottom if bottom and not read_band_texts(inside[y1 - y0 - bottom:, label_end:]) else 0
    # A band alone is a stage only in another grey than the share it frames: a broadcast's dark slide, its own lower
    # margin flat under two code panels whose borders ran across most of its width, lost its last code lines as stage
    # (DEFECTS 350). A call letterboxes its share, a band above and one below, and its light stage stands only 3 to 11
    # levels off a white board's sides, while a slide's own margin is its background's very grey: two bands are a stage
    # from FRAME_STAGE_PAIR_GREY_STEP off
    # The share's own background is read on its outer columns, beside its content: a slide's dark margin there is the
    # grey of its lower margin, where the whole share's median was pulled toward its lighter code panels
    side = max(1, round(FRAME_STAGE_SIDE_SHARE * (x1 - x0)))
    middle = inside[top:y1 - y0 - bottom]
    share = (float(numpy.median(numpy.concatenate([middle[:, :side], middle[:, -side:]], axis=1)))
             if top + bottom < y1 - y0 else None)
    step = FRAME_STAGE_PAIR_GREY_STEP if top and bottom else FRAME_STAGE_GREY_STEP
    if share is not None and top and abs(float(numpy.median(inside[:top])) - share) < step:
        top = 0
    if (share is not None and bottom
            and abs(float(numpy.median(inside[y1 - y0 - bottom:, label_end:])) - share) < step):
        bottom = 0

    return x0, y0 + top, x1, y1 - bottom


def _is_call_bar(screen: numpy.ndarray,
                 box: Box,
                 bottom: int,
                 read_band_texts: Callable[[numpy.ndarray], list[str]] | None) -> bool:
    """Whether the band of `box` from its top down to row `bottom` is a call's bar: the text reader finds a running
    time on it."""

    if read_band_texts is None:
        return False
    texts = read_band_texts(screen[box[1]:bottom, box[0]:box[2]])

    return any(FRAME_TIMER_PATTERN.match(text) for text in texts)


def _is_flat_stage(strip: numpy.ndarray,
                   width: int) -> bool:
    """Whether a strip at the screen's left is a call's flat stage: at least FRAME_STAGE_MIN_WIDTH of the screen wide,
    and FRAME_STAGE_FLAT_ROW of its pixels within FRAME_STAGE_LEVELS grey levels of its median (an editor's file tree
    or a slide's margin with words on it is not)."""

    if strip.shape[1] < FRAME_STAGE_MIN_WIDTH * width:
        return False

    return bool((numpy.abs(strip.astype(numpy.int16) - numpy.median(strip)) <= FRAME_STAGE_LEVELS).mean()
                >= FRAME_STAGE_FLAT_ROW)


def find_framed_share(screen: numpy.ndarray,
                      neighbours: list[numpy.ndarray],
                      read_band_texts: Callable[[numpy.ndarray], list[str]] | None = None) -> BarMargins | None:
    """(top, bottom, left, right) to cut a share framed inside a call window down to its outline, or None when the
    screen holds no such frame. `neighbours`, the grey frames of the same screen box taken around it, must show
    everything above and left of the outline standing still, so a slide's own large table, whose title changes with it,
    is never taken for the frame."""

    # STEP 1: the long straight edges down the left part and across the top part of the screen, short breaks joined in
    # an edge down (joined across, the top edge ran on into the call's own lines on its left and lost its corner)
    height, width = screen.shape
    grey = screen.astype(numpy.int16)
    is_down_edge = numpy.zeros_like(screen, dtype=bool)
    is_down_edge[:, 1:-1] = numpy.abs(grey[:, 2:] - grey[:, :-2]) > FRAME_EDGE_JUMP
    is_across_edge = numpy.zeros_like(screen, dtype=bool)
    is_across_edge[1:-1, :] = numpy.abs(grey[2:, :] - grey[:-2, :]) > FRAME_EDGE_JUMP
    downs = _list_long_edges(is_down_edge[:, :round(FRAME_SEARCH_SHARE * width)].T, round(FRAME_SIDE_SHARE * height),
                             round(FRAME_GAP_SHARE * height))
    acrosses = _list_long_edges(is_across_edge[:round(FRAME_SEARCH_SHARE * height)], round(FRAME_SIDE_SHARE * width), 0)

    # STEP 2: a corner where an edge down and an edge across start together; the largest box they enclose
    boxes = [(x, y, across_start + across_length, down_start + down_length)
             for x, down_start, down_length in downs for y, across_start, across_length in acrosses
             if abs(down_start - y) <= FRAME_CORNER_PX and abs(across_start - x) <= FRAME_CORNER_PX]
    # A share the screen box already starts inside, under the call's row of cameras, has no top edge of its own: its
    # left edge runs down from the screen's top with only the call's flat stage left of its outer border, and its right
    # side is where the edges across it starting at that column end. The settings panel beside such a share was kept
    # (DEFECTS 105)
    from_top = [(x, down_start, down_length) for x, down_start, down_length in downs if down_start <= FRAME_CORNER_PX]
    stage_boxes = []
    if from_top and _is_flat_stage(screen[:, :min(x for x, _, _ in from_top)], width):
        stage_boxes = [(x, 0, max(start + length for _, start, length in starting), down_start + down_length)
                       for x, down_start, down_length in from_top
                       for starting in [[edge for edge in acrosses if abs(edge[1] - x) <= FRAME_CORNER_PX]] if starting]
    # A right or bottom side short of the screen's edge stands only where an edge runs along it: a lecture's top line,
    # broken by the call's floating overlay, ended the box there and cut a slide's right third off (DEFECTS 95)
    boxes = [_expand_unlined_sides(screen, box) for box in boxes]
    # A call's share runs to the window's right or bottom edge; a screenshot inside a slide stops short of both, and a
    # slide was cut down to its bordered screenshot, its title lost (DEFECTS 96)
    boxes = [box for box in boxes if width - box[2] <= FRAME_EDGE_REACH * width or height - box[3] <= FRAME_EDGE_REACH * height]
    # ... and one starting at the screen's top stands on the call's stage below it as well, a call's panel open beside
    # it: under it, past the name label's left fifth, the stage is flat, or it reaches the screen's bottom
    boxes += [box for box in stage_boxes if height - box[3] <= FRAME_EDGE_REACH * height or _is_flat_stage(
        screen[box[3] + FRAME_CORNER_PX:, box[0] + round(FRAME_STAGE_LABEL_SHARE * (box[2] - box[0])):box[2]], 0)]
    boxes = [box for box in boxes if (box[2] - box[0]) * (box[3] - box[1]) >= FRAME_MIN_AREA * width * height]
    if not boxes:
        return None
    largest = max(boxes, key=lambda found: (found[2] - found[0]) * (found[3] - found[1]))

    # STEP 3: nothing outside it changed, above and on the left; of the corners on the largest box's left side whose
    # outside stood still, the highest, or the first below it whose band from that top shows a call's running time. A
    # call on the web in a light theme draws its window and its share down one line: the share's top starts on that line
    # partway down, and the window's corner held the call's bar with its timer inside the cut (DEFECTS 99). The lowest
    # corner was wrong: a share's own still header has a line under it, and three shares were cut there, header lost
    inner = [(largest[0], y, largest[2], largest[3]) for y, start, length in acrosses
             if y > largest[1] + FRAME_CORNER_PX and abs(start - largest[0]) <= FRAME_CORNER_PX
             and abs(start + length - largest[2]) <= FRAME_CORNER_PX
             and (largest[2] - largest[0]) * (largest[3] - y) >= FRAME_MIN_AREA * width * height]
    on_side = sorted((box for box in [*boxes, *inner] if abs(box[0] - largest[0]) <= FRAME_CORNER_PX
                      and box[1] >= largest[1] and _is_still_outside(screen, neighbours, box)),
                     key=lambda found: found[1])
    box = on_side[0] if on_side else None
    if box is not None:
        box = next((lower for lower in on_side[1:] if lower[1] > box[1] + FRAME_CORNER_PX
                    and _is_call_bar(screen, box, lower[1], read_band_texts)), box)
    if box is None:
        return None
    # STEP 4: a shared screen letterboxed on the call's flat stage is cut at its own outline (DEFECTS 109)
    x0, y0, x1, y1 = _trim_stage(screen, box, read_band_texts)
    cut = tuple(margin if margin > FRAME_CORNER_PX else 0 for margin in (y0, height - y1, x0, width - x1))

    return cut if any(cut) else None


def count_still_edges(screen: numpy.ndarray,
                      neighbours: list[numpy.ndarray],
                      is_other_view_counted: bool = False,
                      covered: list[numpy.ndarray] | None = None) -> BarMargins | None:
    """(top, bottom, left, right): how many rows and columns at each edge of one grey screen frame differ from almost
    none of `neighbours`, the grey frames of the same screen box taken around it; unless `is_other_view_counted`, only
    those that show the same view (changed in at most BAR_SAME_VIEW_CHANGE of their pixels). None when fewer than
    BAR_MIN_NEIGHBOURS of them are left."""

    row_differs, column_differs = [], []
    masks = covered if covered is not None else [None] * len(neighbours)
    for other, mask in [(other, mask) for other, mask in zip(neighbours, masks) if other.shape == screen.shape]:
        changed = _find_changed_pixels(screen, other)
        # the pixels a camera tile's cover filled on either frame say nothing of the view: they match on both
        share = float(changed[~mask].mean()) if mask is not None and mask.shape == changed.shape and (~mask).any() \
            else float(changed.mean())
        if not is_other_view_counted and share > BAR_SAME_VIEW_CHANGE:
            continue
        row_differs.append(changed.mean(axis=1) > BAR_LINE_DIFFERS)
        column_differs.append(changed.mean(axis=0) > BAR_LINE_DIFFERS)
    if len(row_differs) < BAR_MIN_NEIGHBOURS:
        return None
    is_static_row = _compute_smoothed_profile(numpy.mean(row_differs, axis=0)) < BAR_STATIC_SHARE
    is_static_column = _compute_smoothed_profile(numpy.mean(column_differs, axis=0)) < BAR_STATIC_SHARE

    return (_count_leading_true(is_static_row), _count_leading_true(is_static_row[::-1]),
            _count_leading_true(is_static_column), _count_leading_true(is_static_column[::-1]))


def _find_cuts_at_bar_edges(screen: numpy.ndarray,
                      still: BarMargins) -> BarMargins:
    """`still` cut inward to the first changing row or column on each side that ends on a straight edge, the border a
    bar has and a slide header lacks; a pair of sides whose cuts would keep less than half of the screen is not cut,
    a screen that stood still rather than one with bars."""

    top, bottom, left, right = still
    height, width = screen.shape[:2]
    # a pair of sides that would keep less than half of the screen stood still rather than holding bars; it decides
    # nothing for the other pair: a still editor pane over most of the width threw away a lecture's top cut too
    if top + bottom > (1 - BAR_MIN_KEPT) * height:
        top, bottom = 0, 0
    if left + right > (1 - BAR_MIN_KEPT) * width:
        left, right = 0, 0
    # a top or bottom cut is moved onto the bar's own border, the straight edge near it
    top_edge = _find_edge_near(screen, top) if top else None
    bottom_edge = _find_edge_near(screen, height - bottom, is_bottom=True) if bottom else None
    top = top_edge if top_edge is not None else 0
    bottom = height - bottom_edge if bottom_edge is not None else 0
    left = left if _has_edge_near(screen.T, left) else 0
    right = right if _has_edge_near(screen.T, width - right) else 0

    return top, bottom, left, right


def _is_band_column(column: numpy.ndarray,
                    level: float) -> bool:
    """Whether a column is part of a letterbox band of grey `level`: flat around its own grey, which is the band's
    (compression shifts a dark band by a few levels across it)."""

    own = numpy.median(column)

    return bool(abs(own - level) <= SIDE_BAND_LEVELS
                and (numpy.abs(column - own) <= SIDE_BAND_LEVELS).mean() >= SIDE_BAND_FLAT_ROW)


# A broadcast's slide shown whole keeps the stream's strips at its edges: a 5 px band on top, a backdrop strip on the
# left, a grey line on the right (froscon talks 3143 and 3147, DEFECTS 262). No measure told them from a slide's own
# margin by its pixels; each ends where the slide starts, a step along most of the line
# At most this many lines a side: 38c3 talk 797's panel view kept a black band of 9 lines on top and 11 on the right,
# past 8 (DEFECTS 472); over 629 stored frames of the public talks 12 changes those 4 frames and no other
EDGE_LINE_MOST_PX = 12
# The backdrop's strip beside a slide is a picture, no flat line: the slide starts where a line steps this many grey
# levels from the line outside it along EDGE_STEP_SHARE of its length (froscon 3147 frame 142: 227 to 10 at column 5)
EDGE_STEP_LEVELS = 40
EDGE_STEP_SHARE = 0.6


def _count_strip_lines(lines: numpy.ndarray) -> int:
    """How many of the outermost `lines` (rows, first the edge's) are a strip: every line outside the last of the
    steps to the slide that follow each other from the first within EDGE_LINE_MOST_PX. A flat margin with no step is the
    screen's own and stays: a shared screen's plain border was cut from frames that held nothing else there."""

    most = min(EDGE_LINE_MOST_PX, len(lines) // 4)
    steps = [line for line in range(1, most + 1)
             if (numpy.abs(lines[line] - lines[line - 1]) >= EDGE_STEP_LEVELS).mean() >= EDGE_STEP_SHARE]
    stepped = 0
    for line in steps:
        if line != stepped + 1 and stepped:
            break
        stepped = line

    return stepped


def find_edge_strips(screen: numpy.ndarray) -> tuple[int, int, int, int]:
    """(top, bottom, left, right): how many of the outermost lines of each side of one grey screen frame are a strip
    round the slide, up to EDGE_LINE_MOST_PX: the lines outside a step to the slide (`_count_strip_lines`)."""

    grey = screen.astype(numpy.int16)
    top, bottom, left, right = (_count_strip_lines(lines) for lines in (grey, grey[::-1], grey.T, grey.T[::-1]))

    return top, bottom, left, right


def find_flat_side_bands(screen: numpy.ndarray) -> tuple[int, int]:
    """(left, right): the columns at each side of one grey screen frame that are a flat letterbox band: every column
    holds SIDE_BAND_FLAT_ROW of its pixels within SIDE_BAND_LEVELS of the band's grey, the band is at least
    SIDE_BAND_MIN_SHARE and at most SIDE_BAND_MOST_SHARE of the width, and it ends on a straight edge down at least
    BAR_EDGE_SHARE of the height. Up to SIDE_BAND_RING_PX columns at the very edge (a call's coloured speaker outline)
    are passed over. A call drew a lecture's 4:3 slides in a 16:9 window between bands of grey 16, which the black
    stage rule (below 4) leaves, and a slide shown full width covers the same columns, so the bar trim never found them
    still (108 frames of a lecture)."""

    height, width = screen.shape
    grey = screen.astype(numpy.int16)
    sides = []
    for columns in (grey.T, grey.T[::-1]):
        # the band's grey is read past the ring
        level = numpy.median(columns[SIDE_BAND_RING_PX + 1])
        start = next((column for column in range(SIDE_BAND_RING_PX + 2) if _is_band_column(columns[column], level)),
                     None)
        if start is None:
            sides.append(0)
            continue
        end = start
        while end < width and _is_band_column(columns[end], level):
            end += 1
        is_edge = (end + EDGE_STEP_PX < width
                   and (numpy.abs(columns[end + EDGE_STEP_PX] - level) > EDGE_JUMP).mean() >= BAR_EDGE_SHARE)
        is_sized = SIDE_BAND_MIN_SHARE * width <= end <= SIDE_BAND_MOST_SHARE * width
        sides.append(end if is_edge and is_sized else 0)

    return sides[0], sides[1]


def find_bar_margins(screen: numpy.ndarray,
                     neighbours: list[numpy.ndarray],
                     covered: list[numpy.ndarray] | None = None) -> BarMargins:
    """(top, bottom, left, right): the rows and columns at the edges of one grey screen frame that differ from
    almost none of `neighbours`, the grey frames of the same screen box taken around it, each side cut where it ends
    on a straight edge. NO_BARS when there are fewer than BAR_MIN_NEIGHBOURS of the same size."""

    # Each side is counted against every neighbour first, then against those of the same view only. A room camera's
    # view shares a lecture's full-frame box with its terminal frames and differs in every row, so the terminal's still
    # menu bar read as changing (51 frames kept their bars, DEFECTS 108); counted only against the same view, a screen's
    # still lines under its menu bar joined the bar, the band ended on no edge, and a lecture's bars stayed on 45 more
    # frames (DEFECTS 112). Other views mark where a screen's content starts; the same view finds a bar they hide
    counts = [count_still_edges(screen, neighbours, is_other_view_counted=True),
              count_still_edges(screen, neighbours, covered=covered)]
    cuts = [_find_cuts_at_bar_edges(screen, still) for still in counts if still is not None]
    if not cuts:
        return NO_BARS
    margins = tuple(next((cut[side] for cut in cuts if cut[side]), 0) for side in range(4))

    return margins


