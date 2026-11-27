"""A run's ingesting profile: which shape its document is written in, picked from its tags,
kept in `ingesting-profile.json`, and handed to phases 4 and 5 as one instruction file. Every name is invented."""

import tempfile
from pathlib import Path

from support import harness

from models import ingesting_profile_vocabulary
from models.web_api_request import QueueRunRequest
from prompts import system_prompt_builder
from models.run_identity import RunIdentity
from services import ingesting_profile_service
from services.run_store_service import RunStore


def test_a_tag_naming_a_profile_or_one_of_its_aliases_picks_it():
    harness.check("an alias in other letter case", ingesting_profile_service.find_profile_in_tags(["Learn german"])
                  == "language-lesson")
    harness.check("the profile's own name", ingesting_profile_service.find_profile_in_tags(["sprint-change"])
                  == "sprint-change")
    harness.check("the first tag that names one wins",
                  ingesting_profile_service.find_profile_in_tags(["AcmeSync", "chat", "meeting"]) == "conversation")
    harness.check("no tag names one", ingesting_profile_service.find_profile_in_tags(["AcmeSync", "week 42"]) is None)


def test_the_profile_is_kept_in_the_run_folder_and_a_run_without_one_is_written_as_knowledge():
    folder = Path(tempfile.mkdtemp())
    harness.check("a run with no profile file is written as the default",
                  ingesting_profile_service.resolve_profile(folder) == ingesting_profile_vocabulary.DEFAULT_PROFILE == "knowledge")
    ingesting_profile_service.write_profile(folder, "conversation", "tag")
    harness.check("the file says the profile and who chose it",
                  ingesting_profile_service.read_profile(folder) == {"profile": "conversation", "chosenBy": "tag"},
                  str(ingesting_profile_service.read_profile(folder)))
    harness.check("and the run is written in it", ingesting_profile_service.resolve_profile(folder) == "conversation")
    try:
        ingesting_profile_service.write_profile(folder, "poem", "tag")
        refusal = ""
    except ValueError as exc:
        refusal = str(exc)
    harness.check("a profile not in the list is refused by name, naming the ones there are",
                  "poem" in refusal and "conversation" in refusal, refusal)


def test_every_profile_but_the_default_has_its_instruction_file_and_reaches_phases_4_and_5():
    for name in ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES:
        for phase in ("write", "review"):
            text = system_prompt_builder.build_system_prompt(phase, {"ingesting_profile": name})
            marker = f"BEGIN BASE: ingesting-profile-{name}.md"
            if name == ingesting_profile_vocabulary.DEFAULT_PROFILE:
                harness.check(f"{phase}: the default profile adds nothing", "BEGIN BASE" not in text)
            else:
                harness.check(f"{phase}: the {name} profile's file is in the prompt", marker in text)
    harness.check("a prompt with no profile is the default's",
                  system_prompt_builder.build_system_prompt("write", {})
                  == system_prompt_builder.build_system_prompt("write", {"ingesting_profile": "knowledge"}))


def test_the_page_lists_every_profile_with_its_aliases():
    listed = ingesting_profile_service.list_profiles()
    harness.check("one row per profile, the default first", [row["name"] for row in listed][0] == "knowledge"
                  and {row["name"] for row in listed} == set(ingesting_profile_vocabulary.PROFILE_ALIASES), str(listed))
    harness.check("each row carries its aliases", "chat" in next(row for row in listed if row["name"] == "conversation")["aliases"])


def test_the_writer_and_the_reviewer_are_told_the_profile_and_the_tags():
    from prompts import review_messages
    from prompts import write_messages

    plain = write_messages.build_write_system_text("knowledge", [], has_frames=True)
    shaped = write_messages.build_write_system_text("conversation", ["chat", "AcmeSync"], has_frames=True)
    harness.check("the default profile with no tag adds nothing", "BEGIN BASE" not in plain and "tagged" not in plain)
    harness.check("the profile's file reaches the writer", "BEGIN BASE: ingesting-profile-conversation.md" in shaped)
    harness.check("and the tags, as a hint", "The owner tagged this recording: chat, AcmeSync." in shaped, shaped[-600:])
    audio = write_messages.build_write_system_text("knowledge", [], has_frames=False)
    harness.check("a recording with no frame is told so", "This recording has no frames" in audio
                  and "This recording has no frames" not in plain)
    review = review_messages.build_review_system_text("language-lesson", ["learn German"], has_frames=False)
    harness.check("the reviewer gets the profile, the tags and the no-frame note",
                  "BEGIN BASE: ingesting-profile-language-lesson.md" in review and "learn German" in review
                  and "This recording has no frames" in review)


def test_a_language_lesson_is_written_in_the_language_it_is_held_in():
    from prompts import write_messages
    from schemas.document_contents import DocumentContents
    from schemas.document_parts import AgendaPoint
    from schemas.document_summary import DocumentClaim

    plain = write_messages.build_write_system_text("knowledge", [], has_frames=False)
    lesson = write_messages.build_write_system_text("language-lesson", [], has_frames=False)
    harness.check("the writer is told English unless the profile names another language",
                  "The document's language is English, unless the ingesting profile" in plain)
    harness.check("a language lesson names the language it is held in",
                  "The document's language is the language the lesson is held in" in lesson
                  and "The document's language is the language the lesson is held in" not in plain)
    described = [field.description or "" for schema in (DocumentContents, AgendaPoint, DocumentClaim)
                 for field in schema.model_fields.values()]
    harness.check("no answer field asks for English", not any("English" in text for text in described),
                  str([text for text in described if "English" in text]))


def test_a_recording_with_no_frame_is_offered_no_frame_tool():
    from models import tool_set_vocabulary
    from services import write_conversation_service

    def names(tool_set: str, submit: str, has_frames: bool) -> set[str]:
        return {spec["function"]["name"] for spec in
                write_conversation_service._build_tool_specs(tool_set, submit, False, has_frames)}

    video = names(tool_set_vocabulary.WRITE_TOOL_SET, "submit_chapter", True)
    audio = names(tool_set_vocabulary.WRITE_TOOL_SET, "submit_chapter", False)
    harness.check("a video's chapter has the frame tools", {"look", "cut", "read_frame"} <= video, str(video))
    harness.check("an audio recording's has none, and keeps its reading tools",
                  not ({"look", "cut", "mark", "mask", "undo_cut", "read_frame"} & audio)
                  and {"read_notes", "read_transcript", "submit_chapter"} <= audio, str(audio))
    review = names(tool_set_vocabulary.REVIEW_TOOL_SET, "submit_review", False)
    harness.check("nor does its review", not ({"read_frame_record", "look_at_frame", "look", "cut"} & review), str(review))


def _make_finished_run(root: Path) -> Path:
    """A run folder with phases 1 to 5 on disk, its tags, its log and its kept recording."""

    run = root / "output" / "Jonas-weekly"
    for name in ("01_transcript.json", "02_frames.json", "02_speech.md", "03_notes.md", "03_batch_summaries.json",
                 "04_document.md", "run-tags.json", "run-name.json", "worker-feedback.md", "audio.wav"):
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text(f"<{name}>", encoding="utf-8")
    for name in ("frames/frame-000.jpg", "03_processed-frames/frame-000.md", "04_writing/draft.md",
                 "05_review/review.md", "logs/t/run.json", "recording/Jonas-weekly.mp3", "Jonas-weekly/Jonas-weekly.md"):
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text(f"<{name}>", encoding="utf-8")
    ingesting_profile_service.write_profile(run, "conversation", "tag")

    return run


def test_a_run_converts_to_another_profile_as_a_new_run_folder_holding_phases_1_to_3():
    from services import profile_convert_service
    from services import run_recording_service

    run = _make_finished_run(Path(tempfile.mkdtemp()))
    before = sorted(str(path.relative_to(run)) for path in run.rglob("*"))
    target = profile_convert_service.copy_run_for_profile(run, "knowledge")
    copied = sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file())
    harness.check("the new folder sits beside the run, named for the profile", target == run.parent / "Jonas-weekly-knowledge")
    harness.check("phases 1 to 3, the frames and the tags are copied, and nothing of phases 4 to 6 or the logs",
                  copied == ["01_transcript.json", "02_frames.json", "02_speech.md", "03_batch_summaries.json",
                             "03_notes.md", "03_processed-frames/frame-000.md", "frames/frame-000.jpg",
                             "ingesting-profile.json", "run-tags.json"], str(copied))
    harness.check("the new folder's profile, chosen by the conversion",
                  ingesting_profile_service.read_profile(target) == {"profile": "knowledge", "chosenBy": "convert"})
    harness.check("the run converted from is untouched",
                  sorted(str(path.relative_to(run)) for path in run.rglob("*")) == before
                  and ingesting_profile_service.read_profile(run) == {"profile": "conversation", "chosenBy": "tag"})
    try:
        profile_convert_service.copy_run_for_profile(run, "knowledge")
        refusal = ""
    except ValueError as exc:
        refusal = str(exc)
    harness.check("a second conversion to the same profile is refused, naming the folder that holds it",
                  "Jonas-weekly-knowledge" in refusal, refusal)
    inbox_audio = run.parent.parent / "inbox" / "Jonas-weekly.mp3"
    inbox_audio.parent.mkdir()
    inbox_audio.write_bytes(b"<audio>")
    record = {"repository": str(inbox_audio.parent), "module": inbox_audio.name}
    harness.check("the recording a converted run reads is the one its run record names, while it is in the inbox",
                  run_recording_service.find_run_recording(run, record) == inbox_audio)
    harness.check("none when the run names none", run_recording_service.find_run_recording(run, {}) is None)


def test_a_conversion_that_fails_part_way_leaves_no_folder_and_the_next_try_is_not_refused():
    from services import profile_convert_service

    run = _make_finished_run(Path(tempfile.mkdtemp()))
    real_copy = profile_convert_service._copy_by_link
    calls = []

    def fail_on_the_second(source, target):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("no space left on device")
        real_copy(source, target)

    profile_convert_service._copy_by_link = fail_on_the_second
    try:
        try:
            profile_convert_service.copy_run_for_profile(run, "knowledge")
            outcome = "converted"
        except OSError as exc:
            outcome = f"raised {exc}"
    finally:
        profile_convert_service._copy_by_link = real_copy
    target = profile_convert_service.build_target_folder(run, "knowledge")
    harness.check("the failure is raised and the half folder is gone", outcome.startswith("raised") and not target.exists(),
                  f"{outcome} {target.exists()}")
    again = profile_convert_service.copy_run_for_profile(run, "knowledge")
    harness.check("the next try converts instead of being told the folder exists", again == target and target.is_dir())


def test_the_converted_run_is_queued_from_phase_4_forced_past_the_processed_once_check():
    from models.run_job import RunJobRequest
    from services import job_queue_service

    request = RunJobRequest(video=Path("/output/Jonas-weekly/recording/Jonas-weekly.mp3"),
                            output_root=Path("/output/Jonas-weekly-knowledge"), from_phase=4, is_forced=True)
    command = job_queue_service.build_run_command(request)
    harness.check("the line runs phase 4 on, forced", "--from" in command and "4" in command and "--force" in command,
                  str(command))
    harness.check("a request with no force has none",
                  "--force" not in job_queue_service.build_run_command(RunJobRequest(video=Path("/a.mp4"), output_root=Path("/o"))))


def test_convert_copies_the_run_and_the_page_queues_its_phase_4():
    from fastapi.testclient import TestClient

    from cli import build_parser
    from services import job_queue_service
    from services import run_operations_service
    from services import run_status_service
    from services import web_api_service

    root = Path(tempfile.mkdtemp())
    run = _make_finished_run(root)
    inbox_audio = root / "inbox" / "Jonas-weekly.mp3"
    inbox_audio.parent.mkdir()
    inbox_audio.write_bytes(b"<audio>")
    (run / "logs" / "t" / "run.json").unlink()
    RunStore.create(run / "logs" / "t" / "run.json", RunIdentity(run_id="t", repository=str(inbox_audio.parent),
                                                                  module=inbox_audio.name, output_root=str(run)),
                    budgets={}, models=[]).save()
    args = build_parser().parse_args(["convert", "--output", str(run), "--to", "presentation"])
    harness.check("the command takes the profile as --to", args.ingesting_profile == "presentation")
    result = run_operations_service.copy_run_for_profile(run, "presentation")
    harness.check("copied, and the run line to write it printed", result.exit_code == 0
                  and "--from 4 --force" in result.output, result.output)
    refused = run_operations_service.copy_run_for_profile(run, "presentation")
    harness.check("again refused, nothing written over", refused.exit_code == 2 and "exists already" in refused.output,
                  refused.output)

    saved = (dict(run_status_service.RUN_ROOTS), job_queue_service.QUEUE_DIR)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": run.parent})
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.post("/api/runs/output/Jonas-weekly/convert", json={"profile": "meeting"}).json()
        listed = client.get("/api/ingesting-profiles").json()
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved[0])
        job_queue_service.QUEUE_DIR = saved[1]
    command = (answer.get("jobs") or [{}])[0].get("command", [])
    harness.check("the page's convert queues phase 4 on in the new folder, forced",
                  answer.get("exit_code") == 0 and "--force" in command and "4" in command
                  and str((run.parent / "Jonas-weekly-meeting").resolve()) in command, str(answer))
    harness.check("the page lists the profiles", [row["name"] for row in listed["profiles"]][0] == "knowledge", str(listed))
    purpose = (answer.get("jobs") or [{}])[0].get("request", {}).get("purpose")
    harness.check("the queued job says in words what it runs: an audio run's phase 4 alone",
                  purpose == "convert to meeting · phase 4 only (document writing)", str(purpose))


def test_a_conversion_names_each_phase_its_job_runs():
    from services import profile_convert_service

    root = Path(tempfile.mkdtemp())
    audio = _make_finished_run(root)
    video = root / "output" / "Gateway-review"
    video.mkdir(parents=True)
    harness.check("an audio run ends at phase 4, its phases 5 and 6 off; phase 3 by its audio title",
                  [profile_convert_service.describe_job(audio, "knowledge", first) for first in (4, 3)]
                  == ["convert to knowledge · phase 4 only (document writing)",
                      "convert to knowledge · phases 3 to 4 (notes preparing, document writing)"])
    harness.check("a video run runs on to phase 6",
                  profile_convert_service.describe_job(video, "meeting", 4)
                  == "convert to meeting · phases 4 to 6 (document writing, review, critique collection)",
                  profile_convert_service.describe_job(video, "meeting", 4))


def test_the_page_learns_each_run_s_medium_and_profile_and_every_tag_in_use():
    from fastapi.testclient import TestClient

    from services import run_status_service
    from services import run_tags_service
    from services import web_api_service

    root = Path(tempfile.mkdtemp())
    run = _make_finished_run(root)
    RunStore.create(run / "logs" / "t" / "run.json", RunIdentity(run_id="t", repository=str(root / "inbox"),
                                                                  module="Jonas-weekly.mp3", output_root=str(run)),
                    budgets={}, models=[]).save()
    other = root / "output" / "AcmeSync-review"
    other.mkdir()
    run_tags_service.write_tags(run, ["learn German", "week 42"])
    run_tags_service.write_tags(other, ["Week 42", "AcmeSync"])
    saved = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    try:
        client = TestClient(web_api_service.build_app())
        runs = {row["name"]: row for row in client.get("/api/runs").json()}
        tags = client.get("/api/tags").json()
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved)
    harness.check("an audio run says so, with its profile", runs["Jonas-weekly"]["is_audio"] is True
                  and runs["Jonas-weekly"]["ingesting_profile"] == "conversation", str(runs["Jonas-weekly"]))
    harness.check("a profile written down counts as chosen; the default for a run with none does not",
                  runs["Jonas-weekly"]["is_profile_chosen"] is True and runs["AcmeSync-review"]["is_profile_chosen"] is False,
                  str({name: row["is_profile_chosen"] for name, row in runs.items()}))
    harness.check("a run with no record is no audio run, written in the default profile",
                  runs["AcmeSync-review"]["is_audio"] is False and runs["AcmeSync-review"]["ingesting_profile"] == "knowledge")
    harness.check("every tag in use once, letter case ignored, in name order",
                  [tag.casefold() for tag in tags["tags"]] == ["acmesync", "learn german", "week 42"], str(tags))
    harness.check("with the profiles and their aliases", "chat" in next(row for row in tags["profiles"]
                                                                      if row["name"] == "conversation")["aliases"])


def test_a_profile_chosen_by_hand_travels_from_the_page_to_the_run_folder():
    from fastapi.testclient import TestClient

    from cli import build_parser
    from cli import run_command
    from models.run_job import RunJobRequest
    from services import job_queue_service
    from services import mcp_server_service
    from services import server_client_service
    from services import web_api_service

    args = build_parser().parse_args(["run", "--video", "Jonas-call.mp3", "--output", "out", "--ingesting-profile",
                                      "meeting"])
    harness.check("`run --ingesting-profile` is read", args.ingesting_profile == "meeting", str(args.ingesting_profile))
    harness.check("no flag is no choice", build_parser().parse_args(["run", "--video", "a.mp3", "--output", "o"])
                  .ingesting_profile == "")
    try:
        build_parser().parse_args(["run", "--video", "a.mp3", "--output", "o", "--ingesting-profile", "poem"])
        refused = False
    except SystemExit:
        refused = True
    harness.check("a name not in the list is refused", refused)
    request = RunJobRequest(video=Path("/in/a.mp3"), output_root=Path("/out/a"), ingesting_profile="meeting")
    command = job_queue_service.build_run_command(request)
    harness.check("the queued line carries it", command[-2:] == ["--ingesting-profile", "meeting"], str(command))
    harness.check("the request keeps it through its JSON form",
                  RunJobRequest.from_dict(request.as_dict()).ingesting_profile == "meeting")
    harness.check("a request stored before it existed reads with none",
                  RunJobRequest.from_dict({"video": "/in/a.mp3", "output_root": "/out/a"}).ingesting_profile == "")
    folder = Path(tempfile.mkdtemp())
    run_command.write_chosen_profile(folder, "meeting", is_dry_run=False)
    harness.check("written before phase 1 as the person's choice",
                  ingesting_profile_service.read_profile(folder) == {"profile": "meeting", "chosenBy": "user"})
    empty = Path(tempfile.mkdtemp())
    run_command.write_chosen_profile(empty, "meeting", is_dry_run=True)
    run_command.write_chosen_profile(empty, "", is_dry_run=False)
    harness.check("a dry run, or no choice, writes nothing", ingesting_profile_service.read_profile(empty) is None)
    client = TestClient(web_api_service.build_app())
    refusal = client.post("/api/jobs", json={"items": [{"video": "/in/a.mp3", "output": "/out/a"}],
                                             "ingesting_profile": "poem"})
    harness.check("the page's queue refuses a name not in the list, by name",
                  refusal.status_code == 400 and "poem" in refusal.text and "meeting" in refusal.text, refusal.text)
    listed = client.get("/api/ingesting-profiles").json()["profiles"]
    harness.check("the page lists each profile with what it is for",
                  all(row["description"] == ingesting_profile_vocabulary.PROFILE_DESCRIPTIONS[row["name"]] for row in listed),
                  str(listed))
    sent = []
    previous = server_client_service.call_server
    server_client_service.call_server = lambda method, path, **kwargs: sent.append(kwargs.get("json")) or []
    try:
        mcp_server_service.queue_run(QueueRunRequest(video="/in/a.mp3", output="/out/a", ingesting_profile="meeting"))
    finally:
        server_client_service.call_server = previous
    harness.check("an agent's queue_run carries it", sent[0]["ingesting_profile"] == "meeting", str(sent))


def test_every_profile_has_its_tags_and_its_description():
    harness.check("the aliases and the descriptions name the same profiles, in one order",
                  tuple(ingesting_profile_vocabulary.PROFILE_ALIASES) == ingesting_profile_vocabulary.PROFILES,
                  str(ingesting_profile_vocabulary.PROFILES))


def test_a_takeaway_run_without_a_speech_file_is_named_an_audio_run_by_its_conversion_job():
    from services import profile_convert_service

    run = Path(tempfile.mkdtemp()) / "output" / "Jonas-weekly"
    run.mkdir(parents=True)
    (run / "02_frames.json").write_text('{"items": [], "medium": "audio"}', encoding="utf-8")
    described = profile_convert_service.describe_job(run, "conversation", 4)
    harness.check("phases 5 and 6 are off for it, so the job runs phase 4 only", "phase 4 only" in described, described)
