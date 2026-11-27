"""A direct-draft run's draft document as sections: what the batches wrote, read back as headings with the lines under
them, the outline phase 4's call and the next batch are shown, the heading changes the call may ask for, and the
picture lines turned into figures. The draft's format is written down in `docs/FLOW.md`, phase 3.

A heading is a markdown line of `#`s outside a code fence. A picture is a line of its own, `{{frame-017: caption}}`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from models.draft_section_spec import LEAD_ID, DraftSectionSpec

__all__ = ["PICTURE_LINE", "CHANGE_ACTIONS", "render_batch_marker", "parse_sections", "render_headings",
           "list_pictures", "render_outline", "read_last_section", "apply_changes", "render_sections",
           "replace_pictures"]

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BATCH_MARKER = re.compile(r"^<!--\s*batch\b.*-->\s*$")
_FENCE = "```"
_MANY_BLANK_LINES = re.compile(r"\n{3,}")
# `{{frame-017: the board's open tickets}}`: the frame's slug and the caption its figure gets
PICTURE_LINE = re.compile(r"^\{\{\s*(frame-\d+)\s*:\s*(.*?)\s*\}\}\s*$")
_ELLIPSIS = "…"
CHANGE_ACTIONS = ("rename", "merge_into_previous", "move_after")


def render_batch_marker(number: int,
                        first_frame: int,
                        last_frame: int) -> str:
    """The line that opens a batch's text in the draft: `<!-- batch 3 · frames 16-23 -->`."""

    return f"<!-- batch {number} · frames {first_frame}-{last_frame} -->"


def parse_sections(text: str) -> list[DraftSectionSpec]:
    """The draft's sections in order, each with an id `H01`, `H02` ... by its place. The draft's own first line (its
    title) and the batch markers are left out; text before the first heading is the lead section, `H00`, kept only when
    it holds a word."""

    sections = [DraftSectionSpec(id=LEAD_ID, level=0, title="")]
    is_fenced = False
    for number, line in enumerate(text.splitlines()):
        if number == 0 and line.startswith("# "):
            continue
        if _BATCH_MARKER.match(line):
            continue
        if line.strip().startswith(_FENCE):
            is_fenced = not is_fenced
        heading = None if is_fenced else _HEADING.match(line)
        if heading:
            sections.append(DraftSectionSpec(id=f"H{len(sections):02d}", level=len(heading.group(1)),
                                             title=heading.group(2)))
        else:
            sections[-1].lines.append(line)

    return sections if any(line.strip() for line in sections[0].lines) else sections[1:]


def render_headings(sections: list[DraftSectionSpec]) -> str:
    """Every heading with its id, one per line: `H03 ## Access tokens`; the outline a batch is shown."""

    return "\n".join(f"{section.id} {'#' * section.level} {section.title}" for section in sections if section.level)


def list_pictures(section: DraftSectionSpec) -> list[tuple[str, str]]:
    """The pictures the section places, each as the frame's slug and its caption, in the order the lines come."""

    matches = [PICTURE_LINE.match(line.strip()) for line in section.lines]

    return [(match.group(1), match.group(2)) for match in matches if match]


def _read_first_line(section: DraftSectionSpec,
                     max_chars: int) -> str:
    """The first line of the section's text that is not a picture, cut to `max_chars`."""

    for line in section.lines:
        text = line.strip()
        if text and not PICTURE_LINE.match(text):
            return text if len(text) <= max_chars else text[:max_chars].rstrip() + _ELLIPSIS

    return ""


def render_outline(sections: list[DraftSectionSpec],
                   first_line_chars: int) -> str:
    """The draft as the last call reads it: each section's id and heading, the first line of its text, and the frames
    its pictures name with their captions."""

    lines = []
    for section in sections:
        lines.append(f"{section.id} {'#' * section.level} {section.title}" if section.level
                     else f"{section.id} (text before the first heading)")
        first_line = _read_first_line(section, first_line_chars)
        pictures = list_pictures(section)
        lines += [f"    {first_line}"] if first_line else []
        lines += [f"    picture {slug}: {caption}" for slug, caption in pictures]

    return "\n".join(lines)


def read_last_section(text: str,
                      max_chars: int) -> str:
    """The draft's last section, its heading and its text, and of a long one the last `max_chars` characters; empty
    when the draft holds none."""

    sections = parse_sections(text)
    if not sections:
        return ""
    last = sections[-1]
    heading = f"{'#' * last.level} {last.title}\n\n" if last.level else ""
    shown = heading + "\n".join(last.lines).strip()

    return shown if len(shown) <= max_chars else _ELLIPSIS + shown[-max_chars:]


def _find_index(sections: list[DraftSectionSpec],
                section_id: str) -> int | None:
    return next((index for index, section in enumerate(sections) if section.id == section_id.strip().upper()), None)


def _find_block_end(sections: list[DraftSectionSpec],
                    index: int) -> int:
    """One past the section at `index` and the deeper sections that follow it: a chapter with its subsections. The text
    before the first heading has no subsections: it is its own block."""

    end = index + 1
    if not sections[index].level:
        return end
    while end < len(sections) and sections[end].level > sections[index].level:
        end += 1

    return end


def _move_after(sections: list[DraftSectionSpec],
                heading: str,
                after: str) -> str:
    """Move `heading`'s section, with its subsections, to follow `after`'s; the reason when it cannot."""

    index, target = _find_index(sections, heading), _find_index(sections, after)
    if target is None:
        return f"{after!r} is not a heading of the draft"
    end = _find_block_end(sections, index)
    if index <= target < end:
        return f"{heading} cannot move after itself or after one of its own subsections"
    block = sections[index:end]
    del sections[index:end]
    position = _find_block_end(sections, _find_index(sections, after))
    sections[position:position] = block

    return ""


def _merge_into_previous(sections: list[DraftSectionSpec],
                         heading: str) -> str:
    """Drop `heading`'s own line and put its text at the end of the section before it; the reason when it cannot."""

    index = _find_index(sections, heading)
    if index == 0:
        return f"{heading} is the first section, with none before it"
    before, merged = sections[index - 1], sections.pop(index)
    before.lines += ["", *merged.lines]

    return ""


def apply_changes(sections: list[DraftSectionSpec],
                  changes: Iterable[dict]) -> list[str]:
    """Carry out the heading changes in the order given, on `sections` itself: `rename` (`heading`, `title`),
    `merge_into_previous` (`heading`) and `move_after` (`heading`, `after`). A change that cannot be done is skipped; the
    list of reasons, each naming the heading, comes back, and everything else is done."""

    refused = []
    for change in changes:
        heading, action = str(change.get("heading") or ""), str(change.get("action") or "")
        index = _find_index(sections, heading)
        if index is None or not sections[index].level:
            refused.append(f"{heading or '(no heading)'}: the draft has no such heading")
            continue
        if action == "rename":
            title = str(change.get("title") or "").strip()
            reason = "" if title else "a rename needs the new title"
            sections[index].title = title or sections[index].title
        elif action == "merge_into_previous":
            reason = _merge_into_previous(sections, heading)
        elif action == "move_after":
            reason = _move_after(sections, heading, str(change.get("after") or ""))
        else:
            reason = f"{action!r} is not a change; the changes are {', '.join(CHANGE_ACTIONS)}"
        if reason:
            refused.append(f"{heading}: {reason}")

    return refused


def render_sections(sections: list[DraftSectionSpec]) -> str:
    """The sections as markdown, a blank line between them."""

    parts = []
    for section in sections:
        heading = f"{'#' * section.level} {section.title}" if section.level else ""
        body = "\n".join(section.lines).strip("\n")
        parts.append(f"{heading}\n\n{body}".strip("\n"))

    return _MANY_BLANK_LINES.sub("\n\n", "\n\n".join(part for part in parts if part)) + "\n"


def _render_figure(file: str,
                   caption: str) -> str:
    """The figure as a chapter places one: the image, then its caption in italics, a paragraph each."""

    alt = re.sub(r"[\[\]\n]", " ", caption).strip()

    return f"![{alt}]({file})\n\n*{caption}*"


def replace_pictures(text: str,
                     cuts: dict[str, list[str]]) -> str:
    """Each picture line replaced by the figure of a cut: `cuts` maps a frame's slug to the files of its cuts in order,
    and a frame's first picture line takes its first cut, its second the second. A picture line with no cut left is
    dropped, and one with no caption is captioned by its frame's slug. Lines inside a code fence are left as they are."""

    taken: dict[str, int] = {}
    lines = []
    is_fenced = False
    for line in text.splitlines():
        if line.strip().startswith(_FENCE):
            is_fenced = not is_fenced
        match = None if is_fenced else PICTURE_LINE.match(line.strip())
        if not match:
            lines.append(line)
            continue
        slug, caption = match.group(1), match.group(2)
        number = taken.get(slug, 0)
        taken[slug] = number + 1
        files = cuts.get(slug) or []
        if number < len(files):
            lines.append(_render_figure(files[number], caption or slug))

    return _MANY_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip("\n") + "\n"
