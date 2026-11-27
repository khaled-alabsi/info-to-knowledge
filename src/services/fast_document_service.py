"""Phase 4 of a fast-processing video run: ONE call of the document model
(`video.fastProcessing.documentModel`) over `03_frame-descriptions.md`, every frame's description with the speech said
while it was on screen, writing the reference document. Nothing is checked (the owner: no validation). The answer is
kept in `04_writing/fast-document.md`, a resume reuses it, and the document and the published folder are written from
it as a takeaway's are.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

import config
from chains import chain_runner
from models import ingesting_profile_vocabulary
from models import output_contract
from models.pipeline_errors import DescriptionsDoNotFit
from models.run_context import RunContext
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from services import call_log_service
from services import call_record_service
from services import chat_model_service
from services import document_folder_service
from services import fast_frame_note_service
from services import fast_turn_service
from services import ingesting_profile_service
from services import instruction_store_service
from services import run_tags_service
from utils import chain_response_utils
from utils import document_text_utils
from utils import json_store_utils
from utils import message_content_utils

__all__ = ["PHASE", "PROMPT_PHASE", "ANSWER_FILE", "set_model_factory", "build_answer_path", "build_messages",
           "count_prompt_tokens", "compute_answer_room", "build_request_path", "render_request", "write_document"]

PHASE = "finalize"
PROMPT_PHASE = "fast_document"
ANSWER_FILE = "fast-document.md"
# Characters a token holds in this mix of German speech, English notes and markdown, for the size check before the call:
# Daily's 545,612 characters were 167,744 tokens (3.25), where 3.5 had estimated 155,889
_CHARS_PER_TOKEN = 3.2
# Tokens kept free between the prompt and the answer, against the estimate's error; the phase 3 follow-ups' margin
_ROOM_MARGIN_TOKENS = 4_000
# The least room a document can be written in; a window with less left after the descriptions stops the phase
_MIN_ANSWER_TOKENS = 8_192

MODEL_FACTORY: Callable[[str], Any] = chat_model_service.get_named_model


def set_model_factory(factory: Callable[[str], Any]) -> Callable[[str], Any]:
    """The test seam: a suite never needs a model host. Returns the factory it replaced."""

    global MODEL_FACTORY
    previous, MODEL_FACTORY = MODEL_FACTORY, factory

    return previous


def build_answer_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.WRITING_DIR_NAME / ANSWER_FILE


def _read_profile_text(folder: Path) -> str:
    """The ingesting profile's own section, which shapes the document as it does on the quality workflow; none for
    `knowledge`, the shape the instruction itself describes."""

    profile = ingesting_profile_service.resolve_profile(folder)
    file_id = f"base/ingesting-profile-{profile}.md"
    if profile == ingesting_profile_vocabulary.DEFAULT_PROFILE or not instruction_store_service.is_editable(file_id):
        return ""

    return "\n\n" + instruction_store_service.read_active(file_id)


def build_messages(paths: RunPaths,
                   recording: str) -> list[BaseMessage]:
    """The instruction, with the run's ingesting profile; then the recording's name, its tags and every description."""

    descriptions = fast_frame_note_service.build_descriptions_path(paths).read_text(encoding="utf-8")
    tags = run_tags_service.read_tags(paths.output_root)
    tags_line = f"\nThe owner tagged it: {', '.join(tags)}. Read the tags as a hint of what the recording is.\n" if tags else ""
    human = (f"## Write the document of this recording\n\nThe recording is {recording}.{tags_line}\n\n{descriptions}\n\n"
             f"Answer with the document in markdown and nothing else.")
    system = system_prompt_builder.build_system_prompt(PROMPT_PHASE) + _read_profile_text(paths.output_root)

    return [SystemMessage(content=system), HumanMessage(content=[{"type": "text", "text": human}])]


def build_request_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.WRITING_DIR_NAME / "requests" / f"{output_contract.FAST_DOCUMENT_SLUG}.md"


def render_request(system_text: str,
                   human_text: str) -> str:
    """The whole request as a person reads it: the instructions the writer was given, then its message, the frame
    descriptions in the meeting's order, each with what was said while the frame was on screen."""

    return (f"# Request sent to the document writer\n\n## Instructions\n\n{system_text.strip()}\n\n"
            f"## Message\n\n{human_text.strip()}\n")


def count_prompt_tokens(messages: list[BaseMessage]) -> int:
    """About how many tokens the call's prompt holds, from its characters."""

    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)

    return int((len(system_text) + len(human_text)) / _CHARS_PER_TOKEN)


def compute_answer_room(prompt_tokens: int,
                        window: int,
                        max_tokens: int | None) -> int:
    """The answer's output limit: the room the window has left after the prompt and a margin, never above the model's
    own limit. A follow-up of phase 3 is sized the same way: a fixed limit stopped Daily's 201 descriptions (about
    155,900 tokens) on a 183,040 window, where 27,000 were free."""

    room = window - prompt_tokens - _ROOM_MARGIN_TOKENS

    return min(room, max_tokens) if max_tokens else room


def _publish(paths: RunPaths,
             answer: str) -> Path:
    """`04_document.md` and the published folder from the answer, every web address's secret hidden; the published
    document's path."""

    text = document_text_utils.hide_secret_query_values(answer.strip() + "\n")
    paths.summary.write_text(text, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(document_folder_service.render_published_front_matter(paths) + text,
                        encoding="utf-8")
    json_store_utils.write_json(document.parent / output_contract.DOCUMENT_PROVENANCE_FILE, {
        "schemaVersion": 1, "document": document.name, "images": [], "leftOut": [], "removedUnreferenced": [],
        "source": output_contract.FRAME_DESCRIPTIONS_FILE})

    return document


def write_document(ctx: RunContext,
                   recording: str) -> Path:
    """The one call, or the answer a resume finds kept, then the document and the published folder; the published
    document's path. Raises `ValueError` naming the sizes when the descriptions do not fit the model's window."""

    # STEP 1: an answer a killed run already got is kept: the call is the phase's whole cost
    answer_path = build_answer_path(ctx.paths)
    if answer_path.is_file() and answer_path.read_text(encoding="utf-8").strip():
        ctx.log.info(f"phase 4: the document's call ran before; publishing {answer_path.name} again")

        return _publish(ctx.paths, answer_path.read_text(encoding="utf-8"))

    # STEP 2: the call fits its window, or the phase stops naming the sizes
    settings = config.settings.video.fast_processing
    messages = build_messages(ctx.paths, recording)
    prompt_tokens = count_prompt_tokens(messages)
    model_settings = config.settings.resolve_model(settings.document_model)
    window = ctx.phase_windows.get(PHASE) or model_settings.num_ctx or 0
    room = compute_answer_room(prompt_tokens, window, model_settings.max_tokens) if window else None
    if room is not None and room < _MIN_ANSWER_TOKENS:
        raise DescriptionsDoNotFit(f"the descriptions take about {prompt_tokens:,} tokens, which leaves {max(room, 0):,} for the "
                         f"answer in {settings.document_model}'s window of {window:,}, under {_MIN_ANSWER_TOKENS:,}")

    # STEP 3: the request on disk before the call, so it can be read while the call runs
    request_path = build_request_path(ctx.paths)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(render_request(*chain_response_utils.render_prompt_as_text(messages)), encoding="utf-8")

    # STEP 4: the one call, thinking as the settings say, writing up to the room left; its answer kept before anything
    # is built from it
    model = MODEL_FACTORY(settings.document_model)
    if room is not None:
        model = model.bind(max_tokens=room)
    if not settings.is_document_thinking_on:
        model = chat_model_service.bind_no_thinking(model)
    ctx.log.info(f"phase 4: a fast-processing run, one call of {settings.document_model} over "
                 f"{len(fast_frame_note_service.list_described_frames(ctx.paths))} frame descriptions, "
                 f"about {prompt_tokens:,} tokens, the answer up to {room or 0:,}")
    started = time.monotonic()
    call_record_service.update_place(windowTokens=window or None)
    reply = chain_runner.invoke_reply(model=model,
                                      messages=messages)
    seconds = time.monotonic() - started
    answer = chain_response_utils.render_message_as_text(reply).strip()
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text(answer + "\n", encoding="utf-8")
    fast_turn_service.start_turns(ctx.paths.output_root, output_contract.FAST_DOCUMENT_SLUG)
    fast_turn_service.append_turn(output_root=ctx.paths.output_root,
                                  slug=output_contract.FAST_DOCUMENT_SLUG,
                                  turn={"call": 0, "thinking": message_content_utils.read_reasoning(reply),
                                        "text": answer, "steps": []})

    # STEP 5: the call on disk as every call is
    usage = chain_response_utils.read_token_usage(reply)
    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, output_contract.FAST_DOCUMENT_SLUG), {
        "phase": PHASE, "slug": output_contract.FAST_DOCUMENT_SLUG, "attempt": 1, "model": settings.document_model,
        "request": {"system": system_text, "human": human_text}, "response": {"content": answer}})
    ctx.log.info(f"phase 4: the document written in {seconds:.0f} s, {usage['output_tokens'] or 0:,} tokens out")

    return _publish(ctx.paths, answer)
