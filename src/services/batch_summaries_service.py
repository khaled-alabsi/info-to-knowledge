"""`03_batch_summaries.json`: one entry per batch of phase 3, in time order. REQUIREMENTS.md R15, part 2.

A batch is one unit of phase 3's work: a batch of frames with its accepted `batch_summary`, or a unit of one frame
whose description stands in for it (`source` says which), so the map of the meeting has no holes. Each entry is
`{"slug", "frames", "start", "end", "title", "summary", "source", "atmosphere", "openQuestions",
"answeredQuestions"}` (the last four from the batch's follow-ups), written the moment it is accepted, never once at the
end.
The frame records' file is not touched.

The account a call returned waits in its own record, `<call slug>-batch-summary`, until the program has judged it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from utils import json_store_utils

__all__ = ["build_batch_summaries_path", "build_summary_slug", "read_group_slug", "build_critique_slug",
           "build_second_pass_slug", "OPEN_QUESTIONS_SUFFIX", "ANSWERS_SUFFIX", "SUMMARY_SUFFIX", "CRITIQUE_SUFFIX",
           "SECOND_PASS_SUFFIX", "read_batch_summaries", "reset_batch_summaries", "save_batch_summary"]


# What a batch's question follow-ups add to its slug: the open questions it leaves, and the answers found for them
OPEN_QUESTIONS_SUFFIX = "open-questions"
ANSWERS_SUFFIX = "answers"
# What the batch's account, its critique and its second look add to its slug
SUMMARY_SUFFIX = "batch-summary"
CRITIQUE_SUFFIX = "critique"
SECOND_PASS_SUFFIX = "second-pass"

# What a call of one batch adds to the batch's slug: a repair round, or the account asked alone
_CALL_SUFFIX = re.compile(r"-(?:repair(?:-\d+)?|alone)$")


def _write_batch_summaries(paths: RunPaths,
                           batches: list[dict[str, Any]]) -> None:
    ordered = sorted(batches, key=lambda batch: (float(batch["start"]), batch["frames"][0]))
    json_store_utils.write_json(build_batch_summaries_path(paths), {"schemaVersion": 1, "batches": ordered})


def build_batch_summaries_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.BATCH_SUMMARIES_FILE


def build_summary_slug(call_slug: str) -> str:
    """The record a call's `batch_summary` is written to, beside the frame records it came with."""

    return f"{call_slug}-{SUMMARY_SUFFIX}"


def read_group_slug(record_slug: str) -> str:
    """The batch a record of its calls belongs to: `frames-000-006-repair-2-batch-summary` is `frames-000-006`. Repair
    round 1 is `-repair`, round N after it `-repair-N`, and a batch's account asked alone `-alone`."""

    return _CALL_SUFFIX.sub("", record_slug.removesuffix(f"-{SUMMARY_SUFFIX}"))


def build_critique_slug(call_slug: str) -> str:
    """The record a batch's critique is written to; its validator finds it by the same name."""

    return f"{call_slug}-{CRITIQUE_SUFFIX}"


def build_second_pass_slug(call_slug: str) -> str:
    """The record a batch's second look is written to; its validator finds it by the same name."""

    return f"{call_slug}-{SECOND_PASS_SUFFIX}"


def read_batch_summaries(paths: RunPaths) -> list[dict[str, Any]]:
    """Every batch, in time order. An absent file is no batch yet."""

    path = build_batch_summaries_path(paths)
    if not path.is_file():
        return []

    return list(json_store_utils.read_json(path).get("batches", []))


def reset_batch_summaries(paths: RunPaths) -> None:
    _write_batch_summaries(paths, [])


def save_batch_summary(paths: RunPaths,
                       batch: dict[str, Any]) -> None:
    """Put one accepted batch into the file, replacing its own earlier entry and no other."""

    kept = [existing for existing in read_batch_summaries(paths) if existing["slug"] != batch["slug"]]
    _write_batch_summaries(paths, [*kept, batch])
