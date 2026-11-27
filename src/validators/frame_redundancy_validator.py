"""The redundancy filter's reply for one call, checked before anything is removed: only frames of the call, one
decision per asked frame, a removal names a frame of the call that holds it, a replacement names only earlier frames,
and every removal gives a reason. Whatever breaks a rule, or is missing, becomes KEEP with the rule in `kept_reason`:
when in doubt a frame stays."""

from __future__ import annotations

import json
import re
from typing import Any

from models.frame_redundancy_outcome import DECISIONS, DISCARD, KEEP, FrameRedundancyOutcome

__all__ = ["read_json_object", "check_reply"]

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# LFM2.5-VL-3B's two common bracket slips, measured in the POC: an extra brace before the list ends, and the list
# closed after its object. Each is tried after the reply as written.
_BRACKET_REPAIRS = (("}}]", "}]"), ("}}]", "}]}"))


def _parse_object(text: str) -> dict | None:
    # A bare list of decisions is the decisions: qwen3.5-4b wrote it so on 42 of 46 calls of a Lecture 9 run, each read
    # as unreadable and every frame kept
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            listed = json.loads(stripped[:stripped.rfind("]") + 1])
        except json.JSONDecodeError:
            listed = None
        if isinstance(listed, list):
            return {"decisions": listed}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        # not JSON as written: the caller tries the next repair, then keeps every frame of the call
        return None

    return parsed if isinstance(parsed, dict) else None


def read_json_object(text: str) -> dict | None:
    """The reply's JSON object: inside a ```json fence when there is one, as written or with a known bracket slip
    repaired; None when no form of it parses."""

    fenced = FENCE.search(text)
    body = fenced.group(1) if fenced else text
    for candidate in (body, *(body.replace(old, new) for old, new in _BRACKET_REPAIRS)):
        parsed = _parse_object(candidate)
        if parsed is not None:
            return parsed

    return None


def _list_ids(written: Any) -> list[str]:
    """Frame ids from what the model wrote: one id as a string, a list of ids, or nothing."""

    if isinstance(written, str):
        return [written]
    if isinstance(written, list):
        return [item for item in written if isinstance(item, str)]

    return []


def _check_entry(entry: dict,
                 frame_id: str,
                 call_ids: list[str],
                 is_reason_required: bool) -> FrameRedundancyOutcome:
    """One asked frame's entry checked: an invalid removal or replacement becomes KEEP with the rule it broke."""

    decision = str(entry.get("decision", "")).strip().upper()
    reason = str(entry.get("reason") or "").strip()
    if decision not in DECISIONS:
        return FrameRedundancyOutcome(frame_id, KEEP, reason,
                                       kept_reason=f"unknown decision {decision!r}")
    if decision == KEEP:
        return FrameRedundancyOutcome(frame_id, decision, reason)
    if not reason and is_reason_required:
        return FrameRedundancyOutcome(frame_id, KEEP, kept_reason=f"{decision} without a reason")

    if decision == DISCARD:
        covered_by = tuple(i for i in _list_ids(entry.get("covered_by")) if i in call_ids and i != frame_id)
        if not covered_by:
            return FrameRedundancyOutcome(frame_id, KEEP, reason,
                                           kept_reason="DISCARD without a covering frame of this call")

        return FrameRedundancyOutcome(frame_id, decision, reason, covered_by=covered_by)

    position = call_ids.index(frame_id)
    replaces = tuple(i for i in _list_ids(entry.get("replaces")) if i in call_ids[:position])
    if not replaces:
        return FrameRedundancyOutcome(frame_id, KEEP, reason,
                                       kept_reason="REPLACE_PREVIOUS without an earlier frame of this call")

    return FrameRedundancyOutcome(frame_id, decision, reason, replaces=replaces)


def check_reply(text: str,
                call_ids: list[str],
                is_reason_required: bool = True) -> tuple[list[FrameRedundancyOutcome], bool]:
    """One checked decision per frame of the call, in its order, and whether the reply could be read at all. Without
    `is_reason_required` a removal with no reason stands (the program's word check guards it instead: the models
    gave no reason on most of Lecture 9's removals)."""

    parsed = read_json_object(text)
    if parsed is None or not isinstance(parsed.get("decisions"), list):
        return [FrameRedundancyOutcome(i, KEEP, kept_reason="unreadable reply")
                for i in call_ids], False

    entries_by_id: dict[str, list[dict]] = {}
    for entry in parsed["decisions"]:
        if isinstance(entry, dict) and entry.get("frame_id") in call_ids:
            entries_by_id.setdefault(entry["frame_id"], []).append(entry)

    decisions = []
    for frame_id in call_ids:
        entries = entries_by_id.get(frame_id, [])
        if not entries:
            decisions.append(FrameRedundancyOutcome(frame_id, KEEP, kept_reason="no decision"))
        elif len(entries) > 1:
            decisions.append(FrameRedundancyOutcome(frame_id, KEEP,
                                                     kept_reason="two decisions"))
        else:
            decisions.append(_check_entry(entries[0], frame_id, call_ids, is_reason_required))

    return decisions, True
