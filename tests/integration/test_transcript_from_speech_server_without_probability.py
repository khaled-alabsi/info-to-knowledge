"""Phase 1 on a speech server that gives its words no `probability` (made data: a one-second clip with a tone, the stand-in
server on port 0, the real node, the real ffmpeg compression and the real transcript file). Before, every word read as a
probability of 0.0: the segment whose words take no time was dropped as invented speech, and the transcript file held no
segment at all. Now the file holds the words, each with an unknown probability."""

import json
import logging
import tempfile
from pathlib import Path

from support import fake_model_server
from support import harness
from support import media_fixtures

import config
from graph import graph_state
from graph.nodes import transcribe_audio_node
from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import faster_whisper_client
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from validators import transcript_file_validator

TRANSCRIBE = output_contract.PHASE_NUMBERS["transcribe"]
LAST = max(output_contract.PHASE_FILES)
ZERO_LENGTH_WORDS = [{"word": word, "start": 0.5, "end": 0.5} for word in ("one", "two", "three")]


def _load_run_context(video: Path) -> RunContext:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(video.parent), module=video.name,
                                        output_root=str(paths.output_root)), budgets={}, models=[])

    return RunContext(repository=video.parent, module=video.name, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList())


def test_a_server_that_gives_no_word_probability_still_gives_a_transcript_defect():
    # Defect: the two Whisper clean-ups read a missing probability as 0.0 and removed the whole segment. Fix: a missing
    # probability is unknown, and the segment is kept
    reply = {"language": "en", "duration": 1.0, "text": "one two three",
             "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "one two three"}], "words": ZERO_LENGTH_WORDS}
    server = fake_model_server.FakeModelServer()
    server.set_reply("/v1/audio/transcriptions", 200, json.dumps(reply).encode("utf-8"))
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    saved = (config.settings.audio.engine, config.settings.audio.diarization.is_on, config.settings.providers.speech.url)
    config.settings.audio.engine = faster_whisper_client.ENGINE_NAME
    config.settings.audio.diarization.is_on = False
    config.settings.providers.speech.url = server.build_url()
    try:
        with fake_model_server.serving(server):
            update = transcribe_audio_node.produce_transcript_node(graph_state.new_run_state(TRANSCRIBE, LAST),
                                                                  {"configurable": {"ctx": ctx}})
    finally:
        config.settings.audio.engine, config.settings.audio.diarization.is_on, config.settings.providers.speech.url = saved
    written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
    harness.check("phase 1 completes", update["phases"]["transcribe"] == "complete", str(update))
    harness.check("the transcript file holds the segment with its three words", len(written["segments"]) == 1
                  and [word["word"].strip() for word in written["segments"][0]["words"]] == ["one", "two", "three"],
                  str(written))
    harness.check("each word's probability is unknown, not 0.0",
                  [word["probability"] for word in written["segments"][0]["words"]] == [None, None, None], str(written))
    harness.check("the transcript file passes its own check", transcript_file_validator.validate_transcript_file(ctx.paths).ok)
    harness.check("the audio went to the server once, compressed", len(server.request_list) == 1, str(len(server.request_list)))
