"""What the worker of each phase saw, step by step, read back from what the run saved: the detail view's "What the
worker saw". Nothing is recorded for it and no model is called.

- **A call** (phases 2, 3, 6, a dry run of any phase): each attempt's folder, `logs/<session>/<phase>/<step>/attempt-N/`
  (`attempt_log_service.claim_attempt_dir`), or a dry run's `logs/<session>/dry-run/<phase>/<step>/`, holds the
  instructions (`system-prompt.md`) and the input (`human-message.md`); an image in the input is written
  `[image: N characters of data URI]`, and it is the frame the text before it last names (`Frame 12, …`).
- **A conversation** (phases 4 and 5): `logs/conversation.jsonl` holds each turn of each follow-up; the request it was
  sent is `04_writing/requests/<step>.md`, else `logs/requests/<step>.md` beside the conversation log, and its
  instructions `logs/system-prompt.md` there (`write_conversation_service` saves both; a run before that has neither).
- **Phase 7's batches**: `07_reshaping/batch-NNN.jsonl` holds each reply and tool result of one batch, its first message
  is `07_reshaping/requests/batch-NNN.md` and the pass's instructions `07_reshaping/system-prompt.md` (`07_cleanup/` in
  a run made before the rename)
  (`document_reshaping_service.save_request`; a pass started before that saved its turns alone).
- **A preview**: the instructions each of a phase's steps would get with the current files, built as a run builds them
  for a step with no fields of its own."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import config
from agent_client import path_sandbox
from agent_client.path_sandbox import SandboxError
from models import output_contract
from models.run_errors import PromptBuildError
from models.ui_errors import UnknownArtifactError
from prompts import system_prompt_builder
from prompts import write_messages
from services import attempt_log_service
from services import conversation_log_service
from services import document_reshaping_service
from services import document_knowledge_service
from services import document_pass_service

__all__ = ["list_worker_steps", "read_attempt", "build_instruction_previews",
           "INSTRUCTION_PHASE_NUMBERS", "INSTRUCTION_USED_BY"]

# Each instruction set of `agent-context/context_map.json`, by the phase whose worker it is sent to
INSTRUCTION_PHASE_NUMBERS = {
    "speech_structuring": 2, "frame_redundancy": 2,
    "preprocessing": 3, "frames_processing": 3, "critique": 3, "second_pass": 3, "open_questions": 3,
    "answer_questions": 3, "fast_frame_note": 3,
    "takeaway": 4, "parts": 4, "outline": 4, "contents": 4, "section": 4, "summary": 4, "write": 4,
    "draft_headings": 4, "table_of_contents": 4, "fast_document": 4,
    "review": 5,
    "critique_collection": 6,
    "document_cleanup": 7, "document_knowledge": 7, "document_chat": 7,
}
# The instruction sets only one medium's workflow sends: a video run never gets the audio ones, nor an audio run the frames'
AUDIO_ONLY_INSTRUCTIONS = {"speech_structuring", "preprocessing", "takeaway"}
VIDEO_ONLY_INSTRUCTIONS = {"frame_redundancy", "frames_processing", "critique", "second_pass", "open_questions",
                           "answer_questions", "fast_frame_note"}
# Who is sent each instruction set, at which stage: shown above its preview, so a reader knows what they are reading
_CALLS_ONLY = ("Sent alone only when `document.writeMode` is \"calls\"; in the default conversation its rules are part "
               "of `write`, under \"When the follow-up asks for {}\".")
INSTRUCTION_USED_BY = {
    "speech_structuring": "Phase 2 of an audio recording: one call of phase 2's model turns the transcript into "
                          "paragraphs. A video run never sends it.",
    "frame_redundancy": "Phase 2 of a video, stage 3 (redundancy model) and stage 4 (similarity recheck): the small "
                        "vision model gets a few frames a call and says which frame another kept frame holds whole.",
    "preprocessing": "Phase 3 of an audio recording whose profile declares preparation steps: each step's call of "
                     "phase 3's model.",
    "frames_processing": "Phase 3 of a video, the first call of each batch: phase 3's model writes each frame's notes.",
    "critique": "Phase 3 of a video, the follow-up after each batch's notes: the same model lists what went wrong in "
                "them and changes nothing.",
    "second_pass": "Phase 3 of a video, the follow-up after the critique: the same model fixes the notes by the "
                   "critique's rules.",
    "open_questions": "Phase 3 of a video, the follow-up after the repair: the same model lists what a reader still "
                      "cannot know from the notes.",
    "answer_questions": "Phase 3 of a video, the last follow-up of each batch: the same model answers those questions "
                        "from the notes, the speech and the frames.",
    "fast_frame_note": "Phase 3 of the fast processing workflow only: the small vision model describes each frame.",
    "write": "Phase 4, the one writing conversation (the default): phase 4's model gets it once and answers every "
             "follow-up with it: summary draft, agenda, each chapter's headings and text, final title.",
    "parts": "Phase 4, the agenda. " + _CALLS_ONLY.format("the agenda"),
    "draft_headings": "Phase 4, each chapter's draft headings. " + _CALLS_ONLY.format("the draft headings of a chapter"),
    "table_of_contents": "Phase 4, the table of contents. No run asks for it any more: the program builds the table "
                         "of contents itself.",
    "outline": "Phase 4, the outline of each batch of notes. Sent only when `document.writeMode` is \"calls\".",
    "section": "Phase 4, each chapter's text. " + _CALLS_ONLY.format("a chapter"),
    "summary": "Phase 4, the final title, executive summary and agenda. "
               + _CALLS_ONLY.format("the final title, executive summary and agenda"),
    "contents": "Phase 4, the document's title. " + _CALLS_ONLY.format("the final title"),
    "takeaway": "Phase 4 of a takeaway run only: one call writes what was agreed and what was left open.",
    "fast_document": "Phase 4 of the fast processing workflow only: one call writes the document from the frames' "
                     "descriptions.",
    "review": "Phase 5, the review conversation: phase 5's model lists what a reader still cannot know and resolves "
              "each point from the sources.",
    "critique_collection": "Phase 6, the critique collection: one call of phase 6's model words each group of critique "
                           "rules as one rule.",
    "document_cleanup": "Phase 7, only when the run asks for an LLM clean up or rewrite: one conversation per batch of "
                        "the published document cleans it up or rewrites it, with the person's wishes added at its end.",
    "document_chat": "The chat on a run's final document, in the Artifacts tab, in every workflow: one conversation that "
                     "reads and changes the published document as the person asks.",
    "document_knowledge": "Phase 7, only when the run asks for an LLM knowledge pass: one conversation per part of the "
                          "published document writes its facts into one section per subject, with the person's wishes "
                          "added at its end.",
}
# An attempt folder's phase, when it is not one of the six phases' own names
_EXTRA_FOLDER_PHASE_NUMBERS = {"frame_redundancy": 2, "speech_structuring": 2, "preprocessing": 3,
                               "fast_frame_note": 3, "takeaway": 4, "fast_document": 4}
_LOGS_DIR = "logs"
_DRY_RUN_DIR = "dry-run"
_HUMAN_MESSAGE_FILE = attempt_log_service.HUMAN_MESSAGE_FILE
_REQUESTS_DIR = "requests"
_CONVERSATION_LOGS = {4: output_contract.WRITE_CONVERSATION_LOG, 5: output_contract.REVIEW_CONVERSATION_LOG}
_IMAGE_PLACEHOLDER = re.compile(r"\[image: \d+ characters of data URI\]")
_FRAME_MENTION = re.compile(r"^(?:#+ )?Frame (\d+)\b", re.MULTILINE)
_ATTEMPT_NUMBER = re.compile(r"^attempt-(\d+)(?:\.(\d+))?$")
_CONVERSATION_PREFIX = "conversation:"
# Phase 7 keeps one conversation log per batch in its own folder, and a batch ends when its worker closes it
_PASS_PHASE = output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE]
_FINISH_BATCH_TOOL = "finish_batch"
_PASS_STATE_FILE = "state.json"
# Said above what a pass started before it saved its requests is shown, built again for it
_REBUILT_NOTE = ("> Built again now from the current instruction files and the pass's saved state: this pass started "
                 "before it saved what it sent. An instruction edited since, or a section count that has grown, differs "
                 "from what the worker got.")


def _read_folder_phase_number(name: str) -> int | None:
    return output_contract.PHASE_NUMBERS.get(name) or _EXTRA_FOLDER_PHASE_NUMBERS.get(name)


def _read_text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _read_json(path: Path) -> dict:
    """A small record the run wrote; one cut short by a kill reads as nothing, never as an error."""

    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def _describe_attempt_status(directory: Path,
                             is_dry_run: bool) -> str:
    """`accepted` or `refused` from the attempt's own decision, else its validation; a dry run is `dry run`."""

    if is_dry_run:
        return "dry run"
    decision = _read_json(directory / attempt_log_service.DECISION_FILE)
    if "accepted" in decision:
        return "accepted" if decision["accepted"] else "refused"
    validation = _read_json(directory / attempt_log_service.VALIDATION_FILE)
    if "ok" in validation:
        return "accepted" if validation["ok"] else "refused"

    return "no answer recorded"


def _list_attempt_folders(folder: Path) -> list[tuple[int, str, str, Path, bool]]:
    """Every saved call of the run: its phase number, its phase folder's name, its step, its folder and whether it is
    a dry run."""

    found = []
    for session in sorted((folder / _LOGS_DIR).glob("*/")):
        for phase_dir in sorted(session.iterdir()):
            if not phase_dir.is_dir():
                continue
            if phase_dir.name == _DRY_RUN_DIR:
                for dry_phase in sorted(path for path in phase_dir.iterdir() if path.is_dir()):
                    number = _read_folder_phase_number(dry_phase.name)
                    # a dry run of a phase-wide call writes straight into its phase folder
                    steps = [dry_phase] if (dry_phase / _HUMAN_MESSAGE_FILE).is_file() else \
                        sorted(path for path in dry_phase.iterdir() if path.is_dir())
                    found += [(number, dry_phase.name, step.name, step, True) for step in steps if number]
                continue
            number = _read_folder_phase_number(phase_dir.name)
            if number is None:
                continue
            for attempt in sorted(phase_dir.glob("**/attempt-*")):
                if (attempt / _HUMAN_MESSAGE_FILE).is_file() or (attempt / attempt_log_service.SYSTEM_PROMPT_FILE).is_file():
                    step = attempt.parent.relative_to(phase_dir).as_posix()
                    found.append((number, phase_dir.name, step if step != "." else phase_dir.name, attempt, False))

    return found


def _find_session_name(directory: Path) -> str:
    """The session folder (`logs/<session>/…`) an attempt belongs to."""

    parts = directory.parts

    return parts[parts.index(_LOGS_DIR) + 1] if _LOGS_DIR in parts else ""


def _sort_attempt_key(directory: Path) -> tuple:
    """Session by session, then attempt 1, 2, 3 and each re-entry after its attempt."""

    match = _ATTEMPT_NUMBER.match(directory.name)

    return (_find_session_name(directory), int(match.group(1)) if match else 0, int(match.group(2) or 0) if match else 0)


def _list_call_steps(folder: Path,
                     phase_number: int) -> list[dict[str, Any]]:
    """The phase's steps that were calls, each with its attempts, in the order the run first asked them."""

    by_step: dict[tuple[str, bool], list[Path]] = {}
    for number, phase_name, step, directory, is_dry_run in _list_attempt_folders(folder):
        if number == phase_number:
            by_step.setdefault((f"{phase_name}/{step}", is_dry_run), []).append(directory)
    steps = []
    for (key, is_dry_run), directories in by_step.items():
        directories.sort(key=_sort_attempt_key)
        attempts = [{"ref": directory.relative_to(folder).as_posix(),
                     "label": "dry run" if is_dry_run else directory.name.replace("-", " "),
                     "session": _find_session_name(directory),
                     "status": _describe_attempt_status(directory, is_dry_run)} for directory in directories]
        started = min(directory.stat().st_mtime for directory in directories)
        steps.append({"step": key.split("/", 1)[1], "phase_folder": key.split("/", 1)[0], "kind": "call",
                      "is_dry_run": is_dry_run, "attempts": attempts, "started": started})
    steps.sort(key=lambda step: step["started"])

    return steps


def _list_conversation_steps(folder: Path,
                             phase_number: int) -> list[dict[str, Any]]:
    """The follow-ups of the phase's conversation, in the order it first asked them, each with how it ended."""

    if phase_number not in _CONVERSATION_LOGS:
        return []
    order: list[str] = []
    status: dict[str, str] = {}
    for row in conversation_log_service.read_rows(folder / _CONVERSATION_LOGS[phase_number]):
        slug = str(row.get("followUp") or "")
        if not slug:
            continue
        if slug not in order:
            order.append(slug)
            status[slug] = "asked"
        if "accepted" in row:
            status[slug] = "accepted"
        elif "blocked" in row:
            status[slug] = "blocked"

    return [{"step": slug, "kind": "conversation", "is_dry_run": False,
             "attempts": [{"ref": f"{_CONVERSATION_PREFIX}{phase_number}:{slug}", "label": "conversation",
                           "session": "", "status": status[slug]}]} for slug in order]


def _list_frame_paths(folder: Path) -> dict[int, str]:
    """Each frame's file by its number, kept and dropped frames alike: as `02_frames.json` records it, else as found in
    `frames/` or `frames/dropped/`."""

    paths = {}
    # the files on disk first, so a frame is found before phase 2 has written its record; the record then wins
    for path in [*sorted((folder / "frames").glob("frame-*.jpg")), *sorted((folder / "frames" / "dropped").glob("frame-*.jpg"))]:
        number = path.stem.rsplit("-", 1)[-1]
        if number.isdigit():
            paths.setdefault(int(number), path.relative_to(folder).as_posix())
    record = _read_json(folder / output_contract.PHASE_FILES[2]["file"])
    for item in [*record.get("items", []), *record.get("droppedFrames", [])]:
        slug, path = str(item.get("slug", "")), item.get("path")
        if path and slug.rsplit("-", 1)[-1].isdigit():
            paths[int(slug.rsplit("-", 1)[-1])] = str(path)

    return paths


def _split_input(text: str,
                 frame_paths: dict[int, str],
                 image_frames: list[int] | None) -> list[dict[str, Any]]:
    """The input as text and image parts in order; each image is the frame `image_frames` names at its place, when the
    call saved them, else the frame the text before it last names."""

    segments: list[dict[str, Any]] = []
    last_frame: int | None = None
    position = 0
    for index, match in enumerate(_IMAGE_PLACEHOLDER.finditer(text)):
        before = text[position:match.start()]
        mentions = _FRAME_MENTION.findall(before)
        if image_frames is not None:
            last_frame = image_frames[index] if index < len(image_frames) else None
        elif mentions:
            last_frame = int(mentions[-1])
        if before.strip():
            segments.append({"kind": "text", "text": before})
        segments.append({"kind": "image", "frame": last_frame,
                         "path": frame_paths.get(last_frame) if last_frame is not None else None})
        position = match.end()
    if text[position:].strip():
        segments.append({"kind": "text", "text": text[position:]})

    return segments


def _read_image_frames(directory: Path) -> list[int] | None:
    """The frame number of each image the call sent, in order, when the call saved them (`images.json`)."""

    path = directory / attempt_log_service.IMAGES_FILE
    if not path.is_file():
        return None

    return [int(str(entry["slug"]).rsplit("-", 1)[-1]) for entry in json.loads(path.read_text(encoding="utf-8"))]


def _read_conversation(folder: Path,
                       phase_number: int,
                       slug: str) -> dict[str, Any]:
    """One follow-up of a conversation: its instructions and request when the run saved them, and its turns in order."""

    log_path = folder / _CONVERSATION_LOGS[phase_number]
    log_dir = log_path.parent
    request = (_read_text(folder / output_contract.WRITING_DIR_NAME / _REQUESTS_DIR / f"{slug}.md") if phase_number == 4
               else None) or _read_text(log_dir / _REQUESTS_DIR / f"{slug}.md")
    turns = []
    for row in conversation_log_service.read_rows(log_path):
        if row.get("followUp") != slug:
            continue
        if "said" in row or "thinking" in row or "calls" in row:
            turns.append({"kind": "turn", "thinking": row.get("thinking") or "", "said": row.get("said") or "",
                          "calls": row.get("calls") or [], "prompt_tokens": row.get("promptTokens"), "at": row.get("at")})
        elif "tool" in row:
            turns.append({"kind": "result", "tool": row["tool"], "result": row.get("result") or "", "at": row.get("at")})
        elif "blocked" in row:
            turns.append({"kind": "blocked", "text": str(row["blocked"]), "at": row.get("at")})
        elif "accepted" in row:
            turns.append({"kind": "accepted", "text": f"accepted after {row['accepted']} refused answer(s)", "at": row.get("at")})
        elif row.get("compacted"):
            turns.append({"kind": "compacted", "text": str(row.get("summary") or ""), "at": row.get("at")})

    return {"kind": "conversation", "phase": phase_number, "step": slug,
            "instructions": _read_text(log_dir / attempt_log_service.SYSTEM_PROMPT_FILE),
            "input": [{"kind": "text", "text": request}] if request else [], "findings": [], "turns": turns}


def _list_pass_batches(folder: Path) -> list[str]:
    """Phase 7's batches whose worker was sent something or answered, as `batch-001`, in order: a batch's request is
    saved before its first call, its turns after each reply."""

    pass_dir = document_pass_service.build_reshaping_dir(folder)
    names = {path.stem for path in pass_dir.glob(document_pass_service.BATCH_LOG_GLOB)}
    names |= {path.stem for path in (pass_dir / document_pass_service.REQUESTS_DIR).glob("batch-*.md")}

    return sorted(names)


def _list_pass_steps(folder: Path) -> list[dict[str, Any]]:
    """Phase 7's batches as steps, each one conversation, closed once its worker called `finish_batch`."""

    steps = []
    pass_dir = document_pass_service.build_reshaping_dir(folder)
    for name in _list_pass_batches(folder):
        rows = conversation_log_service.read_rows(pass_dir / f"{name}.jsonl")
        is_closed = any(row.get("tool") == _FINISH_BATCH_TOOL and not str(row.get("result", "")).startswith("Refused")
                        for row in rows)
        steps.append({"step": name, "kind": "conversation", "is_dry_run": False,
                      "attempts": [{"ref": f"{_CONVERSATION_PREFIX}{_PASS_PHASE}:{name}", "label": "conversation",
                                    "session": "", "status": "accepted" if is_closed else "asked"}]})

    return steps


def _read_pass_batch(folder: Path,
                     name: str) -> dict[str, Any]:
    """One phase 7 batch: the pass's instructions, the batch's first message, and each reply and tool result."""

    if name not in _list_pass_batches(folder):
        raise UnknownArtifactError(f"{_CONVERSATION_PREFIX}{_PASS_PHASE}:{name}")
    pass_dir = document_pass_service.build_reshaping_dir(folder)
    request = _read_text(pass_dir / document_pass_service.REQUESTS_DIR / f"{name}.md")
    instructions = _read_text(pass_dir / document_pass_service.SYSTEM_PROMPT_FILE)
    if instructions is None or request is None:
        instructions, request = _rebuild_pass_request(pass_dir, name, instructions, request)
    turns = []
    for row in conversation_log_service.read_rows(pass_dir / f"{name}.jsonl"):
        if "tool" in row:
            turns.append({"kind": "result", "tool": row["tool"], "result": row.get("result") or "", "at": row.get("at")})
        elif "text" in row or "tools" in row:
            calls = [{"name": call.get("name"), "args": call.get("args")} for call in row.get("tools") or []]
            turns.append({"kind": "turn", "thinking": "", "said": row.get("text") or "", "calls": calls,
                          "prompt_tokens": None, "at": row.get("at")})

    return {"kind": "conversation", "phase": _PASS_PHASE, "step": name,
            "instructions": instructions,
            "input": [{"kind": "text", "text": request}] if request else [], "findings": [], "turns": turns}


def _rebuild_pass_request(pass_dir: Path,
                          name: str,
                          instructions: str | None,
                          request: str | None) -> tuple[str | None, str | None]:
    """What a pass started before it saved its requests sent, built again from the current instruction files and the
    pass's state, each marked as built again; what was saved is kept as saved. Nothing to build from leaves it None."""

    state = _read_json(pass_dir / _PASS_STATE_FILE)
    number = int(name.rsplit("-", 1)[-1])
    if not state.get("batches") or number > len(state["batches"]):
        return instructions, request
    service = (document_knowledge_service if state.get("pass") == output_contract.DOCUMENT_PASS_KNOWLEDGE
               else document_reshaping_service)
    system_text, first_text = service.rebuild_request(state, number)

    return (instructions if instructions is not None else f"{_REBUILT_NOTE}\n\n{system_text}",
            request if request is not None else f"{_REBUILT_NOTE}\n\n{first_text}")


def list_worker_steps(folder: Path,
                      phase_number: int) -> list[dict[str, Any]]:
    """Every step of one phase whose worker the run saved, calls first in the order asked, then the conversation's
    follow-ups; phase 7's are its batches."""

    if phase_number == _PASS_PHASE:
        return _list_pass_steps(folder)
    steps = _list_call_steps(folder, phase_number)
    for step in steps:
        del step["started"]

    return steps + _list_conversation_steps(folder, phase_number)


def read_attempt(folder: Path,
                 ref: str) -> dict[str, Any]:
    """What one attempt (or one follow-up, `conversation:<phase>:<step>`) was sent: its instructions, its input as
    text and frames, and the findings that refused it."""

    # 1. a follow-up of a conversation
    if ref.startswith(_CONVERSATION_PREFIX):
        number, _, slug = ref[len(_CONVERSATION_PREFIX):].partition(":")
        if number == str(_PASS_PHASE):
            return _read_pass_batch(folder, slug)
        if not number.isdigit() or int(number) not in _CONVERSATION_LOGS or not slug:
            raise UnknownArtifactError(ref)

        return _read_conversation(folder, int(number), slug)

    # 2. a saved call: only a folder under logs/ that holds what the worker was sent
    try:
        directory = path_sandbox.resolve_within(folder, ref)
    except SandboxError as exc:
        raise UnknownArtifactError(ref) from exc
    if (not directory.is_relative_to(folder.resolve()) or _LOGS_DIR not in directory.relative_to(folder.resolve()).parts
            or not (directory / _HUMAN_MESSAGE_FILE).is_file() and not (directory / attempt_log_service.SYSTEM_PROMPT_FILE).is_file()):
        raise UnknownArtifactError(ref)
    validation = _read_json(directory / attempt_log_service.VALIDATION_FILE)
    human = _read_text(directory / _HUMAN_MESSAGE_FILE) or ""

    return {"kind": "call", "instructions": _read_text(directory / attempt_log_service.SYSTEM_PROMPT_FILE),
            "input": _split_input(human, _list_frame_paths(folder), _read_image_frames(directory)),
            "findings": [{"code": str(finding.get("code", "")), "detail": str(finding.get("detail", ""))}
                         for finding in validation.get("findings", [])],
            "turns": []}


def build_instruction_previews(phase_number: int,
                               is_audio: bool) -> list[dict[str, str]]:
    """The instructions each instruction set of the phase would send now to a run of that medium, built from the
    current files for a step with no fields of its own (a run adds its recording's profile and tags). One that cannot
    be built that way says why in place of its text."""

    left_out = VIDEO_ONLY_INSTRUCTIONS if is_audio else AUDIO_ONLY_INSTRUCTIONS
    previews = []
    for name in system_prompt_builder.list_phase_specifications(config.AGENT_CONTEXT_DIR):
        if INSTRUCTION_PHASE_NUMBERS.get(name) != phase_number or name in left_out:
            continue
        try:
            # `write` is sent with every follow-up's section added, as the conversation sends it
            workflow_item = {system_prompt_builder.WORKFLOW_FIELD: "audio" if is_audio else "video"}
            text = (write_messages.build_write_system_text("", [], not is_audio) if name == "write"
                    else system_prompt_builder.build_system_prompt(name, workflow_item))
            previews.append({"name": name, "used_by": INSTRUCTION_USED_BY.get(name, ""), "text": text, "error": ""})
        # A set whose rule needs a field of a real step cannot be shown without one: said, and the others still shown
        except PromptBuildError as exc:
            previews.append({"name": name, "used_by": INSTRUCTION_USED_BY.get(name, ""), "text": "", "error": str(exc)})

    return previews
