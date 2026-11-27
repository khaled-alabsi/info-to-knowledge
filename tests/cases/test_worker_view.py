"""What the worker of each phase saw: the detail view lists a phase's steps from what the run
saved, opens one attempt's instructions and input with its frames named, opens a conversation's follow-up with its
turns, and previews a phase's instructions from the current files. Invented run folder, no model."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from support import harness

import config
from services import run_status_service
from services import web_api_service
from services import worker_view_service
from services import write_conversation_service

_SESSION = "2026-01-01_10-00"
_HUMAN = ("Frame 0, captured at 1.0 s.\n\nSpoken from this frame until the next one:\nhello\n\n"
          "[image: 120 characters of data URI]\n\nFrame 1, captured at 2.0 s.\n\n[image: 99 characters of data URI]\n\n"
          "Write the notes.")


def _write(path: Path,
           text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_attempt(folder: Path,
                   step: str,
                   attempt: str,
                   is_accepted: bool,
                   stamp: int) -> None:
    directory = folder / "logs" / _SESSION / "frames_processing" / step / attempt
    _write(directory / "system-prompt.md", f"INSTRUCTIONS {step}")
    _write(directory / "human-message.md", _HUMAN)
    _write(directory / "decision.json", json.dumps({"accepted": is_accepted}))
    findings = [] if is_accepted else [{"code": "FRAME_FACTS_EMPTY", "detail": "frame 1 has no facts", "scope": "target"}]
    _write(directory / "validation.json", json.dumps({"ok": is_accepted, "findings": findings}))
    os.utime(directory, (stamp, stamp))


def _make_run_folder(root: Path) -> Path:
    folder = root / "output" / "AcmeSync-demo"
    _write(folder / "02_frames.json", json.dumps({
        "items": [{"slug": "frame-000", "path": "frames/frame-000.jpg"}, {"slug": "frame-001", "path": "frames/frame-001.jpg"}],
        "droppedFrames": [{"slug": "frame-002", "path": "frames/dropped/frame-002.jpg"}]}))
    _write_attempt(folder, "frames-000-001", "attempt-1", False, 1_000)
    _write_attempt(folder, "frames-000-001", "attempt-2", True, 1_100)
    _write_attempt(folder, "frames-000-001-critique", "attempt-1", True, 1_200)
    _write(folder / "logs" / _SESSION / "dry-run" / "frames_processing" / "frames-000-001" / "human-message.md", _HUMAN)
    rows = [{"followUp": "chapter-01-text", "sent": ["a"], "thinking": "I read chapter 1", "said": "",
             "calls": [{"name": "read_notes", "args": {"chapter": 1}}], "promptTokens": 1200},
            {"followUp": "chapter-01-text", "tool": "read_notes", "result": "Chapter 1 read whole."},
            {"followUp": "chapter-01-text", "accepted": 0}]
    _write(folder / "04_writing" / "logs" / "conversation.jsonl", "\n".join(json.dumps(row) for row in rows) + "\n")
    _write(folder / "04_writing" / "logs" / "system-prompt.md", "WRITE INSTRUCTIONS")
    _write(folder / "04_writing" / "requests" / "chapter-01-text.md", "Write chapter 1.")

    return folder


def test_a_phase_lists_its_steps_and_an_attempt_shows_what_it_was_sent():
    root = Path(tempfile.mkdtemp())
    _make_run_folder(root)
    previous = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    try:
        client = TestClient(web_api_service.build_app())
        steps = client.get("/api/runs/output/AcmeSync-demo/worker", params={"phase": 3}).json()["steps"]
        first = client.get("/api/runs/output/AcmeSync-demo/worker/attempt",
                           params={"ref": steps[0]["attempts"][0]["ref"]}).json()
        writing = client.get("/api/runs/output/AcmeSync-demo/worker", params={"phase": 4}).json()["steps"]
        follow_up = client.get("/api/runs/output/AcmeSync-demo/worker/attempt",
                               params={"ref": writing[0]["attempts"][0]["ref"]}).json()
        climbing = client.get("/api/runs/output/AcmeSync-demo/worker/attempt", params={"ref": "../../etc"})
        outside_logs = client.get("/api/runs/output/AcmeSync-demo/worker/attempt", params={"ref": "frames"})
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous)

    harness.check("phase 3's steps come in the order the run asked them, the dry run apart",
                  [(step["step"], step["is_dry_run"]) for step in steps]
                  == [("frames-000-001", False), ("frames-000-001-critique", False), ("frames-000-001", True)], str(steps))
    harness.check("a step's attempts are listed with how each ended",
                  [(a["label"], a["status"]) for a in steps[0]["attempts"]]
                  == [("attempt 1", "refused"), ("attempt 2", "accepted")], str(steps[0]["attempts"]))
    harness.check("an attempt shows its instructions", first["instructions"] == "INSTRUCTIONS frames-000-001", str(first))
    images = [segment for segment in first["input"] if segment["kind"] == "image"]
    harness.check("each image of the input is named by the frame the text before it names, with its file",
                  [(image["frame"], image["path"]) for image in images]
                  == [(0, "frames/frame-000.jpg"), (1, "frames/frame-001.jpg")], str(images))
    harness.check("the text around the images is kept in order",
                  first["input"][0]["kind"] == "text" and "Frame 0" in first["input"][0]["text"]
                  and first["input"][-1]["text"].strip() == "Write the notes.", str(first["input"]))
    harness.check("a refused attempt shows its findings",
                  first["findings"] == [{"code": "FRAME_FACTS_EMPTY", "detail": "frame 1 has no facts"}], str(first))
    harness.check("phase 4 lists its conversation's follow-ups with how each ended",
                  [(s["step"], s["kind"], s["attempts"][0]["status"]) for s in writing]
                  == [("chapter-01-text", "conversation", "accepted")], str(writing))
    harness.check("a follow-up shows the conversation's instructions, its request and its turns",
                  follow_up["instructions"] == "WRITE INSTRUCTIONS" and follow_up["input"][0]["text"] == "Write chapter 1."
                  and [turn["kind"] for turn in follow_up["turns"]] == ["turn", "result", "accepted"], str(follow_up))
    harness.check("a ref that climbs out of the run folder is refused", climbing.status_code == 404, str(climbing.status_code))
    harness.check("a ref outside the run's logs is refused", outside_logs.status_code == 404, str(outside_logs.status_code))


def test_the_preview_builds_each_instruction_set_of_a_phase_with_no_model():
    client = TestClient(web_api_service.build_app())
    previews = client.get("/api/phase-instructions", params={"phase": 3}).json()["instructions"]
    names = {preview["name"]: preview for preview in previews}
    declared = json.loads((config.AGENT_CONTEXT_DIR / "context_map.json").read_text(encoding="utf-8"))["phases"]

    harness.check("phase 3's preview holds its frames instructions, built", "frames_processing" in names
                  and len(names["frames_processing"]["text"]) > 200, str(list(names)))
    harness.check("no instruction set of another phase is in phase 3's preview",
                  all(worker_view_service.INSTRUCTION_PHASE_NUMBERS[name] == 3 for name in names), str(list(names)))
    video_2 = [item["name"] for item in client.get("/api/phase-instructions", params={"phase": 2}).json()["instructions"]]
    audio_2 = [item["name"] for item in client.get("/api/phase-instructions",
                                                   params={"phase": 2, "audio": "true"}).json()["instructions"]]
    harness.check("a video run's phase 2 preview holds only the redundancy filter's instructions",
                  video_2 == ["frame_redundancy"], str(video_2))
    harness.check("an audio run's phase 2 preview holds only the speech structuring instructions",
                  audio_2 == ["speech_structuring"], str(audio_2))
    phase_4 = {item["name"]: item for item in client.get("/api/phase-instructions", params={"phase": 4}).json()["instructions"]}
    harness.check("every instruction set says who is sent it, at which stage",
                  all(item["used_by"] for item in [*names.values(), *phase_4.values()]), str(list(phase_4)))
    harness.check("write is previewed as the conversation sends it, every follow-up's section added",
                  "# When the follow-up asks for a chapter (`submit_chapter`)" in phase_4["write"]["text"], phase_4["write"]["text"][-300:])
    harness.check("every instruction set the context map declares says who is sent it",
                  set(declared) <= set(worker_view_service.INSTRUCTION_USED_BY),
                  str(set(declared) - set(worker_view_service.INSTRUCTION_USED_BY)))
    harness.check("every instruction set the context map declares belongs to a phase",
                  set(declared) <= set(worker_view_service.INSTRUCTION_PHASE_NUMBERS),
                  str(set(declared) - set(worker_view_service.INSTRUCTION_PHASE_NUMBERS)))


def test_a_conversation_saves_its_instructions_and_each_request_beside_its_log():
    log_path = Path(tempfile.mkdtemp()) / "05_review" / "logs" / "conversation.jsonl"
    log_path.parent.mkdir(parents=True)
    session = SimpleNamespace(conversation_log_path=log_path,
                              starting_messages=SimpleNamespace(system_text="REVIEW INSTRUCTIONS"))
    write_conversation_service._save_follow_up_view(session, SimpleNamespace(slug="todo-03", text="Answer todo 3."))

    harness.check("the instructions are saved beside the conversation's log",
                  (log_path.parent / "system-prompt.md").read_text(encoding="utf-8") == "REVIEW INSTRUCTIONS")
    harness.check("the follow-up's request is saved under its step",
                  (log_path.parent / "requests" / "todo-03.md").read_text(encoding="utf-8") == "Answer todo 3.")


def test_phase_7_shows_each_batch_it_sent_and_its_card_is_named_by_the_pass_defect():
    # Defect: a knowledge pass's card read "clean up or rewrite", and What the worker saw showed nothing of phase 7.
    # Fix: the card is named by the pass, and each batch shows its instructions, first message, replies and results
    folder = Path(tempfile.mkdtemp())
    cleanup = folder / "07_cleanup"
    _write(cleanup / "system-prompt.md", "KNOWLEDGE INSTRUCTIONS")
    _write(cleanup / "requests" / "batch-001.md", "Part 1 of 2: {B1}\nThe router picks the shard.")
    _write(cleanup / "batch-001.jsonl", "\n".join(json.dumps(row) for row in [
        {"at": 1.0, "call": 1, "text": "", "tools": [{"name": "finish_batch", "args": {"summary": "done"}}]},
        {"at": 2.0, "call": 1, "tool": "finish_batch", "result": "Part 1 closed."}]) + "\n")
    # part 2's request is saved before its first call, so it shows while its worker still thinks
    _write(cleanup / "requests" / "batch-002.md", "Part 2 of 2: {B9}\nResponses are cached.")

    steps = worker_view_service.list_worker_steps(folder, 7)
    harness.check("both parts are listed, the first closed, the second asked",
                  [(step["step"], step["attempts"][0]["status"]) for step in steps]
                  == [("batch-001", "accepted"), ("batch-002", "asked")], str(steps))
    seen = worker_view_service.read_attempt(folder, steps[0]["attempts"][0]["ref"])
    harness.check("a part shows its instructions, its first message and its call and result",
                  seen["instructions"] == "KNOWLEDGE INSTRUCTIONS" and "router" in seen["input"][0]["text"]
                  and [turn["kind"] for turn in seen["turns"]] == ["turn", "result"]
                  and seen["turns"][0]["calls"][0]["name"] == "finish_batch", str(seen))
    # a pass started before it saved what it sent: its instructions and first message are built again, marked so
    old = Path(tempfile.mkdtemp()) / "07_cleanup"
    _write(old / "state.json", json.dumps({
        "pass": "knowledge", "wishes": "keep the field tables", "batches": [[1, 2]], "sections": [],
        "size": {"chars": 60, "tokens": 15, "blocks": 2, "figures": 0},
        "blocks": [{"id": 1, "text": "# Routing"}, {"id": 2, "text": "The router picks the shard."}]}))
    _write(old / "batch-001.jsonl", json.dumps({"at": 1.0, "call": 1, "text": "thinking about it", "tools": []}) + "\n")
    rebuilt = worker_view_service.read_attempt(old.parent, "conversation:7:batch-001")
    harness.check("a pass that saved nothing shows its instructions and part built again, marked so",
                  "Built again now" in (rebuilt["instructions"] or "") and "keep the field tables" in rebuilt["instructions"]
                  and "## Goal" in rebuilt["instructions"]
                  and "{B2}\nThe router picks the shard." in rebuilt["input"][0]["text"], str(rebuilt)[:600])
    titles = {name: run_status_service.describe_phase(7, False, "markdown-conversion", name)["title"]
              for name in ("cleanup", "rewrite", "knowledge", "")}
    harness.check("phase 7's card has one name whatever its pass", titles == {
        name: "final document reshaping" for name in ("cleanup", "rewrite", "knowledge", "")}, str(titles))
