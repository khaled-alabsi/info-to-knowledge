"""A frame's new subject tag held to the frame (DEFECTS 457).

A frame's `## Tags` name its subjects, and a subject some earlier frame already has is copied as it stands. A NEW
capitalised tag whose most telling word stands nowhere in the frame's own notes or in the speech over it names a subject
of a later frame: release test 3's frame 47, a chart slide before its first example, was tagged "Adblock Plus" and
"Drupal", which frames 48 and 50 show and say, and every transcript line then inherited them. The tag is dropped as the
notes are written; the repair cannot edit tags, so a critique rule naming one could not be answered. Over the public
runs 31 of 846 new capitalised tags (25 of 1,007 frames) were such. A tag is never dropped when it would leave none.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import config as app_config
from models import output_contract
from models.run_paths import RunPaths
from services import frame_records_service
from services import phase_file_service

__all__ = ["drop_unsupported_new_tags"]

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)
_WORD = re.compile(r"[a-z0-9]{3,}")
# The notes' fields a tag may be supported by, beside the speech over the frame
_TEXT_FIELDS = ("screen", "as_markdown", "facts", "take_aways", "third_person_description", "speech_visual_alignment",
                "open_questions", "diagram")


def _list_field_text(record: dict[str, Any],
                     field: str) -> str:
    value = record.get(field)

    return " ".join(str(line) for line in value) if isinstance(value, list) else str(value or "")


def drop_unsupported_new_tags(paths: RunPaths,
                              slug: str,
                              record: dict[str, Any]) -> dict[str, Any]:
    """The record without each tag that no earlier frame has, that holds a capital letter and whose longest word stands
    nowhere in the frame's notes or its speech (this frame's, and the words before it); unchanged when that would leave
    no tag, or with no frames file to read."""

    tags = [str(tag) for tag in record.get("tags") or []]
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    if not tags or not phase_file_service.has_phase_file(paths, frames_number):
        return record
    item = next((entry for entry in phase_file_service.read_phase_file(paths, frames_number).get("items", [])
                 if entry.get("slug") == slug), None)
    if item is None:
        return record
    earlier = {str(tag).casefold() for known in frame_records_service.read_frame_records(paths)
               if known["index"] < item["index"] for tag in known.get("tags") or []}
    text = " ".join([*(_list_field_text(record, field) for field in _TEXT_FIELDS),
                     str(item.get("transcriptCurrent") or ""), str(item.get("transcriptBefore") or "")]).casefold()
    kept = []
    for tag in tags:
        words = _WORD.findall(tag.casefold())
        is_unsupported = (tag.casefold() not in earlier and any(character.isupper() for character in tag) and bool(words)
                          and max(words, key=len) not in text)
        if is_unsupported:
            _LOG.info(f"  {slug}: tag \"{tag}\" dropped: no earlier frame has it and the frame's notes and speech do not hold it")
        else:
            kept.append(tag)

    return {**record, "tags": kept} if kept and len(kept) < len(tags) else record
