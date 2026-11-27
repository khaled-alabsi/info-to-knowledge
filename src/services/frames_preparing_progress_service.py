"""Phase 1's and phase 2's progress. Phase 2's: its stages in order (the capture rules, the frame classifier, the redundancy model), each with
how far it is, kept in `frames-preparing.json` in the run's log folder and written each time it changes, so the page
draws a bar for phase 2 as it does for phases 3 and 4 and a killed run leaves the record where it stopped. Each stage
that ends prints one console line with its frames, its seconds and its seconds a frame.

The rules count the seconds of video sampled, the classifier the frames it tags (it reports nothing until it ends, so
its count jumps from 0 to all), the model the frames its calls have judged. Phase 1 keeps its own record,
`transcribe.json`, of the same shape: the transcription, then the speakers, each with its time (the owner: a phase
"shows no stages or steps")."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

__all__ = ["PROGRESS_FILE", "TRANSCRIBE_PROGRESS_FILE", "RULES", "CLASSIFIER", "MODEL", "SIMILARITY", "TRANSCRIPTION", "SPEAKERS",
           "TEXT_LAYER", "FIGURES", "PARSING", "MARKDOWN", "SCANNED_PAGES", "PAGE_CLEANING", "PDF_STAGES",
           "set_clock", "open_progress", "start_stage", "mark_stage_failed", "mark_running_stage_failed",
           "report_stage", "report_step", "finish_stage", "finish_transcribe_stage", "record_kept",
           "read_progress"]

PROGRESS_FILE = "frames-preparing.json"
TRANSCRIBE_PROGRESS_FILE = "transcribe.json"
RULES = "rules"
CLASSIFIER = "classifier"
MODEL = "model"
# the fourth: the frames that look alike across the model's batches, asked again
SIMILARITY = "similarity"
TRANSCRIPTION = "transcription"
SPEAKERS = "speakers"
# A PDF's phase 1, each step counted page by page (the owner: a 6,761-page PDF stood at 6,761 of 6,761 for hours while
# its reader went over the pages again): each page's text layer and links, its figures, the reader's parsing of the
# pages, its markdown written from them, the scanned pages Unlimited-OCR-2 reads, and each page's text cleaned
TEXT_LAYER = "text layer"
FIGURES = "figures"
PARSING = "parsing"
MARKDOWN = "markdown"
SCANNED_PAGES = "scanned pages"
PAGE_CLEANING = "page cleaning"
PDF_STAGES = [TEXT_LAYER, FIGURES, PARSING, MARKDOWN, SCANNED_PAGES, PAGE_CLEANING]
# what each stage counts, as the page names it; a recording's transcription counts nothing as it goes, only its time
_UNITS = {RULES: "seconds of video", CLASSIFIER: "frames", MODEL: "frames", SIMILARITY: "frames", TRANSCRIPTION: "steps",
          SPEAKERS: "steps", **{name: "pages" for name in PDF_STAGES}}

CLOCK: Callable[[], float] = time.time


def set_clock(clock: Callable[[], float]) -> Callable[[], float]:
    """Use `clock` for every time this module writes or reads, and return the one it replaces (a test seam)."""

    global CLOCK
    previous, CLOCK = CLOCK, clock

    return previous


def _read_record(run_dir: Path,
                 file_name: str) -> dict:
    path = run_dir / file_name

    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"stages": []}


def _write_record(run_dir: Path,
                  record: dict,
                  file_name: str) -> None:
    # written whole beside the record and moved over it, so a reader never sees half a file
    path = run_dir / file_name
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(record, indent=1), encoding="utf-8")
    partial.replace(path)


def _update_stage(run_dir: Path,
                  file_name: str,
                  name: str,
                  **fields) -> dict:
    """Set `fields` on stage `name` in the record on disk, and return the stage as it now stands."""

    record = _read_record(run_dir, file_name)
    stage = next(stage for stage in record["stages"] if stage["name"] == name)
    stage.update(fields)
    _write_record(run_dir, record, file_name)

    return stage


def open_progress(run_dir: Path,
                  names: list[str],
                  file_name: str = PROGRESS_FILE) -> None:
    """A fresh record of `names`, every stage waiting: phase 2 starts over on every run, so nothing of an earlier try
    is kept."""

    _write_record(run_dir, {"stages": [{"name": name, "unit": _UNITS[name], "status": "waiting", "done": 0,
                                        "total": None, "startedAtS": None, "endedAtS": None, "frames": None,
                                        "progressAtS": None}
                                       for name in names]}, file_name)


def start_stage(run_dir: Path,
                name: str,
                total: float | None,
                file_name: str = PROGRESS_FILE) -> None:
    """Stage `name` is running, with `total` units to do (None until the stage knows it)."""

    now = CLOCK()
    _update_stage(run_dir, file_name, name, status="running", total=total, startedAtS=now, progressAtS=now)


def mark_stage_failed(run_dir: Path,
               name: str,
               file_name: str = PROGRESS_FILE) -> None:
    """Stage `name` stopped by a failure: its clock ends here, so no reader counts it as still running."""

    _update_stage(run_dir, file_name, name, status="failed", endedAtS=CLOCK())


def mark_running_stage_failed(run_dir: Path,
                              file_name: str = PROGRESS_FILE) -> None:
    """The stage running when a failure stopped the phase marked failed, whichever it is; nothing when none runs."""

    record = _read_record(run_dir, file_name)
    running = next((stage for stage in record["stages"] if stage["status"] == "running"), None)
    if running is not None:
        mark_stage_failed(run_dir=run_dir, name=running["name"], file_name=file_name)


def report_stage(run_dir: Path,
                 name: str,
                 done: float,
                 total: float,
                 file_name: str = PROGRESS_FILE) -> None:
    """Stage `name` has done `done` of `total` units."""

    _update_stage(run_dir, file_name, name, done=done, total=total, progressAtS=CLOCK())


def report_step(run_dir: Path,
                name: str,
                done: float,
                total: float,
                file_name: str = PROGRESS_FILE) -> dict:
    """Stage `name` has done `done` of `total`, and is the one running: a stage before it still running is done, one
    before it that never ran is skipped (a PDF with no scanned page), and `name` starts when it waited. The stage as it
    now stands."""

    now = CLOCK()
    record = _read_record(run_dir, file_name)
    names = [stage["name"] for stage in record["stages"]]
    for stage in record["stages"][:names.index(name)]:
        if stage["status"] == "running":
            stage.update(status="done", endedAtS=now, done=stage["total"] or stage["done"])
        elif stage["status"] == "waiting":
            stage.update(status="skipped")
    stage = record["stages"][names.index(name)]
    if stage["status"] == "waiting":
        stage.update(status="running", startedAtS=now)
    stage.update(done=done, total=total, progressAtS=now)
    _write_record(run_dir, record, file_name)

    return stage


def _close_stage(run_dir: Path,
                 file_name: str,
                 name: str,
                 frames: int | None) -> str:
    """Stage `name` marked done with `frames` frames (None for a stage that counts no frame); the console line's start:
    its place, its frames and its seconds."""

    record = _read_record(run_dir, file_name)
    number = next(index for index, stage in enumerate(record["stages"], 1) if stage["name"] == name)
    ended = CLOCK()
    stage = _update_stage(run_dir, file_name, name, status="done", endedAtS=ended, frames=frames,
                          done=record["stages"][number - 1]["total"] or (frames if frames is not None else 1))
    # a stage that never reported a step closes with no time of its own
    seconds = ended - (stage["startedAtS"] or ended)
    counted = f"{frames} frame(s) in " if frames is not None else ""
    pace = f", {seconds / frames:.2f} s a frame" if frames else ""

    return f"  stage {number} of {len(record['stages'])} done ({name}): {counted}{seconds:.1f} s{pace}"


def finish_stage(run_dir: Path,
                 name: str,
                 frames: int,
                 log: logging.Logger,
                 detail: str = "") -> None:
    """Phase 2's stage `name` is done with `frames` frames, and the console says so with its seconds a frame and
    `detail`."""

    log.info(_close_stage(run_dir, PROGRESS_FILE, name, frames) + (f" · {detail}" if detail else ""))


def finish_transcribe_stage(run_dir: Path,
                            name: str,
                            log: logging.Logger,
                            detail: str) -> None:
    """Phase 1's stage `name` is done, and the console says so with its seconds and `detail`."""

    log.info(_close_stage(run_dir, TRANSCRIBE_PROGRESS_FILE, name, None) + f" · {detail}")


def record_kept(run_dir: Path,
                name: str,
                kept: int) -> None:
    """How many of stage `name`'s frames survived it: the frames a filter let through, the frames the rules took."""

    _update_stage(run_dir, PROGRESS_FILE, name, kept=kept)


def read_progress(run_dir: Path,
                  file_name: str = PROGRESS_FILE) -> list[dict]:
    """Every stage of the record, in order, as `{"name", "unit", "status", "done", "total", "frames", "kept",
    "seconds", "seconds_left", "seconds_since_progress"}` (`kept`: the frames that survived the stage, None until it is
    known): `seconds` up to now while it runs, its whole time once done, 0 while it waits. A running stage that counts
    also says the time left at its pace so far (`seconds_left`) and how long since it last counted one more
    (`seconds_since_progress`); None for every other. Empty when the run wrote none."""

    now = CLOCK()
    stages = []
    for stage in _read_record(run_dir, file_name)["stages"]:
        started, ended = stage.get("startedAtS"), stage.get("endedAtS")
        seconds = (ended or now) - started if started else 0.0
        is_running = stage["status"] == "running" and bool(started)
        is_counted = is_running and stage["total"] and 0 < stage["done"] < stage["total"]
        seconds_left = seconds / stage["done"] * (stage["total"] - stage["done"]) if is_counted else None
        last_progress = stage.get("progressAtS") or started
        stages.append({"name": stage["name"], "unit": stage["unit"], "status": stage["status"], "done": stage["done"],
                       "total": stage["total"], "frames": stage["frames"], "kept": stage.get("kept"),
                       "seconds": round(seconds, 1),
                       "seconds_left": round(seconds_left) if seconds_left is not None else None,
                       "seconds_since_progress": round(now - last_progress) if is_running else None})

    return stages
