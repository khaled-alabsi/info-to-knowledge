"""A chat on a run's final document, in every workflow: the person asks from the
Artifacts tab, and a worker reads what it wants from the published document and edits, adds or removes what it was asked.

The worker is a process of its own (`i2k chat-document`), started by the server when a message comes and ended when none
waits, on the model already loaded. Its reply with no tool call ends its turn, as the answer the person waits for. Every
write goes through `artifact_edit_service`, so the version before stays and can be put back."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

import config as app_config
from models import output_contract
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from services import artifact_edit_service
from services import document_folder_service
from services import document_pass_service
from services import document_reshaping_service
from services import job_queue_service
from services import conversation_message_service
from services import worker_message_service
from utils import cleanup_block_utils
from utils import document_edit_utils
from utils import process_utils

__all__ = ["CHAT_DIR", "INSTRUCTION_STEP", "build_tool_specs", "queue_chat_message", "has_waiting_message", "start_worker",
           "answer_waiting_messages", "describe_chat", "set_conversation_aside", "set_process_starter", "build_paths"]

CHAT_DIR = "document-chat"
INSTRUCTION_STEP = "document_chat"
_EARLIER_DIR = "earlier"
_MESSAGES_FILE = "messages.jsonl"
_LOG_FILE = "turns.jsonl"
_THINKING_FILE = "thinking.md"
_CALL_FILE = "call.json"
_CONVERSATION_FILE = "conversation.json"
_DOCUMENT_STATE_FILE = "document.json"
_PROCESS_FILE = "process.json"
_CONSOLE_FILE = "console.log"
_READ_LIMIT_TOKENS = 30_000
_SEARCH_HITS = 40
_SEARCH_SHOWN_CHARS = 200
_LABEL = "document chat"
_DOCUMENT_CHANGED = ("The document was changed outside this chat since your last turn, so its block numbers are new: "
                     "read it again before you edit it.")
_LOOPED_ASK = "Your thinking repeated the same passage and was stopped. Answer now."
_OUT_OF_CALLS = "I stopped here: this turn spent all its calls. Ask me again to go on."


def _build_chat_path(output_root: Path,
                     name: str) -> Path:
    return output_root / CHAT_DIR / name


def build_paths(output_root: Path) -> RunPaths:
    return RunPaths(repository=output_root, module=output_root.name, output_root_override=output_root)


def _start_process(command: list[str],
                   console: Path) -> int:
    console.parent.mkdir(parents=True, exist_ok=True)
    with console.open("ab") as handle:
        # a session of its own, as a queued job's: a restart of the server does not end the worker
        process = subprocess.Popen(command, cwd=app_config.PLAYBOOK_ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)

    return process.pid


# The seam the tests swap: the one place this module starts a process.
_starter: Callable[[list[str], Path], int] = _start_process


def set_process_starter(starter: Callable[[list[str], Path], int]) -> Callable[[list[str], Path], int]:
    """Swap what starts the worker, for a test; returns the one it replaced."""

    global _starter
    previous, _starter = _starter, starter

    return previous


def _is_worker_alive(output_root: Path) -> bool:
    path = _build_chat_path(output_root, _PROCESS_FILE)
    if not path.is_file():
        return False
    try:
        return process_utils.is_process_alive(json.loads(path.read_text(encoding="utf-8")).get("pid"))
    except ValueError:
        return False


def queue_chat_message(output_root: Path,
                       text: str) -> None:
    """Keep what the person asks: a question, which the worker's reply with no tool call answers."""

    worker_message_service.queue_message(_build_chat_path(output_root, _MESSAGES_FILE),
                                         {"text": text, "role": "user", "is_question": True})


def has_waiting_message(output_root: Path) -> bool:
    return bool(worker_message_service.list_waiting(_build_chat_path(output_root, _MESSAGES_FILE)))


def start_worker(output_root: Path) -> None:
    """Start the chat's worker unless one runs."""

    if _is_worker_alive(output_root):
        return
    command = job_queue_service.build_tool_command(["chat-document", "--output", str(output_root)])
    pid = _starter(command, _build_chat_path(output_root, _CONSOLE_FILE))
    _build_chat_path(output_root, _PROCESS_FILE).write_text(json.dumps({"pid": pid}), encoding="utf-8")


def _read_document_state(paths: RunPaths) -> tuple[dict[str, Any], bool]:
    """The document cut into numbered blocks, the numbers kept across turns, and whether it was cut again because the
    file is not what this chat last wrote."""

    text = document_folder_service.build_document_path(paths).read_text(encoding="utf-8")
    path = _build_chat_path(paths.output_root, _DOCUMENT_STATE_FILE)
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("written") == text:
            return state, False
    front, body = document_edit_utils.split_front_matter(text)
    blocks = cleanup_block_utils.list_blocks(body)
    next_id = max((block["id"] for block in blocks), default=0) + 1

    return {"front": front, "blocks": blocks, "next_id": next_id, "written": text}, path.is_file()


def _write_document_state(paths: RunPaths,
                          state: dict[str, Any]) -> None:
    path = _build_chat_path(paths.output_root, _DOCUMENT_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _save_document(paths: RunPaths,
                   state: dict[str, Any]) -> None:
    """The document as the edits left it, the version before kept by the edit service."""

    text = state["front"] + cleanup_block_utils.join_blocks(state["blocks"])
    relative = document_folder_service.build_document_path(paths).relative_to(paths.output_root).as_posix()
    artifact_edit_service.save_markdown(paths.output_root, relative, text)
    state["written"] = text
    _write_document_state(paths, state)


def build_tool_specs() -> list[dict[str, Any]]:
    """The worker's tools: the outline, a read, a search and the edits."""

    spec = document_reshaping_service.build_tool_spec
    block = {"type": "string", "description": "a block's number as the read shows it, such as B12"}
    edit = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["remove", "replace", "insert_after"]},
        "block": block,
        "last_block": {**block, "description": "for remove or replace of a run of blocks: the run's last block"},
        "text": {"type": "string", "description": "replace and insert_after: the new markdown"}},
            "required": ["action", "block"]}

    return [
        spec("read_outline", "Every heading of the document with its block number, and no text. Cheap.", {}, []),
        spec("read_blocks", "The blocks from `first` to `last` with their numbers. A read past about 30,000 tokens is "
             "refused: read less at a time.", {"first": block, "last": block}, ["first", "last"]),
        spec("search_document", "The blocks that hold `text`, without regard to case, each with its number and start.",
             {"text": {"type": "string", "description": "the words to find"}}, ["text"]),
        spec("edit_blocks", "Apply edits in order. Each edit is applied or refused alone, and the reply names every "
             "refused edit with its reason. To add at the document's end, insert after its last block.",
             {"edits": {"type": "array", "items": edit}}, ["edits"]),
    ]


def _read_blocks(state: dict[str, Any],
                 args: dict[str, Any]) -> str:
    first, last = cleanup_block_utils.read_block_id(args.get("first")), cleanup_block_utils.read_block_id(args.get("last"))
    ids = [block["id"] for block in state["blocks"]]
    if first not in ids or last not in ids:
        return f"Refused: the document has no block {args.get('first') if first not in ids else args.get('last')}."
    start, end = sorted((ids.index(first), ids.index(last)))
    blocks = state["blocks"][start:end + 1]
    chars = sum(len(block["text"]) for block in blocks)
    if document_reshaping_service.estimate_tokens(chars) > _READ_LIMIT_TOKENS:
        return f"Refused: that read is about {document_reshaping_service.estimate_tokens(chars):,} tokens: read less at a time."

    return cleanup_block_utils.render_blocks(blocks)


def _search_document(state: dict[str, Any],
                     text: str) -> str:
    wanted = text.casefold()
    hits = [block for block in state["blocks"] if wanted and wanted in block["text"].casefold()]
    lines = [f"B{block['id']} {block['text'][:_SEARCH_SHOWN_CHARS]}" for block in hits[:_SEARCH_HITS]]
    more = [f"... and {len(hits) - _SEARCH_HITS} more."] if len(hits) > _SEARCH_HITS else []

    return "\n".join([*lines, *more]) or f"No block holds {text!r}."


def _answer_tool(paths: RunPaths,
                 state: dict[str, Any],
                 call: dict[str, Any]) -> str:
    name, args = call.get("name"), call.get("args") or {}
    if name == "read_outline":
        headings = [f"B{block['id']} {block['text']}" for block in state["blocks"]
                    if cleanup_block_utils.is_heading(block["text"])]
        return "\n".join(headings) or "The document has no heading."
    if name == "read_blocks":
        return _read_blocks(state, args)
    if name == "search_document":
        return _search_document(state, str(args.get("text") or ""))
    if name == "edit_blocks":
        edits = args.get("edits") if isinstance(args.get("edits"), list) else []
        all_ids = [block["id"] for block in state["blocks"]]
        blocks, refused, next_id = cleanup_block_utils.apply_edits(state["blocks"], all_ids, edits, state["next_id"])
        state["blocks"], state["next_id"] = blocks, next_id
        _save_document(paths, state)
        applied = len(edits) - len(refused)
        return f"{applied} edit(s) applied." + "".join(f"\nRefused {line}" for line in refused)

    return f"Refused: no tool is called {name!r}."


def _run_turn(paths: RunPaths,
              bound: Any,
              messages: list[BaseMessage],
              state: dict[str, Any],
              log: Any) -> None:
    """The worker's turn on the person's last message: calls until a reply with no tool call, which is its answer."""

    root = paths.output_root
    made = document_reshaping_service.count_calls(messages)
    limit = app_config.settings.document_reshaping.batch_calls
    log_path = _build_chat_path(root, _LOG_FILE)
    messages_path = _build_chat_path(root, _MESSAGES_FILE)
    conversation_path = _build_chat_path(root, _CONVERSATION_FILE)
    for call_number in range(made + 1, made + limit + 1):
        reply = document_reshaping_service.stream_reply_to_files(
            bound=bound, messages=messages, files=(_build_chat_path(root, _THINKING_FILE), _build_chat_path(root, _CALL_FILE)),
            log=log, label=_LABEL)
        if document_reshaping_service.is_looped(reply):
            messages.append(HumanMessage(content=_LOOPED_ASK))
            continue
        messages.append(reply)
        tool_calls = reply.tool_calls if isinstance(reply, AIMessage) else []
        document_reshaping_service.log_turn(log_path, {"call": call_number, "text": str(reply.content)[:4_000],
                                                       "tools": [{"name": call.get("name"), "args": call.get("args")}
                                                                 for call in tool_calls]})
        if not tool_calls:
            worker_message_service.record_reply(messages_path, str(reply.content) or _OUT_OF_CALLS)
            document_reshaping_service.write_conversation_file(conversation_path, messages, {})
            return
        for call in tool_calls:
            answer = _answer_tool(paths, state, call)
            log.info(f"{_LABEL} · {call.get('name')} · {answer.splitlines()[0][:160]}")
            messages.append(ToolMessage(content=answer, tool_call_id=call.get("id", "")))
            document_reshaping_service.log_turn(log_path, {"call": call_number, "tool": call.get("name"),
                                                           "result": answer[:4_000]})
        document_reshaping_service.write_conversation_file(conversation_path, messages, {})
    worker_message_service.record_reply(messages_path, _OUT_OF_CALLS)


def _read_medium_workflow(output_root: Path) -> str:
    """The run's workflow, for the instruction it reads: `video`, `audio` or `pdf`, by the medium phase 2 recorded."""

    path = output_root / output_contract.LOOP_ITEMS_FILE
    try:
        medium = str(json.loads(path.read_text(encoding="utf-8")).get("medium") or "")
    except (OSError, ValueError):
        medium = ""
    for workflow in ("pdf", "audio"):
        if medium.startswith(workflow):
            return workflow

    return "video"


def answer_waiting_messages(paths: RunPaths,
                            model: BaseChatModel,
                            log: Any) -> int:
    """Answer what waits, and what comes in while it answers; how many turns it took."""

    root = paths.output_root
    system_prompt_builder.set_run_workflow(_read_medium_workflow(root))
    messages_path = _build_chat_path(root, _MESSAGES_FILE)
    saved = document_reshaping_service.read_conversation_file(_build_chat_path(root, _CONVERSATION_FILE))
    messages = saved[0] if saved else [SystemMessage(content=system_prompt_builder.build_system_prompt(INSTRUCTION_STEP))]
    bound = model.bind_tools(build_tool_specs())
    turns = 0
    while worker_message_service.list_waiting(messages_path):
        state, is_changed = _read_document_state(paths)
        _write_document_state(paths, state)
        if is_changed:
            messages.append(HumanMessage(content=_DOCUMENT_CHANGED))
        conversation_message_service.add_waiting_to_conversation(messages_path, messages)
        _run_turn(paths, bound, messages, state, log)
        turns += 1

    return turns


def describe_chat(output_root: Path,
                  now: float) -> dict:
    """The chat for the page: what the person asked and the answers, the worker's steps, thinking, speed and what it does."""

    files = document_pass_service.describe_worker_files(log_path=_build_chat_path(output_root, _LOG_FILE),
                                                        thinking=_build_chat_path(output_root, _THINKING_FILE),
                                                        call_path=_build_chat_path(output_root, _CALL_FILE),
                                                        messages_path=_build_chat_path(output_root, _MESSAGES_FILE),
                                                        now=now)

    return {**files, "is_worker_running": _is_worker_alive(output_root)}


def set_conversation_aside(output_root: Path) -> None:
    """Start the chat again: its files move into a numbered folder beside it, and the next message starts a new
    conversation. Nothing moves while the worker runs, since it would write them again."""

    if _is_worker_alive(output_root):
        return
    names = [_MESSAGES_FILE, f"{Path(_MESSAGES_FILE).stem}.state.json", _LOG_FILE, _THINKING_FILE, _CALL_FILE,
             _CONVERSATION_FILE]
    earlier = output_root / CHAT_DIR / _EARLIER_DIR
    number = len(list(earlier.iterdir())) + 1 if earlier.is_dir() else 1
    kept = earlier / str(number)
    for name in names:
        path = _build_chat_path(output_root, name)
        if path.is_file():
            kept.mkdir(parents=True, exist_ok=True)
            path.replace(kept / name)
