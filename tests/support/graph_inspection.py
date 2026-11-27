"""A compiled graph over a throwaway context, for tests and for drawing the graph's wiring."""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from pathlib import Path

from graph import graph_assembly
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore

__all__ = ["build_graph_for_inspection"]


def build_graph_for_inspection():
    """A graph compiled with a throwaway context, for `graph.get_graph().draw_mermaid()`. Compiling
    proves the nodes exist; the render proves they are wired as designed."""

    inspection_dir = Path(tempfile.mkdtemp())
    # The returned graph holds the context, so the folder is removed when the process ends, not here
    atexit.register(shutil.rmtree, inspection_dir, ignore_errors=True)
    paths = RunPaths(repository=inspection_dir, module="inspect", output_root_override=inspection_dir / "out")
    identity = RunIdentity(run_id="inspect", repository=str(inspection_dir), module="inspect",
                           output_root=str(paths.output_root))
    ctx = RunContext(repository=inspection_dir, module="inspect", paths=paths,
                     store=RunStore.create(inspection_dir / "run.json", identity, budgets={}, models=[]),
                     run_dir=inspection_dir, log=logging.getLogger("inspect"),
                     ignore=IgnoreList())

    return graph_assembly.build_graph(ctx)
