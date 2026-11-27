"""The takeaway profile: the fast profile. Phase 1 tells no speakers apart, phase 2
makes no call, phase 3 has nothing to read, and phase 4 is ONE call over the transcript lines, with no thinking, that
lists what the recording settled and what it left open; the program checks it, takes the ids out for the reader and
publishes it. Every name is invented."""

import json
import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx
from support import harness

import config as app_config

from graph.nodes import prepare_speech_node
from graph.nodes import transcribe_audio_node
from graph.nodes import write_document_node
from models import ingesting_profile_vocabulary
from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from models.diarization_errors import DiarizationUnavailable
from models.run_context import RunContext
from models.run_identity import RunIdentity
from prompts import speech_structuring_messages
from prompts import takeaway_messages
from graph.nodes.retry_loop import attempt_request_builder
from services import chain_dispatch_service
from services import diarization_service
from services import document_folder_service
from services import ingesting_profile_service
from services import phase_file_service
from services import recording_medium_service
from services import run_tags_service
from services import takeaway_document_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import speech_markdown_utils
from utils import takeaway_markdown_utils
from validators import takeaway_validator

# Two people disagree on the colour of the build badge, then agree; they never settle how often the build runs
SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "hi all so the AcmeSync build badge should be blue", "speaker": "Speaker A"},
    {"start": 4.0, "end": 8.0, "text": "no I think green fits better", "speaker": "Speaker B"},
    {"start": 8.0, "end": 12.0, "text": "okay fine blue it is", "speaker": "Speaker B"},
    {"start": 12.0, "end": 16.0, "text": "and the build runs nightly", "speaker": "Speaker A"},
    {"start": 16.0, "end": 20.0, "text": "weekly would be enough let's see", "speaker": "Speaker B"},
]
GOOD = """# The AcmeSync build badge and schedule

## Agreed
- The build badge is blue. [S0001, S0003]

## Not settled
- It is not known whether the build runs nightly or weekly: both were proposed. [S0004, S0005]
"""


def _make_context(model,
                  profile: str | None = "takeaway") -> RunContext:
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="Jonas-weekly.mp3")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": SEGMENTS})
    if profile:
        ingesting_profile_service.write_profile(paths.output_root, profile, "user")
    run_dir = paths.output_root / "logs" / "t"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=model)


def _run_phase_4(ctx: RunContext,
                 state: dict | None = None) -> dict:
    previous = app_config.settings.budgets.max_attempts
    app_config.settings.budgets.max_attempts = 2
    try:
        return write_document_node.write_document_node(state or {"phases": {}}, {"configurable": {"ctx": ctx}})
    finally:
        app_config.settings.budgets.max_attempts = previous


def _validate(text: str) -> set[str]:
    ctx = _make_context(fakes.scripted())
    path = takeaway_document_service.build_takeaway_path(ctx.paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    return harness.codes(takeaway_validator.validate_takeaway_record(ctx.paths, ["S0001", "S0002", "S0003", "S0004",
                                                                                "S0005"]))


def test_the_takeaway_is_read_back_and_rendered_with_or_without_its_ids():
    parsed, why = takeaway_markdown_utils.parse_takeaway(GOOD)
    harness.check("read: the title, each section's lines with their ids", why == "" and parsed == {
        "title": "The AcmeSync build badge and schedule",
        "agreed": [{"text": "The build badge is blue.", "ids": ["S0001", "S0003"]}],
        "not_settled": [{"text": "It is not known whether the build runs nightly or weekly: both were proposed.",
                         "ids": ["S0004", "S0005"]}]}, str(parsed))
    reader = takeaway_markdown_utils.render_takeaway(parsed, is_cited=False)
    harness.check("the reader's copy has no id", "S000" not in reader and "- The build badge is blue.\n" in reader, reader)
    harness.check("the program's copy reads back the same", takeaway_markdown_utils.parse_takeaway(
        takeaway_markdown_utils.render_takeaway(parsed, is_cited=True))[0] == parsed)
    harness.check("a section left out is named", "Not settled" in takeaway_markdown_utils.parse_takeaway(
        GOOD.split("## Not settled")[0])[1])
    harness.check("prose outside a list is refused", takeaway_markdown_utils.parse_takeaway(
        GOOD.replace("## Agreed\n", "## Agreed\nThey talked about the badge.\n"))[0] is None)
    harness.check("a section of its own is refused, naming the two there are", "Agreed" in takeaway_markdown_utils
                  .parse_takeaway(GOOD.replace("## Not settled", "## Discussion"))[1])


def test_the_check_wants_every_line_cited_with_lines_of_the_transcript():
    harness.check("a good takeaway passes", _validate(GOOD) == set(), str(_validate(GOOD)))
    harness.check("a line with no ids", "TAKEAWAY_LINE_UNCITED" in _validate(GOOD.replace(" [S0001, S0003]", "")))
    harness.check("an id the transcript does not hold", "TAKEAWAY_LINE_UNKNOWN" in _validate(GOOD.replace("S0003", "S0099")))
    harness.check("nothing in either section", "TAKEAWAY_EMPTY" in _validate(
        "# Badge\n\n## Agreed\n\n## Not settled\n"))
    harness.check("an unreadable file", "TAKEAWAY_UNREADABLE" in _validate("They talked about the badge."))


def test_the_instruction_teaches_the_headings_the_check_reads():
    text = (app_config.AGENT_CONTEXT_DIR / "instructions" / "audio" / "takeaway.md").read_text(encoding="utf-8")
    harness.check("both section headings, as the parser reads them",
                  all(f"## {heading}" in text for heading in takeaway_markdown_utils.SECTION_KEYS), text[:300])
    harness.check("the result of a discussion, never the discussion", "never the discussion" in text)


def test_takeaway_is_a_profile_picked_by_hand_or_a_tag_never_by_phase_2_s_call():
    harness.check("listed for the page", "takeaway" in ingesting_profile_service.list_profile_names())
    harness.check("a tag picks it", ingesting_profile_service.find_profile_in_tags(["key facts"]) == "takeaway")
    harness.check("phase 2's call is never offered it",
                  "takeaway" not in ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES
                  and "`takeaway`" not in speech_structuring_messages._render_profiles())
    folder = Path(tempfile.mkdtemp())
    run_tags_service.write_tags(folder, ["takeaway"])
    harness.check("a tag decides it before phase 2 writes the file, so phase 1 can read it",
                  ingesting_profile_service.is_one_call_profile(folder)
                  and ingesting_profile_service.read_profile(folder) is None)
    harness.check("and the run takes the audio workflow, even from a video",
                  recording_medium_service.is_audio_run_recording(folder, folder / "AcmeSync-review.mp4"))


def _refuse_diarization(*args, **kwargs):
    raise DiarizationUnavailable("a stand-in that records it was asked")


def test_phase_1_tells_no_speakers_apart_for_a_takeaway_run():
    ctx = _make_context(fakes.scripted())
    diarization = app_config.settings.audio.diarization
    previous_on, previous_lister = diarization.is_on, diarization_service.compute_speaker_turns
    diarization.is_on = True
    diarization_service.compute_speaker_turns = _refuse_diarization
    segments = {"segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}
    try:
        skipped = transcribe_audio_node._build_transcript_with_speakers(ctx, segments, [(Path("a.wav"), 0.0)])
        app_config.settings.audio.takeaway.is_diarization_on = True
        asked = transcribe_audio_node._build_transcript_with_speakers(ctx, segments, [(Path("a.wav"), 0.0)])
    finally:
        app_config.settings.audio.takeaway.is_diarization_on = False
        diarization.is_on, diarization_service.compute_speaker_turns = previous_on, previous_lister
    harness.check("no diarization for a takeaway run, recorded off", skipped["diarization"] == {"status": "off"},
                  str(skipped["diarization"]))
    harness.check("switched on, it is asked", asked["diarization"]["status"] == "unavailable", str(asked["diarization"]))


def test_phase_2_makes_no_call_and_closes_phase_3():
    model = fakes.scripted()
    ctx = _make_context(model)
    state = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("phases 2 and 3 complete, no call", state["phases"] == {
        "frames_preparing": "complete", "frames_processing": "complete"} and model.call_count == 0, str(state))
    frames = phase_file_service.read_phase_file(ctx.paths, 2)
    harness.check("the empty frame list says audio", frames["items"] == [] and frames["medium"] == "audio", str(frames))
    harness.check("no speech file", not (ctx.paths.output_root / output_contract.SPEECH_FILE).exists())
    again = prepare_speech_node.prepare_speech_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("a resume reads it done with no speech file", again["phases"]["frames_preparing"] == "complete",
                  str(again))


def test_phase_4_is_one_call_whose_takeaway_is_published_without_its_ids():
    model = fakes.scripted(fakes.says(GOOD))
    ctx = _make_context(model)
    state = _run_phase_4(ctx)
    harness.check("one call, phase 4 complete", model.call_count == 1 and state["phases"] == {"finalize": "complete"},
                  str((model.call_count, state)))
    sent = model.calls[0][-1].content[0]["text"]
    harness.check("the call is sent every line with its id, time and speaker",
                  "S0002 [00:04] Speaker B: no I think green fits better" in sent and "from S0001 to S0005" in sent,
                  sent[:600])
    document = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("04_document.md is the reader's: the two lists, no id",
                  "- The build badge is blue." in document and "## Not settled" in document and "S000" not in document,
                  document)
    published = document_folder_service.build_document_path(ctx.paths)
    provenance = json.loads((published.parent / output_contract.DOCUMENT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("the published folder holds the document", "- The build badge is blue." in published.read_text(
        encoding="utf-8"), str(published))
    harness.check("its provenance names each line's transcript lines, with their words",
                  [entry["id"] for entry in provenance["facts"][0]["entries"]] == ["S0001", "S0003"]
                  and provenance["facts"][0]["entries"][1]["text"] == "okay fine blue it is", str(provenance["facts"]))
    again = fakes.scripted()
    ctx.model = again
    ctx.store.set_phase("finalize", "running")
    _run_phase_4(ctx)
    harness.check("a resume keeps the accepted takeaway and calls nothing", again.call_count == 0)


def test_a_rejected_takeaway_is_asked_again_with_why_and_blocks_after_its_attempts():
    model = fakes.scripted(fakes.says(GOOD.replace(" [S0001, S0003]", "")), fakes.says(GOOD.replace("S0003", "S0099")))
    ctx = _make_context(model)
    state = _run_phase_4(ctx)
    harness.check("blocked after its two attempts", state["phases"] == {"finalize": "blocked"} and model.call_count == 2,
                  str(state))
    retry = model.calls[1][-1].content[0]["text"]
    harness.check("the retry was told which line names no transcript line", "names no transcript line" in retry,
                  retry[-500:])
    harness.check("no document is written", not ctx.paths.summary.exists())


def test_the_call_is_sent_with_no_thinking_unless_switched_on():
    harness.check("off by default: no thinking", chain_dispatch_service.select_reasoning_effort("takeaway") == "none")
    harness.check("every other kind keeps its role's", chain_dispatch_service.select_reasoning_effort("section") is None)
    app_config.settings.audio.takeaway.is_thinking_on = True
    try:
        harness.check("switched on, the role's own", chain_dispatch_service.select_reasoning_effort("takeaway") is None)
    finally:
        app_config.settings.audio.takeaway.is_thinking_on = False


def test_a_dry_run_renders_the_one_call_and_writes_nothing():
    model = fakes.scripted()
    ctx = _make_context(model)
    state = _run_phase_4(ctx, {"phases": {}, "is_dry_run": True})
    harness.check("no call, no document", model.call_count == 0 and not ctx.paths.summary.exists()
                  and state["phases"] == {"finalize": "complete"}, str(state))
    rendered = attempt_request_builder.build_standalone_request(ctx, "takeaway", inputs={
        "lines": speech_markdown_utils.list_numbered_lines({"segments": SEGMENTS}), "tags": [], "recording": "x.mp3"})
    system = chain_dispatch_service.build_messages(rendered)[0].content
    harness.check("the system text is takeaway.md", "Write the result of a discussion" in str(system), str(system)[:200])
    tagged = takeaway_messages.build_takeaway_messages(attempt_request_builder.build_standalone_request(
        ctx, "takeaway", inputs={"lines": speech_markdown_utils.list_numbered_lines({"segments": SEGMENTS}),
                                 "tags": ["week 42"], "recording": "Jonas-weekly.mp3"}))[1].content[0]["text"]
    harness.check("the tags reach the call as a hint, with the recording's name and length",
                  "The owner tagged it: week 42." in tagged and "Jonas-weekly.mp3, 00:20 long" in tagged, tagged[:300])


def test_another_profile_is_untouched_by_the_takeaway_path():
    model = fakes.scripted()
    ctx = _make_context(model, profile="meeting")
    harness.check("a meeting run is no one-call run", not ingesting_profile_service.is_one_call_profile(ctx.paths.output_root))
    harness.check("an audio file without a choice is still audio, a video still video",
                  recording_medium_service.is_audio_run_recording(ctx.paths.output_root, Path("a.mp3"))
                  and not recording_medium_service.is_audio_run_recording(ctx.paths.output_root, Path("a.mp4")))


def test_a_dry_run_over_a_transcript_with_no_speech_writes_no_phase_status():
    """A dry run calls nothing and writes nothing: one over an empty transcript recorded phase 4 failed in the run
    record, which a resume then read."""

    ctx = _make_context(fakes.scripted())
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": []})
    state = _run_phase_4(ctx, {"phases": {}, "is_dry_run": True})
    harness.check("the dry run says it has nothing to take the facts from, and records nothing",
                  state["phases"] == {"finalize": "failed"} and ctx.store.get_phase("finalize") != "failed",
                  f"{state} {ctx.store.get_phase('finalize')}")


def test_a_takeaway_run_with_no_transcript_file_fails_phase_4_by_name():
    ctx = _make_context(fakes.scripted())
    phase_file_service.build_phase_file_path(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"]).unlink()
    try:
        state = _run_phase_4(ctx)
    except MissingPhaseInput as exc:
        state = {"raised": str(exc)}
    harness.check("phase 4 is recorded failed, the missing file named, nothing raised",
                  state.get("phases") == {"finalize": "failed"} and ctx.store.get_phase("finalize") == "failed", str(state))
