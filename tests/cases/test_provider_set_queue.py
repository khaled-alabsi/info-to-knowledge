"""The queue's lanes: a job runs in the lane of its worker set; jobs of one lane run one after
another, jobs of different lanes at the same time; a job holding the queue holds its own lane only; one run folder is
never written by two jobs. Made data: a stand-in `main.py` that starts, says so through a named pipe and waits for its
release through another, so no test waits on the clock."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

from support import harness

import config
from models import run_job
from models.run_job import RunJobRequest
from services import job_queue_service

def _point_at_own_settings(text: str = '{"defaultWorkerSet": "default"}') -> tuple:
    """Points the settings at a folder of the test's own holding `text` as the default file, and returns what to point
    back to. Never the owner's files: a set they put on a machine moves its jobs to that machine's lane, and a test
    asking the set's lane would wait for ever."""

    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME)
    folder = Path(tempfile.mkdtemp())
    (folder / "playbook.default.json").write_text(text, encoding="utf-8")
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME = folder, ""

    return previous


def _point_back(previous: tuple) -> None:
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME = previous


_FAKE_MAIN = '''
import os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(args[args.index("--video") + 1]).stem
folder = Path(os.environ["I2K_PIPES"])
if name.startswith("fail"):
    sys.exit(1)
with open(folder / f"started-{name}", "w") as started:
    started.write("up")
with open(folder / f"release-{name}") as release:
    release.read()
sys.exit(0)
'''


def _make_pipes(pipes: Path,
                names: list[str]) -> None:
    for name in names:
        os.mkfifo(pipes / f"started-{name}")
        os.mkfifo(pipes / f"release-{name}")


def _wait_started(pipes: Path,
                  name: str) -> None:
    """Returns when the stand-in job `name` is up: its child writes to the pipe once it runs."""

    with open(pipes / f"started-{name}") as started:
        started.read()


def _release(pipes: Path,
             name: str) -> None:
    with open(pipes / f"release-{name}", "w") as release:
        release.write("go")


def _request(name: str,
             worker_set: str,
             output_root: str | None = None) -> RunJobRequest:
    return RunJobRequest(video=Path(f"/in/{name}.mp4"), output_root=Path(output_root or f"/out/{name}"),
                         worker_set=worker_set)


def _use_stand_in(pipes: Path, retries: int = 0, on_failure: str = "continue") -> tuple:
    os.environ["I2K_PIPES"] = str(pipes)
    main = pipes / "main.py"
    main.write_text(_FAKE_MAIN, encoding="utf-8")

    return (job_queue_service.set_command_prefix([sys.executable, str(main)]),
            job_queue_service.set_run_lister(lambda: []),
            job_queue_service.set_queue_settings_reader(
                lambda profile: config.QueueSettings(retries=retries, on_failure=on_failure)),
            _point_at_own_settings())


def _give_back(previous: tuple) -> None:
    job_queue_service.set_command_prefix(previous[0])
    job_queue_service.set_run_lister(previous[1])
    job_queue_service.set_queue_settings_reader(previous[2])
    _point_back(previous[3])


def _run_in_thread(queue_dir: Path,
                   lane: str,
                   results: list) -> threading.Thread:
    thread = threading.Thread(target=lambda: results.append(job_queue_service.run_next_job(queue_dir, lane)))
    thread.start()

    return thread


def test_the_run_line_carries_the_worker_set_and_a_job_knows_its_lane():
    command = job_queue_service.build_run_command(_request("a", "second-host"))
    harness.check("the run line names the worker set", command[-2:] == ["--worker-set", "second-host"], str(command))
    plain = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a")))
    harness.check("a request with no set leaves the flag out", "--worker-set" not in plain, str(plain))
    folder = Path(tempfile.mkdtemp())
    (folder / "run-worker-set.json").write_text('{"name": "Mac"}', encoding="utf-8")
    kept = run_job.RunJob(job_id="k", request=RunJobRequest(video=Path("/in/a.mp4"), output_root=folder))
    asked = run_job.RunJob(job_id="a", request=RunJobRequest(video=Path("/in/a.mp4"), output_root=folder, worker_set="second-host"))
    none = run_job.RunJob(job_id="n", request=RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/none")))
    previous = _point_at_own_settings()
    try:
        lanes = tuple(job_queue_service.read_job_lane(job, "default") for job in (asked, kept, none))
    finally:
        _point_back(previous)
    harness.check("the lane is the request's set, else the folder's, else the default's",
                  lanes == ("second-host", "Mac", "default"), str(lanes))


def test_two_sets_on_one_machine_share_one_lane_defect():
    # Defect: jobs waited for each other by set name, so a "strict" set on the same machine ran beside a default job
    # on the same card. Fix: a job's lane is the machine its set names, its set's own name only when it names none.
    previous = _point_at_own_settings(
        '{"defaultWorkerSet": "default", "machine": "office", "workerSets": {"strict": {"machine": "office"},'
        ' "remote": {"machine": "gpu-box"}, "plain": {}}}')
    try:
        lanes = tuple(job_queue_service.read_job_lane(run_job.RunJob(job_id=name, request=_request(name, name)), "default")
                      for name in ("default", "strict", "remote", "plain"))
    finally:
        _point_back(previous)
    harness.check("default, strict and a set naming no machine share the office lane, remote has its machine's",
                  lanes == ("office", "office", "gpu-box", "office"), str(lanes))


def test_a_profile_can_name_the_set_its_jobs_run_on():
    """The owner: each profile can override the worker set, and a profile that names none uses the default settings'."""

    folder = Path(tempfile.mkdtemp())
    (folder / "playbook.default.json").write_text('{"defaultWorkerSet": "default"}', encoding="utf-8")
    (folder / "playbook.strict.json").write_text('{"defaultWorkerSet": "second-host"}', encoding="utf-8")
    (folder / "playbook.quiet.json").write_text("{}", encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, config.PROFILE_NAME = folder, ""
    try:
        def lane(profile: str) -> str:
            job = run_job.RunJob(job_id=profile or "none", request=RunJobRequest(
                video=Path("/in/a.mp4"), output_root=Path("/out/" + (profile or "none")), profile=profile))
            return job_queue_service.read_job_lane(job, job_queue_service.read_default_worker_set())

        lanes = (lane(""), lane("strict"), lane("quiet"))
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, config.PROFILE_NAME = previous
    harness.check("a job with no profile, a profile naming second-host and a profile naming none take default, second-host, default",
                  lanes == ("default", "second-host", "default"), str(lanes))


def test_two_jobs_on_two_sets_run_at_the_same_time():
    queue_dir, pipes = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_pipes(pipes, ["one", "two"])
    previous = _use_stand_in(pipes)
    try:
        job_queue_service.queue_jobs(queue_dir, [_request("one", "second-host"), _request("two", "Mac")])
        results: list = []
        threads = [_run_in_thread(queue_dir, "second-host", results), _run_in_thread(queue_dir, "Mac", results)]
        _wait_started(pipes, "one")
        _wait_started(pipes, "two")
        both_running = sorted(job.request.worker_set for job in job_queue_service.list_jobs(queue_dir)
                              if job.status == run_job.JOB_RUNNING)
        _release(pipes, "one")
        _release(pipes, "two")
        for thread in threads:
            thread.join()
    finally:
        _give_back(previous)
    harness.check("both jobs are running at once, each on its own set", both_running == ["Mac", "second-host"], str(both_running))
    harness.check("both end finished", sorted(job.status for job in results) == ["finished", "finished"],
                  str([job.status for job in results]))


def test_two_jobs_on_one_set_run_one_after_the_other():
    queue_dir, pipes = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_pipes(pipes, ["one", "two"])
    previous = _use_stand_in(pipes)
    try:
        job_queue_service.queue_jobs(queue_dir, [_request("one", "second-host"), _request("two", "second-host")])
        results: list = []
        first = _run_in_thread(queue_dir, "second-host", results)
        _wait_started(pipes, "one")
        while_first_runs = job_queue_service.run_next_job(queue_dir, "second-host")
        _release(pipes, "one")
        first.join()
        second = _run_in_thread(queue_dir, "second-host", results)
        _wait_started(pipes, "two")
        _release(pipes, "two")
        second.join()
    finally:
        _give_back(previous)
    harness.check("the second job does not start while the first runs", while_first_runs is None, str(while_first_runs))
    harness.check("it starts once the first ended", [job.request.video.stem for job in results] == ["one", "two"],
                  str([job.request.video.stem for job in results]))


def test_a_job_holding_the_queue_holds_its_own_set_only():
    queue_dir, pipes = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_pipes(pipes, ["other", "behind"])
    previous = _use_stand_in(pipes, retries=0, on_failure="block")
    try:
        job_queue_service.queue_jobs(queue_dir, [_request("fail-second", "second-host"), _request("behind", "second-host"),
                                                 _request("other", "Mac")])
        failed = job_queue_service.run_next_job(queue_dir, "second-host")
        held_lane = job_queue_service.run_next_job(queue_dir, "second-host")
        results: list = []
        other = _run_in_thread(queue_dir, "Mac", results)
        _wait_started(pipes, "other")
        _release(pipes, "other")
        other.join()
    finally:
        _give_back(previous)
    harness.check("the failed job holds the queue", failed is not None and failed.status == run_job.JOB_BLOCKED,
                  str(failed.status if failed else failed))
    harness.check("the job behind it on its own set waits", held_lane is None, str(held_lane))
    harness.check("the job on the other set runs and finishes", [job.status for job in results] == ["finished"],
                  str([job.status for job in results]))


def test_a_run_folder_is_never_written_by_two_jobs():
    queue_dir, pipes = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_pipes(pipes, ["one", "two"])
    previous = _use_stand_in(pipes)
    try:
        job_queue_service.queue_jobs(queue_dir, [_request("one", "second-host", "/out/same"), _request("two", "Mac", "/out/same")])
        results: list = []
        first = _run_in_thread(queue_dir, "second-host", results)
        _wait_started(pipes, "one")
        on_other_set = job_queue_service.run_next_job(queue_dir, "Mac")
        _release(pipes, "one")
        first.join()
    finally:
        _give_back(previous)
    harness.check("a job on another set waits while a job runs in its folder", on_other_set is None, str(on_other_set))


def test_a_run_the_queue_did_not_start_holds_every_lane_and_the_queues_own_do_not():
    running = run_job.RunJob(job_id="r", request=_request("a", "second-host"), status=run_job.JOB_RUNNING, pid=4242)
    previous = job_queue_service.set_run_lister(lambda: ["4242 python main.py run --video a", "999 python main.py run"])
    try:
        foreign = job_queue_service._list_foreign_runs([running])
    finally:
        job_queue_service.set_run_lister(previous)
    harness.check("the queue's own running job is no foreign run, a typed one is", foreign == ["999 python main.py run"],
                  str(foreign))


def test_the_dispatcher_starts_one_waiter_for_each_lane_with_a_job():
    queue_dir, pipes = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    _make_pipes(pipes, ["one", "two"])
    previous = _use_stand_in(pipes)
    try:
        job_queue_service.queue_jobs(queue_dir, [_request("one", "second-host"), _request("two", "Mac"), _request("three", "second-host")])
        job_queue_service._start_lane_waiters(queue_dir)
        job_queue_service._start_lane_waiters(queue_dir)
        _wait_started(pipes, "one")
        _wait_started(pipes, "two")
        lanes = sorted(job_queue_service._running_lanes)
        running = sorted(job.request.video.stem for job in job_queue_service.list_jobs(queue_dir)
                         if job.status == run_job.JOB_RUNNING)
        _release(pipes, "one")
        _release(pipes, "two")
        for thread in [thread for thread in threading.enumerate() if thread.name.startswith("i2k-job-queue-")]:
            thread.join()
    finally:
        _give_back(previous)
    harness.check("a second look starts no second waiter in a lane", lanes == ["Mac", "second-host"], str(lanes))
    harness.check("the first job of each lane runs, the second host's second waits", running == ["one", "two"], str(running))
