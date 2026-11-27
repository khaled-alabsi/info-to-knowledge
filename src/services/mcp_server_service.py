"""`i2k mcp`: the program's controls as MCP tools, for an agent.

Each tool calls the running `i2k serve` over its HTTP API, the same endpoint the page's button calls, so an agent, the
page and the CLI share one queue and one session per run folder: a run queued here shows on the page, and one stopped
on the page is resumed here. No tool returns what a meeting said (notes, transcript, document, logs, the run's topic):
whether an agent may read a run's content is the owner's decision, recorded in the task.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import config
from models.web_api_request import QueueRunRequest
from services import server_client_service

__all__ = ["build_mcp_server", "add_mcp_arguments", "run_from_command_line"]

# What a run's status may carry to an agent: phases, counts and states, never words of the meeting
_STATUS_KEYS = ("root", "name", "status", "run_id", "started_at", "is_live", "has_document", "phases", "frames",
                "timing", "has_recording", "is_recording_kept", "can_keep_recording", "recording_compression",
                "document_stages", "blocked_items", "live_pids", "signal", "seconds_per_frame")
_RUN_LIST_KEYS = ("root", "name", "status", "run_id", "started_at", "is_live", "has_document", "video")
_DESTRUCTIVE = ToolAnnotations(destructive_hint=True)
_READ_ONLY = ToolAnnotations(read_only_hint=True)


def _render(payload: Any) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False)


def _build_run_path(root: str,
                    name: str) -> str:
    return f"/runs/{quote(root, safe='')}/{quote(name, safe='')}"


def list_phases() -> str:
    """The six phases by number and name, each with whether its switch is on."""

    return _render([{key: phase.get(key) for key in ("number", "name", "is_on")} for phase in server_client_service.call_server("GET", "/phases")])


def list_inbox() -> str:
    """The recordings waiting in the inbox, each with its run folder and whether it has a document."""

    return _render(server_client_service.call_server("GET", "/inbox"))


def list_runs() -> str:
    """Every run folder, newest first: its root and name (the pair every run tool takes), status and whether live."""

    return _render([{key: run.get(key) for key in _RUN_LIST_KEYS} for run in server_client_service.call_server("GET", "/runs")])


def get_run_status(root: str,
                   name: str) -> str:
    """One run folder's phases, frames by status, timing, blocks, recording and live process: no meeting content."""

    detail = server_client_service.call_server("GET", _build_run_path(root, name))
    status = {key: detail.get(key) for key in _STATUS_KEYS}
    status["critique_rules"] = (detail.get("critique") or {}).get("rules", 0)
    status["worker_reports"] = (detail.get("worker_feedback") or {}).get("reports", 0)

    return _render(status)


def list_jobs() -> str:
    """The queue, oldest first: each job's id, status, run line and exit code."""

    return _render(server_client_service.call_server("GET", "/jobs"))


def add_recording(path: str) -> str:
    """Put a video file from this machine into the inbox, as the page's upload does; a recording already processed is
    refused with the run that processed it."""

    file = Path(path).expanduser()
    if not file.is_file():
        raise RuntimeError(f"{file} is not a file on this machine (a meeting in parts is added from the page)")
    with file.open("rb") as stream:
        return _render(server_client_service.call_server("POST", "/inbox/upload", files=[("files", (file.name, stream, "video/mp4"))]))


def queue_run(request: QueueRunRequest) -> str:
    """Queue one run of `request.video` into the run folder `request.output` (both as `list_inbox` names them), as the
    page's Run form does: every phase, from a phase, or one phase, optionally on a settings profile, and optionally in
    an ingesting profile chosen by hand (`takeaway`, `meeting`, ...), which no tag and no model call then changes. With
    `is_run_as_audio`, a video is processed as an audio recording: its sound alone, no frame taken. `workflow` picks
    the video workflow from phase 3 on: `quality`, `direct-draft` or `fast-processing`."""

    fields = request.model_dump()
    body = {"items": [{"video": fields.pop("video"), "output": fields.pop("output")}], "worker_indexes": [], **fields}

    return _render(server_client_service.call_server("POST", "/jobs", json=body))


def resume_run(root: str,
               name: str,
               profile: str = "") -> str:
    """Queue a resume of a run folder: a blocked step is asked again, then the run goes on, as Unblock or Resume."""

    detail = server_client_service.call_server("GET", _build_run_path(root, name))
    # A run whose record names no recording cannot be resumed: queued, it ran `--video .` and was refused with no reason
    if not detail.get("video"):
        raise RuntimeError(f"{name} cannot be resumed: its run record names no recording")

    return queue_run(QueueRunRequest(video=detail["video"], output=detail["folder"], is_resuming=True, profile=profile))


def send_run_signal(root: str,
                    name: str,
                    signal: Literal["pause", "drain", "stop", "continue"]) -> str:
    """Pause a running run at its next check, drain it (finish the current item, then stop), stop it, or continue a
    paused one."""

    return _render(server_client_service.call_server("POST", f"{_build_run_path(root, name)}/control", json={"signal": signal}))


def cancel_job(job_id: str) -> str:
    """Take a queued job off the queue."""

    return _render(server_client_service.call_server("POST", f"/jobs/{quote(job_id, safe='')}/cancel"))


def stop_job_now(job_id: str) -> str:
    """Interrupt a running job now, as Ctrl-C would, without waiting for a check."""

    return _render(server_client_service.call_server("POST", f"/jobs/{quote(job_id, safe='')}/interrupt"))


def reset_run(root: str,
              name: str,
              kind: Literal["phase", "chapter"],
              number: int,
              is_confirmed: bool = False) -> str:
    """Take a run back: `phase` N keeps phases up to N and removes the rest; `chapter` N removes chapter N and later
    ones from phase 4. Without `is_confirmed` it only lists what it would remove."""

    return _render(server_client_service.call_server("POST", f"{_build_run_path(root, name)}/reset",
                         json={"kind": kind, "number": number, "is_confirmed": is_confirmed}))


def move_recording(root: str,
                   name: str,
                   to: Literal["inbox", "run"]) -> str:
    """Move a done run's recording back to the inbox (so phases 1 and 2 can run again), or keep it in its run folder."""

    return _render(server_client_service.call_server("POST", f"{_build_run_path(root, name)}/recording/to-{to}"))


def shrink_recording(root: str,
                     name: str) -> str:
    """Re-encode the recording a done run keeps in its folder to 720p, in place; it runs on in its own process, and
    `get_run_status` shows its progress under `recording_compression`."""

    return _render(server_client_service.call_server("POST", f"{_build_run_path(root, name)}/recording/compress"))


def delete_recording(root: str,
                     name: str) -> str:
    """Move a run's recording into the inbox trash, where it can be restored."""

    return _render(server_client_service.call_server("POST", f"{_build_run_path(root, name)}/recording/delete"))


def list_models() -> str:
    """Which model each phase calls, as `i2k models` prints it."""

    return server_client_service.call_server("GET", "/models")["output"]


def probe_hosts() -> str:
    """Whether each configured model host answers, and what it has loaded."""

    return server_client_service.call_server("GET", "/probe")["output"]


def list_profiles() -> str:
    """The settings profiles a run may name, and the server's own."""

    return _render(server_client_service.call_server("GET", "/profiles"))


def list_settings(profile: str = "") -> str:
    """Every setting with its value, the file that set it and its comment; on the default settings, or a profile's."""

    return _render(server_client_service.call_server("GET", "/settings", params={"profile": profile} if profile else None))


def save_setting(key: str,
                 new_value: Any,
                 profile: str = "") -> str:
    """Set one setting (`budgets.maxAttempts`) in both default settings files, or in a profile's file; refused with the reason
    when the settings would not load with it. The next run reads it."""

    return _render(server_client_service.call_server("PUT", "/settings", json={"key": key, "new_value": new_value, "profile": profile}))


_TOOLS = ((list_phases, _READ_ONLY), (list_inbox, _READ_ONLY), (list_runs, _READ_ONLY), (get_run_status, _READ_ONLY),
          (list_jobs, _READ_ONLY), (add_recording, None), (queue_run, None), (resume_run, None), (send_run_signal, None),
          (cancel_job, None), (stop_job_now, _DESTRUCTIVE), (reset_run, _DESTRUCTIVE), (move_recording, None),
          (shrink_recording, _DESTRUCTIVE), (delete_recording, _DESTRUCTIVE), (list_models, _READ_ONLY),
          (probe_hosts, _READ_ONLY), (list_profiles, _READ_ONLY), (list_settings, _READ_ONLY), (save_setting, None))


def add_mcp_arguments(parser: argparse.ArgumentParser) -> None:
    """`--server`, the one option of `i2k mcp`, on the CLI's parser and on `run_from_command_line`'s."""

    parser.add_argument("--server", dest="server_url", default=None,
                        help=f"the `i2k serve` address (default: ${server_client_service.SERVER_URL_ENV} or "
                             f"{server_client_service.DEFAULT_SERVER_URL})")


def run_from_command_line(argv: list[str]) -> int:
    """`i2k mcp`'s own start, reached from `main.py` before the CLI's other commands are imported: through them it
    loaded the whole pipeline, LangChain and transformers with it, and answered its first message only after about 30 s
    on a loaded machine, past the wait of an MCP client (DEFECTS 475). Takes `-v`, `--profile` and `--server`; every log
    line goes to standard error, standard output carrying the MCP messages. 0 when the agent closes the connection."""

    parser = argparse.ArgumentParser(prog="i2k mcp")
    parser.add_argument("-v", "--verbose", dest="is_verbose", action="store_true")
    parser.add_argument("--profile", dest="profile_name", default=os.environ.get(config.PROFILE_ENV_VAR, ""))
    add_mcp_arguments(parser)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.is_verbose else logging.INFO, format="%(message)s", stream=sys.stderr)
    logger = logging.getLogger(config.PLAYBOOK_NAME)
    if args.profile_name:
        try:
            sections = config.load_profile(args.profile_name)
        except (FileNotFoundError, ValueError) as exc:
            logger.error(str(exc))

            return 2
        os.environ[config.PROFILE_ENV_VAR] = args.profile_name
        logger.info("profile %s: %s", args.profile_name, ", ".join(sections))
    if args.server_url:
        server_client_service.set_server_url(args.server_url)
    build_mcp_server().run("stdio")

    return 0


def build_mcp_server() -> MCPServer:
    """The MCP server with every tool registered."""

    server = MCPServer(name="info-to-knowledge", instructions=(
        "Controls info-to-knowledge through its running `i2k serve`: the inbox, the queue, each run folder's phases, "
        "resets, recordings and settings. A run folder is named by `root` and `name`, as `list_runs` gives them. "
        "No tool returns what a meeting said."))
    for function, annotations in _TOOLS:
        server.tool(annotations=annotations)(function)

    return server
