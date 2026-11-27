"""The turns of a fast-processing run's workers: each model call of a phase 3 session
and phase 4's one call, with its thinking, its words and the tool call it made, kept as it returns so the page shows
them live and after (the owner: "the table should have clickable something to show the turns and thinking and chat")."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from models import output_contract
from utils import json_lines_utils

__all__ = ["SESSION_PREFIX", "build_turns_path", "start_turns", "append_turn", "list_turns", "list_phase_turns"]

# A phase 3 session's slug starts so (`fast-notes-000-004`); phase 4's is `output_contract.FAST_DOCUMENT_SLUG`
SESSION_PREFIX = "fast-notes-"
# A session's first and last frame, from its slug
_SESSION_FRAMES = re.compile(rf"^{SESSION_PREFIX}(\d+)-(\d+)$")
# A turn's text and thinking as the panel shows them: a document's whole answer stays readable
_TEXT_SHOWN = 60_000


def build_turns_path(output_root: Path,
                     slug: str) -> Path:
    return output_root / output_contract.RUNS_DIR_NAME / output_contract.FAST_TURNS_DIR / f"{slug}.jsonl"


def start_turns(output_root: Path,
                slug: str) -> None:
    """No turn kept for `slug`: a session asked again keeps only its new turns."""

    build_turns_path(output_root, slug).unlink(missing_ok=True)


def append_turn(output_root: Path,
                slug: str,
                turn: dict) -> None:
    """`turn` ({"call", "thinking", "text", "steps"}) kept at the end of `slug`'s turns, with the time it came."""

    path = build_turns_path(output_root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_lines_utils.append_json_line(path, {"at": time.time(), **turn})


def _read_call_log(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # a log a kill cut short, or none: that call has no turn to show
        return {}


def _list_logged_turns(output_root: Path,
                       slug: str) -> list[dict]:
    """The turns of a run made before turns were kept, from its call logs: each frame's note, or the document, with no
    thinking, which those logs never held."""

    calls = output_root / output_contract.CALLS_DIR_NAME
    match = _SESSION_FRAMES.match(slug)
    if match:
        names = [f"{output_contract.FAST_NOTE_SLUG_PREFIX}{number:03d}"
                 for number in range(int(match.group(1)), int(match.group(2)) + 1)]
    else:
        names = [output_contract.FAST_DOCUMENT_SLUG]
    turns = []
    for name in names:
        path = calls / f"{name}.json"
        response = _read_call_log(path).get("response") or {}
        if "description" in response:
            frame = int(name.removeprefix(output_contract.FAST_NOTE_SLUG_PREFIX))
            args = json.dumps({"description": response["description"]}, ensure_ascii=False)
            turns.append({"at": path.stat().st_mtime, "call": len(turns), "frame": frame, "thinking": "", "text": "",
                          "steps": [{"call": len(turns), "tool": "submit_note", "summary": f"frame {frame}",
                                     "args": args, "result": "Noted."}]})
        elif "content" in response:
            turns.append({"at": path.stat().st_mtime, "call": 0, "thinking": "", "text": str(response["content"]),
                          "steps": []})

    return turns


def list_turns(output_root: Path,
               slug: str) -> list[dict]:
    """Every turn of `slug`, oldest first, each text and thinking cut to what the panel shows."""

    path = build_turns_path(output_root, slug)
    rows = json_lines_utils.read_json_lines(path) if path.is_file() else _list_logged_turns(output_root, slug)

    return [{**row, "thinking": str(row.get("thinking") or "")[-_TEXT_SHOWN:], "text": str(row.get("text") or "")[:_TEXT_SHOWN]}
            for row in rows]


def list_phase_turns(output_root: Path,
                     number: int) -> list[dict]:
    """Every turn of phase `number` (3: every session in the frames' order, 4: the document's call), numbered
    across the phase."""

    folder = output_root / output_contract.RUNS_DIR_NAME / output_contract.FAST_TURNS_DIR
    if number == 3:
        slugs = sorted(path.stem for path in folder.glob(f"{SESSION_PREFIX}*.jsonl"))
        # a run made before turns were kept: every frame its call logs hold, as one span
        logged = sorted(int(path.stem.removeprefix(output_contract.FAST_NOTE_SLUG_PREFIX)) for path in
                        (output_root / output_contract.CALLS_DIR_NAME).glob(f"{output_contract.FAST_NOTE_SLUG_PREFIX}*.json"))
        if not slugs and logged:
            slugs = [f"{SESSION_PREFIX}{logged[0]:03d}-{logged[-1]:03d}"]
    else:
        slugs = [output_contract.FAST_DOCUMENT_SLUG]
    turns = [turn for slug in slugs for turn in list_turns(output_root, slug)]

    return [{**turn, "call": index, "steps": [{**step, "call": index} for step in turn.get("steps") or []]}
            for index, turn in enumerate(turns)]
