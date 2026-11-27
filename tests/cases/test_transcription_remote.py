"""Speech to text through a model server. REQUIREMENTS.md R12, plan step 4.

With `providers.speech.url` set, `transcription_service.produce_transcript` sends the recording's audio, compressed, to
the server on port 0 and builds the same transcript the in-process engine builds. A long recording is cut into windows
whose times are moved back onto the recording's clock. A word the server gives no `probability` is unknown, never 0.
"""

import contextlib
import json
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy

from support import harness
from support import fake_model_server

import config
from services import audio_extraction_service
from services import audio_upload_service
from services import endpoint_client
from services import faster_whisper_client
from services import speech_server_service
from services import transcription_service

PATH = "/v1/audio/transcriptions"
SAMPLE_RATE = 16000


def _write_wav(seconds: float) -> Path:
    """A 16 kHz mono 16-bit WAV of a tone, as phase 1's extraction writes one."""

    path = Path(tempfile.mkdtemp()) / "audio.wav"
    steps = numpy.arange(int(seconds * SAMPLE_RATE))
    tone = (numpy.sin(steps * 2 * numpy.pi * 440 / SAMPLE_RATE) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(tone.tobytes())

    return path


@contextlib.contextmanager
def _speech_url_set(server: fake_model_server.FakeModelServer) -> Iterator[None]:
    """`providers.speech.url` pointed at `server` for the block."""

    previous_url = config.settings.providers.speech.url
    config.settings.providers.speech.url = server.build_url()
    try:
        with fake_model_server.serving(server):
            yield
    finally:
        config.settings.providers.speech.url = previous_url


def _build_settings() -> config.AudioSettings:
    settings = config.settings.audio.model_copy(deep=True)
    settings.engine = faster_whisper_client.ENGINE_NAME
    settings.language = "en"

    return settings


def _build_reply(words: list[dict],
                 text: str = "hello world") -> bytes:
    """A `verbose_json` reply with one segment that spans the words."""

    segment = {"id": 0, "start": words[0]["start"], "end": words[-1]["end"], "text": text}

    return json.dumps({"language": "en", "duration": words[-1]["end"], "text": text, "segments": [segment],
                       "words": words}).encode("utf-8")


class _InProcessWhisper:
    """The in-process engine answering what the server's reply says: one segment, two words, a probability each."""

    def __init__(self, *arguments, **keywords):
        pass

    def transcribe(self, audio_path: str, **keywords):
        words = [SimpleNamespace(word=" hello", start=0.0, end=1.0, probability=0.9),
                 SimpleNamespace(word=" world", start=1.0, end=2.0, probability=0.8)]
        segment = SimpleNamespace(text="hello world", start=0.0, end=2.0, words=words)

        return iter([segment]), SimpleNamespace(language="en", language_probability=None)


def _transcribe_in_process(settings: config.AudioSettings,
                           wav: Path) -> dict:
    previous = faster_whisper_client.MODEL_FACTORY
    faster_whisper_client.set_model_factory(_InProcessWhisper)
    try:
        return transcription_service.produce_transcript(wav, settings)
    finally:
        faster_whisper_client.set_model_factory(previous)


def test_the_server_gives_the_same_transcript_the_in_process_engine_gives():
    settings = _build_settings()
    wav = _write_wav(3.0)
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 200, _build_reply([{"word": "hello", "start": 0.0, "end": 1.0, "probability": 0.9},
                                              {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.8}]))
    with _speech_url_set(server):
        remote = transcription_service.produce_transcript(wav, settings)
    in_process = _transcribe_in_process(settings, wav)
    harness.check("the transcript is the same dict, key for key", remote == in_process,
                  f"remote {remote} / in process {in_process}")
    sent = server.request_list[0]
    fields = {name: content.decode("utf-8") for name, file, content in sent.read_multipart_fields() if file is None}
    harness.check("one POST to the transcription route", [(r.method, r.path) for r in server.request_list] == [("POST", PATH)],
                  str([(r.method, r.path) for r in server.request_list]))
    harness.check("it asks for verbose_json in the settings' language", fields.get("response_format") == "verbose_json"
                  and fields.get("language") == "en", str(fields))
    files = [(name, file, content) for name, file, content in sent.read_multipart_fields() if file is not None]
    harness.check("the audio goes as one compressed Ogg file, never the WAV",
                  len(files) == 1 and files[0][1].endswith(".ogg") and files[0][2][:4] == b"OggS"
                  and len(files[0][2]) < wav.stat().st_size // 4, str([(f[1], len(f[2])) for f in files]))


def test_a_long_recording_is_sent_in_windows_and_each_window_is_moved_to_its_place():
    settings = _build_settings()
    wav = _write_wav(2.5)
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 200, _build_reply([{"word": "hello", "start": 0.1, "end": 0.4, "probability": 0.9},
                                              {"word": "there", "start": 0.4, "end": 0.9, "probability": 0.9}],
                                             text="hello there"))
    previous_window = audio_upload_service.set_window_s(1.0)
    try:
        with _speech_url_set(server):
            transcript = transcription_service.produce_transcript(wav, settings)
    finally:
        audio_upload_service.set_window_s(previous_window)
    harness.check("three requests, one for each second of the recording and its half",
                  len(server.request_list) == 3, str(len(server.request_list)))
    starts = [round(segment["start"], 2) for segment in transcript["segments"]]
    harness.check("each window's segment stands at the window's offset", starts == [0.1, 1.1, 2.1], str(starts))
    word_starts = [round(word["start"], 2) for word in transcript["segments"][2]["words"]]
    harness.check("and so do its words", word_starts == [2.1, 2.4], str(word_starts))
    lengths = []
    for request in server.request_list:
        audio = [content for name, file, content in request.read_multipart_fields() if file is not None][0]
        folder = Path(tempfile.mkdtemp())
        (folder / "window.ogg").write_bytes(audio)
        lengths.append(round(audio_extraction_service.read_duration(folder / "window.ogg"), 1))
    harness.check("the windows are 1, 1 and 0.5 seconds long", lengths == [1.0, 1.0, 0.5], str(lengths))
    harness.check("the transcript text joins the windows' text", transcript["text"] == "hello there hello there hello there",
                  transcript["text"])


def test_a_word_the_server_gives_no_probability_is_kept_and_unknown():
    settings = _build_settings()
    server = fake_model_server.FakeModelServer()
    with _speech_url_set(server):
        transcript = transcription_service.produce_transcript(_write_wav(3.0), settings)
    words = transcript["segments"][0]["words"]
    harness.check("both words are kept", [word["word"].strip() for word in words] == ["hello", "world"], str(words))
    harness.check("the one without a probability says it is unknown, not 0.0",
                  (words[0]["probability"], words[1]["probability"]) == (0.9, None), str(words))
    harness.check("the text keeps its words", transcript["text"] == "hello world", transcript["text"])


def test_words_with_no_probability_are_not_taken_for_invented_speech_defect():
    # Defect: both Whisper clean-ups read a missing probability as 0.0, so a server that gives none lost every segment
    # whose words take no time, and every run of 3 words, as "invented" or "unheard". Fix: a word with none is unknown
    # and is left to stay
    zero_length_words = [{"word": word, "start": 1.0, "end": 1.0} for word in ("one", "two", "three", "four")]
    settings = _build_settings()
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 200, _build_reply(zero_length_words, text="one two three four"))
    with _speech_url_set(server):
        transcript = transcription_service.produce_transcript(_write_wav(2.0), settings)
    harness.check("the segment of four words with no probability and no length is kept",
                  len(transcript["segments"]) == 1 and len(transcript["segments"][0]["words"]) == 4, str(transcript))
    known_low = {"word": "x", "start": 1.0, "end": 1.0, "probability": 0.1}
    harness.check("a segment of words that DO carry a low probability is still invented speech",
                  transcription_service.is_invented_segment({"words": [known_low, known_low]}))
    unheard = {"words": [{"word": f" w{n}", "start": 0.0, "end": 0.1, "probability": 0.001} for n in range(3)],
               "text": " w0 w1 w2"}
    harness.check("three words with a known probability under 0.01 are still dropped",
                  transcription_service.drop_unheard_words(unheard)["words"] == [], str(unheard))
    harness.check("a word with a probability of exactly 0.0 is known, and counts",
                  transcription_service.is_invented_segment(
                      {"words": [{"word": "x", "start": 1.0, "end": 1.0, "probability": 0.0}]}))


def test_words_listed_once_for_the_reply_go_to_the_segment_they_fall_in_and_words_listed_in_a_segment_stay_there():
    flat = {"language": "de",
            "segments": [{"start": 0.0, "end": 1.0, "text": " eins"}, {"start": 1.0, "end": 2.0, "text": " zwei"}],
            "words": [{"word": "eins", "start": 0.2, "end": 0.5, "probability": 0.9},
                      {"word": "zwei", "start": 1.2, "end": 1.6, "probability": 0.8}]}
    nested = {"language": "de", "segments": [{"start": 0.0, "end": 1.0, "text": " eins",
                                              "words": [{"text": " eins", "start": 0.2, "end": 0.5}]}]}
    settings = _build_settings()
    wav = _write_wav(2.0)
    placed = {}
    for name, reply in (("flat", flat), ("nested", nested)):
        server = fake_model_server.FakeModelServer()
        server.set_reply(PATH, 200, json.dumps(reply).encode("utf-8"))
        with _speech_url_set(server):
            endpoint = endpoint_client.find_endpoint(kind="speech")
            placed[name] = speech_server_service.produce_remote_reply(audio_path=wav, settings=settings, endpoint=endpoint)
    flat_words = [[word["word"] for word in segment["words"]] for segment in placed["flat"]["segments"]]
    harness.check("each listed word is in the segment its middle falls in, with a space before it as Whisper writes it",
                  flat_words == [[" eins"], [" zwei"]], str(flat_words))
    harness.check("the language is the server's", placed["flat"]["language"] == "de", str(placed["flat"]["language"]))
    nested_words = placed["nested"]["segments"][0]["words"]
    harness.check("a word read from `text` inside its segment is kept, its probability unknown",
                  nested_words == [{"word": " eins", "start": 0.2, "end": 0.5, "probability": None}], str(nested_words))


def test_a_server_that_fails_is_reported_with_its_address_and_status():
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 500, b'{"error": "model not loaded"}')
    failure = None
    with _speech_url_set(server):
        try:
            transcription_service.produce_transcript(_write_wav(1.0), _build_settings())
        except RuntimeError as failed:
            failure = str(failed)
    harness.check("the failure names the URL and HTTP 500", failure is not None and server.build_url() in failure
                  and "HTTP 500" in failure, str(failure))
