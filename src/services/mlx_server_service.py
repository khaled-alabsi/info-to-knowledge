"""mlx-vlm as a model host the program starts itself: a run whose phases call a model on a
local OpenAI-compatible address (`openai-compatible:<model>`, `base_url` on this machine) where nothing answers yet
starts `mlx_vlm.server` for it from the environment `i2k setup-mlx` built, waits until the server lists its models,
and stops it when the run ends. A server already answering is left alone, so one started by hand still works.

A measurement of mlx-vlm on this Mac showed: every model family the program uses runs on it, the filter model
14% faster than LM Studio but with other answers (13 frames removed against 43), the 35B model 4% slower. LM Studio
stays the default; a model entry naming the address chooses mlx-vlm.
"""

from __future__ import annotations

import atexit
import logging
import os
import selectors
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from services import mlx_setup_service

__all__ = ["LOCAL_HOSTS", "list_servers_to_start", "start_server", "stop_server", "start_needed_servers"]

_LOG = logging.getLogger(__name__)

# The addresses a server the program may start answers on: this machine only
LOCAL_HOSTS = ("127.0.0.1", "localhost")
# The provider whose models mlx-vlm serves
_PROVIDER = "openai-compatible"
# How long a starting server may take to print its ready line: loading a 35B model from disk took about 90 s
_READY_LIMIT_S = 300.0
# The line uvicorn prints once the server takes requests
_READY_LINE = b"Uvicorn running on"
# How long a server asked to stop is given before it is killed
_STOP_LIMIT_S = 20.0


def _is_answering(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/models", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def list_servers_to_start(rows: list[dict],
                          resolve_url) -> list[tuple[str, str]]:
    """`(base_url, model)` for each distinct model the phases call on a local OpenAI-compatible address where nothing
    answers; `rows` are `chat_model_service.describe_phase_models()`'s, `resolve_url(name)` a model's address."""

    wanted: dict[str, str] = {}
    for row in rows:
        if row.get("provider") != _PROVIDER:
            continue
        base_url = str(resolve_url(row["name"]) or "")
        if urlparse(base_url).hostname not in LOCAL_HOSTS:
            continue
        wanted.setdefault(base_url, row["model"])

    return [(base_url, model) for base_url, model in wanted.items() if not _is_answering(base_url)]


def start_server(base_url: str,
                 model: str,
                 log_path: Path,
                 python: Path | None = None) -> subprocess.Popen:
    """`mlx_vlm.server` serving `model` on `base_url`'s port, started from mlx-vlm's environment, its output in
    `log_path`; returned once it lists its models. A missing environment, a server that ends, or one that does not
    answer within the limit is refused by name."""

    python = python or mlx_setup_service.DEFAULT_VENV / "bin" / "python"
    if not python.is_file():
        raise FileNotFoundError(f"a model is served on {base_url} and mlx-vlm is not installed ({python}): run "
                                f"./i2k setup-mlx {model}")
    port = urlparse(base_url).port or mlx_setup_service.MLX_VLM_SERVER_PORT
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([str(python), "-m", "mlx_vlm.server", "--model", model, "--host", "127.0.0.1",
                                "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True, env=dict(os.environ))
    # STEP 1: the server's own ready line is the signal, read as it is printed, under a hard limit
    log = log_path.open("ab")
    deadline = time.monotonic() + _READY_LIMIT_S
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(timeout=remaining):
                stop_server(process)
                log.close()
                raise RuntimeError(f"mlx_vlm.server for {model} did not start on {base_url} within "
                                   f"{_READY_LIMIT_S:.0f} s: see {log_path}")
            line = process.stdout.readline()
            if not line:
                log.close()
                raise RuntimeError(f"mlx_vlm.server for {model} ended with exit code {process.wait()}: see {log_path}")
            log.write(line)
            # written as it comes, so the log shows a server still loading
            log.flush()
            if _READY_LINE in line:
                break
    # STEP 2: the rest of its output kept in the log while it serves
    threading.Thread(target=_copy_output, args=(process, log), daemon=True).start()

    return process


def _copy_output(process: subprocess.Popen,
                 log) -> None:
    for line in process.stdout:
        log.write(line)
        log.flush()
    log.close()


def stop_server(process: subprocess.Popen) -> None:
    """The server asked to stop, and killed when it does not within the limit."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_STOP_LIMIT_S)
    except subprocess.TimeoutExpired:
        process.kill()


def start_needed_servers(rows: list[dict],
                         resolve_url,
                         log_dir: Path) -> list[str]:
    """Every server `list_servers_to_start` names, started and stopped when this process ends; one line per server
    for the run's header."""

    lines = []
    for base_url, model in list_servers_to_start(rows, resolve_url):
        started = time.monotonic()
        process = start_server(base_url, model, log_dir / "mlx-server.log")
        atexit.register(stop_server, process)
        lines.append(f"mlx-vlm: started for {model} on {base_url} in {time.monotonic() - started:.0f} s, stopped "
                     f"when the run ends")
        _LOG.info(lines[-1])

    return lines
