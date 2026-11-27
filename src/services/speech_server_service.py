"""Speech to text by a model server: the recording's audio sent to `providers.speech.url`, the reply made into what an
in-process engine gives.

The server is asked the standard way (`POST /v1/audio/transcriptions`, `verbose_json`, word and segment timestamps), so
any server that answers it can stand in. Its reply is turned into the reply `transcription_service` describes: a language,
a model and segments, each with its words. Three things the in-process engines never needed are done here:

- A long recording is cut into windows (`audio_upload_service`), each sent on its own and moved back to its place on the
  recording's clock, so no request is larger than a server takes.
- A word the server gives no `probability` keeps `None`: it is unknown, and the two clean-ups in `transcription_service`
  leave it alone. Reading it as 0.0 made them throw away every segment of such a server.
- A server that lists its words once for the whole reply (the OpenAI shape) has them placed into the segment they fall in;
  a server that lists them inside each segment is read as it is.
"""

from __future__ import annotations

from pathlib import Path

from config import AudioSettings
from models.endpoint_spec import EndpointSpec
from services import audio_upload_service
from services import endpoint_client
from services import recording_parts_service

__all__ = ["produce_remote_reply"]

GRANULARITIES = ["word", "segment"]


def _build_word(raw: dict) -> dict | None:
    """One word as the in-process engines give it (a space before it, as faster-whisper writes its words), or None when the
    server gave it no start or end: a word with no place in time cannot be cut into a frame's slice."""

    if not isinstance(raw.get("start"), (int, float)) or not isinstance(raw.get("end"), (int, float)):
        return None
    text = str(raw.get("word", raw.get("text", "")))
    if not text.startswith(" "):
        text = " " + text.strip()

    return {"word": text, "start": float(raw["start"]), "end": float(raw["end"]), "probability": raw.get("probability")}


def _list_words(raw_list: list) -> list[dict]:
    word_list = []
    for raw in raw_list:
        word = _build_word(raw=raw) if isinstance(raw, dict) else None
        if word is not None:
            word_list.append(word)

    return word_list


def _find_segment_index(segment_list: list[dict],
                        word: dict) -> int:
    """The place of the segment `word` falls in: the first whose span holds the word's middle, else the one nearest to it."""

    middle = (word["start"] + word["end"]) / 2
    nearest, nearest_gap = 0, float("inf")
    for index, segment in enumerate(segment_list):
        if segment["start"] <= middle <= segment["end"]:
            return index
        gap = min(abs(middle - segment["start"]), abs(middle - segment["end"]))
        if gap < nearest_gap:
            nearest, nearest_gap = index, gap

    return nearest


def _describe_segments(reply: dict) -> list[dict]:
    """The reply's segments as `transcription_service` describes them, each with its words."""

    segment_list = []
    for raw in reply["segments"]:
        words = _list_words(raw_list=raw["words"]) if isinstance(raw.get("words"), list) else []
        segment_list.append({"text": str(raw.get("text", "")), "start": float(raw["start"]), "end": float(raw["end"]),
                             "words": words, "has_words": isinstance(raw.get("words"), list)})
    flat_words = _list_words(raw_list=reply["words"]) if isinstance(reply.get("words"), list) else []
    for word in flat_words:
        owner = segment_list[_find_segment_index(segment_list=segment_list, word=word)] if segment_list else None
        if owner is not None and not owner["has_words"]:
            owner["words"].append(word)
    for segment in segment_list:
        del segment["has_words"]

    return segment_list


def produce_remote_reply(audio_path: Path,
                         settings: AudioSettings,
                         endpoint: EndpointSpec) -> dict:
    """What an engine's `produce_transcript` gives (a language, its probability, the model and the segments) for the WAV at
    `audio_path`, from the speech server `endpoint`. A failed request raises `EndpointError`, which names the address and
    the status."""

    language = ""
    language_probability = None
    segment_list: list[dict] = []
    for offset_s, audio in audio_upload_service.list_windows(wav_path=audio_path):
        reply = endpoint_client.request_transcription(endpoint=endpoint, audio=audio, language=settings.language or "",
                                                      granularity_list=GRANULARITIES)
        window = {"segments": _describe_segments(reply=reply)}
        segment_list += recording_parts_service.move_transcript_times(transcript=window, offset_s=offset_s)["segments"]
        if not language and reply.get("language"):
            language = str(reply["language"])
            language_probability = reply.get("language_probability")

    return {"language": language or settings.language,
            "languageProbability": language_probability,
            "model": endpoint.model or settings.whisper_model,
            "segments": segment_list}
