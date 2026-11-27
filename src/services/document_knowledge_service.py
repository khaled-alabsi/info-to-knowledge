"""Phase 7's knowledge pass: one worker writes the business facts of the published document into one section per
subject, a batch of the document at a time, in one pass over it.

The pass's state is the one every pass starts from (`document_reshaping_service.start_pass`: the document's numbered
blocks and its batches) with the knowledge document beside it: sections, each a heading and its blocks, named `K3` and
`K3.2`. Each batch's worker reads the knowledge outline, its batch's blocks, a section before it adds to it, and a
figure's image, and writes into the sections. After every tool the published document is written from the sections, so
a stopped run loses nothing written; the document it is made from stays in the state and in `before.md`.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from PIL import Image

import config as app_config
from models.run_paths import RunPaths
from prompts import system_prompt_builder
from services import document_reshaping_service
from services import document_folder_service
from services import document_pass_service
from utils import cleanup_block_utils
from utils import frame_message_utils
from utils import mermaid_text_utils

__all__ = ["PROMPT_PHASE", "start_knowledge", "build_tool_specs", "apply_section_edits", "render_knowledge",
           "run_batch", "write_report", "rebuild_request"]

# The instruction set `agent-context/context_map.json` holds for this pass
PROMPT_PHASE = "document_knowledge"
SECTION_ID_PREFIX = "K"
_SECTION_ID = re.compile(rf"^{SECTION_ID_PREFIX}?(\d+)$")
_BLOCK_ID = re.compile(rf"^{SECTION_ID_PREFIX}?(\d+)\.(\d+)$")
_TOP_HEADING = re.compile(r"^#{1,2} \S", re.MULTILINE)
# a fenced block whole, whose lines ("# install") are code, never a heading
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_FIGURE_TARGET = re.compile(r"\]\(([^)\s]+)")
_ACTIONS = ("new_section", "add", "replace", "remove", "rename", "remove_section", "merge", "split", "move")
# the edits that carry markdown, checked before they are applied
_TEXT_ACTIONS = ("new_section", "add", "replace")
# the edits that name a whole section; the others but new_section name a block
_SECTION_ACTIONS = ("add", "rename", "remove_section", "merge")
# A figure's image is sent at most this wide: wide enough to read its labels, small enough for the window
_FIGURE_MAX_WIDTH = 1_600
# A part's figures sent with its first message: narrower than one looked at alone, and together at most this many prompt
# tokens, so a part of 85 figures still fits the window beside its text (sent whole at 1,600 pixels, about 170,000)
_PART_FIGURE_WIDTH = 1_024
_PART_IMAGE_TOKENS = 60_000
# The first words of a section's first block the outline shows, so the worker can tell the subjects apart
_OUTLINE_PREVIEW_CHARS = 100


def _read_section_number(name: Any) -> int | None:
    match = _SECTION_ID.match(str(name).strip().strip("{}"))

    return int(match.group(1)) if match else None


def _read_block_address(name: Any) -> tuple[int, int] | None:
    match = _BLOCK_ID.match(str(name).strip().strip("{}"))

    return (int(match.group(1)), int(match.group(2))) if match else None


def _clean_heading(heading: Any) -> str:
    return str(heading or "").strip().lstrip("#").strip()


def _find_section(state: dict[str, Any],
                  number: int | None) -> dict[str, Any] | None:
    return next((section for section in state["sections"] if section["id"] == number), None)


def _list_source_figure_lines(state: dict[str, Any]) -> set[str]:
    return {line for block in state["blocks"] for line in cleanup_block_utils.list_figure_lines(block["text"])}


def _use_document_figure_lines(text: str,
                               figure_lines: set[str]) -> str:
    """`text` with each figure line that names a figure's file the document holds once written as the document's own
    line: a worker wrote its own caption for a figure it placed ("Figure on page 1024" for "Figure 2: ..."), was
    refused, and spent a turn finding the line to copy. A figure is named by its file, so its caption is the document's."""

    lines_by_file: dict[str, list[str]] = {}
    for line in figure_lines:
        found = _FIGURE_TARGET.search(line)
        if found:
            lines_by_file.setdefault(found.group(1), []).append(line)
    written = []
    for line in text.split("\n"):
        is_unknown_figure = bool(cleanup_block_utils.list_figure_lines(line)) and line.strip() not in figure_lines
        found = _FIGURE_TARGET.search(line) if is_unknown_figure else None
        own = lines_by_file.get(found.group(1), []) if found else []
        # a file the document holds under two lines names no one of them, and is refused as before
        written.append(own[0] if len(own) == 1 else line)

    return "\n".join(written)


def _check_text(text: str,
                figure_lines: set[str]) -> str:
    """Why a text written into a section is refused, or an empty string: a heading of the document's own two levels
    (a subject is a section of its own), a figure line the document does not hold, or a Mermaid block that is none."""

    if not text:
        # said with what to send instead: a worker sent six replaces with no text, refused only as "the text is empty"
        return ("the text is empty: write the block's whole new text in `text`; to delete a block, send "
                "{\"action\": \"remove\", \"block\": ...} instead")
    if _TOP_HEADING.search(_FENCED.sub("", text)):
        return "a heading of level 1 or 2 inside a section's text; start a new section for a new subject (### is fine)"
    unknown = [line for line in cleanup_block_utils.list_figure_lines(text) if line not in figure_lines]
    if unknown:
        return f"the figure line {unknown[0]!r} is in no block of the document; copy a figure's line exactly"
    for source in _MERMAID_FENCE.findall(text):
        if not mermaid_text_utils.is_mermaid(source):
            return "a ```mermaid block that is no Mermaid diagram; start it with its kind, such as flowchart LR"
    problems = mermaid_text_utils.list_fenced_problems(text)
    if problems:
        return f"a ```mermaid block Mermaid cannot draw: {'; '.join(problems)}"

    return ""


def _build_section_blocks(section: dict[str, Any],
                          text: str) -> list[dict[str, Any]]:
    """`text` cut into the section's next numbered blocks."""

    blocks = []
    for block in cleanup_block_utils.list_blocks(text):
        blocks.append({"id": section["next_block"], "text": block["text"]})
        section["next_block"] += 1

    return blocks


def _name_section(section: dict[str, Any]) -> str:
    return f"{SECTION_ID_PREFIX}{section['id']}"


def _find_read_section(state: dict[str, Any],
                       name: Any,
                       read_sections: set[int]) -> tuple[dict[str, Any] | None, str]:
    """The section `name` names, or the reason it cannot be changed: it names none, or this part never read it."""

    section = _find_section(state=state, number=_read_section_number(name))
    if section is None:
        return None, f"{name!r} names no section; write a section as K3"
    if section["id"] not in read_sections:
        return None, f"read {_name_section(section)} with read_section before you change it"

    return section, ""


def _find_read_block(state: dict[str, Any],
                     name: Any,
                     read_sections: set[int]) -> tuple[dict[str, Any] | None, int, str]:
    """The section holding block `name` and the block's place in it, or the reason it cannot be changed."""

    address = _read_block_address(name)
    if address is None:
        return None, -1, f"{name!r} names no block; write a block as K3.2"
    section, refusal = _find_read_section(state=state, name=address[0], read_sections=read_sections)
    if section is None:
        return None, -1, refusal
    places = [block["id"] for block in section["blocks"]]
    if address[1] not in places:
        # names the blocks the section holds now: a worker sent 15 removes of blocks an earlier write of its own had
        # removed, each refused as "no block", and could not tell why
        held = ", ".join(f"K{section['id']}.{place}" for place in places) or "no block"
        return None, -1, (f"{name!r} is no block of {_name_section(section)}, which holds {held} now: it was removed or "
                          f"merged by an earlier edit, so there is nothing left to change")

    return section, places.index(address[1]), ""


def _check_new_heading(state: dict[str, Any],
                       heading: str,
                       kept: dict[str, Any] | None = None) -> str:
    """Why a heading cannot be a section's, or an empty string: it is empty, or another section has it."""

    if not heading:
        return "the heading is empty"
    same = next((section for section in state["sections"]
                 if section is not kept and section["heading"].lower() == heading.lower()), None)
    if same is not None:
        return f"{_name_section(same)} already has the heading {heading!r}; add to it, or merge into it"

    return ""


def _find_place_after(section: dict[str, Any],
                      after: Any) -> tuple[int, str]:
    """Where in `section` a block goes: after the block `after` names, or at the end when it names none."""

    if not after:
        return len(section["blocks"]), ""
    address = _read_block_address(after)
    places = [block["id"] for block in section["blocks"]]
    if address is None or address[0] != section["id"] or address[1] not in places:
        return -1, f"after {after!r} is no block of {_name_section(section)}"

    return places.index(address[1]) + 1, ""


def _build_renumbered_blocks(section: dict[str, Any],
                             blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`blocks` with the next numbers of `section`, for blocks that move into it."""

    renumbered = []
    for block in blocks:
        renumbered.append({"id": section["next_block"], "text": block["text"]})
        section["next_block"] += 1

    return renumbered


def _apply_new_section(state: dict[str, Any],
                       edit: dict[str, Any],
                       read_sections: set[int],
                       text: str) -> tuple[str, str]:
    heading = _clean_heading(edit.get("heading"))
    refusal = _check_new_heading(state=state, heading=heading)
    if refusal:
        return refusal, ""
    section = {"id": state["next_section"], "heading": heading, "blocks": [], "next_block": 1}
    state["next_section"] += 1
    section["blocks"] = _build_section_blocks(section=section, text=text)
    state["sections"].append(section)
    read_sections.add(section["id"])

    return "", f"{_name_section(section)} {heading} started"


def _apply_section_edit(state: dict[str, Any],
                        action: str,
                        edit: dict[str, Any],
                        read_sections: set[int],
                        text: str) -> tuple[str, str]:
    """add, rename, remove_section and merge: an edit that names a whole section."""

    section, refusal = _find_read_section(state=state, name=edit.get("section"), read_sections=read_sections)
    if section is None:
        return refusal, ""
    if action == "add":
        place, refusal = _find_place_after(section=section, after=edit.get("after"))
        if refusal:
            return refusal, ""
        section["blocks"][place:place] = _build_section_blocks(section=section, text=text)
        return "", ""
    if action == "rename":
        heading = _clean_heading(edit.get("heading"))
        refusal = _check_new_heading(state=state, heading=heading, kept=section)
        if refusal:
            return refusal, ""
        section["heading"] = heading
        return "", ""
    if action == "remove_section":
        state["sections"].remove(section)
        return "", f"{_name_section(section)} {section['heading']} removed"
    target, refusal = _find_read_section(state=state, name=edit.get("into"), read_sections=read_sections)
    if target is None:
        return f"into: {refusal}", ""
    if target is section:
        return "a section cannot be merged into itself", ""
    first = target["next_block"]
    target["blocks"].extend(_build_renumbered_blocks(section=target, blocks=section["blocks"]))
    state["sections"].remove(section)

    return "", (f"{_name_section(section)} merged into {_name_section(target)} as {_name_section(target)}.{first} to "
                f"{_name_section(target)}.{target['next_block'] - 1}")


def _apply_block_edit(state: dict[str, Any],
                      action: str,
                      edit: dict[str, Any],
                      read_sections: set[int],
                      text: str) -> tuple[str, str]:
    """replace, remove, split and move: an edit that names a block of a section."""

    section, place, refusal = _find_read_block(state=state, name=edit.get("block"), read_sections=read_sections)
    if section is None:
        return refusal, ""
    if action == "replace":
        section["blocks"][place] = {"id": section["blocks"][place]["id"], "text": text}
        return "", ""
    if action == "remove":
        del section["blocks"][place]
        return "", ""
    if action == "split":
        heading = _clean_heading(edit.get("heading"))
        refusal = _check_new_heading(state=state, heading=heading)
        if refusal:
            return refusal, ""
        if place == 0:
            return "a split at the section's first block leaves it empty; rename the section instead", ""
        new = {"id": state["next_section"], "heading": heading, "blocks": [], "next_block": 1}
        state["next_section"] += 1
        new["blocks"] = _build_renumbered_blocks(section=new, blocks=section["blocks"][place:])
        del section["blocks"][place:]
        state["sections"].insert(state["sections"].index(section) + 1, new)
        read_sections.add(new["id"])
        return "", f"{edit.get('block')} onward is now {_name_section(new)} {heading}, after {_name_section(section)}"
    target, refusal = _find_read_section(state=state, name=edit.get("section"), read_sections=read_sections)
    if target is None:
        return refusal, ""
    moved = section["blocks"].pop(place)
    target_place, refusal = _find_place_after(section=target, after=edit.get("after"))
    if refusal:
        section["blocks"].insert(place, moved)
        return refusal, ""
    new_block = _build_renumbered_blocks(section=target, blocks=[moved])[0]
    target["blocks"].insert(target_place, new_block)

    return "", f"{edit.get('block')} is now {_name_section(target)}.{new_block['id']}"


def _drop_empty_sections(state: dict[str, Any]) -> list[str]:
    """Each section with no block left taken out, and a note naming it."""

    empty = [section for section in state["sections"] if not section["blocks"]]
    for section in empty:
        state["sections"].remove(section)

    return [f"{_name_section(section)} {section['heading']} had no block left and was removed" for section in empty]


def _apply_one_edit(state: dict[str, Any],
                    edit: dict[str, Any],
                    read_sections: set[int],
                    figure_lines: set[str]) -> tuple[str, str]:
    """One edit applied to the knowledge sections: the reason it is refused (it then changes nothing) or an empty
    string, and a note naming what moved or was renamed (empty when nothing did)."""

    action = str(edit.get("action", "")).strip()
    if action not in _ACTIONS:
        return f"action {action!r} is none of {', '.join(_ACTIONS)}", ""
    # a bracket in a diagram's label is mended here, costing the worker no call; what cannot be mended is refused
    repaired = mermaid_text_utils.repair_fenced_blocks(str(edit.get("text") or "").strip())
    text = _use_document_figure_lines(text=repaired, figure_lines=figure_lines)
    if action in _TEXT_ACTIONS:
        refusal = _check_text(text=text, figure_lines=figure_lines)
        if refusal:
            return refusal, ""
    if action == "new_section":
        return _apply_new_section(state=state, edit=edit, read_sections=read_sections, text=text)
    if action in _SECTION_ACTIONS:
        return _apply_section_edit(state=state, action=action, edit=edit, read_sections=read_sections, text=text)

    return _apply_block_edit(state=state, action=action, edit=edit, read_sections=read_sections, text=text)


def _render_outline(state: dict[str, Any]) -> str:
    lines = []
    for section in state["sections"]:
        words = [block["text"] for block in section["blocks"]
                 if not cleanup_block_utils.list_figure_lines(block["text"])]
        preview = " ".join(words[0].split())[:_OUTLINE_PREVIEW_CHARS] if words else "(figures only)"
        lines.append(f"{SECTION_ID_PREFIX}{section['id']} {section['heading']} · {len(section['blocks'])} block(s) · "
                     f"{preview}")

    return "\n".join(lines) or "The knowledge document has no section yet."


def _render_section(section: dict[str, Any]) -> str:
    blocks = "\n\n".join(f"{{{SECTION_ID_PREFIX}{section['id']}.{block['id']}}}\n{block['text']}"
                         for block in section["blocks"])

    return f"{SECTION_ID_PREFIX}{section['id']} ## {section['heading']}\n\n{blocks or '(no block yet)'}"


def _find_figure_path(paths: RunPaths,
                      text: str) -> Path | None:
    """The image file the first figure line of `text` links, beside the published document; None when it has none."""

    lines = cleanup_block_utils.list_figure_lines(text)
    target = _FIGURE_TARGET.search(lines[0]) if lines else None
    if target is None:
        return None
    path = document_folder_service.build_document_path(paths).parent / target.group(1)

    return path if path.is_file() else None


def _answer_look_figure(paths: RunPaths,
                        state: dict[str, Any],
                        args: dict[str, Any],
                        read_ids: set[int]) -> tuple[str, dict[str, Any] | None]:
    """The tool's reply to `look_figure`, and the figure's image as a message part (None when refused)."""

    number = cleanup_block_utils.read_block_id(args.get("block"))
    block = next((block for block in state["blocks"] if block["id"] == number), None)
    if block is None:
        return f"Refused: {args.get('block')!r} names no block of the document.", None
    if number not in read_ids:
        return f"Refused: read B{number} before you look at its figure.", None
    if not cleanup_block_utils.list_figure_lines(block["text"]):
        return (f"Refused: B{number} is text and holds no figure line (![...](...)); look_figure takes a block that "
                f"holds one."), None
    path = _find_figure_path(paths=paths, text=block["text"])
    if path is None:
        return f"Refused: the image B{number}'s figure line links is not in the document's folder.", None
    image = frame_message_utils.encode_frame(path=path, max_width=_FIGURE_MAX_WIDTH)

    return f"The figure of B{number} ({path.name}) follows as an image.", image


def _build_batch_text(state: dict[str, Any],
                      number: int) -> str:
    """The part's message: the document's size, where the part stands, and the part's blocks themselves."""

    size = state["size"]
    ids = state["batches"][number - 1]
    wanted = set(ids)
    blocks = [block for block in state["blocks"] if block["id"] in wanted]

    return (f"The document: {size['chars']:,} characters, about {size['tokens']:,} tokens, {size['blocks']:,} blocks, "
            f"{size['figures']} figures.\n"
            f"This is part {number} of {len(state['batches'])}: blocks B{ids[0]} to B{ids[-1]}, below. The knowledge "
            f"document has {len(state['sections'])} section(s) so far.\n"
            f"Read the knowledge outline, write this part's facts into the sections (refactoring what earlier parts "
            f"wrote where this part asks for it), decide each figure, and call finish_batch.\n\n"
            f"# Part {number}\n\n{cleanup_block_utils.render_blocks(blocks)}")


def _list_part_figures(paths: RunPaths,
                       state: dict[str, Any],
                       number: int) -> list[tuple[int, Path]]:
    """The figures of part `number` whose image the document has, as (block number, image file), in order."""

    wanted = set(state["batches"][number - 1])
    found = [(block["id"], _find_figure_path(paths=paths, text=block["text"])) for block in state["blocks"]
             if block["id"] in wanted and cleanup_block_utils.list_figure_lines(block["text"])]

    return [(number_, path) for number_, path in found if path is not None]


def _build_batch_message(paths: RunPaths,
                         state: dict[str, Any],
                         number: int) -> HumanMessage:
    """Part `number`'s first message: its text, then each of its figures as an image under the block it stands in.
    Sent as lines of text alone, a part's figures were never seen: the worker had to ask for each with `look_figure`
    and, over the first three parts of a 52-figure PDF, asked for none and placed none."""

    text = _build_batch_text(state=state, number=number)
    figures = _list_part_figures(paths=paths, state=state, number=number)
    if not figures:
        return HumanMessage(content=text)
    sent: list[dict[str, Any]] = []
    unsent: list[str] = []
    spent = 0
    for block_number, path in figures:
        cost = _estimate_image_tokens(path)
        if spent + cost > _PART_IMAGE_TOKENS:
            unsent.append(f"B{block_number}")
            continue
        spent += cost
        sent += [{"type": "text", "text": f"The figure of B{block_number} ({path.name}):"},
                 frame_message_utils.encode_frame(path=path, max_width=_PART_FIGURE_WIDTH)]
    note = (f"\n\nThe {len(figures) - len(unsent)} figure(s) of this part follow as images, each under the block it "
            f"stands in. Keep, drop or replace each one as you decide.")
    if unsent:
        note += (f" {len(unsent)} more did not fit in this message: look at each with look_figure before you decide it "
                 f"({', '.join(unsent)}).")

    return HumanMessage(content=[{"type": "text", "text": text + note}, *sent])


def _estimate_image_tokens(path: Path) -> int:
    """About how many prompt tokens the image costs once scaled to the part's width: a vision model of this family
    reads one token per 28 by 28 pixels."""

    with Image.open(path) as image:
        width, height = image.size
    scale = min(1.0, _PART_FIGURE_WIDTH / max(1, width))

    return max(1, round(width * scale * height * scale / (28 * 28)))


def rebuild_request(state: dict[str, Any],
                    number: int) -> tuple[str, str]:
    """Part `number`'s instructions and first message as built now from the current files and the pass's state, for a
    pass that started before it saved what it sent."""

    return _build_system_text(state.get("wishes", "")), _build_batch_text(state=state, number=number)


def _build_system_text(wishes: str) -> str:
    text = system_prompt_builder.build_system_prompt(PROMPT_PHASE)
    wish_text = wishes.strip() or "(none)"

    return (f"{text.rstrip()}\n\n{document_reshaping_service.build_feedback_text()}\n\n"
            f"## The person's own wishes for this document\n\n{wish_text}\n")


def _answer_tool(paths: RunPaths,
                 state: dict[str, Any],
                 number: int,
                 call: dict[str, Any],
                 read: dict[str, set[int]]) -> tuple[str, dict[str, Any] | None, bool]:
    """The tool's reply, a figure's image to send after it (or None), and whether the batch is now closed."""

    name, args = call.get("name"), call.get("args") or {}
    if name == "read_knowledge_outline":
        return _render_outline(state), None, False
    if name == "read_blocks":
        blocks, refusal = document_reshaping_service.read_block_range(state=state, args=args)
        if refusal:
            return f"Refused: {refusal}.", None, False
        read["blocks"].update(block["id"] for block in blocks)
        return cleanup_block_utils.render_blocks(blocks), None, False
    if name == "read_section":
        section = _find_section(state=state, number=_read_section_number(args.get("section")))
        if section is None:
            return f"Refused: {args.get('section')!r} names no section; read the outline for their names.", None, False
        rendered = _render_section(section)
        # a section is read twice at most as it stands: a worker read one section five times in a row, unchanged,
        # about 50 minutes of calls (the owner: "add smart refusal of more the 2 time"); a changed section is new text
        seen = zlib.crc32(rendered.encode("utf-8"))
        read["sections"].add(section["id"])
        if seen in read.setdefault("seen_twice", set()):
            return (f"Refused: you read {_name_section(section)} twice already and it has not changed since; its text "
                    f"is above in this conversation. Write your edits with write_sections, read another section, or "
                    f"close the part with finish_batch."), None, False
        if seen in read.setdefault("seen", set()):
            read["seen_twice"].add(seen)
        read["seen"].add(seen)
        return rendered, None, False
    if name == "look_figure":
        answer, image = _answer_look_figure(paths=paths, state=state, args=args, read_ids=read["blocks"])
        return answer, image, False
    if name == "write_sections":
        edits = args.get("edits") if isinstance(args.get("edits"), list) else []
        refused, notes = apply_section_edits(state=state, edits=edits, read_sections=read["sections"])
        applied = len(edits) - len(refused)
        return (f"{applied} edit(s) applied." + "".join(f"\n{note}" for note in notes)
                + "".join(f"\nRefused {line}" for line in refused)), None, False
    if name == "finish_batch":
        state["closed"][str(number)] = str(args.get("summary") or "").strip()
        return "Part closed.", None, True

    return f"Refused: no tool is called {name!r}.", None, False


def _write_document(paths: RunPaths,
                    state: dict[str, Any]) -> None:
    document_folder_service.build_document_path(paths).write_text(state["front"] + render_knowledge(state),
                                                                  encoding="utf-8")


def _save_part_draft(paths: RunPaths,
                     state: dict[str, Any],
                     number: int) -> None:
    """The document as part `number` left it, which the Open document menu lists as that part's draft."""

    path = document_pass_service.build_part_draft_path(output_root=paths.output_root, number=number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state["front"] + render_knowledge(state), encoding="utf-8")


def start_knowledge(paths: RunPaths,
                    asked: dict[str, str]) -> dict[str, Any]:
    """The pass's state: every pass's blocks and batches, with the knowledge sections kept from an earlier start of
    this same request, or none yet."""

    state = document_reshaping_service.start_pass(paths=paths, asked=asked)
    if "sections" not in state:
        title = next((block["text"] for block in state["blocks"] if block["text"].startswith("# ")), "")
        state.update({"title": title, "sections": [], "next_section": 1})
        document_reshaping_service.write_state(paths=paths, state=state)

    return state


def build_tool_specs() -> list[dict[str, Any]]:
    """The worker's eight tools: the knowledge outline, a read of the document, a read of a section, a figure's image,
    the writes, the part's close, the prune of results it no longer needs, and the report."""

    spec = document_reshaping_service.build_tool_spec
    source_block = {"type": "string", "description": "a block of the document as the read shows it, such as B12"}
    edit = {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS)},
        "heading": {"type": "string",
                    "description": "new_section, rename and split: the subject's heading, plain words"},
        "section": {"type": "string", "description": "add, rename, remove_section and merge: the section, such as "
                    "K3; move: the section the block goes to"},
        "into": {"type": "string", "description": "merge: the section that takes the other's blocks, such as K1"},
        "block": {"type": "string", "description": "replace, remove, move and split (its first block of the new "
                  "section): a block of a section, such as K3.2"},
        "after": {"type": "string", "description": "add and move: the block to put it after, such as K3.2; else at "
                  "the end"},
        "text": {"type": "string", "description": "new_section, add and replace: the markdown"}},
            "required": ["action"]}

    return [
        spec("read_knowledge_outline", "Every section of the knowledge document so far: its name, heading, block "
             "count and first words. Cheap.", {}, []),
        spec("read_blocks", "Blocks of the document outside your part, from `first` to `last` with their numbers; your "
             "own part is in the first message. A read of more than about one part is refused.",
             {"first": source_block, "last": source_block}, ["first", "last"]),
        spec("read_section", "One section of the knowledge document whole, its blocks numbered K3.1, K3.2. Read a "
             "section before you add to it.", {"section": {"type": "string", "description": "such as K3"}},
             ["section"]),
        spec("look_figure", "The image of the figure a block of the document holds, so you can decide to keep it, drop "
             "it, or write it as Mermaid, code or a table.", {"block": source_block}, ["block"]),
        spec("write_sections", "Apply edits to the knowledge document, in order. Each edit is applied or refused alone, "
             "and the reply names every refused edit with its reason.", {"edits": {"type": "array", "items": edit}},
             ["edits"]),
        spec("finish_batch", "Close this part once its facts and figures are placed.",
             {"summary": {"type": "string", "description": "what you wrote from this part, in a few lines"}},
             ["summary"]),
        document_reshaping_service.build_prune_tool_spec(),
        document_reshaping_service.build_feedback_tool_spec(),
    ]


def apply_section_edits(state: dict[str, Any],
                        edits: list[Any],
                        read_sections: set[int]) -> tuple[list[str], list[str]]:
    """`edits` applied to the knowledge sections in order: the reason of each edit refused (it changes nothing), and a
    note for each section started, moved, merged, split or removed, a section left with no block among them."""

    figure_lines = _list_source_figure_lines(state)
    refused = []
    notes = []
    for number, edit in enumerate(edits, start=1):
        refusal, note = _apply_one_edit(state=state, edit=edit if isinstance(edit, dict) else {},
                                        read_sections=read_sections, figure_lines=figure_lines)
        if refusal:
            refused.append(f"edit {number}: {refusal}")
            continue
        notes.extend([note] if note else [])
        notes.extend(_drop_empty_sections(state))

    return refused, notes


def render_knowledge(state: dict[str, Any]) -> str:
    """The knowledge document as markdown: the document's title, then each section under its heading."""

    parts = [state["title"]] if state.get("title") else []
    for section in state["sections"]:
        parts.append(f"## {section['heading']}")
        parts.extend(block["text"] for block in section["blocks"] if block["text"].strip())

    # sections a part wrote before the check held are mended too, as the document is written on a resume
    return mermaid_text_utils.repair_fenced_blocks("\n\n".join(parts) + "\n")


def run_batch(paths: RunPaths,
              state: dict[str, Any],
              number: int,
              model: BaseChatModel,
              log: Any,
              control_check: Callable[[], str | None] | None = None) -> tuple[bool, int, str]:
    """Part `number`'s conversation, until the worker closes it or spends `documentCleanup.batchCalls` calls: whether
    it was closed, the calls it made, and why not. Every write reaches the published document at once. A stop asked for
    ends it before its next call."""

    log_path = document_reshaping_service.build_batch_log_path(paths, number)
    messages: list[BaseMessage] = [SystemMessage(content=_build_system_text(state.get("wishes", ""))),
                                   _build_batch_message(paths=paths, state=state, number=number)]
    document_reshaping_service.save_request(paths, number, messages)
    bound = model.bind_tools(build_tool_specs())
    # the part's blocks are in its first message, so they count as read; a block outside it is read with read_blocks
    read: dict[str, set[int]] = {"blocks": set(state["batches"][number - 1]), "sections": set()}
    calls = app_config.settings.document_reshaping.batch_calls
    # a part stopped mid-way (a crash, a restart, a stop) goes on with its saved conversation, never from its first
    # message (the owner: "it contiue where it stoped in the same document")
    saved = document_reshaping_service.read_conversation(paths, number)
    if saved is not None:
        messages, saved_read = saved
        read = {**saved_read, "blocks": read["blocks"] | saved_read.get("blocks", set()),
                "sections": saved_read.get("sections", set())}
        log.info(f"phase 7: part {number} · goes on with its saved conversation, "
                 f"{document_reshaping_service.count_calls(messages)} calls made")
    # each start of the part gets its full budget of calls, its numbers going on from the saved ones
    made = document_reshaping_service.count_calls(messages)
    call_number, last_call = made, made + calls
    while call_number < last_call:
        document_reshaping_service.stop_if_asked(control_check, f"before call {call_number + 1} of part {number}")
        call_number += 1
        # the person's message costs the part none of its calls
        delivered = document_reshaping_service.add_part_messages(paths, number, messages)
        if delivered:
            last_call += delivered
            document_reshaping_service.save_conversation(paths, number, messages, read)
            log.info(f"phase 7: part {number} · {delivered} message(s) from the person sent to the worker")
        # at the size limit the conversation is compacted, as phase 4's is, and the sections count as unread again
        if document_reshaping_service.is_conversation_full(messages):
            size = document_reshaping_service.estimate_conversation_tokens(messages)
            messages = document_reshaping_service.build_compacted_conversation(paths, bound, messages, number, log)
            read = {"blocks": set(state["batches"][number - 1]), "sections": set(), "warned": set()}
            document_reshaping_service.save_conversation(paths, number, messages, read)
            log.info(f"phase 7: part {number} · its conversation reached the size limit of "
                     f"{app_config.settings.document_reshaping.conversation_size_limit_tokens:,} tokens at about {size:,}: "
                     f"compacted into the worker's summary")
        reply = document_reshaping_service.stream_reply(paths, bound, messages, number, log)
        if document_reshaping_service.is_looped(reply):
            log.info(f"phase 7: part {number} · the thinking repeated itself and was stopped")
            document_reshaping_service.log_turn(log_path=log_path, row={
                "call": call_number, "text": "(thinking stopped: it repeated itself)", "tools": []})
            messages.append(HumanMessage(content=document_reshaping_service.LOOPED_REPLY_ASK))
            continue
        messages.append(reply)
        document_reshaping_service.record_part_reply(paths, number, reply)
        tool_calls = reply.tool_calls if isinstance(reply, AIMessage) else []
        tool_rows = [{"name": call.get("name"), "args": call.get("args")} for call in tool_calls]
        document_reshaping_service.log_turn(log_path=log_path,
                                          row={"call": call_number, "text": str(reply.content)[:4_000],
                                               "tools": tool_rows})
        if not tool_calls:
            messages.append(HumanMessage(content="Answer with a tool call: read, write_sections, or finish_batch."))
            continue
        # a turn that only reported costs the part none of its calls, so a report is never saved up
        if document_reshaping_service.is_feedback_turn(tool_calls):
            last_call += 1
        is_closed = False
        images = []
        for call in tool_calls:
            if document_reshaping_service.is_feedback_call(call):
                answer, image, is_now_closed = (
                    document_reshaping_service.record_feedback(paths, number, call.get("args") or {}, log), None, False)
            elif call.get("name") == document_reshaping_service.PRUNE_TOOL:
                names = (call.get("args") or {}).get("results")
                answer = document_reshaping_service.prune_results(messages, names if isinstance(names, list) else [])
                image, is_now_closed = None, False
            else:
                answer, image, is_now_closed = _answer_tool(paths=paths, state=state, number=number, call=call, read=read)
            is_closed = is_closed or is_now_closed
            log.info(f"phase 7: part {number} · {call.get('name')} · {answer.splitlines()[0][:160]}")
            # each result carries its mark, which the worker names to prune it
            marked = document_reshaping_service.mark_result(messages, answer)
            messages.append(ToolMessage(content=marked, tool_call_id=call.get("id", "")))
            document_reshaping_service.log_turn(log_path=log_path, row={"call": call_number, "tool": call.get("name"),
                                                                      "result": answer[:4_000]})
            if image is not None:
                images.append(image)
        # the context check reaches the worker on its last result of the turn, before any figure
        warning = document_reshaping_service.warn_conversation_size(messages, read)
        if warning:
            messages[-1] = ToolMessage(content=f"{messages[-1].content}{warning}", tool_call_id=messages[-1].tool_call_id)
        # a tool message carries text only, so each figure looked at reaches the model as a message after the results
        if images:
            lead = document_reshaping_service.mark_result(messages, "The figure(s) you looked at:")
            messages.append(HumanMessage(content=[{"type": "text", "text": lead}, *images]))
        document_reshaping_service.write_state(paths=paths, state=state)
        _write_document(paths=paths, state=state)
        # saved after every turn, so the running part's row opens the document as it stands (the owner could see no
        # output of a part until it closed)
        _save_part_draft(paths=paths, state=state, number=number)
        document_reshaping_service.save_conversation(paths, number, messages, read)
        if is_closed:
            return True, call_number, ""

    return False, calls, f"part {number} was not closed within {calls} calls"


def write_report(paths: RunPaths,
                 state: dict[str, Any]) -> Path:
    """The phase's file: the document's size before, the knowledge document's after, and what each part wrote."""

    before = state["size"]
    after = render_knowledge(state)
    lines = [f"# Document {document_pass_service.describe_pass(state['pass'])}", "",
             f"Before: {before['chars']:,} characters (about {before['tokens']:,} tokens), {before['blocks']:,} blocks, "
             f"{before['figures']} figures.",
             f"After: {len(after):,} characters (about {document_reshaping_service.estimate_tokens(len(after)):,} tokens), "
             f"{len(state['sections'])} sections, {len(cleanup_block_utils.list_figure_lines(after))} figures.",
             "", "The document before this phase: `before.md`.", ""]
    for number in range(1, len(state["batches"]) + 1):
        lines += [f"## Part {number}", "", state["closed"].get(str(number), "(not closed)"), ""]
    path = document_pass_service.build_report_path(paths.output_root)
    path.write_text("\n".join(lines), encoding="utf-8")

    return path
