"""Whether a needed frame shows what a placed cut or another needed frame already shows: phase 4 names such frames in a
chapter's request, and the checks hold a `leave_out` that claims it to the same measure."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy

from models import output_contract
from models.run_paths import RunPaths
from services import document_file_service
from services import text_detection_service
from utils import frame_region_utils
from utils import section_markdown_utils

__all__ = ["list_placed_matches", "list_twin_frames", "list_placed_cut_ids", "map_placed_frames"]

# Two images this close are one view: a needed frame's part and a placed cut, or two needed frames. The same view on
# two slides measured 0.0, two tables of one template with other words 0.115
_SAME_CONTENT_SHARE = 0.05
# Two frames of other shapes are one slide when this share of the words of the one with fewer is in both; a slide
# needs this many words for its text to tell
_SAME_TEXT_SHARE = 0.8
_SAME_TEXT_MIN_WORDS = 15
# A word counts toward a frame's text from this many letters: shorter ones are the text reader's noise
_MIN_WORD_LETTERS = 4


def _build_frame_path(paths: RunPaths,
                      frame_id: str) -> Path:
    return paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME / f"{frame_id}.jpg"


def _read_frame_words(paths: RunPaths,
                      frame_id: str) -> set[str]:
    """The words of `_MIN_WORD_LETTERS` letters or more the text reader finds on a frame, in lower case."""

    return {word["text"].casefold() for word in text_detection_service.find_words(_build_frame_path(paths, frame_id))
            if len(word["text"]) >= _MIN_WORD_LETTERS}


def list_placed_cut_ids(paths: RunPaths,
                        section_slugs: list[str]) -> set[str]:
    """The cuts the sections named place, read from their markdown."""

    placed = set()
    for slug in section_slugs:
        path = document_file_service.build_section_path(paths, slug)
        if path.is_file():
            placed |= {str(block["cut"]) for block in section_markdown_utils.parse_section(path.read_text(encoding="utf-8"))
                       if block["kind"] == "image"}

    return placed


def map_placed_frames(paths: RunPaths,
                      section_slugs: list[str],
                      cuts: dict[str, dict]) -> dict[str, str]:
    """Each frame a cut placed by the named sections comes from, with that cut's id."""

    return {str(cuts[cut_id]["frame"]): cut_id for cut_id in sorted(list_placed_cut_ids(paths, section_slugs))
            if cut_id in cuts and cuts[cut_id].get("frame")}


def _is_same_view(frame: numpy.ndarray,
                  frame_id: str,
                  cut: dict,
                  shown: numpy.ndarray | None) -> bool:
    """Whether `frame`, cropped to the rectangle a placed cut was looked at in, is a near copy of that cut's image
    `shown`. A cut of the frame itself, with no rectangle recorded or with no image to read, never is."""

    x0, y0, x1, y1 = cut.get("lookRect") or [0, 0, 0, 0]
    if shown is None or cut.get("frame") == frame_id or x1 <= x0 or y1 <= y0:
        return False
    difference = frame_region_utils.compute_content_difference(frame[y0:y1, x0:x1], shown)
    if difference is not None and difference < _SAME_CONTENT_SHARE:
        return True
    # Anywhere on the frame, at the size it had on its own frame: a slide scrolled up a few lines puts its chart lower,
    # and the chart a placed cut showed was not found at the same rectangle (DEFECTS 240). Over froscon 3211's pairs the
    # same part matched 0.985 and 0.996, other parts at most 0.24
    sized = cv2.resize(shown, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)

    return frame_region_utils.is_contained(sized, frame)


def list_placed_matches(paths: RunPaths,
                        needed_frames: list[str],
                        cuts: dict[str, dict],
                        earlier_slugs: list[str]) -> list[str]:
    """The needed frames whose part an earlier chapter already placed: `frame-022 shows what cut-003 shows`. Two slides of
    one deck can show one diagram under two titles, and the second cut of it was refused as a repeat after the chapter
    was written. The same rectangle is cropped from the needed frame and compared with each placed cut; only
    a near copy counts (_SAME_CONTENT_SHARE), so a table of the same template with other words is not named."""

    placed = sorted(list_placed_cut_ids(paths, earlier_slugs))
    # Each placed cut read once, not once per needed frame
    shown_by_cut = {cut_id: cv2.imread(str(paths.output_root / cuts[cut_id]["file"]))
                    for cut_id in placed if (cuts.get(cut_id) or {}).get("file")}
    matches = []
    for frame_id in needed_frames:
        frame = cv2.imread(str(_build_frame_path(paths, frame_id)))
        if frame is None:
            continue
        for cut_id in placed:
            cut = cuts.get(cut_id) or {}
            if _is_same_view(frame, frame_id, cut, shown_by_cut.get(cut_id)):
                matches.append(f"{frame_id} shows what {cut_id} (from {cut.get('frame', '')}) already shows")
                break

    return matches


def _has_enough_words(words: list[set[str]]) -> bool:
    """Whether the frame with fewer words holds enough of them for its text to tell."""

    return min(len(frame_words) for frame_words in words) >= _SAME_TEXT_MIN_WORDS


def _is_same_slide_text(paths: RunPaths,
                        first_id: str,
                        second_id: str) -> bool:
    """Whether two frames hold nearly the same words: at least _SAME_TEXT_SHARE of the words of 4+ letters of the frame
    with fewer are in the other."""

    words = [_read_frame_words(paths, frame_id) for frame_id in (first_id, second_id)]

    return _has_enough_words(words) and len(words[0] & words[1]) >= _SAME_TEXT_SHARE * len(min(words, key=len))


def _is_different_slide_text(paths: RunPaths,
                             first_id: str,
                             second_id: str) -> bool:
    """Whether both frames hold enough words to compare and those words are not nearly the same."""

    words = [_read_frame_words(paths, frame_id) for frame_id in (first_id, second_id)]

    return _has_enough_words(words) and not _is_same_slide_text(paths, first_id, second_id)


def _is_twin(paths: RunPaths,
             frame_id: str,
             earlier: str,
             frame_image: numpy.ndarray,
             earlier_image: numpy.ndarray) -> bool:
    """Whether a needed frame shows what an earlier one shows."""

    difference = frame_region_utils.compute_content_difference(frame_image, earlier_image)
    # Frames of other shapes are compared by their words: one slide was taken twice, once with the camera column the
    # crop left in, and the shapes kept the pixels from being compared
    if difference is None:
        return _is_same_slide_text(paths, frame_id, earlier)
    if difference >= _SAME_CONTENT_SHARE:
        return False
    # One page scrolled to other lines differs in few pixels when the scrolled part is small: frames alike in pixels
    # that both hold enough words, and other ones, are no twins (an editor's first lines and lines far
    # below were named twins)
    return not _is_different_slide_text(paths, frame_id, earlier)


def list_twin_frames(paths: RunPaths,
                     needed_frames: list[str],
                     earlier_frames: list[str],
                     placed_frames: dict[str, str] | None = None) -> list[str]:
    """The needed frames that show what an earlier needed frame of the same chapter shows: a slide shown twice is two
    frames, and a chapter cut and placed both, a repeat refused after writing. An earlier frame an earlier
    part of the chapter already placed (`placed_frames`, frame to cut) is named with its cut, so the later frame is left
    out: told only "place one of the two", a worker could not know which one was placed already."""

    images = {frame_id: cv2.imread(str(_build_frame_path(paths, frame_id)))
              for frame_id in [*earlier_frames, *needed_frames]}
    twins = []
    for position, frame_id in enumerate(needed_frames):
        for earlier in [*earlier_frames, *needed_frames[:position]]:
            first, second = images.get(frame_id), images.get(earlier)
            if first is None or second is None or earlier == frame_id:
                continue
            if _is_twin(paths, frame_id, earlier, first, second):
                placed = (placed_frames or {}).get(earlier)
                twins.append(f"{frame_id} shows what {earlier} shows, placed already as {placed}: leave {frame_id} out"
                             if placed else f"{frame_id} shows what {earlier} shows: place one of the two")
                break

    return twins
