"""Speaker turns through a model server. REQUIREMENTS.md R12, plan step 5.

With `providers.diarization.host` naming a host, `diarization_service.compute_speaker_turns` sends the meeting's audio, every part
at its offset and compressed, to the server on port 0 as one request, and returns the turns the server names in the form
the in-process model gives them. Nothing of pyannote or torch is used.
"""

import json
import tempfile
import wave
from pathlib import Path

import numpy

from support import harness
from support import fake_model_server

import config
from models.diarization_errors import DiarizationFailed, DiarizationUnavailable
from services import audio_extraction_service
from services import diarization_service

PATH = "/v1/audio/transcriptions"
SAMPLE_RATE = 16000
LOUD = 2000


def _write_wav(seconds: float) -> Path:
    """A 16 kHz mono 16-bit WAV of a tone, as phase 1's extraction writes one."""

    path = Path(tempfile.mkdtemp()) / "part.wav"
    steps = numpy.arange(int(seconds * SAMPLE_RATE))
    tone = (numpy.sin(steps * 2 * numpy.pi * 440 / SAMPLE_RATE) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(tone.tobytes())

    return path


def _read_sent_audio(request: fake_model_server.RecordedRequest) -> bytes:
    return [content for name, file, content in request.read_multipart_fields() if file is not None][0]


def _decode_samples(audio: bytes) -> numpy.ndarray:
    """The samples of the Ogg file the program sent, decoded by ffmpeg as the server would."""

    folder = Path(tempfile.mkdtemp())
    (folder / "sent.ogg").write_bytes(audio)
    decoded = audio_extraction_service.extract_audio(folder / "sent.ogg", folder / "decoded.wav")
    with wave.open(str(decoded), "rb") as reader:
        raw = reader.readframes(reader.getnframes())

    return numpy.frombuffer(raw, dtype="<i2")


def _loudest(samples: numpy.ndarray,
             from_s: float,
             to_s: float) -> int:
    return int(numpy.abs(samples[round(from_s * SAMPLE_RATE):round(to_s * SAMPLE_RATE)]).max())


def test_the_meeting_goes_as_one_request_with_every_part_at_its_offset_and_the_turns_come_back():
    server = fake_model_server.FakeModelServer()
    settings = config.DiarizationSettings(max_speakers=4)
    with fake_model_server.serving_kind(server=server, kind="diarization"):
        result = diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0), (_write_wav(0.5), 2.0)], settings)
    harness.check("one POST to the transcription route for the whole meeting",
                  [(request.method, request.path) for request in server.request_list] == [("POST", PATH)],
                  str([(request.method, request.path) for request in server.request_list]))
    sent = server.request_list[0]
    fields = {name: content.decode("utf-8") for name, file, content in sent.read_multipart_fields() if file is None}
    harness.check("it asks for diarized_json and tells the server the most speakers", fields.get("response_format")
                  == "diarized_json" and fields.get("max_speakers") == "4" and "min_speakers" not in fields, str(fields))
    samples = _decode_samples(_read_sent_audio(sent))
    harness.check("the audio is 2.5 seconds long: the second part ends there", abs(len(samples) / SAMPLE_RATE - 2.5) < 0.1,
                  str(len(samples) / SAMPLE_RATE))
    harness.check("the first part is heard at 0 s, the gap before 2 s is silence, the second part is heard after it",
                  _loudest(samples, 0.2, 0.8) > LOUD and _loudest(samples, 1.2, 1.8) < LOUD / 4
                  and _loudest(samples, 2.1, 2.4) > LOUD,
                  str([_loudest(samples, 0.2, 0.8), _loudest(samples, 1.2, 1.8), _loudest(samples, 2.1, 2.4)]))
    turns = [{"start": 0.0, "end": 2.0, "label": "SPEAKER_00"}, {"start": 2.0, "end": 4.0, "label": "SPEAKER_01"}]
    harness.check("the turns are the server's, on the meeting clock, with the same turns as the exclusive ones",
                  result == {"turns": turns, "exclusiveTurns": turns, "device": "remote"}, str(result))


def test_a_server_that_marks_overlap_gives_the_exclusive_turns_apart():
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 200, json.dumps({
        "segments": [{"speaker": "A", "start": 0.0, "end": 2.0}, {"speaker": "B", "start": 1.5, "end": 3.0}],
        "exclusive_segments": [{"speaker": "A", "start": 0.0, "end": 1.5}, {"speaker": "B", "start": 1.5, "end": 3.0}],
    }).encode("utf-8"))
    with fake_model_server.serving_kind(server=server, kind="diarization"):
        result = diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], config.DiarizationSettings())
    harness.check("the turns overlap where two people speak at once",
                  [(turn["start"], turn["end"], turn["label"]) for turn in result["turns"]]
                  == [(0.0, 2.0, "A"), (1.5, 3.0, "B")], str(result["turns"]))
    harness.check("the exclusive turns give each moment to one speaker",
                  [(turn["start"], turn["end"], turn["label"]) for turn in result["exclusiveTurns"]]
                  == [(0.0, 1.5, "A"), (1.5, 3.0, "B")], str(result["exclusiveTurns"]))


def test_a_remote_run_loads_no_model_and_frees_no_device_memory():
    server = fake_model_server.FakeModelServer()
    loaded: list[str] = []
    freed: list[str] = []
    previous_loader = diarization_service.set_pipeline_loader(lambda settings, device: loaded.append(device))
    previous_release = diarization_service._release_device_memory
    diarization_service._release_device_memory = lambda: freed.append("freed")
    try:
        with fake_model_server.serving_kind(server=server, kind="diarization"):
            diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], config.DiarizationSettings())
    finally:
        diarization_service.set_pipeline_loader(previous_loader)
        diarization_service._release_device_memory = previous_release
    harness.check("no pipeline was built for a meeting the server diarized", loaded == [], str(loaded))
    harness.check("the device memory is left alone: the program holds none", freed == [], str(freed))


def test_the_run_header_says_the_server_instead_of_asking_for_pyannote():
    server = fake_model_server.FakeModelServer()
    settings = config.DiarizationSettings(is_on=True)
    with fake_model_server.serving_kind(server=server, kind="diarization"):
        line = diarization_service.describe_diarization(settings)
    harness.check("the line names the server's address and does not say pyannote is missing",
                  server.build_url() in line and "not installed" not in line, line)


def test_a_server_that_fails_leaves_the_transcript_without_speakers():
    server = fake_model_server.FakeModelServer()
    server.set_reply(PATH, 500, b'{"error": "out of memory"}')
    failure = None
    with fake_model_server.serving_kind(server=server, kind="diarization"):
        try:
            diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], config.DiarizationSettings())
        except DiarizationFailed as failed:
            failure = str(failed)
    harness.check("an HTTP 500 is DiarizationFailed, naming the URL and the status",
                  failure is not None and server.build_url() in failure and "HTTP 500" in failure, str(failure))


def test_a_server_nobody_answers_is_unavailable():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving_kind(server=server, kind="diarization"):
        address = server.build_url()
    config.settings.hosts[fake_model_server.TEST_HOST] = config.HostSettings(url=address)
    config.settings.providers.diarization.host = fake_model_server.TEST_HOST
    failure = None
    try:
        diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], config.DiarizationSettings())
    except DiarizationUnavailable as unavailable:
        failure = str(unavailable)
    finally:
        config.settings.providers.diarization.host = ""
        del config.settings.hosts[fake_model_server.TEST_HOST]
    harness.check("a refused connection is DiarizationUnavailable and names the address",
                  failure is not None and address in failure and "no answer" in failure, str(failure))
