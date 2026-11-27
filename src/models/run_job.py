"""What the UI's queue holds: one request to run `i2k run` over one video, and the job it became.

A job is the child process `main.py run ...` started for one request. Its outcome is read from the
exit code `run` already defines (0, 1, 2, 130), so the queue never decides on its own whether a run
went well.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["EXIT_INTERRUPTED", "EXIT_STATUSES", "JOB_QUEUED", "JOB_RUNNING", "JOB_CRASHED", "JOB_CANCELLED",
           "JOB_LOST", "JOB_RETRYING", "JOB_BLOCKED", "RETRIED_STATUSES", "ON_FAILURE_CHOICES", "RunJobRequest",
           "RunJob"]

# `run`'s exit codes, as the status a job ends with. Any other code is `crashed`. -2 is a process the interrupt ended
# before `run` could answer it with 130 (while it still loads its libraries): stopped all the same.
EXIT_INTERRUPTED = 130
EXIT_STATUSES = {0: "finished", 1: "incomplete", 2: "refused", EXIT_INTERRUPTED: "stopped", -2: "stopped"}

# Waiting for the job before it to end
JOB_QUEUED = "queued"
# Its child process is alive
JOB_RUNNING = "running"
# Its process could not start, or ended with an exit code `run` does not define
JOB_CRASHED = "crashed"
# Taken off the queue before it started
JOB_CANCELLED = "cancelled"
# Its child process ended while no server was waiting on it, so its exit code is unknown
JOB_LOST = "lost"
# It failed and waits for its next try (`retry_at`); no other job starts before it
JOB_RETRYING = "retrying"
# It failed its last try and was set to hold the queue: no job starts until the person retries or skips it
JOB_BLOCKED = "blocked"
# The ends a job is run again after: `run` gave up on a step (exit 1), the process ended in a way `run` does not
# define, or no server saw its end while its run's own record says the run did not complete. A stopped job was stopped
# by the person, and a refused line is refused again
RETRIED_STATUSES = (EXIT_STATUSES[1], JOB_CRASHED, JOB_LOST)
# What the queue does after a job's last try: start the next job, or hold until the person decides
ON_FAILURE_CHOICES = ("continue", "block")

# The request's tuple fields: a list in its JSON form, a tuple again when read back, so a request read from
# `ui/jobs.json` equals the one written
_TUPLE_FIELDS = ("worker_indexes", "tags", "phase_models")
# Fields a request no longer has, which an older `ui/jobs.json` still holds: `pdf_reader`, the PDF reader choice
# removed with MinerU
_RETIRED_FIELDS = ("pdf_reader",)


@dataclass(frozen=True)
class RunJobRequest:
    """One `i2k run` line: which video, which run folder, and which phases."""

    video: Path
    output_root: Path
    only_phase: int | None = None
    from_phase: int | None = None
    to_phase: int | None = None
    worker_indexes: tuple[int, ...] = ()
    is_resuming: bool = False
    is_dry_run: bool = False
    # A settings profile (`playbook.<name>.json`) for this job alone; empty takes the server's own
    profile: str = ""
    # Tags written into the run folder before phase 1 (`--tag`); a tag naming an ingesting profile picks it
    tags: tuple[str, ...] = ()
    # The ingesting profile chosen by hand (`--ingesting-profile`); empty leaves it to a tag or phase 2's call
    ingesting_profile: str = ""
    # `--as-audio`: a video processed as an audio recording, its sound alone
    is_run_as_audio: bool = False
    # `--pdf-mode`: how a PDF is read, `pages-as-images` (each page a picture, read in batches) or `text-only` (its text
    # alone); empty is pages-as-images
    pdf_mode: str = ""
    # `--pdf-ocr-engine`: what reads a PDF's scanned pages in phase 1, `tesseract` or `unlimited-ocr`; empty leaves the
    # folder's or the settings' `pdf.ocrEngine`
    pdf_ocr_engine: str = ""
    # `--document-pass` and `--document-wishes`: phase 7's clean up or rewrite, and the person's wishes for it; empty
    # leaves the folder's choice
    document_pass: str = ""
    document_wishes: str = ""
    # `--document-source`: what that pass starts from, `original` or `current`; empty leaves the original
    document_source: str = ""
    # `--knowledge-filter-model`: which model drops the frames with no knowledge in phase 2, `google-SigLIP-2` or
    # `liquidai-d1-2B`; empty leaves the folder's or the settings' `frames.knowledgeFilterModel`
    knowledge_filter_model: str = ""
    # `--start`, `--end` and `--pages`: the part of the input the run processes; empty leaves the whole input
    range_start: str = ""
    range_end: str = ""
    range_pages: str = ""
    # `--phase-model PHASE=NAME` pairs: the configured model a phase calls in this job alone
    phase_models: tuple[str, ...] = ()
    # `--workflow`: the video workflow from phase 3 on (quality, direct-draft or fast-processing); empty leaves the
    # folder's or the settings'
    workflow: str = ""
    # `--worker-set`: the worker set (an entry of the settings' `workerSets`, or `default`) the run is on, whose models and
    # hosts it calls; jobs of one set run one after another, jobs of different sets at the same time. Empty leaves the
    # folder's set, else the settings' `defaultWorkerSet`
    worker_set: str = ""
    # `--speed-preset` and `--speed-setting KEY=VALUE` pairs: the run's Workflow speed preset and the values changed
    # from it; empty leaves the folder's or the settings'
    speed_preset: str = ""
    speed_settings: tuple[str, ...] = ()
    # `--force`: past the processed-once check. Set only by a conversion to another ingesting profile, whose run folder
    # is a copy of a done run of the same recording; the page's run form never sets it
    is_forced: bool = False
    # What the job is for, in words, when the run line alone does not say it ("convert to knowledge · phase 4 only
    # (document writing)"); the queue shows it in place of the phase range
    purpose: str = ""
    # What the queue does after this job's last failed try, "continue" or "block"; empty takes `queue.onFailure`
    on_failure: str = ""

    def as_dict(self) -> dict:
        """The JSON form `ui/jobs.json` stores and the API returns."""

        return {**asdict(self), "video": str(self.video), "output_root": str(self.output_root),
                **{name: list(getattr(self, name)) for name in _TUPLE_FIELDS}}

    @classmethod
    def from_dict(cls,
                  values: dict) -> RunJobRequest:
        """The request back from its JSON form."""

        kept = {name: value for name, value in values.items() if name not in _RETIRED_FIELDS}

        return cls(**{**kept, "video": Path(values["video"]), "output_root": Path(values["output_root"]),
                      **{name: tuple(values.get(name) or ()) for name in _TUPLE_FIELDS}})


@dataclass
class RunJob:
    """One request in the queue, and what happened to it."""

    job_id: str
    request: RunJobRequest
    status: str = JOB_QUEUED
    pid: int | None = None
    exit_code: int | None = None
    queued_at: str = ""
    started_at: str = ""
    ended_at: str = ""
    command: list[str] = field(default_factory=list)
    # How many times its process was started: 1 on its first try, one more for each retry
    attempt: int = 0
    # When a job waiting for its retry may start again; empty when it waits for nothing
    retry_at: str = ""

    def as_dict(self) -> dict:
        """The JSON form `ui/jobs.json` stores and the API returns."""

        return {**asdict(self), "request": self.request.as_dict()}

    @classmethod
    def from_dict(cls,
                  values: dict) -> RunJob:
        """The job back from its JSON form."""

        return cls(**{**values, "request": RunJobRequest.from_dict(values["request"])})
