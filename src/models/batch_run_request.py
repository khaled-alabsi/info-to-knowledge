"""What one batch's conversation with the worker needs: which batch of which run, the model that answers, and where it logs and checks for a stop. Lives for one call."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

from models.run_paths import RunPaths

__all__ = ["BatchRunRequest"]


@dataclass(frozen=True)
class BatchRunRequest:
    paths: RunPaths
    state: dict[str, Any]                                       # the pass's state: its batches, wishes and (phase 7 cleanup) pass
    number: int                                                 # the batch's number, from 1
    model: BaseChatModel
    log: Any
    control_check: Callable[[], str | None] | None = None       # asks for a stop or a drain; `None` never asks
