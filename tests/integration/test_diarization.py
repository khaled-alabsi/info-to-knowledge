"""Speakers placed by the real models on a rendered recording: `tests/resources/audio/two-voices.wav`, two macOS voices
taking turns (A, B, A), then both at once, rendered by its `render.sh` and kept in git. Whisper runs with this machine's
`audio` settings and pyannote with its `audio.diarization` settings; without pyannote installed, or its model not on
this machine, the case says so in one line and checks nothing.
"""

from pathlib import Path

from support import harness

import config
from models.diarization_errors import DiarizationUnavailable
from services import diarization_service
from services import transcription_service
from utils import speaker_assignment_utils

TWO_VOICES = Path(__file__).resolve().parents[1] / "resources" / "audio" / "two-voices.wav"
# Where render.sh puts both voices at once: the fourth turn, from about 25.6 s to 28.4 s
OVERLAP_START_S = 25.0


def _list_turn_order(segments: list[dict]) -> list[str]:
    """The speakers in the order they take turns: one entry per run of one speaker, a segment with none left out."""

    order: list[str] = []
    for segment in segments:
        if segment["speaker"] and (not order or order[-1] != segment["speaker"]):
            order.append(segment["speaker"])

    return order


def test_two_voices_taking_turns_get_two_speakers_and_their_overlap_gets_none():
    settings = config.settings.audio.diarization
    try:
        diarization = diarization_service.compute_speaker_turns([(TWO_VOICES, 0.0)], settings)
    except DiarizationUnavailable as exc:
        print(f"  skipped: speaker diarization cannot run here: {exc}")

        return
    transcript = transcription_service.produce_transcript(TWO_VOICES, config.settings.audio)
    segments = speaker_assignment_utils.add_speakers(transcript["segments"], diarization, settings.overlap_share)
    harness.check("two speakers", speaker_assignment_utils.list_speakers(segments) == ["Speaker A", "Speaker B"],
                  str(speaker_assignment_utils.list_speakers(segments)))
    before_overlap = [segment for segment in segments if segment["end"] <= OVERLAP_START_S]
    harness.check("they take turns A, B, A before both speak at once",
                  _list_turn_order(before_overlap) == ["Speaker A", "Speaker B", "Speaker A"],
                  str([(round(segment["start"], 1), segment["speaker"], segment["text"]) for segment in before_overlap]))
    harness.check("the question is Speaker B's",
                  any("database" in segment["text"] and segment["speaker"] == "Speaker B" for segment in segments),
                  str([(segment["speaker"], segment["text"]) for segment in segments]))
    overlap_words = [word for segment in segments if segment["start"] >= OVERLAP_START_S
                     for word in segment.get("words") or []]
    harness.check("words said by both at once are given to nobody",
                  any(word["speaker"] is None for word in overlap_words),
                  str([(round(word["start"], 1), word["word"], word["speaker"]) for word in overlap_words]))


def test_the_real_pipeline_gives_its_gpu_memory_back_defect():
    # Release test 3: a 54-minute meeting's diarization left the run's process holding 14 GB into phase 3, and the
    # system's swap filled. On the 28 s clip the real pipeline left 4.2 GB in torch's cache; now it leaves under 1.5 GB
    try:
        diarization_service.compute_speaker_turns([(TWO_VOICES, 0.0)], config.settings.audio.diarization)
    except DiarizationUnavailable as exc:
        print(f"  skipped: speaker diarization cannot run here: {exc}")

        return
    torch = diarization_service._import_torch()
    if not torch.backends.mps.is_available():
        print("  skipped: no Metal device here, nothing is cached on the GPU")

        return
    held_gb = torch.mps.driver_allocated_memory() / 2 ** 30
    harness.check("torch holds under 1.5 GB of GPU memory after the diarization", held_gb < 1.5, f"{held_gb:.2f} GB")
