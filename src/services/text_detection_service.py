"""The words on a frame and the blocks of text they form, found by Tesseract. REQUIREMENTS.md R10.

Phase 4's agent is shown a frame with the program's boxes on it as anchor points; the text blocks are most of them.
The same words tell the program when a cut's edge runs through a line of text. Tesseract runs locally as a command,
as the research scripts already run it; a machine without it gets no text boxes, and the tools say so, rather than
failing the phase. Text reading runs inside the program on the processor; no model server is asked for it.
"""

from __future__ import annotations

import csv
import functools
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy

import config
from utils import frame_region_utils

__all__ = ["SCREEN_TEXT_MIN_CONFIDENCE", "read_figure_words", "is_same_wording", "count_shared_words",
           "find_words", "find_words_in_image", "read_tsv_words", "group_text_blocks",
           "is_available", "fill_masks_with_background", "list_missing_languages", "set_language_lister",
           "set_recording_language", "TESSERACT_LANGUAGES", "set_words_cache_dir"]

_LOG = logging.getLogger(__name__)

_TESSERACT = "tesseract"
# A word Tesseract is less sure of than this is noise: a shadow, an icon, a grid line.
_MIN_CONFIDENCE = 40.0
# The words a frame's outline is checked against are read surer than that: phase 3's check and phase 2's full-size
# reading (`screen_words_service`) read at this one confidence
SCREEN_TEXT_MIN_CONFIDENCE = 70.0
_TIMEOUT_S = 60
# How much an image is enlarged before it is read
_SCALE = 2
# A small image is read larger, up to this many times, so that it is read at about `_READ_WIDTH` pixels: a 640-pixel
# editor frame of a 360p recording read 2 words at twice its size and 17 at four times
_MAX_SCALE = 4
_READ_WIDTH = 2560


def _choose_scale(width: int) -> int:
    """How many times an image `width` pixels wide is enlarged before it is read: 2 at least, 4 at most."""

    return max(_SCALE, min(_MAX_SCALE, -(-_READ_WIDTH // max(width, 1))))
# The words read from each file, by its path, size, change time and confidence floor: the check for a figure placed
# twice compares every pair of placed cuts by their words, and read each cut again for every pair, one Tesseract run at
# a time; a resume stood minutes on it before its first call. A file changed since (a mask) is read again,
# and a read that failed is never kept.
_WORDS_BY_FILE: dict[tuple[str, int, int, float, str], list[dict]] = {}
# Where the words read are also kept on disk, by the file's content, when set (`set_words_cache_dir`): the integration
# tests read the frames of real recordings anew in each process, 1.3 to 5 s a frame, and five of their files ran past
# five minutes each (DEFECTS 499). None (the program's runs) keeps them in memory only
_WORDS_CACHE_DIR: Path | None = None


def set_words_cache_dir(folder: Path | None) -> None:
    """Keep each file's words on disk in `folder`, by its content, so a later process reads none again; None stops."""

    global _WORDS_CACHE_DIR
    _WORDS_CACHE_DIR = folder


def _build_disk_key(frame_path: Path,
                    min_confidence: float) -> Path | None:
    """The file in the words cache for this file's content, confidence floor and languages; None with no cache set."""

    if _WORDS_CACHE_DIR is None:
        return None
    digest = hashlib.sha256(Path(frame_path).read_bytes())
    digest.update(f"{float(min_confidence)}|{_select_languages()}".encode())

    return _WORDS_CACHE_DIR / f"{digest.hexdigest()}.json"


# Past this share of pure black, black is the image's background, not a mask
_MAX_MASK_SHARE = 0.6
# More lines than this in one block and the block is listed line by line
_MAX_BLOCK_LINES = 6
# The words of a block kept as its text, which `look` lists beside the block's box
_BLOCK_TEXT_WORDS = 8
# A word this long or longer read one letter off in another figure is the same word misread: an umlaut came back as
# "ii" in one cut and "il" in the other of one warning box
_NEAR_WORD_MIN_LETTERS = 5


def _group_by(words: list[dict],
              key) -> dict:
    groups: dict = {}
    for word in words:
        groups.setdefault(key(word), []).append(word)

    return groups


def _list_word_runs(words_of_line: list[dict]) -> list[tuple[dict, int]]:
    """Each word of one line, left to right, with the number of its run: a new run starts after a gap wider than
    PANE_GAP_HEIGHTS of the word's height."""

    numbered, run = [], 0
    words_left_to_right = sorted(words_of_line, key=lambda word: word["box"][0])
    for previous, word in zip([None] + words_left_to_right, words_left_to_right):
        gap = frame_region_utils.PANE_GAP_HEIGHTS * max(word["box"][3] - word["box"][1], 1)
        if previous is not None and word["box"][0] - previous["box"][2] > gap:
            run += 1
        numbered.append((word, run))

    return numbered


def _is_one_edit_apart(word: str,
                       other: str) -> bool:
    """One letter changed, added or dropped."""

    if abs(len(word) - len(other)) > 1:
        return False
    if len(word) == len(other):
        return sum(left != right for left, right in zip(word, other)) == 1
    shorter, longer = (word, other) if len(word) < len(other) else (other, word)

    return any(longer[:index] + longer[index + 1:] == shorter for index in range(len(longer)))


@functools.lru_cache(maxsize=1)
def _list_installed_languages() -> tuple[str, ...]:
    """The languages Tesseract has a model for on this machine; none when it is not installed or will not say."""

    try:
        listed = subprocess.run([_TESSERACT, "--list-langs"], capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ()

    return tuple(line.strip() for line in listed.stdout.splitlines()[1:] if line.strip())


# Lists the languages Tesseract has a model for; a test hands its own (`set_language_lister`)
LANGUAGE_LISTER: Callable[[], tuple[str, ...]] = _list_installed_languages


def set_language_lister(lister: Callable[[], tuple[str, ...]]) -> None:
    global LANGUAGE_LISTER
    LANGUAGE_LISTER = lister


def list_missing_languages(languages: list[str] | None = None) -> list[str]:
    """The languages of `languages` (`frames.textReaderLanguages` when None) that Tesseract has no model for here: the
    setup check reports them, and the reader reads with the rest."""

    wanted = config.settings.frames.text_reader_languages if languages is None else languages

    return [language for language in wanted if language not in LANGUAGE_LISTER()]


@functools.lru_cache(maxsize=None)
def _warn_missing(languages: tuple[str, ...]) -> None:
    _LOG.warning("Tesseract has no model for %s, so text is read without it (frames.textReaderLanguages)",
                 ", ".join(languages))


# Tesseract's model of each language phase 1 can hear, by Whisper's code
TESSERACT_LANGUAGES = {"en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "nl": "nld", "pt": "por",
                       "pl": "pol", "tr": "tur", "ar": "ara"}
# The language phase 1 heard in the recording being processed, by Whisper's code; "" until a run sets it
_RECORDING_LANGUAGE = ""


def set_recording_language(language: str) -> None:
    """The language the run's recording is spoken in (`01_transcript.json`'s `language`): its text is read in it as
    well as in English, when `frames.textReaderLanguages` lists it."""

    global _RECORDING_LANGUAGE
    _RECORDING_LANGUAGE = str(language or "")


def _select_languages() -> str:
    """The `-l` value for Tesseract: English, plus the recording's own language when the settings list it and it has a
    model here, joined with "+"; "" leaves Tesseract's own default (English). Read in both on an English lecture, the
    German model read three words of a slide differently on two frames of it, and a frame was kept as new; so a
    second language is read only where it is spoken. A language with no model is left out with one warning."""

    allowed = config.settings.frames.text_reader_languages
    wanted = [language for language in ("eng", TESSERACT_LANGUAGES.get(_RECORDING_LANGUAGE, "")) if language in allowed]
    missing = list_missing_languages(wanted)
    if missing:
        _warn_missing(tuple(missing))

    return "+".join(dict.fromkeys(language for language in wanted if language not in missing))


def _run_tesseract(image: numpy.ndarray,
                   scale: int,
                   page_mode: int | None = None) -> str | None:
    """Tesseract's tab-separated output for `image`, read on a copy `scale` times its size, with Tesseract's page
    segmentation mode `page_mode` when given; None when it could not run, ran past its time or exited with an error."""

    # Small interface text is read on an enlarged copy, and its boxes are brought back to the frame's pixels
    # (one word read on a cut of a settings page whose every line was plain to see)
    with tempfile.TemporaryDirectory() as folder:
        enlarged = Path(folder) / "enlarged.png"
        cv2.imwrite(str(enlarged), cv2.resize(fill_masks_with_background(image), None, fx=scale, fy=scale,
                                              interpolation=cv2.INTER_CUBIC))
        try:
            mode = ["--psm", str(page_mode)] if page_mode is not None else []
            languages = _select_languages()
            language = ["-l", languages] if languages else []
            finished = subprocess.run([_TESSERACT, str(enlarged), "stdout", *language, *mode, "tsv"], capture_output=True, text=True,
                                      timeout=_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            _LOG.warning("Tesseract did not read a %dx%d image at scale %d (%s)", image.shape[1], image.shape[0], scale,
                         type(error).__name__)
            return None
    if finished.returncode != 0:
        _LOG.warning("Tesseract exited with %d on a %dx%d image at scale %d: %s", finished.returncode, image.shape[1],
                     image.shape[0], scale, (finished.stderr or "").strip().splitlines()[:1])

    return finished.stdout if finished.returncode == 0 else None


def _read_words_in_image(image: numpy.ndarray,
                         min_confidence: float,
                         is_read_larger: bool = False,
                         page_mode: int | None = None) -> list[dict] | None:
    """The words on `image` in its own pixels: empty when Tesseract is not installed, None when its run failed."""

    if not is_available():
        return []
    # A small image is read at twice its size and at the larger scale, and the reading with more words is kept: four
    # times lost a word of a line that twice read whole, and found 17 words on a frame where twice found 2
    readings = []
    # Only a stored frame or cut, read by phases 3 and 4: phase 2's screen decisions are tuned on the twice-size count
    # and read every second of a recording (with it, its integration tests failed 5 stills and ran 6 min against 35 s)
    for scale in dict.fromkeys((_SCALE, _choose_scale(image.shape[1]) if is_read_larger else _SCALE)):
        tsv = _run_tesseract(image, scale, page_mode)
        if tsv is None:
            return None
        readings.append([{**word, "box": [value // scale for value in word["box"]]}
                         for word in read_tsv_words(tsv, min_confidence)])

    return max(readings, key=len)


def _read_cached_words(frame_path: Path,
                       min_confidence: float) -> list[dict] | None:
    """The words on the file, read once per version of it; None when the file or Tesseract's run failed."""

    try:
        stat = Path(frame_path).stat()
    except OSError:
        return None
    key = (str(Path(frame_path).resolve()), stat.st_size, stat.st_mtime_ns, float(min_confidence), _select_languages())
    if key not in _WORDS_BY_FILE:
        disk_key = _build_disk_key(frame_path, min_confidence)
        if disk_key is not None and disk_key.is_file():
            _WORDS_BY_FILE[key] = json.loads(disk_key.read_text(encoding="utf-8"))
        else:
            image = cv2.imread(str(frame_path))
            words = None if image is None else _read_words_in_image(image, min_confidence, is_read_larger=True)
            if words is None:
                return None
            _WORDS_BY_FILE[key] = words
            if disk_key is not None:
                disk_key.parent.mkdir(parents=True, exist_ok=True)
                disk_key.write_text(json.dumps(words), encoding="utf-8")

    # Copies, so a caller that changes a word never changes what the next caller reads
    return [{**word, "box": list(word["box"])} for word in _WORDS_BY_FILE[key]]


def is_available() -> bool:
    """Whether words can be read here: Tesseract is installed."""

    return shutil.which(_TESSERACT) is not None


def fill_masks_with_background(image: numpy.ndarray) -> numpy.ndarray:
    """`image` with its pure black, which is what a mask draws, painted in the median colour of the rest. On a dark cut
    with a black mask, Tesseract read no word at all, the unmasked lines included."""

    black = image.max(axis=2) == 0
    if not black.any() or black.mean() > _MAX_MASK_SHARE:
        return image
    painted = image.copy()
    painted[black] = numpy.median(image[~black], axis=0).astype(image.dtype)

    return painted


def find_words(frame_path: Path,
               min_confidence: float = _MIN_CONFIDENCE) -> list[dict]:
    """Every word Tesseract reads on the frame: `{"text", "box", "block", "line"}`, `box` in the frame's pixels,
    `block` the number of the text block it belongs to and `line` the number of its line. Empty when Tesseract is not
    installed or fails."""

    return _read_cached_words(frame_path, min_confidence) or []


def find_words_in_image(image: numpy.ndarray,
                        min_confidence: float = _MIN_CONFIDENCE,
                        page_mode: int | None = None) -> list[dict]:
    """`find_words` on an image held in memory, with Tesseract's page segmentation mode `page_mode` when given."""

    return _read_words_in_image(image, min_confidence, page_mode=page_mode) or []


def read_tsv_words(tsv: str,
                   min_confidence: float = _MIN_CONFIDENCE) -> list[dict]:
    """The words of Tesseract's tab-separated output at `min_confidence` or above. A quote mark is part of a word, never
    a field's quoting. A check for something that must never slip through reads with no floor: an e-mail address on a
    cut was read at confidence 22 and dropped."""

    words = []
    for row in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text or float(row.get("conf") or -1) < min_confidence:
            continue
        left, top, width, height = (int(row[name]) for name in ("left", "top", "width", "height"))
        block = int(row.get("block_num") or 0) * 1000 + int(row.get("par_num") or 0)
        words.append({"text": text, "box": [left, top, left + width, top + height],
                      "block": block, "line": block * 1000 + int(row.get("line_num") or 0)})

    return words


def group_text_blocks(words: list[dict]) -> list[dict]:
    """The text blocks the words form: `{"box", "text"}`, `text` the block's first `_BLOCK_TEXT_WORDS` words, in reading
    order."""

    # A block past _MAX_BLOCK_LINES lines is listed line by line, each line split where a gap wider than PANE_GAP_HEIGHTS of
    # its heights runs through it: on an editor the reader made one block of the whole file and of the file list beside it,
    # and the worker had no box to cut a few lines by
    # STEP 1: key each word by its block, or by its line and its run within the line in a block too long
    lines_per_block: dict[int, set] = {}
    for word in words:
        lines_per_block.setdefault(word["block"], set()).add(word.get("line"))
    block_key_by_word_id = {}
    for (block, line), members in _group_by(words, lambda word: (word["block"], word.get("line"))).items():
        if len(lines_per_block[block]) <= _MAX_BLOCK_LINES or line is None:
            block_key_by_word_id.update({id(word): (block,) for word in members})
            continue
        block_key_by_word_id.update({id(word): (block, line, run) for word, run in _list_word_runs(members)})

    # STEP 2: join the words of each key into one box and its text
    blocks: dict[tuple, dict] = {}
    for word in words:
        block = blocks.setdefault(block_key_by_word_id[id(word)], {"box": list(word["box"]), "words": []})
        block["box"] = [min(block["box"][0], word["box"][0]), min(block["box"][1], word["box"][1]),
                        max(block["box"][2], word["box"][2]), max(block["box"][3], word["box"][3])]
        block["words"].append(word["text"])
    blocks_in_reading_order = sorted(blocks.values(), key=lambda block: (block["box"][1], block["box"][0]))

    return [{"box": block["box"], "text": " ".join(block["words"][:_BLOCK_TEXT_WORDS])} for block in blocks_in_reading_order]


def read_figure_words(image_path: Path) -> set[str] | None:
    """The words of three letters or more a figure holds, case folded: what tells two figures of one layout apart. None
    when the text reader failed on it, which is never the same as a figure with no words."""

    words = _read_cached_words(image_path, _MIN_CONFIDENCE)

    return None if words is None else {word["text"].casefold() for word in words if len(word["text"]) >= 3}


def count_shared_words(fewer: set[str],
                       more: set[str]) -> int:
    """How many words of `fewer` the other figure holds, a word of five letters or more one letter off counted: the
    text reader misreads a letter differently in two cuts of one figure, and a box shown twice fell under the share
    that names it a part."""

    return sum(1 for word in fewer
               if word in more or (len(word) >= _NEAR_WORD_MIN_LETTERS
                                   and any(_is_one_edit_apart(word, other) for other in more)))


def is_same_wording(first: set[str] | None,
                    second: set[str] | None) -> bool:
    """Whether two figures read alike: at least half the words either holds are in both, or neither holds words. Two
    slides of one deck with the same table layout and other words in every cell matched pixel for pixel at the size
    figures are compared. A figure the text reader failed on reads like no other."""

    if first is None or second is None:
        return False
    if not first and not second:
        return True

    return len(first & second) * 2 >= len(first | second)
