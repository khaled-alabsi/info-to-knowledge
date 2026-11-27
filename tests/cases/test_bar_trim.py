"""Cutting the bars off the shared screen: the owner's requirement that the start menu, the taskbar and the other
panels that carry nothing reach no frame. Measured in research/crop-methods/ ("bar trim, nearby frames").

A bar is found by what it does, not by how it looks: it stays the same while the content beside it changes. So each
taken frame is compared with the frames taken around it that share its screen box, and the rows and columns at the
screen's edges that differ from almost none of them are cut. A screen that never changed says nothing about which
part is a bar, and is left whole.
"""

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from services import face_detection_service
from services import frame_extraction_service
from utils import screen_crop_utils

SCREEN = (0, 20, 400, 245)                  # x0, y0, x1, y1 of the shared screen in a 480 x 270 frame
TITLE_BAR_PX = 18                           # a static title bar at the top of the shared screen
TASKBAR_PX = 16                             # a static taskbar at its bottom
# How many pixels of a bar a trim may leave on the content side
TRIM_TOLERANCE_PX = 5


def _draw_screen(slide: int,
                 height: int = 225,
                 width: int = 400) -> numpy.ndarray:
    """A shared screen: the same title bar and taskbar on every slide, and between them content that fills the
    screen from edge to edge and differs per slide, in blocks of 8 pixels."""

    screen = numpy.full((height, width), 230, numpy.uint8)
    screen[:TITLE_BAR_PX] = 60
    screen[4:12, 10:120] = 200                              # the title bar's own text, the same on every slide
    screen[height - TASKBAR_PX:] = 90
    screen[height - 12:height - 4, 150:250] = 180           # the taskbar's icons
    blocks = numpy.random.default_rng(slide).integers(100, 230, ((height - TITLE_BAR_PX - TASKBAR_PX) // 8 + 1, width // 8))
    content = numpy.kron(blocks, numpy.ones((8, 8), numpy.int64)).astype(numpy.uint8)
    screen[TITLE_BAR_PX:height - TASKBAR_PX] = content[:height - TITLE_BAR_PX - TASKBAR_PX]

    return screen


def test_the_rows_that_stay_the_same_while_the_content_changes_are_cut():
    own = _draw_screen(0)
    neighbours = [_draw_screen(slide) for slide in range(1, 6)]
    top, bottom, left, right = screen_crop_utils.find_bar_margins(own, neighbours)
    harness.check("most of the title bar is cut, and none of the content", TITLE_BAR_PX - TRIM_TOLERANCE_PX <= top <= TITLE_BAR_PX,
                  f"top={top}")
    harness.check("most of the taskbar is cut, and none of the content", TASKBAR_PX - TRIM_TOLERANCE_PX <= bottom <= TASKBAR_PX,
                  f"bottom={bottom}")
    harness.check("the sides, where the content reaches the edge, are kept", (left, right) == (0, 0),
                  f"left={left} right={right}")


def test_frames_of_another_view_in_the_same_box_do_not_hide_the_bars():
    """Phase 2 audit round 1: a lecture's room camera shares its screen frames' full-frame box, and every row of it
    differs from a screen frame; compared with it, the terminal's still title bar read as changing and was kept."""

    own = _draw_screen(0)
    room = 255 - _draw_screen(7)
    neighbours = [_draw_screen(slide) for slide in range(1, 6)] + [room.copy() for _ in range(5)]
    top, bottom, _, _ = screen_crop_utils.find_bar_margins(own, neighbours)
    harness.check("the title bar and the taskbar are cut though half the neighbours show another view",
                  top >= TITLE_BAR_PX - TRIM_TOLERANCE_PX and bottom >= TASKBAR_PX - TRIM_TOLERANCE_PX,
                  f"top={top} bottom={bottom}")


def test_a_screen_that_never_changed_or_has_too_few_neighbours_is_left_whole():
    still = _draw_screen(0)
    harness.check("a screen that never changed says nothing about bars",
                  screen_crop_utils.find_bar_margins(still, [still.copy() for _ in range(5)]) == (0, 0, 0, 0))
    harness.check("fewer than three neighbours decide nothing",
                  screen_crop_utils.find_bar_margins(still, [_draw_screen(1), _draw_screen(2)]) == (0, 0, 0, 0))
    shifted = [numpy.roll(still, 1, axis=1) for _ in range(5)]
    harness.check("a screen that only moved by a pixel did not change",
                  screen_crop_utils.find_bar_margins(still, shifted) == (0, 0, 0, 0))


def _build_screen_video(folder: Path) -> Path:
    """Six slides of 3 s each at 5 fps, on a black stage, with the same title bar and taskbar throughout."""

    target = folder / "bars.mp4"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), 5, (480, 270))
    x0, y0, x1, y1 = SCREEN
    for slide in range(6):
        frame = numpy.zeros((270, 480, 3), numpy.uint8)
        frame[y0:y1, x0:x1] = cv2.cvtColor(_draw_screen(slide), cv2.COLOR_GRAY2BGR)
        for _ in range(15):
            writer.write(frame)
    writer.release()

    return target


def _extract(trim: bool) -> dict:
    folder = Path(tempfile.mkdtemp())
    with media_fixtures.face_finder(lambda gray: []):
        frames = frame_extraction_service.extract_frames(
            _build_screen_video(folder), folder / "frames",
            config.FrameSettings(mode="change", max_count=100, box_every_seconds=1, trim_bars=trim))

    return {**frames, "folder": folder}


def test_change_mode_writes_every_frame_without_its_bars():
    frames = _extract(trim=True)
    items = frames["items"]
    harness.check("one frame per slide", len(items) == 6, str([item["timestamp"] for item in items]))
    boxes = {tuple(item["box"]) for item in items}
    harness.check("each frame keeps the screen box it was found in", all(item["screenBox"] == list(SCREEN) for item in items),
                  str([item["screenBox"] for item in items]))
    harness.check("and is cut to a box inside it, without the title bar and the taskbar",
                  all(SCREEN[1] + TITLE_BAR_PX - TRIM_TOLERANCE_PX <= box[1] <= SCREEN[1] + TITLE_BAR_PX
                      and SCREEN[3] - TASKBAR_PX <= box[3] <= SCREEN[3] - TASKBAR_PX + TRIM_TOLERANCE_PX
                      and (box[0], box[2]) == (SCREEN[0], SCREEN[2]) for box in boxes), str(boxes))
    first = cv2.imread(str(frames["folder"] / items[0]["path"]))
    box = items[0]["box"]
    harness.check("the frame written is that box", first.shape[:2] == (box[3] - box[1], box[2] - box[0]),
                  f"{first.shape} vs {box}")


def test_with_the_trim_off_the_frames_keep_their_bars():
    items = _extract(trim=False)["items"]
    harness.check("the box is the screen box", all(item["box"] == item["screenBox"] == list(SCREEN) for item in items),
                  str([item["box"] for item in items]))


def test_the_rows_cut_off_the_top_are_not_kept():
    """R8: the owner is against keeping the address-bar band; it rarely held anything useful."""

    frames = _extract(trim=True)
    harness.check("no frame carries a band", not any("band" in item or "bandBox" in item for item in frames["items"]),
                  str(frames["items"][0]))
    harness.check("and no band file is written", not list(frames["folder"].glob("*-band.png")))



def test_a_mark_with_no_letter_is_no_word_a_cut_must_keep_whole_defect():
    # A slide's 4 px grey rim was read as "|" 70 px tall; the word check took it for text running across the top cut and
    # left the 5 px band along the top on 20 of one talk's 50 frames. Only a word with a letter or a digit counts
    rim = {"text": "|", "box": [0, 0, 4, 70]}
    title = {"text": "Booting", "box": [30, 0, 170, 60]}
    harness.check("a mark of an edge does not stop the top cut",
                  screen_crop_utils.keep_words_whole((6, 0, 0, 0), [rim], 546, 438) == (6, 0, 0, 0))
    harness.check("a word with letters still does",
                  screen_crop_utils.keep_words_whole((6, 0, 0, 0), [rim, title], 546, 438) == (0, 0, 0, 0))


def test_a_cut_a_word_runs_across_is_no_bar_cut():
    """A picture's border inside a slide stood still beside the slide's margin and was cut as a bar, through the
    slide's title (38c3-fault-injection frame 17, 2026-10-15). A bar's edge never has a word through it."""

    title = {"text": "Booting", "box": [30, 20, 170, 60]}
    menu = {"text": "File", "box": [4, 2, 30, 18]}
    harness.check("a left cut through a title is dropped", screen_crop_utils.keep_words_whole((0, 0, 91, 0), [title], 640, 480) == (0, 0, 0, 0))
    harness.check("a top cut below a menu bar's words stays", screen_crop_utils.keep_words_whole((24, 0, 0, 0), [menu], 640, 480) == (24, 0, 0, 0))
    harness.check("a right cut no word reaches stays", screen_crop_utils.keep_words_whole((0, 0, 0, 40), [title, menu], 640, 480) == (0, 0, 0, 40))


def _draw_web_call(listing: int,
                   timer: int) -> numpy.ndarray:
    """A call on the web, 640 x 400: the browser's and the call's bars on top (the call's timer in them), its side bar
    on the left, and the share inside a dark outline from (40, 90) to the right and bottom edges, its list of lines
    drawn per `listing`, a name label across the outline's bottom-left corner."""

    screen = numpy.full((400, 640), 255, numpy.uint8)
    screen[:40] = 232                                       # the browser's tabs and address
    screen[12:24, 30:200] = 120
    screen[40:80, 30:600] = 250                             # the call's bar: its timer and controls
    screen[55:65, 40:40 + 6 * (timer % 5 + 1)] = 90
    screen[55:65, 400:600:30] = 110
    screen[80:, :28] = 238                                  # the call's side bar and its icons
    screen[100:380:40, 8:20] = 150
    screen[90, 40:] = 30                                    # the share's outline
    screen[90:, 40] = 30
    rows = numpy.random.default_rng(listing).integers(60, 300, 18)
    for number, length in enumerate(rows):                  # the shared list's lines
        screen[110 + number * 15:116 + number * 15, 60:60 + length] = 70
    screen[370:384, 22:70] = 90                             # the name label across the outline
    screen[374:380, 26:66] = 240

    return screen


def _draw_table_slide(slide: int) -> numpy.ndarray:
    """A slide, 640 x 400: its title, then a large table with a dark outline from (40, 90), its title and its rows
    drawn per `slide`, so both change together."""

    screen = numpy.full((400, 640), 255, numpy.uint8)
    title = numpy.random.default_rng(slide).integers(0, 2, 40).astype(bool)
    for number, is_ink in enumerate(title):                 # the title's letters
        if is_ink:
            screen[30:60, 40 + number * 8:44 + number * 8] = 40
    screen[90, 40:600] = 30
    screen[390, 40:600] = 30
    screen[90:391, 40] = 30
    screen[90:391, 600] = 30
    for number, length in enumerate(numpy.random.default_rng(slide + 10).integers(60, 300, 18)):
        screen[105 + number * 15:110 + number * 15, 60:60 + length] = 70

    return screen


def test_a_share_framed_in_a_web_call_is_cut_at_its_outline_and_a_table_slide_is_not():
    """DEFECTS.md 66: phase 2 kept the whole call window, the call's timer making its bar look like content and the
    name label across the outline undoing the left cut."""

    call = [_draw_web_call(listing, timer) for listing, timer in ((0, 0), (1, 1), (2, 2), (3, 3))]
    cut = screen_crop_utils.find_framed_share(call[0], call[1:])
    harness.check("the share is cut at its outline, the label across it no matter",
                  cut is not None and abs(cut[0] - 90) <= 2 and abs(cut[2] - 40) <= 2 and cut[1] == cut[3] == 0, str(cut))
    calendar = numpy.full((400, 640), 245, numpy.uint8)
    calendar[30:390:20, 10:630] = 120                       # another window filling the screen: no outline
    beside = screen_crop_utils.find_framed_share(call[0], [*call[1:], calendar])
    harness.check("a neighbour showing another window is left out of the comparison",
                  beside is not None and abs(beside[0] - 90) <= 2, str(beside))
    menu = call[3].copy()
    menu[0:80, 300:640] = 60                                # a menu open over the bars on one neighbour only
    once = screen_crop_utils.find_framed_share(call[0], [call[1], call[2], menu])
    harness.check("one neighbour whose bars changed refuses nothing when the others' stood still",
                  once is not None and abs(once[0] - 90) <= 2, str(once))
    overlaid = [screen.copy() for screen in call]
    for screen in overlaid:
        screen[84:100, 430:500] = 200                       # a floating overlay over the outline's top edge
    broken = screen_crop_utils.find_framed_share(overlaid[0], overlaid[1:])
    harness.check("a top edge broken by an overlay is no right side: the right is not cut (DEFECTS 95)",
                  broken is not None and abs(broken[0] - 90) <= 2 and broken[3] == 0, str(broken))
    shots = []
    for listing in range(4):
        slide = numpy.full((400, 640), 250, numpy.uint8)
        slide[20:40, 30:300] = 60                           # the slide's title, the same on every frame
        page = _draw_web_call(listing, 0)[80:400, 30:570]   # a screenshot in a dark border, 84% by 80% of the slide
        slide[60:380, 60:600] = page
        slide[60, 60:600] = slide[379, 60:600] = 30
        slide[60:380, 60] = slide[60:380, 599] = 30
        shots.append(slide)
    held = screen_crop_utils.find_framed_share(shots[0], shots[1:])
    harness.check("a slide's bordered screenshot, short of the screen's right and bottom, is no share (DEFECTS 96)",
                  held is None, str(held))
    harness.check("not while nothing inside it changed beside it",
                  screen_crop_utils.find_framed_share(call[0], [call[0].copy() for _ in range(3)]) is None)
    slides = [_draw_table_slide(slide) for slide in range(4)]
    harness.check("a slide whose table changes with its title is not cut to the table",
                  screen_crop_utils.find_framed_share(slides[0], slides[1:]) is None,
                  str(screen_crop_utils.find_framed_share(slides[0], slides[1:])))


def _draw_light_web_call(listing: int,
                         timer: int) -> numpy.ndarray:
    """A call on the web in a light theme, 640 x 400: the browser's bars, then the call's window, whose left line runs from
    its bar (y 50, the call's timer in it) down to the bottom, and the share inside it from y 90, its outline 221 grey
    between a 232 rail and a 243 share, so 22 levels across it; the share's top starts on the window's line."""

    screen = numpy.full((400, 640), 255, numpy.uint8)
    screen[:40] = 232                                       # the browser's tabs and address
    screen[12:24, 30:200] = 120
    screen[50:, :26] = 232                                  # the call's rail, from its bar down
    screen[50:, 26:28] = 221                                # the window's left line, the share's left side below y 90
    screen[50:52, 28:] = 221                                # the window's top
    screen[58:68, 40:40 + 6 * (timer % 5 + 1)] = 90         # the call's timer and controls in its bar
    screen[58:68, 400:600:30] = 110
    screen[90:92, 28:] = 221                                # the share's top, starting on the window's line
    screen[92:, 28:] = 243
    for number, length in enumerate(numpy.random.default_rng(listing).integers(60, 300, 18)):
        screen[110 + number * 15:116 + number * 15, 60:60 + length] = 70

    return screen


def test_a_light_share_on_the_call_window_s_line_is_cut_at_the_share():
    """DEFECTS.md 99: a call on the web in a light theme draws a faint outline (22 grey levels across it, under the 40 the
    rule asked for), and the share's top starts partway down the window's own left line, so the window's corner, with
    the call's bar and timer inside it, was the only corner found."""

    call = [_draw_light_web_call(listing, timer) for listing, timer in ((0, 0), (1, 1), (2, 2), (3, 3))]
    cut = screen_crop_utils.find_framed_share(call[0], call[1:], lambda band: ["07:24", "Chat"])
    harness.check("the share is cut at its own top, the call's bar and timer left out, and at the window's left line",
                  cut is not None and abs(cut[0] - 91) <= 3 and abs(cut[2] - 27) <= 3 and cut[1] == cut[3] == 0, str(cut))
    header = screen_crop_utils.find_framed_share(call[0], call[1:], lambda band: ["Dashboards", "Projects"])
    harness.check("a band changing in a spot with no running time on it is a share's own header, kept inside the cut",
                  header is not None and abs(header[0] - 51) <= 3, str(header))


def _draw_staged_web_call(listing: int) -> numpy.ndarray:
    """A grey 400x640 call on the web whose shared screen stands letterboxed on the call's flat stage: the stage's outline
    from (28, 90), the screen from row 110 to 360 with a list changing on it, the presenter's name label in the band
    below it."""

    screen = numpy.full((400, 640), 232, dtype=numpy.uint8)
    screen[90:92, 28:] = 221
    screen[92:, 28:30] = 221
    screen[92:, 30:] = 243
    screen[110:360, 30:] = 255
    screen[110:130, 30:] = 222                              # the shared screen's own tab bar
    screen[359, 30:] = 195                                  # its bottom border, a line as the recording draws it
    for number, length in enumerate(numpy.random.default_rng(listing).integers(60, 300, 14)):
        screen[140 + number * 15:146 + number * 15, 60:60 + length] = 70
    screen[370:388, 12:100] = 92                            # the name label, across the stage's left line

    return screen


def test_a_share_letterboxed_on_the_call_s_stage_is_cut_at_the_shared_screen():
    """DEFECTS 109: the outline found is the stage's; the flat bands above and below the shared screen, the name label
    in the lower one, are cut off when the text reader finds no word in them, and kept when it does (a slide's title
    on a white page is flat by its pixels too)."""

    call = [_draw_staged_web_call(listing) for listing in range(4)]
    cut = screen_crop_utils.find_framed_share(call[0], call[1:], lambda band: [])
    harness.check("the stage's band above the screen and the band with the label below it are cut off",
                  cut is not None and abs(cut[0] - 110) <= 3 and abs(cut[1] - 40) <= 3, str(cut))
    worded = screen_crop_utils.find_framed_share(call[0], call[1:], lambda band: ["Roadmap"])
    harness.check("a flat band holding a word is kept", worded is not None and worded[0] <= 92 and worded[1] == 0,
                  str(worded))


def test_a_cut_starting_on_a_line_of_text_moves_out_before_it():
    """A terminal's first line began a pixel under its tmux bar, and the cut took the top of its letters on 11 frames of
    a lecture: a top cut a line starts on, or within a few pixels of, moves out before that line."""

    words = [{"text": "pass", "box": [60, 41, 120, 53]}]
    harness.check("a top cut at the line's first row moves above it",
                  screen_crop_utils.keep_words_whole((41, 0, 0, 0), words, 400, 225)[0] == 38)
    harness.check("a top cut well above the line stays",
                  screen_crop_utils.keep_words_whole((30, 0, 0, 0), words, 400, 225)[0] == 30)
    bottom = [{"text": "end", "box": [60, 190, 120, 203]}]
    harness.check("a bottom cut at the line's last row moves below it",
                  screen_crop_utils.keep_words_whole((0, 22, 0, 0), bottom, 400, 225)[1] == 19)


def test_a_still_pane_over_most_of_the_width_leaves_the_title_bar_cut():
    """A lecture's still editor pane covered most of the width: the left and right cuts together would keep less than
    half of the screen, and that threw away the top cut of its title bar too (DEFECTS 118)."""

    def draw(slide: int) -> numpy.ndarray:
        screen = _draw_screen(slide)
        screen[TITLE_BAR_PX:225 - TASKBAR_PX, 100:] = 200          # a pane that never changes, 3/4 of the width
        return screen

    top, bottom, left, right = screen_crop_utils.find_bar_margins(draw(0), [draw(slide) for slide in range(1, 6)])
    harness.check("the title bar and the taskbar are cut; the still pane is kept",
                  TITLE_BAR_PX - TRIM_TOLERANCE_PX <= top <= TITLE_BAR_PX and bottom > 0 and (left, right) == (0, 0),
                  str((top, bottom, left, right)))


def test_a_flat_letterbox_band_at_the_sides_is_cut_on_its_own_frame():
    """DEFECTS 119: a call drew 4:3 slides in a 16:9 window between bands of grey 16, a speaker's coloured outline round
    the window and the call's label over the right band; a slide shown full width covered the same columns, so they never
    stood still across frames."""

    screen = numpy.full((360, 640), 16, numpy.uint8)
    screen[:, 80:560] = 235                                 # the slide
    screen[60:200, 120:500] = 40                            # its text, a block
    screen[:, :2] = 170                                     # the outline
    screen[:, -2:] = 170
    screen[40:62, 590:636] = 120                            # the call's label over the right band
    left, right = screen_crop_utils.find_flat_side_bands(screen)
    harness.check("both bands cut at the slide's edges", abs(left - 80) <= 2 and abs(right - 80) <= 2, str((left, right)))
    full = numpy.full((360, 640), 235, numpy.uint8)
    full[60:200, 40:600] = 40
    harness.check("a slide shown full width loses nothing", screen_crop_utils.find_flat_side_bands(full) == (0, 0),
                  str(screen_crop_utils.find_flat_side_bands(full)))


def _draw_share_under_cameras(listing: int) -> numpy.ndarray:
    """A call on the web cut below its row of cameras, 640 x 400: the share starts at the screen's top, from x 30 to 470
    and down to 360, on the call's flat grey stage, its header bar dark, its list of lines drawn per `listing`; the
    call's settings panel open on the right, and the name label under the share."""

    screen = numpy.full((400, 640), 232, numpy.uint8)        # the call's stage
    screen[:, 27:30] = 244                                  # the share's light border and its dark line
    screen[:360, 29] = 195
    screen[:360, 30:470] = 252                              # the share
    screen[:14, 30:470] = 40                                # its header bar
    for number, length in enumerate(numpy.random.default_rng(listing).integers(60, 380, 20)):
        screen[30 + number * 16:36 + number * 16, 50:50 + min(length, 400)] = 70
    screen[:, 480:] = 255                                   # the call's settings panel and its lines of text
    screen[40:330:30, 500:620] = 110
    screen[370:386, 36:110] = 90                            # the name label under the share
    screen[375:381, 40:106] = 240

    return screen


def test_a_share_the_screen_box_starts_inside_is_cut_from_the_settings_panel_beside_it_defect():
    # A share under a call's row of cameras has no top edge in the screen box, and the call's settings panel open beside
    # it and the name label below were kept. A share whose left edge runs down from the screen's top on the stage is cut
    call = [_draw_share_under_cameras(listing) for listing in range(4)]
    cut = screen_crop_utils.find_framed_share(call[0], call[1:])
    harness.check("cut at the share: nothing off the top, the panel and the label left out",
                  cut is not None and cut[0] == 0 and 36 <= cut[1] <= 44 and 28 <= cut[2] <= 32 and 166 <= cut[3] <= 172,
                  str(cut))
    shifted = [numpy.where(numpy.arange(640)[None, :] < 30, 232, screen).astype(numpy.uint8) for screen in call]
    for screen in shifted:
        screen[:, :30] = numpy.random.default_rng(9).integers(0, 255, (400, 30))   # a file tree, no stage, on the left
    harness.check("with no flat stage on its left, nothing is cut by it",
                  screen_crop_utils.find_framed_share(shifted[0], shifted[1:]) is None,
                  str(screen_crop_utils.find_framed_share(shifted[0], shifted[1:])))


def test_a_covered_camera_tile_does_not_make_another_view_read_as_the_same_defect():
    # A lecture's terminal frame kept its menu bar: a room view beside it matched it on the camera tile both had covered
    # with one colour, passed as the same view at 50% changed, and its top rows, unlike the terminal's, undid the bar
    screen = numpy.full((100, 200), 60, numpy.uint8)
    other = screen.copy()
    other[:, :100] = 160                                     # another view on half the pixels
    other[:, 100:150] = screen[:, 100:150] = 128             # both covered on the same quarter with one grey
    covered = numpy.zeros(screen.shape, bool)
    covered[:, 100:150] = True
    neighbours = [other, other.copy(), other.copy()]
    harness.check("counted whole, the other view passes as the same view",
                  screen_crop_utils.count_still_edges(screen, neighbours) is not None)
    harness.check("with the covered pixels left out, it is another view and nothing is counted",
                  screen_crop_utils.count_still_edges(screen, neighbours, covered=[covered] * 3) is None)


def test_a_screen_s_own_menu_bar_is_cut_by_its_words_and_a_slide_header_is_not_defect():
    # A lecture's macOS menu bar and window title stayed on 48 of 176 frames: the still band under them ran on into a
    # terminal's still lines and ended on no edge. Three menu words on one line in the top band now cut the bar
    screen = numpy.full((720, 1280), 30, numpy.uint8)
    screen[:14] = 200                                       # the menu bar
    screen[14:36] = 120                                     # the window's title bar
    menu = [{"text": text, "box": [70 + index * 80, 3, 120 + index * 80, 12], "line": 1}
            for index, text in enumerate(("iTerm2", "Shell", "Edit", "View", "Window", "Help"))]
    harness.check("the menu bar and the title bar under it are cut at the title bar's edge",
                  35 <= screen_crop_utils.find_menu_bar_cut(screen, menu) <= 38,
                  str(screen_crop_utils.find_menu_bar_cut(screen, menu)))
    header = [{"text": text, "box": [70 + index * 120, 10, 160 + index * 120, 30], "line": 1}
              for index, text in enumerate(("Refund", "rules", "for", "AcmeSync"))]
    harness.check("a slide's header is not cut", screen_crop_utils.find_menu_bar_cut(screen, header) == 0)
    lower = [{**word, "box": [word["box"][0], 300, word["box"][2], 310]} for word in menu]
    harness.check("menu words in the middle of the screen are not cut", screen_crop_utils.find_menu_bar_cut(screen, lower) == 0)


def test_a_share_s_own_margin_of_its_grey_is_no_stage_defect():
    # A dark slide's flat lower margin, under panels whose borders crossed most of its width, was cut as a call's stage.
    # Now a band is cut only when its grey stands off the share's own background, read on its outer columns
    slide = numpy.full((400, 500), 30, dtype=numpy.uint8)
    slide[60:330, 20:480] = 55
    slide[80:320, 40:460] = 200
    box = (0, 0, 500, 400)
    harness.check("the slide's own dark margin stays",
                  screen_crop_utils._trim_stage(slide, box, lambda band: []) == box)
    staged = numpy.full((400, 500), 120, dtype=numpy.uint8)
    staged[60:340, :] = 235
    staged[100:300, 40:460] = 20
    harness.check("a call's grey stage above and below is cut",
                  screen_crop_utils._trim_stage(staged, box, lambda band: []) == (0, 60, 500, 340),
                  str(screen_crop_utils._trim_stage(staged, box, lambda band: [])))


def test_a_light_call_stage_near_a_white_board_s_grey_is_still_cut_defect():
    # 350's grey rule kept a call's light stage (242) above and below a white board (245 at its sides), and 42 frames
    # of a board recording kept both bands. Now two bands, a letterbox, are a stage from 2 levels off
    board = numpy.full((400, 500), 242, dtype=numpy.uint8)
    board[45:355, :] = 245
    board[45, :] = board[354, :] = 200                      # the share's outline, where each band ends
    board[120:280, 60:440] = 40
    box = (0, 0, 500, 400)
    harness.check("both bands of the light stage are cut", screen_crop_utils._trim_stage(board, box, lambda band: [])
                  == (0, 45, 500, 355), str(screen_crop_utils._trim_stage(board, box, lambda band: [])))
