"""Phase 4 of a direct-draft video run (the Fast speed's workflow): one conversation of phase 4's model over the draft
phase 3 wrote (`03_draft.md`), and the program builds the document from its answer.

The model is shown the draft as an outline only: each section's id and heading, the first line of its text, and the
pictures it places. It may look at a frame and cut, mark and mask the picture a picture line places (the quality
workflow's frame tools, carried out by the same code), and ends by submitting the title, the summary, the agenda and the
heading changes it wants (`schemas/direct_draft_outline.py`). A picture line whose frame it did not cut is left out. The
program then applies the heading changes, puts each cut where its picture line stood, builds the summary, the agenda and
the table of contents, and publishes the folder as a quality run's is. The submitted answer is kept in
`04_writing/direct-draft-outline.json`, so a resume builds the document again without asking. Nothing the model wrote
is checked.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ValidationError

import config
from chains import chain_runner
from models import output_contract
from models.draft_section_spec import DraftSectionSpec
from models.illustration_context import IllustrationContext
from models.run_context import RunContext
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from schemas import direct_draft_outline
from schemas import illustration_actions
from services import call_log_service
from services import call_record_service
from services import chat_model_service
from services import direct_draft_batch_service
from services import document_file_service
from services import document_folder_service
from services import fast_turn_service
from services import illustration_action_service
from services import ingesting_profile_service
from services import phase_file_service
from services import run_tags_service
from services import write_conversation_service
from utils import chain_response_utils
from utils import document_block_utils
from utils import document_heading_utils
from utils import document_text_utils
from utils import draft_structure_utils
from utils import json_store_utils
from utils import message_content_utils

__all__ = ["PHASE", "PROMPT_PHASE", "SUBMIT_TOOL", "FRAME_TOOLS", "set_model_factory", "build_outline_path",
           "build_tool_specs", "build_messages", "build_document", "write_document"]

PHASE = "finalize"
# The instruction's entry in `agent-context/context_map.json`
PROMPT_PHASE = "direct_draft_outline"
SUBMIT_TOOL = "submit_outline"
# The quality workflow's frame tools this call is offered: it looks at a frame, cuts the picture, and marks or masks it
FRAME_TOOLS = ("look", "cut", "mark", "mask", "undo_cut")
_ASK_FOR_SUBMIT = (f"Call `{SUBMIT_TOOL}` with the title, the summary, the agenda and the heading changes when your "
                   f"pictures are cut; otherwise go on with the next picture.")
# What a call that never submitted leaves the document with: the draft as it was written, under the recording's name
_NOTHING_SUBMITTED = "the calls ran out before {tool} was called; the draft is published as written, under the recording's name"
# A tool answer's longest text kept in the panel's step
_STEP_RESULT_CHARS = 2_000

MODEL_FACTORY: Callable[[str], Any] = chat_model_service.get_named_model


def set_model_factory(factory: Callable[[str], Any]) -> Callable[[str], Any]:
    """The test seam: a suite never needs a model host. Returns the factory it replaced."""

    global MODEL_FACTORY
    previous, MODEL_FACTORY = MODEL_FACTORY, factory

    return previous


def build_outline_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.WRITING_DIR_NAME / output_contract.DIRECT_DRAFT_OUTLINE_FILE


def _build_tool_spec(name: str,
                     schema: type[BaseModel]) -> dict[str, Any]:
    """One tool as the model sees it: its name, its schema's docstring on one line, its parameters."""

    return {"type": "function", "function": {"name": name, "description": " ".join((schema.__doc__ or "").split()),
                                             "parameters": schema.model_json_schema()}}


def build_tool_specs() -> list[dict[str, Any]]:
    """The frame tools and the submit tool, as the model sees them."""

    specs = [_build_tool_spec(name, illustration_actions.ACTIONS[name]) for name in FRAME_TOOLS]

    return [*specs, _build_tool_spec(SUBMIT_TOOL, direct_draft_outline.SubmitOutline)]


def build_messages(paths: RunPaths,
                   recording: str) -> list[Any]:
    """The instruction, then the recording's name, its tags and the draft's outline."""

    path = direct_draft_batch_service.build_draft_path(paths)
    sections = draft_structure_utils.parse_sections(path.read_text(encoding="utf-8") if path.is_file() else "")
    outline = draft_structure_utils.render_outline(sections, config.settings.video.direct_draft.first_line_chars)
    tags = run_tags_service.read_tags(paths.output_root)
    tags_line = f"\nThe owner tagged it: {', '.join(tags)}. Read the tags as a hint of what the recording is.\n" if tags else ""
    pictures = sum(len(draft_structure_utils.list_pictures(section)) for section in sections)
    human = (f"## Finish the draft of this recording\n\nThe recording is {recording}.{tags_line}\n"
             f"### The draft: {len(sections)} sections and {pictures} pictures\n\n{outline}")

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(PROMPT_PHASE)),
            HumanMessage(content=[{"type": "text", "text": human}])]


def _build_context(ctx: RunContext) -> IllustrationContext:
    """What the frame tools work in: the run's paths, the recording, and the frames phase 2 took."""

    frames_file = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"])

    return IllustrationContext(paths=ctx.paths, video_path=ctx.paths.repository / ctx.paths.module,
                               frames={item["slug"]: item for item in frames_file.get("items", [])},
                               min_cut_pixels=config.settings.document.min_cut_pixels, chapter="")


def _run_tool_call(tool_call: dict,
                   context: IllustrationContext,
                   store: dict[str, Any]) -> tuple[str, Path | None, dict | None]:
    """One tool call of a reply: the answer the model is told, the frame it is shown after it, and, for a submit that
    fits its schema, the answer submitted."""

    name, args = str(tool_call.get("name") or ""), tool_call.get("args") or {}
    if name == SUBMIT_TOOL:
        try:
            submitted = direct_draft_outline.SubmitOutline.model_validate(args).model_dump()
        except ValidationError as exc:
            return f"Refused: the arguments do not fit {SUBMIT_TOOL}: {exc.errors()[0].get('msg', exc)}", None, None

        return "Submitted.", None, submitted
    if name not in FRAME_TOOLS:
        return f"`{name}` is not offered here; the tools are {', '.join([*FRAME_TOOLS, SUBMIT_TOOL])}.", None, None
    result = illustration_action_service.run_action(context, store, name, args)
    document_file_service.write_image_store(context.paths, store)

    return result.text, result.frame_path, None


def _run_conversation(ctx: RunContext,
                      context: IllustrationContext,
                      store: dict[str, Any],
                      model: Any,
                      messages: list[Any]) -> dict | None:
    """The conversation: a reply, its tool calls carried out, the frames it asked for shown, until it submits; the
    answer submitted, or None when `outlineCalls` calls were made without one."""

    for call in range(config.settings.video.direct_draft.outline_calls):
        reply = chain_runner.invoke_reply(model=model,
                                          messages=messages)
        messages.append(reply)
        illustration_action_service.mark_looks_seen(store)
        steps, frames, submitted = [], [], None
        for tool_call in reply.tool_calls or []:
            answer, frame, accepted = _run_tool_call(tool_call, context, store)
            messages.append(ToolMessage(content=answer, tool_call_id=tool_call.get("id") or str(tool_call.get("name"))))
            frames += [frame] if frame is not None and frame.is_file() else []
            submitted = accepted or submitted
            args = tool_call.get("args") or {}
            steps.append({"call": call, "tool": str(tool_call.get("name") or ""),
                          "summary": str(args.get("frame") or args.get("cut") or ""),
                          "args": json.dumps(args, ensure_ascii=False), "result": answer[:_STEP_RESULT_CHARS]})
        fast_turn_service.append_turn(output_root=ctx.paths.output_root,
                                      slug=output_contract.FAST_DOCUMENT_SLUG,
                                      turn={"call": call, "thinking": message_content_utils.read_reasoning(reply),
                                            "text": message_content_utils.read_message_text(reply.content),
                                            "steps": steps})
        if submitted is not None:
            return submitted
        if frames:
            messages.append(HumanMessage(content=[{"type": "text", "text": "The frame you asked for:"},
                                                  *write_conversation_service.encode_tool_frames(frames)]))
        if not reply.tool_calls:
            messages.append(HumanMessage(content=_ASK_FOR_SUBMIT))

    return None


def _list_cut_files(store: dict[str, Any]) -> dict[str, list[str]]:
    """The files of the cuts still in use, by the frame each was cut from, each frame's cuts in the order they were made."""

    live = document_file_service.list_cuts_in_use(store)
    ordered = sorted(live.items(), key=lambda item: int(item[0].rsplit("-", 1)[1]))
    files: dict[str, list[str]] = {}
    for _, cut in ordered:
        files.setdefault(str(cut.get("frame") or ""), []).append(str(cut["file"]))

    return files


def _move_headings_down(sections: list[DraftSectionSpec]) -> None:
    """Chapters are `##` in the document, under its `#` title: a draft whose headings start at `#` moves down one level."""

    levels = [section.level for section in sections if section.level]
    if levels and min(levels) < 2:
        for section in sections:
            section.level = min(section.level + 1, 6) if section.level else 0


def _render_summary(summary: list[str],
                    heading: str) -> list[str]:
    """The summary: its first sentence, the conclusion, as a paragraph, and the rest as a list."""

    lines = [line.strip() for line in summary if line.strip()]
    if not lines:
        return []
    rest = [f"- {line}" for line in lines[1:]]

    return [f"## {heading}", "", lines[0], "", *rest, *([""] if rest else [])]


def _render_agenda(agenda: list[dict],
                   sections: dict[str, DraftSectionSpec],
                   anchors: dict[str, str],
                   heading: str) -> list[str]:
    """One numbered line for each chapter the answer names, linked to its heading. A line naming a heading the draft no
    longer has is left out."""

    kept = [(sections[line["heading"].strip().upper()], str(line.get("text") or "").strip()) for line in agenda
            if line["heading"].strip().upper() in sections]
    if not kept:
        return []
    lines = [f"## {heading}", ""]
    for number, (section, text) in enumerate(kept, start=1):
        link = f"[{section.title}](#{anchors[section.id]})"
        lines.append(f"{number}. {link}: {text}" if text else f"{number}. {link}")

    return [*lines, ""]


def _render_contents(sections: list[DraftSectionSpec],
                     anchors: dict[str, str],
                     heading: str) -> list[str]:
    """The table of contents as a list a reader clicks: each chapter, and under it each of its sections."""

    lines = [f"## {heading}", ""]
    for section in sections:
        lines.append(f"{'' if section.level <= 2 else '  '}- [{section.title}](#{anchors[section.id]})")

    return [*lines, ""]


def build_document(sections: list[DraftSectionSpec],
                   outline: dict,
                   cut_files: dict[str, list[str]],
                   headings: Any) -> str:
    """The document: the title, the summary, the agenda and the contents the answer and the headings give, then the
    draft's sections with each picture line replaced by its cut's figure. `headings` is the document language's set
    of fixed headings (`document_heading_utils.select_headings`)."""

    # STEP 1: the headings the draft prints, and the anchor each one gets
    _move_headings_down(sections)
    printed = [section for section in sections if section.level]
    by_id = {section.id: section for section in printed}
    summary = _render_summary(outline["summary"], headings.executive_summary)
    top = [outline["title"].strip()] + ([headings.executive_summary] if summary else [])
    # The agenda's and the contents' own headings come before the draft's, so their anchors are counted first
    has_agenda = any(line["heading"].strip().upper() in by_id for line in outline["agenda"])
    top += ([headings.agenda] if has_agenda else []) + ([headings.contents] if printed else [])
    found = document_text_utils.build_heading_anchors(top + [section.title for section in printed])
    anchors = {section.id: anchor for section, anchor in zip(printed, found[len(top):])}

    # STEP 2: the top of the document, then the draft with its pictures
    lines = [f"# {outline['title'].strip()}", "", *summary,
             *_render_agenda(outline["agenda"], by_id, anchors, headings.agenda),
             *(_render_contents(printed, anchors, headings.contents) if printed else [])]
    body = draft_structure_utils.replace_pictures(draft_structure_utils.render_sections(sections), cut_files)

    return "\n".join(lines) + "\n" + body


def _publish(ctx: RunContext,
             markdown: str,
             store: dict[str, Any]) -> Path:
    """`04_document.md` and the published folder from the document, as a quality run's: every placed figure's cut copied
    into the folder's `resources/`, and the provenance written. The published document's path."""

    text = document_text_utils.hide_secret_query_values(markdown)
    ctx.paths.summary.write_text(text, encoding="utf-8")
    cuts = store.get("cuts") or {}
    blocks, _ = document_block_utils.convert_placed_images(document_block_utils.split_into_blocks(text, {}), cuts)
    folder_store = illustration_action_service.build_empty_store(blocks)
    folder_store.update({"looks": store.get("looks") or {}, "cuts": cuts, "nextCut": int(store.get("nextCut") or 1)})
    frames_file = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"])

    return document_folder_service.write_document_folder(
        ctx.paths, folder_store, {"source": output_contract.DRAFT_FILE}, {},
        {item["slug"]: item["timestamp"] for item in frames_file.get("items", [])})


def _request_outline(ctx: RunContext,
                     recording: str,
                     store: dict[str, Any]) -> dict:
    """The conversation, then its answer kept on disk; the answer, or an empty one named for the recording when no
    answer came."""

    # STEP 1: the model, with the frame tools and the submit tool, and the call's record on disk
    model_name = config.settings.resolve_phase_model_name(PHASE)
    model = MODEL_FACTORY(model_name).bind_tools(build_tool_specs())
    messages = build_messages(ctx.paths, recording)
    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
    call_record_service.update_place(windowTokens=ctx.phase_windows.get(PHASE))
    fast_turn_service.start_turns(ctx.paths.output_root, output_contract.FAST_DOCUMENT_SLUG)
    illustration_action_service.clear_last_call(store)

    # STEP 2: the conversation
    ctx.log.info(f"phase 4: a direct-draft run, one conversation of {model_name} over the draft's outline")
    started = time.monotonic()
    submitted = _run_conversation(ctx, _build_context(ctx), store, model, messages)
    ctx.log.info(f"phase 4: the conversation ended in {time.monotonic() - started:.0f} s")
    if submitted is None:
        ctx.log.warning(f"phase 4: {_NOTHING_SUBMITTED.format(tool=SUBMIT_TOOL)}")
        submitted = {"title": recording, "summary": [], "agenda": [], "changes": []}

    # STEP 3: the answer kept before anything is built from it, and the call on disk as every call is
    json_store_utils.write_json(build_outline_path(ctx.paths), submitted)
    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, output_contract.FAST_DOCUMENT_SLUG), {
        "phase": PHASE, "slug": output_contract.FAST_DOCUMENT_SLUG, "attempt": 1, "model": model_name,
        "request": {"system": system_text, "human": human_text}, "response": {"content": json.dumps(submitted)}})

    return submitted


def write_document(ctx: RunContext,
                   recording: str) -> Path:
    """The conversation, or the answer a resume finds kept, then the document built from it and published; the
    published document's path."""

    # STEP 1: an answer a killed run already got is kept: the conversation is the phase's whole cost
    store = {**illustration_action_service.build_empty_store([]), **document_file_service.read_image_store(ctx.paths)}
    outline_path = build_outline_path(ctx.paths)
    if outline_path.is_file():
        ctx.log.info(f"phase 4: the conversation ran before; building the document from {outline_path.name} again")
        outline = json_store_utils.read_json(outline_path)
    else:
        outline = _request_outline(ctx, recording, store)

    # STEP 2: the heading changes, each refused one named in the log, then the document
    draft = direct_draft_batch_service.build_draft_path(ctx.paths).read_text(encoding="utf-8")
    sections = draft_structure_utils.parse_sections(draft)
    for refusal in draft_structure_utils.apply_changes(sections, outline["changes"]):
        ctx.log.warning(f"phase 4: heading change skipped, {refusal}")
    headings = document_heading_utils.select_headings(ingesting_profile_service.read_document_language(ctx.paths.output_root))
    markdown = build_document(sections, outline, _list_cut_files(store), headings)

    # STEP 3: published
    document = _publish(ctx, markdown, store)
    ctx.log.info(f"phase 4: published as {document.parent.name}/{document.name}")

    return document
