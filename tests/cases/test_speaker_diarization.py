"""Speakers in phase 1: placing pyannote's turns on Whisper's words, the service that runs
pyannote over the whole meeting, phase 1's node with diarization on, off, missing and failing, and every reader of the
transcript that shows a speaker. pyannote itself is replaced by a fake pipeline through the service's own seam; the
real model runs in `tests/integration/test_diarization.py`."""

import json
import logging
import tempfile
import wave
from pathlib import Path

import numpy

from support import fixtures as fx
from support import harness
from support import media_fixtures

import config as app_config
from graph import graph_state
from graph.nodes import transcribe_audio_node
from models import output_contract
from models.attempt_request import AttemptRequest
from models.diarization_errors import DiarizationFailed, DiarizationUnavailable
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from prompts import speech_structuring_messages
from services import diarization_service
from services import faster_whisper_client
from services import phase_file_service
from services import run_artifact_service
from services import run_paths_service
from services import speech_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import meeting_notes_utils
from utils import speaker_assignment_utils
from utils import speech_markdown_utils
from utils import transcript_slice_utils
from validators import speech_validator
from validators import transcript_file_validator

TRANSCRIBE = output_contract.PHASE_NUMBERS["transcribe"]
LAST = max(output_contract.PHASE_FILES)


def _build_word(text: str,
                start: float,
                end: float) -> dict:
    return {"word": f" {text}", "start": start, "end": end, "probability": 0.9}


def _build_turns(*turns: tuple[float, float, str]) -> list[dict]:
    return [{"start": start, "end": end, "label": label} for start, end, label in turns]


class _FakeAnnotation:
    """What the service reads of a pyannote Annotation: its turns, one by one."""

    def __init__(self, turns: list[dict]):
        self.turns = turns

    def itertracks(self, yield_label: bool):
        return [(type("Segment", (), {"start": turn["start"], "end": turn["end"]})(), "_", turn["label"])
                for turn in self.turns]


class _FakePipeline:
    """Records the samples it was handed and answers with fixed turns."""

    def __init__(self, turns: list[dict], exclusive: list[dict] | None = None, error: Exception | None = None):
        self.turns, self.exclusive, self.error, self.calls = turns, exclusive or turns, error, []

    def __call__(self, audio: dict, **keywords):
        self.calls.append({"samples": audio["waveform"].shape[-1], "rate": audio["sample_rate"], **keywords})
        if self.error is not None:
            raise self.error

        return type("Output", (), {"speaker_diarization": _FakeAnnotation(self.turns),
                                   "exclusive_speaker_diarization": _FakeAnnotation(self.exclusive)})()


def _write_wav(seconds: float) -> Path:
    path = Path(tempfile.mkdtemp()) / "part.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes((numpy.full(int(seconds * 16000), 1000, dtype="<i2")).tobytes())

    return path


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


def _run_node(ctx: RunContext,
              loader,
              is_on: bool = True) -> dict:
    # The engine and the switch are pinned, never read from the settings files this machine has
    audio = app_config.settings.audio
    previous = (audio.engine, audio.diarization.is_on, faster_whisper_client.MODEL_FACTORY)
    audio.engine, audio.diarization.is_on = faster_whisper_client.ENGINE_NAME, is_on
    faster_whisper_client.set_model_factory(media_fixtures.FakeWhisperModel)
    previous_loader = diarization_service.set_pipeline_loader(loader)
    try:
        return transcribe_audio_node.produce_transcript_node(graph_state.new_run_state(TRANSCRIBE, LAST),
                                                             {"configurable": {"ctx": ctx}})
    finally:
        diarization_service.set_pipeline_loader(previous_loader)
        faster_whisper_client.set_model_factory(previous[2])
        audio.engine, audio.diarization.is_on = previous[0], previous[1]


def test_each_word_takes_the_speaker_whose_turn_covers_it_and_a_segment_splits_where_the_speaker_changes():
    segment = {"text": " We start now. What about the database?", "start": 0.0, "end": 4.0,
               "words": [_build_word("We", 0.0, 0.5), _build_word("start", 0.5, 1.0), _build_word("now.", 1.0, 1.5),
                         _build_word("What", 2.0, 2.5), _build_word("about", 2.5, 3.0),
                         _build_word("the", 3.0, 3.3), _build_word("database?", 3.3, 4.0)]}
    turns = _build_turns((0.0, 1.8, "SPEAKER_01"), (1.8, 4.0, "SPEAKER_00"))
    placed = speaker_assignment_utils.add_speakers([segment], {"turns": turns, "exclusiveTurns": turns}, 0.5)
    harness.check("the segment is split in two at the change", len(placed) == 2, str(placed))
    harness.check("the first speaker to speak is Speaker A, whatever pyannote's number",
                  [piece["speaker"] for piece in placed] == ["Speaker A", "Speaker B"], str(placed))
    harness.check("each piece holds its own words and times",
                  placed[0]["text"] == " We start now." and placed[1]["text"] == " What about the database?"
                  and placed[1]["start"] == 2.0 and placed[1]["end"] == 4.0, str(placed))
    harness.check("every word carries its speaker",
                  [word["speaker"] for word in placed[1]["words"]] == ["Speaker B"] * 4, str(placed[1]))


def test_a_speaker_who_returns_keeps_the_letter_and_overlapping_speech_gets_no_speaker():
    segments = [{"text": " Hello", "start": 0.0, "end": 1.0, "words": [_build_word("Hello", 0.0, 1.0)]},
                {"text": " Hi", "start": 2.0, "end": 3.0, "words": [_build_word("Hi", 2.0, 3.0)]},
                {"text": " Again", "start": 4.0, "end": 5.0, "words": [_build_word("Again", 4.0, 5.0)]},
                {"text": " Both", "start": 6.0, "end": 7.0, "words": [_build_word("Both", 6.0, 7.0)]}]
    turns = _build_turns((0.0, 1.0, "X"), (2.0, 3.0, "Y"), (4.0, 5.0, "X"), (6.0, 7.0, "X"), (6.0, 7.0, "Y"))
    exclusive = _build_turns((0.0, 1.0, "X"), (2.0, 3.0, "Y"), (4.0, 5.0, "X"), (6.0, 7.0, "X"))
    placed = speaker_assignment_utils.add_speakers(segments, {"turns": turns, "exclusiveTurns": exclusive}, 0.5)
    harness.check("A, B, A again, then nobody for the overlap",
                  [segment["speaker"] for segment in placed] == ["Speaker A", "Speaker B", "Speaker A", None],
                  str(placed))
    harness.check("the overlapped word has no speaker either", placed[3]["words"][0]["speaker"] is None, str(placed[3]))
    harness.check("the speakers are listed in the order they first speak",
                  speaker_assignment_utils.list_speakers(placed) == ["Speaker A", "Speaker B"])


def test_a_word_no_turn_covers_stays_in_its_piece_and_a_segment_without_words_is_placed_by_its_own_times():
    segment = {"text": " Yes well", "start": 0.0, "end": 2.0,
               "words": [_build_word("Yes", 0.0, 0.5), _build_word("well", 1.5, 2.0)]}
    bare = {"text": " Then", "start": 3.0, "end": 4.0, "words": []}
    turns = _build_turns((0.0, 1.0, "S1"), (3.0, 4.0, "S2"))
    placed = speaker_assignment_utils.add_speakers([segment, bare], {"turns": turns, "exclusiveTurns": turns}, 0.5)
    harness.check("a word outside every turn does not split its segment", len(placed) == 2, str(placed))
    harness.check("it keeps no speaker of its own while the segment keeps the one it has",
                  placed[0]["speaker"] == "Speaker A" and placed[0]["words"][1]["speaker"] is None, str(placed[0]))
    harness.check("the segment with no word timings is placed by its times", placed[1]["speaker"] == "Speaker B",
                  str(placed[1]))


def test_the_service_diarizes_every_part_once_at_its_offset_on_the_meeting_clock():
    pipeline = _FakePipeline(_build_turns((0.0, 1.0, "SPEAKER_00")))
    previous = diarization_service.set_pipeline_loader(lambda settings, device: pipeline)
    try:
        result = diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0), (_write_wav(0.5), 2.0)],
                                                        app_config.DiarizationSettings(max_speakers=4))
    finally:
        diarization_service.set_pipeline_loader(previous)
    harness.check("one call for the whole meeting", len(pipeline.calls) == 1, str(pipeline.calls))
    harness.check("the second part starts at its offset, a gap of silence between",
                  pipeline.calls[0]["samples"] == int(2.5 * 16000) and pipeline.calls[0]["rate"] == 16000,
                  str(pipeline.calls))
    harness.check("the speaker bounds reach pyannote", pipeline.calls[0]["max_speakers"] == 4, str(pipeline.calls))
    harness.check("both kinds of turns come back with the device",
                  result["turns"] == result["exclusiveTurns"] == _build_turns((0.0, 1.0, "SPEAKER_00"))
                  and result["device"] == "cpu", str(result))


def test_the_gpu_memory_the_diarization_held_is_given_back_defect():
    # A meeting's diarization left gigabytes in torch's cache for the rest of the run, which filled the system's swap
    # beside the model host. The service now empties the cache when it returns, and when it fails
    torch = diarization_service._import_torch()
    emptied: list[str] = []
    previous = (torch.backends.mps.is_available, torch.mps.empty_cache, torch.cuda.is_available)
    torch.backends.mps.is_available = lambda: True
    torch.mps.empty_cache = lambda: emptied.append("mps")
    torch.cuda.is_available = lambda: False
    loader = diarization_service.set_pipeline_loader(lambda settings, device: _FakePipeline(_build_turns((0.0, 1.0, "SPEAKER_00"))))
    try:
        diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], app_config.DiarizationSettings())
        harness.check("the cache is emptied after a meeting is diarized", emptied == ["mps"], str(emptied))
        diarization_service.set_pipeline_loader(lambda settings, device: _FakePipeline(None, error=RuntimeError("mps ran out")))
        try:
            diarization_service.compute_speaker_turns([(_write_wav(1.0), 0.0)], app_config.DiarizationSettings())
        except DiarizationFailed:
            pass
        harness.check("and after one that failed", emptied == ["mps", "mps"], str(emptied))
    finally:
        diarization_service.set_pipeline_loader(loader)
        torch.backends.mps.is_available, torch.mps.empty_cache, torch.cuda.is_available = previous


def test_the_run_header_says_whether_speakers_will_be_placed():
    harness.check("off says off", diarization_service.describe_diarization(app_config.DiarizationSettings()) ==
                  "diarization: off")
    previous = diarization_service.set_pipeline_loader(lambda settings, device: None)
    try:
        line = diarization_service.describe_diarization(app_config.DiarizationSettings(is_on=True))
    finally:
        diarization_service.set_pipeline_loader(previous)
    harness.check("on names the model and the device", "community-1 on cpu" in line, line)


def test_phase_1_with_diarization_on_writes_a_speaker_on_every_segment():
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    turns = _build_turns((0.0, 0.45, "SPEAKER_03"), (0.45, 1.0, "SPEAKER_01"))
    update = _run_node(ctx, lambda settings, device: _FakePipeline(turns))
    written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
    harness.check("phase 1 completes", update["phases"]["transcribe"] == "complete", str(update))
    harness.check("the two segments have two speakers",
                  [segment["speaker"] for segment in written["segments"]] == ["Speaker A", "Speaker B"], str(written))
    harness.check("pyannote's turns are kept, named as the segments' speakers",
                  written["diarization"]["turns"] == [{"start": 0.0, "end": 0.45, "speaker": "Speaker A"},
                                                      {"start": 0.45, "end": 1.0, "speaker": "Speaker B"}]
                  and written["diarization"]["exclusiveTurns"] == written["diarization"]["turns"],
                  str(written["diarization"]))
    harness.check("the file records how it went",
                  written["diarization"]["status"] == "done"
                  and written["diarization"]["speakers"] == ["Speaker A", "Speaker B"], str(written["diarization"]))


def _raise_unavailable(settings, device):
    raise DiarizationUnavailable("not installed here")


def test_phase_1_writes_the_plain_transcript_when_diarization_is_off_missing_or_failing():
    cases = [("off", lambda settings, device: _FakePipeline([]), False, "off"),
             ("missing", _raise_unavailable, True, "unavailable"),
             ("failing", lambda settings, device: _FakePipeline([], error=RuntimeError("MPS out of memory")), True,
              "failed")]
    for name, loader, is_on, status in cases:
        ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
        update = _run_node(ctx, loader, is_on)
        written = phase_file_service.read_phase_file(ctx.paths, TRANSCRIBE)
        harness.check(f"{name}: phase 1 still completes", update["phases"]["transcribe"] == "complete", str(update))
        harness.check(f"{name}: Whisper's segments are kept with no speaker",
                      [segment["text"] for segment in written["segments"]] == [" Hallo", " Welt"]
                      and all("speaker" not in segment for segment in written["segments"]), str(written))
        harness.check(f"{name}: the file says {status}", written["diarization"]["status"] == status,
                      str(written["diarization"]))
    harness.check("a failure on the audio is its own error", issubclass(DiarizationFailed, RuntimeError))


def test_the_transcript_check_accepts_speakers_or_none_and_refuses_any_other_name():
    ctx = _load_run_context(media_fixtures.build_clip(is_with_audio=True))
    for speaker, is_ok in [("Speaker A", True), (None, True), ("Jonas", False), ("Speaker 1", False)]:
        phase_file_service.write_phase_file(ctx.paths, TRANSCRIBE, {"segments": [
            {"text": " Hi", "start": 0.0, "end": 1.0, "speaker": speaker,
             "words": [{"word": " Hi", "start": 0.0, "end": 1.0, "speaker": speaker}]}]})
        codes = harness.codes(transcript_file_validator.validate_transcript_file(ctx.paths))
        harness.check(f"speaker {speaker!r}", ("T_SPEAKER_SHAPE" not in codes) == is_ok, str(codes))


SPOKEN = {"segments": [{"text": " Is the import done", "start": 0.0, "end": 2.0, "speaker": "Speaker A",
                        "words": [_build_word("Is", 0.0, 0.5), _build_word("the", 0.5, 1.0),
                                  _build_word("import", 1.0, 1.5), _build_word("done", 1.5, 2.0)]},
                       {"text": " Yes on Monday", "start": 2.0, "end": 4.0, "speaker": "Speaker B",
                        "words": [_build_word("Yes", 2.0, 2.5), _build_word("on", 2.5, 3.0),
                                  _build_word("Monday", 3.0, 4.0)]},
                       {"text": " Both at once", "start": 4.0, "end": 5.0, "speaker": None,
                        "words": [_build_word("Both", 4.0, 4.3), _build_word("at", 4.3, 4.6),
                                  _build_word("once", 4.6, 5.0)]}]}


def test_audio_phase_2_is_shown_the_lines_in_speaker_turns_and_a_paragraph_may_not_cross_one():
    lines = speech_markdown_utils.list_numbered_lines(SPOKEN)
    rendered = speech_structuring_messages._render_lines(lines)
    harness.check("each turn names its speaker over its lines, a line said at once in the turn before it",
                  "Turn S0001 · Speaker A\nS0001 [00:00] Is the import done\n\nTurn S0002-S0003 · Speaker B\n"
                  "S0002 [00:02] Yes on Monday\nS0003 [00:04] Both at once" == rendered, rendered)
    plain = {"segments": [{key: value for key, value in segment.items() if key != "speaker"}
                          for segment in SPOKEN["segments"]]}
    harness.check("without speakers the lines come one by one",
                  speech_structuring_messages._render_lines(speech_markdown_utils.list_numbered_lines(plain))
                  .startswith("S0001 [00:00] Is the import done\nS0002"))
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-sync.wav")
    phase_file_service.write_phase_file(paths, TRANSCRIBE, SPOKEN)
    good = "# Speech\n\n## S0001 · Speaker A\nIs the import done?\n\n## S0002-S0003 · Speaker B\nYes, on Monday. Both at once.\n"
    crossing = "# Speech\n\n## S0001-S0002 · Speaker A\nIs the import done? Yes, on Monday.\n\n## S0003 · Speaker B\nBoth at once.\n"
    renamed = good.replace("S0001 · Speaker A", "S0001 · Speaker B")
    for name, text, is_ok in [("paragraphs inside their turns pass", good, True),
                              ("a paragraph across two turns is refused", crossing, False),
                              ("a turn written as the other speaker is refused", renamed, False)]:
        speech_file_service.build_speech_path(paths).write_text(text, encoding="utf-8")
        codes = harness.codes(speech_validator.validate_speech_record(paths, False))
        harness.check(name, ("SPEECH_SPEAKER_NOT_HEARD" not in codes) == is_ok, str(codes))


def test_a_frame_s_speech_names_each_change_of_speaker_and_nothing_without_speakers():
    slices = transcript_slice_utils.build_frame_slices(SPOKEN["segments"], [0.0], 5.0, None)
    harness.check("the speakers are named where they change",
                  slices[0]["current"] == "Speaker A: Is the import done Speaker B: Yes on Monday Both at once",
                  slices[0]["current"])
    plain = [{key: value for key, value in segment.items() if key != "speaker"} for segment in SPOKEN["segments"]]
    harness.check("without speakers the slice is the words alone",
                  transcript_slice_utils.build_frame_slices(plain, [0.0], 5.0, None)[0]["current"]
                  == "Is the import done Yes on Monday Both at once")


def test_the_notes_print_a_speech_line_with_its_speaker():
    line = {"id": "S0002", "text": " Yes  on Monday", "speaker": "Speaker B"}
    harness.check("with a speaker", meeting_notes_utils._render_spoken_line(line) == "S0002 Speaker B: Yes on Monday")
    harness.check("without one", meeting_notes_utils._render_spoken_line({**line, "speaker": None}) == "S0002 Yes on Monday")


def test_the_transcript_download_is_one_block_per_speaker_turn():
    folder = Path(tempfile.mkdtemp())
    segments = [*SPOKEN["segments"][:2], {"text": " and the export", "start": 4.0, "end": 3725.0,
                                          "speaker": "Speaker B"}]
    (folder / output_contract.TRANSCRIPT_FILE).write_text(json.dumps({"segments": segments}))
    text = run_artifact_service.render_transcript_text(folder)
    harness.check("each turn has its times, its speaker and its words below, one speaker's lines joined",
                  text == "[00:00:00 - 00:00:02] Speaker A:\nIs the import done\n\n"
                          "[00:00:02 - 01:02:05] Speaker B:\nYes on Monday and the export\n", repr(text))


def test_a_paragraph_inside_one_turn_is_written_with_that_turn_s_speaker_defect():
    # A model wrote a one-line turn's paragraph as the next turn's speaker, and the run blocked on its copy of a letter
    # the program knows from the voices; the program now writes each paragraph inside one turn with its turn's speaker
    from services import chain_dispatch_service

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-sync.wav")
    phase_file_service.write_phase_file(paths, TRANSCRIBE, SPOKEN)
    lines = speech_markdown_utils.list_numbered_lines(SPOKEN)
    renamed = "# Speech\n\n## S0001 · Speaker B\nIs the import done?\n\n## S0002-S0003 · Speaker B\nYes, on Monday. Both at once.\n"
    parsed, _why = speech_markdown_utils.parse_speech(renamed)
    request = AttemptRequest(job_kind="speech_structuring", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"lines": lines})
    chain_dispatch_service._write_speech(request, parsed)
    codes = harness.codes(speech_validator.validate_speech_record(paths, False))
    harness.check("the paragraph is written as its turn's speaker and passes", "SPEECH_SPEAKER_NOT_HEARD" not in codes,
                  str(codes))
    harness.check("the file names Speaker A for S0001",
                  "## S0001 · Speaker A" in speech_file_service.build_speech_path(paths).read_text(encoding="utf-8"))


def test_a_paragraph_ends_where_the_next_one_begins_defect():
    # A model counting a long transcript started a paragraph on the line the one before ended on and wrote a heading's
    # last line short of its text; the run blocked. The program now sets each last line from the next paragraph's first
    from services import chain_dispatch_service

    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-sync.wav")
    phase_file_service.write_phase_file(paths, TRANSCRIBE, SPOKEN)
    lines = speech_markdown_utils.list_numbered_lines(SPOKEN)
    miscounted = "# Speech\n\n## S0001-S0002 · Speaker A\nIs the import done?\n\n## S0002 · Speaker B\nYes, on Monday. Both at once.\n"
    parsed, _why = speech_markdown_utils.parse_speech(miscounted)
    request = AttemptRequest(job_kind="speech_structuring", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"lines": lines})
    chain_dispatch_service._write_speech(request, parsed)
    codes = harness.codes(speech_validator.validate_speech_record(paths, False))
    harness.check("every line once, the file passes", not {"SPEECH_LINE_TWICE", "SPEECH_LINE_MISSING"} & set(codes), str(codes))
    text = speech_file_service.build_speech_path(paths).read_text(encoding="utf-8")
    harness.check("the spans are S0001 and S0002-S0003", "## S0001 · Speaker A" in text and "## S0002-S0003 · Speaker B" in text,
                  text)
    harness.check("first lines out of order are left for the check",
                  speech_markdown_utils.close_paragraph_spans({"paragraphs": [{"first": "S0002"}, {"first": "S0001"}]},
                                                              lines)["paragraphs"][0].get("last") is None)


def test_words_moved_across_a_line_cut_between_two_paragraphs_pass_defect():
    # A transcript cut a sentence across two lines, the model joined it in the later paragraph, and that paragraph read
    # as words added; the check now judges a paragraph with its neighbour of the same speaker, and a summary still fails
    cut = {"segments": [
        {"text": " AcmeSync keeps every single file in two regions and then every single night it quietly", "start": 0.0,
         "end": 3.0, "speaker": "Speaker A"},
        {"text": " copies each one to a second store in Frankfurt", "start": 3.0, "end": 4.0, "speaker": "Speaker A"}]}
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-sync.wav")
    phase_file_service.write_phase_file(paths, TRANSCRIBE, cut)
    joined = ("# Speech\n\n## S0001 · Speaker A\nAcmeSync keeps every single file in two regions.\n\n"
              "## S0002 · Speaker A\nAnd then every single night it quietly copies each one to a second store in Frankfurt.\n")
    summary = "# Speech\n\n## S0001 · Speaker A\nAcmeSync keeps files.\n\n## S0002 · Speaker A\nCopies.\n"
    for name, text, is_ok in [("words moved across the cut pass", joined, True), ("a summary of both fails", summary, False)]:
        speech_file_service.build_speech_path(paths).write_text(text, encoding="utf-8")
        codes = set(harness.codes(speech_validator.validate_speech_record(paths, False)))
        harness.check(name, not codes & {"SPEECH_PARAGRAPH_ADDED", "SPEECH_PARAGRAPH_SHORTENED"} if is_ok
                      else bool(codes & {"SPEECH_PARAGRAPH_ADDED", "SPEECH_PARAGRAPH_SHORTENED"}), str(codes))
