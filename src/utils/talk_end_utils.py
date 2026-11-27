"""Where a talk ends in its transcript: the end of the last segment that says more than thanks (DEFECTS 173).

A broadcast closes on the stream's own cards (its noise transition, its credits and licence) after the last word of the
talk; phase 2 took both as frames, the speech over them nothing but thanks, and phase 3 spent a call on each.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["find_talk_end", "find_talk_start"]

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Words of thanks and applause, in the languages the transcripts hold: a segment of only these closes the talk
_THANKS_WORDS = frozenset({"thank", "thanks", "you", "very", "much", "so", "a", "lot", "again", "applause", "bye",
                           "okay", "ok", "yeah", "yes", "danke", "schön", "sehr", "vielen", "dank", "merci", "gracias",
                           "and", "everyone", "everybody", "all", "speaker"})
# A segment holding at least this many other words is still the talk
_TALK_WORDS = 3
# ... unless the speech model gave its words this mean probability or less: words written over a closing card nobody
# said. Release test 2's talk ended on "Danke", then two segments of 7 and 6 words at a mean 0.065 and 0.071 ran over
# the licence card, which phase 3 got (DEFECTS 413). Of the public runs' 15,039 segments of 3 words or more, 7 fall
# under 0.1, all but one words nobody said, the one a lecture's line in the middle of the talk (0.077)
_HEARD_MEAN_PROBABILITY = 0.1


def _is_heard(segment: dict[str, Any]) -> bool:
    probabilities = [float(word["probability"]) for word in segment.get("words") or [] if word.get("probability") is not None]

    return not probabilities or sum(probabilities) / len(probabilities) > _HEARD_MEAN_PROBABILITY


def _is_substance(segment: dict[str, Any]) -> bool:
    """Whether a segment says more than thanks: at least `_TALK_WORDS` words that are no thanks, its words heard."""

    words = [word.casefold() for word in _WORD.findall(str(segment.get("text") or ""))]

    return sum(word not in _THANKS_WORDS for word in words) >= _TALK_WORDS and _is_heard(segment)


def find_talk_end(segments: list[dict[str, Any]]) -> float | None:
    """The time, in seconds, the talk's last segment of substance ends: one with at least `_TALK_WORDS` words that are
    no thanks, its words heard (`_is_heard`). None when no segment has any."""

    return next((float(segment.get("end") or 0.0) for segment in reversed(segments) if _is_substance(segment)), None)


def find_talk_start(segments: list[dict[str, Any]]) -> float | None:
    """The time, in seconds, the first segment of substance starts, the same test as `find_talk_end`: before it a
    broadcast shows its opening animation, said over by nobody (DEFECTS 468). None when no segment has any."""

    return next((float(segment.get("start") or 0.0) for segment in segments if _is_substance(segment)), None)
