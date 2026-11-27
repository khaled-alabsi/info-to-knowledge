"""The record of every model call: what was sent, everything that came back, and what it cost.

Two files, for two readers. The CALL LOG of an item is one JSON file beside its record, rewritten by
each attempt and copied into that attempt's own directory by the capture node, so a validator can
read how the newest call ended and an operator can read how every earlier one did. The METRICS file
is one line per call for the whole run, appended as the call returns, so a run killed at item forty
still has thirty-nine rows and the closing line is a read of it rather than a sum kept in memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_client import path_sandbox
from models import output_contract
from models.run_paths import RunPaths
from utils import json_lines_utils
from utils import json_store_utils

__all__ = ["build_call_log_path", "write_call_log", "read_call_log", "append_call_metrics",
           "read_call_metrics", "render_metrics_line", "METRICS_FILE"]

METRICS_FILE = "calls.jsonl"


def _compute_total(rows: list[dict[str, Any]],
                   key: str) -> int | float:
    return sum(row[key] for row in rows if isinstance(row.get(key), (int, float)))


def build_call_log_path(paths: RunPaths,
                        name: str) -> Path:
    """Where the newest call for one item, or for one phase-wide job, is logged."""

    # Resolved through the jail, never joined: `name` can be a slug an operator typed after `--item`.
    return path_sandbox.resolve_within(paths.output_root, f"{output_contract.CALLS_DIR_NAME}/{name}.json")


def write_call_log(path: Path,
                   payload: dict[str, Any]) -> None:
    json_store_utils.write_json(path, payload)


def read_call_log(path: Path) -> dict[str, Any]:
    return json_store_utils.read_json(path)


def append_call_metrics(run_dir: Path,
                        row: dict[str, Any]) -> None:
    """One line per call, written the moment the call returns. A last line a kill cut in half is ended first, so this
    row starts a line of its own and is read; the torn one is still dropped."""

    json_lines_utils.append_json_line(run_dir / METRICS_FILE, row)


def read_call_metrics(run_dir: Path) -> list[dict[str, Any]]:
    """Every complete line. A line a kill cut in half is dropped, never a reason to lose the rest."""

    return json_lines_utils.read_json_lines(run_dir / METRICS_FILE)


def render_metrics_line(rows: list[dict[str, Any]],
                        table_path: str) -> str:
    """The exit line: this process's calls summed, and the file that holds every call (one line, never a table of
    every call)."""

    if not rows:
        return "calls this resume: none"
    prompt = _compute_total(rows, "promptTokens")
    output = _compute_total(rows, "outputTokens")
    minutes = _compute_total(rows, "durationS") / 60

    return (f"calls this resume: {len(rows)} · {prompt:,} prompt + {output:,} output tokens · {minutes:.1f} min · "
            f"table in {table_path}")
