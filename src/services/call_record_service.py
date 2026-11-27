"""The one writer of the run's call record (`calls.jsonl`): every model call that passes through `chain_runner` is
one row, wherever in the program it was made.

Each place that called a model once wrote its own row, and a place added later was counted only when someone noticed
its phase's closing line saying "no model call" after hours of calls: phase 4, phase 6, the audio and the video phase 2
and phase 7 each in turn (AGENTS.md, "Findings fixed again and again"). Now a phase or a step says where its calls
belong (`set_place`, `update_place`), from its timing start, and `record_call` writes the row the moment a call returns.
With no place set (a test, a probe, the document chat) nothing is written.
"""

from __future__ import annotations

import contextvars
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from agent_client import finish_reason_utils
from services import call_log_service
from utils import chain_response_utils

__all__ = ["set_place", "update_place", "read_place", "clear_place", "record_call"]

# Where the next call's row goes and what it is of: the run folder, the phase, the step, the attempt, the window
_PLACE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("call_place", default=None)


def set_place(run_dir: Path,
              phase: str,
              slug: str | None = None,
              attempt: int | None = None,
              window_tokens: int | None = None) -> None:
    """The calls made from now on belong to `phase` (and its step `slug`, attempt `attempt`, on a model loaded with
    `window_tokens`) of the run in `run_dir`."""

    _PLACE.set({"run_dir": run_dir, "phase": phase, "slug": slug, "attempt": attempt, "windowTokens": window_tokens})


def update_place(**fields: Any) -> None:
    """Change what the next calls are of (`slug`, `attempt`, `windowTokens`); nothing when no place is set."""

    place = _PLACE.get()
    if place is not None:
        _PLACE.set({**place, **fields})


def read_place() -> dict[str, Any] | None:
    """The place set now, or None."""

    return _PLACE.get()


def clear_place() -> None:
    """No place: calls made from now on write no row."""

    _PLACE.set(None)


def record_call(reply: BaseMessage | None,
                started_epoch_s: float,
                duration_s: float) -> None:
    """One row for one call that returned `reply` after `duration_s`, in the run the place names: its phase, step and
    attempt, its tokens and times as the host reported them, and how it ended (a thinking stopped as a loop is named
    so). Nothing when no place is set."""

    place = _PLACE.get()
    if place is None:
        return
    usage = chain_response_utils.read_token_usage(reply) if reply is not None else {}
    row = {key: place[key] for key in ("phase", "slug", "attempt", "windowTokens") if place.get(key) is not None}
    row.update({"startedAtS": round(started_epoch_s, 3), "promptTokens": usage.get("prompt_tokens"),
                "outputTokens": usage.get("output_tokens"), "promptDurationS": usage.get("prompt_duration_s"),
                "outputDurationS": usage.get("output_duration_s"), "durationS": round(duration_s, 1),
                "finishReason": finish_reason_utils.read_finish_reason(reply) if reply is not None else None,
                "recordedAtS": round(time.time(), 3)})
    call_log_service.append_call_metrics(run_dir=place["run_dir"],
                                         row=row)
