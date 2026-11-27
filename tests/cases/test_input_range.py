"""The part of the input a run processes: a start and an end time for a video or an audio
recording, a first and a last page for a PDF, kept in the run folder and read the same by every phase."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import cli
from cli import run_command
from models import output_contract
from models.input_range_spec import InputRangeSpec
from models.run_errors import BadTarget
from models.run_job import RunJobRequest
from services import input_range_service
from services import input_preview_service
from services import job_queue_service
from services import run_operations_service
from services import run_recording_service
from services import run_status_service
from services import web_api_service
from utils import document_name_utils


def _is_refused(function,
                **arguments) -> bool:
    """Whether `function(**arguments)` refuses with a ValueError that says why."""

    try:
        function(**arguments)
    except ValueError as error:
        return bool(str(error))

    return False


def _is_refused_as_target(function,
                          **arguments) -> bool:
    """Whether `function(**arguments)` refuses the run line with a BadTarget that says why."""

    try:
        function(**arguments)
    except BadTarget as error:
        return bool(str(error))

    return False


def test_a_time_is_read_as_seconds_minutes_or_hours():
    parse = input_range_service.parse_time
    harness.check("seconds", parse(text="90") == 90.0)
    harness.check("minutes and seconds", parse(text="12:30") == 750.0)
    harness.check("hours, minutes and seconds", parse(text="1:05:00") == 3900.0)
    harness.check("a fraction of a second", parse(text="0:01.5") == 1.5)
    for wrong in ("", "abc", "1:75", "-3", "1:2:3:4"):
        harness.check(f"{wrong!r} is refused by name", _is_refused(parse, text=wrong))


def test_a_page_range_is_read_as_its_first_and_last_page():
    parse = input_range_service.parse_pages
    harness.check("from and to", parse(text="12-40") == (12, 40))
    harness.check("one page", parse(text="7") == (7, 7))
    harness.check("spaces", parse(text=" 3 - 5 ") == (3, 5))
    for wrong in ("", "0-3", "5-2", "a-b", "3-"):
        harness.check(f"{wrong!r} is refused by name", _is_refused(parse, text=wrong))


def test_the_range_is_kept_in_the_run_folder_and_read_back():
    folder = Path(tempfile.mkdtemp())
    harness.check("no file: the whole input", input_range_service.read_range(folder=folder) is None)
    times = InputRangeSpec(start_s=750.0, end_s=3900.0)
    input_range_service.write_range(folder=folder, input_range=times)
    harness.check("written under its own name", (folder / output_contract.RUN_INPUT_RANGE_FILE).is_file())
    harness.check("a time range read back", input_range_service.read_range(folder=folder) == times)
    pages = InputRangeSpec(first_page=12, last_page=40)
    input_range_service.write_range(folder=folder, input_range=pages)
    harness.check("a page range read back", input_range_service.read_range(folder=folder) == pages)
    (folder / output_contract.RUN_INPUT_RANGE_FILE).write_text("not json", encoding="utf-8")
    harness.check("an unreadable file: the whole input", input_range_service.read_range(folder=folder) is None)


def test_a_range_from_the_run_line_is_checked_before_it_is_kept():
    build = input_range_service.build_range
    harness.check("nothing given: no range", build(start="", end="", pages="") is None)
    harness.check("a start alone runs to the end", build(start="10:00", end="", pages="") == InputRangeSpec(start_s=600.0))
    harness.check("an end alone starts at 0", build(start="", end="5:00", pages="") == InputRangeSpec(end_s=300.0))
    harness.check("pages", build(start="", end="", pages="2-4") == InputRangeSpec(first_page=2, last_page=4))
    harness.check("an end before the start is refused",
                  _is_refused(build, start="5:00", end="4:00", pages=""))
    harness.check("times and pages together are refused",
                  _is_refused(build, start="1:00", end="", pages="2-4"))


def test_a_time_window_is_cut_from_each_part_it_crosses():
    # Three parts of 100 s, 50 s and 100 s: the window 120 s to 200 s starts 20 s into the second part and ends 50 s
    # into the third
    windows = input_range_service.list_part_windows(durations=[100.0, 50.0, 100.0],
                                                    input_range=InputRangeSpec(start_s=120.0, end_s=200.0))
    harness.check("the first part, wholly before, is skipped", [window.part_index for window in windows] == [1, 2])
    harness.check("the second part from 20 s to its end", (windows[0].start_s, windows[0].end_s) == (20.0, 50.0))
    harness.check("on the meeting clock at 120 s", windows[0].offset_s == 120.0)
    harness.check("the third part from 0 s to 50 s", (windows[1].start_s, windows[1].end_s) == (0.0, 50.0))
    harness.check("on the meeting clock at 150 s", windows[1].offset_s == 150.0)
    whole = input_range_service.list_part_windows(durations=[100.0], input_range=InputRangeSpec(start_s=30.0))
    harness.check("a window with no end runs to the part's end", (whole[0].start_s, whole[0].end_s) == (30.0, 100.0))
    harness.check("a window past the recording's end is refused by name",
                  _is_refused(input_range_service.list_part_windows, durations=[100.0],
                                 input_range=InputRangeSpec(start_s=150.0)))


def test_the_pages_of_a_range_are_the_pdfs_own_page_indexes():
    indexes = input_range_service.list_page_indexes(input_range=InputRangeSpec(first_page=2, last_page=4), page_count=10)
    harness.check("pages 2 to 4 are indexes 1 to 3", indexes == [1, 2, 3])
    harness.check("no range: every page", input_range_service.list_page_indexes(input_range=None, page_count=3) == [0, 1, 2])
    harness.check("a last page past the PDF's end runs to its last page",
                  input_range_service.list_page_indexes(input_range=InputRangeSpec(first_page=9, last_page=40),
                                                        page_count=10) == [8, 9])
    harness.check("a first page past the PDF's end is refused by name",
                  _is_refused(input_range_service.list_page_indexes,
                                 input_range=InputRangeSpec(first_page=11, last_page=12), page_count=10))


def test_the_run_line_and_the_queue_carry_the_range():
    args = cli.build_parser().parse_args(["run", "--video", "acmesync-demo.mp4", "--output", "out",
                                          "--start", "12:30", "--end", "1:05:00"])
    harness.check("the run line reads the window", run_command.read_input_range(args)
                  == InputRangeSpec(start_s=750.0, end_s=3900.0))
    wrong = cli.build_parser().parse_args(["run", "--video", "acmesync-demo.mp4", "--output", "out", "--pages", "9-3"])
    harness.check("a wrong range is refused before the run starts",
                  _is_refused_as_target(run_command.read_input_range, args=wrong))
    request = RunJobRequest(video=Path("/inbox/acmesync-manual.pdf"), output_root=Path("/output/acmesync-manual"),
                            range_pages="12-40")
    command = job_queue_service.build_run_command(request)
    harness.check("the queued line carries the pages", "--pages" in command
                  and command[command.index("--pages") + 1] == "12-40", str(command))
    harness.check("the request keeps them through its JSON form",
                  RunJobRequest.from_dict(request.as_dict()).range_pages == "12-40")


def test_the_page_s_request_with_a_wrong_range_is_refused_before_it_is_queued():
    client = TestClient(web_api_service.build_app())
    answer = client.post("/api/jobs", json={"items": [{"video": "/inbox/acmesync-demo.mp4",
                                                       "output": "/output/acmesync-demo"}],
                                            "range_start": "5:00", "range_end": "4:00"})
    harness.check("refused with why", answer.status_code == 400 and "before the start" in answer.text, answer.text)


def test_the_range_is_written_into_the_run_folder_before_phase_1():
    folder = Path(tempfile.mkdtemp())
    run_command.write_chosen_range(folder, InputRangeSpec(first_page=2, last_page=3), is_dry_run=True)
    harness.check("a dry run writes nothing", input_range_service.read_range(folder=folder) is None)
    run_command.write_chosen_range(folder, InputRangeSpec(first_page=2, last_page=3), is_dry_run=False)
    harness.check("kept", input_range_service.read_range(folder=folder) == InputRangeSpec(first_page=2, last_page=3))
    run_command.write_chosen_range(folder, None, is_dry_run=False)
    harness.check("no range on a resume leaves the kept one",
                  input_range_service.read_range(folder=folder) == InputRangeSpec(first_page=2, last_page=3))


def test_the_page_is_told_a_recording_s_length_and_a_run_s_part():
    audio = Path(__file__).resolve().parents[1] / "resources" / "audio" / "two-voices.wav"
    harness.check("the made audio's length, read by ffprobe", 28.0 <= run_status_service._read_recording_seconds([audio])
                  <= 29.0, str(run_status_service._read_recording_seconds([audio])))
    harness.check("a PDF has no length", run_status_service._read_recording_seconds([Path("guide.pdf")]) == 0.0)
    folder = Path(tempfile.mkdtemp())
    harness.check("no range: no part", run_status_service._describe_input_range(folder) == "")
    input_range_service.write_range(folder=folder, input_range=InputRangeSpec(first_page=2, last_page=4))
    harness.check("a run's part", run_status_service._describe_input_range(folder) == "pages 2 to 4")


def test_the_published_document_names_the_part_it_covers():
    front = document_name_utils.render_front_matter("Refunds", "", [], "", ["acme"], part="12:30 to 1:05:00")
    harness.check("the part stands before the tags", 'part: "12:30 to 1:05:00"\ntags: ["acme"]' in front, front)
    harness.check("no part: the block as it was", "part:" not in document_name_utils.render_front_matter(
        "Refunds", "", [], "", ["acme"]))


class _RunsIn:
    """The page's runs read from a made output folder, its queue a folder of its own; both put back after."""

    def __init__(self) -> None:
        self.folder = Path(tempfile.mkdtemp())

    def __enter__(self):
        self.saved = (dict(run_status_service.RUN_ROOTS), job_queue_service.QUEUE_DIR,
                      input_preview_service.PREVIEWS_DIR)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update({"output": self.folder / "output"})
        job_queue_service.QUEUE_DIR = self.folder / "ui"
        input_preview_service.PREVIEWS_DIR = self.folder / "previews"

        return self.folder

    def __exit__(self, *exc):
        roots, queue, previews = self.saved
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(roots)
        job_queue_service.QUEUE_DIR = queue
        input_preview_service.PREVIEWS_DIR = previews


def _build_run_keeping(folder: Path,
                       recording: Path) -> Path:
    """A run folder that keeps `recording` in its folder, as a done run does."""

    run = folder / "output" / "acmesync-call"
    kept = run / run_recording_service.RECORDING_DIR
    kept.mkdir(parents=True)
    (kept / recording.name).write_bytes(recording.read_bytes())
    (kept / ".origin.json").write_text(json.dumps({"name": recording.name}), encoding="utf-8")

    return run


def test_a_run_s_input_is_described_and_another_part_of_it_is_queued_in_a_new_run_folder():
    audio = Path(__file__).resolve().parents[1] / "resources" / "audio" / "two-voices.wav"
    with _RunsIn() as folder:
        run = _build_run_keeping(folder, audio)
        input_range_service.write_range(folder=run, input_range=InputRangeSpec(start_s=5.0, end_s=15.0))
        client = TestClient(web_api_service.build_app())
        described = client.get("/api/runs/output/acmesync-call/input").json()
        peaks = client.get("/api/runs/output/acmesync-call/preview/waveform?bins=20").json()
        refused = client.post("/api/runs/output/acmesync-call/part", json={"range_start": "0:20", "range_end": "0:10"})
        answer = client.post("/api/runs/output/acmesync-call/part", json={"range_start": "0:10", "range_end": "0:25"})
        jobs = json.loads((folder / "ui" / job_queue_service.JOBS_FILE).read_text(encoding="utf-8"))
    harness.check("its length, its part and its kind", described["duration_s"] == 28.5 and described["is_audio"]
                  and (described["range_start"], described["range_end"]) == ("0:05", "0:15"), str(described))
    harness.check("its waveform", len(peaks["peaks"]) == 20, str(peaks))
    harness.check("a wrong part refused with why", refused.status_code == 400 and "before the start" in refused.text,
                  refused.text)
    branch = Path(answer.json()["records"][0]["folder"])
    harness.check("a new run folder beside it holds the new part",
                  input_range_service.read_range(folder=branch) == InputRangeSpec(start_s=10.0, end_s=25.0), str(branch))
    harness.check("the run it was made from keeps its own part",
                  input_range_service.read_range(folder=run) == InputRangeSpec(start_s=5.0, end_s=15.0))
    harness.check("its run is queued from phase 1, named by its part",
                  len(jobs) == 1 and jobs[0]["request"]["from_phase"] == 1
                  and jobs[0]["request"]["purpose"] == "0:10 to 0:25 · phases 1 on", str(jobs))


def test_a_branch_of_a_part_back_to_the_whole_input_keeps_no_range():
    folder = Path(tempfile.mkdtemp())
    with _RunsIn() as runs:
        run = _build_run_keeping(runs, Path(__file__).resolve().parents[1] / "resources" / "audio" / "two-voices.wav")
        input_range_service.write_range(folder=run, input_range=InputRangeSpec(start_s=5.0))
        whole = run_operations_service.create_branch(run, 1, input_range=InputRangeSpec())
        refused = run_operations_service.create_branch(run, 3, input_range=InputRangeSpec(start_s=1.0))
    branch = Path(whole.records[0]["folder"])
    harness.check("the whole input again: no range kept", input_range_service.read_range(folder=branch) is None
                  and folder.is_dir(), str(branch))
    harness.check("a part is phase 1's: a branch from a later phase refused", refused.exit_code == 2
                  and "phase 1" in refused.lines[0], str(refused.lines))


def test_a_range_is_described_in_the_words_the_page_shows():
    describe = input_range_service.describe_range
    harness.check("times", describe(input_range=InputRangeSpec(start_s=750.0, end_s=3900.0)) == "12:30 to 1:05:00")
    harness.check("a start alone", describe(input_range=InputRangeSpec(start_s=65.0)) == "1:05 to the end")
    harness.check("pages", describe(input_range=InputRangeSpec(first_page=12, last_page=40)) == "pages 12 to 40")
    harness.check("one page", describe(input_range=InputRangeSpec(first_page=7, last_page=7)) == "page 7")
