"""Phase 7's blocks: the published document cut into numbered blocks a worker reads and edits by number.

A block is a heading line, a fenced block whole, or a run of lines up to a blank line. Each keeps the number it got when
phase 7 first cut the document, so an edit named by an earlier read still names the same text after other edits.
"""

from __future__ import annotations

import re
from typing import Any

from utils import mermaid_text_utils

__all__ = ["BLOCK_ID_PREFIX", "list_blocks", "render_blocks", "join_blocks", "split_batches", "apply_edits",
           "read_block_id", "list_figure_lines", "is_heading"]

BLOCK_ID_PREFIX = "B"
_HEADING = re.compile(r"^#{1,6} \S")
_FENCE = "```"
_FIGURE_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)")
_BLOCK_ID = re.compile(rf"^{BLOCK_ID_PREFIX}?(\d+)$")
_ACTIONS = ("remove", "replace", "insert_after")
# How many times a batch's size it may grow while no heading comes before it is cut at the next block
_MOST_BATCH_SHARE = 2


def is_heading(text: str) -> bool:
    return bool(_HEADING.match(text))


def list_blocks(markdown: str) -> list[dict[str, Any]]:
    """The blocks of `markdown`, numbered from 1: {"id": N, "text": ...}."""

    texts: list[str] = []
    run: list[str] = []
    is_fenced = False
    for line in markdown.split("\n"):
        if line.strip().startswith(_FENCE):
            if not is_fenced and run:
                texts.append("\n".join(run))
                run = []
            run.append(line)
            is_fenced = not is_fenced
            if not is_fenced:
                texts.append("\n".join(run))
                run = []
            continue
        if is_fenced:
            run.append(line)
            continue
        if not line.strip() or _HEADING.match(line):
            if run:
                texts.append("\n".join(run))
                run = []
            if line.strip():
                texts.append(line.rstrip())
            continue
        run.append(line)
    if run:
        texts.append("\n".join(run))

    return [{"id": number, "text": text} for number, text in enumerate(texts, start=1)]


def render_blocks(blocks: list[dict[str, Any]]) -> str:
    """Each block under its `{B12}` line, the form the worker reads and names blocks by."""

    return "\n\n".join(f"{{{BLOCK_ID_PREFIX}{block['id']}}}\n{block['text']}" for block in blocks)


def join_blocks(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(block["text"] for block in blocks if block["text"].strip()) + "\n"


def split_batches(blocks: list[dict[str, Any]],
                  batch_chars: int) -> list[list[int]]:
    """The block ids of each batch, in order: a batch holds at least `batch_chars` characters and ends right before the
    next heading after that, so a section stays in one batch, or at the next block once it holds `_MOST_BATCH_SHARE`
    times `batch_chars` with no heading come. The last batch holds what is left."""

    batches: list[list[int]] = [[]]
    size = 0
    for block in blocks:
        # a long stretch with no heading is cut anyway: a part of 1,221 blocks, about 265,000 tokens with no heading
        # where a cut could fall, was sent whole to a model loaded with 183,040, and the run ended there
        is_too_long = size >= batch_chars * _MOST_BATCH_SHARE
        if size >= batch_chars and (is_heading(block["text"]) or is_too_long):
            batches.append([])
            size = 0
        batches[-1].append(block["id"])
        size += len(block["text"])

    return [batch for batch in batches if batch]


def read_block_id(name: Any) -> int | None:
    """The number of a block named "B12", "{B12}" or "12"; None for anything else."""

    match = _BLOCK_ID.match(str(name).strip().strip("{}"))

    return int(match.group(1)) if match else None


def list_figure_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if _FIGURE_LINE.match(line.strip())]


def _check_edit(edit: dict[str, Any],
                index: dict[int, int],
                batch: set[int]) -> tuple[str, int, int, str] | str:
    """(action, first, last, text) of one edit, or the reason it is refused."""

    action = str(edit.get("action", "")).strip()
    if action not in _ACTIONS:
        return f"action {action!r} is none of {', '.join(_ACTIONS)}"
    first = read_block_id(edit.get("block"))
    last = read_block_id(edit.get("last_block", edit.get("block")))
    if first is None or last is None:
        return f"block {edit.get('block')!r} names no block; write it as B12"
    for number in (first, last):
        if number not in batch:
            return f"{BLOCK_ID_PREFIX}{number} is not a block of this batch"
        if number not in index:
            return f"{BLOCK_ID_PREFIX}{number} was already removed or merged"
    if index[last] < index[first]:
        return f"last_block {BLOCK_ID_PREFIX}{last} comes before block {BLOCK_ID_PREFIX}{first}"
    if action == "insert_after" and first != last:
        return "insert_after takes one block, no last_block"
    text = mermaid_text_utils.repair_fenced_blocks(str(edit.get("text") or "").strip())
    if action in ("replace", "insert_after") and not text:
        return f"{action} needs the text; to take a block out, use remove"
    problems = mermaid_text_utils.list_fenced_problems(text)
    if problems:
        return f"a ```mermaid block Mermaid cannot draw: {'; '.join(problems)}"

    return action, first, last, text


def apply_edits(blocks: list[dict[str, Any]],
                batch_ids: list[int],
                edits: list[dict[str, Any]],
                next_id: int) -> tuple[list[dict[str, Any]], list[str], int]:
    """`blocks` with `edits` applied in order, the reasons of the edits refused (each refused edit changes nothing), and
    the next free block id. `replace` puts new text in place of one block, or of every block from `block` to
    `last_block`, under the first one's id; it may not drop a figure line those blocks hold (remove the block for that).
    `remove` takes a block, or a range, out. `insert_after` adds a new block after one."""

    current = [dict(block) for block in blocks]
    refused: list[str] = []
    batch = set(batch_ids)
    for number, edit in enumerate(edits, start=1):
        index = {block["id"]: place for place, block in enumerate(current)}
        checked = _check_edit(edit if isinstance(edit, dict) else {}, index, batch)
        if isinstance(checked, str):
            refused.append(f"edit {number}: {checked}")
            continue
        action, first, last, text = checked
        start, end = index[first], index[last] + 1
        if action == "remove":
            del current[start:end]
        elif action == "insert_after":
            current.insert(start + 1, {"id": next_id, "text": text})
            batch.add(next_id)
            next_id += 1
        else:
            lost = [line for block in current[start:end] for line in list_figure_lines(block["text"])
                    if line not in text]
            if lost:
                refused.append(f"edit {number}: the new text drops the figure line {lost[0]!r}; keep it, or remove "
                               "that block on its own")
                continue
            current[start:end] = [{"id": first, "text": text}]

    return current, refused, next_id
