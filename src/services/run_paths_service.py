# <!-- sync: seed/services.run_paths_service v1 -->
"""Creating the directories a run owns.

Nothing is staged into them. The instruction text under the playbook's own `agent-context/` reaches
a worker INLINED in its system prompt, assembled in process by `prompts/system_prompt_builder.py`,
and no tool a call is given takes a path at all, so there is nothing a copy under the output root
would make reachable.
"""

from __future__ import annotations

from models.run_paths import RunPaths

__all__ = ["ensure_run_paths"]


def ensure_run_paths(paths: RunPaths) -> None:
    """Create the directories the orchestrator owns."""

    for directory in (paths.output_root, paths.runs_root):
        directory.mkdir(parents=True, exist_ok=True)
