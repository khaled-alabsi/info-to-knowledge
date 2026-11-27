# <!-- sync: seed/services.dry_run_service v1 -->
"""Rendering what a worker WOULD be sent, and sending nothing.

Writing the two messages to disk is the point of a dry run: an unresolved placeholder or a missing
contract file is exactly what it should surface, rather than leaving it to fail mid-attempt against a
real repository.

A service, not a method on `RunContext`: it creates directories and writes files, and a carrier that
does that is a service wearing the wrong name.
"""

from __future__ import annotations

from models.run_context import RunContext

__all__ = ["describe_dry_run"]


def describe_dry_run(ctx: RunContext,
                     phase: str,
                     slug: str | None,
                     system_prompt: str,
                     human_message: str) -> None:
    """Write this phase's rendered prompt and message under the run's `dry-run/` directory."""

    target = f"{phase}/{slug}" if slug else phase
    directory = ctx.run_dir / "dry-run" / target
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "system-prompt.md").write_text(system_prompt, encoding="utf-8")
    (directory / "human-message.md").write_text(human_message, encoding="utf-8")
    ctx.log.info(f"  [dry-run] {target}")
    ctx.log.info(f"    system prompt : {len(system_prompt.splitlines())} lines "
                 f"-> {directory}/system-prompt.md")
    ctx.log.info(f"    human message : {len(human_message.splitlines())} lines "
                 f"-> {directory}/human-message.md")
