"""Choosing a transcription engine, and the contract both of them return.

No model is downloaded and no GPU is touched: the MLX engine is driven through its own seam with a
fake that returns the shape `mlx_whisper.transcribe` returns, recorded from its real signature.
"""

import tempfile
from pathlib import Path

from support import harness
from support.media_fixtures import FakeWhisperModel

import config
from services import faster_whisper_client
from services import mlx_whisper_client
from services import transcription_service
from models.pipeline_errors import EngineUnavailable

# What `mlx_whisper.transcribe(..., word_timestamps=True)` returns: openai-whisper's shape, a dict per
# segment, `words` a list of dicts, and NO language probability anywhere in it.
MLX_REPLY = {
    "text": " Guten Abend Welt",
    "language": "de",
    "segments": [
        {"id": 0, "start": 0.0, "end": 0.9, "text": " Guten Abend",
         "words": [{"word": " Guten", "start": 0.0, "end": 0.4, "probability": 0.91},
                   {"word": " Abend", "start": 0.4, "end": 0.9, "probability": 0.88}]},
        {"id": 1, "start": 0.9, "end": 1.4, "text": " Welt", "no_speech_prob": 0.1},
    ],
}


def _make_mlx_transcriber(reply: dict = None) -> tuple:
    """A fake that records the keywords it was called with and answers with an MLX-shaped reply."""

    calls = []

    def transcribe(audio, **keywords):
        calls.append({**keywords, "sampleCount": len(audio)})

        return reply if reply is not None else MLX_REPLY

    return transcribe, calls


def _make_audio_loader(seconds: float):
    """Samples instead of a file, so nothing is decoded and no audio has to exist."""

    def load_audio(path, sr=mlx_whisper_client.SAMPLE_RATE):
        return [0.0] * int(seconds * sr)

    return load_audio


def _transcribe_with_mlx(settings: config.AudioSettings,
                         reply: dict = None,
                         seconds: float = 60.0) -> tuple:
    transcriber, calls = _make_mlx_transcriber(reply)
    previous_transcriber = mlx_whisper_client.set_transcriber(transcriber)
    previous_loader = mlx_whisper_client.set_audio_loader(_make_audio_loader(seconds))
    try:
        return transcription_service.produce_transcript(Path("audio.wav"), settings), calls
    finally:
        mlx_whisper_client.set_transcriber(previous_transcriber)
        mlx_whisper_client.set_audio_loader(previous_loader)


def _mlx_settings(**overrides) -> config.AudioSettings:
    return config.AudioSettings(engine="mlx", **overrides)


def test_the_default_engine_is_faster_whisper():
    harness.check("nothing changes for an existing run", config.AudioSettings().engine == "faster-whisper",
                  config.AudioSettings().engine)


def test_both_engines_are_registered_and_an_unknown_one_raises():
    harness.check("both engines are in the table", sorted(transcription_service.ENGINES) == ["faster-whisper", "mlx"],
                  str(sorted(transcription_service.ENGINES)))
    try:
        transcription_service.read_engine("whisper.cpp")
        harness.check("an unknown engine raises", False, "it returned instead")
    except EngineUnavailable as exc:
        harness.check("an unknown engine raises, naming the engines there are",
                      "whisper.cpp" in str(exc) and "faster-whisper" in str(exc) and "mlx" in str(exc), str(exc))


def test_an_unknown_engine_is_refused_by_the_settings_before_any_run():
    try:
        config.AudioSettings(engine="whisper.cpp")
        harness.check("the settings refuse an engine that does not exist", False, "it was accepted")
    except Exception as exc:
        harness.check("the settings refuse an engine that does not exist", "engine" in str(exc), str(exc)[:200])


def test_each_engine_only_accepts_its_own_settings():
    """The reason the settings are split: `extra=forbid` would otherwise reject the other engine's."""

    settings = config.AudioSettings(engine="mlx", mlx={"noSpeechThreshold": 0.4})
    harness.check("mlx settings bind in camelCase", settings.mlx.no_speech_threshold == 0.4,
                  str(settings.mlx.no_speech_threshold))
    harness.check("and faster-whisper's own keys keep their defaults, unused",
                  settings.faster_whisper.vad_min_silence_ms == 500, str(settings.faster_whisper))
    try:
        config.AudioSettings(mlx={"vadMinSilenceMs": 500})
        harness.check("a faster-whisper key under mlx is refused", False, "it was accepted")
    except Exception as exc:
        harness.check("a faster-whisper key under mlx is refused", "vad" in str(exc).lower(), str(exc)[:200])


def test_mlx_is_asked_for_word_timings_and_a_repo_id():
    _, calls = _transcribe_with_mlx(_mlx_settings())
    harness.check("one call was made", len(calls) == 1, str(calls))
    asked = calls[0]
    harness.check("word timings are always requested", asked["word_timestamps"] is True, str(asked))
    harness.check("the model size became a repo id",
                  asked["path_or_hf_repo"] == "mlx-community/whisper-large-v3", str(asked))
    harness.check("the language is detected, not forced", asked["language"] is None, str(asked))
    harness.check("NO faster-whisper argument is passed",
                  "vad_filter" not in asked and "beam_size" not in asked, str(asked))


def test_a_repo_id_in_the_model_setting_is_passed_through():
    _, calls = _transcribe_with_mlx(_mlx_settings(whisper_model="mlx-community/whisper-large-v3-turbo"))
    harness.check("an explicit repo id is used as given",
                  calls[0]["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo", str(calls[0]))


def test_a_model_size_mlx_has_no_repository_for_is_refused_by_name():
    try:
        _transcribe_with_mlx(_mlx_settings(whisper_model="enormous"))
        harness.check("an unmapped size raises", False, "it returned instead")
    except ValueError as exc:
        harness.check("an unmapped size raises, listing the sizes that do resolve",
                      "enormous" in str(exc) and "large" in str(exc), str(exc))


def test_mlx_returns_the_same_contract_as_faster_whisper():
    transcript, _ = _transcribe_with_mlx(_mlx_settings())
    harness.check("every contract key is present",
                  set(transcript) == {"language", "languageProbability", "engine", "model", "text", "segments"},
                  str(sorted(transcript)))
    harness.check("the detected language is carried", transcript["language"] == "de", str(transcript["language"]))
    harness.check("mlx reports no language probability, and that is None not a crash",
                  transcript["languageProbability"] is None, str(transcript["languageProbability"]))
    harness.check("word timings survive with their probabilities",
                  transcript["segments"][0]["words"][0] == {"word": " Guten", "start": 0.0, "end": 0.4,
                                                            "probability": 0.91},
                  str(transcript["segments"][0]["words"][0]))
    harness.check("a segment with no words gets an empty list, not a crash",
                  transcript["segments"][1]["words"] == [], str(transcript["segments"][1]))
    harness.check("the text is the segments joined by the service, not the engine's own",
                  transcript["text"] == "Guten Abend Welt", repr(transcript["text"]))


def test_the_transcript_records_which_engine_and_model_wrote_it():
    """Without this a stored transcript cannot be attributed, and comparing engines is the whole point."""

    mlx_transcript, _ = _transcribe_with_mlx(_mlx_settings())
    harness.check("mlx names itself", mlx_transcript["engine"] == "mlx", str(mlx_transcript["engine"]))
    harness.check("and the repo id it used", mlx_transcript["model"] == "mlx-community/whisper-large-v3",
                  str(mlx_transcript["model"]))

    FakeWhisperModel.calls.clear()
    previous = faster_whisper_client.MODEL_FACTORY
    faster_whisper_client.set_model_factory(FakeWhisperModel)
    try:
        fw_transcript = transcription_service.produce_transcript(Path("audio.wav"), config.AudioSettings())
    finally:
        faster_whisper_client.set_model_factory(previous)
    harness.check("faster-whisper names itself", fw_transcript["engine"] == "faster-whisper",
                  str(fw_transcript["engine"]))
    harness.check("and the size it was given", fw_transcript["model"] == "large", str(fw_transcript["model"]))
    harness.check("both engines return the same keys", set(mlx_transcript) == set(fw_transcript),
                  str(sorted(set(mlx_transcript) ^ set(fw_transcript))))


def test_no_speech_is_an_empty_transcript_on_mlx_too():
    transcript, _ = _transcribe_with_mlx(_mlx_settings(), reply={"text": "", "language": "de", "segments": []})
    harness.check("no speech is an empty segment list and empty text",
                  transcript["segments"] == [] and transcript["text"] == "", str(transcript))


def test_the_engine_is_checked_before_a_run_starts():
    """The check exists so a missing optional package refuses at the start, not after the audio has
    been extracted and the expensive part paid for."""

    line = transcription_service.describe_available_engine(config.AudioSettings())
    harness.check("the line names the engine and the model",
                  "faster-whisper" in line and "large" in line, line)
    previous = mlx_whisper_client.set_transcriber(None)
    try:
        is_installed, why_not = mlx_whisper_client.is_installed()
        harness.check("when mlx is absent the reason names the package to install",
                      is_installed or "mlx-whisper" in why_not, why_not or "it reported installed")
    finally:
        mlx_whisper_client.set_transcriber(previous)


def test_long_audio_is_handed_over_in_windows():
    """macOS kills a Metal command buffer that blocks the interface, so one call cannot hold 87
    minutes. Measured: 4.5 minutes finished, 87 minutes died with kIOGPUCommandBufferCallbackError."""

    settings = _mlx_settings(mlx={"chunkSeconds": 300})
    _, calls = _transcribe_with_mlx(settings, seconds=1250.0)
    harness.check("1250 seconds at 300 per window is five calls", len(calls) == 5, str(len(calls)))
    counts = [call["sampleCount"] for call in calls]
    harness.check("four full windows and a short last one",
                  counts == [300 * 16000] * 4 + [50 * 16000], str(counts))


def test_chunking_off_is_one_call_with_everything():
    _, calls = _transcribe_with_mlx(_mlx_settings(mlx={"chunkSeconds": 0}), seconds=1250.0)
    harness.check("chunkSeconds 0 means one call", len(calls) == 1, str(len(calls)))
    harness.check("carrying every sample", calls[0]["sampleCount"] == 1250 * 16000, str(calls[0]["sampleCount"]))


def test_a_window_shifts_its_times_onto_the_whole_file_clock():
    """Phase 2 cuts frame slices from these numbers, so an unshifted window would put every later
    frame against the wrong speech."""

    transcript, calls = _transcribe_with_mlx(_mlx_settings(mlx={"chunkSeconds": 300}), seconds=600.0)
    harness.check("two windows", len(calls) == 2, str(len(calls)))
    starts = [segment["start"] for segment in transcript["segments"]]
    harness.check("the first window keeps its own times, the second is shifted by 300",
                  starts == [0.0, 0.9, 300.0, 300.9], str(starts))
    words = transcript["segments"][2]["words"]
    harness.check("word timings are shifted too, not just the segment",
                  (words[0]["start"], words[0]["end"]) == (300.0, 300.4), str(words[0]))


def test_a_segment_with_no_text_is_dropped_and_an_end_before_its_start_is_raised_to_it():
    """Measured on an 87-minute meeting: whisper returned 190 zero-length segments with no text, and one
    whose end lay 0.02 s before its start, and the transcript check refused the whole file for it."""

    reply = {"text": "", "language": "de",
             "segments": [{"start": 1.0, "end": 1.0, "text": ""},
                          {"start": 2.0, "end": 2.0, "text": "   "},
                          {"start": 5.16, "end": 5.14, "text": " Welt"}]}
    transcript, _ = _transcribe_with_mlx(_mlx_settings(), reply=reply)
    segments = transcript["segments"]
    harness.check("the segments with no text are gone", [segment["text"] for segment in segments] == [" Welt"],
                  str(segments))
    harness.check("the end is raised to the start", (segments[0]["start"], segments[0]["end"]) == (5.16, 5.16),
                  str(segments[0]))


def test_the_language_is_detected_once_and_forced_on_every_later_window():
    """A quiet window mid-meeting otherwise detects as English and comes back translated."""

    _, calls = _transcribe_with_mlx(_mlx_settings(mlx={"chunkSeconds": 300}), seconds=900.0)
    harness.check("three windows", len(calls) == 3, str(len(calls)))
    harness.check("the first window detects", calls[0]["language"] is None, str(calls[0]["language"]))
    harness.check("every later window is forced to what it found",
                  [call["language"] for call in calls[1:]] == ["de", "de"],
                  str([call["language"] for call in calls[1:]]))


def test_a_forced_language_is_honoured_on_the_first_window_too():
    _, calls = _transcribe_with_mlx(config.AudioSettings(engine="mlx", language="de",
                                                        mlx={"chunkSeconds": 300}), seconds=600.0)
    harness.check("a forced language is sent from the first call",
                  [call["language"] for call in calls] == ["de", "de"],
                  str([call["language"] for call in calls]))


def test_the_default_window_is_short_enough_for_the_watchdog():
    harness.check("chunking is on by default", config.AudioSettings().mlx.chunk_seconds == 300.0,
                  str(config.AudioSettings().mlx.chunk_seconds))


def test_a_segment_whisper_made_up_is_dropped():
    """Over a talk's intro music Whisper wrote a credit line ("Jonas Reviewer. AcmeSync Reviewer.") its words taking no
    time at a low probability, and the worker took it for the speaker's name (38c3-fault-injection, 2026-10-15)."""

    def word(text, start, end, probability):
        return {"word": text, "start": start, "end": end, "probability": probability}

    made_up = {"text": " Jonas Reviewer. AcmeSync Reviewer.", "start": 0.0, "end": 19.1,
               "words": [word(" Jonas", 0.0, 0.66, 0.06), word(" Reviewer.", 0.66, 0.72, 0.24),
                         word(" AcmeSync", 3.44, 3.44, 0.15), word(" Reviewer.", 3.44, 3.44, 0.83)]}
    heard = {"text": " Thank you all for being here.", "start": 19.1, "end": 21.0,
             "words": [word(" Thank", 19.1, 19.3, 0.9), word(" you", 19.3, 19.4, 0.95), word(" all", 19.4, 19.6, 0.9),
                       word(" for", 19.6, 19.7, 0.9), word(" being", 19.7, 19.9, 0.9), word(" here.", 19.9, 20.2, 0.9)]}
    sure_but_short = {**made_up, "words": [word(" Yes.", 1.0, 1.0, 0.9), word(" Yes.", 1.0, 1.4, 0.9)]}
    harness.check("half its words taking no time, at a low probability: made up",
                  transcription_service.is_invented_segment(made_up))
    harness.check("words with their own time: heard", not transcription_service.is_invented_segment(heard))
    harness.check("zero-length words read with confidence: heard",
                  not transcription_service.is_invented_segment(sure_but_short))
    harness.check("a segment with no word timings is kept",
                  not transcription_service.is_invented_segment({"text": " x", "start": 0, "end": 1}))


def test_a_run_of_words_whisper_did_not_hear_is_dropped_from_its_segment():
    """In the middle of an English talk Whisper wrote words of other languages, each at a probability under 0.003, and a
    batch asked readers what they meant (Lecture 9, 2026-10-21). A run of 3 words or more under 0.01 is dropped and the
    segment's text is written from the words left; over 8 public runs, 76,159 words, only such runs were found."""

    def word(text, probability):
        return {"word": text, "start": 1.0, "end": 1.2, "probability": probability}

    segment = {"text": " search for it zut alors banane and then run it", "start": 0.0, "end": 5.0,
               "words": [word(" search", 0.9), word(" for", 0.9), word(" it", 0.8), word(" zut", 0.004),
                         word(" alors", 0.0001), word(" banane", 0.002), word(" and", 0.7), word(" then", 0.9),
                         word(" run", 0.9), word(" it", 0.9)]}
    kept = transcription_service.drop_unheard_words(segment)
    harness.check("the run of three is gone, the text rebuilt from the words left",
                  kept["text"] == " search for it and then run it" and len(kept["words"]) == 7, str(kept))
    unsure = {**segment, "words": [word(" search", 0.9), word(" fur", 0.004), word(" it", 0.9)]}
    harness.check("one unsure word alone stays", transcription_service.drop_unheard_words(unsure)["words"]
                  == unsure["words"])
    harness.check("a segment with no word timings is kept as it is",
                  transcription_service.drop_unheard_words({"text": " x", "start": 0, "end": 1}) == {"text": " x",
                                                                                                    "start": 0, "end": 1})
