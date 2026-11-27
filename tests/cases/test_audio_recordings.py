"""An audio file is a recording like a video: the inbox lists it, an upload, a rename and a
move take it, and a folder of audio parts is one meeting. Every name is invented."""

import io
import tempfile
import wave
from pathlib import Path

from support import harness

import config
from graph.nodes import write_document_node
from models.web_api_request import PhaseFlowRequest
from services import audio_extraction_service
from services import inbox_rename_service
from services import inbox_service
from services import recording_hash_service
from services import recording_parts_service
from services import run_status_service
from services import upload_service
from services import upload_bundle_service
from utils import document_plan_utils


class _Swap:
    """The run roots, the inbox and the hash cache pointed at a test's own folders, put back afterwards."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        self.saved = (dict(run_status_service.RUN_ROOTS), config.INBOX_DIR, recording_hash_service.CACHE_PATH)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update({"output": self.root / "output"})
        config.INBOX_DIR = self.root / "inbox"
        recording_hash_service.set_cache_path(self.root / "recording-hashes.json")

    def __exit__(self, *exc):
        roots, inbox, cache = self.saved
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(roots)
        config.INBOX_DIR = inbox
        recording_hash_service.set_cache_path(cache)


def _make_inbox() -> Path:
    """An inbox with a video, two audio files, a folder of two audio parts and a folder of one audio file."""

    inbox = Path(tempfile.mkdtemp()) / "inbox"
    inbox.mkdir()
    for name in ("AcmeSync-review.mp4", "Jonas-lesson.mp3", "Gateway-chat.M4A", "notes.txt"):
        (inbox / name).write_bytes(b"fixture")
    (inbox / "Weekly").mkdir()
    for name in ("part 2.mp3", "part 10.mp3"):
        (inbox / "Weekly" / name).write_bytes(b"fixture")
    (inbox / "Standup").mkdir()
    (inbox / "Standup" / "standup.wav").write_bytes(b"fixture")

    return inbox


def test_the_inbox_lists_audio_files_and_folders_of_audio_parts():
    inbox = _make_inbox()
    names = [path.name for path in inbox_service.list_inbox_videos(inbox)]
    harness.check("audio files are items beside the video, a folder of parts is one item, a folder of one file is "
                  "that file", names == ["AcmeSync-review.mp4", "Gateway-chat.M4A", "Jonas-lesson.mp3", "standup.wav",
                                         "Weekly"], str(names))
    parts = [path.name for path in recording_parts_service.list_recording_parts(inbox / "Weekly")]
    harness.check("audio parts are read in natural name order", parts == ["part 2.mp3", "part 10.mp3"], str(parts))
    with _Swap(inbox.parent):
        listed = {item["name"]: item["is_audio"] for item in run_status_service.list_inbox_items(inbox, inbox.parent / "output")}
    harness.check("the page is told which items are audio, so it asks no Process as of them",
                  listed == {"AcmeSync-review.mp4": False, "Gateway-chat.M4A": True, "Jonas-lesson.mp3": True,
                             "standup.wav": True, "Weekly": True}, str(listed))


def test_an_audio_recording_is_told_from_a_video():
    inbox = _make_inbox()
    harness.check("an mp3 is audio", inbox_service.is_audio_recording(inbox / "Jonas-lesson.mp3"))
    harness.check("an M4A in capitals is audio", inbox_service.is_audio_recording(inbox / "Gateway-chat.M4A"))
    harness.check("a folder of audio parts is audio", inbox_service.is_audio_recording(inbox / "Weekly"))
    harness.check("a video is not audio", not inbox_service.is_audio_recording(inbox / "AcmeSync-review.mp4"))
    types = {name: inbox_service.describe_file_type(inbox / name)
             for name in ("Jonas-lesson.mp3", "Weekly", "AcmeSync-review.mp4", "Gateway-report.PDF")}
    harness.check("the page is told each file's own type, a folder of audio parts audio, a PDF in capitals pdf",
                  types == {"Jonas-lesson.mp3": "audio", "Weekly": "audio", "AcmeSync-review.mp4": "video",
                            "Gateway-report.PDF": "pdf"}, str(types))


def test_an_audio_upload_is_saved_and_a_text_file_is_still_refused():
    root = Path(tempfile.mkdtemp())
    inbox = root / "inbox"
    inbox.mkdir()
    with _Swap(root):
        saved = upload_service.save_upload(io.BytesIO(b"<audio bytes>"), inbox, "Jonas-lesson.mp3")
        try:
            upload_service.choose_upload_path(inbox, "notes.txt")
            refusal = ""
        except Exception as exc:  # noqa: BLE001 (the refusal's words are what is checked)
            refusal = str(exc)
    harness.check("an mp3 upload lands in the inbox", saved == inbox / "Jonas-lesson.mp3" and saved.is_file(), str(saved))
    harness.check("a text file is refused, naming audio among what the inbox takes",
                  "is not a video or an audio file" in refusal and ".mp3" in refusal, refusal)


def test_a_folder_upload_saves_audio_files_as_recordings():
    root = Path(tempfile.mkdtemp())
    inbox = root / "inbox"
    inbox.mkdir()
    with _Swap(root):
        saved = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<part one>"), "drop/part 1.mp3"),
                                                                 (io.BytesIO(b"<part two>"), "drop/part 2.mp3")])
    harness.check("both audio parts are saved as recordings", saved["added"] == ["part 1.mp3", "part 2.mp3"], str(saved))


def test_an_audio_file_in_the_inbox_can_be_renamed():
    inbox = _make_inbox()
    renamed = inbox_rename_service.rename_inbox_video(inbox, inbox / "Jonas-lesson.mp3", "Jonas-lesson-week-2.mp3")
    harness.check("renamed under its new audio name", renamed == (inbox / "Jonas-lesson-week-2.mp3").resolve() and renamed.is_file(),
                  str(renamed))


def _write_silence(path: Path,
                   seconds: float) -> None:
    """A mono 16 kHz WAV of silence, `seconds` long."""

    with wave.open(str(path), "wb") as written:
        written.setnchannels(1)
        written.setsampwidth(2)
        written.setframerate(16000)
        written.writeframes(b"\x00\x00" * int(16000 * seconds))


def test_an_audio_file_s_length_is_read_by_ffprobe():
    folder = Path(tempfile.mkdtemp())
    _write_silence(folder / "Jonas-lesson.wav", 1.5)
    length = audio_extraction_service.read_duration(folder / "Jonas-lesson.wav")
    harness.check("1.5 s of audio is 1.5 s long", abs(length - 1.5) < 0.01, str(length))


def test_a_folder_of_audio_parts_has_each_part_s_length_in_reading_order():
    folder = Path(tempfile.mkdtemp()) / "Weekly"
    folder.mkdir()
    _write_silence(folder / "part 10.wav", 2.0)
    _write_silence(folder / "part 2.wav", 1.0)
    lengths = recording_parts_service.list_part_durations(folder)
    harness.check("part 2 (1 s) is read before part 10 (2 s)", [round(value, 2) for value in lengths] == [1.0, 2.0],
                  str(lengths))


def test_a_run_line_takes_tags_and_the_queue_writes_one_tag_flag_each():
    from cli import build_parser
    from cli import run_command
    from models.run_job import RunJobRequest
    from services import job_queue_service
    from services import run_tags_service

    args = build_parser().parse_args(["run", "--video", "Jonas-lesson.mp3", "--output", "out", "--tag", "learn German",
                                      "--tag", "chat"])
    harness.check("--tag is repeatable", args.tags == ["learn German", "chat"], str(args.tags))
    request = RunJobRequest(video=Path("/inbox/Jonas-lesson.mp3"), output_root=Path("/output/Jonas-lesson"),
                            tags=("learn German", "chat"))
    command = job_queue_service.build_run_command(request)
    harness.check("the queued line carries one --tag per tag", command[-4:] == ["--tag", "learn German", "--tag", "chat"],
                  str(command))
    harness.check("the request keeps its tags through its JSON form",
                  RunJobRequest.from_dict(request.as_dict()).tags == ("learn German", "chat"))
    harness.check("a request stored before tags existed reads with none",
                  RunJobRequest.from_dict({"video": "/inbox/a.mp4", "output_root": "/output/a"}).tags == ())
    folder = Path(tempfile.mkdtemp())
    run_tags_service.write_tags(folder, ["meeting"])
    run_command.add_given_tags(folder, ["chat", "Meeting"], is_dry_run=False)
    harness.check("the given tags are added after the run's own, a tag in other letter case kept once",
                  run_tags_service.read_tags(folder) == ["meeting", "chat"], str(run_tags_service.read_tags(folder)))
    empty = Path(tempfile.mkdtemp())
    run_command.add_given_tags(empty, ["chat"], is_dry_run=True)
    harness.check("a dry run writes no tag", run_tags_service.read_tags(empty) == [])


def test_an_audio_recording_s_length_is_its_parts_lengths_added():
    from services import frame_extraction_service

    folder = Path(tempfile.mkdtemp()) / "Weekly"
    folder.mkdir()
    _write_silence(folder / "part 1.wav", 1.0)
    _write_silence(folder / "part 2.wav", 2.5)
    single = folder.parent / "Jonas-lesson.wav"
    _write_silence(single, 1.5)
    (folder.parent / "not-audio.mp3").write_bytes(b"not audio")
    harness.check("a folder of audio parts lasts as long as its parts together",
                  frame_extraction_service.read_duration_s(folder) == 3.5, str(frame_extraction_service.read_duration_s(folder)))
    harness.check("one audio file lasts its own length", frame_extraction_service.read_duration_s(single) == 1.5,
                  str(frame_extraction_service.read_duration_s(single)))
    harness.check("an audio file ffprobe cannot read has no length", frame_extraction_service.read_duration_s(
        folder.parent / "not-audio.mp3") is None)


def test_an_audio_recording_kept_in_its_run_is_not_compressed_to_720p():
    from services import run_operations_service

    run = Path(tempfile.mkdtemp()) / "Jonas-lesson"
    (run / "recording").mkdir(parents=True)
    (run / "recording" / "Jonas-lesson.mp3").write_bytes(b"<audio>")
    (run / "recording" / ".origin.json").write_text('{"name": "Jonas-lesson.mp3"}', encoding="utf-8")
    refusal = run_operations_service.find_compression_refusal(run)
    harness.check("refused by name: an audio recording has no picture to shrink",
                  refusal is not None and refusal.exit_code == 2 and "audio" in refusal.output,
                  refusal.output if refusal else "not refused")


def test_an_audio_run_names_its_phases_for_what_they_do_and_a_video_run_keeps_the_phase_names():
    audio = {number: run_status_service.describe_phase(number, True) for number in range(1, 7)}
    video = {number: run_status_service.describe_phase(number, False) for number in range(1, 7)}
    harness.check("an audio run's phase 2 and 3 are speech and notes preparing",
                  (audio[2]["title"], audio[3]["title"]) == ("speech preparing", "notes preparing"), str(audio))
    harness.check("its other phases keep their names", [audio[number]["title"] for number in (1, 4, 5, 6)]
                  == ["transcribe", "document writing", "review", "critique collection"], str(audio))
    harness.check("a video run's phases are named as before",
                  [video[number]["title"] for number in range(1, 7)]
                  == ["transcribe", "frames preparing", "frames processing", "document writing", "review",
                      "critique collection"], str(video))
    harness.check("no help of an audio run speaks of frames it does not have, but phase 6's saying it has none",
                  all("frames/" not in audio[number]["reads"] and "the frames" not in audio[number]["summary"]
                      for number in range(1, 6)), str(audio))
    harness.check("phase 2 of an audio run calls the model", audio[2]["is_model_used"] is True)


def test_phase_4_names_frames_in_its_first_line_only_for_a_recording_that_has_them():
    entries = [{"kind": "speech"}, {"kind": "speech"}, {"kind": "facts"}]
    integration = {"entries": [{"kind": "speech"}] * 5, "frames": []}
    audio = write_document_node.describe_writing_input(entries, integration, 1200)
    video = write_document_node.describe_writing_input(entries, {**integration, "frames": [{"index": 1}]}, 1200)
    harness.check("an audio run names its paragraphs and prepared sections, no frame and no notes",
                  audio == "phase 4: 04_document.md from 2 speech paragraphs (5 transcript lines), 1 prepared sections, "
                           "1,200 tokens", audio)
    plain = write_document_node.describe_writing_input(entries[:2], integration, 1200)
    harness.check("with nothing prepared, no section count", plain.endswith("(5 transcript lines), 1,200 tokens"), plain)
    harness.check("a video run counts its frames", video.endswith("of 5 lines), 1 frames, 1,200 tokens"), video)


def test_a_recording_with_no_frame_has_a_moment_per_paragraph_with_its_speech_whole():
    long_text = "the gateway sends the token to the provider and waits for its answer " * 5
    integration = {"frames": [], "entries": [
        {"kind": "speech", "text": long_text, "start": 0.0, "timestamps": [0.0], "source": "transcript"},
        {"kind": "speech", "text": "Jonas asks when the import runs.", "start": 40.0, "timestamps": [40.0],
         "source": "transcript"},
        {"kind": "speech", "text": "On Monday.", "start": 52.0, "timestamps": [52.0], "source": "transcript"}]}
    moments = document_plan_utils.list_moments(integration, 200)
    harness.check("three paragraphs are three moments", [moment["start"] for moment in moments] == [0.0, 40.0, 52.0],
                  str(moments))
    harness.check("each with its speech whole, never cut at the moment limit",
                  moments[0]["speech"] == " ".join(long_text.split()) and moments[2]["speech"] == "On Monday.",
                  moments[0]["speech"][-40:])


def test_an_audio_run_shows_no_review_and_no_critique_collection():
    """The owner: an audio recording's flow has no critic, so its detail view has no phase 5 or 6 card."""

    from models.run_identity import RunIdentity
    from services.run_store_service import RunStore
    root = Path(tempfile.mkdtemp())
    numbers = {}
    for name, module in (("Jonas-weekly", "Jonas-weekly.mp3"), ("AcmeSync-review", "AcmeSync-review.mp4")):
        run = root / "output" / name
        RunStore.create(run / "logs" / "t" / "run.json", RunIdentity(run_id="t", repository=str(root / "inbox"),
                                                                      module=module, output_root=str(run)),
                        budgets={}, models=[]).save()
    saved = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    try:
        for name in ("Jonas-weekly", "AcmeSync-review"):
            numbers[name] = [phase["number"] for phase in run_status_service.describe_run_folder("output", name)["phases"]]
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved)
    harness.check("an audio run's phases end at phase 4; a video run keeps all six",
                  numbers == {"Jonas-weekly": [1, 2, 3, 4], "AcmeSync-review": [1, 2, 3, 4, 5, 6]}, str(numbers))


def test_an_audio_run_s_phase_3_that_prepared_nothing_says_so():
    """The owner found an audio run's phase 3 "complete" with no file to open: its profile prepares nothing for so
    few paragraphs, and the card now says so."""

    import json
    from models.run_identity import RunIdentity
    from services.run_store_service import RunStore
    root = Path(tempfile.mkdtemp())
    run = root / "output" / "Jonas-weekly"
    store = RunStore.create(run / "logs" / "t" / "run.json", RunIdentity(run_id="t", repository=str(root / "inbox"),
                                                                          module="Jonas-weekly.mp3", output_root=str(run)),
                            budgets={}, models=[])
    store.set_phase("frames_processing", "complete")
    store.save()
    saved = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    try:
        bare = {phase["number"]: phase for phase in run_status_service.describe_run_folder("output", "Jonas-weekly")["phases"]}
        (run / "03_prepared").mkdir()
        prepared = {phase["number"]: phase for phase in run_status_service.describe_run_folder("output", "Jonas-weekly")["phases"]}
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved)
    harness.check("complete with no prepared notes: the card says nothing was to prepare",
                  bare[3].get("note") == "nothing to prepare", json.dumps(bare[3])[:300])
    harness.check("once notes are prepared, no such note", "note" not in prepared[3], json.dumps(prepared[3])[:300])


def test_a_video_processed_as_audio_takes_the_audio_workflow_from_the_page_to_its_status():
    """The owner: a video never run can be processed as audio, its sound alone, as if it had no picture."""

    from cli import build_parser
    from cli import run_command
    from models.run_identity import RunIdentity
    from models.run_job import RunJobRequest
    from services import job_queue_service
    from services import recording_medium_service
    from services.run_store_service import RunStore
    root = Path(tempfile.mkdtemp())
    video = root / "inbox" / "AcmeSync-review.mp4"
    plain = root / "output" / "plain"
    chosen = root / "output" / "AcmeSync-review"
    for run in (plain, chosen):
        RunStore.create(run / "logs" / "t" / "run.json", RunIdentity(run_id="t", repository=str(root / "inbox"),
                                                                      module=video.name, output_root=str(run)),
                        budgets={}, models=[]).save()
    harness.check("a video with no choice takes the video workflow",
                  not recording_medium_service.is_audio_run_recording(plain, video))
    args = build_parser().parse_args(["run", "--video", str(video), "--output", str(chosen), "--as-audio"])
    harness.check("`run --as-audio` is read", args.is_run_as_audio)
    run_command.write_chosen_medium(chosen, args.is_run_as_audio, is_dry_run=True)
    harness.check("a dry run writes no choice", recording_medium_service.read_chosen_medium(chosen) is None)
    run_command.write_chosen_medium(chosen, args.is_run_as_audio, is_dry_run=False)
    harness.check("written before phase 1, the video then takes the audio workflow",
                  recording_medium_service.read_chosen_medium(chosen) == "audio"
                  and recording_medium_service.is_audio_run_recording(chosen, video))
    harness.check("an audio file with no choice is audio", recording_medium_service.is_audio_run_recording(
        plain, root / "inbox" / "Jonas-weekly.mp3"))
    try:
        recording_medium_service.write_chosen_medium(chosen, "pictures")
        refusal = ""
    except ValueError as exc:
        refusal = str(exc)
    harness.check("a medium not in the list is refused by name", "pictures" in refusal and "audio" in refusal, refusal)
    request = RunJobRequest(video=video, output_root=chosen, is_run_as_audio=True)
    command = job_queue_service.build_run_command(request)
    harness.check("the queued line carries --as-audio", command[-1] == "--as-audio", str(command))
    harness.check("the request keeps it through its JSON form", RunJobRequest.from_dict(request.as_dict()).is_run_as_audio)
    with _Swap(root):
        statuses = {name: run_status_service.describe_run_folder("output", name) for name in ("plain", "AcmeSync-review")}
    harness.check("the detail view shows the video run as audio, with no review and no critique collection, and the other as video",
                  [statuses["AcmeSync-review"]["is_audio"], statuses["plain"]["is_audio"]] == [True, False]
                  and [phase["number"] for phase in statuses["AcmeSync-review"]["phases"]] == [1, 2, 3, 4],
                  str({name: (status["is_audio"], [phase["number"] for phase in status["phases"]])
                       for name, status in statuses.items()}))


def test_the_phases_route_lists_the_phases_of_the_flow_asked_defect():
    # Defect: the page listed a video's six phases for a run chosen as audio, review and critique collection included.
    # Fix: /api/phases takes the flow (medium, workflow, ingesting profile) and titles each phase for it, the ones it
    # never runs off.
    from services import web_api_service

    def read(**flow):
        return {row["number"]: (row.get("title"), row["is_on"])
                for row in web_api_service.list_phases(flow=PhaseFlowRequest(**flow))}

    audio = read(medium="audio")
    harness.check("an audio run's phases 2 and 3 are its own and it has no review or critique collection",
                  audio[2][0] == "speech preparing" and not audio[5][1] and not audio[6][1] and audio[4][1], str(audio))
    fast = read(medium="video", workflow="fast-processing")
    harness.check("a fast video has no review or critique collection", not fast[5][1] and fast[2][0] == "frames preparing", str(fast))
    harness.check("the takeaway profile is an audio run", not read(ingesting_profile="takeaway")[5][1], "")
    pdf = read(medium="pdf-text-only")
    harness.check("a PDF read as text only names its figures and has no review",
                  pdf[3][0] == "figures reading" and not pdf[5][1], str(pdf))


def test_a_markdown_conversion_shows_no_phase_3_defect():
    # A PDF's markdown conversion showed a phase 3 card, "nothing to read", for a phase that reads nothing.
    # Now its phase 3 is off with review and critique collection, so neither the run page nor the run form shows it.
    from services import web_api_service

    flow = {row["number"]: row["is_on"] for row in web_api_service.list_phases(
        flow=PhaseFlowRequest(medium="pdf-markdown-conversion"))}
    harness.check("phases 3, 5 and 6 off, 1, 2 and 4 on",
                  [number for number, is_on in sorted(flow.items()) if is_on] == [1, 2, 4], str(flow))
