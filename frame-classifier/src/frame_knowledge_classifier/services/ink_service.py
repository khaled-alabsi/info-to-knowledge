"""What is drawn on a frame besides its text: the measures the text-only task decides on.

Tesseract reads the words and their boxes; everything else drawn on the slide is "other ink". A frame holds only text
an OCR can carry when there is almost no other ink, no word of the body is in a second colour, and no fraction is
drawn, nor any box filled with a colour. Things of no value are not other ink: black bands along the edges (cropping, lighting), a slide's coloured
border, long plain rules, marks smaller than 40 x 40 pixels (a bullet, a small emoji), a vignette or a gradient
(they have no sharp edge), and a title line in its own colour.

Every measure is a number, so a rule can be chosen over them (`ink_rule_service`). Each dataset's measures are cached
in `.cache/<dataset>-ink.json` with each frame's file size and modification time, like the embeddings.
`set_measurer` replaces the measuring for the tests, which have no Tesseract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.ink_parts_record import InkPartsRecord

WIDTH = 1280                # every frame is measured at this width
INK_DISTANCE = 60           # grey levels from the background colour that make a pixel ink
FAINT_DISTANCE = 25         # a fainter pixel counts where it lies on a sharp edge (a pale outline, a table's pale cell lines)
RULE_SHARE = 0.35           # a plain rule, underline or border runs across at least this share of the slide
SMALL_MARK = 40             # marks smaller than this many pixels each way are of no value
EDGE_MARGIN_SHARE = 0.015   # the slide's outer margin, where a window's border or a crop's seam lies
LINE_DISTANCE = 12          # a table's cell line may be this pale and still count, where it runs straight and long
STEEP_SHARE = 0.08          # a filled region's edge rises at least this share of its own contrast per pixel
SOLID_CORE = 11             # ink still there after an erosion this many pixels wide is a solid shape, not a stroke
SOLID_SHARE = 0.3           # a box read as text whose ink keeps this share after the erosion is a filled block
TOP_BAR_SHARE = 0.09         # other ink lying wholly in the top 9% or the bottom 8% is a window's bars, a header or a
BOTTOM_BAR_SHARE = 0.08      # footer (the same bands info-to-knowledge leaves out of its text check)
CORNER_SHARE = 0.3           # other ink touching the frame's edge inside the outer 30% of both sides is decoration
EDGE_TOUCH_SHARE = 0.03      # "touching the edge": within this share of the side
TILE_MAX_SHARE = 0.25        # a camera tile, the other ink holding a face, covers at most this share of the frame
FACE_SCORE = 0.8             # YuNet's score for a face
FACE_MODEL = Path(__file__).resolve().parents[3] / "weights" / "face_detection_yunet_2023mar.onnx"  # beside the code
# words a code editor colours by their syntax: a body word in a second colour is meaning, these are not
CODE_KEYWORDS = frozenset("""def return for in if elif else import from class while try except finally raise with as
    pass break continue lambda yield async await function end local then do const let var new this self none true false
    null nil public private static void int float str bool print and or not is""".split())
OFF_COLOUR_DISTANCE = 18    # a word whose ink differs from the main text colour by more than this (a or b of Lab)
# the measures the rule decides on, each with the way it passes: "max" at or below its cut, "min" at or above it
MEASURES = {"otherInkShare": "max", "largestOtherShare": "max", "saturatedOtherShare": "max",
            "inlineOffColourWords": "max", "otherEdgeShare": "max", "stackedBars": "max", "fillShare": "max",
            "tableJunctions": "max", "words": "min"}

# frame path -> its measures
Measurer = Callable[[Path], dict]


def _find_edge_band(is_dark: numpy.ndarray) -> int:
    """How many lines from the edge belong to a dark band: the furthest line within a third of the side up to which
    all but 8 lines are dark, so a thin border line drawn round the frame does not end the band."""

    band, dark_count = 0, 0
    for index, is_line_dark in enumerate(is_dark[:len(is_dark) // 3]):
        dark_count += bool(is_line_dark)
        if is_line_dark and dark_count >= index + 1 - 8:
            band = index + 1

    return band if band > 8 else 0


def _crop_dark_edges(image: numpy.ndarray) -> numpy.ndarray:
    """The frame without bands of near-black rows or columns along its edges; a dark-theme slide, dark all over, is
    kept whole."""

    dark = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 45
    height, width = dark.shape
    if dark[height // 4:3 * height // 4, width // 4:3 * width // 4].mean() > 0.6:
        return image
    rows, cols = dark.mean(axis=1) > 0.9, dark.mean(axis=0) > 0.9
    top, bottom = _find_edge_band(rows), _find_edge_band(rows[::-1])
    left, right = _find_edge_band(cols), _find_edge_band(cols[::-1])

    return image[top:height - bottom, left:width - right]


def _find_background_colour(image: numpy.ndarray) -> numpy.ndarray:
    """The median colour of the pixels in the most common step of 16 grey levels per channel. Not the step's middle:
    white (255) and a light panel (240) share the step whose middle is 248, so a panel was within 8 levels of the
    "background" and never counted as filled."""

    pixels = image.reshape(-1, 3).astype(int)
    quantised = pixels // 16
    codes = quantised[:, 0] * 256 + quantised[:, 1] * 16 + quantised[:, 2]

    return numpy.median(pixels[codes == numpy.bincount(codes).argmax()], axis=0)


def _read_boxes(image: numpy.ndarray) -> list[tuple[int, int, int, int, str, bool, str]]:
    """Every box Tesseract reads with confidence 20 or more: x, y, width, height, its line's key, whether it is a
    word (confidence 45 or more, two letters or digits) rather than a symbol such as ">>>", and what it reads."""

    is_encoded, png = cv2.imencode(".png", image)
    if not is_encoded:
        raise RuntimeError("the frame could not be encoded for Tesseract")
    # Tesseract's reading of the same pixels is kept, so a change to the measures re-measures in seconds
    cached = config.CACHE_DIR / "tesseract" / f"{hashlib.sha1(png.tobytes()).hexdigest()}.tsv"
    if cached.exists():
        tsv = cached.read_text(encoding="utf-8")
    else:
        finished = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=png.tobytes(),
                                  capture_output=True, check=True)
        tsv = finished.stdout.decode("utf-8", errors="replace")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(tsv, encoding="utf-8")
    boxes = []
    for line in tsv.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or not parts[11].strip() or float(parts[10]) < 20:
            continue
        x, y, w, h = map(int, parts[6:10])
        is_word = float(parts[10]) >= 45 and sum(character.isalnum() for character in parts[11]) >= 2
        boxes.append((x, y, w, h, "-".join(parts[1:5]), is_word, parts[11].strip()))

    return boxes


def _find_faces(image: numpy.ndarray) -> list[tuple[int, int, int, int]]:
    """The faces YuNet finds with score 0.8 or more, each at least 2% of the frame wide: x, y, width, height."""

    height, width = image.shape[:2]
    detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (width, height), FACE_SCORE)
    _, faces = detector.detect(image)

    return [tuple(int(value) for value in face[:4]) for face in (faces if faces is not None else [])
            if face[2] >= 0.02 * width]


def _find_frame_parts(other: numpy.ndarray,
                      image: numpy.ndarray) -> numpy.ndarray:
    """1 inside the box of each piece of other ink that belongs to the frame and not to the slide: a camera tile (a
    piece holding a face, touching the frame's edge, at most a quarter of the frame), a window's bars, header or footer (a piece wholly in the top 9% or bottom 8%),
    and a corner's decoration (a piece touching the frame's edge inside the outer 30% of both sides)."""

    height, width = other.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(other, numpy.ones((7, 7), numpy.uint8)))
    faces = _find_faces(image)
    area = numpy.zeros_like(other)
    for index in range(1, count):
        x, y, w, h = stats[index, :4]
        touches = (x <= EDGE_TOUCH_SHARE * width or y <= EDGE_TOUCH_SHARE * height
                   or x + w >= (1 - EDGE_TOUCH_SHARE) * width or y + h >= (1 - EDGE_TOUCH_SHARE) * height)
        # a camera tile stands against the frame's edge; a photo of a person placed on the slide does not
        holds_face = touches and w * h <= TILE_MAX_SHARE * width * height and any(
            fx >= x and fy >= y and fx + fw <= x + w and fy + fh <= y + h for fx, fy, fw, fh in faces)
        is_bar = y + h <= TOP_BAR_SHARE * height or y >= (1 - BOTTOM_BAR_SHARE) * height
        is_corner = touches and (x + w <= CORNER_SHARE * width or x >= (1 - CORNER_SHARE) * width) and (
            y + h <= CORNER_SHARE * height or y >= (1 - CORNER_SHARE) * height)
        if holds_face or is_bar or is_corner:
            area[y:y + h, x:x + w] = 1

    return area


def _count_inline_off_colour_words(image: numpy.ndarray,
                                   word_ink: numpy.ndarray,
                                   words: list[tuple]) -> int:
    """The words of the body in a second colour: each word's ink colour against the colour most words have (one vote
    per word, so a large title does not outweigh the body); a whole line in its own colour is excused only when it is
    the top line, the slide's title."""

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(int)
    coloured = []
    for x, y, w, h, line in words:
        box = word_ink[y:y + h, x:x + w]
        if box.sum() >= 10:
            coloured.append((line, y, numpy.median(lab[y:y + h, x:x + w][box], axis=0)))
    if not coloured:
        return 0
    main = numpy.median(numpy.array([colour for _, _, colour in coloured]), axis=0)
    is_off, tops = {}, {}
    for line, y, colour in coloured:
        is_off.setdefault(line, []).append(bool(numpy.abs(colour[1:] - main[1:]).max() > OFF_COLOUR_DISTANCE))
        tops[line] = min(tops.get(line, y), y)
    top_line = min(tops, key=tops.get)

    return sum(sum(flags) for line, flags in is_off.items() if not (all(flags) and line == top_line))


def _count_stacked_bars(ink: numpy.ndarray,
                        words: list[tuple],
                        word_height: float) -> int:
    """Fractions: a short bar (under 40% of the slide, at most 6 pixels thick) with words right above and right below
    it, both centred on it."""

    width = ink.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(1.2 * word_height)), 1))
    count, _, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel))
    stacked = 0
    for index in range(1, count):
        x, y, w, h = stats[index, :4]
        if h > 6 or w > 0.4 * width:
            continue
        centre = x + w / 2
        sides = []
        for is_below in (False, True):
            side = []
            for wx, wy, ww, wh, _ in words:
                gap = wy - (y + h) if is_below else y - (wy + wh)
                if -0.5 * word_height <= gap <= 1.5 * word_height and wx < x + w and wx + ww > x:
                    side.append((wx, ww))
            sides.append(side)
        if all(sides) and all(abs((min(wx for wx, _ in side) + max(wx + ww for wx, ww in side)) / 2 - centre) < 0.25 * w
                              for side in sides):
            stacked += 1

    return stacked


def _count_table_junctions(distance: numpy.ndarray,
                           text: numpy.ndarray) -> int:
    """Where thin lines meet as a table's lines do: a horizontal and a vertical line, however pale (12 grey levels off
    the background), crossing or meeting in a T, so three or four arms leave the meeting point. A box drawn round text
    has only corners, two arms each. The lines are read outside the text and only where they are thin: a filled area
    (the dark stage round a slide, a band) is taken out with its rim, since inside one every point looks like a
    crossing (text frames had up to 107 junctions until it was; a table of three rows and columns has 12)."""

    height, width = distance.shape
    pale = ((distance > LINE_DISTANCE) & (text == 0)).astype(numpy.uint8)
    filled = cv2.morphologyEx(pale, cv2.MORPH_OPEN, numpy.ones((5, 5), numpy.uint8))
    pale = pale & (1 - cv2.dilate(filled, numpy.ones((5, 5), numpy.uint8)))
    horizontal = cv2.morphologyEx(pale, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, width // 20), 1)))
    vertical = cv2.morphologyEx(pale, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, height // 20))))
    meeting = cv2.dilate(horizontal, numpy.ones((3, 3), numpy.uint8)) & cv2.dilate(vertical, numpy.ones((3, 3), numpy.uint8))
    count, _, _, centres = cv2.connectedComponentsWithStats(meeting)
    junctions = 0
    for x, y in centres[1:].astype(int):
        arms = (horizontal[max(0, y - 2):y + 3, max(0, x - 15):max(0, x - 7)].any(),
                horizontal[max(0, y - 2):y + 3, x + 8:x + 16].any(),
                vertical[max(0, y - 15):max(0, y - 7), max(0, x - 2):x + 3].any(),
                vertical[y + 8:y + 16, max(0, x - 2):x + 3].any())
        junctions += int(sum(arms) >= 3)

    return junctions


def _measure_fill_share(image: numpy.ndarray,
                        distance: numpy.ndarray,
                        text: numpy.ndarray) -> float:
    """The share of the slide in filled boxes: regions of one even colour other than the background (most of their
    pixels, the words inside aside, within 12 grey levels of their median) whose outline departs from the background
    steeply: more than 3 grey levels a pixel, or more than `STEEP_SHARE` of the region's own distance from the
    background a pixel, so a light panel 15 levels off white counts (a callout, a cell, a kanban column; a vignette or
    a gradient rises under both)."""

    area = image.shape[0] * image.shape[1]
    smooth = cv2.GaussianBlur(distance.astype(numpy.float32), (5, 5), 0)
    steepness = numpy.hypot(cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)) / 8
    tint = cv2.morphologyEx((distance > 15).astype(numpy.uint8), cv2.MORPH_OPEN, numpy.ones((9, 9), numpy.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(tint)
    fill_area = 0
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 0.003 * area:
            continue
        region = labels == index
        colours = image[region & (text == 0)].astype(int)
        if len(colours) < 0.002 * area:
            continue
        if (numpy.abs(colours - numpy.median(colours, axis=0)).max(axis=1) <= 12).mean() < 0.7:
            continue
        contours, _ = cv2.findContours(region.astype(numpy.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        outline = numpy.zeros(region.shape, numpy.uint8)
        cv2.drawContours(outline, contours, -1, 1, 5)
        edge = numpy.median(steepness[outline > 0])
        if edge > 3 or edge > STEEP_SHARE * numpy.median(distance[region]):
            fill_area += stats[index, cv2.CC_STAT_AREA]

    return float(fill_area / area)


def find_ink_parts(path: Path) -> InkPartsRecord:
    """What the measures are taken from: the frame's text, the other ink, and what is set aside as of no value."""

    # STEP 1: the frame at the measuring width, without dark bands along its edges
    image = cv2.imread(str(path))
    image = _crop_dark_edges(cv2.resize(image, (WIDTH, round(image.shape[0] * WIDTH / image.shape[1]))))
    height, width = image.shape[:2]

    # STEP 2: ink, faint ink on sharp edges, and long plain rules
    distance = numpy.abs(image.astype(int) - _find_background_colour(image)).max(axis=2)
    ink = (distance > INK_DISTANCE).astype(numpy.uint8)
    edges = (cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 20, 60) > 0).astype(numpy.uint8)
    faint = ((distance > FAINT_DISTANCE) & (cv2.dilate(edges, numpy.ones((3, 3), numpy.uint8)) > 0)).astype(numpy.uint8)
    lines = (cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(width * RULE_SHARE), 1)))
             | cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(height * RULE_SHARE)))))
    lines = lines & (1 - cv2.morphologyEx(lines, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (6, 6))))

    # STEP 3: the text: every box read at the size of the words; a box much wider than tall is a line read as text, and
    # a box whose ink is solid is a filled block read as text (Tesseract read a row of light diagram boxes on a dark
    # slide as one confident word: they keep 51% to 58% of the box after the erosion, real words at most 16%)
    core = cv2.erode(ink, numpy.ones((SOLID_CORE, SOLID_CORE), numpy.uint8))
    boxes = [box for box in _read_boxes(image)
             if core[box[1]:box[1] + box[3], box[0]:box[0] + box[2]].mean() <= SOLID_SHARE]
    words = [box[:5] for box in boxes if box[5]]
    colour_words = [box[:5] for box in boxes if box[5] and box[6].strip("():;,.{}[]").lower() not in CODE_KEYWORDS]
    word_height = float(numpy.median([box[3] for box in words])) if words else 0.0
    text = numpy.zeros_like(ink)
    for x, y, w, h, _, is_word, _ in boxes:
        if is_word or (words and h <= 1.5 * word_height and w <= 6 * h):
            text[max(0, y - 3):y + h + 3, max(0, x - 3):x + w + 3] = 1

    # STEP 4: other ink: not text, not a rule nothing touches (dots on a line make a timeline), not the outer margin,
    # not a small mark
    count, line_labels = cv2.connectedComponents(lines)
    touching = cv2.dilate(ink & (1 - text) & (1 - lines), numpy.ones((9, 9), numpy.uint8))
    for index in range(1, count):
        if (touching[line_labels == index] > 0).sum() > 20:
            lines[line_labels == index] = 0
    other = (ink | faint) & (1 - text) & (1 - lines)
    margin_y, margin_x = max(1, int(height * EDGE_MARGIN_SHARE)), max(1, int(width * EDGE_MARGIN_SHARE))
    other[:margin_y], other[-margin_y:], other[:, :margin_x], other[:, -margin_x:] = 0, 0, 0, 0
    frame_parts = _find_frame_parts(other, image)
    other = other & (1 - frame_parts)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(other, numpy.ones((3, 3), numpy.uint8)))
    is_small = (stats[:, cv2.CC_STAT_WIDTH] < SMALL_MARK) & (stats[:, cv2.CC_STAT_HEIGHT] < SMALL_MARK)
    is_small[0] = True
    small = is_small[labels].astype(numpy.uint8)
    other = other & (1 - small)
    near_text = cv2.dilate(text, numpy.ones((5, 5), numpy.uint8))
    near_lines = cv2.dilate(lines, numpy.ones((5, 5), numpy.uint8))
    other_edges = edges & (1 - near_text) & (1 - near_lines) & (1 - small) & (1 - frame_parts)

    return InkPartsRecord(image=image, distance=distance, ink=ink, text=text, lines=lines, small=small, other=other,
                          other_edges=other_edges, words=words, colour_words=colour_words, word_height=word_height)


def measure_with_tesseract(path: Path) -> dict:
    """The measures of one frame (`MEASURES`)."""

    parts = find_ink_parts(path)
    image, ink, text, other, words = parts.image, parts.ink, parts.text, parts.other, parts.words
    area = image.shape[0] * image.shape[1]
    count, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(other, numpy.ones((7, 7), numpy.uint8)))
    largest = max((stats[index, 2] * stats[index, 3] for index in range(1, count)), default=0)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturated = (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 60)

    return {"words": len(words), "otherInkShare": float(other.sum() / area), "largestOtherShare": float(largest / area),
            "saturatedOtherShare": float((saturated & (other > 0)).sum() / area),
            "inlineOffColourWords": _count_inline_off_colour_words(image, (ink & text).astype(bool), parts.colour_words)
            if (ink & text).sum() > 50 else 0,
            "otherEdgeShare": float(parts.other_edges.sum() / area),
            "stackedBars": _count_stacked_bars(ink, words, parts.word_height) if words else 0,
            "fillShare": _measure_fill_share(image, parts.distance, text),
            "tableJunctions": _count_table_junctions(parts.distance, text)}


MEASURER: Measurer = measure_with_tesseract


def set_measurer(measurer: Measurer) -> Measurer:
    """Replaces the measuring and returns the one it replaced, so a test can put it back in `finally`."""

    global MEASURER
    previous = MEASURER
    MEASURER = measurer

    return previous


def _stamp(path: Path) -> str:
    status = path.stat()

    return f"{path.name}:{status.st_size}:{status.st_mtime_ns}"


def measure_dataset_frames(dataset_name: str,
                           paths: list[Path]) -> list[dict]:
    """The measures of every frame in `paths` (all of one dataset), in their order, from the cache where it holds an
    unchanged copy of the frame, else measured now."""

    cache_path = config.CACHE_DIR / f"{dataset_name}-ink.json"
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    stamps = [_stamp(path) for path in paths]
    missing = [(path, stamp) for path, stamp in zip(paths, stamps) if stamp not in cached]
    for path, stamp in missing:
        cached[stamp] = MEASURER(path)
    if missing:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({stamp: cached[stamp] for stamp in stamps}))

    return [cached[stamp] for stamp in stamps]


def measure_frames(paths: list[Path]) -> list[dict]:
    """The measures of every frame in `paths`, with no cache: for a folder outside the datasets."""

    return [MEASURER(path) for path in paths]
