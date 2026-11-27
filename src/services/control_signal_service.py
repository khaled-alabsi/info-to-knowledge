# <!-- sync: seed/services.control_signal_service v2 -->
"""`control.json` - how an operator pauses, drains or stops a running graph.

Three signals, honoured at the points a run can wait or end:

  between items    all three  (a graph edge)
  between attempts pause, stop (the attempt loop)
  between a conversation's calls  pause, stop (phases 4 and 5)

`drain` deliberately is NOT honoured between attempts: drain means "finish the current item", and a
retry is part of finishing it. Between items is where drain lands.

An unreadable or torn file is NO signal, never an exception. This file is written by another process
while this one polls it, so a partial read is normal and must not end a run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

PAUSE = "pause"
DRAIN = "drain"
STOP = "stop"
_VALID = {PAUSE, DRAIN, STOP}

CONTROL_FILE = "control.json"
# How the console line a run writes when it starts holding at a pause ends; the page reads it to say "paused"
HELD_LINE_END = "- waiting for `unpause` or `stop`"
_POLL_S = 2.0
# The seconds this process has waited out a pause, so a step timed across one counts only its work (DEFECTS 334: a fix
# call paused for 54 minutes was logged at 58:16, "one frame takes 1165 s")
_paused_s = 0.0

__all__ = ["PAUSE", "DRAIN", "STOP", "HELD_LINE_END", "is_run_holding", "read_signal", "read_paused_seconds", "request", "clear", "check"]


def _build_signal_path(run_dir: Path) -> Path:
    return run_dir / CONTROL_FILE


def read_paused_seconds() -> float:
    """The seconds this process has spent waiting out pauses so far."""

    return _paused_s


def read_signal(run_dir: Path) -> str | None:
    path = _build_signal_path(run_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None                      # torn write, or gone since is_file(). Not a signal.
    signal = data.get("signal") if isinstance(data, dict) else None

    return signal if signal in _VALID else None


def is_run_holding(run_dir: Path,
                   log_path: Path | None) -> bool:
    """Whether the run is holding at its pause now: a pause is asked, and the last line of its console says it holds
    (a run sending a model call when the pause came holds only once that call ends)."""

    if read_signal(run_dir) != PAUSE or log_path is None or not log_path.is_file():
        return False
    with log_path.open("rb") as log:
        log.seek(max(0, log_path.stat().st_size - 2000))
        lines = [line for line in log.read().decode("utf-8", "replace").splitlines() if line.strip()]

    return bool(lines) and lines[-1].endswith(HELD_LINE_END)


def request(run_dir: Path,
            signal: str,
            by: str = "cli") -> None:
    if signal not in _VALID:
        raise ValueError(f"{signal!r} is not one of {sorted(_VALID)}")
    path = _build_signal_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replaced whole: a reader polling the file never finds it empty, which would lift a pause
    written = path.with_name(f"{path.name}.writing")
    written.write_text(json.dumps({
        "signal": signal,
        "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "requestedBy": by,
    }, indent=2), encoding="utf-8")
    os.replace(written, path)


def clear(run_dir: Path) -> None:
    _build_signal_path(run_dir).unlink(missing_ok=True)


def check(run_dir: Path,
          logger=None,
          label: str = '') -> str | None:
    """Block while paused; return the signal that needs acting on."""

    # Returns `STOP` or `DRAIN` for the caller to act on, and `None` when there is nothing pending. A
    # `PAUSE` is consumed here by waiting it out, so a caller never has to handle it.

    signal = read_signal(run_dir)
    if signal != PAUSE:
        return signal

    if logger is not None:
        logger.info(f"  paused{f' before {label}' if label else ''} {HELD_LINE_END}")
    global _paused_s
    paused_at = time.monotonic()
    while True:
        time.sleep(_POLL_S)
        signal = read_signal(run_dir)
        if signal == PAUSE:
            continue
        _paused_s += time.monotonic() - paused_at
        if logger is not None:
            logger.info(f"  resumed ({signal or 'pause cleared'})")

        return signal
