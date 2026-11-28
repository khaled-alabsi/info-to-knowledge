"""How long every unit of work took and what it cost: one line each in `performance.jsonl`, written the moment the
unit ends, and the same line printed to the console.

The file sits in the run's directory beside `calls.jsonl`, which keeps one line per model call. This one keeps one
line per UNIT the run is made of:

  session  a command started: which phases it was asked for. Every later line names its session, so the lines of
           a resumed run are told apart.
  item     one frame asked alone
  batch    several frames asked in one call
  part     one follow-up of phase 4's or phase 5's conversation (or one call of phase 4's calls path)
  phase    one phase, when it ends in this session
  video    the whole session, when the run ends or is stopped, against the recording's length

A unit's calls, tokens and model time are READ from `calls.jsonl`: its phase, its slug, the attempts it made. A
phase's figures are read from its unit lines of the same session, and the video's from its phase lines. Nothing is
summed in memory, so a run killed at frame forty keeps thirty-nine lines, and the closing table is a read of the file.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_client import finish_reason_utils
from models import output_contract
from models import phase_guide
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from services import batch_summaries_service
from services import call_log_service
from services import call_record_service
from services import control_signal_service
from utils import json_lines_utils

__all__ = ["PERFORMANCE_FILE", "UNIT_LEVELS", "PART_LEVEL", "RESET_LEVEL", "read_rows", "record_reset", "open_session", "start_unit", "record_unit",
           "record_phase", "record_phase_end", "record_video", "render_performance_table", "render_listed_frames_header",
           "render_batch_follow_up_header"]

PERFORMANCE_FILE = "performance.jsonl"
ITEM_LEVEL = "item"
BATCH_LEVEL = "batch"
# A step of a phase that is not a frame or a batch of frames: a one-call phase's call, a document part
PART_LEVEL = "part"
# A line `i2k reset-to` writes: the rows of later phases before it are no longer the run's
RESET_LEVEL = "reset"
UNIT_LEVELS = (ITEM_LEVEL, BATCH_LEVEL, PART_LEVEL)
_SUMMED = ("calls", "promptTokens", "outputTokens", "callS", "promptS", "outputS", "cutAtLimit", "loopStopped")
# A batch's question calls (open questions, answers) are about the batch, not its frames, so their line gives no time
# per frame
_QUESTION_CALL_SUFFIXES = (f"-{batch_summaries_service.OPEN_QUESTIONS_SUFFIX}", f"-{batch_summaries_service.ANSWERS_SUFFIX}")
_COLUMNS = ("phase", "status", "seconds", "frames", "s per frame", "calls", "prompt tok", "output tok", "output tok/s")
# Left out of an audio recording's table: it has no frame
_FRAME_COLUMNS = ("frames", "s per frame")


def _append_row(run_dir: Path,
                row: dict[str, Any]) -> None:
    json_lines_utils.append_json_line(run_dir / PERFORMANCE_FILE, row)


def _read_session(rows: list[dict[str, Any]]) -> str:
    """The session the newest `session` line opened. Empty when no command opened one, as in a node's own test."""

    sessions = [row["session"] for row in rows if row.get("level") == "session"]

    return sessions[-1] if sessions else ""


def _compute_rate(tokens: float,
                  seconds: float) -> float | None:
    return round(tokens / seconds, 1) if seconds else None


def _compute_sums(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The summed cost of these lines, and the output speed that follows from it."""

    sums = {key: round(sum(row.get(key) or 0 for row in rows), 1) for key in _SUMMED}
    for key in ("calls", "promptTokens", "outputTokens", "cutAtLimit", "loopStopped"):
        sums[key] = int(sums[key])
    sums["outputTokPerS"] = _compute_rate(sums["outputTokens"], sums["outputS"])

    return sums


def _list_unit_calls(ctx: RunContext,
                     handles: TimingHandles) -> list[dict[str, Any]]:
    """The calls this unit made, retries included, as `calls.jsonl` recorded them."""

    return [row for row in call_log_service.read_call_metrics(ctx.run_dir)
            if row.get("phase") == handles.phase and row.get("slug") == handles.slug
            and int(row.get("attempt") or 0) >= handles.first_attempt]


def _list_calls_since(ctx: RunContext,
                      phase: str,
                      started_epoch_s: float) -> list[dict[str, Any]]:
    """The calls of `phase` that started at or after `started_epoch_s`, for a phase that made calls in no unit of its
    own (phase 2's model stages)."""

    # a row's start is kept to the millisecond: a call in the phase's first millisecond, compared with the unrounded
    # start, read as made before the phase
    started_ms_s = round(started_epoch_s, 3)

    return [row for row in call_log_service.read_call_metrics(ctx.run_dir)
            if row.get("phase") == phase and (row.get("startedAtS") or 0) >= started_ms_s]


def _compute_call_sums(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The summed cost of these `calls.jsonl` lines."""

    rows = [{"calls": 1, "promptTokens": row.get("promptTokens"), "outputTokens": row.get("outputTokens"),
             "callS": row.get("durationS"), "promptS": row.get("promptDurationS"), "outputS": row.get("outputDurationS"),
             # A reply ended for repeating its thinking is counted apart, named for what happened to it
             "cutAtLimit": int(row.get("finishReason") == finish_reason_utils.CUT_AT_OUTPUT_LIMIT),
             "loopStopped": int(row.get("finishReason") == finish_reason_utils.THINKING_LOOPED)}
            for row in calls]
    # The LAST call's size, not the sum: it is what filled the window, and the window it was measured against.
    last = calls[-1] if calls else {}

    return {**_compute_sums(rows), "lastPromptTokens": last.get("promptTokens"),
            "lastOutputTokens": last.get("outputTokens"), "windowTokens": last.get("windowTokens")}


def _read_elapsed_s(handles: TimingHandles) -> float:
    """The unit's time since it started, less the time the run waited out a pause meanwhile (none for a session)."""

    paused = (0.0 if handles.paused_at_start_s is None
              else control_signal_service.read_paused_seconds() - handles.paused_at_start_s)

    return round(time.monotonic() - handles.started_monotonic_s - paused, 1)


def _compute_per_frame(duration_s: float,
                       frame_count: int) -> float | None:
    return round(duration_s / frame_count, 1) if frame_count else None


def _render_cost(row: dict[str, Any]) -> str:
    """The calls, tokens and output speed of one line, in the words the console uses."""

    if not row.get("calls"):
        return "no model call"
    speed = f", {row['outputTokPerS']:.1f} output tokens a second" if row.get("outputTokPerS") is not None else ""
    cut = f", {row['cutAtLimit']} cut at the output limit" if row.get("cutAtLimit") else ""
    cut += f", {row['loopStopped']} stopped for repeating its thinking" if row.get("loopStopped") else ""

    return (f"{row['calls']} call{'s' if row['calls'] > 1 else ''}, {row['promptTokens']:,} prompt + "
            f"{row['outputTokens']:,} output tokens{speed}{cut}")


def _render_phase_duration(row: dict[str, Any]) -> str:
    frames = row.get("frames") or 0
    if not frames:
        return f"{row['durationS']:.1f} s"
    # A label a person reads is a sentence: "a" is no word for "per", let alone for "on average".
    per_frame = f" (one frame takes {row['secondsPerFrame']:.1f} s on average)" if frames > 1 else ""

    return f"{row['durationS']:.1f} s for {frames} frame{'s' if frames > 1 else ''}{per_frame}"


def _render_clock(seconds: float) -> str:
    """`4:39`, or `1:02:03` past an hour: how a person reads a duration or a place in a recording."""

    whole = int(round(seconds))
    hours, minutes, rest = whole // 3600, whole // 60 % 60, whole % 60

    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def _list_frame_items(ctx: RunContext,
                      frame_slugs: list[str]) -> list[dict]:
    return [ctx.store.get_item(frame_slug) for frame_slug in frame_slugs]


def _render_frame_span(numbers: list[int]) -> str:
    """`frame 20`, or `frames 20 → 23`: the span the console shows for a run of frames."""

    return f"frame {numbers[0]}" if len(numbers) == 1 else f"frames {numbers[0]} → {numbers[-1]}"


def _render_unit_name(ctx: RunContext,
                      slug: str,
                      frame_slugs: list[str]) -> str:
    """`reading frames 20 → 23 of 321 at 6:59` for a batch of frames, with its verb, also when copies in between were passed over; a part's own slug."""

    if not frame_slugs:
        return slug
    items = _list_frame_items(ctx, frame_slugs)
    numbers = [int(item["index"]) for item in items]
    span = _render_frame_span(numbers)

    return _render_reading_line(ctx, span, items[0])


def _render_reading_line(ctx: RunContext,
                         span: str,
                         first_item: dict) -> str:
    # "of" names the last frame's number when frames were dropped and the numbers run past the count: "frames 173 →
    # 174 of 173" read as past the end with 16 frames still to go (seen on a lecture)
    total = ctx.store.count_by_status()["total"]
    last = max((int(item.get("index") or 0) for item in ctx.store.list_items().values()), default=0)

    return f"reading {span} of {max(total, last)} at {_render_clock(float(first_item.get('timestamp') or 0.0))}"


def render_batch_follow_up_header(ctx: RunContext,
                                  frame_slugs: list[str],
                                  verb: str) -> str:
    """`fix from the critique of frames 20 → 29 (10 frames, 6:59)`: the header of a follow-up on a batch the worker already
    wrote, named for what it does ("checking" and "asking about" said nothing)."""

    items = _list_frame_items(ctx, frame_slugs)
    numbers = [int(item["index"]) for item in items]
    span = _render_frame_span(numbers)
    clock = _render_clock(float(items[0].get("timestamp") or 0.0))

    return f"{verb} {span} ({len(numbers)} frame{'s' if len(numbers) != 1 else ''}, {clock})"


def render_listed_frames_header(ctx: RunContext,
                                frame_slugs: list[str]) -> str:
    """`reading frames 1, 4, 16, 18 of 321 at 0:11`: the header of a unit that asks about NAMED frames rather than a
    batch, which is what a repair of the frames a group got wrong does. As a span it claimed 18 frames where it
    asked about 4. Passed as `start_unit`'s `label`."""

    items = _list_frame_items(ctx, frame_slugs)
    numbers = ", ".join(str(int(item["index"])) for item in items)

    return _render_reading_line(ctx, f"frame{'s' if len(items) != 1 else ''} {numbers}", items[0])


def _render_status(row: dict[str, Any]) -> str:
    """`accepted` when every frame passed, the count when some did not, and a cut named first."""

    status = str(row["status"])
    counted = re.fullmatch(r"(\d+) of (\d+) accepted(.*)", status)
    if status in ("written", "accepted"):
        status = "accepted"
    elif counted and counted.group(1) == counted.group(2):
        status = f"accepted{counted.group(3)}"

    ends = [*(["cut at output limit"] if row.get("cutAtLimit") else []),
            *(["stopped looping"] if row.get("loopStopped") else [])]

    return " · ".join([*ends, status])


def _render_unit_outcome(row: dict[str, Any]) -> str:
    """What a unit printed when it ended: how long it took, its tokens, how full the window was, how it ended."""

    is_about_frames = not str(row.get("slug") or "").endswith(_QUESTION_CALL_SUFFIXES)
    per_frame = (f" (one frame takes {row['secondsPerFrame']:.0f} s on average)"
                 if (row.get("frames") or 0) > 1 and is_about_frames else "")
    parts = [f"{_render_clock(row['durationS'])}{per_frame}"]
    if row.get("calls"):
        parts.append(f"{row['promptTokens']:,} in / {row['outputTokens']:,} out")
    if row.get("windowTokens") and row.get("lastPromptTokens") is not None:
        used = (row["lastPromptTokens"] or 0) + (row.get("lastOutputTokens") or 0)
        parts.append(f"context {int(used * 100 / row['windowTokens'])}% of {row['windowTokens']:,}")
    parts.append(_render_status(row))

    return " · ".join(parts)


def read_rows(run_dir: Path) -> list[dict[str, Any]]:
    """Every complete line but those of a phase a later reset took back. A line a kill cut in half is dropped, never a
    reason to lose the rest."""

    return _drop_rows_reset(json_lines_utils.read_json_lines(run_dir / PERFORMANCE_FILE))


def record_reset(run_dir: Path,
                 phase: int) -> None:
    """A line saying the run was taken back to `phase`: the rows of every later phase written before it are no longer
    the run's. Without it, a phase 3 redone after `reset-to 1` was timed from its first run a month before, and the
    page's batch table listed the old batches beside the new (38c3-openpv)."""

    _append_row(run_dir, {"level": RESET_LEVEL, "toPhase": phase,
                          "at": datetime.now().isoformat(timespec="milliseconds"), "atS": time.time()})


def _drop_rows_reset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`rows` without each row of a phase after a reset's `toPhase` written before that reset, and without the resets."""

    kept: list[dict[str, Any]] = []
    for row in rows:
        if row.get("level") != RESET_LEVEL:
            kept.append(row)
            continue
        phase = int(row.get("toPhase") or 0)
        kept = [earlier for earlier in kept if _find_phase_number(earlier) <= phase]

    return kept


def _find_phase_number(row: dict[str, Any]) -> int:
    """The number of the phase a row is of; 0 for a row of no phase (a session, the video's totals)."""

    name = str(row.get("phase") or "")

    return output_contract.PHASE_NUMBERS.get(output_contract.RETIRED_STEP_NAMES.get(name, name), 0)


def start_unit(ctx: RunContext,
               phase: str,
               slug: str | None,
               frame_slugs: list[str] | None = None,
               label: str | None = None) -> TimingHandles:
    """The start of a frame, a group, a part or a phase: now, and the first attempt its calls will carry. A frame, a
    group or a part says what it starts, so a long call never leaves the console silent: its `label` when it has one
    (a phase-4 follow-up's place, `outline 3 of 11`), its own name when `label` is None or empty. A phase says
    nothing."""

    now = datetime.now()
    name = label or (_render_unit_name(ctx, slug, frame_slugs or []) if slug else "")
    if name:
        ctx.log.info(f"{name} …")
    handles = TimingHandles(phase=phase, slug=slug, started_monotonic_s=time.monotonic(),
                            started_at=now.isoformat(timespec="milliseconds"), started_epoch_s=now.timestamp(),
                            first_attempt=ctx.store.count_attempts(phase, slug) + 1,
                            paused_at_start_s=control_signal_service.read_paused_seconds())
    # every model call from now on is a row of this phase and step, whichever code makes it
    call_record_service.set_place(run_dir=ctx.run_dir,
                                  phase=phase,
                                  slug=slug,
                                  attempt=handles.first_attempt)

    return handles


def open_session(ctx: RunContext,
                 first_phase: int,
                 last_phase: int) -> TimingHandles:
    """A command starts: one `session` line, and the handles its closing `video` line is timed from."""

    handles = start_unit(ctx, "run", None)
    _append_row(ctx.run_dir, {"level": "session", "session": handles.started_at, "startedAt": handles.started_at,
                              "module": ctx.module, "phases": f"{first_phase}..{last_phase}"})

    return handles


def record_unit(ctx: RunContext,
                handles: TimingHandles,
                level: str,
                status: str,
                frame_slugs: list[str]) -> dict[str, Any]:
    """A frame, a group or a part has ended: its line, with the calls it made, on disk and on the console."""

    duration_s = _read_elapsed_s(handles)
    row = {"level": level, "session": _read_session(read_rows(ctx.run_dir)), "phase": handles.phase,
           "slug": handles.slug, "startedAt": handles.started_at, "startedAtS": round(handles.started_epoch_s, 3),
           "durationS": duration_s, "status": status,
           "attempts": ctx.store.count_attempts(handles.phase, handles.slug) - handles.first_attempt + 1,
           "frames": len(frame_slugs), "frameSlugs": frame_slugs,
           "secondsPerFrame": _compute_per_frame(duration_s, len(frame_slugs)),
           **_compute_call_sums(_list_unit_calls(ctx, handles))}
    _append_row(ctx.run_dir, row)
    # A unit's name was printed when it started, so its outcome goes under it
    ctx.log.info(f"  {_render_unit_outcome(row)}")

    return row


def record_phase(ctx: RunContext,
                 phase: str,
                 status: str,
                 handles: TimingHandles | None) -> dict[str, Any]:
    """A phase has ended in this session: its line, summed from its unit lines. A phase run as one step is timed
    from its handles; the frame loop, one step per frame, from its first unit line of the session."""

    rows = read_rows(ctx.run_dir)
    session = _read_session(rows)
    units = [row for row in rows if row.get("session") == session and row.get("level") in UNIT_LEVELS
             and row.get("phase") == phase]
    if handles is not None:
        started_at, started_epoch_s, duration_s = handles.started_at, handles.started_epoch_s, _read_elapsed_s(handles)
    else:
        started_epoch_s = min((row["startedAtS"] for row in units), default=time.time())
        started_at = datetime.fromtimestamp(started_epoch_s).isoformat(timespec="milliseconds")
        duration_s = round(time.time() - started_epoch_s, 1)
    frame_count = len({slug for row in units for slug in row.get("frameSlugs") or []})
    # The phase's cost: summed from its unit lines when it has any (or when it has no handles to say when it began),
    # else from the model calls made since it started
    if units or handles is None:
        cost = _compute_sums(units)
    else:
        cost = _compute_call_sums(_list_calls_since(ctx, phase, handles.started_epoch_s))
    row = {"level": "phase", "session": session, "phase": phase, "number": output_contract.PHASE_NUMBERS[phase],
           "startedAt": started_at, "startedAtS": round(started_epoch_s, 3), "durationS": duration_s, "status": status,
           "units": len(units), "frames": frame_count, "secondsPerFrame": _compute_per_frame(duration_s, frame_count),
           **cost}
    _append_row(ctx.run_dir, row)
    ctx.log.info(f"phase {row['number']} time: {_render_phase_duration(row)}, {_render_cost(row)}, {status}")

    return row


def record_phase_end(ctx: RunContext,
                     phase: str,
                     status: str,
                     handles: TimingHandles | None) -> None:
    """A phase node's end: its status in the run record, and its line here when it was timed (`handles`). The one
    place every phase node records how it ended."""

    ctx.store.set_phase(phase, status)
    if handles is not None:
        record_phase(ctx, phase, status, handles)


def _render_phase_name(row: dict[str, Any],
                       is_audio: bool,
                       is_direct_draft: bool = False) -> str:
    """A phase's name in the closing table: an audio recording's phases 2 and 3 by what they do there, a direct-draft
    run's phases 3 and 4 by theirs."""

    title = phase_guide.AUDIO_PHASE_TITLES.get(row.get("number")) if is_audio else None
    if title is None and is_direct_draft and not is_audio:
        title = phase_guide.DIRECT_DRAFT_PHASE_TITLES.get(row.get("number"))

    return title.replace(" ", "_") if title else str(row["phase"])


def render_performance_table(phases: list[dict[str, Any]],
                             video: dict[str, Any],
                             is_audio: bool = False,
                             is_direct_draft: bool = False) -> str:
    """The closing table: one row per phase this session ran, and the recording. An audio recording has no frame
    column and names its phases 2 and 3 by what they do there; a direct-draft run names its phases 3 and 4 by theirs."""

    medium = output_contract.AUDIO_MEDIUM if is_audio else output_contract.VIDEO_MEDIUM
    columns = [column for column in _COLUMNS if not (is_audio and column in _FRAME_COLUMNS)]
    body = []
    for row in [*phases, {**video, "phase": medium}]:
        per_frame = f"{row['secondsPerFrame']:.1f}" if row.get("secondsPerFrame") is not None else "-"
        speed = f"{row['outputTokPerS']:.1f}" if row.get("outputTokPerS") is not None else "-"
        cells = dict(zip(_COLUMNS, (_render_phase_name(row, is_audio, is_direct_draft), str(row.get("status") or "-"),
                                    f"{row['durationS']:.1f}", str(row.get("frames") or 0), per_frame,
                                    str(row.get("calls") or 0), str(row.get("promptTokens") or 0),
                                    str(row.get("outputTokens") or 0), speed)))
        body.append([cells[column] for column in columns])
    widths = [max(len(columns[index]), *(len(line[index]) for line in body)) for index in range(len(columns))]
    lines = ["  ".join(title.ljust(widths[index]) for index, title in enumerate(columns))]
    lines.extend("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)) for line in body)
    if video.get("videoS"):
        lines.append(f"{video['videoS'] / 60:.1f} min of {medium} in {video['durationS'] / 60:.1f} min: "
                     f"{video['realTimeFactor']:.3f} s of work per second of {medium}")

    return "\n".join(lines)


def record_video(ctx: RunContext,
                 handles: TimingHandles,
                 status: str,
                 video_s: float | None,
                 is_audio: bool = False,
                 is_direct_draft: bool = False) -> dict[str, Any]:
    """The session has ended: the video line, summed from this session's phase lines, and the closing table. A phase
    the session left unfinished (a stop between two frames) has unit lines and no phase line of its own; it gets one
    first, marked with the session's status, or the video line would report none of its work."""

    rows = read_rows(ctx.run_dir)
    session = _read_session(rows)
    closed = {row["phase"] for row in rows if row.get("session") == session and row.get("level") == "phase"}
    unfinished_phases = list(dict.fromkeys(
        row["phase"] for row in rows
        if row.get("session") == session and row.get("level") in UNIT_LEVELS and row["phase"] not in closed))
    for phase in unfinished_phases:
        record_phase(ctx, phase, status, None)
    # Read again: the phase lines just written are part of the sum
    rows = read_rows(ctx.run_dir)
    phases = [row for row in rows if row.get("session") == session and row.get("level") == "phase"]
    duration_s = _read_elapsed_s(handles)
    frame_count = sum(row.get("frames") or 0 for row in phases)
    row = {"level": "video", "session": session, "module": ctx.module, "startedAt": handles.started_at,
           "startedAtS": round(handles.started_epoch_s, 3), "durationS": duration_s, "status": status,
           "videoS": video_s, "realTimeFactor": round(duration_s / video_s, 3) if video_s else None,
           "phases": [phase["phase"] for phase in phases], "frames": frame_count,
           "secondsPerFrame": _compute_per_frame(duration_s, frame_count), **_compute_sums(phases)}
    _append_row(ctx.run_dir, row)
    ctx.log.info("")
    ctx.log.info(render_performance_table(phases, row, is_audio, is_direct_draft))

    return row
