"""Cutting the frames the frame agents will look at, and writing each one as a JPEG.

Three modes, REQUIREMENTS.md R1 plus the one measured in research/frame-change/:

  interval  by the clock. Candidates are sampled at twice the wanted rate and the most different ones
            are kept, which is the archived program's rule. What is NOT carried is its ordering: it
            returned the frames sorted by difference score, so frame numbers and timestamps disagreed.
  scene     by content. Every frame is scored against the one before it, the scores are cut into
            scenes, and each scene gives exactly one frame, however long it is on screen.
  change    by what the shared screen shows. Once a second the frame is cropped to the shared screen,
            the black stage and any column of faces cut away, and compared with the last frame taken.
            A frame is taken when the screen changed enough and then held still. A second showing
            only faces is never taken. A return to an earlier screen is taken again, marked
            `repeatOf` that frame, so the speech while it is back on screen stays attached to it; a repeat
            on screen under `frames.passThroughSeconds` is a pass-through and is not taken. A frame that changed
            little from the frame just before it (`frames.buildStepChange`) is marked `stepOf` the build's first
            frame: one slide built up step by step.
            Once every frame is taken, the bars inside the screen (menu bars, title bars, taskbars)
            are cut: the edge rows and columns that stay the same across the frames around it.

Every mode returns the frames in chronological order, `frame-000` earliest, and writes no frame wider
than `frames.maxWidth`.
"""

from __future__ import annotations

import bisect
import logging
from collections import deque
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy

from config import FrameSettings
from models import screen_box
from models.broadcast_layout_state import BroadcastLayoutState
from models.input_range_spec import InputRangeSpec
from models.shared_screen_result import SharedScreenResult
from models.video_errors import AudioExtractionFailed, UnreadableVideo
from services import face_detection_service
from services import inbox_service
from services import person_detection_service
from services import pdf_source_service
from services import recording_parts_service
from services import text_detection_service
from utils import change_capture_utils
from utils import scene_segmentation_utils
from utils import screen_crop_utils
from utils import frame_slug_utils

__all__ = ["extract_frames", "read_duration_s", "is_same_picture_scaled"]

_LOG = logging.getLogger(__name__)

# Scores are computed on a small copy of each frame: a cut is as visible at 160 pixels wide as at
# 960, and scoring every frame of a video at full size is most of this phase's cost.
_SCORING_WIDTH = 160
# `change` mode: how many recent seconds decide that a block never stops moving.
_ACTIVE_WINDOW_S = 30
# `change` mode: how often, in seconds of video sampled, the sampling reports how far it is
_PROGRESS_EVERY_SECONDS = 10
# `change` mode: a taken frame is held at this quality until its bars are cut, then written at `jpegQuality`, so the
# written frame is compressed once at the chosen quality, not twice.
_HELD_JPEG_QUALITY = 98
# `change` mode: before the capture loop, one moment every this many seconds is looked at for a conference broadcast's
# slide panels, and a panel found on at least `_LAYOUT_LEAST_SIGHTINGS` of them (a box a few pixels off counting as it)
# is known to the loop from its start; at each moment the loop takes the smallest known panel whose frame around it
# matches, the most particular layout. A talk switched between its slide large and its slide small at the top right,
# and the small one's diagram slides filled no grey level, so that layout was found only at a later text slide and every
# frame before kept the speaker's camera and the backdrop; the large layout's surroundings matched those moments too.
# On that talk the small layout's box was found on 9 moments, a box trimmed by one slide's content on 1 (DEFECTS 195)
_LAYOUT_LOOK_SECONDS = 30
# Two known layouts whose surroundings differ from a moment's by this many grey levels of each other match it as well
_LAYOUT_DIFFERENCE_TIE_LEVELS = 1.0
_LAYOUT_LEAST_SIGHTINGS = 2
_LAYOUT_SAME_BOX_PX = 6
# A slide shown again in another view: at least this many words, nearly all of them on both showings
_REPEAT_MIN_WORDS = 8
_REPEAT_WORD_SHARE = 0.9
# The shortest word that test counts, in letters
_REPEAT_MIN_WORD_LETTERS = 3
# ... or when the two pictures, each scaled to this size in grey, differ by more than this many levels in under this
# share of their pixels: a 39c3 talk's slide beside the speaker and then in full differed in 3.8% to 7% (four pairs),
# where its words, misread at the smaller size, matched 41% to 86%; other slides of the talk differed in 26% to 93%
_REPEAT_SCALED_SIZE = (160, 120)
_REPEAT_SCALED_LEVELS = 30
_REPEAT_SCALED_SHARE = 0.1
# ... and in under this share of the pixels either picture holds content on (off its median grey by those levels): two
# mostly white frames, a circuit schematic and a terminal log, differed in 9% of all their pixels and 90% of their
# content, and the log was marked a repeat of the schematic; the Photon clip's true repeats differed in 10% to 15% of
# their content, its other slides in 32% or more (DEFECTS 208)
_REPEAT_SCALED_CONTENT_SHARE = 0.3
# `change` mode: each taken frame is compared with up to this many taken frames either side of it, in its screen box.
_BAR_NEIGHBOURS = 10
# A slide the face rules call a gallery is kept when the text reader finds this many words on it, the same count that
# tells a lone camera from a slide
_SLIDE_WITH_FACES_MIN_WORDS = screen_crop_utils.LONE_CAMERA_MAX_WORDS


def _build_frame_file_name(slug: str) -> str:
    return f"{slug}.jpg"


def _build_item(index: int,
                frame_number: int,
                score: float,
                video: dict,
                frames_dir: Path) -> dict:
    """The fields every frame's item holds, whichever mode took it."""

    slug = frame_slug_utils.build_frame_slug(index)

    return {"slug": slug, "index": index, "frameNumber": frame_number, "timestamp": round(video["frameTimes"][frame_number], 3),
            "path": f"{frames_dir.name}/{_build_frame_file_name(slug)}", "score": round(score, 4)}


def _decode_held_jpeg(frame: dict,
                      flags: int) -> numpy.ndarray:
    return cv2.imdecode(numpy.frombuffer(frame["jpeg"], numpy.uint8), flags)


def _clip_to_box(image: numpy.ndarray,
                 box: screen_box.Box) -> numpy.ndarray:
    """The part of `image` inside `box`, x0, y0, x1, y1."""

    x0, y0, x1, y1 = box

    return image[y0:y1, x0:x1]


def _read_screen_words(frame: numpy.ndarray,
                       box: screen_box.Box) -> list[dict]:
    """The words the text reader finds on the screen in `box`."""

    return text_detection_service.find_words_in_image(_clip_to_box(frame, box))


def _encode_jpeg(image: numpy.ndarray,
                 quality: int,
                 slug: str) -> bytes:
    """`image` as JPEG bytes; a frame that cannot be encoded stops the phase by name, never listed with no file."""

    is_encoded, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not is_encoded:
        raise OSError(f"{slug} could not be encoded as a JPEG")

    return jpeg.tobytes()


def _open_video(video_path: Path) -> tuple[recording_parts_service.RecordingContext, dict]:
    """The recording, one file or a folder of parts read as one video, and what it is."""

    capture = recording_parts_service.open_recording(video_path)
    fps = capture.fps
    frame_count = capture.frame_count
    if not capture.is_open() or fps <= 0 or frame_count <= 0:
        capture.release()
        raise UnreadableVideo(f"{video_path} could not be opened as a video, or it holds no frame")
    # Each frame's own time: a screen recording's frame rate varies, so a frame number divided by the average rate is
    # up to minutes off (107 s on one, and the notes got the speech of another moment)
    try:
        frame_times = capture.list_frame_times()
        video = {"fps": float(fps), "frameCount": frame_count, "durationS": sum(capture.list_part_durations()),
                 "width": capture.width, "height": capture.height, "frameTimes": frame_times}
    except BaseException:
        capture.release()
        raise

    return capture, video


def _shrink_to_width(image: numpy.ndarray,
                     max_width: int,
                     interpolation: int = cv2.INTER_AREA) -> numpy.ndarray:
    """The frame as written: scaled down to `max_width` when wider, untouched otherwise or when it is 0."""

    if not max_width or image.shape[1] <= max_width:
        return image

    return cv2.resize(image, (max_width, max(1, round(image.shape[0] * max_width / image.shape[1]))),
                      interpolation=interpolation)


def _score_frames(capture: recording_parts_service.RecordingContext,
                  step: int) -> dict[int, float]:
    """frame number -> how much it differs from the previously scored frame. Every `step`-th frame is
    scored; the first scored frame has nothing before it and scores 0."""

    scores: dict[int, float] = {}
    previous = None
    number = 0
    while True:
        # Only the frames scored are decoded: the others are stepped over, as change mode does
        if number % step:
            if not capture.skip_frame():
                break
            number += 1
            continue
        is_read, frame = capture.read_frame()
        if not is_read:
            break
        # OpenCV's own interpolation, as the scene scores were measured with
        small = _shrink_to_width(frame, _SCORING_WIDTH, cv2.INTER_LINEAR)
        scores[number] = 0.0 if previous is None else scene_segmentation_utils.compute_frame_difference(previous, small)
        previous = small
        number += 1

    return scores


def _keep_window_scores(scores: dict[int, float],
                        video: dict,
                        window: tuple[float, float]) -> dict[int, float]:
    """The scores of the frames whose time lies in `window`, in seconds of the recording."""

    times = video["frameTimes"]

    return {number: score for number, score in scores.items()
            if number < len(times) and window[0] <= times[number] < window[1]}


def _choose_interval_frames(scores: dict[int, float],
                            wanted: int) -> list[int]:
    """The `wanted` most different candidates, returned in chronological order."""

    chosen = sorted(scores, key=lambda number: (-scores[number], number))[:wanted]

    return sorted(chosen)


def _choose_scene_frames(scores: dict[int, float],
                         video: dict,
                         settings: FrameSettings) -> list[int]:
    numbers = sorted(scores)
    scenes = scene_segmentation_utils.split_into_scenes([scores[number] for number in numbers], video["fps"],
                                                     settings.scene_threshold, settings.min_scene_seconds,
                                                     settings.max_count)

    # A scene's frame is a position among the scored frames, mapped back to the frame's number
    return [numbers[scene.representative_frame] for scene in scenes]


def _write_frames(capture: recording_parts_service.RecordingContext,
                  numbers: list[int],
                  scores: dict[int, float],
                  video: dict,
                  frames_dir: Path,
                  settings: FrameSettings) -> list[dict]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    items = []
    unread = 0
    for number in numbers:
        is_read, frame = capture.read_frame() if capture.move_to_frame(number) else (False, None)
        if not is_read:
            unread += 1
            continue
        if screen_crop_utils.is_blank(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)):
            continue
        # Numbered by the frames written, so a frame the recording would not give leaves no gap in the slugs
        item = _build_item(len(items), number, scores.get(number, 0.0), video, frames_dir)
        (frames_dir / _build_frame_file_name(item["slug"])).write_bytes(
            _encode_jpeg(_shrink_to_width(frame, settings.max_width), settings.jpeg_quality, item["slug"]))
        items.append(item)
    if unread:
        _LOG.warning("%s of %s chosen frame(s) could not be read from the recording", unread, len(numbers))

    return items


def _find_slide_box_despite_faces(frame: numpy.ndarray,
                                  gray: numpy.ndarray,
                                  faces: list[screen_box.Box]) -> tuple[screen_box.Box, list[dict]]:
    """The screen a gallery's face count would drop, kept when the text reader finds words on it: two faces in a slide's
    picture were read as a gallery once the camera panel beside them was cut. Read only for frames the
    face rules drop, which are few. The words read on it come back with it, so they are not read twice."""

    box = screen_crop_utils.find_screen_box(gray, faces, is_gallery_refused=False)
    if box is None:
        return screen_box.NO_SCREEN, []
    words = _read_screen_words(frame, box)
    is_slide = screen_crop_utils.count_content_words(words, box[3] - box[1]) >= _SLIDE_WITH_FACES_MIN_WORDS

    return (box, words) if is_slide else (screen_box.NO_SCREEN, [])


def _detect_side_people(frame: numpy.ndarray) -> list[screen_box.Box]:
    """The people on the frame; when none stands in its outer PANEL_SIDE on a side, the people found on that side's
    strip alone too. The detector reads the frame scaled to 416 pixels, and a lecture's camera inset shrank its
    lecturer to about 30 pixels, found on no frame; read on the strip, found on 103 and 127 of Lecture 7 (DEFECTS 601)."""

    people = person_detection_service.detect_people(frame)
    width = frame.shape[1]
    strip = round(screen_crop_utils.PANEL_SIDE * width)
    for x0, x1 in ((0, strip), (width - strip, width)):
        if any(x0 <= (person[0] + person[2]) / 2 < x1 for person in people):
            continue
        people += [(left + x0, top, right + x0, bottom)
                   for left, top, right, bottom in person_detection_service.detect_people(frame[:, x0:x1])]

    return people


def _find_shared_screen(frame: numpy.ndarray,
                        gray: numpy.ndarray) -> SharedScreenResult:
    """The shared screen's box on this frame, or NO_SCREEN for a gallery of cameras or one person's camera, and the
    presenter's camera floating in one of its corners, if any."""

    faces = face_detection_service.detect_faces(gray)
    found, read_words = screen_crop_utils.find_screen_box(gray, faces), None
    if found is None:
        found, read_words = _find_slide_box_despite_faces(frame, gray, faces)
    # The screen's words are read once, and only when a face is on it: the two rules below are the ones that need them
    is_screen = found != screen_box.NO_SCREEN
    is_lone_camera_view = is_screen and screen_crop_utils.is_lone_camera(found, faces)
    has_face_on_screen = is_screen and screen_crop_utils.has_face_inside(found, faces)
    is_words_needed = is_lone_camera_view or has_face_on_screen
    words = (read_words if read_words is not None else _read_screen_words(frame, found)) if is_words_needed else []
    # One person's camera filling the screen, with almost no text on it, shares nothing (two speaker views
    # were taken as frames)
    if is_lone_camera_view and screen_crop_utils.count_content_words(
            words, found[3] - found[1]) < screen_crop_utils.LONE_CAMERA_MAX_WORDS:
        found = screen_box.NO_SCREEN
    # A call window, its tiles named "<Last>, <First>": no shared screen, however small its faces
    if found != screen_box.NO_SCREEN and has_face_on_screen and screen_crop_utils.is_call_window(words, found[3] - found[1]):
        found = screen_box.NO_SCREEN
    # A conference broadcast: the slide is a panel beside the speaker's camera, on a backdrop with almost no words, and
    # the frame is the panel alone; the words are read only when such a panel is found
    panel = None if found == screen_box.NO_SCREEN else screen_crop_utils.find_slide_panel(frame, gray, faces)
    panel = None if panel is None else screen_crop_utils.clip_panel_to_box(panel, found, gray.shape[1])
    is_panel = panel is not None and screen_crop_utils.count_words_outside(
        text_detection_service.find_words_in_image(frame), panel) < screen_crop_utils.SLIDE_PANEL_MAX_OUTSIDE_WORDS
    found = panel if is_panel else found
    tile = None if found == screen_box.NO_SCREEN else screen_crop_utils.find_camera_tile(gray, found, faces)
    # A camera inset at mid height on a side has no corner, and its lecturer, small and looking down, no face the face
    # detector finds: it is found from the person (DEFECTS 91, 103)
    if tile is None and found != screen_box.NO_SCREEN:
        tile = screen_crop_utils.find_side_tile(gray, found, _detect_side_people(frame))

    return SharedScreenResult(found, tile, is_panel)


def _compute_box_area(box: screen_box.Box) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def _is_inside(inner: screen_box.Box,
               outer: screen_box.Box) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _apply_layout(found: SharedScreenResult,
                  gray: numpy.ndarray,
                  layout: BroadcastLayoutState) -> SharedScreenResult:
    """`found`, or the broadcast's slide panel kept from an earlier second while the frame around it still matches; a
    panel found now is kept for the seconds after. The panel rule needs a face beside the slide and a slide that fills
    its box, and found the slide on 7 of 105 frames of one talk: every other frame kept the speaker's camera. A camera
    tile is kept the same way: the person detector missed a lecture's inset on 2 of 8 frames."""

    # A camera tile found is kept for the seconds its person is not found, while its edges still stand
    if found.tile is not None:
        layout.tile = found.tile
    elif (layout.tile is not None and found.box != screen_box.NO_SCREEN
          and _is_inside(layout.tile, found.box) and screen_crop_utils.is_side_tile_kept(gray, layout.tile)):
        found = found._replace(tile=layout.tile)
    # A panel found inside a known slide whose outline stands is a picture on that slide: a large dark picture passed
    # as a panel beside the camera, and the slide's title and address line were cut off (DEFECTS 307)
    if found.is_panel:
        holders = [(panel, outside) for panel, outside in layout.known
                   if panel != found.box and _is_inside(found.box, panel) and screen_crop_utils.is_outline_standing(gray, panel)]
        if holders:
            layout.panel, layout.outside = max(holders, key=lambda entry: _compute_box_area(entry[0]))

            return SharedScreenResult(layout.panel, found.tile, True)
        layout.panel, layout.outside = found.box, screen_crop_utils.shrink_outside_panel(gray, found.box)

        return found
    if found.box == screen_box.NO_SCREEN:
        return found
    candidates = layout.known + ([] if layout.panel is None else [(layout.panel, layout.outside)])
    matching = [(panel, outside) for panel, outside in candidates if screen_crop_utils.is_same_layout(gray, panel, outside)]
    if not matching:
        return found
    # The layout whose surroundings differ least; of those as close, the smallest: a small slide's surroundings hold
    # the large layout's too, while a large slide stands in the small layout's surroundings
    differences = [screen_crop_utils.compute_layout_difference(gray, panel, outside) for panel, outside in matching]
    closest = [entry for entry, difference in zip(matching, differences)
               if difference <= min(differences) + _LAYOUT_DIFFERENCE_TIE_LEVELS]
    layout.panel, layout.outside = min(closest, key=lambda entry: _compute_box_area(entry[0]))
    # ... unless it lies inside another matching layout whose outline stands: then it is a picture on that slide
    holders = [(panel, outside) for panel, outside in matching if panel != layout.panel
               and _is_inside(layout.panel, panel) and screen_crop_utils.is_outline_standing(gray, panel)]
    if holders:
        layout.panel, layout.outside = max(holders, key=lambda entry: _compute_box_area(entry[0]))

    return SharedScreenResult(layout.panel, None, True)


def _find_layout_ahead(video_path: Path,
                       video: dict,
                       window: tuple[float, float]) -> BroadcastLayoutState:
    """The layout the capture loop starts with, from one moment looked at every `_LAYOUT_LOOK_SECONDS` on a capture of
    its own: each slide panel found on at least `_LAYOUT_LEAST_SIGHTINGS` of them, with the outside sample of its first
    sighting, and the first camera tile found. A lecture's inset was found only past the
    loop's first frames, which kept it uncovered (DEFECTS 601)."""

    sightings: list[list] = []
    layout = BroadcastLayoutState()
    capture = recording_parts_service.open_recording(video_path)
    try:
        # Within the run's window alone (`window`, in seconds of the recording): a layout outside it is never shown
        for second in range(int(window[0]), int(window[1]), _LAYOUT_LOOK_SECONDS):
            number = bisect.bisect_left(video["frameTimes"], float(second))
            if number >= len(video["frameTimes"]) or not capture.move_to_frame(number):
                continue
            is_read, frame = capture.read_frame()
            if not is_read:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found = _find_shared_screen(frame, gray)
            if found.tile is not None and layout.tile is None:
                layout.tile = found.tile
            if not found.is_panel:
                continue
            same = next((entry for entry in sightings if all(abs(one - other) <= _LAYOUT_SAME_BOX_PX
                                                               for one, other in zip(entry[0], found.box))), None)
            if same is None:
                sightings.append([found.box, screen_crop_utils.shrink_outside_panel(gray, found.box), 1])
            else:
                same[2] += 1
    finally:
        capture.release()

    layout.known = [(box, outside) for box, outside, count in sightings if count >= _LAYOUT_LEAST_SIGHTINGS]

    return layout


def _read_slide_words(image: numpy.ndarray) -> set[str]:
    words = text_detection_service.find_words_in_image(image)

    return {word["text"].casefold() for word in words if len(word["text"]) >= _REPEAT_MIN_WORD_LETTERS}


def is_same_picture_scaled(one: numpy.ndarray,
                            other: numpy.ndarray,
                            content_share: float = _REPEAT_SCALED_CONTENT_SHARE) -> bool:
    """Whether two pictures of the screen, each scaled to one size in grey, show the same: under a tenth of their pixels
    differ by more than _REPEAT_SCALED_LEVELS, and under `content_share` of the pixels holding content."""

    scaled = [cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), _REPEAT_SCALED_SIZE, interpolation=cv2.INTER_AREA)
              .astype(numpy.int16) for image in (one, other)]
    differ = numpy.abs(scaled[0] - scaled[1]) > _REPEAT_SCALED_LEVELS
    content = numpy.zeros(differ.shape, bool)
    for image in scaled:
        content |= numpy.abs(image - numpy.median(image)) > _REPEAT_SCALED_LEVELS

    return (float(differ.mean()) < _REPEAT_SCALED_SHARE
            and float(differ[content].mean() if content.any() else 0.0) < content_share)


def _find_shown_again(gray: numpy.ndarray,
                      frame: numpy.ndarray,
                      box: screen_box.Box,
                      sample: numpy.ndarray,
                      taken: list[dict],
                      taken_samples: list[numpy.ndarray],
                      ignored: numpy.ndarray,
                      taken_words: dict[int, set[str]]) -> int | None:
    """The index of the taken frame this moment shows again, or None. Each earlier frame is compared with this moment
    cut by that frame's own box, exactly: compared across two boxes a pixel apart, one slide shown twice changed 1.4% of
    its blocks and was not a repeat. An earlier frame of another
    view (a broadcast's slide beside the speaker, then the slide in full) is the same slide when nearly every word the
    text reader finds on either is on both: no pixel matches across a zoom (frames 14 and 16). Fills `taken_words` with
    the words of each earlier frame it reads, so each is read once a run."""

    # STEP 1: the same picture, compared under each earlier frame's own box, this moment cut once per box
    samples_by_box = {tuple(box): sample}
    changes = []
    for earlier, earlier_sample in zip(taken, taken_samples):
        earlier_box = tuple(earlier["box"])
        if earlier_box not in samples_by_box:
            samples_by_box[earlier_box] = change_capture_utils.shrink_screen(gray, earlier_box)
        changes.append(change_capture_utils.compute_change(
            change_capture_utils.map_changed_blocks(samples_by_box[earlier_box], earlier_sample), ignored))
    closest = int(numpy.argmin(changes)) if changes else None
    repeat_of = closest if closest is not None and changes[closest] < change_capture_utils.REPEAT_LIMIT else None
    other_views = [index for index, earlier in enumerate(taken)
                   if screen_crop_utils.is_other_box(box, tuple(earlier["box"]), gray.shape[1])]
    if repeat_of is not None or not other_views:
        return repeat_of

    # STEP 2: the same slide in another view, by its picture scaled to one size, then by its words
    clip = _clip_to_box(frame, box)
    for index in reversed(other_views):
        if is_same_picture_scaled(clip, _decode_held_jpeg(taken[index], cv2.IMREAD_COLOR)):
            return index
    words = _read_slide_words(clip)
    if len(words) < _REPEAT_MIN_WORDS:
        return None
    for index in reversed(other_views):
        if index not in taken_words:
            taken_words[index] = _read_slide_words(_decode_held_jpeg(taken[index], cv2.IMREAD_COLOR))
        earlier_words = taken_words[index]
        if len(earlier_words) >= _REPEAT_MIN_WORDS and min(
                text_detection_service.count_shared_words(words, earlier_words) / len(words),
                text_detection_service.count_shared_words(earlier_words, words) / len(earlier_words)) >= _REPEAT_WORD_SHARE:
            return index

    return None


# A moving frame's words are read in three bands that overlap: read whole, a tour video's frames gave the text reader
# nothing, its small captions lost in the picture; read in bands, the captions came back on half of them
_WORDING_BANDS = ((0.0, 0.4), (0.3, 0.7), (0.6, 1.0))
# A screen held still this long, in seconds of the recording, is taken again (DEFECTS 157)
_HELD_RETAKE_SECONDS = 180.0
# A moving stretch with nothing new to read is sampled this many times more slowly than `busySeconds`
_MOVING_SLOW_TIMES = 3


def _read_band_words(image: numpy.ndarray) -> set[str]:
    height = image.shape[0]

    return {word for top, bottom in _WORDING_BANDS for word in _read_slide_words(image[int(height * top):int(height * bottom)])}


def _is_wording_held(frame: numpy.ndarray,
                     box: screen_box.Box,
                     taken: list[dict],
                     band_words: dict[int, set[str]]) -> bool:
    """Whether every word of the moment, none included, is on the last frame taken (a letter misread allowed), both
    read in bands; `band_words` keeps each taken frame's, read once."""

    words = _read_band_words(_clip_to_box(frame, box))
    last = len(taken) - 1
    if last < 0:
        return False
    if last not in band_words:
        band_words[last] = _read_band_words(_decode_held_jpeg(taken[last], cv2.IMREAD_COLOR))

    return text_detection_service.count_shared_words(words, band_words[last]) == len(words)


def _find_last_taken(box: screen_box.Box,
                     taken: list[dict],
                     taken_samples: list[numpy.ndarray]) -> tuple[numpy.ndarray | None, int]:
    """The last frame taken's sample (None before the first) and the shift a comparison with it allows: the capture
    and the taken frame's entry compare with it the same way."""

    if not taken:
        return None, 0

    return taken_samples[-1], change_capture_utils.choose_shift_tolerance(box, taken[-1]["box"])


def _build_taken(number: int,
                 frame: numpy.ndarray,
                 gray: numpy.ndarray,
                 box: screen_box.Box,
                 sample: numpy.ndarray,
                 change: float,
                 settings: FrameSettings,
                 taken: list[dict],
                 taken_samples: list[numpy.ndarray],
                 ignored: numpy.ndarray,
                 taken_words: dict[int, set[str]]) -> dict:
    """A taken frame's entry: its number, box, change, JPEG held at _HELD_JPEG_QUALITY, the earlier frame it shows
    again (found by `_find_shown_again`, which reads the text of earlier frames into `taken_words`), the frame before
    it, and whether it only adds to that frame or takes most of its content away. `change` is the share of blocks the
    frame changed against the last frame taken: for a view's last second (`_take_view_end`), the finer comparison's
    share with people left out, which build steps and the frame cap read as they read any other."""

    last_taken, last_shift = _find_last_taken(box, taken, taken_samples)
    jpeg = _encode_jpeg(_shrink_to_width(_clip_to_box(frame, box), settings.max_width), _HELD_JPEG_QUALITY,
                        f"the frame taken at frame number {number}")

    return {"frameNumber": number, "box": list(box), "change": change, "jpeg": jpeg,
            "repeatIndex": _find_shown_again(gray, frame, box, sample, taken, taken_samples, ignored, taken_words),
            "afterFrame": taken[-1]["frameNumber"] if taken else None,
            "isAddition": last_taken is not None and change_capture_utils.is_addition(last_taken, sample, last_shift),
            "contentGone": change_capture_utils.compute_content_gone(last_taken, sample)
            if last_taken is not None else 0.0}


def _take_view_end(before: tuple,
                   settings: FrameSettings,
                   taken: list[dict],
                   taken_samples: list[numpy.ndarray],
                   taken_people: list[numpy.ndarray],
                   taken_fine: list[numpy.ndarray],
                   ignored: numpy.ndarray,
                   taken_words: dict[int, set[str]],
                   is_held_still: bool) -> tuple[dict, numpy.ndarray, numpy.ndarray] | None:
    """The last second of a view the camera just cut away from, `before` (its number, frame, grey frame, box and
    sample), as a taken frame's entry with its people's blocks and its fine sample, when it shows the view of the last
    frame taken, in the same box, differing from it in VIEW_END_CHANGE of the blocks no person covers on either,
    compared at FINE_WIDTH; None otherwise. A slide with nobody in it counts too: a lecture's slide built up by steps
    of 1% to 4% each, under the capture's share, was never taken whole before the camera cut to the lecturer (DEFECTS
    115), when it held still for `is_held_still`; over eight clips this adds 0 to 3 frames each."""

    number, frame, gray, box, sample = before
    if not taken or taken[-1]["frameNumber"] == number or taken[-1]["box"] != list(box):
        return None
    # the view is the one its last frame taken shows: compared with a slide taken before the cut to a lecturer's
    # camera, a close-up of the lecturer was taken twice, with nothing on it
    view_change = change_capture_utils.map_changed_blocks(sample, taken_samples[-1])
    if view_change.mean() > change_capture_utils.NEW_VIEW_CHANGE:
        return None
    people = change_capture_utils.map_box_blocks(person_detection_service.detect_people(frame), box)
    # ... with nobody in it, only a view that held still before the cut: moving pictures, a camera through a tunnel,
    # change over half the blocks every second, and each second would read as a cut away from the one before
    if not people.any() and not is_held_still:
        return None
    fine = change_capture_utils.shrink_screen_fine(gray, box)
    changed = change_capture_utils.map_fine_changed_blocks(fine, taken_fine[-1])
    change = change_capture_utils.compute_change(changed, ignored | people | taken_people[-1])
    if change < change_capture_utils.VIEW_END_CHANGE:
        return None
    # the moment is checked for a screen in the box in use and a presenter's camera tile covered; the broadcast panel a
    # taken frame keeps (`_apply_layout`) is not read here, so under a kept panel the panel rule alone must find it
    found = _find_shared_screen(frame, gray)
    if found.box == screen_box.NO_SCREEN or screen_crop_utils.is_other_box(box, found.box, gray.shape[1]):
        return None
    if found.tile is not None:
        frame = screen_crop_utils.cover_camera_tile(frame, found.tile)

    return ({**_build_taken(number, frame, gray, box, sample, change, settings, taken, taken_samples, ignored,
                            taken_words), "tile": found.tile}, people, fine)


def _take_screen_changes(capture: recording_parts_service.RecordingContext,
                         video: dict,
                         settings: FrameSettings,
                         report_progress: Callable[[float, float], None] | None = None,
                         layout: BroadcastLayoutState | None = None,
                         window: tuple[float, float] | None = None) -> list[dict]:
    """Once a second, crop the frame to the shared screen, and take it when it changed at least `change_to_capture`
    since the last frame taken and has since held still for `still_seconds`, or kept moving for `busy_seconds`. Each
    taken frame is kept as its encoded JPEG, with its box, its change and the earlier frame it repeats, if any. With
    `window`, the run's range in seconds of the recording, sampling starts at its start and stops at its end."""

    frame_times = video["frameTimes"]
    window_start_s, window_end_s = window or (0.0, video["durationS"])
    # sample_index: how many seconds were sampled, one a second of the recording's time while it has frames, so behind
    # the recording's own time where it holds none
    next_second, sample_index = window_start_s, -1
    box, pending, previous_sample = None, None, None
    layout = layout if layout is not None else BroadcastLayoutState()
    recent_changes: deque = deque(maxlen=_ACTIVE_WINDOW_S)
    still_run, busy_run, number = 0, 0, -1
    taken: list[dict] = []
    taken_samples: list[numpy.ndarray] = []
    taken_people: list[numpy.ndarray] = []
    taken_fine: list[numpy.ndarray] = []
    # the places in `taken` of the frames taken while the screen kept moving
    taken_moving: set[int] = set()
    # each frame taken while moving, its words read in bands, by its place in `taken`
    taken_band_words: dict[int, set[str]] = {}
    # the second before this one, as decoded: its number, frame, grey frame, box and sample
    before: tuple | None = None
    # the screen as last seen with nobody in front of it, its blocks seen so, and the box they belong to
    unhidden_view, unhidden_seen, unhidden_box = None, None, None
    taken_words: dict[int, set[str]] = {}
    # frames decoded past the ones the recording's header counted, which have no time and are never sampled
    uncounted = 0
    # A window starting later is sought, never decoded up to: the reader is left on the frame before its first
    first_number = bisect.bisect_left(frame_times, window_start_s)
    if first_number and capture.move_to_frame(first_number):
        number = first_number - 1
    while capture.skip_frame():
        number += 1
        if number >= len(frame_times):
            uncounted += 1
            continue
        if frame_times[number] >= window_end_s:
            break
        # One sample a second of the recording's own time, whatever its frame rate does
        if frame_times[number] < next_second:
            continue
        next_second, sample_index = float(int(frame_times[number]) + 1), sample_index + 1
        # how far the sampling is, for phase 2's progress, counted within the window
        if report_progress is not None and (sample_index + 1) % _PROGRESS_EVERY_SECONDS == 0:
            report_progress(next_second - window_start_s, window_end_s - window_start_s)
        is_read, frame = capture.decode_frame()
        if not is_read:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = None

        # STEP 1: where the shared screen is, looked for every few seconds (every second while a new box waits), and
        # kept unless a new box holds
        # Looked for at once when none was found yet: a first second that did not decode left no box to read
        if box is None or screen_crop_utils.is_box_check_due(sample_index, settings.box_every_seconds, pending is not None):
            found = _apply_layout(_find_shared_screen(frame, gray), gray, layout)
            box, pending = screen_crop_utils.choose_crop_box(box, pending, found.box, gray.shape[1])
        if box == screen_box.NO_SCREEN or screen_crop_utils.is_blank(_clip_to_box(gray, box)):
            still_run, busy_run, previous_sample, before = 0, 0, None, None
            continue

        # STEP 2: how much moved since the second before, ignoring blocks that never stop moving
        sample = change_capture_utils.shrink_screen(gray, box)
        ignored = change_capture_utils.find_active_blocks(list(recent_changes))
        is_still = True
        if previous_sample is not None:
            recent_changes.append(change_capture_utils.map_changed_blocks(sample, previous_sample))
            is_still = change_capture_utils.compute_change(recent_changes[-1], ignored) <= change_capture_utils.STILL_LIMIT
            # The camera cut away from a view: its last second is taken when it shows what its last frame taken lacks.
            # A lecturer's few words written on a board in close-up changed 0% of the blocks compared at 320 px, and
            # the close-up gave way to a distant room view where they were barely readable (DEFECTS 122)
            if before is not None and recent_changes[-1].mean() > change_capture_utils.NEW_VIEW_CHANGE:
                # `still_run` is still the count up to the view's last second: it is updated for this second below
                view_end = _take_view_end(before, settings, taken, taken_samples, taken_people, taken_fine, ignored,
                                          taken_words, still_run >= settings.still_seconds)
                if view_end is not None:
                    view_end_entry, view_end_people, view_end_fine = view_end
                    _, _, _, before_box, before_sample = before
                    taken.append(view_end_entry)
                    taken_samples.append(before_sample)
                    taken_people.append(view_end_people)
                    taken_fine.append(view_end_fine)
                    unhidden_view, unhidden_seen = change_capture_utils.update_unhidden_view(
                        unhidden_view if unhidden_box == before_box else None,
                        unhidden_seen if unhidden_box == before_box else None, before_sample, view_end_people)
                    unhidden_box = before_box
        still_run, busy_run = (still_run + 1, 0) if is_still else (0, busy_run + 1)
        previous_sample = sample
        before = (number, frame, gray, box, sample)

        # STEP 3: taken once it changed enough since the last frame taken and held still, or kept moving too long
        # A frame taken under a box a pixel or two off is compared with that shift allowed: compared exactly, the same
        # slide changed 18% of its blocks and was taken again as new
        last_taken, last_shift = _find_last_taken(box, taken, taken_samples)
        changed = None if last_taken is None else change_capture_utils.map_changed_blocks(sample, last_taken, last_shift)
        change = 1.0 if changed is None else change_capture_utils.compute_change(changed, ignored)
        is_due = still_run >= settings.still_seconds or busy_run >= settings.busy_seconds
        # A screen held still for _HELD_RETAKE_SECONDS since the last frame taken is taken again, a repeat of it, so the
        # speech said over it is split among frames: one frame held 6 minutes carried the notes of several subjects,
        # and a heading started inside its speech got none of them (DEFECTS 157)
        is_held_long = (bool(taken) and still_run >= settings.still_seconds
                        and frame_times[number] - frame_times[taken[-1]["frameNumber"]] >= _HELD_RETAKE_SECONDS)
        if (change < settings.change_to_capture and not is_held_long) or not is_due:
            continue
        # A person is no content: the blocks a person covers now or covered on the last frame taken are left out, so a
        # lecturer walking before an unchanged blackboard takes no frame. Counted whole, a room camera's lecture took a
        # frame every 10 s of walking, about 100 of its 218 (DEFECTS 100). Looked for only here, where a frame is due
        people = change_capture_utils.map_box_blocks(person_detection_service.detect_people(frame), box)
        if changed is not None:
            # where the person stood on the last frame taken and no longer does, the screen is judged against how it
            # last looked with nobody in front of it: new writing there is a change, the person's leaving is not
            is_same_box = unhidden_box == box
            judged, hidden = change_capture_utils.compare_behind_person(
                changed, sample, taken_people[-1] & ~people, unhidden_view if is_same_box else None,
                unhidden_seen if is_same_box else None)
            if (change_capture_utils.compute_change(judged, ignored | people | hidden) < settings.change_to_capture
                    and not is_held_long):
                continue
        # The moment itself is checked before it is taken: the box is looked for every few seconds and changes on its
        # second check, so a speaker view that replaced the screen kept the last box for up to six seconds and was
        # taken (three speaker views of one meeting)
        is_box_checked = found is not None
        found = found or _apply_layout(_find_shared_screen(frame, gray), gray, layout)
        if found.box == screen_box.NO_SCREEN:
            continue
        # A moment showing another screen than the box in use waits for that box to hold, its sighting counted as a
        # check: taken at once, a talk's full-screen slides were cut with the box of the slide beside the speaker
        # (three frames of one talk)
        if screen_crop_utils.is_other_box(box, found.box, gray.shape[1]):
            if not is_box_checked:
                box, pending = screen_crop_utils.choose_crop_box(box, pending, found.box, gray.shape[1])
            continue
        # A presenter's camera floating in a corner of the screen is covered: a face is no content
        if found.tile is not None:
            frame = screen_crop_utils.cover_camera_tile(frame, found.tile)
        # A moment due only because the screen kept moving, right after another such frame, with no word the frame
        # before lacks, waits until the motion has run _MOVING_SLOW_TIMES as long: a tour video in a talk gave a frame
        # every 10 s of a moving camera, 13 of 33 holding only the caption the one before held, and most showing no
        # word the reader can read (DEFECTS 172). The stretch is still sampled, and the frame after the motion stops
        # is taken by the still rule, so a moving picture's last state is kept
        is_moving = still_run < settings.still_seconds
        if (is_moving and len(taken) - 1 in taken_moving and busy_run < _MOVING_SLOW_TIMES * settings.busy_seconds
                and _is_wording_held(frame, box, taken, taken_band_words)):
            continue
        if is_moving:
            taken_moving.add(len(taken))
        # Taken only because the screen was held 3 minutes: a repeat of the frame before, never a step of its build
        # (DEFECTS 471)
        is_held_retake = is_held_long and change < settings.change_to_capture
        taken.append({**_build_taken(number, frame, gray, box, sample, change, settings, taken, taken_samples, ignored,
                                     taken_words), "tile": found.tile, "isHeldRetake": is_held_retake})
        taken_samples.append(sample)
        taken_people.append(people)
        taken_fine.append(change_capture_utils.shrink_screen_fine(gray, box))
        if unhidden_box != box or change > change_capture_utils.NEW_VIEW_CHANGE:
            unhidden_view, unhidden_seen, unhidden_box = None, None, box
        unhidden_view, unhidden_seen = change_capture_utils.update_unhidden_view(unhidden_view, unhidden_seen, sample,
                                                                                 people)
        busy_run = 0
    if uncounted:
        _LOG.warning("%s frame(s) decoded past the %s the recording counted were not sampled", uncounted, len(frame_times))

    return taken


def _drop_pass_throughs(taken: list[dict],
                        video: dict,
                        pass_through_seconds: float) -> list[dict]:
    """The taken frames less every repeat that stays on screen under `pass_through_seconds`, until the next frame taken or the
    recording's end: a pass-through (slides flicked past, windows switched back and forth), not a return to discuss
    the screen again. A repeat of a dropped frame repeats what that one repeated."""

    frame_times = video["frameTimes"]
    ends = [frame_times[frame["frameNumber"]] for frame in taken[1:]] + [video["durationS"]]
    is_dropped = [frame["repeatIndex"] is not None and end - frame_times[frame["frameNumber"]] < pass_through_seconds
                  for frame, end in zip(taken, ends)]

    return _remove_taken_frames(taken, is_dropped)


def _remove_taken_frames(taken: list[dict],
                         is_dropped: list[bool]) -> list[dict]:
    """The taken frames less those `is_dropped` marks, each kept repeat renumbered to what it repeats among the kept:
    a repeat of a dropped frame repeats what that one repeated, and becomes an original only when none is kept."""

    new_index = {}
    for old, dropped in enumerate(is_dropped):
        if not dropped:
            new_index[old] = len(new_index)
    kept = []
    for old, frame in enumerate(taken):
        if is_dropped[old]:
            continue
        original = frame["repeatIndex"]
        while original is not None and is_dropped[original]:
            original = taken[original]["repeatIndex"]
        kept.append({**frame, "repeatIndex": None if original is None else new_index[original]})

    return kept


def _is_scroll_of_frame(before: dict,
                        frame: dict) -> bool:
    """Whether the taken frame is the one before it scrolled, from their held images; False when either has none."""

    if not (before.get("jpeg") and frame.get("jpeg")):
        return False

    return change_capture_utils.is_scroll_of(_decode_held_jpeg(before, cv2.IMREAD_GRAYSCALE),
                                             _decode_held_jpeg(frame, cv2.IMREAD_GRAYSCALE))


def _mark_build_steps(taken: list[dict],
                      max_change: float) -> list[dict]:
    """Each frame that follows the frame before it on screen and changed from it by at most `max_change`, or only added
    to it at any size (`isAddition`: a slide that gains a box, an arrow or a large picture on its free space), as a step
    of the build that frame started: `stepIndex`, the index of the build's first frame. A repeat of that build, right
    after it (an overlay taken off again), is a step of it too, not a return: repeats one after another are the same
    slide. The frame before it scrolled is a step of it as well. A `max_change` of 0 turns build steps off: no frame is
    marked."""

    if max_change <= 0:
        return [{**frame, "stepIndex": None} for frame in taken]
    marked = []
    for index, frame in enumerate(taken):
        before = marked[index - 1] if index else None
        is_next_on_screen = before is not None and before["frameNumber"] == frame.get("afterFrame")
        start = None
        if is_next_on_screen:
            start = before["stepIndex"] if before["stepIndex"] is not None else index - 1
        # A repeat of any frame of the build just before it (its first frame or a step) is that build again
        # ... unless it was taken only because the screen was held 3 minutes: that re-take is a repeat, splitting the
        # speech, and was marked a step of itself (DEFECTS 471)
        is_repeat_of_build = (is_next_on_screen and frame["repeatIndex"] is not None and start <= frame["repeatIndex"] < index
                              and not frame.get("isHeldRetake", False))
        # A frame that took most of the slide's content away is a new slide, however little changed in pixels
        is_slide_kept = frame.get("contentGone", 0.0) < change_capture_utils.BUILD_CONTENT_GONE_LIMIT
        is_small_change = (frame["repeatIndex"] is None and is_slide_kept
                           and (frame["change"] <= max_change or frame.get("isAddition", False)))
        # The frame before it scrolled is that screen again with what the scroll brought into view: a step of it
        is_scrolled = is_next_on_screen and frame["repeatIndex"] is None and _is_scroll_of_frame(before, frame)
        is_step = is_repeat_of_build or (is_next_on_screen and (is_small_change or is_scrolled))
        marked.append({**frame, "stepIndex": start if is_step else None,
                       "repeatIndex": None if is_step else frame["repeatIndex"]})

    return marked


def _keep_most_changed(taken: list[dict],
                       max_count: int) -> list[dict]:
    """At most `max_count` taken frames, in time order. Repeats are dropped first, then the frames that changed least; a
    repeat whose original was dropped repeats what that one repeated, or becomes an original itself."""

    order = sorted(range(len(taken)), key=lambda i: (taken[i]["repeatIndex"] is None, taken[i]["change"]), reverse=True)
    kept_set = set(order[:max_count])

    return _remove_taken_frames(taken, [index not in kept_set for index in range(len(taken))])


def _read_gray_words(gray: numpy.ndarray) -> list[dict]:
    """The words the text reader finds on a grey image, as its word records."""

    return text_detection_service.find_words_in_image(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))


def _read_top_band_words(gray: numpy.ndarray) -> list[dict]:
    """The words of a grey frame's top band (`screen_crop_utils.MENU_BAR_SHARE`), read twice as large, in the frame's
    pixels: a menu bar's letters are about 14 pixels high on a frame held 1280 wide."""

    band = gray[:round(screen_crop_utils.MENU_BAR_SHARE * gray.shape[0]) + 2]
    words = _read_gray_words(cv2.resize(band, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))

    return [{**word, "box": [value // 2 for value in word["box"]]} for word in words]


def _read_band_texts(band: numpy.ndarray) -> list[str]:
    """The words the text reader finds on a grey band of a frame."""

    return [word["text"] for word in _read_gray_words(band)]


def _map_covered(frame: dict,
                 other: dict,
                 shape: tuple[int, int]) -> numpy.ndarray:
    """The pixels of `frame`'s held image, of `shape`, that the camera tile covered on it or on `other` (both in the
    same screen box): filled with one colour on both, they match whatever each showed, and a room view beside a
    lecture's terminal passed as the same view at 50.0% changed (DEFECTS 91's cover)."""

    covered = numpy.zeros(shape, bool)
    x0, y0, x1, y1 = frame["box"]
    scale = shape[1] / (x1 - x0)
    for tile in (frame.get("tile"), other.get("tile")):
        if tile is not None:
            left, top, right, bottom = (round((value - origin) * scale) for value, origin in zip(tile, (x0, y0, x0, y0)))
            covered[max(0, top):max(0, bottom), max(0, left):max(0, right)] = True

    return covered


def _find_bars(taken: list[dict],
               index: int,
               grays: dict[int, numpy.ndarray]) -> screen_box.BarMargins:
    """The bars of frame `index`, from up to `_BAR_NEIGHBOURS` taken frames either side with its screen box. `grays`
    holds the decoded neighbours across calls, and loses the ones behind the window."""

    frame = taken[index]
    window = [other for other in range(max(0, index - _BAR_NEIGHBOURS), min(len(taken), index + _BAR_NEIGHBOURS + 1))
              if taken[other]["box"] == frame["box"]]
    for other in window:
        if other not in grays:
            grays[other] = _decode_held_jpeg(taken[other], cv2.IMREAD_GRAYSCALE)
    for other in [held for held in grays if held < index - _BAR_NEIGHBOURS]:
        del grays[other]

    neighbours = [grays[other] for other in window if other != index]
    covered = [_map_covered(frame, taken[other], grays[index].shape) for other in window if other != index]
    # A share framed inside a call window is cut at its outline, which a name label may cross (DEFECTS.md 66)
    if len([other for other in neighbours if other.shape == grays[index].shape]) >= screen_crop_utils.FRAME_MIN_NEIGHBOURS:
        framed = screen_crop_utils.find_framed_share(grays[index], neighbours, _read_band_texts)
        if framed is not None:
            return framed
    top, bottom, left, right = screen_crop_utils.find_bar_margins(grays[index], neighbours, covered)
    # a screen's own menu bar is cut by its words when no still band was (DEFECTS 118)
    if not top:
        top = screen_crop_utils.find_menu_bar_cut(grays[index], _read_top_band_words(grays[index]))
    # a flat letterbox band at a side is cut on this frame alone: a slide shown full width covers the same columns, so
    # they never stand still across frames (DEFECTS 119)
    band_left, band_right = screen_crop_utils.find_flat_side_bands(grays[index])
    # and a broadcast's strips round a slide shown whole, outside the step to the slide (DEFECTS 262)
    edges = screen_crop_utils.find_edge_strips(grays[index])
    margins = (max(top, edges[0]), max(bottom, edges[1]), max(left, band_left, edges[2]),
               max(right, band_right, edges[3]))
    if not any(margins):
        return margins
    height, width = grays[index].shape

    return screen_crop_utils.keep_words_whole(margins, _read_gray_words(grays[index]), width, height)


def _trim_bars(taken: list[dict],
               settings: FrameSettings) -> list[dict]:
    """Every taken frame cut to its screen box less its bars and encoded at the written quality. `box` becomes what
    the frame shows, in the recording's pixels; `screenBox` keeps the box the frame was found in."""

    grays: dict[int, numpy.ndarray] = {}
    finished = []
    for index, frame in enumerate(taken):
        # STEP 1: the bars, from the frames around this one that share its screen box
        margins = _find_bars(taken, index, grays) if settings.trim_bars else screen_box.NO_BARS

        # STEP 2: cut them off, and say in the recording's pixels what is left
        top, bottom, left, right = margins
        image = _decode_held_jpeg(frame, cv2.IMREAD_COLOR)
        height, width = image.shape[:2]
        x0, y0, x1, y1 = frame["box"]
        scale = (x1 - x0) / width
        box = [x0 + round(left * scale), y0 + round(top * scale), x1 - round(right * scale), y1 - round(bottom * scale)]
        jpeg = _encode_jpeg(image[top:height - bottom, left:width - right], settings.jpeg_quality,
                            frame_slug_utils.build_frame_slug(index))
        finished.append({**frame, "jpeg": jpeg, "box": box, "screenBox": frame["box"]})

    return finished


def _write_taken_frames(taken: list[dict],
                        video: dict,
                        frames_dir: Path) -> list[dict]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for index, frame in enumerate(taken):
        item = _build_item(index, frame["frameNumber"], frame["change"], video, frames_dir)
        (frames_dir / _build_frame_file_name(item["slug"])).write_bytes(frame["jpeg"])
        repeat_of = None if frame["repeatIndex"] is None else frame_slug_utils.build_frame_slug(frame["repeatIndex"])
        step_of = None if frame.get("stepIndex") is None else frame_slug_utils.build_frame_slug(frame["stepIndex"])
        # The camera tile covered on the stored frame, in the recording's pixels: the cut tool reads the recording and
        # covers it again, or a cut brings the presenter back (Lecture 7's chapter 1, DEFECTS 582)
        tile = {"cameraTile": [int(value) for value in frame["tile"]]} if frame.get("tile") is not None else {}
        # A re-take of a screen held 3 minutes says so, as a repeat of the frame before that is no return: the step
        # dropping returns that add nothing took it for one and gave its speech back to the frame before (DEFECTS 665)
        retake = {"isHeldRetake": True} if frame.get("isHeldRetake") else {}
        items.append({**item, "box": frame["box"], "screenBox": frame["screenBox"], "repeatOf": repeat_of, "stepOf": step_of,
                      **tile, **retake})

    return items


def extract_frames(video_path: Path,
                   frames_dir: Path,
                   settings: FrameSettings,
                   report_progress: Callable[[float, float], None] | None = None,
                   input_range: InputRangeSpec | None = None) -> dict:
    """`{"video": {...}, "mode": ..., "items": [...]}`, the items in chronological order, taken within the run's time
    range when it has one, each at its own time of the recording. `change` mode calls `report_progress(seconds sampled,
    seconds of the window)` as it samples the video."""

    # STEP 1: open the video and read what it is, and the window frames are taken in
    capture, video = _open_video(video_path)
    start_s = (input_range.start_s or 0.0) if input_range is not None else 0.0
    end_s = video["durationS"]
    if input_range is not None and input_range.end_s is not None:
        end_s = min(input_range.end_s, end_s)
    window = (start_s, end_s)
    try:
        # STEP 2: choose the frames: by what the shared screen shows, by scene, or by the clock; the scene and the
        # clock modes score the whole video and keep the frames of the window
        if settings.selection_mode == "change":
            layout = _find_layout_ahead(video_path, video, window)
            taken = _drop_pass_throughs(_take_screen_changes(capture, video, settings, report_progress, layout, window),
                                        video, settings.pass_through_seconds)
            taken = _mark_build_steps(_keep_most_changed(taken, settings.max_count), settings.build_step_change)
            taken = _trim_bars(taken, settings)
            items = _write_taken_frames(taken, video, frames_dir)
        elif settings.selection_mode == "scene":
            scores = _keep_window_scores(_score_frames(capture, step=1), video, window)
            numbers = _choose_scene_frames(scores, video, settings)
            items = _write_frames(capture, numbers, scores, video, frames_dir, settings)
        else:
            wanted = max(1, min(int(round((end_s - start_s) / 60 * settings.per_minute)), settings.max_count))
            scores = _keep_window_scores(_score_frames(capture, step=max(1, video["frameCount"] // (wanted * 2))), video,
                                         window)
            numbers = _choose_interval_frames(scores, wanted)
            items = _write_frames(capture, numbers, scores, video, frames_dir, settings)
    finally:
        capture.release()

    # The frame times are this phase's working table, one per frame of the recording; each item keeps its own
    return {"video": {key: value for key, value in video.items() if key != "frameTimes"}, "mode": settings.selection_mode,
            "items": items}


def read_duration_s(video_path: Path) -> float | None:
    """How long the recording is, in seconds: its parts' lengths added (ffprobe), for a video or an audio recording.
    None when it cannot be read. A PDF lasts its pages, a page a minute of its timeline."""

    if pdf_source_service.is_pdf(video_path):
        return pdf_source_service.count_pages(video_path) * pdf_source_service.PAGE_SECONDS
    # a Word or OpenDocument file has no length a reader could give it
    if inbox_service.is_document(video_path):
        return None
    if inbox_service.is_audio_recording(video_path):
        try:
            return round(sum(recording_parts_service.list_part_durations(video_path)), 1)
        except AudioExtractionFailed:
            # ffprobe could not read it: a length unknown, as for a video that cannot be opened
            return None
    try:
        capture, video = _open_video(video_path)
    except UnreadableVideo:
        return None
    capture.release()

    return round(video["durationS"], 1)
