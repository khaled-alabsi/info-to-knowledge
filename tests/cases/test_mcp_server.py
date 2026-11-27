"""`i2k mcp`: every tool goes through the server's own API, so an agent, the page and the CLI share
one queue; no tool hands over what a meeting said."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from unittest import mock

from support import harness

import config
from cli import mcp_command
from cli import run_command
from models.web_api_request import QueueRunRequest
from services import job_queue_service
from services import mcp_server_service
from services import server_client_service
from services import web_api_service

from cases import test_recording_in_run_folder as recording_tests
from cases import test_ui_services as ui_tests


def _run_against_server(check) -> None:
    """The tools pointed at the server app itself, on a run root and a queue of their own."""

    root = Path(tempfile.mkdtemp())
    ui_tests._make_run_folder(root / "knowledge", "AcmeSync-demo")
    previous_roots = ui_tests._swap_run_roots({"knowledge": root / "knowledge"})
    previous_queue, previous_inbox = job_queue_service.QUEUE_DIR, config.INBOX_DIR
    job_queue_service.QUEUE_DIR, config.INBOX_DIR = root / "ui", root / "inbox"
    server_client_service.set_http_client(TestClient(web_api_service.build_app()))
    try:
        check(root)
    finally:
        server_client_service.set_http_client(None)
        ui_tests._swap_run_roots(previous_roots)
        job_queue_service.QUEUE_DIR, config.INBOX_DIR = previous_queue, previous_inbox


def test_the_server_lists_every_tool_with_its_annotations():
    tools = asyncio.run(mcp_server_service.build_mcp_server().list_tools())
    names = {tool.name: tool for tool in tools}
    harness.check("every control the page has is a tool",
                  {"list_runs", "get_run_status", "queue_run", "resume_run", "send_run_signal", "reset_run",
                   "add_recording", "shrink_recording", "save_setting", "list_settings"} <= set(names), str(sorted(names)))
    harness.check("a reset is marked destructive, a status read-only",
                  names["reset_run"].annotations.destructive_hint and names["get_run_status"].annotations.read_only_hint, "")


def test_a_run_s_status_carries_states_and_counts_never_the_meeting_s_words():
    def check(root: Path) -> None:
        status = json.loads(mcp_server_service.get_run_status("knowledge", "AcmeSync-demo"))
        runs = json.loads(mcp_server_service.list_runs())
        harness.check("the status names the run and its phases",
                      status["name"] == "AcmeSync-demo" and len(status["phases"]) == 6, str(status)[:300])
        harness.check("no topic, log, batch titles or worker words",
                      not {"topic", "log", "batches", "critique", "worker_feedback"} & set(status)
                      and all("topic" not in run for run in runs), str(sorted(status)))

    _run_against_server(check)


def test_a_run_queued_by_an_agent_is_in_the_page_s_queue_and_a_refusal_comes_back_as_the_tool_s_error():
    def check(root: Path) -> None:
        queued = json.loads(mcp_server_service.queue_run(QueueRunRequest(
            video="/work/inbox/AcmeSync.mp4", output=str(root / "knowledge" / "AcmeSync-demo"), from_phase=3,
            is_dry_run=True, profile="strict")))
        jobs = json.loads(mcp_server_service.list_jobs())
        harness.check("the job is in the one queue, with its profile on its run line",
                      jobs[0]["job_id"] == queued[0]["job_id"] and "--profile" in jobs[0]["command"], str(jobs[0])[:300])
        try:
            mcp_server_service.reset_run("knowledge", "Gateway-missing", "phase", 2)
            error = ""
        except RuntimeError as exc:
            error = str(exc)
        harness.check("an unknown run folder is the server's 404, said by the tool", error.startswith("404"), error)
        listing = json.loads(mcp_server_service.reset_run("knowledge", "AcmeSync-demo", "phase", 2))
        harness.check("a reset without confirming only lists", listing["exit_code"] == 0, str(listing))

    _run_against_server(check)


def test_with_no_server_the_tool_says_to_start_it():
    server_client_service.set_http_client(None)
    previous = os.environ.get(server_client_service.SERVER_URL_ENV)
    os.environ[server_client_service.SERVER_URL_ENV] = "http://127.0.0.1:9"
    try:
        mcp_server_service.list_runs()
        error = ""
    except RuntimeError as exc:
        error = str(exc)
    finally:
        server_client_service.set_http_client(None)
        if previous is None:
            os.environ.pop(server_client_service.SERVER_URL_ENV, None)
        else:
            os.environ[server_client_service.SERVER_URL_ENV] = previous
    harness.check("the agent is told to start `i2k serve`", "start it first" in error, error)


def _list_mcp_answers(environment: dict[str, str]) -> list[dict]:
    """Every line `i2k mcp` writes to standard output for an initialize and a tools list, read as JSON."""

    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    process = subprocess.Popen([sys.executable, str(config.PLAYBOOK_ROOT / "main.py"), "mcp"], cwd=config.PLAYBOOK_ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                               env=environment)
    answers: list[dict] = []
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        # Each answer is one line; the tools' answer (id 2) is the signal that it is done, read within 40 lines
        for _ in range(40):
            is_ready, _, _ = select.select([process.stdout], [], [], 30)
            line = process.stdout.readline() if is_ready else ""
            if not line:
                break
            try:
                answers.append(json.loads(line))
            except ValueError:
                answers.append({"not an MCP message": line})
            if answers[-1].get("id") == 2:
                break
    finally:
        process.kill()
        process.wait()

    return answers


def test_i2k_mcp_answers_an_agent_over_stdio_with_nothing_else_on_its_output():
    """The real command, spoken to as an agent speaks: initialize, then list the tools. A log line on standard output
    would break the connection."""

    answers = _list_mcp_answers({key: value for key, value in os.environ.items() if key != config.PROFILE_ENV_VAR})
    tools = next((answer["result"]["tools"] for answer in answers if answer.get("id") == 2), [])
    harness.check("every line on standard output is an MCP message, and the tools are listed",
                  len(answers) == 2 and any(tool["name"] == "queue_run" for tool in tools), str(answers)[-500:])


def test_the_mcp_command_takes_the_server_address():
    parser = argparse.ArgumentParser()
    mcp_command.add_mcp_arguments(parser)
    harness.check("--server names the `i2k serve` the tools call",
                  parser.parse_args(["--server", "http://127.0.0.1:9000"]).server_url == "http://127.0.0.1:9000"
                  and parser.parse_args([]).server_url is None)


def test_run_queue_hands_the_run_to_the_server_s_queue_and_says_its_job():
    def check(root: Path) -> None:
        logger, handler = recording_tests._build_logger()
        video = root / "inbox" / "AcmeSync.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"<video bytes>")
        arguments = dict(target_file=video, output_root=root / "knowledge" / "AcmeSync-demo",
                         only_phase=None, from_phase=3, to_phase=None, worker_indexes=[], is_resuming=True,
                         is_dry_run=False, is_forced=False, is_queued=True, profile_name="strict", tags=["chat"],
                         ingesting_profile="meeting", is_run_as_audio=True, phase_models=["frames_processing=frame"], workflow="", logger=logger)
        code = run_command.cmd_run(argparse.Namespace(**arguments))
        jobs = json.loads(mcp_server_service.list_jobs())
        harness.check("queued: exit 0, the job in the page's queue with every flag",
                      code == 0 and jobs and {"--from", "--resume", "--profile", "strict", "--tag", "chat", "--ingesting-profile", "meeting",
                                                "--as-audio"} <= set(jobs[-1]["command"]),
                      str(jobs[-1]["command"] if jobs else jobs))
        harness.check("the console names the job", any("queued as job" in line for line in handler.lines), str(handler.lines))
        forced = run_command.cmd_run(argparse.Namespace(**{**arguments, "is_forced": True}))
        harness.check("--force cannot be queued, and says so", forced == 2
                      and any("--force cannot be queued" in line for line in handler.lines), str(handler.lines))
        queued_before = len(json.loads(mcp_server_service.list_jobs()))
        contradicted = run_command.cmd_run(argparse.Namespace(**{**arguments, "only_phase": 3, "from_phase": 4}))
        missing = run_command.cmd_run(argparse.Namespace(**{**arguments, "target_file": root / "inbox" / "Gateway.mp4"}))
        harness.check("a selection `run` refuses, or a recording that is not there, is refused before it is queued",
                      (contradicted, missing) == (2, 2) and len(json.loads(mcp_server_service.list_jobs())) == queued_before,
                      f"{contradicted} {missing} {handler.lines[-2:]}")

    _run_against_server(check)


def test_i2k_mcp_starts_without_the_pipeline_defect():
    # `i2k mcp` loaded every command, the pipeline, LangChain and transformers with them, and on a loaded machine answered
    # its first message only after about 30 s. It now starts from its own entry: none of them is imported
    imported = subprocess.run([sys.executable, "-X", "importtime", str(config.PLAYBOOK_ROOT / "main.py"), "mcp",
                               "--profile", "no-such-profile"], cwd=config.PLAYBOOK_ROOT, capture_output=True, text=True,
                              timeout=120)
    names = {line.split("|")[-1].strip() for line in imported.stderr.splitlines() if line.startswith("import time:")}
    harness.check("a profile with no file ends it by name", imported.returncode == 2
                  and "no-such-profile" in imported.stderr, imported.stderr[-300:])
    harness.check("neither the CLI's commands, LangChain nor transformers is imported",
                  not names & {"cli", "langchain_core", "transformers", "graph"}, str(sorted(names & {"cli", "langchain_core",
                                                                                                "transformers", "graph"})))


def test_i2k_mcp_under_a_profile_still_writes_only_mcp_messages():
    """The "profile ..." line is written before any handler runs, so it too must go to standard error."""

    answers = _list_mcp_answers({**os.environ, config.PROFILE_ENV_VAR: "strict"})
    harness.check("the first line is the answer to initialize", bool(answers) and answers[0].get("id") == 1,
                  str(answers)[:300])


def test_a_run_whose_record_names_no_recording_is_not_resumed():
    asked = []
    with mock.patch.object(server_client_service, "call_server",
                           lambda method, path, **kwargs: asked.append((method, path)) or {"video": "", "folder": "/out/a"}):
        try:
            answer = mcp_server_service.resume_run("/out", "a")
        except RuntimeError as exc:
            answer = str(exc)
    harness.check("it says why, and queues nothing", "names no recording" in answer and [method for method, _ in asked] == ["GET"],
                  f"{answer} {asked}")


def test_an_error_body_that_is_json_but_no_object_is_the_server_s_refusal():
    class ListBody:
        status_code = 409
        text = '["busy"]'

        def json(self):
            return ["busy"]

    class Client:
        base_url = "http://localhost:8765"

        def request(self, method, path, **kwargs):
            return ListBody()

    server_client_service.set_http_client(Client())
    try:
        try:
            server_client_service.call_server("GET", "/jobs")
            outcome = "returned"
        except RuntimeError as exc:
            outcome = str(exc)
        except AttributeError as exc:
            outcome = f"raised AttributeError {exc}"
    finally:
        server_client_service.set_http_client(None)
    harness.check("the refusal names its status and body", outcome == '409: ["busy"]', outcome)


def test_a_server_that_times_out_is_a_refusal_by_name_never_a_traceback():
    def time_out(request):
        raise httpx.ReadTimeout("timed out", request=request)

    server_client_service.set_http_client(httpx.Client(base_url="http://test", transport=httpx.MockTransport(time_out)))
    try:
        try:
            server_client_service.call_server("GET", "/jobs")
            answer = "no error"
        except Exception as exc:
            answer = f"{type(exc).__name__}: {exc}"
    finally:
        server_client_service.set_http_client(None)
    harness.check("a RuntimeError naming the server and the call", answer.startswith("RuntimeError")
                  and "http://test" in answer and "/api/jobs" in answer, answer)


def test_a_success_answer_that_is_no_json_is_a_refusal_by_name():
    page = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>another server</html>"))
    server_client_service.set_http_client(httpx.Client(base_url="http://test", transport=page))
    try:
        try:
            server_client_service.call_server("GET", "/jobs")
            answer = "no error"
        except Exception as exc:
            answer = f"{type(exc).__name__}: {exc}"
    finally:
        server_client_service.set_http_client(None)
    harness.check("a RuntimeError naming the server and what it answered", answer.startswith("RuntimeError")
                  and "http://test" in answer and "another server" in answer, answer)
