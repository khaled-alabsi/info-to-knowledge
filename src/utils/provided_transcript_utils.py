"""The meeting tool's own transcript, a WebVTT or SubRip file beside the recording, read and compared with phase 1's.
REQUIREMENTS.md R14. Pure functions.

Measured in `research/transcript-sources/`: it is worse than phase 1 on names and terms, so it never replaces phase
1's text. What it adds is the speaker names its `<v Name>` tags carry, when it carries them.
"""

from __future__ import annotations

import re

__all__ = ["read_cues", "list_speakers", "compute_agreement", "count_words"]

AGREEMENT_WINDOW_SECONDS = 60
# WebVTT makes the hours optional on either side of the arrow
_TIMING = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
_VOICE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]+)>")
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[\w]+(?:['’]\w+)?")


def _to_seconds(hours: str | None,
                minutes: str,
                seconds: str,
                millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _list_words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _count_edits(reference: list[str],
                 hypothesis: list[str]) -> int:
    """Word-level edit distance: substitutions, insertions and deletions."""

    previous = list(range(len(hypothesis) + 1))
    for row, reference_word in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_word in enumerate(hypothesis, start=1):
            current.append(min(previous[column] + 1, current[column - 1] + 1,
                               previous[column - 1] + (reference_word != hypothesis_word)))
        previous = current

    return previous[-1]


def _list_timed_words(units: list[dict]) -> list[tuple[float, str]]:
    return [((unit["start"] + unit["end"]) / 2, word) for unit in units for word in _list_words(unit["text"])]


def read_cues(text: str) -> list[dict]:
    """Every cue as `{"start", "end", "text", "speaker"}`, times in seconds, tags removed from the text, the speaker
    the cue's `<v Name>` tag names or an empty string."""

    cues = []
    # A file saved on Windows may open with a byte-order mark, which is no part of the first line
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").lstrip("\ufeff")):
        lines = block.strip().splitlines()
        found = next(((index, match) for index, line in enumerate(lines) if (match := _TIMING.search(line))), None)
        if found is None:
            continue
        timing_line, match = found
        numbers = match.groups()
        body = " ".join(lines[timing_line + 1:])
        voice = _VOICE.search(body)
        cues.append({"start": _to_seconds(*numbers[:4]), "end": _to_seconds(*numbers[4:]),
                     "text": " ".join(_TAG.sub("", body).split()), "speaker": voice.group(1).strip() if voice else ""})

    return cues


def list_speakers(cues: list[dict]) -> list[str]:
    """The speakers the cues name, the one who said the most words first."""

    words: dict[str, int] = {}
    for cue in cues:
        if cue["speaker"]:
            words[cue["speaker"]] = words.get(cue["speaker"], 0) + len(_list_words(cue["text"]))

    return sorted(words, key=lambda speaker: -words[speaker])


def count_words(cues: list[dict]) -> int:
    return sum(len(_list_words(cue["text"])) for cue in cues)


def compute_agreement(cues: list[dict],
                      segments: list[dict]) -> float | None:
    """1 minus the word error rate of phase 1's segments against the cues, over one-minute windows so that a long
    meeting stays cheap to compare. 1.0 when both hold the same words at the same times; None when the cues hold no
    word to compare."""

    windows: dict[int, tuple[list[str], list[str]]] = {}
    for side, words in enumerate((_list_timed_words(cues), _list_timed_words(segments))):
        for time, word in words:
            windows.setdefault(int(time // AGREEMENT_WINDOW_SECONDS), ([], []))[side].append(word)
    edits = sum(_count_edits(reference, hypothesis) for reference, hypothesis in windows.values())
    total = sum(len(reference) for reference, _ in windows.values())

    return round(max(0.0, 1 - edits / total), 3) if total else None
