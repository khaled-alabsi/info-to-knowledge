"""Who spoke when, from phase 1's own WAVs: pyannote's speaker diarization over the whole meeting at once.

ONE CALL OVER THE MEETING, NOT ONE PER PART. pyannote numbers the speakers of each call afresh, so two parts diarized
apart could call one voice SPEAKER_00 in the first and SPEAKER_01 in the second. Each part's samples are placed at the
part's offset on the meeting clock (the same offsets phase 1 moves the part's transcript by) and the meeting is
diarized once.

THE SAMPLES ARE HANDED OVER IN MEMORY. pyannote decodes a file through torchcodec and ffmpeg's libraries; phase 1
already wrote 16 kHz mono PCM, which the standard library reads, so pyannote never decodes anything.

TWO RESULTS. `turns` may overlap (two people at once); `exclusiveTurns` gives every moment to one speaker, the form
pyannote makes for placing a transcript's words. `utils/speaker_assignment_utils.py` reads both.

NOTHING LEAVES THE MACHINE. pyannote.audio 4 sends usage data to its own server unless `PYANNOTE_METRICS_ENABLED` is
false, so it is set false before the package is imported. The model is read from the Hugging Face cache; `HF_TOKEN` is
needed only for its first download.

The imports are LAZY, by `importlib`, because `pyannote.audio` is an optional dependency
(`requirements-diarization.txt`): a run on a machine without it writes the transcript with no speakers.
"""

from __future__ import annotations

import gc
import importlib
import os
import wave
from pathlib import Path
from typing import Any, Callable

import numpy

import config
from config import DiarizationSettings
from models.diarization_errors import DiarizationFailed, DiarizationUnavailable
from models.endpoint_errors import EndpointError
from models.endpoint_spec import EndpointSpec
from services import audio_upload_service
from services import endpoint_client

__all__ = ["PACKAGE", "SAMPLE_RATE", "REMOTE_DEVICE", "is_installed", "resolve_device", "set_pipeline_loader",
           "describe_diarization", "compute_speaker_turns"]

PACKAGE = "pyannote.audio"
SAMPLE_RATE = 16000
# What a result names as its device when a server did the work: the program used none of its own
REMOTE_DEVICE = "remote"
NOT_INSTALLED = ("speaker diarization needs the optional package `pyannote.audio`. Install it with: "
                 ".venv/bin/python -m pip install -r requirements-diarization.txt")

# What builds the pipeline from the settings and the device. `None` means pyannote's own `Pipeline.from_pretrained`;
# the suite sets a fake through `set_pipeline_loader`, so no model is downloaded and no GPU is touched.
_pipeline_loader: Callable[[DiarizationSettings, str], Any] | None = None


def _import_pyannote_without_metrics() -> Any:
    # Set before the first import of pyannote, which reads it then: no usage data leaves the machine.
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"

    return importlib.import_module(PACKAGE)


def _import_torch() -> Any:
    """torch is an optional install, with pyannote: imported where it is used, not at the top as numpy is."""

    return importlib.import_module("torch")


def _read_wav_samples(wav_path: Path) -> numpy.ndarray:
    """A 16 kHz mono 16-bit WAV, as phase 1's extraction writes it, as float samples between -1 and 1."""

    with wave.open(str(wav_path), "rb") as reader:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise DiarizationFailed(f"{wav_path.name} is not 16 kHz mono 16-bit PCM: rate {reader.getframerate()}, "
                                    f"{reader.getnchannels()} channel(s), {reader.getsampwidth() * 8}-bit")
        raw = reader.readframes(reader.getnframes())

    return numpy.frombuffer(raw, dtype="<i2").astype(numpy.float32) / 32768.0


def _join_parts_on_meeting_clock(parts: list[tuple[Path, float]]) -> numpy.ndarray:
    """Every part's samples at its offset in seconds, in one array: silence fills a gap, and where a part's audio runs
    past the next part's offset, the next part wins."""

    placed = [(round(offset_s * SAMPLE_RATE), _read_wav_samples(path)) for path, offset_s in parts]
    meeting = numpy.zeros(max((start + len(samples) for start, samples in placed), default=0), dtype=numpy.float32)
    for start, samples in placed:
        meeting[start:start + len(samples)] = samples

    return meeting


def _load_pretrained_pipeline(settings: DiarizationSettings,
                              device: str) -> Any:
    try:
        audio = _import_pyannote_without_metrics()
    except ImportError as exc:
        raise DiarizationUnavailable(NOT_INSTALLED) from exc
    torch = _import_torch()
    try:
        pipeline = audio.Pipeline.from_pretrained(settings.model, token=os.environ.get(config.HF_TOKEN_ENV_VAR) or None)
    # Hugging Face's gated, missing and offline errors are all OSError: the model is not on this machine yet
    except OSError as exc:
        raise DiarizationUnavailable(f"the model {settings.model} is not cached and could not be downloaded: {exc}. "
                                     f"Accept its terms on huggingface.co and set HF_TOKEN for the first download"
                                     ) from exc
    if pipeline is None:
        raise DiarizationUnavailable(f"the model {settings.model} could not be loaded: set HF_TOKEN and accept its "
                                     f"terms on huggingface.co")

    return pipeline.to(torch.device(device))


def _list_turns(annotation: Any) -> list[dict]:
    return [{"start": float(segment.start), "end": float(segment.end), "label": str(label)}
            for segment, _track, label in annotation.itertracks(yield_label=True)]


def set_pipeline_loader(loader: Callable[[DiarizationSettings, str], Any] | None
                        ) -> Callable[[DiarizationSettings, str], Any] | None:
    """Swap what builds the pipeline, for a test; returns the one it replaced."""

    global _pipeline_loader
    previous, _pipeline_loader = _pipeline_loader, loader

    return previous


def is_installed() -> tuple[bool, str]:
    """Whether diarization can run at all, asked before the audio is extracted."""

    if _pipeline_loader is not None:
        return True, ""
    try:
        _import_pyannote_without_metrics()
    except ImportError:
        return False, NOT_INSTALLED

    return True, ""


def resolve_device(settings: DiarizationSettings) -> str:
    """The torch device the setting names; `auto` takes CUDA, then Apple's MPS, then the CPU."""

    if settings.device != "auto":
        return settings.device
    if _pipeline_loader is not None:
        return "cpu"
    torch = _import_torch()
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"

    return "cpu"


def describe_diarization(settings: DiarizationSettings) -> str:
    """One line for the run's header, said before the audio is extracted: whether speakers will be placed, and where.
    Never a refusal: a run without diarization still writes its transcript."""

    if not settings.is_on:
        return "diarization: off"
    endpoint = endpoint_client.find_endpoint(kind="diarization")
    if endpoint is not None:
        return f"diarization: the diarization server {endpoint.base_url}"
    is_installed_now, why_not = is_installed()
    if not is_installed_now:
        return f"diarization: not installed, the transcript gets no speakers ({why_not})"

    return f"diarization: {settings.model} on {resolve_device(settings)}"


def _release_device_memory() -> None:
    """Gives the GPU memory the diarization held back to the system. torch keeps what the pipeline allocated in its
    own cache after the pipeline is gone: on Apple's MPS a 28 s clip left 4.2 GB held, and a whole meeting's run
    carried gigabytes into phase 3, beside the model host, until the system's swap was full."""

    gc.collect()
    try:
        torch = _import_torch()
    except ImportError:
        return
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def compute_speaker_turns(parts: list[tuple[Path, float]],
                          settings: DiarizationSettings) -> dict:
    """`{"turns", "exclusiveTurns", "device"}` for the meeting whose audio is `parts`, each a WAV and its offset in
    seconds on the meeting clock; every turn `{"start", "end", "label"}` in seconds, labelled as pyannote labels it."""

    endpoint = endpoint_client.find_endpoint(kind="diarization")
    if endpoint is not None:
        # A server holds the model and its memory: nothing of the program's own is loaded, so nothing is released here
        return _request_meeting_turns(parts=parts, settings=settings, endpoint=endpoint)
    try:
        return _compute_meeting_turns(parts, settings)
    finally:
        # The pipeline, its waveform and its output are gone once the function returns, so the cache is emptied here
        _release_device_memory()


def _list_server_turns(raw_list: list) -> list[dict]:
    """The server's segments as turns: `speaker` is the label, in seconds on the meeting's clock."""

    turn_list = []
    for raw in raw_list:
        if isinstance(raw, dict) and {"speaker", "start", "end"} <= set(raw):
            turn_list.append({"start": float(raw["start"]), "end": float(raw["end"]), "label": str(raw["speaker"])})

    return turn_list


def _request_meeting_turns(parts: list[tuple[Path, float]],
                           settings: DiarizationSettings,
                           endpoint: EndpointSpec) -> dict:
    """The meeting diarized by the server in one request, every part at its offset: the speakers are numbered once for the
    whole meeting, as the in-process model numbers them. A server that gives no `exclusive_segments` has no overlap to
    take out, so its turns are the exclusive ones too."""

    pcm = audio_upload_service.join_parts_pcm(parts=parts)
    audio = audio_upload_service.build_compressed_file(pcm=pcm, file_name="meeting.ogg")
    try:
        reply = endpoint_client.request_speaker_turns(endpoint=endpoint, audio=audio, min_speakers=settings.min_speakers,
                                                      max_speakers=settings.max_speakers)
    except EndpointError as failed:
        if failed.status is None:
            raise DiarizationUnavailable(f"the diarization server gave no answer: {failed}") from failed
        raise DiarizationFailed(f"the diarization server failed: {failed}") from failed
    turns = _list_server_turns(raw_list=reply["segments"])
    exclusive = reply.get("exclusive_segments")
    exclusive_turns = _list_server_turns(raw_list=exclusive) if isinstance(exclusive, list) else turns

    return {"turns": turns, "exclusiveTurns": exclusive_turns, "device": REMOTE_DEVICE}


def _compute_meeting_turns(parts: list[tuple[Path, float]],
                           settings: DiarizationSettings) -> dict:
    # STEP 1: the pipeline, on this machine's device; a missing package or model is DiarizationUnavailable
    device = resolve_device(settings)
    pipeline = (_pipeline_loader or _load_pretrained_pipeline)(settings, device)

    # STEP 2: the meeting's samples, every part at its offset
    samples = _join_parts_on_meeting_clock(parts)
    torch = _import_torch()
    waveform = torch.from_numpy(samples).unsqueeze(0)

    # STEP 3: diarize once over the whole meeting
    try:
        output = pipeline({"waveform": waveform, "sample_rate": SAMPLE_RATE},
                          min_speakers=settings.min_speakers, max_speakers=settings.max_speakers)
    # torch raises RuntimeError for a device fault (an MPS operation it lacks, memory) and ValueError for a shape
    # it refuses: the model ran and failed on this audio, and the transcript is written without speakers
    except (RuntimeError, ValueError) as exc:
        raise DiarizationFailed(f"{settings.model} failed on {device}: {type(exc).__name__}: {exc}") from exc

    return {"turns": _list_turns(output.speaker_diarization),
            "exclusiveTurns": _list_turns(output.exclusive_speaker_diarization),
            "device": device}
