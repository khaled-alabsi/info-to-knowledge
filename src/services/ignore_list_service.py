# <!-- sync: seed/services.ignore_list_service v1 -->
"""The ignore list, applied in two layers.

Layer A - here, in Python, and authoritative. A matching item is never dispatched.
Layer B - rendered into the phase-2 task file, so a worker launched on an ignored target by
          mistake recognises it and exits without tracing. That is a guard against an
          orchestrator bug, not the mechanism; layer A is the mechanism.

Ignored items are recorded as `skipped` with the rule that matched, never silently dropped, so
coverage still reads against the true denominator.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import config
from utils import display_path_utils

LOG = logging.getLogger(__name__)

FIELDS = ("paths", "slugs", "kinds", "triggers", "actionPoints")


@dataclass
class IgnoreList:
    paths: list[str] = field(default_factory=list)
    slugs: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    actionPoints: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in FIELDS)

    def as_dict(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in FIELDS}

    # -- matching -----------------------------------------------------------------------

    def match(self,
              item: dict) -> str | None:
        """The first rule this item matches, as a human-readable string, or None."""

        checks = (
            ("slugs", item.get("slug", "")),
            ("kinds", item.get("kind", "")),
            ("triggers", item.get("trigger", "")),
            ("actionPoints", item.get("actionPoint", "")),
        )
        for name, value in checks:
            for pattern in getattr(self, name):
                if _matches_rule(pattern, value):
                    return f"{name}: {pattern}"

        source = (item.get("source") or "").replace("\\", "/")
        for pattern in self.paths:
            if _matches_path(pattern, source):
                return f"paths: {pattern}"

        return None

    def apply(self,
              items: list[dict]) -> list[dict]:
        """Return items with `ignored` / `ignoreRule` set. Order and count are preserved."""

        out = []
        for item in items:
            rule = self.match(item)
            out.append({**item, "ignored": rule is not None, "ignoreRule": rule})

        return out

    def render(self) -> str:
        """The list as it is shown to a worker, for layer B."""

        if self.is_empty:
            return "(empty - nothing is ignored on this run)"
        lines = []
        for name in FIELDS:
            values = getattr(self, name)
            if values:
                lines.append(f"- {name}:")
                lines += [f"    - `{value}`" for value in values]

        return "\n".join(lines)


def _matches_rule(pattern: str,
                  candidate: str) -> bool:
    if not candidate:
        return False

    return fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(candidate.lower(), pattern.lower())


def _matches_path(pattern: str,
                  candidate: str) -> bool:
    """Path match that treats `**/x` the way a developer expects: x at any depth."""

    if not candidate:
        return False
    if fnmatch.fnmatch(candidate, pattern):
        return True
    # fnmatch has no `**`; collapse a leading `**/` and retry against every suffix.
    if pattern.startswith("**/"):
        tail = pattern[3:]
        parts = candidate.split("/")

        return any(fnmatch.fnmatch("/".join(parts[i:]), tail) for i in range(len(parts)))

    return False


def load_ignore_list(path: Path | None = None) -> IgnoreList:
    """Load `ignore.json` into an `IgnoreList`. A missing or malformed file means no rules."""

    # Never raises: an unreadable ignore file must not stop a run, and an empty list is the safe
    # default - it analyses everything rather than silently skipping everything.

    source = path or config.DEFAULT_IGNORE_FILE
    if not source.is_file():
        # an ignore file is optional, and most runs have none: said on every run's console, the line was noise
        LOG.debug(f"no ignore rules file found at {display_path_utils.render_path(source)}; no items will be ignored")

        return IgnoreList()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOG.warning(f"ignoring {source.name}: it is not valid JSON; no ignore rules applied")

        return IgnoreList()
    if not isinstance(data, dict):
        return IgnoreList()
    known = {"paths", "slugs", "kinds", "triggers", "actionPoints", "sources"}

    return IgnoreList(**{k: list(v) for k, v in data.items()
                         if k in known and isinstance(v, list)})
