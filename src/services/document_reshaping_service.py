"""Phase 7, document reshaping: one worker cleans up or rewrites the published document, a batch at a time;
the knowledge reshaping, `document_knowledge_service`, runs on the same parts and state.

The program cuts the document into numbered blocks (`utils/cleanup_block_utils.py`) and the blocks into batches of at
least `documentReshaping.batchTokens` estimated tokens, each ending before a heading, or at the next block once it holds
twice that with no heading come. Each batch is one conversation: the
worker is told the document's size, its estimated tokens, its figure count and where the batch stands, reads the
document's outline and its batch's blocks with its tools, edits them, and closes the batch. After every edit the
program writes the published document from the blocks, so a stopped run loses no batch already edited, and a resume
starts at the first batch not closed. The document as it was before is kept in `07_reshaping/before.md`.
"""

from __future__ import annotations

import base64
import binascii
import functools
import io
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openai
from PIL import Image, UnidentifiedImageError
from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
                                     messages_from_dict, messages_to_dict)

import config as app_config
from agent_client import finish_reason_utils
from agent_client.agent_errors import ControlStop
from chains import chain_runner
from models import output_contract
from models.batch_run_request import BatchRunRequest
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from services import control_signal_service
from services import document_folder_service
from services import document_pass_service
from services import host_return_service
from services import settings_service
from services import conversation_message_service
from services import worker_feedback_service
from services import worker_message_service
from models import worker_feedback_vocabulary
from schemas.feedback_actions import ReportProblem
from utils import cleanup_block_utils
from utils import document_edit_utils
from utils import starting_messages_utils

__all__ = ["start_pass", "run_batch", "write_report", "list_open_batches", "build_tool_specs", "build_tool_spec",
           "write_state", "estimate_tokens", "read_block_range", "log_turn", "build_batch_log_path", "save_request",
           "rebuild_request", "stream_reply", "is_looped", "LOOPED_REPLY_ASK", "save_conversation",
           "read_conversation", "count_calls", "build_conversation_path", "read_part_tokens", "split_open_parts",
           "add_part_messages", "record_part_reply", "build_feedback_tool_spec", "build_feedback_text",
           "is_feedback_call", "is_feedback_turn", "record_feedback","stream_reply_to_files", "write_conversation_file",
           "read_conversation_file", "estimate_conversation_tokens", "mark_result", "build_prune_tool_spec",
           "prune_results", "warn_conversation_size", "is_conversation_full", "build_compacted_conversation", "PRUNE_TOOL"]

# Each open part's conversation, saved after every turn, `conversations/batch-NNN.json` in the pass's folder
_CONVERSATIONS_DIR = "conversations"
# How to report a problem or an improvement, the same text every worker with `report_problem` reads
_FEEDBACK_INSTRUCTION = "instructions/_shared/worker-feedback.md"
# How often the console says a part's model call is still under way
_PROGRESS_SECONDS = 120
# How many calls a part's reply gets when the model host errors while its model works
_HOST_ERROR_TRIES = 3
# What a part's worker is told after its thinking repeated itself and was stopped
LOOPED_REPLY_ASK = "Your thinking repeated the same passage and was stopped. Answer now with one tool call."
# A part's conversation is kept within the model's window the way phase 4's is (AGENTS.md, "A worker's conversation is
# warned, pruned by the worker, then compacted"): warned at two shares of its own size limit (`documentReshaping.*`),
# pruned by the worker with `PRUNE_TOOL`, and compacted at the limit; the program never clears a message on its own,
# since each change to an earlier message makes the model host read the whole prompt again
PRUNE_TOOL = "prune_results"
# A result's mark at its start, `[M45]`: its place in the conversation, which the worker names to prune it
_RESULT_MARK = re.compile(r"^M(\d+)$")
_COMPACT_ASK = ("Your conversation on this part has reached its size limit and is compacted now. Write, in words and "
                "with no tool call, a summary for yourself to go on from: what of your part you have already written "
                "and into which sections, what of your part is still to write, and every decision later turns must "
                "keep. The conversation then restarts from your instructions, your part and this summary; what you "
                "wrote is in the document.")
_COMPACT_HANDOFF = ("Your conversation on this part was compacted at its size limit. Your summary of your work so far:\n\n"
                    "{summary}\n\nWhat you wrote is in the document. Read the outline and any section again before you "
                    "change it, and go on with the part.")

_STATE_FILE = "state.json"
# The instruction the clean up and rewrite passes are sent, its step in `context_map.json`: the clean up pass's own
# name, kept when phase 7 became document reshaping
CLEANUP_INSTRUCTION_STEP = "document_cleanup"
_BEFORE_FILE = document_pass_service.BEFORE_FILE
_EARLIER_DIR = document_pass_service.EARLIER_DIR
# The most a read returns, as a share of a batch's size: a read of the whole document is refused, as the owner asked
_READ_SHARE = 1.25


def build_reshaping_dir(paths: RunPaths) -> Path:
    return document_pass_service.build_reshaping_dir(paths.output_root)


def build_report_path(paths: RunPaths) -> Path:
    return document_pass_service.build_report_path(paths.output_root)


def _read_state(paths: RunPaths) -> dict[str, Any] | None:
    path = build_reshaping_dir(paths) / _STATE_FILE

    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_state(paths: RunPaths,
                state: dict[str, Any]) -> None:
    (build_reshaping_dir(paths) / _STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                                                        encoding="utf-8")


def _write_document(paths: RunPaths,
                    state: dict[str, Any]) -> None:
    document_folder_service.build_document_path(paths).write_text(
        state["front"] + cleanup_block_utils.join_blocks(state["blocks"]), encoding="utf-8")


def estimate_tokens(chars: int) -> int:
    return round(chars / app_config.settings.document_reshaping.chars_per_token)


def _estimate_figure_tokens(part: dict[str, Any]) -> int:
    """About how many prompt tokens one figure costs as it is sent: a vision model of this family reads one token per
    28 by 28 pixels."""

    url = str((part.get("image_url") or {}).get("url", ""))
    try:
        with Image.open(io.BytesIO(base64.b64decode(url.partition(",")[2]))) as image:
            width, height = image.size
    except (UnidentifiedImageError, binascii.Error):
        # a figure whose size cannot be read is counted as a whole video frame, the most a figure costs
        return app_config.settings.document.frame_tokens

    return max(1, round(width * height / (28 * 28)))


def _estimate_message_tokens(message: BaseMessage) -> int:
    """About how many prompt tokens one message of a part's conversation costs: its text and tool calls, and each
    figure by its own size (a fixed cost per figure counted 150 small figures as 150,000 tokens)."""

    text_tokens = starting_messages_utils.estimate_prompt_tokens(
        messages=[message],
        chars_per_token=app_config.settings.document_reshaping.chars_per_token,
        frame_tokens=0,
        tools_tokens=0)
    parts = message.content if isinstance(message.content, list) else []
    figure_tokens = [_estimate_figure_tokens(part) for part in parts
                     if isinstance(part, dict) and part.get("type") == "image_url"]

    return text_tokens + sum(figure_tokens)


def estimate_conversation_tokens(messages: list[BaseMessage]) -> int:
    return sum(_estimate_message_tokens(message) for message in messages)


def mark_result(messages: list[BaseMessage],
                text: str) -> str:
    """`text` with the mark the worker prunes it by, `[M<n>]`, `n` the place it takes once appended to `messages`."""

    return f"[M{len(messages)}] {text}"


def build_prune_tool_spec() -> dict[str, Any]:
    return build_tool_spec(PRUNE_TOOL, "Drop from this conversation results you no longer need, named by the mark at "
                           "their start, such as M45: a section you have written from, a figure you have decided on, "
                           "a read of blocks. Each becomes one line; read it again if you need it later. What you wrote "
                           "stays in the document. Use it when a context check says the conversation is filling up.",
                           {"results": {"type": "array", "items": {"type": "string", "description": "such as M45"}}},
                           ["results"])


def prune_results(messages: list[BaseMessage],
                  names: list[Any]) -> str:
    """The results the worker named (`[M<n>]` marks) replaced by one line each, which it chose; the answer says what was
    pruned, about how many tokens that freed, and each name refused. The instructions and the part stay whole."""

    pruned, refused, freed = [], [], 0
    for name in names:
        found = _RESULT_MARK.match(str(name).strip().strip("[]"))
        index = int(found.group(1)) if found else -1
        message = messages[index] if 2 <= index < len(messages) else None
        is_result = isinstance(message, ToolMessage) or (isinstance(message, HumanMessage) and isinstance(message.content, list))
        if message is None or not is_result:
            refused.append(f"{name!r} names no result of this conversation")
            continue
        line = f"(M{index} pruned by you: read it again if you need it)"
        shorter = (ToolMessage(content=line, tool_call_id=message.tool_call_id) if isinstance(message, ToolMessage)
                   else HumanMessage(content=line))
        freed += _estimate_message_tokens(message) - _estimate_message_tokens(shorter)
        messages[index] = shorter
        pruned.append(f"M{index}")

    answer = f"Pruned {', '.join(pruned)}, about {freed:,} tokens freed." if pruned else "Nothing pruned."

    return answer + "".join(f"\nRefused: {reason}." for reason in refused)


def warn_conversation_size(messages: list[BaseMessage],
                           read: dict[str, set[int]]) -> str:
    """The context check the worker reads on its next tool result once its conversation passes the first and then the
    second warning share of the size limit, each once (`read["warned"]` keeps which), or an empty string."""

    settings = app_config.settings.document_reshaping
    limit = settings.conversation_size_limit_tokens
    percents = (settings.first_warning_percent, settings.second_warning_percent)
    count = estimate_conversation_tokens(messages)
    warned = read.setdefault("warned", set())
    passed = [percent for percent in percents if count * 100 >= limit * percent and percent not in warned]
    if not passed:
        return ""
    warned.update(passed)

    return (f"\n\nContext check: this conversation holds about {count:,} tokens; at {limit:,} it is compacted: you write a "
            f"summary of your work on this part, and the conversation restarts from your instructions, your part and "
            f"that summary. Prune with {PRUNE_TOOL} the results you no longer need, by the mark at their start; each "
            f"one pruned puts that off. This is not a request to finish: carry on with the part.")


def is_conversation_full(messages: list[BaseMessage]) -> bool:
    return estimate_conversation_tokens(messages) >= app_config.settings.document_reshaping.conversation_size_limit_tokens


def build_compacted_conversation(paths: RunPaths,
                                 bound: Any,
                                 messages: list[BaseMessage],
                                 number: int,
                                 log: Any) -> list[BaseMessage]:
    """Part `number`'s conversation compacted at its size limit, as phase 4's is: the worker writes a summary of its work
    in words, and the conversation restarts from its instructions, its part's first message and that summary."""

    reply = stream_reply(paths, bound, [*messages, HumanMessage(content=_COMPACT_ASK)], number, log)
    summary = str(reply.content).strip() or "(no summary was written: read the outline to see what is written)"
    # the parts table counts these rows as the part's compactions
    log_turn(log_path=build_batch_log_path(paths=paths, number=number),
             row={"compacted": estimate_conversation_tokens(messages)})

    return [messages[0], messages[1], HumanMessage(content=_COMPACT_HANDOFF.format(summary=summary))]


def _set_finished_pass_aside(paths: RunPaths) -> None:
    """A finished pass's files moved into `earlier/<n>/` with the document it left (`after.md`): a new pass may start
    from the original, so the next pass's `before.md` is no longer this pass's result."""

    folder = build_reshaping_dir(paths)
    target = folder / _EARLIER_DIR / str(len(list((folder / _EARLIER_DIR).glob("*"))) + 1)
    target.mkdir(parents=True)
    for path in list(folder.iterdir()):
        if path.name != _EARLIER_DIR:
            path.rename(target / path.name)
    published = document_folder_service.build_document_path(paths)
    if published.is_file():
        (target / document_pass_service.AFTER_FILE).write_text(published.read_text(encoding="utf-8"), encoding="utf-8")


def start_pass(paths: RunPaths,
               asked: dict[str, str]) -> dict[str, Any]:
    """The phase's state, for any pass: read back when an earlier start of this pass left it unfinished, else made from
    the published document, which is kept first as `before.md`. A pass that finished, or one asked with another pass
    or other wishes, is set aside first, its files kept."""

    state = _read_state(paths)
    source = asked.get("source", document_pass_service.SOURCE_ORIGINAL)
    is_same_pass = (state is not None and state["pass"] == asked["pass"] and state.get("wishes", "") == asked["wishes"]
                    and state.get("source", document_pass_service.SOURCE_ORIGINAL) == source)
    if is_same_pass and not build_report_path(paths).is_file():
        return state
    if state is not None:
        _set_finished_pass_aside(paths)
    folder = build_reshaping_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    text = document_pass_service.find_pass_input(paths.output_root, source,
                                                 document_folder_service.build_document_path(paths)).read_text(encoding="utf-8")
    (folder / _BEFORE_FILE).write_text(text, encoding="utf-8")
    front, body = document_edit_utils.split_front_matter(text)
    blocks = cleanup_block_utils.list_blocks(body)
    settings = app_config.settings.document_reshaping
    batch_chars = round(settings.batch_tokens * settings.chars_per_token)
    state = {"pass": asked["pass"], "wishes": asked["wishes"], "source": source, "front": front, "blocks": blocks, "next_id": len(blocks) + 1,
             "batches": cleanup_block_utils.split_batches(blocks, batch_chars), "closed": {},
             "size": {"chars": len(body), "tokens": estimate_tokens(len(body)), "blocks": len(blocks),
                      "figures": len(cleanup_block_utils.list_figure_lines(body))}}
    write_state(paths, state)

    return state


def read_part_tokens() -> int:
    """The part size the settings files hold now, read again at each part: a value saved on the Settings page while a
    pass runs cuts its parts not yet started (the owner: "automaticaly recalculate the parts for the currnt run")."""

    try:
        return settings_service.read_next_job_settings(app_config.PROFILE_NAME).document_reshaping.batch_tokens
    except (ValueError, OSError):
        return app_config.settings.document_reshaping.batch_tokens


def split_open_parts(paths: RunPaths,
                     state: dict[str, Any],
                     part_tokens: int) -> bool:
    """The parts no worker has started cut again at `part_tokens`, the parts closed or started kept as they are, so the
    part under way is never broken (the owner: "not break or stop the current running part"). Whether the parts
    changed; the size they were cut at is kept in the state."""

    batches = state["batches"]
    kept = 0
    while kept < len(batches) and (str(kept + 1) in state["closed"] or build_batch_log_path(paths, kept + 1).is_file()):
        kept += 1
    by_id = {block["id"]: block for block in state["blocks"]}
    rest = [by_id[block_id] for batch in batches[kept:] for block_id in batch if block_id in by_id]
    chars = round(part_tokens * app_config.settings.document_reshaping.chars_per_token)
    recut = cleanup_block_utils.split_batches(rest, chars)
    if recut == batches[kept:]:
        state["part_tokens"] = part_tokens
        return False
    state["batches"], state["part_tokens"] = batches[:kept] + recut, part_tokens

    return True


def list_open_batches(state: dict[str, Any]) -> list[int]:
    return [number for number in range(1, len(state["batches"]) + 1) if str(number) not in state["closed"]]


def build_tool_spec(name: str,
                    description: str,
                    properties: dict[str, Any],
                    required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


def build_feedback_tool_spec() -> dict[str, Any]:
    """`report_problem`, which every phase 7 worker is offered beside its own tools (the owner: "also give the worker the
    feedbak tool"), with the kinds the feedback file counts."""

    return build_tool_spec(worker_feedback_vocabulary.FEEDBACK_TOOL, " ".join(str(ReportProblem.__doc__).split()), {
        "kind": {"type": "string", "enum": list(worker_feedback_vocabulary.FEEDBACK_KINDS)},
        "summary": {"type": "string", "description": "one line: what went wrong or could be better, in plain words"},
        "detail": {"type": "string", "description": "what you were doing, the sentence or tool at fault, and what "
                                                    "would have helped"}}, ["kind", "summary"])


def build_feedback_text() -> str:
    """How to report, which the code adds to the instructions of a worker it offers `report_problem`, so a worker of a
    job started without the tool is never told to use it."""

    return system_prompt_builder.read_context_text(_FEEDBACK_INSTRUCTION)


def is_feedback_call(call: dict[str, Any]) -> bool:
    return call.get("name") == worker_feedback_vocabulary.FEEDBACK_TOOL


def is_feedback_turn(tool_calls: list[dict[str, Any]]) -> bool:
    """Whether a turn only reported: such a turn costs the part none of its calls, so a report is never saved up."""

    return bool(tool_calls) and all(is_feedback_call(call) for call in tool_calls)


def record_feedback(paths: RunPaths,
                    number: int,
                    args: dict[str, Any],
                    log: Any) -> str:
    """Carry out part `number`'s `report_problem`: the report goes into the run folder's feedback file, which the
    detail view shows, and onto the console; the sentence the worker reads back."""

    outcome = worker_feedback_service.record_report(paths.output_root / output_contract.WORKER_FEEDBACK_FILE,
                                                    kind=str(args.get("kind") or ""),
                                                    summary=str(args.get("summary") or ""),
                                                    detail=str(args.get("detail") or ""), where=f"phase 7 · part {number}")
    for line in outcome.console_lines:
        log.info(line)

    return outcome.reply


def build_tool_specs() -> list[dict[str, Any]]:
    """The worker's six tools: the outline, a read, the edits, the batch's close, the prune of results it no longer needs,
    and the report."""

    spec = build_tool_spec
    block = {"type": "string", "description": "a block's number as the read shows it, such as B12"}
    edit = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["remove", "replace", "insert_after"]},
        "block": block,
        "last_block": {**block, "description": "for remove or replace of a run of blocks: the run's last block"},
        "text": {"type": "string", "description": "replace and insert_after: the new markdown"}},
            "required": ["action", "block"]}

    return [
        spec("read_outline", "Every heading of the whole document with its block number, and no text. Cheap.", {}, []),
        spec("read_blocks", "The blocks from `first` to `last` with their numbers. Read only what you need: a read "
             "of more than about one batch is refused.", {"first": block, "last": block}, ["first", "last"]),
        spec("edit_blocks", "Apply edits to blocks of this batch, in order. Each edit is applied or refused alone, "
             "and the reply names every refused edit with its reason.",
             {"edits": {"type": "array", "items": edit}}, ["edits"]),
        spec("finish_batch", "Close this batch once every block of it was read and edited as needed.",
             {"summary": {"type": "string", "description": "what you changed in this batch, in a few lines"}},
             ["summary"]),
        build_prune_tool_spec(),
        build_feedback_tool_spec(),
    ]


def _build_system_text(pass_name: str,
                       wishes: str) -> str:
    text = system_prompt_builder.build_system_prompt(CLEANUP_INSTRUCTION_STEP)
    # the other pass's section left out, so the worker reads only the pass it does
    for name in output_contract.DOCUMENT_PASSES:
        if name != pass_name:
            for section in document_edit_utils.read_sections(text, f"The {document_pass_service.describe_pass(name)} pass"):
                text = text.replace(section, "")
    wish_text = wishes.strip() or "(none)"

    return (f"{text.rstrip()}\n\n{build_feedback_text()}\n\n"
            f"## The person's own wishes for this document\n\n{wish_text}\n")


def rebuild_request(state: dict[str, Any],
                    number: int) -> tuple[str, str]:
    """Batch `number`'s instructions and first message as built now from the current files and the pass's state, for a
    pass that started before it saved what it sent."""

    return _build_system_text(state["pass"], state.get("wishes", "")), _build_batch_text(state, number)


def _build_batch_text(state: dict[str, Any],
                      number: int) -> str:
    size = state["size"]
    ids = state["batches"][number - 1]
    present = {block["id"]: block for block in state["blocks"]}
    batch_chars = sum(len(present[block_id]["text"]) for block_id in ids if block_id in present)
    first, last = ids[0], ids[-1]

    return (f"The document: {size['chars']:,} characters, about {size['tokens']:,} tokens, {size['blocks']:,} blocks, "
            f"{size['figures']} figures.\n"
            f"This is batch {number} of {len(state['batches'])}: blocks B{first} to B{last}, about "
            f"{estimate_tokens(batch_chars):,} tokens. Edit only blocks of this batch.\n"
            f"Read the outline first if you need the document's structure, then read this batch in as few reads as "
            f"you can, edit it, and call finish_batch.")


def read_block_range(state: dict[str, Any],
                     args: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The source blocks from `args["first"]` to `args["last"]`, or the reason the read is refused (a block the
    document does not hold, or more than about one batch)."""

    first = cleanup_block_utils.read_block_id(args.get("first"))
    last = cleanup_block_utils.read_block_id(args.get("last"))
    order = [block["id"] for block in state["blocks"]]
    if first not in order or last not in order:
        return [], f"first and last must name blocks the document holds now (B1 to B{max(order)})"
    start, end = order.index(first), order.index(last) + 1
    blocks = state["blocks"][start:end]
    settings = app_config.settings.document_reshaping
    limit = round(settings.batch_tokens * settings.chars_per_token * _READ_SHARE)
    if sum(len(block["text"]) for block in blocks) > limit:
        return [], (f"that read holds more than about {round(settings.batch_tokens * _READ_SHARE):,} tokens; read a "
                    "smaller range")

    return blocks, ""


def _answer_tool(state: dict[str, Any],
                 number: int,
                 call: dict[str, Any],
                 read_ids: set[int]) -> tuple[str, bool]:
    """The tool's reply, and whether the batch is now closed."""

    name, args = call.get("name"), call.get("args") or {}
    batch = state["batches"][number - 1]
    if name == "read_outline":
        lines = [f"B{block['id']} {block['text']}" for block in state["blocks"]
                 if cleanup_block_utils.is_heading(block["text"])]
        return "\n".join(lines) or "The document has no heading.", False
    if name == "read_blocks":
        blocks, refusal = read_block_range(state, args)
        if refusal:
            return f"Refused: {refusal}.", False
        read_ids.update(block["id"] for block in blocks)
        return cleanup_block_utils.render_blocks(blocks), False
    if name == "edit_blocks":
        edits = args.get("edits") if isinstance(args.get("edits"), list) else []
        unread = [edit.get("block") for edit in edits if isinstance(edit, dict)
                  and cleanup_block_utils.read_block_id(edit.get("block")) not in read_ids]
        if unread:
            return f"Refused: read {unread[0]} before you edit it.", False
        blocks, refused, next_id = cleanup_block_utils.apply_edits(state["blocks"], batch, edits, state["next_id"])
        batch.extend(range(state["next_id"], next_id))
        read_ids.update(range(state["next_id"], next_id))
        state["blocks"], state["next_id"] = blocks, next_id
        applied = len(edits) - len(refused)
        return f"{applied} edit(s) applied." + ("".join(f"\nRefused {line}" for line in refused)), False
    if name == "finish_batch":
        present = {block["id"] for block in state["blocks"]}
        unread = [block_id for block_id in batch if block_id in present and block_id not in read_ids]
        if unread:
            return (f"Refused: {len(unread)} block(s) of this batch were never read, the first B{unread[0]}; read "
                    "them and edit what needs it, then finish."), False
        state["closed"][str(number)] = str(args.get("summary") or "").strip()
        return "Batch closed.", True

    return f"Refused: no tool is called {name!r}.", False


def _read_through_host_failures(read_once: Callable[[], BaseMessage],
                                log: Any,
                                label: str,
                                note_host: Callable[[str], None]) -> BaseMessage:
    """The reply `read_once` reads, through what the model host does under it. A host with no working model is waited
    for (`host_return_service`), and an error of the host while its model works, which a long prompt can hit where a
    short one passes, is asked again up to `_HOST_ERROR_TRIES` calls in all. LM Studio restarted under a part and its
    next load answered with engine errors: the run crashed, and the queue's next jobs failed in seconds each.
    `note_host` is told what is wrong with the model while the wait runs, and an empty text once it works again."""

    errors = 0
    while True:
        try:
            return read_once()
        except Exception as error:  # noqa: BLE001 (re-raised when no wait or new try can help)
            is_back = host_return_service.wait_for_lost_model(output_contract.DOCUMENT_RESHAPING_PHASE,
                                                               on_wait=note_host)
            note_host("")
            if is_back:
                log.info(f"{label} · the model host works again; the call is asked again")
                continue
            errors += 1
            if not isinstance(error, openai.APIError) or errors >= _HOST_ERROR_TRIES:
                raise
            # never "while its model works": a host that refused the connection reaches this line too, after its wait
            log.warning(f"{label} · the model host failed this call ({type(error).__name__}: {str(error)[:120]}); "
                        f"the call is asked again, try {errors + 1} of {_HOST_ERROR_TRIES}")


def stream_reply(paths: RunPaths,
                 bound: Any,
                 messages: list[BaseMessage],
                 number: int,
                 log: Any) -> BaseMessage:
    """Part `number`'s next reply, streamed into the part's thinking and call files."""

    files = (document_pass_service.build_part_thinking_path(paths.output_root, number),
             document_pass_service.build_part_call_path(paths.output_root, number))
    reply = stream_reply_to_files(bound=bound, messages=messages, files=files, log=log, label=f"phase 7: part {number}")
    # each call's written tokens and writing time go into the part's turn log, where the parts table adds them up into
    # the part's average speed (the owner: "add to the time column the average token per second"); the call file keeps
    # only the last call
    call = json.loads(files[1].read_text(encoding="utf-8")) if files[1].is_file() else {}
    if call.get("first_piece") and call.get("ended") and call.get("output_tokens"):
        log_turn(build_batch_log_path(paths, number), {"written_tokens": int(call["output_tokens"]),
                                                       "writing_seconds": round(call["ended"] - call["first_piece"], 2)})

    return reply


def stream_reply_to_files(bound: Any,
                          messages: list[BaseMessage],
                          files: tuple[Path, Path],
                          log: Any,
                          label: str) -> BaseMessage:
    """A worker's next reply, streamed: `files` are its thinking file and its call file. Its thinking so far is written
    to the first, a thinking that loops is ended, and the console says every `_PROGRESS_SECONDS` that the call is still
    under way, with its time and words. Sent whole, a part's call thought 12 minutes with nothing on the page or the
    console, and the owner could not tell it from a stuck one."""

    live, call_path = files
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("", encoding="utf-8")
    started_at = time.time()
    call_path.write_text(json.dumps({"started": started_at}), encoding="utf-8")
    is_done = threading.Event()
    started = time.monotonic()
    host_wait = {"problem": "", "since": 0.0}

    def log_still_writing() -> None:
        while not is_done.wait(_PROGRESS_SECONDS):
            if host_wait["problem"]:
                minutes = round((time.monotonic() - host_wait["since"]) / 60)
                log.info(f"{label} · waiting for the model host, {minutes} min: {host_wait['problem']}")
                continue
            words = len(live.read_text(encoding="utf-8").split()) if live.is_file() else 0
            minutes = round((time.monotonic() - started) / 60)
            log.info(f"{label} · the model is still writing its reply, {minutes} min, "
                     f"{words:,} words of thinking so far")

    def note_first_piece() -> None:
        # the host has read the prompt and writes: the part's live panel says reading until here, thinking after
        call_path.write_text(json.dumps({"started": started_at, "first_piece": time.time()}), encoding="utf-8")

    def note_host(problem: str) -> None:
        # a wait for a model that does not work is written to the call file, so the page says the part waits for the
        # model host and not that it writes; an empty problem ends the wait and the call starts again
        if not problem and not host_wait["problem"]:
            return
        host_wait.update(problem=problem, since=time.monotonic())
        call = {"started": time.time()} if not problem else {"started": started_at, "host_wait": time.time(),
                                                             "host_problem": problem}
        call_path.write_text(json.dumps(call), encoding="utf-8")

    threading.Thread(target=log_still_writing, daemon=True).start()
    read_once = functools.partial(chain_runner.read_streamed_reply, bound, messages, live, note_first_piece)
    try:
        reply = _read_through_host_failures(read_once=read_once, log=log, label=label, note_host=note_host)
    finally:
        is_done.set()
    # each call's prompt size and writing speed, measured: a part's calls took 10 to 25 minutes, and only an estimate
    # of the conversation's size could say why
    usage = getattr(reply, "usage_metadata", None) or {}
    seconds = time.monotonic() - started
    written = int(usage.get("output_tokens") or 0)
    # the speed the model host reports once the call ends, for the part's live panel (the owner: "get from the api the
    # token per scond speed, if its not provided we fallback to self estimation"); a stream stopped as a loop reports none
    first_piece = json.loads(call_path.read_text(encoding="utf-8")).get("first_piece")
    ended_at = time.time()
    # the speed counts the writing alone: over the whole call, the minutes the host read the prompt read as slow writing
    writing_seconds = ended_at - first_piece if first_piece else seconds
    speed = round(written / writing_seconds, 1) if written and writing_seconds > 0 else None
    reading = f"read its prompt in {round((first_piece - started_at) / 60, 1)} min · " if first_piece else ""
    call_path.write_text(json.dumps({"started": started_at, "first_piece": first_piece, "ended": ended_at,
                                     "prompt_tokens": int(usage.get("input_tokens") or 0), "output_tokens": written,
                                     "tokens_per_second": speed}),
                         encoding="utf-8")
    log.info(f"{label} · call took {round(seconds / 60, 1)} min · {reading}prompt "
             f"{int(usage.get('input_tokens') or 0):,} tokens · wrote {written:,} tokens, {speed or 0} a second")

    return reply


def build_conversation_path(paths: RunPaths,
                            number: int) -> Path:
    return build_reshaping_dir(paths) / _CONVERSATIONS_DIR / f"batch-{number:03d}.json"


def save_conversation(paths: RunPaths,
                      number: int,
                      messages: list[BaseMessage],
                      read: dict[str, set[int]]) -> None:
    """Part `number`'s whole conversation and what its worker has read (blocks, sections), written after every turn,
    so a run stopped mid-part (a crash, a restart, a stop) goes on with it instead of starting the part again: a part of
    a large document took hours, and a resume began it from its first message (the owner: "it contiue where it stoped
    in the same document")."""

    write_conversation_file(build_conversation_path(paths, number), messages, read)


def write_conversation_file(path: Path,
                            messages: list[BaseMessage],
                            read: dict[str, set[int]]) -> None:
    """A conversation and what its worker has read, replaced whole, so a stop never leaves half of it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    written = path.with_suffix(".tmp")
    written.write_text(json.dumps({"messages": messages_to_dict(messages),
                                   "read": {kind: sorted(ids) for kind, ids in read.items()}}, ensure_ascii=False),
                       encoding="utf-8")
    written.replace(path)


def read_conversation(paths: RunPaths,
                      number: int) -> tuple[list[BaseMessage], dict[str, set[int]]] | None:
    """Part `number`'s saved conversation and what its worker had read, or None when it has none or it cannot be
    read."""

    return read_conversation_file(build_conversation_path(paths, number))


def read_conversation_file(path: Path) -> tuple[list[BaseMessage], dict[str, set[int]]] | None:
    """A saved conversation and what its worker had read, or None when there is none or it cannot be read."""

    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return (messages_from_dict(saved["messages"]),
                {kind: set(ids) for kind, ids in (saved.get("read") or {}).items()})
    except (ValueError, KeyError, TypeError):
        return None


def count_calls(messages: list[BaseMessage]) -> int:
    """The model calls a saved conversation already made: one per reply, a looped reply being dropped and asked again."""

    return sum(1 for message in messages if isinstance(message, AIMessage))


def stop_if_asked(control_check: Callable[[], str | None] | None,
                  where: str) -> None:
    """End the run with ControlStop when the person asked it to stop; a pause is waited out inside `control_check`.
    Called between two turns of a part, where the conversation is saved and nothing is half written: phase 7 read no
    signal, so the page's Stop and Pause did nothing for a pass that runs for hours."""

    if control_check is not None and control_check() == control_signal_service.STOP:
        raise ControlStop(f"stopped {where}")


def is_looped(reply: BaseMessage) -> bool:
    return (getattr(reply, "response_metadata", None) or {}).get("finish_reason") == finish_reason_utils.THINKING_LOOPED


def log_turn(log_path: Path,
             row: dict[str, Any]) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n")


def build_batch_log_path(paths: RunPaths,
                         number: int) -> Path:
    return build_reshaping_dir(paths) / f"batch-{number:03d}.jsonl"


def add_part_messages(paths: RunPaths,
                      number: int,
                      messages: list[BaseMessage]) -> int:
    """Add what the person wrote from the part's live panel since the last call to the conversation; how many."""

    path = document_pass_service.build_part_messages_path(paths.output_root, number)

    return conversation_message_service.add_waiting_to_conversation(path, messages)


def record_part_reply(paths: RunPaths,
                      number: int,
                      reply: BaseMessage) -> None:
    """The worker's words, the answer to a question the person asked it."""

    worker_message_service.record_reply(document_pass_service.build_part_messages_path(paths.output_root, number),
                                        str(reply.content))


def save_request(paths: RunPaths,
                 number: int,
                 messages: list[BaseMessage]) -> None:
    """The instructions and the first message batch `number`'s worker is sent, saved before its first call for the
    detail view's "What the worker saw" (`worker_view_service`), which showed nothing of phase 7 without them."""

    pass_dir = build_reshaping_dir(paths)
    requests_dir = pass_dir / document_pass_service.REQUESTS_DIR
    requests_dir.mkdir(parents=True, exist_ok=True)
    (pass_dir / document_pass_service.SYSTEM_PROMPT_FILE).write_text(str(messages[0].content), encoding="utf-8")
    (requests_dir / f"batch-{number:03d}.md").write_text(_render_content(messages[1].content), encoding="utf-8")


def _render_content(content: Any) -> str:
    """A message's content as saved text: a part with images keeps its text and writes each image as the placeholder
    the worker view reads (`[image: N characters of data URI]`), never its bytes."""

    if isinstance(content, str):
        return content
    pieces = []
    for part in content:
        if part.get("type") == "image_url":
            pieces.append(f"[image: {len(str(part.get('image_url', {}).get('url', '')))} characters of data URI]")
        else:
            pieces.append(str(part.get("text", "")))

    return "\n\n".join(pieces)


def run_batch(request: BatchRunRequest) -> tuple[bool, int, str]:
    """Batch `request.number`'s conversation, until the worker closes it or spends `documentCleanup.batchCalls` calls:
    whether it was closed, the calls it made, and why not. Every edit is written into the published document at once.
    The worker is told the pass and the person's wishes the state holds. A stop asked for ends it before its next
    call."""

    paths = request.paths
    state = request.state
    number = request.number
    model = request.model
    log = request.log
    control_check = request.control_check
    log_path = build_batch_log_path(paths, number)
    messages: list[BaseMessage] = [SystemMessage(content=_build_system_text(state["pass"], state.get("wishes", ""))),
                                   HumanMessage(content=_build_batch_text(state, number))]
    save_request(paths, number, messages)
    bound = model.bind_tools(build_tool_specs())
    read_ids: set[int] = set()
    warned: set[int] = set()
    calls = app_config.settings.document_reshaping.batch_calls
    saved = read_conversation(paths, number)
    if saved is not None:
        messages, read = saved
        read_ids = read.get("blocks", set())
        warned = read.get("warned", set())
        log.info(f"phase 7: batch {number} · goes on with its saved conversation, {count_calls(messages)} calls made")
    # each start of the part gets its full budget of calls, its numbers going on from the saved ones
    made = count_calls(messages)
    call_number, last_call = made, made + calls
    while call_number < last_call:
        stop_if_asked(control_check, f"before call {call_number + 1} of batch {number}")
        call_number += 1
        # the person's message costs the part none of its calls
        delivered = add_part_messages(paths, number, messages)
        if delivered:
            last_call += delivered
            save_conversation(paths, number, messages, {"blocks": read_ids, "warned": warned})
            log.info(f"phase 7: batch {number} · {delivered} message(s) from the person sent to the worker")
        # at the size limit the conversation is compacted, as phase 4's is, and what it held counts as unread
        if is_conversation_full(messages):
            size = estimate_conversation_tokens(messages)
            messages = build_compacted_conversation(paths, bound, messages, number, log)
            read_ids, warned = set(), set()
            save_conversation(paths, number, messages, {"blocks": read_ids, "warned": warned})
            log.info(f"phase 7: batch {number} · its conversation reached the size limit of "
                     f"{app_config.settings.document_reshaping.conversation_size_limit_tokens:,} tokens at about {size:,}: "
                     f"compacted into the worker's summary")
        reply = stream_reply(paths, bound, messages, number, log)
        if is_looped(reply):
            log.info(f"phase 7: batch {number} · the thinking repeated itself and was stopped")
            log_turn(log_path, {"call": call_number, "text": "(thinking stopped: it repeated itself)", "tools": []})
            messages.append(HumanMessage(content=LOOPED_REPLY_ASK))
            continue
        messages.append(reply)
        record_part_reply(paths, number, reply)
        tool_calls = reply.tool_calls if isinstance(reply, AIMessage) else []
        log_turn(log_path, {"call": call_number, "text": str(reply.content)[:4_000],
                             "tools": [{"name": call.get("name"), "args": call.get("args")} for call in tool_calls]})
        if not tool_calls:
            messages.append(HumanMessage(content="Answer with a tool call: read, edit, or finish_batch."))
            continue
        if is_feedback_turn(tool_calls):
            last_call += 1
        is_closed = False
        for call in tool_calls:
            if is_feedback_call(call):
                answer, is_now_closed = record_feedback(paths, number, call.get("args") or {}, log), False
            elif call.get("name") == PRUNE_TOOL:
                names = (call.get("args") or {}).get("results")
                answer, is_now_closed = prune_results(messages, names if isinstance(names, list) else []), False
            else:
                answer, is_now_closed = _answer_tool(state, number, call, read_ids)
            is_closed = is_closed or is_now_closed
            log.info(f"phase 7: batch {number} · {call.get('name')} · {answer.splitlines()[0][:160]}")
            # each result carries its mark, which the worker names to prune it
            messages.append(ToolMessage(content=mark_result(messages, answer), tool_call_id=call.get("id", "")))
            log_turn(log_path, {"call": call_number, "tool": call.get("name"), "result": answer[:4_000]})
        # the context check reaches the worker on its last result of the turn
        warning = warn_conversation_size(messages, {"warned": warned})
        if warning:
            messages[-1] = ToolMessage(content=f"{messages[-1].content}{warning}", tool_call_id=messages[-1].tool_call_id)
        write_state(paths, state)
        _write_document(paths, state)
        save_conversation(paths, number, messages, {"blocks": read_ids, "warned": warned})
        if is_closed:
            return True, call_number, ""

    return False, calls, f"batch {number} was not closed within {calls} calls"


def write_report(paths: RunPaths,
                 state: dict[str, Any]) -> Path:
    """The phase's file: what each batch changed, and the document's size before and after."""

    before = state["size"]
    after_chars = len(cleanup_block_utils.join_blocks(state["blocks"]))
    lines = [f"# Document {document_pass_service.describe_pass(state['pass'])}", "",
             f"Before: {before['chars']:,} characters (about {before['tokens']:,} tokens), {before['blocks']:,} blocks, "
             f"{before['figures']} figures.",
             f"After: {after_chars:,} characters (about {estimate_tokens(after_chars):,} tokens), "
             f"{len(state['blocks']):,} blocks, "
             f"{len(cleanup_block_utils.list_figure_lines(cleanup_block_utils.join_blocks(state['blocks'])))} figures.",
             "", "The document before this phase: `before.md`.", ""]
    for number in range(1, len(state["batches"]) + 1):
        lines += [f"## Batch {number}", "", state["closed"].get(str(number), "(not closed)"), ""]
    path = build_report_path(paths)
    path.write_text("\n".join(lines), encoding="utf-8")

    return path
