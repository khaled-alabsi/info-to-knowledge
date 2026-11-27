"""Speakers on a transcript's words and segments, from pyannote's turns (`services/diarization_service.py`). Pure:
times in, labelled segments out.

    word " Hallo" 0.1-0.4, exclusive turn SPEAKER_01 0.0-2.0         -> word speaker "Speaker A"
    word " ja" 3.0-3.2, turns SPEAKER_00 and SPEAKER_01 both over it  -> word speaker None (overlapping speech)

- **A word takes the exclusive turn covering most of it**, and no speaker (`None`) when no turn covers it or when a
  second speaker's turn, among the overlapping ones, covers `overlap_share` of it: speech two people say at once is
  left without a speaker rather than given to one of them.
- **A segment is split where its words' speaker changes.** A word with no speaker stays in the piece it falls in and
  keeps `None` itself; a piece whose words all have none has `speaker: None`. A segment without word timings is placed
  by its own times.
- **Labels are `Speaker A`, `Speaker B` …, in the order the speakers first speak**, the form phase 2's speech format
  already uses (`speech_markdown_utils.SPEAKER_PATTERN`). A 27th speaker and later have no letter and get `None`.
"""

from __future__ import annotations

import string

__all__ = ["add_speakers", "name_turns", "list_speakers"]

_LETTERS = string.ascii_uppercase


def _compute_overlap_seconds(start: float,
                     end: float,
                     turn: dict) -> float:
    """Seconds of `start`-`end` inside the turn; a word of no length counts 1 when its moment is inside."""

    if end <= start:
        return 1.0 if turn["start"] <= start <= turn["end"] else 0.0

    return max(0.0, min(end, turn["end"]) - max(start, turn["start"]))


def _compute_cover_by_label(start: float,
                        end: float,
                        turns: list[dict]) -> dict[str, float]:
    covered: dict[str, float] = {}
    for turn in turns:
        seconds = _compute_overlap_seconds(start, end, turn)
        if seconds > 0:
            covered[turn["label"]] = covered.get(turn["label"], 0.0) + seconds

    return covered


def _find_label(start: float,
                end: float,
                diarization: dict,
                overlap_share: float) -> str | None:
    """The pyannote label of the speaker of `start`-`end`, or None for no turn or overlapping speech."""

    exclusive = _compute_cover_by_label(start, end, diarization["exclusiveTurns"])
    if not exclusive:
        return None
    label = max(exclusive, key=exclusive.get)
    length = max(end - start, 0.0) or 1.0
    others = [seconds for other, seconds in _compute_cover_by_label(start, end, diarization["turns"]).items()
              if other != label]
    if any(seconds / length >= overlap_share for seconds in others):
        return None

    return label


def _split_by_label(segment: dict,
                    labelled_words: list[tuple[dict, str | None]]) -> list[dict]:
    """The segment's pieces, one per run of one label; a word with no label stays in the run it falls in."""

    runs: list[tuple[str | None, list[tuple[dict, str | None]]]] = []
    for word, label in labelled_words:
        if runs and (label is None or label == runs[-1][0] or runs[-1][0] is None):
            run_label = runs[-1][0] if label is None else label
            runs[-1] = (run_label, [*runs[-1][1], (word, label)])
        else:
            runs.append((label, [(word, label)]))
    if len(runs) == 1:
        return [{**segment, "label": runs[0][0], "words": [{**word, "label": label} for word, label in runs[0][1]]}]

    return [{"text": "".join(word["word"] for word, _label in run),
             "start": run[0][0]["start"], "end": max(run[-1][0]["end"], run[0][0]["start"]),
             "label": run_label, "words": [{**word, "label": label} for word, label in run]}
            for run_label, run in runs]


def _name_labels(pieces: list[dict]) -> dict[str, str | None]:
    """Each pyannote label's `Speaker <letter>`, lettered in the order the labels first speak."""

    names: dict[str, str | None] = {}
    for piece in sorted(pieces, key=lambda piece: piece["start"]):
        for label in [piece["label"], *(word["label"] for word in piece.get("words") or [])]:
            if label is not None and label not in names:
                names[label] = f"Speaker {_LETTERS[len(names)]}" if len(names) < len(_LETTERS) else None

    return names


def _split_segments_by_label(segments: list[dict],
                             diarization: dict,
                             overlap_share: float) -> list[dict]:
    """Each segment's pieces, one per run of one pyannote label, every word carrying its label."""

    pieces: list[dict] = []
    for segment in segments:
        words = segment.get("words") or []
        if not words:
            pieces.append({**segment, "label": _find_label(segment["start"], segment["end"], diarization,
                                                           overlap_share)})
            continue
        labelled = [(word, _find_label(word["start"], word["end"], diarization, overlap_share)) for word in words]
        pieces.extend(_split_by_label(segment, labelled))

    return pieces


def add_speakers(segments: list[dict],
                 diarization: dict,
                 overlap_share: float) -> list[dict]:
    """The segments with `speaker` on every segment and word, split where the speaker changes; `diarization` is
    `{"turns", "exclusiveTurns"}` as `diarization_service.compute_speaker_turns` returns it."""

    # STEP 1: each word's pyannote label, and each segment's pieces by label
    pieces = _split_segments_by_label(segments, diarization, overlap_share)

    # STEP 2: the labels renamed Speaker A, B … in the order they first speak
    names = _name_labels(pieces)

    return [{**{key: value for key, value in piece.items() if key not in ("label", "words")},
             "speaker": names.get(piece["label"]) if piece["label"] is not None else None,
             **({"words": [{**{key: value for key, value in word.items() if key != "label"},
                            "speaker": names.get(word["label"]) if word["label"] is not None else None}
                           for word in piece["words"]]} if "words" in piece else {})}
            for piece in pieces]


def name_turns(segments: list[dict],
               diarization: dict,
               overlap_share: float) -> dict[str, list[dict]]:
    """pyannote's turns, both kinds, each `{"start", "end", "speaker"}` named as `add_speakers` names the same
    segments' speakers; a label no word was given has no name (None)."""

    names = _name_labels(_split_segments_by_label(segments, diarization, overlap_share))

    return {kind: [{"start": turn["start"], "end": turn["end"], "speaker": names.get(turn["label"])}
                   for turn in diarization[kind]] for kind in ("turns", "exclusiveTurns")}


def list_speakers(segments: list[dict]) -> list[str]:
    """The speakers the segments name, in the order they first speak."""

    speakers: list[str] = []
    for segment in segments:
        if segment.get("speaker") and segment["speaker"] not in speakers:
            speakers.append(segment["speaker"])

    return speakers
