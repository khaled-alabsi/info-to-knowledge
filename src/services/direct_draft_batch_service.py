"""Phase 3 of a direct-draft video run (the Fast speed's workflow): the frames go straight into the draft document.

One call of phase 3's model takes a batch of `framesProcessing.framesPerCall` frames (8 on the Fast speed), each as a line
with its number, time and the speech said while it was on screen, and its image. The call also gets the headings the
draft holds so far and the last section's text, and answers with the markdown that goes on from there: it continues the
last section or opens a new heading, so the draft is already in chapters. The program appends the answer to
`03_draft.md` under a marker line for the batch. Nothing is checked, retried or repaired, and no frame is described on
its own (the owner: no frame notes, no critique, no repair). Phase 4 reads the draft (`direct_draft_outline_service`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

import config
from chains import chain_runner
from models import output_contract
from models.run_context import RunContext
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from services import call_log_service
from services import call_record_service
from services import chat_model_service
from services import conversation_message_service
from services import fast_document_service
from services import fast_turn_service
from services import performance_log_service
from services import run_tags_service
from services import worker_message_service
from utils import chain_response_utils
from utils import clock_utils
from utils import draft_structure_utils
from utils import frame_message_utils
from utils import frame_slug_utils
from utils import message_content_utils

__all__ = ["PHASE", "PROMPT_PHASE", "set_model_factory", "build_draft_path", "start_draft", "count_written_batches",
           "build_batch_message", "write_batch"]

PHASE = "frames_processing"
# The instruction's entry in `agent-context/context_map.json`
PROMPT_PHASE = "direct_draft_batch"
_CALL_SLUG_PREFIX = "direct-draft-batch-"
_BATCH_MARKER = re.compile(r"^<!-- batch ", re.MULTILINE)
_NOTHING_YET = "(nothing yet: this is the first batch, so open the draft with a heading)"

MODEL_FACTORY: Callable[[str], Any] = chat_model_service.get_named_model


def set_model_factory(factory: Callable[[str], Any]) -> Callable[[str], Any]:
    """The test seam: a suite never needs a model host. Returns the factory it replaced."""

    global MODEL_FACTORY
    previous, MODEL_FACTORY = MODEL_FACTORY, factory

    return previous


def build_draft_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.DRAFT_FILE


def start_draft(paths: RunPaths) -> None:
    """A draft with nothing in it, for a pass over every frame: no text of an earlier pass is kept."""

    build_draft_path(paths).write_text(f"# Draft of {Path(paths.module).name}\n", encoding="utf-8")


def count_written_batches(paths: RunPaths) -> int:
    """How many batches the draft holds, by their marker lines."""

    path = build_draft_path(paths)

    return len(_BATCH_MARKER.findall(path.read_text(encoding="utf-8"))) if path.is_file() else 0


def _read_frame_number(item: dict) -> int:
    return frame_slug_utils.read_frame_number(str(item["slug"]))


def _build_frame_parts(paths: RunPaths,
                       item: dict,
                       width: int) -> list[dict[str, Any]]:
    """One frame as the call reads it: its slug and time, the speech said while it is on screen, then its image."""

    said = str(item.get("transcriptCurrent") or "").strip() or "(nothing is said)"
    clock = clock_utils.render_clock(float(item.get("timestamp") or 0))
    image = frame_message_utils.encode_frame(paths.output_root / item["path"], min_width=width, max_width=width)

    return [{"type": "text", "text": f"{item['slug']} · {clock}\nSaid while it is on screen: {said}"}, image]


def build_batch_message(paths: RunPaths,
                        items: list[dict]) -> HumanMessage:
    """The batch's message: the recording, the headings and last section written so far, then the batch's frames."""

    settings = config.settings.video.direct_draft
    path = build_draft_path(paths)
    draft = path.read_text(encoding="utf-8") if path.is_file() else ""
    headings = draft_structure_utils.render_headings(draft_structure_utils.parse_sections(draft))
    last_section = draft_structure_utils.read_last_section(draft, settings.last_section_chars)
    tags = run_tags_service.read_tags(paths.output_root)
    tags_line = f"\nThe owner tagged it: {', '.join(tags)}. Read the tags as a hint of what the recording is.\n" if tags else ""
    first, last = items[0]["slug"], items[-1]["slug"]
    lead = (f"## Continue the draft\n\nThe recording is {Path(paths.module).name}.{tags_line}\n"
            f"### Headings written so far\n\n{headings or _NOTHING_YET}\n\n"
            f"### The last section so far\n\n{last_section or _NOTHING_YET}\n\n"
            f"### The next frames, {first} to {last}\n\nWrite what these frames add to the draft.")
    content: list[dict[str, Any]] = [{"type": "text", "text": lead}]
    for item in items:
        content += _build_frame_parts(paths, item, settings.frame_width)

    return HumanMessage(content=content)


def _log_call(ctx: RunContext,
              slug: str,
              model_name: str,
              items: list[dict],
              text: str) -> None:
    """The call on disk as every call is: its log under `calls/`; its row in the run's call record is written by
    `chain_runner` as the call returns."""

    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, f"{_CALL_SLUG_PREFIX}{slug}"), {
        "phase": PHASE, "slug": slug, "attempt": 1, "model": model_name,
        "request": {"frames": [{"frame": item["slug"], "image": item["path"]} for item in items]},
        "response": {"content": text}})


def write_batch(ctx: RunContext,
                items: list[dict]) -> list[str]:
    """One call over `items`, in order: its answer appended to the draft under the batch's marker, the items written.
    The slugs written."""

    # STEP 1: the call's model, the one phase 3 has on the quality workflow, and what it is told
    model_name = config.settings.resolve_phase_model_name(PHASE)
    model = MODEL_FACTORY(model_name)
    slugs = [item["slug"] for item in items]
    first, last = _read_frame_number(items[0]), _read_frame_number(items[-1])
    session_slug = f"{fast_turn_service.SESSION_PREFIX}{first:03d}-{last:03d}"
    handles = performance_log_service.start_unit(ctx, PHASE, session_slug, slugs)
    call_record_service.update_place(windowTokens=ctx.phase_windows.get(PHASE))
    messages_path = worker_message_service.build_phase_messages_path(ctx.paths.output_root, PHASE)
    # the ingesting profile's own section shapes the draft as it shapes a quality run's document
    system = system_prompt_builder.build_system_prompt(PROMPT_PHASE) + fast_document_service.read_profile_text(
        ctx.paths.output_root)
    messages: list[Any] = [SystemMessage(content=system)]
    # what the person wrote to the worker comes before the batch
    conversation_message_service.add_waiting_to_conversation(messages_path, messages)
    messages.append(build_batch_message(ctx.paths, items))

    # STEP 2: the one call
    fast_turn_service.start_turns(ctx.paths.output_root, session_slug)
    started = time.monotonic()
    reply = chain_runner.invoke_reply(model=model,
                                      messages=messages)
    seconds = time.monotonic() - started
    text = chain_response_utils.render_message_as_text(reply).strip()

    # STEP 3: the answer on disk before the items are marked written, so a kill loses no batch that was answered
    marker = draft_structure_utils.render_batch_marker(count_written_batches(ctx.paths) + 1, first, last)
    with build_draft_path(ctx.paths).open("a", encoding="utf-8") as draft:
        draft.write(f"\n{marker}\n\n{text}\n")
    _log_call(ctx=ctx,
              slug=session_slug,
              model_name=model_name,
              items=items,
              text=text)
    fast_turn_service.append_turn(output_root=ctx.paths.output_root,
                                  slug=session_slug,
                                  turn={"call": 0, "frame": first, "thinking": message_content_utils.read_reasoning(reply),
                                        "text": text, "steps": []})
    for item in items:
        ctx.store.set_item_status(item["slug"], "written", attempts=1)
    ctx.log.info(f"  frames {first}-{last} written into the draft: {len(text.split())} words in {seconds:.1f} s")

    # STEP 4: the batch's line in the performance log
    performance_log_service.record_unit(ctx, handles, "batch", "written", slugs)

    return slugs
