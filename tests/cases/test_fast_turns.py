"""A fast-processing run's turns: each session's model turns kept as they come, listed for the page's panel, and a run
made before turns were kept read back from its call logs."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

from models import output_contract
from services import fast_turn_service


def test_a_session_s_turns_are_kept_in_order_and_asked_again_keep_only_the_new_ones():
    root = Path(tempfile.mkdtemp())
    slug = f"{fast_turn_service.SESSION_PREFIX}001-004"
    fast_turn_service.append_turn(root, slug, {"call": 0, "thinking": "first", "text": "", "steps": []})
    fast_turn_service.append_turn(root, slug, {"call": 1, "thinking": "", "text": "second", "steps": []})
    turns = fast_turn_service.list_turns(root, slug)
    harness.check("both turns are listed oldest first", [turn["call"] for turn in turns] == [0, 1]
                  and turns[1]["text"] == "second", json.dumps(turns))
    fast_turn_service.start_turns(root, slug)
    fast_turn_service.append_turn(root, slug, {"call": 0, "thinking": "", "text": "again", "steps": []})
    harness.check("a session asked again keeps only its new turns",
                  [turn["text"] for turn in fast_turn_service.list_turns(root, slug)] == ["again"])


def test_a_phase_lists_every_session_in_frame_order_numbered_across_the_phase():
    root = Path(tempfile.mkdtemp())
    for slug, text in ((f"{fast_turn_service.SESSION_PREFIX}005-008", "later"),
                       (f"{fast_turn_service.SESSION_PREFIX}001-004", "earlier")):
        fast_turn_service.append_turn(root, slug, {"call": 0, "thinking": "", "text": text,
                                                   "steps": [{"call": 0, "tool": "submit_note"}]})
    turns = fast_turn_service.list_phase_turns(root, 3)
    harness.check("the sessions come in frame order, each turn and its steps numbered across the phase",
                  [turn["text"] for turn in turns] == ["earlier", "later"]
                  and [turn["call"] for turn in turns] == [0, 1] and turns[1]["steps"][0]["call"] == 1,
                  json.dumps(turns))


def test_a_run_made_before_turns_were_kept_shows_each_frame_s_note_from_its_call_logs():
    root = Path(tempfile.mkdtemp())
    calls = root / output_contract.CALLS_DIR_NAME
    calls.mkdir(parents=True)
    for frame in (3, 4):
        (calls / f"{output_contract.FAST_NOTE_SLUG_PREFIX}{frame:03d}.json").write_text(
            json.dumps({"response": {"description": f"frame {frame} shows the Gateway login form"}}), encoding="utf-8")
    (calls / f"{output_contract.FAST_DOCUMENT_SLUG}.json").write_text(
        json.dumps({"response": {"content": "# AcmeSync onboarding"}}), encoding="utf-8")
    notes = fast_turn_service.list_phase_turns(root, 3)
    harness.check("each logged frame is one turn with its note as a submit_note step",
                  [turn["frame"] for turn in notes] == [3, 4]
                  and notes[0]["steps"][0]["tool"] == "submit_note" and "login form" in notes[0]["steps"][0]["args"],
                  json.dumps(notes))
    document = fast_turn_service.list_phase_turns(root, 4)
    harness.check("the document's call is phase 4's one turn", [turn["text"] for turn in document] == ["# AcmeSync onboarding"],
                  json.dumps(document))
