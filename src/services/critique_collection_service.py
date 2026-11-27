"""Phase 6, the critique collection: every batch's critique of one run, grouped into one rule set.

The GROUPING AND THE COUNTING ARE PROGRAM WORK, never a model's: which rules are the same rule is decided by their
names, and how many batches and frames each one hit is arithmetic. The model is asked one thing only - to word each
group as one rule for any meeting - and a group it drops as one meeting's content is dropped.

A rule arrives here already generic: the check that accepted its critique refuses a frame number inside the rule and
a quote off this meeting's screens, so this file carries no meeting content and can be read straight into
`agent-context/instructions/frames_processing.md`.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from schemas import critique_record
from services import batch_summaries_service
from utils import critique_markdown_utils

__all__ = ["CRITIQUE_SUFFIX", "list_critique_files", "read_run_rules", "group_rules", "split_group_number",
           "split_group_numbers", "merge_worded_groups", "match_group",
           "render_rule_set", "write_rule_set",
           "build_rule_set_path", "find_written_rule_set", "count_kept_rules"]

# Each kept rule opens its section of the rule set with this mark
RULE_HEADING_MARK = "## "

# `<batch>-critique.md`: the record name phase 3 writes the critique under (`batch_summaries_service.build_critique_slug`)
CRITIQUE_SUFFIX = f"{batch_summaries_service.build_critique_slug('')}.md"
# Two rules are the same rule when their names read alike at this much: "An arrow is kept from the frame before" and
# "An arrow is kept from the previous frame" are one anti-pattern, and a model words one differently in each batch.
_SAME_NAME = 0.82
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
# "Group 3: A table is outlined without its row labels": the request numbers its groups, and each answer heading opens
# with the number of the group it words, so a rule worded afresh from all its group's wordings is still found
_GROUP_NUMBER = re.compile(r"^\s*group\s+(\d+)\s*[:.\-–—]\s*", re.IGNORECASE)
# "Groups 3, 7 and 12: <name>": one rule worded for several groups that are one anti-pattern (DEFECTS 406)
_GROUP_NUMBERS = re.compile(r"^\s*groups?\s+(\d+(?:\s*(?:,|and|&)\s*\d+)*)\s*[:.\-–—]\s*", re.IGNORECASE)


def build_rule_set_path(paths: RunPaths) -> Path:
    """The one file phase 6 writes."""

    return paths.output_root / output_contract.CRITIQUE_COLLECTION_DIR_NAME / output_contract.CRITIQUE_COLLECTION_FILE


def find_written_rule_set(paths: RunPaths) -> Path | None:
    """The rule set this run's phase 6 wrote, under its name now or under the name a run made before the rename used
    (`06_lessons/anti-patterns.md`); None when it wrote none."""

    retired = (paths.output_root / output_contract.RETIRED_CRITIQUE_COLLECTION_DIR_NAME
               / output_contract.RETIRED_CRITIQUE_COLLECTION_FILE)

    return next((path for path in (build_rule_set_path(paths), retired) if path.is_file()), None)


def list_critique_files(paths: RunPaths) -> list[Path]:
    """Every batch's critique of this run, in batch order, which is the order their names sort in."""

    folder = paths.output_root / output_contract.ITEMS_DIR_NAME

    return sorted(folder.glob(f"*{CRITIQUE_SUFFIX}")) if folder.is_dir() else []


def read_run_rules(paths: RunPaths) -> list[dict[str, Any]]:
    """Every rule of every critique of this run, each carrying the batch it came from."""

    rules: list[dict[str, Any]] = []
    for path in list_critique_files(paths):
        critique = critique_markdown_utils.parse_critique(path.read_text(encoding="utf-8")).critique
        batch = path.name[: -len(CRITIQUE_SUFFIX)]
        for rule in (critique or {}).get("rules") or []:
            rules.append({**rule, "batch": batch})

    return rules


def _is_same_rule(one: str,
                  other: str) -> bool:
    """Whether two rule names are the same anti-pattern: the same words, or names that read alike."""

    one_name, other_name = one.casefold(), other.casefold()
    one_words, other_words = set(_WORD_PATTERN.findall(one_name)), set(_WORD_PATTERN.findall(other_name))
    if one_words and one_words == other_words:
        return True

    return difflib.SequenceMatcher(None, one_name, other_name).ratio() >= _SAME_NAME


def group_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per anti-pattern, most frequent first: its name, every wording it was given, the batches it was seen
    in and how many frames it hit in all. Ties keep the order the batches ran in, so the list is the same every run."""

    groups: list[dict[str, Any]] = []
    for rule in rules:
        name = str(rule.get("name") or "").strip()
        group = next((entry for entry in groups if _is_same_rule(entry["name"], name)), None)
        if group is None:
            group = {"name": name, "wordings": [], "batches": [], "frames": 0}
            groups.append(group)
        group["wordings"].append({field: str(rule.get(field) or "") for field in critique_record.RULE_LINES})
        if rule.get("batch") and rule["batch"] not in group["batches"]:
            group["batches"].append(rule["batch"])
        group["frames"] += len(critique_markdown_utils.list_rule_frames(rule))

    # A stable sort: ties keep the order the batches ran in
    return sorted(groups, key=lambda group: (-len(group["batches"]), -group["frames"]))


def split_group_number(name: str) -> tuple[int | None, str]:
    """The group number a rule's heading opens with, and the name after it; None and the name as written without one."""

    match = _GROUP_NUMBER.match(name)
    if match is None:
        return None, name.strip()

    return int(match.group(1)), name[match.end():].strip()


def split_group_numbers(name: str) -> tuple[list[int], str]:
    """Every group number a rule's heading opens with ("Group 3, 7 and 12: <name>"), and the name after them; no number
    and the name as written without one."""

    match = _GROUP_NUMBERS.match(name)
    if match is None:
        return [], name.strip()

    return [int(number) for number in re.findall(r"\d+", match.group(1))], name[match.end():].strip()


def merge_worded_groups(groups: list[dict[str, Any]],
                        rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """The groups as the model's rules word them, and each kept group's wording by its name. A rule naming several
    groups by number words them as one rule: the program merges them, their batches joined and their frames added, so
    the counting stays the program's. Names that read alike never matched rules a model words afresh in each batch, and
    one anti-pattern stood as four groups (DEFECTS 406). A group a rule already took is not taken again."""

    taken: set[int] = set()
    merged: dict[int, dict[str, Any]] = {}
    worded: dict[str, dict[str, str]] = {}
    for rule in rules:
        name = str(rule.get("name") or "")
        numbers, rest = split_group_numbers(name)
        positions = [number - 1 for number in dict.fromkeys(numbers) if 1 <= number <= len(groups)]
        if not positions:
            group = match_group(groups, name)
            positions = [groups.index(group)] if group is not None else []
        positions = [position for position in positions if position not in taken]
        if not positions:
            continue
        taken.update(positions)
        first = groups[positions[0]]
        group = {"name": first["name"], "wordings": [wording for position in positions
                                                     for wording in groups[position]["wordings"]],
                 "batches": list(dict.fromkeys(batch for position in positions for batch in groups[position]["batches"])),
                 "frames": sum(groups[position]["frames"] for position in positions)}
        merged[positions[0]] = group
        worded[group["name"]] = {**rule, "name": rest}
    # The groups no rule took stay as they are, so the file can say how many were dropped
    kept = [merged.get(position, group) for position, group in enumerate(groups)
            if position in merged or position not in taken]

    return sorted(kept, key=lambda group: (-len(group["batches"]), -group["frames"])), worded


def match_group(groups: list[dict[str, Any]],
                name: str) -> dict[str, Any] | None:
    """The group a rule the model wrote answers: the group its heading's number names, else the group whose name reads
    alike; None when it answers none, which drops the rule."""

    number, rest = split_group_number(name)
    if number is not None and 1 <= number <= len(groups):
        return groups[number - 1]

    return next((group for group in groups if _is_same_rule(str(group.get("name") or ""), rest)), None)


# Letters of a script the rule set is not written in, and the English gloss the model put after them: a rule read
# "The notes截取 (cut off) a direct quotation" (the Matrix talk); the gloss is kept where it stands, the letters dropped
_OTHER_SCRIPT = re.compile(r"\s*[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]+(?:\s*\(([^()]*)\))?")


def _drop_other_script(text: str) -> str:
    """`text` with each run of letters of another script replaced by the gloss in brackets after it, or removed."""

    return _OTHER_SCRIPT.sub(lambda match: f" {match.group(1)}" if match.group(1) else "", text).strip()


def _render_count(count: int,
                 singular: str,
                 plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def render_rule_set(groups: list[dict[str, Any]],
                    worded: dict[str, dict[str, str]]) -> str:
    """The run's rule set: one section per group, most frequent first, with the counts the program worked out and the
    wording the model gave it. A group the model dropped is left out, and the file says how many it dropped."""

    kept = [group for group in groups if group["name"] in worded]
    lines = [f"# {critique_record.CRITIQUE_TITLE}: the anti-patterns of this run", ""]
    kept_batches = {batch for group in kept for batch in group["batches"]}
    lines.append(f"{_render_count(len(kept), 'rule', 'rules')} from {_render_count(len(kept_batches), 'batch', 'batches')}"
                 + (f", {len(groups) - len(kept)} dropped as one meeting's content" if len(groups) > len(kept) else "")
                 + ".")
    lines.append("")
    for group in kept:
        rule = worded[group["name"]]
        lines.append(f"{RULE_HEADING_MARK}{_drop_other_script(str(rule.get('name') or group['name']))}")
        for field, label in critique_record.RULE_LINES.items():
            lines.append(f"{label}: {_drop_other_script(str(rule.get(field) or ''))}")
        lines.append(f"Seen in: {_render_count(len(group['batches']), 'batch', 'batches')}, "
                     f"{_render_count(group['frames'], 'frame', 'frames')}")
        lines.append("")

    return "\n".join(lines)


def write_rule_set(paths: RunPaths,
                   groups: list[dict[str, Any]],
                   worded: dict[str, dict[str, str]]) -> Path:
    """The run's rule set written into `06_critique-collection/critique-collection.md`."""

    path = build_rule_set_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_rule_set(groups, worded), encoding="utf-8")

    return path


def count_kept_rules(path: Path) -> int:
    """How many rules the rule set at `path` keeps: one section each, as `render_rule_set` writes them."""

    return sum(line.startswith(RULE_HEADING_MARK) for line in path.read_text(encoding="utf-8").splitlines())
