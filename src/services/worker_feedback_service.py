"""Carry out `report_problem`: write a worker's report into `worker-feedback.md` in the run folder, and render it for
the console in full.

One file for the whole run, so one problem hit by several follow-ups is one entry with a count, not five entries that
make it look small. A report sent again with the same kind and summary raises that count. The file is markdown a
person reads; the program reads it back only to find a repeat. The detail is written as a quote block, so a `##` line
a worker quotes from its instructions never reads as a new entry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from models import output_contract
from models import worker_feedback_vocabulary
from models.worker_feedback_answer import WorkerFeedbackAnswerItem
from models.worker_feedback_outcome import WorkerFeedbackOutcome
from models.worker_feedback_report import WorkerFeedbackReport
from utils import feedback_report_utils

__all__ = ["list_open_reports", "read_answers", "read_reports", "record_report", "remove_reports",
           "resolve_kind_or_other"]

_TITLE = "# Worker feedback"
_INTRO = ("The model writes a problem here when a check refused a right attempt, an instruction was unclear, or a "
          "tool could not do the job. Each problem is listed once, with the number of times it was reported.")
_FIELDS = (("when", "When"), ("where", "Where"), ("times", "Times"))
_DETAIL_INDENT = " " * 8


def _find_stored_kind(kind: str) -> str:
    """The kind as the file stores it; "" when it is none of the vocabulary's."""

    accepted_kind = (kind or "").strip().lower()

    return accepted_kind if accepted_kind in worker_feedback_vocabulary.FEEDBACK_KINDS else ""


def resolve_kind_or_other(kind: str) -> str:
    """The kind a report written under a reply's `# Feedback` is stored as: its own when the vocabulary holds it,
    `other` when not."""

    return _find_stored_kind(kind) or "other"


def _render_console_lines(accepted_kind: str,
                          summary_line: str,
                          detail: str) -> list[str]:
    """The report as the console prints it: the kind and the summary, then every line of the detail, uncut. The owner
    reads the console live, and a report cut to one line is a report nobody can act on."""

    head = f"  WORKER FEEDBACK {accepted_kind}: {summary_line}"

    return [head, *[f"{_DETAIL_INDENT}{line}".rstrip() for line in (detail or "").strip().splitlines()]]


def _parse_entry(block: str) -> WorkerFeedbackReport:
    lines = block.splitlines()
    kind, _, summary = lines[0].partition(" · ")
    fields = dict.fromkeys((key for key, _ in _FIELDS), "")
    labels = {f"- {label}: ": key for key, label in _FIELDS}
    detail = []
    for line in lines[1:]:
        prefix = next((prefix for prefix in labels if line.startswith(prefix)), None)
        if prefix is not None:
            fields[labels[prefix]] = line[len(prefix):].strip()
        elif line.startswith(">"):
            detail.append(line[2:] if line.startswith("> ") else line[1:])

    times = int(fields["times"]) if fields["times"].isdigit() else 1

    return WorkerFeedbackReport(kind=kind.strip(), summary=summary.strip(), when=fields["when"], where=fields["where"],
                                times=times, detail="\n".join(detail))


def _render_entry(entry: WorkerFeedbackReport) -> str:
    fields = "\n".join(f"- {label}: {getattr(entry, key)}" for key, label in _FIELDS)
    detail = ("\n\n" + "\n".join(f"> {line}".rstrip() for line in entry.detail.splitlines())) if entry.detail else ""

    return f"## {entry.kind} · {entry.summary}\n\n{fields}{detail}\n"


def _write_reports(path: Path,
                   entries: list[WorkerFeedbackReport]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"{_TITLE}\n\n{_INTRO}\n", *[_render_entry(entry) for entry in entries]]),
                    encoding="utf-8")


def read_reports(path: Path) -> list[WorkerFeedbackReport]:
    """Every entry of the file that names a problem, in the order they were first reported; none when there is no file.
    An entry whose summary says only that there is nothing to report ("None") is passed over: one stored before such
    reports were refused, and copied with its run into a branch, stood on the page as "A worker reported a problem"
    (froscon talk 3143, DEFECTS 264)."""

    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    entries = [_parse_entry(block) for block in text.split("\n## ")[1:]]

    return [entry for entry in entries if not feedback_report_utils.is_empty_report(entry.summary)]


def read_answers(path: Path) -> dict[str, WorkerFeedbackAnswerItem]:
    """Each report's newest answer in the answers file, by the report's time; none when there is no file. A line in
    no answer's shape (`- <When> · times <n> · <decision>`) is passed over: the file is written by hand."""

    if not path.is_file():
        return {}
    answers = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        when, _, rest = line.removeprefix("- ").partition(" · times ")
        count, _, text = rest.partition(" · ")
        if line.startswith("- ") and count.strip().isdigit() and text.strip():
            answers[when.strip()] = WorkerFeedbackAnswerItem(when=when.strip(), times=int(count), text=text.strip())

    return answers


def list_open_reports(path: Path) -> list[WorkerFeedbackReport]:
    """The reports of the feedback file at `path` that the answers file beside it does not answer at their count: a
    report answered once and reported again since is open again, since the new case was never judged. The owner, after
    asking session after session whether the workers' reports were read: a report must be answered where it is seen."""

    answers = read_answers(path.parent / output_contract.WORKER_FEEDBACK_ANSWERS_FILE)

    return [entry for entry in read_reports(path)
            if entry.when not in answers or answers[entry.when].times < entry.times]


def remove_reports(path: Path,
                   is_removed: Callable[[str], bool]) -> int:
    """Take out every entry whose `Where` (the step it was sent from) `is_removed` names, and the file with its last
    entry. Returns how many were taken out. A reset that takes a phase back takes its reports back too: a branch
    redoing phases 3 to 6 kept phase 3's report on a copy its new phase 2 no longer took, and the page showed it as
    a problem of the new run (froscon talk 3211)."""

    if not path.is_file():
        return 0
    entries = [_parse_entry(block) for block in path.read_text(encoding="utf-8").split("\n## ")[1:]]
    kept = [entry for entry in entries if not is_removed(entry.where)]
    if len(kept) == len(entries):
        return 0
    if kept:
        _write_reports(path, kept)
    else:
        path.unlink()

    return len(entries) - len(kept)


def record_report(path: Path,
                  kind: str,
                  summary: str,
                  detail: str,
                  where: str) -> WorkerFeedbackOutcome:
    """Write one report, or raise the count of the same one: the sentence the worker reads back, and the lines the
    console prints, none for a refused report."""

    # STEP 1: refuse what cannot be counted or read, naming what is accepted
    accepted_kind = _find_stored_kind(kind)
    if not accepted_kind:
        kinds = "\n".join(f"- `{name}`: {why}" for name, why in worker_feedback_vocabulary.FEEDBACK_KINDS.items())

        return WorkerFeedbackOutcome(reply=f"Refused: {kind!r} is not a kind of report. The kinds are:\n{kinds}\n"
                                           f"Send the same report again with the closest kind.")
    summary_line = " ".join((summary or "").split())
    if feedback_report_utils.is_empty_report(summary_line):
        return WorkerFeedbackOutcome(reply="Refused: the summary names no problem. Say in one line what went wrong, and "
                                           "put what you tried in the detail; with nothing wrong, send no report.")
    console_lines = _render_console_lines(accepted_kind, summary_line, detail)

    # STEP 2: the same problem again raises its count
    entries = read_reports(path)
    for entry in entries:
        if entry.kind == accepted_kind and entry.summary == summary_line:
            entry.times += 1
            _write_reports(path, entries)

            return WorkerFeedbackOutcome(reply=f"Already reported: this is time {entry.times}, and the count is "
                                               f"recorded. Do not report it again; carry on with the follow-up.",
                                         console_lines=console_lines)

    # STEP 3: a new problem is a new entry
    entries.append(WorkerFeedbackReport(kind=accepted_kind, summary=summary_line, when=time.strftime("%Y-%m-%d %H:%M:%S"),
                                        where=where, detail=(detail or "").strip()))
    _write_reports(path, entries)

    return WorkerFeedbackOutcome(reply=f"Reported as {accepted_kind!r}; it is on record for the people building this "
                                       f"program. Carry on with the follow-up: a report never replaces your answer, and "
                                       f"it is not how you say you are done.", console_lines=console_lines)
