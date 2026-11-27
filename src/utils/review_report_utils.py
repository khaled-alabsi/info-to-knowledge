"""Phase 5's report, `review.md`, written by the program from the review's store with no model call, and where each
todo's facts were lost. REQUIREMENTS.md R16. Pure functions.

One block per todo: its kind, where it is, what was missing and why it matters, then its outcome: the change made and
the sources each new sentence rests on, or the searches made and the question added to the document's open points,
or why it was not settled. The report is for the person checking the review, so it names ids and clock times, which
the document itself never shows.
"""

from __future__ import annotations

from typing import Any

from utils import clock_utils

__all__ = ["count_outcomes", "decide_lost_at", "render_report", "FINAL_STATUSES", "LOST_AT_LABELS"]

# A todo with one of these outcomes is not asked again; any other is asked on the next life of the phase.
FINAL_STATUSES = ("resolved", "unresolved")
# Where the flow lost what a todo was missing, and how the report names it (the owner's order, 2026-09-27: the critique
# is kept to improve the flow's instructions, so each todo points at the step whose instruction to change). "" is a
# todo nobody could place: one not settled, or one whose sources the coverage table does not know.
LOST_AT_LABELS = {"frame_notes": "lost in the frame notes",
                  "outline": "set aside by the table of contents", "section": "left out by its section",
                  "section_written": "written by its section with no source",
                  "frame_notes_written": "written from frame notes that should not hold it",
                  "not_in_recording": "not in the recording", "figure_cut": "cut short by phase 4's figure cut",
                  "already_written": "nothing lost: the document already says it", "": "not placed"}
# The steps a source can reach, in the order the flow runs them: of several sources, the one that got furthest
# names the step that lost it.
_STEP_ORDER = tuple(step for step in LOST_AT_LABELS
                    if step not in ("not_in_recording", "figure_cut", "section_written", "frame_notes_written",
                                    "already_written", ""))
# Why a todo or the critique is not settled when nothing recorded a reason
_NOT_ASKED_REASON = "it was not asked yet"
# Phase 4's coverage status of an entry (`document_plan_utils.list_entry_statuses`) -> the step that lost it. An
# on-screen quote or a speech visual alignment note is kept beside the document by design: the frame notes held the words
# and never stated the facts.
_STATUS_STEPS = {"setAside": "outline", "cited": "section", "missing": "section",
                 "provenance": "frame_notes"}


def _render_sources(sentence: dict[str, Any],
                    entries_by_id: dict[str, dict[str, Any]],
                    frame_times: dict[int, float]) -> list[str]:
    sources = []
    for entry_id in [*(sentence.get("transcript_ids") or []), *(sentence.get("entry_ids") or [])]:
        entry = entries_by_id.get(entry_id)
        if entry is None:
            sources.append(entry_id)
            continue
        when = clock_utils.render_clock(float(entry["timestamps"][0]))
        sources.append(f"{entry_id} ({entry.get('kind')}, {when}): \"{entry.get('text', '')}\"")
    for frame in sentence.get("frames") or []:
        when = f" at {clock_utils.render_clock(frame_times[frame])}" if frame in frame_times else ""
        sources.append(f"frame {frame}{when}")

    return sources


def _render_tally(todos: list[dict[str, Any]],
                  key: str,
                  labels: dict[str, str]) -> list[str]:
    """One line per value of `key`, most todos first, each with the ids of its todos."""

    groups: dict[str, list[str]] = {}
    for todo in todos:
        groups.setdefault(str(todo.get(key) or ""), []).append(todo["id"])

    return [f"- {labels.get(value, value)}: {len(ids)} ({', '.join(ids)})"
            for value, ids in sorted(groups.items(), key=lambda group: -len(group[1]))]


def _render_open_point(todo: dict[str, Any]) -> list[str]:
    """The question a todo added to the open points, when it added one, and the reads its follow-up made, read off its
    own calls: found on the first real run, where the searches a worker reported included some it never made."""

    question = str(todo.get("question") or "").strip()
    lines = [f"- **Open point added:** \"{question}\""] if question else []

    return lines + [f"- **Reads made:** {'; '.join(todo.get('reads') or []) or 'none'}"]


def _render_outcome(todo: dict[str, Any],
                    entries_by_id: dict[str, dict[str, Any]],
                    frame_times: dict[int, float]) -> list[str]:
    status = todo.get("status")
    if status == "resolved" and not _is_edit(todo):
        # A point the document already answers: the report printed an empty change, 'added after "" ""' (DEFECTS 639)
        searches = "; ".join(todo.get("searches") or []) or "none"
        return [f"- **No change:** the document already says what this point asks; where, as the reviewer reported it: "
                f"{searches}"] + _render_open_point(todo)
    if status == "resolved":
        new_text = " ".join(" ".join(str(sentence.get("text") or "").split())
                            for sentence in todo.get("sentences") or [])
        where = (f"replaced \"{todo['replaces']}\" with" if todo.get("replaces")
                 else f"added after \"{todo.get('after', '')}\"")
        lines = [f"- **Change**, in \"{todo.get('editSection', '')}\": {where} \"{new_text}\""]
        for sentence in todo.get("sentences") or []:
            sources = _render_sources(sentence, entries_by_id, frame_times)
            cited = "; ".join(sources) or "none, carried over"
            text = " ".join(str(sentence.get("text") or "").split())
            lines.append(f"- **Sources** of \"{text}\": {cited}")
        return lines + _render_open_point(todo)
    if status == "unresolved":
        searches = "; ".join(todo.get("searches") or []) or "none"
        return [f"- **Searches, as it reported them:** {searches}", *_render_open_point(todo)]

    return [f"- **Not settled:** {todo.get('reason') or _NOT_ASKED_REASON}"]


def _render_audit(audit: dict[str, Any]) -> list[str]:
    """What the program removed and repaired before the critique, with no model; nothing when it did neither."""

    removed = audit.get("removedFigures") or {}
    repairs = audit.get("repairs") or []
    if not removed and not repairs:
        return []

    return ["Before the critique the program, with no model, removed these figures and made these repairs:", "",
            *[f"- Figure {number} removed: {reason}." for number, reason in removed.items()],
            *[f"- {repair}." for repair in repairs], ""]


def _render_dropped(dropped: list[dict[str, Any]]) -> list[str]:
    """The critique's points dropped since their sentences failed its checks; nothing when none was."""

    if not dropped:
        return []

    return [f"Dropped from the critique, since their sentences failed its checks through every attempt: "
            f"{len(dropped)}.", "",
            *[f"- {todo.get('answerId')} ({todo.get('kind')}), in \"{todo.get('section')}\": "
              f"{todo.get('missing')}. Why dropped: {todo.get('finding')}" for todo in dropped], ""]


def _render_todo(todo: dict[str, Any],
                 entries_by_id: dict[str, dict[str, Any]],
                 frame_times: dict[int, float]) -> list[str]:
    """One point of the critique: where it is, what was missing, where it was lost, and how it ended."""

    return [f"## {todo['id']}: {todo['kind']}, {todo.get('status')}", "",
            f"- **Where:** \"{todo['section']}\", the sentence \"{todo['quote']}\"",
            f"- **What was missing:** {todo['missing']}",
            f"- **Why it matters:** {todo['why_it_matters']}",
            f"- **Where the answer might be:** {todo['where']}",
            f"- **Lost at:** {LOST_AT_LABELS.get(str(todo.get('lost_at') or ''), todo.get('lost_at'))} "
            f"(decided by the {todo.get('lost_at_by') or 'program'})",
            *_render_outcome(todo, entries_by_id, frame_times), ""]


def count_outcomes(todos: list[dict[str, Any]]) -> dict[str, int]:
    """How many todos the critique listed, and how many ended resolved, unresolved, or neither (blocked)."""

    statuses = [todo.get("status") for todo in todos]

    return {"todos": len(todos), "resolved": statuses.count("resolved"), "unresolved": statuses.count("unresolved"),
            "blocked": sum(1 for status in statuses if status not in FINAL_STATUSES)}


def _is_edit(answer: dict[str, Any]) -> bool:
    """Whether a resolution changes the document: new sentences, or a sentence it replaces or follows."""

    return any(str(sentence.get("text") or "").strip() for sentence in answer.get("sentences") or []) or any(
        str(answer.get(key) or "").strip() for key in ("replaces", "after"))


def decide_lost_at(answer: dict[str, Any],
                   entry_statuses: dict[str, str],
                   is_stated_by_notes: bool = False) -> tuple[str, str]:
    """Where the flow lost what a resolution found, and who decided it: "program" when the sources and phase 4's
    coverage table tell, "worker" when only the worker's `lost_at` does. An unresolved todo is not in the recording;
    speech or a note the writing set aside or left out was lost there; a frame cited alone was missed by the frame
    notes. `is_stated_by_notes`: the notes the replaced sentence was written from state it."""

    if answer.get("status") == "unresolved":
        return "not_in_recording", "program"
    # Resolved with no edit: the document already said what the point asked, so nothing was lost (38c3-openpv's two
    # such points were reported as "not placed", DEFECTS 639)
    if not _is_edit(answer):
        return "already_written", "program"
    # A sentence replaced by nothing was one the writing step should not have written: the section wrote it, and
    # nothing was left out (twelve demo sentences deleted this way were reported as "not placed", then as "left out by
    # its section", which sent the fix to the wrong instruction: DEFECTS 300). One its notes state word for word came
    # from them: a slide's picture described in frame notes was filed as written with no source (DEFECTS 403)
    if str(answer.get("replaces") or "").strip() and not answer.get("sentences"):
        return ("frame_notes_written" if is_stated_by_notes else "section_written"), "program"
    steps = []
    for sentence in answer.get("sentences") or []:
        if sentence.get("frames"):
            steps.append("frame_notes")
        for source in [*(sentence.get("transcript_ids") or []), *(sentence.get("entry_ids") or [])]:
            status = entry_statuses.get(source)
            if status in _STATUS_STEPS:
                steps.append(_STATUS_STEPS[status])
    if steps:
        return max(steps, key=_STEP_ORDER.index), "program"
    worker = str(answer.get("lost_at") or "")

    return (worker, "worker") if worker else ("", "program")


def render_report(store: dict[str, Any],
                  entries_by_id: dict[str, dict[str, Any]],
                  frame_times: dict[int, float]) -> str:
    """The report of the review `store` holds."""

    lines = ["# Review of the finished document", "", *_render_audit(store.get("audit") or {})]
    critique = store.get("critique") or {}
    if store.get("todos") is None:
        reason = critique.get("reason") or _NOT_ASKED_REASON
        lines += [f"The critique could not be written: {reason}. The document "
                  "is published as phase 4 wrote it.", ""]

        return "\n".join(lines)
    todos = store["todos"]
    counts = count_outcomes(todos)
    lines += [f"A worker that did not write the document read it as a critic and listed {counts['todos']} point(s) a "
              f"reader would miss: {counts['resolved']} fixed from the sources, {counts['unresolved']} left open as a "
              f"question, {counts['blocked']} not settled. The document as it was before the review is kept in the "
              f"run folder as `05_review/before.md`.", "",
              "Where each point was lost, the step whose instruction to change first:", "",
              *_render_tally(todos, "lost_at", LOST_AT_LABELS), "", "By kind:", "",
              *_render_tally(todos, "kind", {}), ""]
    lines += _render_dropped(critique.get("dropped") or [])
    for todo in todos:
        lines += _render_todo(todo, entries_by_id, frame_times)

    return "\n".join(lines)
