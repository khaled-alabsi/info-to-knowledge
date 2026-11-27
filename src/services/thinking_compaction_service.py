"""Phase 3's compaction: a batch follow-up's reply cut at its output limit while still thinking is condensed, by one more
call with the step's own prompt, into what that thinking had settled, and the next attempt opens with it.

A follow-up of phase 3 is one call, not a conversation, so nothing grows between its calls; only one reply's own
thinking can outgrow its room. Thrown away, that thinking is ten minutes or more of checking the next attempt does again
(phase 3 is compacted too, with no limit of its own).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from chains import chain_runner
from models.chain_call_spec import ChainCallSpec
from prompts import compaction_prompt_loader
from services import chain_dispatch_service
from services import chat_model_service
from utils import message_content_utils

__all__ = ["list_found_frames", "read_found_mistakes", "keep_summary_rules", "COMPACTION_FILE", "ThinkingCompaction", "bind_summary_call", "is_compacted", "is_settled",
           "is_nothing_found", "summarise_cut_thinking", "write_compaction"]

COMPACTION_FILE = "compaction.md"
# The summary's own room: the sections of one batch, never a second copy of the thinking
SUMMARY_ALLOWANCE_TOKENS = 1_500
# A cut reply's thinking can be most of the window; the summary call is handed its last part, where what was settled
# last stands, and is told the start was left out
MAX_THINKING_CHARS = 240_000
# The summary's last section, and the word it holds when every frame was checked
_NOT_CHECKED_YET = re.compile(r"^##\s*NOT CHECKED YET\s*$(?P<body>.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_NOTHING_LEFT = re.compile(r"^\W*none\W*$", re.IGNORECASE)
# A critique summary's first section, and the word it opens with when the thinking found no mistake
_MISTAKES_FOUND = re.compile(r"^##\s*MISTAKES FOUND\s*$(?P<body>.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_NONE_FOUND = re.compile(r"^\W*none\b", re.IGNORECASE)


def is_nothing_found(job_kind: str,
                     summary: str) -> bool:
    """Whether a critique's summary says its thinking found no mistake: its MISTAKES FOUND section opens with "None"."""

    section = _MISTAKES_FOUND.search(summary or "") if job_kind == "critique" else None

    return section is not None and bool(_NONE_FOUND.match(section.group("body").strip()))


# A frame number the summary's MISTAKES FOUND names ("Frame 97", "frames 3 and 4", "frame 12-14"), also as a list with
# a colon and a note after each number ("Frames: 4 (Fact [F4.3]), 7 (Facts [F7.1])"): read only in that shape, a
# 38c3 talk's summary named no frame, every rule of its finished critique was set aside, and three mistakes it had
# found were written "Nothing to criticise" (DEFECTS 476)
_FRAME_NOTE = r"(?:\s*\([^)]*\))?"
_SUMMARY_FRAME = re.compile(rf"\bframes?\s*:?\s*\d+{_FRAME_NOTE}(?:\s*(?:-|–|to|and|,)\s*\d+{_FRAME_NOTE})*",
                            re.IGNORECASE)
# A note's own id names its frame too, "F0.3" is frame 0: a summary named its mistakes by the notes' ids alone, and
# three mistakes were written "Nothing to criticise" (DEFECTS 507)
_NOTE_ID_FRAME = re.compile(r"\b[FTQO](\d+)\.\d+\b")


def read_found_mistakes(summary: str) -> str:
    """The text of the summary's MISTAKES FOUND section; empty when it has none or opens with "None"."""

    section = _MISTAKES_FOUND.search(summary or "")
    body = section.group("body").strip() if section else ""

    return "" if not body or _NONE_FOUND.match(body) else body


def _list_frames_in(text: str) -> set[int]:
    """The frames a piece of a summary names, by "frame 4", "frames: 4, 7" or a note's id."""

    named = {int(number) for match in _SUMMARY_FRAME.finditer(text)
             for number in re.findall(r"\d+", re.sub(r"\([^)]*\)", "", match.group(0)))}

    return named | {int(number) for number in _NOTE_ID_FRAME.findall(text)}


def _has_unplaced_mistake(summary: str) -> bool:
    """Whether a mistake of the summary names no frame the program reads: each block when the blocks end on a
    "Frames:" line as asked, else each line (a summary wrote one mistake a line, one of three naming a note's id)."""

    body = read_found_mistakes(summary)
    is_block_form = re.search(r"^\s*frames?\s*:", body, re.IGNORECASE | re.MULTILINE) is not None
    units = re.split(r"\n\s*\n", body) if is_block_form else body.splitlines()

    return any(unit.strip() and not _list_frames_in(unit) for unit in units)


def list_found_frames(summary: str) -> set[int]:
    """The frames the summary's MISTAKES FOUND section names; empty when it has none."""

    section = _MISTAKES_FOUND.search(summary or "")

    return set() if section is None else _list_frames_in(section.group("body"))


def keep_summary_rules(parsed: dict,
                       summary: str) -> dict:
    """A critique written from its summary with only the rules seen on frames the summary's mistakes name: written with
    no thinking, the answer walked the instructions' list of common mistakes and added rules the summary did not hold,
    two of them naming no frame, and the batch blocked (DEFECTS 39, again on a 39c3 talk's frames 90 to 99). A summary
    that names mistakes in no frame the program can read sets no rule aside: read by guess, a summary's way of naming
    its frames lost found mistakes three times (DEFECTS 437, 476, 507), which is worse than an extra rule."""

    found = list_found_frames(summary)
    is_unplaced = _has_unplaced_mistake(summary)
    rules, seen = [], []
    for rule in parsed.get("rules") or []:
        frames = {int(number) for number in rule.get("frames") or []}
        # The same mistake written again, re-evaluated under another name, is one rule: the first stands
        if frames and (is_unplaced or frames <= found) and frames not in seen:
            rules.append(rule)
            seen.append(frames)

    return {**parsed, "rules": rules}


@dataclass
class ThinkingCompaction:
    summary: str
    thinking_chars: int
    is_trimmed: bool
    duration_s: float


def is_compacted(job_kind: str,
                 is_cut: bool,
                 thinking: str) -> bool:
    """Whether a reply is compacted: a phase 3 follow-up, cut at its limit, with thinking to condense."""

    return is_cut and job_kind in compaction_prompt_loader.CUT_THINKING_STEPS and bool(thinking.strip())


def is_settled(summary: str) -> bool:
    """Whether a summary says its thinking had checked everything: its NOT CHECKED YET section holds only "None"."""

    section = _NOT_CHECKED_YET.search(summary or "")

    return section is not None and bool(_NOTHING_LEFT.match(section.group("body").strip()))


def bind_summary_call(chat_model: Any,
                      room: int | None) -> Any:
    """The summary call with no thinking of its own (`reasoning_effort: "none"`, which LM Studio honours; a template
    switch and "/no_think" were ignored) and at most the window's room: handed a looping critique's thinking, a summary
    call that thought carried on the critique itself and looped the same way, 704 s and no word."""

    return chat_model_service.bind_no_thinking(chat_model, **({"max_tokens": room} if room is not None else {}))


def summarise_cut_thinking(model: BaseChatModel | None,
                           spec: ChainCallSpec,
                           thinking: str) -> ThinkingCompaction:
    """What the cut reply's thinking had settled, in the sections the step's prompt names (`spec.job_kind`'s). `model` is
    injected by tests; None means `spec.phase`'s own model. Streamed like the follow-up it condenses, its thinking
    written to `spec.live_path` and ended if it loops: a summary of a looping thinking ran past ten minutes unseen."""

    is_trimmed = len(thinking) > MAX_THINKING_CHARS
    kept = ("(the start of the thinking is left out)\n" + thinking[-MAX_THINKING_CHARS:]) if is_trimmed else thinking
    prompt = compaction_prompt_loader.read_cut_thinking_prompt(spec.job_kind).format(allowance=SUMMARY_ALLOWANCE_TOKENS,
                                                                               thinking=kept)
    messages = [HumanMessage(content=prompt)]
    chat_model = model if model is not None else chat_model_service.get_model(spec.phase)
    if model is None:
        chat_model = bind_summary_call(chat_model,
                                       chain_dispatch_service.compute_window_room(messages, spec.window_tokens))
    started = time.monotonic()
    reply = chain_runner.read_streamed_reply(chat_model, messages, spec.live_path)

    return ThinkingCompaction(summary=message_content_utils.read_message_text(reply.content).strip(),
                              thinking_chars=len(thinking), is_trimmed=is_trimmed,
                              duration_s=time.monotonic() - started)


def write_compaction(directory: Path,
                     name: str,
                     output_tokens: int | None,
                     compaction: ThinkingCompaction,
                     is_loop: bool = False) -> Path:
    """The compaction as its own file in the attempt's log folder: what was cut, and the summary the next attempt gets."""

    path = directory / COMPACTION_FILE
    if is_loop:
        reason = "Stopped because its thinking repeated the same passage"
    else:
        tokens = output_tokens if output_tokens is not None else "an unknown number of"
        reason = f"Cut at the output limit after {tokens} tokens"
    trimmed_note = " (its last part summarised)" if compaction.is_trimmed else ""
    path.write_text(f"# Compaction of {name}\n\n"
                    f"- {reason}, with {compaction.thinking_chars:,} characters of thinking{trimmed_note}.\n"
                    f"- Summarised in {compaction.duration_s:.0f} s; the next attempt opens with the summary.\n\n"
                    f"## Summary\n\n{compaction.summary or '(the summary call returned no text)'}\n", encoding="utf-8")

    return path
