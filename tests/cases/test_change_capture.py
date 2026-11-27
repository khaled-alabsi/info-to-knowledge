"""Phase 2's `change` mode: crop every second to the shared screen, and take a frame only when that screen changed and
then held still. Tested as plain code against frames and a video whose content is known, with a stand-in face finder
where a real face is needed, through `face_detection_service.set_face_finder`, that service's own seam.
"""

import tempfile
from pathlib import Path

import cv2
import numpy

from support import harness
from support import media_fixtures

import config
from models.video_errors import MissingFaceModel
from models.video_errors import MissingPersonModel
from services import face_detection_service
from services import frame_extraction_service
from services import person_detection_service
from utils import change_capture_utils
from utils import screen_crop_utils

SLIDE_TOPS = (20, 60, 100, 140, 180)


def _draw_slide(width: int,
                height: int) -> numpy.ndarray:
    slide = numpy.full((height, width), 230, numpy.uint8)
    for top in SLIDE_TOPS:
        slide[top:top + 8, 20:width - 20] = 40

    return slide


def _build_packed_panel_frame(face_count: int) -> tuple[numpy.ndarray, list[tuple[int, int, int, int]]]:
    """A 400 x 270 frame: a slide over x 0..300, and three camera tiles packed against it over x 300..400, no stage
    between them. `face_count` of the tiles show a face the finder reports."""

    frame = numpy.zeros((270, 400), numpy.uint8)
    frame[:, :300] = _draw_slide(300, 270)
    frame[:, 300:] = 100
    faces = [(330, 20 + 90 * row, 370, 70 + 90 * row) for row in range(face_count)]

    return frame, faces


def test_the_stage_is_pure_black_touching_the_edge_and_a_dark_app_is_not():
    frame = numpy.zeros((270, 480), numpy.uint8)
    frame[20:245, 0:400] = 12                      # a dark editor: darker than any slide, but not the stage
    frame[60:70, 30:370] = 200
    box = screen_crop_utils.find_screen_box(frame, [])
    harness.check("a dark app is the screen, the black above and below it is not", box == (0, 20, 400, 245), str(box))


def test_a_slide_letterboxed_by_more_than_a_third_is_still_the_screen():
    """A 16:9 slide shown in a narrower stage had black bands above and below it making each of its columns 35.2%
    stage, over the 35% that marks a panel column; every slide of a meeting was dropped and only the opening card was
    taken (2026-10-06). A screen column's content is one unbroken run; a panel's tiles are separate runs."""

    frame = numpy.zeros((300, 480), numpy.uint8)
    frame[54:246, 0:360] = _draw_slide(360, 192)            # 108 of 300 rows are stage in every slide column: 36%
    for row in range(3):                                    # camera tiles to the right, on the stage between them
        frame[20 + 95 * row:80 + 95 * row, 390:470] = 100
    box = screen_crop_utils.find_screen_box(frame, [])
    harness.check("the slide is the screen, the tiles beside it are not", box == (0, 54, 360, 246), str(box))


def test_a_letterboxed_slide_keeps_columns_where_its_own_black_touches_the_stage():
    """A slide whose photo ends in a few near-black rows at its right edge: those pixels touch the stage, join it, and
    tipped the slide's columns past 35% stage with their content in more than one run. The crop's right edge then
    jumped between 1400 and 1461 px on every look, cut through the slide and took each jump as a new frame. A screen
    column's content may hold a little stage inside its span; a panel's tiles hold far more between them."""

    frame = numpy.zeros((300, 480), numpy.uint8)
    frame[54:246, 0:360] = _draw_slide(360, 192)
    frame[200:211, 320:360] = 0                             # the photo's near-black corner, on the slide's right edge
    for row in range(3):
        frame[20 + 95 * row:80 + 95 * row, 390:470] = 100
    box = screen_crop_utils.find_screen_box(frame, [])
    harness.check("the whole slide is the screen, its black corner included", box == (0, 54, 360, 246), str(box))


def test_a_column_of_faces_packed_against_the_screen_is_cut_at_its_edge():
    frame, faces = _build_packed_panel_frame(face_count=3)
    box = screen_crop_utils.find_screen_box(frame, faces)
    harness.check("the cut lands on the tiles' edge, within 4 px", box is not None and 296 <= box[2] <= 300, str(box))


def test_one_face_inside_the_content_is_never_cut():
    frame, faces = _build_packed_panel_frame(face_count=1)
    box = screen_crop_utils.find_screen_box(frame, faces)
    harness.check("one face is a photo, not a panel: nothing is cut", box == (0, 0, 400, 270), str(box))


def test_a_gallery_of_faces_is_no_screen():
    gray = cv2.cvtColor(media_fixtures.draw_meeting_frame("gallery", numpy.random.default_rng(1)), cv2.COLOR_BGR2GRAY)
    box = screen_crop_utils.find_screen_box(gray, media_fixtures.GALLERY_FACES)
    harness.check("a gallery of faces with nothing shared has no screen", box is None, str(box))


def test_a_box_that_wobbles_by_a_few_pixels_is_kept_and_a_new_one_waits_two_checks():
    current = (0, 68, 1673, 1011)
    box, pending = screen_crop_utils.choose_crop_box(current, None, (0, 69, 1676, 1010), 1920)
    harness.check("a box 3 px off is the same box", box == current and pending is None, f"{box} {pending}")
    moved = (0, 0, 1920, 1080)
    box, pending = screen_crop_utils.choose_crop_box(current, None, moved, 1920)
    harness.check("a different box is not taken on its first sighting", box == current and pending is not None,
                  f"{box} {pending}")
    box, pending = screen_crop_utils.choose_crop_box(box, pending, moved, 1920)
    harness.check("it is taken on the second check in a row", box == moved and pending is None, f"{box} {pending}")
    box, pending = screen_crop_utils.choose_crop_box(None, None, moved, 1920)
    harness.check("the first box found is taken at once", box == moved, str(box))


def test_a_box_widened_only_on_its_panel_side_waits_much_longer():
    """The camera panel cut off the right side and not seen on a few checks: the whole window found twice in a row
    became the box, and frames were taken with the cameras on them."""

    current, widened = (0, 66, 2891, 2234), (0, 66, 3456, 2234)
    box, pending = current, None
    for _ in range(screen_crop_utils.PANEL_RETURN_CHECKS - 1):
        box, pending = screen_crop_utils.choose_crop_box(box, pending, widened, 3456)
    harness.check("the panel's side is not widened before PANEL_RETURN_CHECKS checks", box == current, str(box))
    box, pending = screen_crop_utils.choose_crop_box(box, pending, widened, 3456)
    harness.check("it is widened once the panel stays away that long", box == widened, str(box))
    box, pending = screen_crop_utils.choose_crop_box(current, None, (0, 66, 3456, 1900), 3456)
    box, pending = screen_crop_utils.choose_crop_box(box, pending, (0, 66, 3456, 1900), 3456)
    harness.check("a box that also moves another side waits two checks as before", box == (0, 66, 3456, 1900), str(box))


def test_block_changes_count_what_moved_and_ignore_blocks_that_never_stop():
    first = numpy.full((180, 320), 200, numpy.uint8)
    second = first.copy()
    second[0:10, 0:10] = 0
    changed = change_capture_utils.map_changed_blocks(first, second)
    harness.check("one changed block of 576", int(changed.sum()) == 1, str(int(changed.sum())))
    nothing = numpy.zeros_like(changed)
    harness.check("its share is 1 in 576", abs(change_capture_utils.compute_change(changed, nothing) - 1 / 576) < 1e-9)
    harness.check("an ignored block does not count", change_capture_utils.compute_change(changed, changed) == 0.0)
    busy = [changed] * 12
    harness.check("a block changing in every recent second is ignored",
                  bool(change_capture_utils.find_active_blocks(busy)[0, 0]))


def _draw_text_slide(words: str) -> numpy.ndarray:
    slide = numpy.full((360, 640), 235, numpy.uint8)
    for row, line in enumerate(("REFUNDS by order", f"a = 2   temp = 100.4   {words}", "b = -0.3   go = True",
                                "small = 0.001   n = 17")):
        cv2.putText(slide, line, (40, 70 + 60 * row), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 30, 2, cv2.LINE_AA)

    return slide


def test_a_screen_box_moved_by_a_pixel_or_two_is_no_change_and_a_new_word_still_is():
    """One slide cut with two boxes a pixel or two apart, (2, 0) and (0, 1) on a 640 x 360 frame, was taken again as a
    new slide (lecture-1 frames 125, 131, 144, 2026-10-14): every edge of its text moved. Within one box the
    comparison stays exact."""

    slide = _draw_text_slide("x = 123")
    first_box, moved_box = (2, 0, 640, 360), (0, 1, 640, 360)
    first = change_capture_utils.shrink_screen(slide, first_box)
    moved = change_capture_utils.shrink_screen(slide, moved_box)
    shift = change_capture_utils.choose_shift_tolerance(moved_box, first_box)
    nothing = numpy.zeros((change_capture_utils.BLOCK_ROWS, change_capture_utils.BLOCK_COLUMNS), bool)
    exact = change_capture_utils.compute_change(change_capture_utils.map_changed_blocks(first, moved), nothing)
    harness.check("compared exactly, the moved box changes blocks past a new slide's 5%", exact > 0.05, f"{exact:.4f}")
    shifted = change_capture_utils.compute_change(change_capture_utils.map_changed_blocks(moved, first, shift), nothing)
    harness.check("the same slide under a box moved by a pixel or two is no new slide", shifted < 0.05,
                  f"shift {shift}: {shifted:.4f}")
    harness.check("two samples of one box are compared exactly",
                  change_capture_utils.choose_shift_tolerance(first_box, list(first_box)) == 0)
    edited = change_capture_utils.shrink_screen(_draw_text_slide("x = 456"), moved_box)
    share = change_capture_utils.compute_change(change_capture_utils.map_changed_blocks(first, edited, shift), nothing)
    harness.check("one value changed on it still changes blocks past a still second",
                  share > change_capture_utils.STILL_LIMIT, f"{share:.4f}")


def _list_scene_starts() -> list[int]:
    """The second each scene of the fixture meeting starts at, in order."""

    starts, second = [], 0
    for _, seconds in media_fixtures.MEETING_TIMELINE:
        starts.append(second)
        second += seconds

    return starts


def test_change_mode_takes_each_slide_once_marks_the_return_and_skips_the_gallery():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_meeting_video(folder)
    with media_fixtures.face_finder(media_fixtures.find_gallery_faces):
        settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                        box_every_seconds=1, max_width=0)
        reports: list[tuple[float, float]] = []
        frames = frame_extraction_service.extract_frames(video, folder / "frames", settings,
                                                         lambda done, total: reports.append((done, total)))
    items = frames["items"]
    seconds = [round(item["timestamp"]) for item in items]
    harness.check("the sampling reports how far it is every 10 seconds of video, of the whole length",
                  reports and [done for done, _ in reports] == [10.0 * n for n in range(1, len(reports) + 1)]
                  and all(total == frames["video"]["durationS"] for _, total in reports), str(reports))
    harness.check("slide A, slide B, slide A again: three frames, none from the gallery", len(items) == 3,
                  str([(item["slug"], item["timestamp"], item.get("repeatOf")) for item in items]))
    if len(items) != 3:
        return
    starts = _list_scene_starts()
    harness.check("each slide is taken within 2 s of appearing",
                  all(start <= second <= start + 2 for start, second in zip(starts, seconds)), f"{seconds} {starts}")
    harness.check("the return to slide A is marked as a repeat of the first frame",
                  items[2].get("repeatOf") == "frame-000" and items[1].get("repeatOf") is None, str(items))
    harness.check("every frame carries the shared screen's box",
                  all(tuple(item["box"]) == media_fixtures.MEETING_SCREEN for item in items), str(items[0].get("box")))
    frame = cv2.imread(str(folder / "frames" / Path(items[0]["path"]).name))
    x0, y0, x1, y1 = media_fixtures.MEETING_SCREEN
    harness.check(f"the written frame is the shared screen alone, {x1 - x0} x {y1 - y0}",
                  frame is not None and frame.shape[:2] == (y1 - y0, x1 - x0), str(None if frame is None else frame.shape))


def test_max_width_scales_a_wider_frame_down_to_it():
    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_meeting_video(folder)
    with media_fixtures.face_finder(media_fixtures.find_gallery_faces):
        narrow = frame_extraction_service.extract_frames(video, folder / "narrow",
                                                         config.FrameSettings(mode="change", box_every_seconds=1,
                                                                              max_width=200))
    narrow_frame = cv2.imread(str(folder / "narrow" / Path(narrow["items"][0]["path"]).name))
    harness.check("max_width scales a wider frame down to it",
                  narrow_frame is not None and narrow_frame.shape[1] == 200,
                  str(None if narrow_frame is None else narrow_frame.shape))


def test_a_gallery_shown_between_two_box_checks_is_not_taken():
    """The box is looked for every few seconds; a speaker view that replaced the screen between two checks kept the
    last box and was taken. Each frame's own moment is checked before it is taken."""

    folder = Path(tempfile.mkdtemp())
    video = media_fixtures.build_meeting_video(folder)
    with media_fixtures.face_finder(media_fixtures.find_gallery_faces):
        settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                        box_every_seconds=60, max_width=0)
        frames = frame_extraction_service.extract_frames(video, folder / "frames", settings)
    seconds = [round(item["timestamp"]) for item in frames["items"]]
    gallery_start = _list_scene_starts()[-1]
    harness.check(f"with the box checked once a minute, the gallery from {gallery_start} s on is still not taken",
                  seconds and max(seconds) < gallery_start, str(seconds))


def test_a_missing_face_model_is_refused_by_name():
    previous_model = config.settings.frames.face_model
    with media_fixtures.face_finder(face_detection_service.find_faces_with_yunet):
        config.settings.frames.face_model = "weights/no-such-model.onnx"
        try:
            face_detection_service.detect_faces(numpy.zeros((100, 100), numpy.uint8))
        except MissingFaceModel as exc:
            harness.check("the refusal names the missing file", "no-such-model.onnx" in str(exc), str(exc))
        else:
            harness.check("a missing face model raises", False)
        finally:
            config.settings.frames.face_model = previous_model


def test_the_shipped_face_model_loads_and_finds_nothing_on_a_blank_frame():
    with media_fixtures.face_finder(face_detection_service.find_faces_with_yunet):
        faces = face_detection_service.detect_faces(numpy.full((360, 640), 128, numpy.uint8))
    harness.check("a blank frame has no face", faces == [], str(faces))


def test_the_change_settings_bind_from_the_local_file_s_camel_case_keys():
    settings = config.Settings(frames={"mode": "change", "changeToCapture": 0.1, "stillSeconds": 2, "busySeconds": 20,
                                       "boxEverySeconds": 3, "maxWidth": 960, "faceModel": "elsewhere.onnx"})
    frames = settings.frames
    harness.check("every change key binds", (frames.selection_mode, frames.change_to_capture, frames.still_seconds,
                                              frames.busy_seconds, frames.box_every_seconds, frames.max_width,
                                              frames.face_model)
                  == ("change", 0.1, 2, 20, 3, 960, "elsewhere.onnx"), str(frames))


def test_a_grid_of_large_faces_filling_the_stage_is_no_screen_and_small_faces_are_content():
    frame = numpy.zeros((270, 480), numpy.uint8)
    faces = []
    for row in range(3):
        for column in range(3):
            frame[row * 90:(row + 1) * 90, column * 160:(column + 1) * 160] = 60 + 20 * (3 * row + column)
            faces.append((column * 160 + 60, row * 90 + 25, column * 160 + 100, row * 90 + 65))
    gallery_box = screen_crop_utils.find_screen_box(frame, faces)
    harness.check("a 3 x 3 gallery of camera tiles filling the stage, nothing shared: no screen",
                  gallery_box is None, str(gallery_box))
    slide = numpy.zeros((270, 480), numpy.uint8)
    slide[20:250, 20:460] = _draw_slide(440, 230)
    small = [(100, 150, 112, 164), (300, 150, 312, 164)]
    slide_box = screen_crop_utils.find_screen_box(slide, small)
    harness.check("two small faces in a photo on a slide: the slide stays", slide_box == (20, 20, 460, 250),
                  str(slide_box))


def test_a_crop_of_one_flat_colour_is_blank_and_a_slide_with_text_is_not():
    black = numpy.zeros((72, 128), dtype=numpy.uint8)
    harness.check("a crop of pure stage black shows nothing", screen_crop_utils.is_blank(black))
    title_card = numpy.full((72, 128), 61, dtype=numpy.uint8)
    title_card[::7, ::5] += 3                                          # compression noise on a plain red card
    harness.check("a plain coloured card shows nothing", screen_crop_utils.is_blank(title_card))
    dark_slide = numpy.full((72, 128), 6, dtype=numpy.uint8)
    dark_slide[30:34, 20:60] = 180                                     # a title on a dark slide
    harness.check("a dark slide with a title is content", not screen_crop_utils.is_blank(dark_slide))
    mostly_black = black.copy()
    mostly_black[30:42, 20:108] = 200                                  # one line of text on black
    harness.check("one line of text on black is content", not screen_crop_utils.is_blank(mostly_black))


def test_one_large_face_in_the_middle_is_a_lone_camera():
    """A speaker view, one person filling the screen with no slide, was taken as a frame (2026-10-02): the gallery rule
    needs two faces. One face in the middle 60%, covering enough of the screen, is a lone camera."""

    box = (0, 0, 1280, 720)
    harness.check("one large face in the middle is a lone camera",
                  screen_crop_utils.is_lone_camera(box, [(520, 260, 760, 520)]))
    harness.check("a small face in the middle of a big screen, as on a slide's photo, is still one when large enough",
                  screen_crop_utils.is_lone_camera(box, [(560, 430, 720, 600)]))
    harness.check("a small face in a corner, a floating camera beside content, is none",
                  not screen_crop_utils.is_lone_camera(box, [(1150, 20, 1250, 120)]))
    harness.check("a tiny face is none", not screen_crop_utils.is_lone_camera(box, [(630, 350, 660, 380)]))
    harness.check("two faces are the gallery rule's, not this one",
                  not screen_crop_utils.is_lone_camera(box, [(200, 200, 400, 400), (800, 200, 1000, 400)]))
    harness.check("a large face with a self-view tile in the corner is a lone camera",
                  screen_crop_utils.is_lone_camera(box, [(560, 430, 720, 600), (1170, 640, 1200, 680)]))
    harness.check("a large face with a face a third of its size beside it is none",
                  not screen_crop_utils.is_lone_camera(box, [(560, 430, 720, 600), (900, 430, 1000, 530)]))


def _build_word(y0: int,
                y1: int) -> dict:
    """A word the text reader found, between rows `y0` and `y1`."""

    return {"text": "Kamera", "box": [100, y0, 180, y1]}


def test_a_window_s_title_and_button_bars_are_not_words_that_make_a_speaker_view_a_slide():
    """A call window's buttons (camera, microphone, share, leave) read as 16 words over a speaker view, which was kept
    as a slide: words in the screen's top 9% and bottom 8% are window bars and do not count."""

    bar = [_build_word(20, 40) for _ in range(12)] + [_build_word(950, 980) for _ in range(5)]
    harness.check("a top bar and a bottom bar of a 1000-high screen count nothing",
                  screen_crop_utils.count_content_words(bar, 1000) == 0)
    harness.check("words between them count",
                  screen_crop_utils.count_content_words(bar + [_build_word(300, 330), _build_word(800, 830)], 1000) == 2)


def test_the_face_column_is_its_largest_stack_whatever_other_faces_lie_beside_it():
    """A face in a slide's picture, and a lone avatar in a corner, both moved the panel's inner side, and the column of
    cameras was never cut (2026-10-02)."""

    column = [(1775, 60 + 150 * row, 1820, 110 + 150 * row) for row in range(5)]
    picture = (1302, 514, 1436, 675)
    avatar = (1853, 843, 1872, 868)
    harness.check("the slide's picture face is not part of the column",
                  sorted(screen_crop_utils._keep_outer_stack(column + [picture], is_right=True)) == sorted(column))
    harness.check("a lone avatar nearer the side does not replace the column",
                  sorted(screen_crop_utils._keep_outer_stack(column + [avatar], is_right=True)) == sorted(column))


def test_a_new_box_seen_once_is_checked_again_the_next_second():
    card, screen = (0, 0, 1920, 1080), (0, 69, 1676, 1010)
    box, pending, switched_at = None, None, None
    for second in range(12):
        if screen_crop_utils.is_box_check_due(second, 5, pending is not None):
            box, pending = screen_crop_utils.choose_crop_box(box, pending, card if second < 4 else screen, 1920)
        if box == screen and switched_at is None:
            switched_at = second
    harness.check("an opening card's box gives way to the screen one second after the screen is first seen",
                  switched_at == 6, str(switched_at))
    harness.check("without a waiting box the check stays every few seconds",
                  [second for second in range(12) if screen_crop_utils.is_box_check_due(second, 5, False)] == [0, 5, 10])


# A 60 s recording at one frame a second, each frame at its own second
_ONE_FRAME_A_SECOND = {"fps": 1.0, "frameCount": 60, "durationS": 60.0, "frameTimes": [float(second) for second in range(60)]}


def test_a_repeat_flicked_past_is_dropped_and_a_return_is_kept():
    # Frames taken at 0, 10, 12, 14 and 40 s of a 60 s recording at 1 fps: 12 s repeats 0 s for 2 s (a flick), 14 s
    # repeats the flick for 26 s (a return, to the first screen), 40 s repeats 10 s for 20 s (a return)
    taken = [{"frameNumber": second, "repeatIndex": repeat} for second, repeat in
             ((0, None), (10, None), (12, 0), (14, 2), (40, 1))]
    kept = frame_extraction_service._drop_pass_throughs(taken, _ONE_FRAME_A_SECOND, 5.0)
    harness.check("the 2 s flick is dropped, the returns stay", [frame["frameNumber"] for frame in kept] == [0, 10, 14, 40],
                  str(kept))
    harness.check("a return to a dropped flick repeats what the flick repeated, renumbered",
                  [frame["repeatIndex"] for frame in kept] == [None, None, 0, 1], str(kept))
    harness.check("0 keeps every repeat",
                  len(frame_extraction_service._drop_pass_throughs(taken, _ONE_FRAME_A_SECOND, 0)) == 5)


def test_a_title_slide_that_takes_the_slide_s_content_away_is_no_build_step():
    """A section title replaced a slide of two dark code panes: the panes sat 22 grey levels off the background, so
    only their text counted as changed (14.4%), and the title was marked a step of the code slide (38c3-fault-injection
    frames 30 and 31, 2026-10-15)."""

    code = numpy.full((change_capture_utils.SAMPLE_HEIGHT, change_capture_utils.SAMPLE_WIDTH), 48, numpy.uint8)
    code[10:22, 20:160] = 220                                  # the title
    code[50:150, 5:155] = 26                                   # two dark panes
    code[50:150, 165:315] = 26
    for row in range(55, 145, 8):                              # thin code lines in them
        code[row:row + 2, 10:120] = 120
        code[row:row + 2, 170:290] = 120
    title = numpy.full_like(code, 48)
    title[80:95, 90:230] = 230                                 # one section title in the middle
    harness.check("most of the code slide's content is gone",
                  change_capture_utils.compute_content_gone(code, title) >= change_capture_utils.BUILD_CONTENT_GONE_LIMIT,
                  str(change_capture_utils.compute_content_gone(code, title)))
    gained = code.copy()
    gained[155:170, 20:200] = 200                              # a line added under the panes
    harness.check("a line added keeps all of it", change_capture_utils.compute_content_gone(code, gained) == 0.0)
    taken = [{"frameNumber": 0, "change": 1.0, "repeatIndex": None, "afterFrame": None},
             {"frameNumber": 10, "change": 0.144, "repeatIndex": None, "afterFrame": 0, "contentGone": 0.902},
             {"frameNumber": 20, "change": 0.10, "repeatIndex": None, "afterFrame": 10, "contentGone": 0.0}]
    harness.check("under the change limit, the slide taken away is no step, and the next small change is a step of it",
                  [frame["stepIndex"] for frame in frame_extraction_service._mark_build_steps(taken, 0.15)]
                  == [None, None, 1], str(frame_extraction_service._mark_build_steps(taken, 0.15)))


def test_a_slide_gaining_a_large_picture_on_its_free_space_is_a_build_step_and_a_replaced_screen_is_not():
    slide = numpy.full((change_capture_utils.SAMPLE_HEIGHT, change_capture_utils.SAMPLE_WIDTH), 255, numpy.uint8)
    slide[20:30, 20:300] = 0                                  # a title line
    slide[60:160, 20:140] = 90                                # a picture on the left
    slide[64:156, 24:136] = 160
    gained = slide.copy()
    gained[60:160, 180:300] = 60                              # a second picture on the empty right, about a quarter
    gained[64:156, 184:296] = 200
    replaced = slide.copy()
    replaced[60:160, 20:140] = 30                             # the left picture swapped for another
    harness.check("a large picture placed on empty space only adds to the slide",
                  change_capture_utils.is_addition(slide, gained))
    harness.check("a picture swapped for another replaces what was there",
                  not change_capture_utils.is_addition(slide, replaced))
    taken = [{"frameNumber": 0, "change": 1.0, "repeatIndex": None, "afterFrame": None},
             {"frameNumber": 10, "change": 0.25, "repeatIndex": None, "afterFrame": 0, "isAddition": True},
             {"frameNumber": 20, "change": 0.25, "repeatIndex": None, "afterFrame": 10, "isAddition": False}]
    harness.check("over the change limit, an addition is still a step and a replacement is not",
                  [frame["stepIndex"] for frame in frame_extraction_service._mark_build_steps(taken, 0.15)]
                  == [None, 0, None])


def test_a_slide_held_three_minutes_is_taken_again_as_a_repeat_not_a_step_defect():
    # Defect: a slide held 3 minutes and taken again (the re-take that splits its speech) was marked a build step of
    # itself, as an overlay taken off again is (DEFECTS 471). Fix: a held re-take stays a repeat of the frame before
    taken = [{"frameNumber": 0, "change": 1.0, "repeatIndex": None, "afterFrame": None},
             {"frameNumber": 5400, "change": 0.0, "repeatIndex": 0, "afterFrame": 0, "isHeldRetake": True}]
    marked = frame_extraction_service._mark_build_steps(taken, 0.15)
    harness.check("the held re-take is a repeat of the frame before, no step",
                  (marked[1]["stepIndex"], marked[1]["repeatIndex"]) == (None, 0), str(marked[1]))
    overlay = [taken[0], {**taken[1], "isHeldRetake": False}]
    harness.check("the same frame taken for another reason is still a step of the build, as before",
                  frame_extraction_service._mark_build_steps(overlay, 0.15)[1]["stepIndex"] == 0)


def test_frames_that_follow_each_other_with_a_small_change_are_steps_of_one_build():
    # 0 starts; 1 and 2 follow it with 8% and 6% changes (a slide gaining a box, then an arrow); 3 changes 60% (a new
    # slide); 4 changes 7% but its frame before was a dropped pass-through, so it does not follow 3; 5 is a repeat
    taken = [{"frameNumber": 0, "change": 1.0, "repeatIndex": None, "afterFrame": None},
             {"frameNumber": 10, "change": 0.08, "repeatIndex": None, "afterFrame": 0},
             {"frameNumber": 20, "change": 0.06, "repeatIndex": None, "afterFrame": 10},
             {"frameNumber": 30, "change": 0.60, "repeatIndex": None, "afterFrame": 20},
             {"frameNumber": 40, "change": 0.07, "repeatIndex": None, "afterFrame": 35},
             {"frameNumber": 50, "change": 0.05, "repeatIndex": 0, "afterFrame": 40}]
    marked = frame_extraction_service._mark_build_steps(taken, 0.10)
    harness.check("1 and 2 are steps of the build 0 starts; 3, 4 and the repeat 5 are not",
                  [frame["stepIndex"] for frame in marked] == [None, 0, 0, None, None, None], str(marked))
    harness.check("0 marks no step", all(frame["stepIndex"] is None
                                         for frame in frame_extraction_service._mark_build_steps(taken, 0)))
    # 0 starts; 1 adds an overlay (10%); 2 takes it off again, a repeat of 0 right after: a step of the build, not a return
    overlay = [{"frameNumber": 0, "change": 1.0, "repeatIndex": None, "afterFrame": None},
               {"frameNumber": 10, "change": 0.10, "repeatIndex": None, "afterFrame": 0},
               {"frameNumber": 20, "change": 0.10, "repeatIndex": 0, "afterFrame": 10},
               {"frameNumber": 30, "change": 0.10, "repeatIndex": 1, "afterFrame": 20}]
    marked = frame_extraction_service._mark_build_steps(overlay, 0.12)
    harness.check("a repeat of any frame of the build right after it is a step of it, and no longer a return",
                  [(frame["stepIndex"], frame["repeatIndex"]) for frame in marked] == [(None, None), (0, None), (0, None), (0, None)],
                  str(marked))


def test_a_slide_panel_a_pixel_over_the_screen_box_is_still_taken():
    """A broadcast's screen box started at x 1 and its slide at x 0, and the slide was refused on every such second, so
    8 of 55 frames of one talk kept the speaker's camera (2026-10-12)."""

    harness.check("a panel one pixel over the box's edge is cut to the box",
                  screen_crop_utils.clip_panel_to_box((0, 0, 545, 438), (1, 0, 720, 576), 720) == (1, 0, 545, 438))
    harness.check("a panel well outside the box is refused",
                  screen_crop_utils.clip_panel_to_box((0, 0, 545, 438), (40, 0, 720, 576), 720) is None)
    harness.check("a panel as wide as the box is no broadcast's slide: a call's share beside its faces column (DEFECTS 97)",
                  screen_crop_utils.clip_panel_to_box((0, 97, 1674, 1011), (0, 67, 1674, 1012), 1920) is None)


def _draw_board(first_row: int) -> numpy.ndarray:
    """A board page, 512 x 400 (half of it compared, so a scroll is a whole number of pixels there too): a still header,
    then a list of rows of varying lengths starting at row `first_row` of the list, as a page scrolled that far shows
    it."""

    page = numpy.full((400, 512), 250, numpy.uint8)
    page[:60] = 40                                          # the board's header and its title
    page[20:40, 20:200] = 230
    lengths = numpy.random.default_rng(7).integers(40, 460, 60)
    for slot, number in enumerate(range(first_row, first_row + 16)):
        top = 80 + slot * 20
        page[top:top + 10, 30:30 + lengths[number]] = 90    # the row's text, each its own length

    return page


def test_a_list_scrolled_under_a_still_header_is_the_frame_before_scrolled():
    """A board's list scrolled by a few rows was taken as a new screen,
    and its notes said "same as" the frame before."""

    board = _draw_board(0)
    other = numpy.full((400, 512), 250, numpy.uint8)
    other[:60] = 40
    for slot, length in enumerate(numpy.random.default_rng(99).integers(40, 460, 16)):
        other[80 + slot * 20:90 + slot * 20, 30:30 + length] = 90
    harness.check("scrolled by three rows, it is the frame before scrolled",
                  change_capture_utils.is_scroll_of(board, _draw_board(3)))
    harness.check("another list under the same header is not", not change_capture_utils.is_scroll_of(board, other))
    harness.check("nor the same frame", not change_capture_utils.is_scroll_of(board, board.copy()))
    held = [{"frameNumber": number, "afterFrame": number - 1, "repeatIndex": None, "change": 0.9,
             "jpeg": cv2.imencode(".jpg", image)[1].tobytes()} for number, image in ((1, board), (2, _draw_board(3)))]
    held[0]["afterFrame"] = None
    marked = frame_extraction_service._mark_build_steps(held, 0.15)
    harness.check("the scrolled frame is a step of the build the frame before started", marked[1]["stepIndex"] == 0,
                  str([frame["stepIndex"] for frame in marked]))


LECTURE_SIZE = (480, 270)                  # width, height
LECTURE_PERSON = (40, 110)                 # the walking person's width and height
LECTURE_FPS = 5


def _draw_lecture_frame(person_x: int,
                        is_second_board: bool) -> numpy.ndarray:
    """A board filling the frame, its lines of chalk known, and a dark person standing at `person_x`."""

    frame = numpy.full((LECTURE_SIZE[1], LECTURE_SIZE[0], 3), 220, numpy.uint8)
    for top in ((30, 70, 110) if not is_second_board else (50, 90, 130, 170)):
        frame[top:top + 8, 40:440] = 40
    width, height = LECTURE_PERSON
    frame[LECTURE_SIZE[1] - height:, person_x:person_x + width] = 70

    return frame


def _find_lecture_person(frame: numpy.ndarray) -> list[tuple[int, int, int, int]]:
    """A stand-in person finder for the lecture video: the columns of the person's grey along the bottom row."""

    columns = numpy.where(numpy.abs(frame[-1, :, 0].astype(int) - 70) < 12)[0]
    people = [] if not len(columns) else [(int(columns[0]), LECTURE_SIZE[1] - LECTURE_PERSON[1], int(columns[-1]) + 1,
                                           LECTURE_SIZE[1])]

    return people


def test_a_person_walking_before_an_unchanged_board_takes_no_frame_and_a_new_board_still_does():
    """DEFECTS.md 100: a room camera's lecture took a frame every 10 s while the lecturer walked before the same board.
    The person walks without stopping for 24 s; the board changes at 16 s."""

    folder = Path(tempfile.mkdtemp())
    video = folder / "lecture.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), LECTURE_FPS, LECTURE_SIZE)
    for index in range(24 * LECTURE_FPS):
        writer.write(_draw_lecture_frame(20 + (index * 37) % 400, index >= 16 * LECTURE_FPS))
    writer.release()
    settings = config.FrameSettings(mode="change", change_to_capture=0.05, still_seconds=1, busy_seconds=10,
                                    box_every_seconds=1, max_width=0, trim_bars=False)
    with media_fixtures.face_finder(lambda gray: []), media_fixtures.person_finder(_find_lecture_person):
        frames = frame_extraction_service.extract_frames(video, folder / "frames", settings)
    seconds = [round(item["timestamp"]) for item in frames["items"]]
    harness.check("the first board and the second: two frames, none for the walking alone",
                  len(seconds) == 2 and seconds[0] < 3 and 16 <= seconds[1] <= 18, str(seconds))


def test_a_missing_person_model_is_refused_by_name():
    previous_model = config.settings.frames.person_model
    with media_fixtures.person_finder(person_detection_service.find_people_with_nanodet):
        config.settings.frames.person_model = "weights/no-such-person-model.onnx"
        try:
            person_detection_service.detect_people(numpy.zeros((100, 100, 3), numpy.uint8))
        except MissingPersonModel as exc:
            harness.check("the refusal names the missing file", "no-such-person-model.onnx" in str(exc), str(exc))
        else:
            harness.check("a missing person model raises", False)
        finally:
            config.settings.frames.person_model = previous_model


def test_the_shipped_person_model_loads_and_finds_nobody_on_a_blank_frame():
    with media_fixtures.person_finder(person_detection_service.find_people_with_nanodet):
        people = person_detection_service.detect_people(numpy.full((270, 480, 3), 200, numpy.uint8))
    harness.check("no person on a blank frame", people == [], str(people))


def _draw_docs_page(scroll: int,
                    seed: int = 3) -> numpy.ndarray:
    """A documentation page in a browser, 1280 x 703: a still tab and address bar, a still menu on the left, a still
    camera tile on the right, and a content pane of words in lines scrolled `scroll` pixels down its text."""

    rng = numpy.random.default_rng(seed)
    text = numpy.full((2000, 760), 250, numpy.uint8)
    for top in range(0, 2000, 34):
        left = 0
        while left < 700:
            width = int(rng.integers(20, 90))
            text[top + 8:top + 22, left:min(left + width, 760)] = int(rng.integers(40, 120))
            left += width + int(rng.integers(8, 16))
    page = numpy.full((703, 1280), 250, numpy.uint8)
    page[:100] = 60                                         # tab bar, address bar, the page's header
    page[30:50, 300:700] = 200
    for top in range(140, 520, 26):                         # the menu, which does not scroll
        page[top:top + 12, 40:40 + 60 + (top % 90)] = 90
    page[100:703, 280:1040] = text[scroll:scroll + 603]
    page[260:460, 1050:1250] = 120                          # the presenter's camera, still
    page[300:420, 1100:1200] = 70

    return page


def test_a_page_scrolled_beside_a_still_menu_and_camera_is_the_frame_before_scrolled():
    """Four frames of one documentation page, scrolled 40 to 265 pixels each time beside its still menu, its still
    header and a still camera tile, were taken as new screens: compared whole, moved matched no better than unmoved,
    and each frame's notes said "same as" the first (Lecture 9, frames 30 to 33, blocked)."""

    page = _draw_docs_page(0)
    harness.check("scrolled 250 pixels, it is the frame before scrolled",
                  change_capture_utils.is_scroll_of(page, _draw_docs_page(250)))
    harness.check("scrolled 40 pixels too", change_capture_utils.is_scroll_of(page, _draw_docs_page(40)))
    harness.check("another page in the same browser, menu and camera is not",
                  not change_capture_utils.is_scroll_of(page, _draw_docs_page(0, seed=11)))
    harness.check("nor the same page", not change_capture_utils.is_scroll_of(page, page.copy()))


def test_the_frame_cap_keeps_a_repeat_of_a_dropped_repeat_tied_to_the_original():
    """A repeat can repeat another repeat; the cap dropped the middle one and the last became an original, although the
    first showing was still kept (the pass-through drop already followed the chain)."""

    taken = [{"frameNumber": 0, "repeatIndex": None, "change": 0.5},
             {"frameNumber": 10, "repeatIndex": 0, "change": 0.1},
             {"frameNumber": 20, "repeatIndex": 1, "change": 0.3}]
    kept = frame_extraction_service._keep_most_changed(taken, 2)
    harness.check("the original and the last repeat are kept, the repeat pointing at the original",
                  [(frame["frameNumber"], frame["repeatIndex"]) for frame in kept] == [(0, None), (20, 0)], str(kept))


def test_a_frame_of_static_noise_is_blank_and_a_dense_slide_is_not_defect():
    # Defect: a broadcast's full-screen static noise was taken as a frame and sent to phase 3, since only one flat colour
    # counted as blank. Fix: a crop with no flat patch at all and edges everywhere far stronger than any screen's is blank too.
    noise = numpy.random.default_rng(7).integers(0, 256, (360, 640), dtype=numpy.uint8)
    harness.check("static noise shows nothing", screen_crop_utils.is_blank(noise))
    dense = numpy.full((360, 640), 245, dtype=numpy.uint8)
    for row in range(40, 320, 12):                                     # a page of small text, line under line, in its margins
        for column in range(40, 600, 9):
            dense[row:row + 7, column:column + 6] = 20
    harness.check("a page dense with small text is content", not screen_crop_utils.is_blank(dense))


def test_a_cleared_terminal_is_empty_inside_and_a_one_word_title_slide_is_not_defect():
    # Defect: a cleared terminal, nothing on it but its prompt line and its status bar, was kept and sent to phase 3, since
    # its bars broke the one flat colour. Fix: a frame whose inside, between the chrome bands, is one flat colour is empty.
    terminal = numpy.full((720, 1280), 24, dtype=numpy.uint8)
    terminal[8:22, 900:1270:2] = 200                                   # the prompt's corner line, in the top band
    terminal[686:704, 0:1280:2] = 180                                  # the multiplexer's status bar, in the bottom band
    harness.check("a cleared terminal shows nothing inside", screen_crop_utils.is_empty_inside(terminal))
    harness.check("though the whole frame is no flat colour", not screen_crop_utils.is_blank(terminal))
    title = numpy.full((720, 1280), 232, dtype=numpy.uint8)
    title[330:400, 100:470:3] = 30                                     # one large word of a section title slide
    harness.check("a slide holding one title word is content", not screen_crop_utils.is_empty_inside(title))
    one_line = terminal.copy()
    one_line[80:94, 10:1110:2] = 200                                   # a terminal that printed one line
    harness.check("a terminal holding one line is content", not screen_crop_utils.is_empty_inside(one_line))


def test_a_terminal_with_its_lines_only_at_the_bottom_is_not_empty_defect():
    # Defect: a white terminal scrolled to its last lines, a command and its error in the bottom band and nothing above,
    # read as an empty screen and left phase 2. Fix: a frame is empty only when both chrome bands hold a bar.
    terminal = numpy.full((720, 1280), 250, dtype=numpy.uint8)
    terminal[668:680, 0:900:2] = 40                                    # a typed command, in the bottom band
    terminal[690:702, 0:1250:2] = 40                                   # its error line under it
    harness.check("lines standing only at the bottom are content", not screen_crop_utils.is_empty_inside(terminal))
    terminal[8:22, 900:1270:2] = 40                                    # a prompt's corner line in the top band too
    harness.check("with a bar above as well, an empty inside is empty", screen_crop_utils.is_empty_inside(terminal))


def test_a_broadcast_slide_keeps_its_title_band_in_another_colour_defect():
    # Defect: a broadcast's slide was cut to its white body: its dark title band, another grey, fell outside the panel
    # of one grey, and four slides lost their titles. Fix: a band of one grey above or below the panel, spanning exactly its columns and differing from what stands beside it, is the slide's.
    frame = numpy.zeros((576, 720), dtype=numpy.uint8)
    frame[:, :] = (numpy.arange(720) * 7 % 100 + 120).astype(numpy.uint8)       # a backdrop of light stripes
    frame[6:97, 4:544] = 41                                                   # the title band
    frame[40:56, 20:300:3] = 240                                              # its title's letters
    frame[97, 4:544] = 169                                                    # the slide's thin line
    frame[98:420, 4:544] = 252                                                # the white body
    banded = screen_crop_utils._add_slide_bands(frame, (4, 101, 544, 420))
    harness.check("the title band joins the slide", banded[1] <= 8 and banded[3] == 420, str(banded))
    stage = frame.copy()
    stage[:98, :] = 0                                                         # a black stage above and beside instead
    harness.check("a stage above the slide, the same beside it, is no band",
                  screen_crop_utils._add_slide_bands(stage, (4, 101, 544, 420))[1] == 101)


def test_a_broadcast_slide_of_many_tones_on_a_black_backdrop_is_cut_to_it_defect():
    # Defect: a talk's slides, photos behind their text, stood on a flat black backdrop beside the speaker's camera and
    # the stream's banner; no grey level filled them, and 20 frames kept the camera. Fix: the part no backdrop holds is the slide.
    across = numpy.linspace(30, 230, 544, dtype=numpy.float32)
    rows = numpy.stack([numpy.tile(across, (432, 1)), numpy.tile(across[::-1], (432, 1)),
                        numpy.tile(numpy.linspace(60, 200, 432, dtype=numpy.float32)[:, None], (1, 544))], axis=2).astype(numpy.uint8)
    frame = numpy.full((576, 720, 3), 17, dtype=numpy.uint8)
    frame[4:436, 173:717] = rows                                              # the slide, many tones
    frame[60:110, 10:150] = 240                                               # the stream's banner
    frame[240:560, 10:160] = 90                                               # the camera tile
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    camera = (40, 280, 120, 380)
    harness.check("the slide is cut out, the camera and the banner left beside it",
                  screen_crop_utils.find_slide_panel(frame, gray, [camera]) == (173, 4, 717, 436),
                  str(screen_crop_utils.find_slide_panel(frame, gray, [camera])))
    harness.check("with no face beside it, nothing is cut", screen_crop_utils.find_slide_panel(frame, gray, []) is None)
    light = frame.copy()
    light[light == 17] = 250
    light_gray = cv2.cvtColor(light, cv2.COLOR_BGR2GRAY)
    harness.check("a light backdrop is no dark backdrop", screen_crop_utils._find_panel_on_backdrop(light_gray, [camera]) is None)


def test_a_slide_shown_beside_the_speaker_then_in_full_is_the_same_slide_defect():
    # Defect: a talk showed a slide beside the speaker's camera, then in full; its words, misread at the small size,
    # matched 41% to 86%, and it was never marked a repeat. Fix: the two pictures scaled to one size are compared too.
    slide = numpy.full((432, 544, 3), 250, dtype=numpy.uint8)
    cv2.putText(slide, "Inducing electrons", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 90, 200), 3)
    cv2.rectangle(slide, (60, 140), (480, 300), (40, 160, 60), -1)
    full = cv2.resize(slide, (720, 576), interpolation=cv2.INTER_LINEAR)
    other = slide.copy()
    cv2.rectangle(other, (60, 140), (480, 300), (250, 250, 250), -1)
    cv2.putText(other, "Detector simulation", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3)
    harness.check("the small view and the full view are one slide",
                  frame_extraction_service.is_same_picture_scaled(slide, full))
    harness.check("another slide of the same look is not", not frame_extraction_service.is_same_picture_scaled(other, full))


def test_two_mostly_white_screens_of_other_content_are_not_one_picture_defect():
    # A terminal log was marked a repeat of a circuit schematic: both mostly white, they differed in under a tenth of
    # all their pixels. Two pictures are one only when their content pixels differ little too
    schematic = numpy.full((432, 544, 3), 250, dtype=numpy.uint8)
    for column in range(100, 480, 120):                                        # the schematic's traces
        cv2.line(schematic, (column, 80), (column, 360), (40, 140, 40), 2)
    cv2.line(schematic, (60, 220), (500, 220), (40, 40, 140), 2)
    terminal = numpy.full((432, 544, 3), 250, dtype=numpy.uint8)
    for row in range(20, 200, 18):                                             # the terminal's log lines
        cv2.putText(terminal, "Setting RFM value 200", (10, row), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (20, 20, 20), 1)
    harness.check("a schematic and a terminal log are two pictures",
                  not frame_extraction_service.is_same_picture_scaled(schematic, terminal))
    harness.check("the terminal log in a larger view is one picture",
                  frame_extraction_service.is_same_picture_scaled(terminal, cv2.resize(terminal, (720, 576))))


def test_a_small_white_slide_touching_white_clouds_of_the_backdrop_is_cut_to_it_defect():
    # A talk's white slide, a quarter of the frame, touched the white clouds of its backdrop: its grey's piece held the
    # clouds too, and the frames kept the backdrop and the camera. A solid rectangle with a little attached is a panel now
    frame = numpy.full((576, 720, 3), (120, 90, 60), dtype=numpy.uint8)        # a blue sky
    frame[47:329, 364:716] = 250                                               # the slide, 24% of the frame
    cv2.circle(frame, (340, 90), 45, (250, 250, 250), -1)                      # a cloud touching the slide's left side
    cv2.circle(frame, (600, 40), 30, (250, 250, 250), -1)                      # and one over its top edge
    for row in range(100, 300, 40):                                            # the slide's lines of text
        frame[row:row + 4, 400:650] = 60
    frame[240:520, 8:358] = 70                                                 # the camera tile
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    camera = (116, 317, 145, 359)
    panel = screen_crop_utils.find_slide_panel(frame, gray, [camera])
    harness.check("the slide is the panel, the clouds and the camera left out",
                  panel is not None and abs(panel[0] - 364) <= 4 and abs(panel[1] - 47) <= 4 and panel[3] <= 335, str(panel))


def test_a_slide_beside_a_camera_tile_framed_by_white_clouds_is_cut_to_it_defect():
    # A talk's slide on white clouds that ran round the speaker's camera: the clouds joined the slide's piece, the face
    # stood inside that piece, and no panel was found. A face in a tile running past the slide's rows is beside it now
    frame = numpy.full((576, 720, 3), (200, 150, 60), dtype=numpy.uint8)       # a blue sky
    frame[35:438, 0:546] = 250                                                 # the slide
    for row in range(90, 400, 50):                                             # the slide's lines of text
        frame[row:row + 4, 60:480] = 60
    frame[500:576, :] = 250                                                    # clouds along the bottom
    frame[438:500, 100:140] = 250                                              # one joined to the slide
    frame[241:565, 557:712] = 30                                               # the camera tile, down into the clouds
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    face = (598, 377, 647, 447)
    panel = screen_crop_utils.find_slide_panel(frame, gray, [face])
    harness.check("the slide is the panel, the camera left out",
                  panel is not None and panel[2] <= 550 and abs(panel[1] - 35) <= 4 and abs(panel[3] - 438) <= 4, str(panel))


def test_a_dark_slide_holding_a_large_picture_on_a_light_backdrop_is_cut_to_it_defect():
    # A talk's opening layout: its dark title slide, a quarter of it a turquoise picture, small on white clouds beside
    # the speaker's camera. The slide's grey filled 41% of the columns over the picture, and no panel was found; what
    # the slide's grey encloses counts as the slide now
    frame = numpy.full((576, 720, 3), 245, dtype=numpy.uint8)                  # white clouds
    frame[47:328, 364:716] = 42                                                # the dark slide, 24% of the frame
    frame[105:270, 540:680] = (200, 200, 30)                                   # its turquoise picture
    for row in range(120, 230, 30):                                            # the slide's lines of text
        frame[row:row + 4, 380:500] = 230
    frame[243:525, 0:359] = 90                                                 # the camera tile
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    camera = (152, 277, 180, 320)
    panel = screen_crop_utils.find_slide_panel(frame, gray, [camera])
    harness.check("the slide with its picture is the panel, the camera left out",
                  panel is not None and abs(panel[0] - 364) <= 4 and abs(panel[1] - 47) <= 4 and panel[2] >= 700, str(panel))


def test_a_full_screen_slide_with_a_photo_is_no_panel_defect():
    # A full-screen slide's grey, its photo left out, passed as a panel kept as a solid rectangle, the photo's face
    # counting as beside it, and the cut took the photo off; under that exception the face must stand outside the grey
    import cv2
    import numpy

    from utils import screen_crop_utils

    frame = numpy.full((576, 720, 3), (70, 66, 52), numpy.uint8)
    for row in range(60, 520, 50):
        cv2.rectangle(frame, (30, row), (520, row + 3), (220, 220, 220), -1)
    cv2.rectangle(frame, (578, 115), (720, 385), (150, 170, 200), -1)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    photo_face = (620, 170, 680, 250)
    harness.check("no panel: the face is the slide's own photo",
                  screen_crop_utils.find_slide_panel(frame, gray, [photo_face]) is None,
                  str(screen_crop_utils.find_slide_panel(frame, gray, [photo_face])))


def _draw_broadcast(panel: tuple[int, int, int, int]) -> numpy.ndarray:
    """A broadcast frame: a sky backdrop with clouds, the speaker's camera at the bottom left, the slide in `panel`."""

    frame = numpy.full((576, 720, 3), (200, 150, 60), dtype=numpy.uint8)
    for centre in ((90, 80), (250, 120), (620, 500), (420, 540)):
        cv2.circle(frame, centre, 60, (245, 245, 245), -1)
    frame[240:520, 8:358] = 70
    frame[panel[1]:panel[3], panel[0]:panel[2]] = 250

    return frame


def test_the_smallest_known_layout_whose_surroundings_match_is_taken_defect():
    # A talk switched between its slide large and small at the top right; the small layout was found only later, and
    # the large layout's surroundings matched its moments too, so they kept the camera or were cut too wide. Layouts
    # found ahead are known from the start, and the smallest that matches is taken
    from models.broadcast_layout_state import BroadcastLayoutState
    from models.shared_screen_result import SharedScreenResult

    large, small = (177, 0, 720, 438), (364, 47, 720, 329)
    known = []
    for panel in (large, small):
        gray = cv2.cvtColor(_draw_broadcast(panel), cv2.COLOR_BGR2GRAY)
        known.append((panel, screen_crop_utils.shrink_outside_panel(gray, panel)))
    frame = _draw_broadcast(small)
    frame[120:200, 400:500] = (40, 120, 220)                                   # a diagram's box on the small slide
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    whole = SharedScreenResult((0, 0, 720, 576), None, False)
    found = frame_extraction_service._apply_layout(whole, gray, BroadcastLayoutState(known=list(known)))
    harness.check("a moment of the small layout is cut to the small panel", found.box == small and found.is_panel, str(found))
    found = frame_extraction_service._apply_layout(whole, cv2.cvtColor(_draw_broadcast(large), cv2.COLOR_BGR2GRAY),
                                                   BroadcastLayoutState(known=list(known)))
    harness.check("a moment of the large layout keeps the large panel", found.box == large, str(found))


def test_a_dark_brown_slide_beside_the_camera_is_cut_to_the_slide_defect():
    # Defect: a talk's dark brown slide (RGB 32, 16, 14) has an HSV saturation of 143, over the panel rule's limit, so no
    # grey band counted it and the slide was cut to a black picture inside it. Fix: a pixel whose channels differ by at
    # most 30 levels counts as neutral too
    frame = numpy.full((576, 720, 3), 225, dtype=numpy.uint8)                 # a light backdrop
    frame[5:437, 5:545] = (14, 16, 32)                                        # the dark brown slide
    frame[90:370, 80:470] = (8, 8, 8)                                         # a black picture on it
    frame[300:560, 560:710] = (60, 70, 80)                                    # the speaker's camera
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    panel = screen_crop_utils.find_slide_panel(frame, gray, [(620, 350, 650, 390)])
    harness.check("the whole slide is the panel", panel is not None and panel[0] <= 6 and panel[1] <= 6
                  and panel[2] >= 544 and panel[3] >= 436, str(panel))


def test_a_near_black_slide_on_a_dark_backdrop_ends_where_its_field_ends_defect():
    # Defect: a near-black slide and the dark patterned backdrop under it fell in one grey band, and the panel ran to the
    # picture's bottom with 125 rows of backdrop. Fix: a side near the border ends where the slide's field colour does
    frame = numpy.full((576, 720, 3), (8, 0, 11), numpy.uint8)                # the dark backdrop
    frame[440::24, :] = (30, 0, 40)                                           # its grid lines
    frame[0:438, 164:720] = (12, 0, 20)                                       # the near-black slide, to the right edge
    frame[100:120, 220:600] = (230, 230, 230)                                 # a line of its text
    trimmed = screen_crop_utils._trim_backdrop_beyond_field(frame, (164, 0, 720, 563))
    harness.check("the panel ends at the slide's bottom", trimmed == (164, 0, 720, 438), str(trimmed))
    short = screen_crop_utils._trim_backdrop_beyond_field(frame, (164, 0, 720, 503))
    harness.check("so does a panel stopped at a backdrop line far from the border", short == (164, 0, 720, 438), str(short))
    strip = numpy.full((576, 720, 3), (200, 160, 120), numpy.uint8)           # a blue backdrop strip of 6 px above
    strip[6:438, 6:545] = (14, 16, 32)
    harness.check("a strip of 12 px or less is left to the edge strips",
                  screen_crop_utils._trim_backdrop_beyond_field(strip, (0, 0, 545, 438)) == (0, 0, 545, 438))


def test_a_panel_found_inside_a_known_slide_whose_outline_stands_is_the_slide_defect():
    # Defect: a slide holding a large dark picture was cut to the picture, which passed as a panel beside the camera, and
    # the slide's title was lost. Fix: a panel inside a known slide whose outline stands in this moment is that slide;
    # with the outline gone (a smaller layout of the talk on its backdrop) the panel found stays
    from models.broadcast_layout_state import BroadcastLayoutState
    from models.shared_screen_result import SharedScreenResult

    frame = numpy.full((576, 720), 225, dtype=numpy.uint8)                    # a light backdrop
    frame[5:437, 5:545] = 25                                                  # the dark slide
    frame[60:380, 60:490] = 8                                                 # a picture on it
    slide, picture = (5, 5, 545, 437), (60, 60, 490, 380)
    known = [(slide, screen_crop_utils.shrink_outside_panel(frame, slide))]
    found = frame_extraction_service._apply_layout(SharedScreenResult(picture, None, True), frame,
                                                   BroadcastLayoutState(known=list(known)))
    harness.check("the picture's moment is cut to the slide", found.box == slide and found.is_panel, str(found))
    alone = numpy.full((576, 720), 225, dtype=numpy.uint8)
    alone[60:380, 60:490] = 8                                                 # the picture alone on the backdrop
    found = frame_extraction_service._apply_layout(SharedScreenResult(picture, None, True), alone,
                                                   BroadcastLayoutState(known=list(known)))
    harness.check("with no slide outline around it the panel stays", found.box == picture, str(found))



def test_a_panel_view_band_of_eleven_lines_is_cut_defect():
    # A panel view kept a black band of 9 lines on top and 11 on the right of a white slide, past the trim's 8-line
    # limit (DEFECTS 472); the limit is 12 now
    from utils import screen_crop_utils

    frame = numpy.full((180, 320), 250, numpy.uint8)
    frame[:9] = 4
    frame[:, -11:] = 4
    harness.check("both bands are cut, (top, bottom, left, right)",
                  screen_crop_utils.find_edge_strips(frame) == (9, 0, 0, 11), str(screen_crop_utils.find_edge_strips(frame)))


def test_a_broadcast_strip_outside_the_slide_is_cut_and_a_plain_margin_kept_defect():
    # Slides shown whole in a broadcast kept the stream's strips on their edges (a 5 px band on top, a backdrop strip
    # on the left); the lines outside the step to the slide are cut now, a margin with no step kept
    from utils import screen_crop_utils

    frame = numpy.full((180, 320), 30, numpy.uint8)
    frame[:4] = 140
    frame[:, :3] = 220
    harness.check("a band and a strip outside a step are cut, (top, bottom, left, right)",
                  screen_crop_utils.find_edge_strips(frame) == (4, 0, 3, 0), str(screen_crop_utils.find_edge_strips(frame)))
    plain = numpy.full((180, 320), 240, numpy.uint8)
    plain[60:120, 100:220] = 20
    harness.check("a plain margin with no step stays", screen_crop_utils.find_edge_strips(plain) == (0, 0, 0, 0),
                  str(screen_crop_utils.find_edge_strips(plain)))
