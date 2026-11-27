"""Phases 1 to 3's files written from a layer-2 fixture, for the cases that describe a meeting by its entries.

Layer 2 is merged in memory from `01_transcript.json` and `02_frames.json`, whose items hold each frame's notes
(`services/integration_service.py`), so a case that states the entries it wants writes the three files they come
from. The fixture stays the readable half; the helper then merges them and REFUSES when the merge does not give the
fixture back, id for id, so a case can never test entries the program would not produce.
"""

from __future__ import annotations

from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from services import document_file_service
from services import integration_service
from services import phase_file_service
from utils import meeting_notes_utils

# Layer-2 kind -> the frame record's field it comes from, and whether that field holds one text or a list of them.
_RECORD_FIELDS = {"facts": ("facts", True), "diagram": ("diagram", False),
                  "speech_visual_alignment": ("speech_visual_alignment", False), "open_question": ("open_questions", True),
                  "url": ("urls", True), "action_item": ("action_items", True), "question": ("questions_asked", True),
                  "markdown": ("as_markdown", False)}


def _build_frames(integration: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """frame index -> `{"timestamp", "screenKind", "screen", "third_person_description"}`, from the fixture's frames
    or, when it lists none, from the frames its entries name."""

    frames = {int(frame["index"]): {"timestamp": float(frame["timestamp"]), "screenKind": frame.get("screenKind", ""),
                                    "screen": frame.get("screen", ""),
                                    "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
                                    "third_person_description": frame.get("third_person_description", "")}
              for frame in integration.get("frames") or []}
    for entry in integration.get("entries", []):
        for index, timestamp in zip(entry.get("frames") or [], entry.get("timestamps") or []):
            frames.setdefault(int(index), {"timestamp": float(timestamp), "screenKind": "", "screen": "",
                                           "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
                                           "third_person_description": ""})

    return frames


def _build_records(integration: dict[str, Any],
                   frames: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    records = {index: {"slug": f"frame-{index:03d}", "index": index, "timestamp": frame["timestamp"],
                       "screen_kind": frame["screenKind"], "screen": frame["screen"],
                       "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
                       "third_person_description": frame["third_person_description"]}
               for index, frame in frames.items()}
    for entry in integration.get("entries", []):
        if entry["kind"] not in _RECORD_FIELDS:
            continue
        field_name, is_list = _RECORD_FIELDS[entry["kind"]]
        for index in entry.get("frames") or []:
            record = records[int(index)]
            if is_list:
                record.setdefault(field_name, []).append(entry["text"])
            elif record.get(field_name):
                raise ValueError(f"frame {index} holds two `{field_name}` entries; a frame record holds one")
            else:
                record[field_name] = entry["text"]
            for label in ("topics", "tags"):
                record[label] = list(dict.fromkeys([*record.get(label, []), *(entry.get(label) or [])]))
    for contradiction in integration.get("contradictions", []):
        records[int(contradiction["frame"])].setdefault("contradicts_earlier", []).append(contradiction["text"])

    return [records[index] for index in sorted(records)]


def _describe_entries(entries: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(entry["id"], entry["kind"], entry["text"]) for entry in entries]


def write_merge_inputs(paths: RunPaths,
                       integration: dict[str, Any],
                       blocked: tuple[int, ...] = ()) -> dict[str, Any]:
    """Phases 1 to 3's files for `integration`'s entries and frames, frames in `blocked` written with no record.
    Returns the merge the program will compute, and raises when its entries or its unread speech differ from the
    fixture's."""

    speech = [entry for entry in integration.get("entries", []) if entry["kind"] == "speech"]
    frames = _build_frames(integration)
    records = [record for record in _build_records(integration, frames) if record["index"] not in blocked]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": [
        {"text": entry["text"], "start": entry["start"], "end": entry["end"]} for entry in speech]})
    notes = {record["index"]: {key: value for key, value in record.items() if key not in ("slug", "index", "timestamp")}
             for record in records}
    # A frames file the case wrote first keeps its items' own fields (a frame's path, its box); the notes are added
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    listed = ({int(item["index"]): item for item in phase_file_service.read_phase_file(paths, frames_number).get("items", [])}
              if phase_file_service.has_phase_file(paths, frames_number) else {})
    phase_file_service.write_phase_file(paths, frames_number, {"items": [
        {**listed.get(index, {}), "slug": f"frame-{index:03d}", "index": index, "timestamp": frames[index]["timestamp"],
         **({"record": notes[index], "builtFrom": []} if index in notes else {})} for index in sorted(frames)]})
    merged = integration_service.build_integration(paths)
    document_file_service.write_meeting_notes(
        paths, meeting_notes_utils.render_meeting_notes(paths.module, merged))
    given, wanted = _describe_entries(merged["entries"]), _describe_entries(integration.get("entries", []))
    if given != wanted:
        first = next((position for position, pair in enumerate(zip(given, wanted)) if pair[0] != pair[1]),
                     min(len(given), len(wanted)))
        raise AssertionError(f"the fixture's entries are not what the merge gives, first at position {first}: the merge "
                             f"gives {given[first:first + 2]}, the fixture says {wanted[first:first + 2]}")
    wanted = [(span["start"], span["end"]) for span in integration.get("unreadSpeech") or []]
    if "unreadSpeech" in integration and [(span["start"], span["end"]) for span in merged["unreadSpeech"]] != wanted:
        raise AssertionError(f"the fixture's unread speech is not what the merge gives: {merged['unreadSpeech']} vs {wanted}")

    return merged
