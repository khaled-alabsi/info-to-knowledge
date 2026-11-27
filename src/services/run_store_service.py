# <!-- sync: seed/services.run_store_service v2 -->
"""`run.json` - the durable record of a run, and the ONLY thing that outlives the process.

A `*Store`: the suffix has to name a lifetime, because that is what decides whether
a value may be mirrored into graph state (it may not) or checkpointed (it is not). Written atomically
and wholesale, human-readable, and what `status` and `report` read.

**It commits INSIDE the node, the moment a validator passes** - which is what makes it the resume
mechanism rather than a checkpointer. A checkpoint only commits after the node returns, so a crash
part-way through a long item loses it and keeps this. Resume re-reads item statuses from here before
dispatching anything.

**State never mirrors a fact from here.** Where the graph needs one - which phase finished, which
items are still pending - the node derives it at the point of use. Two copies of one fact is how a
resume decides an item is pending that is already written.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from models import output_contract
from models.run_identity import RunIdentity
from utils import json_store_utils

__all__ = ["RunStore", "new_run_id"]


def new_run_id(is_dry_run: bool = False) -> str:
    """`2026-09-28_18-05`: the date and the minute the run was created, as a reader reads them. To the minute, because
    two runs of one output folder cannot start in the same one."""

    stamp = time.strftime("%Y-%m-%d_%H-%M")

    return f"dry-{stamp}" if is_dry_run else stamp



def _rename_retired_steps(store_data: dict[str, Any]) -> dict[str, Any]:
    """A record written before the phases were renamed, with its phase and attempt names read as they are now."""

    retired = output_contract.RETIRED_STEP_NAMES
    phases = {retired.get(name, name): status for name, status in (store_data.get("phases") or {}).items()}
    attempts = {}
    for key, rows in (store_data.get("attempts") or {}).items():
        kind, separator, slug = key.partition("/")
        attempts[f"{retired.get(kind, kind)}{separator}{slug}"] = rows

    return {**store_data, "phases": phases, "attempts": attempts}


class RunStore:
    def __init__(self,
                 path: Path,
                 store_data: dict[str, Any]):
        self.path = path
        self.data = store_data
        # The attempts each key held when this life of the run opened the store: a resumed life gets its budget again
        self._life_offsets = {key: len(attempts) for key, attempts in (store_data.get("attempts") or {}).items()}

    # -- lifecycle ------------------------------------------------------------------------------

    @classmethod
    def create(cls,
               path: Path,
               identity: RunIdentity,
               budgets: dict,
               models: list[dict]) -> "RunStore":
        return cls(path, {
            "schemaVersion": output_contract.RUN_STATE_SCHEMA_VERSION,
            **identity.as_dict(),
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "running",
            "budgets": budgets,
            # Recording which model each role resolved to is what makes "why did this run come out
            # differently" answerable. A role that silently resolved elsewhere explains a whole run.
            "models": models,
            # The process running this folder now. `status`, the page and the reset read it to tell a live run from a
            # stopped one: a run started by `inbox`, by the queue or by hand all write it, and a path with spaces in
            # it reads back the same way, which a command line scanned with a regex does not.
            "pid": os.getpid(),
            "phases": {},
            "items": {},
            "attempts": {},
        })

    @classmethod
    def load(cls,
             path: Path) -> "RunStore | None":
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "runId" not in data:
            return None

        return cls(path, _rename_retired_steps(data))

    @classmethod
    def load_latest(cls,
                    runs_root: Path) -> "RunStore | None":
        """Newest resumable run. Dry runs are skipped - they are throwaway by construction."""

        if not runs_root.is_dir():
            return None
        for directory in sorted((d for d in runs_root.iterdir() if d.is_dir()), reverse=True):
            if directory.name.startswith("dry-"):
                continue
            store = cls.load(directory / output_contract.RUN_STATE_FILE)
            if store is not None:
                return store

        return None

    def save(self) -> None:
        json_store_utils.write_json(self.path, self.data)

    def mark_alive(self) -> None:
        """Record this process as the one writing the folder. Called where a run begins, new or resumed."""

        self.data["pid"] = os.getpid()
        self.save()

    # -- items ----------------------------------------------------------------------------------

    def seed_items(self,
                   items: list[dict]) -> None:
        """Freeze the loop. After this the item set does not change - only statuses do."""

        self.data["items"] = {
            item["slug"]: {**item, "status": item.get("status", "pending"), "attempts": 0}
            for item in items
        }
        self.save()

    def list_items(self) -> dict[str, dict]:
        return self.data.get("items", {})

    def get_item(self,
                 slug: str) -> dict:
        return self.list_items().get(slug, {})

    def set_item_status(self,
                        slug: str,
                        status: str,
                        **extra: Any) -> None:
        if status not in output_contract.ITEM_STATUSES:
            raise ValueError(f"{status!r} is not an item status")
        entry = self.data.setdefault("items", {}).setdefault(slug, {"slug": slug})
        entry["status"] = status
        entry.update(extra)
        self.save()

    def count_by_status(self) -> dict[str, int]:
        out = {status: 0 for status in output_contract.ITEM_STATUSES}
        for entry in self.list_items().values():
            out[entry.get("status", "pending")] = out.get(entry.get("status", "pending"), 0) + 1
        out["total"] = len(self.list_items())

        return out

    def list_pending_queue(self,
                           selected_slugs: list[str] | None = None) -> list[str]:
        """Slugs still to do."""

        # Terminal items are dropped here, on every run including a resume, and `selected_slugs` does not
        # override that: dispatching a finished item again puts an accepted artifact back under a worker and
        # costs a full attempt. Redoing finished work is `reset-to`.

        wanted = set(selected_slugs or [])
        slugs = [
            slug for slug, entry in self.list_items().items()
            if entry.get("status") not in output_contract.TERMINAL_ITEM_STATUSES
            and (not wanted or slug in wanted)
        ]

        return sorted(slugs)

    # -- phases and attempts --------------------------------------------------------------------

    def set_phase(self,
                  phase: str,
                  status: str) -> None:
        self.data.setdefault("phases", {})[phase] = status
        self.save()

    def get_phase(self,
                  phase: str) -> str:
        return self.data.get("phases", {}).get(phase, "pending")

    def count_attempts(self,
                       phase: str,
                       slug: str | None = None) -> int:
        return len(self.data.get("attempts", {}).get(self._build_attempt_key(phase, slug), []))

    def count_attempts_this_life(self,
                                 phase: str,
                                 slug: str | None = None) -> int:
        """The attempts this life of the run made: a step blocked in an earlier life is asked again on a resume, as
        "fix the cause, then `--resume`" promises (with one attempt a step, a blocked frame never was)."""

        key = self._build_attempt_key(phase, slug)

        return self.count_attempts(phase, slug) - self._life_offsets.get(key, 0)

    def record_attempt(self,
                       phase: str,
                       slug: str | None,
                       attempt: dict) -> None:
        self.data.setdefault("attempts", {}).setdefault(self._build_attempt_key(phase, slug), []).append(attempt)
        if slug and slug in self.list_items():
            self.list_items()[slug]["attempts"] = self.count_attempts(phase, slug)
        self.save()

    def list_attempts_for(self,
                          phase: str,
                          slug: str | None = None) -> list[dict]:
        return self.data.get("attempts", {}).get(self._build_attempt_key(phase, slug), [])

    def read_last_findings(self,
                           phase: str,
                           slug: str | None = None) -> list[dict]:
        """The findings the last recorded attempt of this job was refused with; none when it has no attempt."""

        attempts = self.list_attempts_for(phase, slug)

        return list((attempts[-1].get("validation") or {}).get("findings") or []) if attempts else []

    @staticmethod
    def _build_attempt_key(phase: str,
                           slug: str | None) -> str:
        return f"{phase}/{slug}" if slug else phase

    def set_status(self,
                   status: str) -> None:
        self.data["status"] = status
        self.data["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def read_budget(self,
                    name: str,
                    default: Any = None) -> Any:
        return self.data.get("budgets", {}).get(name, default)
