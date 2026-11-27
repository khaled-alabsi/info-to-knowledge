# <!-- sync: seed/models.run_context v1 -->
"""`RunContext` - everything a node needs that is not state.

A LangGraph node's signature is `(state) -> dict`, and that pushes hard toward reaching for a module
singleton or a `RunContext.current()` accessor to get at a repository path, a store or a model. Do
not. Ambient state breaks the moment two runs share a process, it cannot be substituted in a test, and
it silently couples every node to import order.

The answer used everywhere in this playbook is a **factory that closes over the context**:
`build_graph(ctx)` assembles the nodes, each closed over this object.

A CARRIER, and only that. It holds handles; it performs nothing. Rendering a dry run, reading a
control signal and loading the ignore list are side effects, and they live in `services/` - a carrier
that calls `write_text` is a service wearing the wrong name, and putting them here is what made this
module four things at once.

Nothing here belongs in state either: a Path, a logger, an open store and a live model are not
serialisable and must never be checkpointed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

# Read for the type hint alone: imported at run time, it brought LangChain into every module that names a run's
# context, `i2k mcp`'s start among them (DEFECTS 663)
if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from models.run_paths import RunPaths
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore

__all__ = ["RunContext"]


@dataclass
class RunContext:
    repository: Path
    module: str
    paths: RunPaths
    store: RunStore
    run_dir: Path
    log: logging.Logger
    ignore: IgnoreList
    is_dry_run: bool = False
    selected_slugs: list[str] = field(default_factory=list)
    control_check: Callable[[], str | None] | None = None
    # Injected in tests. None means each worker resolves its own phase's model.
    model: BaseChatModel | None = None
    # The context window each phase's model is loaded with, read once at the start: a call's context line is measured
    # against it. A phase missing here falls back to its model's configured `num_ctx`.
    phase_windows: dict[str, int] = field(default_factory=dict)
