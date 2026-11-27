"""What a run asks of phase 7: a clean up, a rewrite or a knowledge pass over its published document, and the person's
wishes for it. Read by the run's status, the reset and the run line; it loads no model
library, so the page and `i2k mcp` read it at no cost. The passes themselves are `document_reshaping_service` and
`document_knowledge_service`."""

from __future__ import annotations

import json
from pathlib import Path

import config as app_config
from models import output_contract
from services import worker_message_service
from utils import cleanup_block_utils
from utils import document_edit_utils
from utils import json_lines_utils

__all__ = ["describe_parts", "find_pass_input", "SOURCE_ORIGINAL", "SOURCE_CURRENT", "DOCUMENT_SOURCES",
           "read_document_pass","write_document_pass", "describe_pass", "build_reshaping_dir","build_report_path",
           "is_pass_done", "find_first_before", "list_document_versions", "build_part_draft_path",
           "build_part_thinking_path", "build_part_call_path", "describe_part_activity", "describe_worker_files",
           "list_worker_steps_of_log", "list_worker_turns","build_part_messages_path", "BEFORE_FILE", "AFTER_FILE",
           "EARLIER_DIR", "REQUESTS_DIR", "SYSTEM_PROMPT_FILE", "BATCH_LOG_GLOB"]

# The document as it was before a pass, kept in phase 7's folder
BEFORE_FILE = "before.md"
# The document a pass left, kept in its folder once another pass sets it aside
AFTER_FILE = "after.md"
# The document as a knowledge pass left it at the end of each part, `parts/part-NNN.md` in the pass's folder
_PARTS_DIR = "parts"
# Each part's model call under way, its thinking so far, `thinking/part-NNN.md` in the pass's folder
_THINKING_DIR = "thinking"
_MESSAGES_DIR = "messages"
# How much of a part's live panel is sent: its thinking's last characters, each step's arguments and answer's start
_THINKING_SHOWN = 30_000
# The line the streamed reader writes into the thinking file once the answer itself streams (`chain_runner`)
_ANSWER_HEADING = "[the answer so far]"
_ARGS_SHOWN = 300
_ITEMS_SHOWN = 10
_RESULT_SHOWN = 600
# where a part's conversation is saved after every turn, by document_reshaping_service
_CONVERSATIONS_DIR = "conversations"
# The edits a step's one-line summary names before "…"
_EDITS_NAMED = 6
# A finished pass's files, set aside when another pass runs over the document it left
EARLIER_DIR = "earlier"
# Each batch's first message and the pass's instructions, as its worker was sent them, and each batch's turns
REQUESTS_DIR = "requests"
SYSTEM_PROMPT_FILE = "system-prompt.md"
BATCH_LOG_GLOB = "batch-*.jsonl"
# The tool a part's worker drops results from its conversation with (`document_reshaping_service.PRUNE_TOOL`, which
# imports this module), counted per part from the turn log
_PRUNE_TOOL = "prune_results"
# A pass's state, which names the pass (`document_reshaping_service` writes it)
_STATE_FILE = "state.json"
_PASS_TITLES = {output_contract.DOCUMENT_PASS_CLEANUP: "clean up", output_contract.DOCUMENT_PASS_REWRITE: "rewrite",
                output_contract.DOCUMENT_PASS_KNOWLEDGE: "knowledge reshaping"}
_WISHES_LIMIT = 4_000
# What a pass starts from (the owner, asked when a pass runs again): the original, the document before phase 7's first
# pass, the default; or the current, the document the last pass left, so passes stack
SOURCE_ORIGINAL = "original"
SOURCE_CURRENT = "current"
DOCUMENT_SOURCES = (SOURCE_ORIGINAL, SOURCE_CURRENT)


def describe_pass(name: str) -> str:
    return _PASS_TITLES.get(name, name)


def build_reshaping_dir(output_root: Path) -> Path:
    """Phase 7's folder: under its name from before the rename when a run made before it has one and none under the
    new name, so that run's passes, parts and report still read and a resume goes on in it."""

    folder = output_root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    retired = output_root / output_contract.RETIRED_DOCUMENT_RESHAPING_DIR_NAME

    return retired if retired.is_dir() and not folder.exists() else folder


def build_report_path(output_root: Path) -> Path:
    return build_reshaping_dir(output_root) / output_contract.DOCUMENT_RESHAPING_FILE


def build_part_draft_path(output_root: Path,
                          number: int) -> Path:
    return build_reshaping_dir(output_root) / _PARTS_DIR / f"part-{number:03d}.md"


def build_part_thinking_path(output_root: Path,
                             number: int) -> Path:
    """The thinking of part `number`'s model call under way, written as it streams (the owner could not tell a worker
    thinking for 12 minutes from one stuck)."""

    return build_reshaping_dir(output_root) / _THINKING_DIR / f"part-{number:03d}.md"


def read_document_pass(output_root: Path) -> dict[str, str] | None:
    """The pass this run asked phase 7 for, {"pass", "wishes", "source"}, or None when it asked for none. A request
    written before the source was asked starts from the original, the default."""

    path = output_root / output_contract.RUN_DOCUMENT_PASS_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("pass") not in output_contract.DOCUMENT_PASSES:
        return None
    source = data.get("source") if data.get("source") in DOCUMENT_SOURCES else SOURCE_ORIGINAL

    return {"pass": data["pass"], "wishes": str(data.get("wishes") or ""), "source": source}


def write_document_pass(output_root: Path,
                        name: str,
                        wishes: str,
                        source: str = "") -> None:
    if name not in output_contract.DOCUMENT_PASSES:
        raise ValueError(f"document pass {name!r} is none of {', '.join(output_contract.DOCUMENT_PASSES)}")
    if source and source not in DOCUMENT_SOURCES:
        raise ValueError(f"document source {source!r} is none of {', '.join(DOCUMENT_SOURCES)}")
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / output_contract.RUN_DOCUMENT_PASS_FILE).write_text(
        json.dumps({"pass": name, "wishes": wishes.strip()[:_WISHES_LIMIT], "source": source or SOURCE_ORIGINAL},
                   indent=2) + "\n", encoding="utf-8")


def find_pass_input(output_root: Path,
                    source: str,
                    published: Path) -> Path:
    """The document a pass starts from: the original, as it was before phase 7's first pass, or the published document
    as the last pass left it. The original is the published document while no pass ran."""

    first = find_first_before(output_root)

    return first if source == SOURCE_ORIGINAL and first is not None else published


def is_pass_done(output_root: Path) -> bool:
    """Whether the pass the run asks for already ran to its report: the report written after the pass was last asked.
    A resume of a finished run then leaves the document alone; asking again (`run --document-pass`, the page's Clean up
    or rewrite) writes the request anew, and the pass runs over the document as it is now."""

    report = build_report_path(output_root)
    asked = output_root / output_contract.RUN_DOCUMENT_PASS_FILE

    return report.is_file() and asked.is_file() and report.stat().st_mtime >= asked.stat().st_mtime


def _list_pass_dirs(output_root: Path) -> list[Path]:
    """Each pass's folder, oldest first: the passes set aside under `earlier/<n>/`, then the latest, phase 7's folder."""

    folder = build_reshaping_dir(output_root)
    earlier = sorted((folder / EARLIER_DIR).glob(f"*/{BEFORE_FILE}"), key=lambda path: int(path.parent.name))
    latest = [folder] if (folder / BEFORE_FILE).is_file() else []

    return [path.parent for path in earlier] + latest


def _read_pass_state(pass_dir: Path) -> dict:
    path = pass_dir / _STATE_FILE

    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _read_pass_name(pass_dir: Path) -> str:
    return describe_pass(str(_read_pass_state(pass_dir).get("pass", "")))


def _count_part_progress(pass_dir: Path,
                         number: int,
                         now: float) -> tuple[int, int, int, float | None, float | None]:
    """A part's model calls, compactions and prunes (its worker dropping results it no longer needs) so far, and when it
    started and last moved: its request is saved before its first call (a pass started before that has only its turns),
    and each reply, tool result, compaction and call's speed is a row with its time."""

    rows = json_lines_utils.read_json_lines(pass_dir / f"batch-{number:03d}.jsonl")
    times = [float(row["at"]) for row in rows if isinstance(row.get("at"), (int, float))]
    request = pass_dir / REQUESTS_DIR / f"batch-{number:03d}.md"
    if request.is_file():
        times.append(min(request.stat().st_mtime, now))
    calls = sum(1 for row in rows if "tools" in row)
    compactions = sum(1 for row in rows if "compacted" in row)
    prunes = sum(1 for row in rows if row.get("tool") == _PRUNE_TOOL)

    return calls, compactions, prunes, (min(times) if times else None), (max(times) if times else None)


def _read_part_speed(pass_dir: Path,
                     number: int) -> float | None:
    """Part `number`'s average writing speed in output tokens a second: every call's written tokens over the time it
    spent writing them (the prompt's reading left out), from the speed rows of its turn log; None before its first call
    is recorded, as for a part that ran before calls were recorded there."""

    rows = json_lines_utils.read_json_lines(pass_dir / f"batch-{number:03d}.jsonl")
    measured = [row for row in rows if row.get("writing_seconds")]
    seconds = sum(float(row["writing_seconds"]) for row in measured)

    return round(sum(int(row["written_tokens"]) for row in measured) / seconds, 1) if seconds > 0 else None


def describe_parts(output_root: Path,
                   is_live: bool,
                   now: float) -> list[dict]:
    """The latest pass's parts in order, for the phase's bar and table: each part's blocks, the document as the part
    left it (a knowledge pass's draft, its path in the run folder; empty until the part is done), its status (done once its
    worker closed it; running, while the run lives, for the first part not closed that started; stopped for one that
    started with no run going; waiting else), its model calls, how often its conversation was compacted, and its time. Empty while no pass has cut its parts."""

    pass_dir = build_reshaping_dir(output_root)
    state = _read_pass_state(pass_dir)
    closed = state.get("closed") or {}
    sizes = _list_part_sizes(pass_dir, state.get("batches") or [])
    parts = []
    for number, block_ids in enumerate(state.get("batches") or [], start=1):
        calls, compactions, prunes, started, last = _count_part_progress(pass_dir=pass_dir,
                                                                         number=number,
                                                                         now=now)
        is_closed = str(number) in closed
        if is_closed:
            status = "done"
        elif started is None:
            status = "waiting"
        else:
            status = "running" if is_live else "stopped"
        end = last if is_closed or not is_live else now
        tokens, sections, figures = sizes[number - 1]
        speed = _read_part_speed(pass_dir, number)
        draft = build_part_draft_path(output_root, number)
        thinking = build_part_thinking_path(output_root, number)
        # a running part's model call under way: how long since its last step, and its thinking so far; or, while the
        # model host has no working model, that wait with what is wrong, which is no thinking
        host_wait = _read_host_wait(build_part_call_path(output_root, number)) if status == "running" else {}
        is_thinking = status == "running" and thinking.is_file() and last is not None and not host_wait
        parts.append({"number": number, "status": status, "blocks": len(block_ids), "tokens": tokens,
                      "sections": sections, "figures": figures, "calls": calls, "compactions": compactions,
                      "prunes": prunes, "tokens_per_second": speed,
                      "host_wait": host_wait.get("problem", ""),
                      "host_wait_seconds": max(0.0, now - host_wait["since"]) if host_wait else 0.0,
                      "document": draft.relative_to(output_root).as_posix() if draft.is_file() else "",
                      "thinking": thinking.relative_to(output_root).as_posix() if is_thinking else "",
                      "thinking_words": len(thinking.read_text(encoding="utf-8").split()) if is_thinking else 0,
                      "thinking_seconds": max(0.0, now - last) if is_thinking else 0.0,
                      "seconds": max(0.0, end - started) if started is not None and end is not None else 0.0})

    return parts


def _read_host_wait(path: Path) -> dict:
    """The wait for a model host with no working model that a part's call file `path` records: what is wrong and since
    when; empty while the call is not waiting."""

    call = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not call.get("host_wait") or call.get("first_piece") or call.get("ended"):
        return {}

    return {"problem": str(call.get("host_problem") or "the model does not work"), "since": float(call["host_wait"])}


def build_part_call_path(output_root: Path,
                         number: int) -> Path:
    """Part `number`'s last model call: when it started, and once it ended, its tokens and the speed the host reported."""

    return build_part_thinking_path(output_root, number).with_suffix(".json")


def _describe_speed(path: Path,
                    thinking: str,
                    now: float) -> dict:
    """The writing speed of a worker's last call, kept in `path`: the host's own count once the call ended; while it
    runs, or when the host reported none, estimated from the characters written so far."""

    call = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    started = call.get("started")
    if call.get("tokens_per_second"):
        return {"tokens_per_second": call["tokens_per_second"], "is_estimated": False,
                "prompt_tokens": call.get("prompt_tokens") or 0, "is_writing": False}
    if not started:
        return {"tokens_per_second": None, "is_estimated": True, "prompt_tokens": 0, "is_writing": False}
    # timed from the host's first piece: the prompt's reading before it writes nothing
    seconds = max(1.0, (call.get("ended") or now) - (call.get("first_piece") or started))
    estimated = len(thinking) / app_config.settings.document_reshaping.chars_per_token

    return {"tokens_per_second": round(estimated / seconds, 1), "is_estimated": True,
            "prompt_tokens": call.get("prompt_tokens") or 0, "is_writing": not call.get("ended")}


def _summarise_tool_args(args: object) -> str:
    """A step's arguments in one short line for the part's Steps tab: each edit's action and the block or section it
    names ("6 edits: replace K8.13, replace K8.14, …"), the section or block read, or the blocks a read spans; the
    arguments whole, a block's new text with them, ran to a screen of JSON per step."""

    if not isinstance(args, dict):
        return ""
    edits = args.get("edits")
    if isinstance(edits, list):
        named = [" ".join(str(part) for part in (edit.get("action"), edit.get("block") or edit.get("section")
                                                 or edit.get("heading")) if part)
                 for edit in edits if isinstance(edit, dict)]
        shown = ", ".join(named[:_EDITS_NAMED]) + (", …" if len(named) > _EDITS_NAMED else "")
        return f"{len(edits)} edit{'s' if len(edits) != 1 else ''}: {shown}"
    if args.get("first") or args.get("last"):
        return f"{args.get('first', '')} to {args.get('last', '')}"

    return str(args.get("section") or args.get("block") or args.get("summary") or "")[:_ARGS_SHOWN]


def _describe_doing(path: Path,
                    thinking: str) -> dict:
    """What a worker does now, and since when, from its call file `path` (the owner: "i want to know, if the agent now
    thinking or doing something else"): the host reading the prompt before its first piece, thinking, writing its
    answer (the tool call), or, once the call ended, the program running the tools before the next call. Empty for a
    worker whose calls recorded no start."""

    call = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not call.get("started"):
        return {"what": "", "since": None}
    if call.get("ended"):
        return {"what": "the program runs its tools", "since": call["ended"]}
    host_wait = _read_host_wait(path)
    if host_wait:
        return {"what": f"waiting for the model host: {host_wait['problem']}", "since": host_wait["since"]}
    if not call.get("first_piece"):
        return {"what": "reading the prompt", "since": call["started"]}
    if _ANSWER_HEADING in thinking:
        return {"what": "writing its answer", "since": call["first_piece"]}

    return {"what": "thinking", "since": call["first_piece"]}


def build_part_messages_path(output_root: Path,
                             number: int) -> Path:
    """The messages the person wrote to part `number`'s worker from its live panel, one a line, oldest first."""

    return build_reshaping_dir(output_root) / _MESSAGES_DIR / f"part-{number:03d}.jsonl"


def _shorten_value(inputs: object) -> object:
    """A call's `inputs` with each text cut at `_ARGS_SHOWN` characters and each list at `_ITEMS_SHOWN` items, so they
    stay whole JSON: cut as one text, a write's inputs ended mid-value and the page could show them only as raw text."""

    if isinstance(inputs, str):
        return inputs if len(inputs) <= _ARGS_SHOWN else f"{inputs[:_ARGS_SHOWN]}…"
    if isinstance(inputs, list):
        shown = [_shorten_value(item) for item in inputs[:_ITEMS_SHOWN]]
        return shown + ([f"… {len(inputs) - _ITEMS_SHOWN} more"] if len(inputs) > _ITEMS_SHOWN else [])
    if isinstance(inputs, dict):
        return {key: _shorten_value(item) for key, item in inputs.items()}

    return inputs


def list_worker_steps_of_log(log_path: Path) -> list[dict]:
    """Each tool call a worker made in order, with its time, its arguments and the start of what the program answered,
    from a log `document_reshaping_service.log_turn` writes."""

    steps: list[dict] = []
    for row in json_lines_utils.read_json_lines(log_path):
        if "tools" in row:
            steps.extend({"at": row.get("at"), "call": row.get("call"), "tool": str(tool.get("name") or ""),
                          "summary": _summarise_tool_args(tool.get("args")),
                          "args": json.dumps(_shorten_value(tool.get("args") or {}), ensure_ascii=False),
                          "result": ""}
                         for tool in row["tools"])
        elif "result" in row:
            # each answer fills the first step of its call and tool still unanswered
            step = next((step for step in steps if step["call"] == row.get("call") and step["tool"] == row.get("tool")
                         and not step["result"]), None)
            if step is not None:
                step["result"] = str(row["result"])[:_RESULT_SHOWN]

    return steps


def _list_reply_thinking(conversation_path: Path) -> list[str]:
    """The thinking of each reply a part's saved conversation holds, oldest first; none while the job writes it whole."""

    try:
        saved = json.loads(conversation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # a streamed reply is saved as the chunk it was joined into
    replies = [message.get("data") or {} for message in saved.get("messages", [])
               if message.get("type") in ("ai", "AIMessageChunk")]

    return [str((reply.get("additional_kwargs") or {}).get("reasoning_content") or "") for reply in replies]


def list_worker_turns(log_path: Path,
                      conversation_path: Path) -> list[dict]:
    """Each model call of a part as one turn (the owner: "rename steps to turns, and make each turn presist own
    thinking"): its time, its thinking read from the saved conversation, its words, and the tool calls it made with what
    the program answered. A call whose thinking looped kept no reply, so it takes no thinking from the conversation."""

    thinking = iter(_list_reply_thinking(conversation_path))
    turns: list[dict] = []
    steps = list_worker_steps_of_log(log_path)
    for row in json_lines_utils.read_json_lines(log_path):
        if "tools" not in row:
            continue
        is_looped = str(row.get("text") or "").startswith("(thinking stopped")
        turns.append({"at": row.get("at"), "call": row.get("call"),
                      "thinking": "" if is_looped else next(thinking, "")[-_THINKING_SHOWN:],
                      "text": str(row.get("text") or ""), "steps": [step for step in steps if step["call"] == row.get("call")
                                                                    and step["at"] == row.get("at")]})

    return turns


def describe_worker_files(log_path: Path,
                          thinking: Path,
                          call_path: Path,
                          messages_path: Path,
                          now: float) -> dict:
    """What a worker that writes these files has done and is doing, for its live panel: its steps, the thinking of the
    call under way (its last `_THINKING_SHOWN` characters), its speed, what it does now and the person's messages."""

    text = thinking.read_text(encoding="utf-8") if thinking.is_file() else ""

    return {"steps": list_worker_steps_of_log(log_path), "thinking": text[-_THINKING_SHOWN:],
            "thinking_at": thinking.stat().st_mtime if thinking.is_file() else None,
            "speed": _describe_speed(call_path, text, now), "doing": _describe_doing(call_path, text),
            "messages": worker_message_service.list_messages(messages_path)}


def describe_part_activity(output_root: Path,
                           number: int,
                           now: float) -> dict:
    """What part `number`'s worker has done and is doing, for its live panel (the owner: "when i click, its open
    details of thinking, doing, live what the agent is doing or thinking")."""

    pass_dir = build_reshaping_dir(output_root)
    log_path = pass_dir / f"batch-{number:03d}.jsonl"
    files = describe_worker_files(log_path=log_path, thinking=build_part_thinking_path(output_root, number),
                                  call_path=build_part_call_path(output_root, number),
                                  messages_path=build_part_messages_path(output_root, number), now=now)
    turns = list_worker_turns(log_path, pass_dir / _CONVERSATIONS_DIR / f"batch-{number:03d}.json")

    return {"number": number, **files, "turns": turns}


def _list_part_sizes(pass_dir: Path,
                   batches: list[list[int]]) -> list[tuple[int, int, int]]:
    """Each part's estimated tokens, its sections (its headings) and its figures, read from the document the pass
    started from, which keeps the block numbers the parts were cut by: the page showed a part's size as its block count,
    a word the person never sees anywhere else."""

    before = pass_dir / BEFORE_FILE
    if not before.is_file():
        return [(0, 0, 0) for _batch in batches]
    _front, body = document_edit_utils.split_front_matter(before.read_text(encoding="utf-8"))
    texts = {block["id"]: block["text"] for block in cleanup_block_utils.list_blocks(body)}
    chars_per_token = app_config.settings.document_reshaping.chars_per_token
    sizes = []
    for batch in batches:
        part = [texts.get(block_id, "") for block_id in batch]
        sizes.append((round(sum(len(text) for text in part) / chars_per_token),
                      sum(1 for text in part if cleanup_block_utils.is_heading(text)),
                      sum(len(cleanup_block_utils.list_figure_lines(text)) for text in part)))

    return sizes


def list_document_versions(output_root: Path,
                           document: Path) -> list[dict]:
    """Every version of the published document `document`, oldest first, each {"number", "label", "path"} with its
    number from 1 and its path inside the run folder: as phase 4 wrote it, then the document each phase 7 pass left, the last being the published one.
    Each pass set aside keeps the document it left as its `after.md`; a pass set aside before that was kept has its
    result as the next pass's `before.md`, which every pass then started from. A knowledge pass's drafts after each part
    are no version (the owner: "the parts dont need to be in this menu"): a running part's draft is read in its live
    panel. Empty while phase 7 never started: the published document is then the only version."""

    pass_dirs = _list_pass_dirs(output_root)
    if not pass_dirs:
        return []
    befores = [pass_dir / BEFORE_FILE for pass_dir in pass_dirs]
    results = [pass_dir / AFTER_FILE if (pass_dir / AFTER_FILE).is_file() else next_before
               for pass_dir, next_before in zip(pass_dirs, befores[1:])] + [document]
    rows = [{"label": "original", "path": befores[0]}]
    for pass_dir, result in zip(pass_dirs, results):
        is_finished = (pass_dir / output_contract.DOCUMENT_RESHAPING_FILE).is_file()
        rows.append({"label": f"after {_read_pass_name(pass_dir)}{'' if is_finished else ' (not finished)'}",
                     "path": result})

    return [{"number": number, "label": row["label"], "path": row["path"].relative_to(output_root).as_posix()}
            for number, row in enumerate(rows, start=1)]


def find_first_before(output_root: Path) -> Path | None:
    """The document as it was before phase 7's first pass: an earlier pass's copy when a later one ran over it, else
    this pass's; None when phase 7 never started."""

    folder = build_reshaping_dir(output_root)
    earlier = sorted((folder / EARLIER_DIR).glob(f"*/{BEFORE_FILE}"), key=lambda path: int(path.parent.name))
    if earlier:
        return earlier[0]

    return folder / BEFORE_FILE if (folder / BEFORE_FILE).is_file() else None
