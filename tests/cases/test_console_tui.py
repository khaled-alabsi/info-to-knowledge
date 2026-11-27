"""The terminal UI driven the way a person drives it: typed slash commands and a click, against the real API.

The API app runs in this process (`ui_api_client.set_transport`), over a temporary inbox, run root
and queue; its worker is not started, so a queued job stays queued and nothing runs.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx

from support import harness

import config
from services import job_queue_service
from services import run_status_service
from models import slash_command_vocabulary
from models.ui_errors import ServerAnswerError
from services import ui_api_client
from services import web_api_service
from tui import console_tui_runner


async def _read_messages(app: console_tui_runner.ConsoleApp,
                         pilot) -> str:
    """The Messages tab's text. A log on a hidden tab draws nothing until it is shown, so the tab is shown first."""

    app.query_one("#outputs").active = "tab-messages"
    # A log draws what was written while it was hidden once it has a size: under load that took more than one turn of
    # the event loop, and a test read an empty log. Each pause yields to the loop, at most 50 times.
    messages = app.query_one("#messages")
    for _ in range(50):
        await pilot.pause()
        if messages.lines:
            break

    return "\n".join(line.text for line in messages.lines)


async def _drive(app: console_tui_runner.ConsoleApp) -> dict:
    seen = {}
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        seen["inbox"] = len(app.inbox_items)
        command = app.query_one("#command")
        for line in ("/bogus", "/process 1 --from 3 --dry-run", "/resume"):
            command.value = line
            await pilot.press("enter")
            await pilot.pause()
        seen["messages_typed"] = await _read_messages(app, pilot)
        await pilot.click("#process-all")
        await pilot.pause()
        seen["messages_clicked"] = await _read_messages(app, pilot)
        for line in ("/archive 1", "/unarchive AcmeSync.mp4"):
            command.focus()
            command.value = line
            await pilot.press("enter")
            await pilot.pause()
            seen[line] = (await _read_messages(app, pilot), len(app.inbox_items))

    return seen


def test_typed_and_clicked_commands_reach_the_queue():
    root = Path(tempfile.mkdtemp())
    (root / "inbox").mkdir()
    (root / "inbox" / "AcmeSync.mp4").write_bytes(b"video")
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"knowledge": root / "knowledge"})
    previous_inbox, previous_documents = config.INBOX_DIR, config.DOCUMENT_ROOT
    previous_queue = job_queue_service.QUEUE_DIR
    config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = root / "inbox", root / "knowledge", root / "ui"
    previous_transport = ui_api_client.set_transport(httpx.ASGITransport(app=web_api_service.build_app()))
    try:
        seen = asyncio.run(_drive(console_tui_runner.ConsoleApp("http://ui.test")))
        jobs = job_queue_service.list_jobs(root / "ui")
    finally:
        ui_api_client.set_transport(previous_transport)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
        config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = (previous_inbox, previous_documents,
                                                                               previous_queue)

    harness.check("the inbox tab lists the dropped video", seen["inbox"] == 1, str(seen))
    harness.check("an unknown command is answered with what to type", "/bogus is no command; /help lists them"
                  in seen["messages_typed"], seen["messages_typed"])
    harness.check("/resume with no run open says how to open one", "no run is open" in seen["messages_typed"],
                  seen["messages_typed"])
    harness.check("the typed /process queued one job with its phase and dry run",
                  len(jobs) >= 1 and jobs[0].request.from_phase == 3 and jobs[0].request.is_dry_run
                  and jobs[0].request.video == root / "inbox" / "AcmeSync.mp4"
                  and jobs[0].request.output_root == root / "knowledge" / "AcmeSync", str(jobs[:1]))
    harness.check("the click on 'Process all new' queued the same video again, with no phase selection",
                  len(jobs) == 2 and jobs[1].request.from_phase is None and not jobs[1].request.is_dry_run,
                  str(jobs))
    harness.check("each queueing is reported", seen["messages_clicked"].count("queued 1 job(s)") == 2,
                  seen["messages_clicked"])
    harness.check("/archive 1 moves the video into inbox/.archive and the inbox tab no longer lists it",
                  "moved to inbox/.archive as AcmeSync.mp4" in seen["/archive 1"][0] and seen["/archive 1"][1] == 0,
                  str(seen["/archive 1"]))
    harness.check("/unarchive brings it back into the inbox tab",
                  "moved AcmeSync.mp4 back into the inbox" in seen["/unarchive AcmeSync.mp4"][0]
                  and seen["/unarchive AcmeSync.mp4"][1] == 1, str(seen["/unarchive AcmeSync.mp4"]))


def test_an_error_body_that_is_no_object_is_still_the_servers_refusal():
    previous = ui_api_client.set_transport(httpx.MockTransport(lambda request: httpx.Response(502, json=["bad gateway"])))
    try:
        try:
            asyncio.run(ui_api_client.fetch_json("http://test", "/jobs"))
            answer = "no error"
        except Exception as exc:
            answer = f"{type(exc).__name__}: {exc}"
    finally:
        ui_api_client.set_transport(previous)
    harness.check("raised as the server's answer, its body as the reason",
                  answer.startswith("ServerAnswerError: 502") and "bad gateway" in answer, answer)


class _UiPlace:
    """An inbox, two run roots and a queue in a test's own folder, the terminal UI reaching the API in process."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        root = self.root
        (root / "inbox").mkdir(exist_ok=True)
        self.saved = (dict(run_status_service.RUN_ROOTS), config.INBOX_DIR, config.DOCUMENT_ROOT,
                      job_queue_service.QUEUE_DIR)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update({"knowledge": root / "knowledge", "output": root / "output"})
        config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = root / "inbox", root / "knowledge", root / "ui"
        self.transport = ui_api_client.set_transport(httpx.ASGITransport(app=web_api_service.build_app()))
        # The app's own refreshes never run inside a test: a slow machine fired the 1-second one mid-test, and the
        # lines a test counts came out twice
        self.refreshes = (console_tui_runner._REFRESH_RUN_S, console_tui_runner._REFRESH_LISTS_S)
        console_tui_runner._REFRESH_RUN_S = console_tui_runner._REFRESH_LISTS_S = 3600.0

        return self

    def __exit__(self, *exc):
        roots, inbox, documents, queue = self.saved
        console_tui_runner._REFRESH_RUN_S, console_tui_runner._REFRESH_LISTS_S = self.refreshes
        ui_api_client.set_transport(self.transport)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(roots)
        config.INBOX_DIR, config.DOCUMENT_ROOT, job_queue_service.QUEUE_DIR = inbox, documents, queue


def test_a_failed_reload_keeps_the_lists_the_screen_shows():
    root = Path(tempfile.mkdtemp())
    (root / "inbox").mkdir()
    (root / "inbox" / "AcmeSync.mp4").write_bytes(b"video")

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            (root / "inbox" / "Jonas-weekly.mp4").write_bytes(b"other video")
            original = ui_api_client.fetch_json

            async def fail_on_runs(base_url, path, params=None):
                if path == "/runs":
                    raise ServerAnswerError("the server went away")
                return await original(base_url, path, params)

            ui_api_client.fetch_json = fail_on_runs
            try:
                await app.reload_lists()
            finally:
                ui_api_client.fetch_json = original

            return len(app.inbox_items), app.query_one("#inbox").option_count

    with _UiPlace(root):
        stored, shown = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("the stored inbox is the one on screen", stored == shown == 1, f"stored {stored}, shown {shown}")


def test_a_ticked_video_stays_ticked_when_a_new_one_lands_before_it():
    root = Path(tempfile.mkdtemp())
    (root / "inbox").mkdir()
    (root / "inbox" / "AcmeSync.mp4").write_bytes(b"video")

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            app.query_one("#inbox").select(1)
            (root / "inbox" / "Jonas-weekly.mp4").write_bytes(b"other video")
            await app.reload_lists()
            await pilot.pause()

            return [app.inbox_items[number - 1]["name"] for number in app.query_one("#inbox").selected]

    with _UiPlace(root):
        ticked = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("the same video is ticked, whatever its new place", ticked == ["AcmeSync.mp4"], str(ticked))


def test_open_names_both_folders_when_two_roots_hold_the_name():
    root = Path(tempfile.mkdtemp())
    for place in ("knowledge", "output"):
        (root / place / "AcmeSync").mkdir(parents=True)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            command.value = "/open AcmeSync"
            await pilot.press("enter")
            await pilot.pause()

            return await _read_messages(app, pilot), app.open_run

    with _UiPlace(root):
        said, opened = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("nothing opened, and both folders are named", opened is None and "knowledge/AcmeSync" in said
                  and "output/AcmeSync" in said, said[-400:])


def test_every_slash_command_has_the_method_that_carries_it_out():
    
    missing = [name for name in slash_command_vocabulary.SLASH_COMMANDS
               if not hasattr(console_tui_runner.ConsoleApp, f"_run_{name.replace('-', '_')}")]
    harness.check("none is missing", missing == [], str(missing))


def test_a_run_folder_whose_name_holds_a_hash_opens():
    root = Path(tempfile.mkdtemp())
    (root / "knowledge" / "Weekly #3").mkdir(parents=True)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            command.value = "/open knowledge/Weekly #3"
            await pilot.press("enter")
            await pilot.pause()

            return await _read_messages(app, pilot), app.open_run

    with _UiPlace(root):
        said, opened = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("opened, its state read from the server", opened is not None and "phases" in opened
                  and "server:" not in said, said[-300:])


def test_a_server_that_stays_down_is_said_once():
    root = Path(tempfile.mkdtemp())

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            original = ui_api_client.fetch_json

            async def fail(base_url, path, params=None):
                raise ServerAnswerError("connection refused")

            ui_api_client.fetch_json = fail
            try:
                for _ in range(3):
                    await app.reload_lists()
            finally:
                ui_api_client.fetch_json = original

            return await _read_messages(app, pilot)

    with _UiPlace(root):
        said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("one line for three failed reloads", said.count("connection refused") == 1, said[-400:])


def test_reset_to_lists_what_it_would_remove_for_the_open_run():
    root = Path(tempfile.mkdtemp())
    (root / "knowledge" / "AcmeSync").mkdir(parents=True)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            for line in ("/open knowledge/AcmeSync", "/reset-to 3", "/reset-to three"):
                command.focus()
                command.value = line
                await pilot.press("enter")
                await pilot.pause()

            return await _read_messages(app, pilot)

    with _UiPlace(root):
        said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("a listing, then a refusal of a word that is no number", "nothing removed; /reset-to 3 --yes" in said
                  and "/reset-to needs a number: /reset-to 3" in said, said[-500:])


def test_interrupt_acts_on_the_open_runs_job_only():
    import json
    import subprocess as process_module
    import sys

    from models.run_job import RunJob, RunJobRequest

    root = Path(tempfile.mkdtemp())
    (root / "knowledge" / "AcmeSync").mkdir(parents=True)
    # A process that lives until its input closes stands for another video's run
    other = process_module.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=process_module.PIPE)
    job = RunJob(job_id="otherjob", request=RunJobRequest(video=root / "inbox" / "Jonas.mp4",
                                                          output_root=root / "knowledge" / "Jonas"),
                 queued_at="2026-01-01 00:00:00", command=[], status="running", pid=other.pid)
    (root / "ui").mkdir(parents=True)
    (root / "ui" / job_queue_service.JOBS_FILE).write_text(json.dumps([job.as_dict()]), encoding="utf-8")

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            for line in ("/open knowledge/AcmeSync", "/interrupt"):
                command.focus()
                command.value = line
                await pilot.press("enter")
                await pilot.pause()

            return await _read_messages(app, pilot)

    try:
        with _UiPlace(root):
            said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
        alive = other.poll() is None
    finally:
        other.kill()
        other.wait()
    harness.check("another video's job is left running, and why is said", alive and "interrupted job" not in said
                  and "no running job" in said, said[-400:])


def test_a_refused_reset_never_says_yes_would_remove_it():
    root = Path(tempfile.mkdtemp())
    (root / "knowledge" / "AcmeSync").mkdir(parents=True)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            for line in ("/open knowledge/AcmeSync", "/reset-to 9"):
                command.focus()
                command.value = line
                await pilot.press("enter")
                await pilot.pause()

            return await _read_messages(app, pilot)

    with _UiPlace(root):
        said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("the refusal, and no promise of --yes", "there is no phase 9" in said and "--yes removes it" not in said,
                  said[-400:])


def test_an_open_run_whose_folder_is_gone_is_said_once():
    import shutil

    root = Path(tempfile.mkdtemp())
    (root / "knowledge" / "AcmeSync").mkdir(parents=True)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            command = app.query_one("#command")
            command.value = "/open knowledge/AcmeSync"
            await pilot.press("enter")
            await pilot.pause()
            shutil.rmtree(root / "knowledge" / "AcmeSync")
            for _ in range(3):
                await app.reload_open_run()
                await app.reload_lists()

            return await _read_messages(app, pilot)

    with _UiPlace(root):
        said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("one line about the folder, and no line that the server answers again",
                  said.count("no run folder") == 1 and "answering again" not in said, said[-500:])


def test_a_stopped_server_is_said_and_the_ui_stays_open():
    class RefusingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused", request=request)

    async def drive(app):
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            previous = ui_api_client.set_transport(RefusingTransport())
            try:
                await app.reload_lists()
            except Exception as exc:
                return f"raised {type(exc).__name__}"
            finally:
                ui_api_client.set_transport(previous)

            return await _read_messages(app, pilot)

    with _UiPlace(Path(tempfile.mkdtemp())):
        said = asyncio.run(drive(console_tui_runner.ConsoleApp("http://ui.test")))
    harness.check("the failure is a line of Messages, never an error that ends the app", "connection refused" in said
                  and not said.startswith("raised"), said[-300:])
