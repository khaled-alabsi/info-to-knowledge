"""The speech around each frame. Pure functions over the transcript's segments.

Frame `i` is on screen from its own timestamp to the next frame's. Two slices are cut for it:

  before   everything spoken from 0 to the start of its window
  current  what is spoken inside its window

Windows TILE the video: frame 0's window starts at 0 and the last frame's ends with the video, so
every word lands in exactly one `current` slice. Words are placed by their own start time when the
transcription gave word timings, and by their segment's start when it did not. A window ends where the
sentence said across the next frame's timestamp ends, at most `_SENTENCE_REACH_WORDS` words later, so a
frame's words never stop mid-sentence and the next frame's start with a whole one. A transcript with no punctuation
there ends the window at the next pause the speech model marked, the start of its next segment, within the same reach.

When phase 1 placed speakers, a slice names the speaker where its words start and wherever the speaker changes
(`Speaker A: … Speaker B: …`); a segment with no speaker (overlapping speech) is named by nobody and runs on after the
speaker before it. A transcript without speakers gives the same slices as before.
"""

from __future__ import annotations

__all__ = ["build_frame_slices"]

# The last frame's window runs this far past the reported end, so a word starting at or just past it still lands there
_END_MARGIN_S = 1.0
# How many words past the next frame's timestamp a window may reach to end its sentence: cut at the timestamp, the
# sentence's end was read as the next frame's, and three steps disagreed about which frame it belonged to (2026-10-13:
# "we never told it to store that result" | "anywhere, but …")
_SENTENCE_REACH_WORDS = 25
_SENTENCE_END = (".", "?", "!")


def _list_timed_words(segment: dict) -> list[tuple[float, str]]:
    """`(start, text)` per word of one segment, or the whole segment as one entry. A word keeps the
    spacing the speech model gave it: a LEADING space marks a new word, and a token with none, such
    as `-Gateway` after `AcmeSync`, continues the word before it."""

    words = segment.get("words") or []
    if not words:
        return [(float(segment["start"]), f" {str(segment.get('text', '')).strip()}")]

    return [(float(word["start"]), str(word["word"])) for word in words]


def _join_words(words: list[str]) -> str:
    """The tokens run together as the speech model spaced them, with runs of spaces collapsed."""

    return " ".join("".join(words).split())


def _extract_window_words(timed_segments: list[tuple[float, str | None, list[tuple[float, str]]]],
                          start_s: float,
                          end_s: float,
                          earliest_segment_end_s: float) -> str:
    """The words that start inside `[start_s, end_s)`, from each segment's end, speaker and timed words, each change of
    speaker named before its words. A segment that ended before `earliest_segment_end_s` is dropped WHOLE, so a cap on
    the past never cuts a sentence."""

    words = []
    named_speaker = None
    for segment_end, speaker, timed_words in timed_segments:
        if segment_end < earliest_segment_end_s:
            continue
        picked = [text for word_start, text in timed_words if start_s <= word_start < end_s]
        if picked and speaker and speaker != named_speaker:
            words.append(f" {speaker}:")
            named_speaker = speaker
        words.extend(picked)

    return _join_words(words)


def _move_to_sentence_end(words: list[tuple[float, str]],
                          boundary_s: float,
                          pauses: set[float]) -> float:
    """`boundary_s` moved to the start of the first word after the sentence in progress there ends, when that end is
    within `_SENTENCE_REACH_WORDS` words; with no sentence end there, to the first of `pauses` (segment starts) within
    that reach, since a transcript written with no punctuation (release test 1's "me tries to install" | "a snap
    package") ends no sentence at all; `boundary_s` itself when the words before it end a sentence, or neither is
    found."""

    before = [text for start, text in words if start < boundary_s]
    if not before or before[-1].strip().endswith(_SENTENCE_END):
        return boundary_s
    after = [(start, text) for start, text in words if start >= boundary_s]
    for position, (start, text) in enumerate(after[:_SENTENCE_REACH_WORDS]):
        if text.strip().endswith(_SENTENCE_END):
            # The last word of the whole speech ends the sentence: the window runs just past its start
            return after[position + 1][0] if position + 1 < len(after) else start + 0.001
    for start, _text in after[:_SENTENCE_REACH_WORDS]:
        if start in pauses:
            return start

    return boundary_s


def build_frame_slices(segments: list[dict],
                       timestamps: list[float],
                       duration_s: float,
                       before_seconds: float | None) -> list[dict]:
    """One `{"before": ..., "current": ...}` per frame, in frame order. `before_seconds` caps how far
    back `before` reaches; `None` sends everything spoken so far."""

    if timestamps != sorted(timestamps):
        raise ValueError(f"frame timestamps must be in chronological order, got {timestamps}")

    timed_segments = [(float(segment["end"]), segment.get("speaker"), _list_timed_words(segment)) for segment in segments]
    words = sorted((word for _end, _speaker, timed in timed_segments for word in timed), key=lambda word: word[0])
    # Each frame's window starts where the one before it ended, so the windows still tile the video
    pauses = {float(segment["start"]) for segment in segments}
    starts = [0.0] + [_move_to_sentence_end(words, timestamp, pauses) for timestamp in timestamps[1:]]
    starts = [max(start, starts[index - 1]) if index else start for index, start in enumerate(starts)]
    slices = []
    for index, timestamp in enumerate(timestamps):
        window_start = starts[index]
        is_last = index + 1 == len(timestamps)
        window_end = max(duration_s, timestamp) + _END_MARGIN_S if is_last else starts[index + 1]
        earliest = 0.0 if before_seconds is None else window_start - before_seconds
        slices.append({"before": _extract_window_words(timed_segments, 0.0, window_start, earliest),
                       "current": _extract_window_words(timed_segments, window_start, window_end, 0.0)})

    return slices
