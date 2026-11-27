"""Phase 1's one question: what was said, and when. One engine answers it, chosen by name.

THE SEAM IS THE RETURNED DICTIONARY, NOT THE MODEL OBJECT. Phase 2 cuts its per-frame slices from
`segments[].words[]`, and the validator reads the same file, so anything that can produce this shape
is a valid engine:

    {"language": str | None,              what the engine detected, or what was forced
     "languageProbability": float | None, None when the engine does not report one
     "engine": str,                       which engine produced it
     "model": str,                        the model or repo id it used
     "text": str,                         every segment's text, joined
     "segments": [{"text", "start", "end",
                   "words": [{"word", "start", "end", "probability"}]}]}

`engine` and `model` are recorded because without them a stored transcript cannot be attributed, and
comparing two engines on the same file is the whole reason there is more than one.

Word timings are always requested: the frame slices are cut from them.

An engine's OWN arguments stay inside its own module. `vad_filter` belongs to faster-whisper and
`path_or_hf_repo` to MLX, and neither appears here.
"""

from __future__ import annotations

import types
from pathlib import Path

from config import AudioSettings
from models.pipeline_errors import EngineUnavailable
from services import endpoint_client
from services import faster_whisper_client
from services import mlx_whisper_client
from services import speech_server_service

__all__ = ["ENGINES", "is_invented_segment", "drop_unheard_words", "produce_transcript", "read_engine", "describe_available_engine"]


# Engine name -> the module implementing it. `config.AudioSettings.engine` selects the row, and the
# suite registers a fake here rather than imitating any real engine's argument list.
ENGINES: dict[str, types.ModuleType] = {
    faster_whisper_client.ENGINE_NAME: faster_whisper_client,
    mlx_whisper_client.ENGINE_NAME: mlx_whisper_client,
}


def read_engine(name: str) -> types.ModuleType:
    """The module for `name`, or a refusal naming every engine there is."""

    if name not in ENGINES:
        raise EngineUnavailable(f"no transcription engine named {name!r}. Known: {', '.join(sorted(ENGINES))}")

    return ENGINES[name]


def describe_available_engine(settings: AudioSettings) -> str:
    """One line describing the engine a run will use, or a raise if it cannot run at all."""

    endpoint = endpoint_client.find_endpoint(kind="speech")
    if endpoint is not None:
        return f"audio: the speech server {endpoint.base_url} with model {endpoint.model or settings.whisper_model}"

    # Asked BEFORE the audio is extracted. An optional package that is missing is a refusal at the
    # start of a run, never a failure after the expensive part is already paid for.
    engine = read_engine(settings.engine)
    is_installed, why_not = engine.is_installed()
    if not is_installed:
        raise EngineUnavailable(why_not)

    return f"audio: {settings.engine} with model {settings.whisper_model}"


# A segment is invented when half its words or more take no time and its words' mean probability is under this: over 11
# public runs 42 of 10,674 segments, nearly all words over music or at a window's edge ("Thank you." three times in a
# second, credit lines over a talk's intro, "I can be a little bit going / on / to go")
INVENTED_ZERO_SHARE = 0.5
INVENTED_MAX_PROBABILITY = 0.6


def _is_known_under(word: dict,
                    limit: float) -> bool:
    """Whether the word has a probability and it is under `limit`. A word with none (a speech server that gives none) is
    unknown, not 0.0: nothing says it was not heard."""

    probability = word.get("probability")

    return probability is not None and probability < limit


def is_invented_segment(segment: dict) -> bool:
    """Whether Whisper made the segment up rather than heard it: half its words or more end where they start and the mean
    probability of the words that have one is under INVENTED_MAX_PROBABILITY. A segment with no word timings, or whose
    words have no probability at all, is kept."""

    words = segment.get("words") or []
    known = [word["probability"] for word in words if word.get("probability") is not None]
    if not words or not known:
        return False
    zero_share = sum(1 for word in words if word["end"] - word["start"] <= 0) / len(words)

    return zero_share >= INVENTED_ZERO_SHARE and sum(known) / len(known) < INVENTED_MAX_PROBABILITY


# A run of at least UNHEARD_RUN words each under UNHEARD_MAX_PROBABILITY was not heard: in the middle of an English
# talk Whisper wrote words of other languages ("output每 одного downloads"), each under 0.003, and a batch asked readers
# what they meant. Over 8 public runs (76,159 words) the 4 runs at these values were all such words or beside them; at
# 0.05 a real phrase of another talk would have gone (measured once, offline)
UNHEARD_MAX_PROBABILITY = 0.01
UNHEARD_RUN = 3


def drop_unheard_words(segment: dict) -> dict:
    """`segment` without each run of UNHEARD_RUN words or more under UNHEARD_MAX_PROBABILITY, its text written again
    from the words left; a segment with no such run, or with no word timings, as it is."""

    words = segment.get("words") or []
    unheard = [_is_known_under(word=word, limit=UNHEARD_MAX_PROBABILITY) for word in words]
    dropped = set()
    start = None
    for index, is_unheard in enumerate(unheard + [False]):
        if is_unheard and start is None:
            start = index
        elif not is_unheard and start is not None:
            if index - start >= UNHEARD_RUN:
                dropped.update(range(start, index))
            start = None
    if not dropped:
        return segment
    kept = [word for index, word in enumerate(words) if index not in dropped]

    return {**segment, "words": kept, "text": "".join(word["word"] for word in kept)}


def produce_transcript(audio_path: Path,
                       settings: AudioSettings) -> dict:
    """What was said, from the configured engine. Audio with no speech is an empty segment list,
    which is an answer: every frame is still analysed, with nothing said around it."""

    endpoint = endpoint_client.find_endpoint(kind="speech")
    if endpoint is None:
        engine = read_engine(settings.engine)
        reply = engine.produce_transcript(audio_path, settings)
    else:
        reply = speech_server_service.produce_remote_reply(audio_path=audio_path, settings=settings, endpoint=endpoint)
    # Whisper returns zero-length segments with no text at window edges, and now and then an end a
    # few hundredths before its start; one such segment made the transcript check refuse the file.
    segments = [drop_unheard_words(segment) for segment in reply["segments"] if not is_invented_segment(segment)]
    segments = [{**segment, "end": max(segment["end"], segment["start"])} for segment in segments
                if segment["text"].strip()]

    return {"language": reply["language"],
            "languageProbability": reply["languageProbability"],
            "engine": settings.engine,
            "model": reply["model"],
            "text": " ".join(segment["text"].strip() for segment in segments),
            "segments": segments}
