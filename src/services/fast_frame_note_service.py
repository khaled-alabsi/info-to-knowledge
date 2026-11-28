"""Phase 3 of a fast-processing video run: each frame described from its image alone.

One session of the frame model (`video.fastProcessing.frameModel`) takes `frameFollowUps` frames: the instruction once,
then each frame as a follow-up message holding its number, a one-line ask and its image and nothing else; the worker answers each with
`submit_note(description)`, which the program appends to `03_frame-descriptions.md` with the frame's time and the
speech said while it was on screen (which the worker never sees: the document writer does). A reply with no call keeps
its text. Nothing is checked, retried or repaired; after the session's last frame the next session starts fresh. A
reply cut at `frameMaxTokens` keeps its text, and the session goes on without it, so the next frame does not follow a
looped reply.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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
from services import fast_turn_service
from services import performance_log_service
from services import record_file_service
from services import worker_message_service
from utils import chain_response_utils
from utils import clock_utils
from utils import frame_message_utils
from utils import frame_slug_utils
from utils import message_content_utils

__all__ = ["PHASE", "PROMPT_PHASE", "SUBMIT_NOTE_TOOL", "set_model_factory", "build_descriptions_path",
           "start_descriptions", "list_described_frames", "describe_session"]

PHASE = "frames_processing"
# The instruction's entry in `agent-context/context_map.json`
PROMPT_PHASE = "fast_frame_note"
SUBMIT_NOTE_TOOL = {"type": "function", "function": {
    "name": "submit_note",
    "description": "Submit your notes on the frame you were just given; the next frame follows.",
    "parameters": {"type": "object", "required": ["description"], "properties": {"description": {
        "type": "string", "description": "every piece of knowledge on the frame, one per line, in markdown"}}}}}
_NO_DESCRIPTION = "(no description)"
# The ask each frame's message repeats: a 4B model, thinking nothing, followed its last message over the instruction
# above it and described a session's first frame by one popup or one list row (DEFECTS 77, 79)
_FRAME_ASK = ("Describe this frame whole: every part of its screen, an opened item first with every field and every "
              "line of its description, then every row of the list; a popup or a notice is one line. Then call "
              "submit_note.")
_SECTION_HEADING = re.compile(r"^## Frame (\d+) ·", re.MULTILINE)

MODEL_FACTORY: Callable[[str], Any] = chat_model_service.get_named_model


def set_model_factory(factory: Callable[[str], Any]) -> Callable[[str], Any]:
    """The test seam: a suite never needs a model host. Returns the factory it replaced."""

    global MODEL_FACTORY
    previous, MODEL_FACTORY = MODEL_FACTORY, factory

    return previous


def build_descriptions_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.FRAME_DESCRIPTIONS_FILE


def start_descriptions(paths: RunPaths) -> None:
    """An empty descriptions file, for a pass over every frame: no description of an earlier pass is kept."""

    build_descriptions_path(paths).write_text("# Frame descriptions\n", encoding="utf-8")


def list_described_frames(paths: RunPaths) -> set[int]:
    """The numbers of the frames the descriptions file holds a section for."""

    path = build_descriptions_path(paths)

    return {int(number) for number in _SECTION_HEADING.findall(path.read_text(encoding="utf-8"))} if path.is_file() else set()


def _read_frame_number(item: dict) -> int:
    return frame_slug_utils.read_frame_number(str(item["slug"]))


def _build_frame_message(paths: RunPaths,
                         item: dict,
                         width: int) -> HumanMessage:
    """The frame's number, the ask and its image, nothing else: the worker describes it from what it sees."""

    image = frame_message_utils.encode_frame(paths.output_root / item["path"], min_width=width, max_width=width)

    return HumanMessage(content=[{"type": "text", "text": f"Frame {_read_frame_number(item)}: {_FRAME_ASK}"}, image])


def _read_note(reply: AIMessage) -> tuple[str, str]:
    """The description the reply submitted and how: `tool` from `submit_note`, `text` from a reply that made no call."""

    for call in reply.tool_calls or []:
        if call.get("name") == "submit_note" and str((call.get("args") or {}).get("description") or "").strip():
            return str(call["args"]["description"]).strip(), "tool"
    text = chain_response_utils.render_message_as_text(reply).strip() if isinstance(reply.content, (str, list)) else ""

    return text or _NO_DESCRIPTION, "text"


def _render_section(item: dict,
                    description: str) -> str:
    """One frame's section: its number and time, what was said while it was on screen, and its description."""

    said = str(item.get("transcriptCurrent") or "").strip() or "(nothing is said)"

    return (f"\n## Frame {_read_frame_number(item)} · {clock_utils.render_clock(float(item.get('timestamp') or 0))}\n\n"
            f"Said while it is on screen: {said}\n\n{description}\n")


def _log_call(ctx: RunContext,
              session_slug: str,
              item: dict,
              note: str) -> None:
    """The call on disk as every call is: its log under `calls/`; its row in the run's call record is written by
    `chain_runner` as the call returns."""

    name = f"{output_contract.FAST_NOTE_SLUG_PREFIX}{_read_frame_number(item):03d}"
    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, name), {
        "phase": PHASE, "slug": session_slug, "attempt": 1, "model": config.settings.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE),
        "request": {"frame": item["slug"], "image": item["path"]}, "response": {"description": note}})


def _build_turn(reply: AIMessage,
                frame_number: int,
                call: int) -> dict:
    """The call as a turn of the worker's panel: its thinking, its words, and each tool call with what the program
    answered."""

    steps = [{"call": call, "tool": str(tool_call.get("name") or ""), "summary": f"frame {frame_number}",
              "args": json.dumps(tool_call.get("args") or {}, ensure_ascii=False), "result": "Noted."}
             for tool_call in reply.tool_calls or []]

    return {"call": call, "frame": frame_number, "thinking": message_content_utils.read_reasoning(reply),
            "text": message_content_utils.read_message_text(reply), "steps": steps}


def describe_session(ctx: RunContext,
                     items: list[dict]) -> list[str]:
    """One session over `items`, in order: each frame a follow-up, its description appended as it comes, its item
    written. The slugs written."""

    # STEP 1: the session's model, with its one tool, thinking as the settings say
    settings = config.settings.video.fast_processing
    model = MODEL_FACTORY(config.settings.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE)).bind_tools([SUBMIT_NOTE_TOOL]).bind(max_tokens=settings.frame_max_tokens)
    if not settings.is_frame_thinking_on:
        model = chat_model_service.bind_no_thinking(model)
    slugs = [item["slug"] for item in items]
    session_slug = f"fast-notes-{_read_frame_number(items[0]):03d}-{_read_frame_number(items[-1]):03d}"
    handles = performance_log_service.start_unit(ctx, PHASE, session_slug, slugs)
    call_record_service.update_place(windowTokens=ctx.phase_windows.get(PHASE))
    messages: list[Any] = [SystemMessage(content=system_prompt_builder.build_system_prompt(PROMPT_PHASE))]
    fast_turn_service.start_turns(ctx.paths.output_root, session_slug)
    messages_path = worker_message_service.build_phase_messages_path(ctx.paths.output_root, PHASE)

    # STEP 2: each frame a follow-up of the session; its note appended the moment it comes, so a kill loses no frame
    # that was described. What the person wrote to the worker comes before the next frame
    for call, item in enumerate(items):
        conversation_message_service.add_waiting_to_conversation(messages_path, messages)
        messages.append(_build_frame_message(ctx.paths, item, settings.frame_width))
        started = time.monotonic()
        reply = chain_runner.invoke_reply(model=model,
                                          messages=messages)
        seconds = time.monotonic() - started
        note, channel = _read_note(reply)
        section = _render_section(item, note)
        with build_descriptions_path(ctx.paths).open("a", encoding="utf-8") as descriptions:
            descriptions.write(section)
        # the frame's own file too, as a quality run's notes are, so the page's Frame notes and a frame's "Read its
        # notes" open it (the owner: "the artifacts show no output for each frame as it used to")
        record_path = record_file_service.build_record_path(ctx.paths, item["slug"])
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(section.lstrip("\n").replace("## Frame", "# Frame", 1), encoding="utf-8")
        _log_call(ctx=ctx,
                  session_slug=session_slug,
                  item=item,
                  note=note)
        fast_turn_service.append_turn(output_root=ctx.paths.output_root,
                                      slug=session_slug,
                                      turn=_build_turn(reply=reply,
                                                       frame_number=_read_frame_number(item),
                                                       call=call))
        is_cut = (reply.response_metadata or {}).get("finish_reason") == "length"
        if is_cut:
            messages.pop()
        else:
            messages.append(reply)
            for call in reply.tool_calls or []:
                messages.append(ToolMessage(content="Noted.", tool_call_id=call.get("id") or "submit_note"))
        ctx.store.set_item_status(item["slug"], "written", attempts=1)
        kept = "" if channel == "tool" else ", no submit_note: its text kept"
        kept += f", cut at {settings.frame_max_tokens:,} tokens and left out of the session" if is_cut else ""
        ctx.log.info(f"  frame {_read_frame_number(item)} described: {len(note.split())} words in {seconds:.1f} s{kept}")

    # STEP 3: the session's line, as a batch's
    performance_log_service.record_unit(ctx, handles, "batch", "written", slugs)

    return slugs
