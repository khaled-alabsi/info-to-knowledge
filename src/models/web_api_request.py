"""The bodies the UI's API accepts, as the web page and the terminal UI send them."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from models import output_contract

__all__ = ["JobItemRequest", "RunSelectionRequest", "StartJobsRequest", "QueueRunRequest", "ControlRequest",
           "ResetRequest", "FrameDropRequest", "MoveRequest", "RestoreRequest", "ZipRequest", "HiddenRequest",
           "ConvertRequest", "BranchRequest", "RunTagsRequest", "RunRef", "BatchDownloadRequest", "RunNamesRequest",
           "InboxTagsRequest", "RenameRequest", "MergeRequest", "MarkdownWriteRequest", "SettingRequest", "SettingRemoveRequest",
           "ModelsDownloadRequest", "ModelsConfigureRequest", "SignInRequest", "AccountRequest", "AccountChangeRequest",
           "InstructionSaveRequest", "InstructionResetRequest", "InstructionPreviewRequest", "PhaseFlowRequest"]


class JobItemRequest(BaseModel):
    """One video to run, and the run folder it writes into."""

    video: str
    output: str


class RunSelectionRequest(BaseModel):
    """What a queued run is asked to do, the same for the page's jobs and an agent's `queue_run`: which phases, on which
    settings and ingesting profile, and whether a video runs as audio. One home, so a field renamed here is renamed for
    both."""

    only_phase: int | None = None
    from_phase: int | None = None
    to_phase: int | None = None
    is_resuming: bool = False
    is_dry_run: bool = False
    # `playbook.<name>.json` for these jobs; empty or absent runs on the server's own settings
    profile: str = ""
    # The worker set (`default` or a name in the settings' `workerSets`) these runs are on; empty leaves each run
    # folder's set, else the settings' `defaultWorkerSet`
    worker_set: str = ""
    # The ingesting profile chosen by hand (`takeaway`, `meeting`, ...); empty leaves it to a tag or phase 2's call, and
    # once chosen no tag and no model call changes it
    ingesting_profile: str = ""
    # A video processed as an audio recording, its sound alone, no frame taken
    is_run_as_audio: bool = False
    # How a PDF is read: `pages` (each page a frame, read in batches) or `markdown` (its text alone); empty is pages
    pdf_mode: Literal["", "pages-as-images", "text-only", "markdown-conversion"] = ""
    # What reads a PDF's scanned pages in phase 1 (`tesseract`, `unlimited-ocr`); empty leaves the folder's or the
    # settings'
    pdf_ocr_engine: Literal["", "tesseract", "unlimited-ocr"] = ""
    # Phase 7, after the document is published: a model cleans it up, rewrites it or writes it as knowledge; empty
    # leaves the folder's choice. Checked against the program's one list of passes, never a copy of it here.
    document_pass: str = ""
    # The person's own wishes for that pass ("remove every Jira link")
    document_wishes: str = Field(default="", max_length=4_000)
    # What that pass starts from: the original (the document before phase 7's first pass) or the current document
    document_source: Literal["", "original", "current"] = ""
    # Which model drops the frames with no knowledge in phase 2 (`google-SigLIP-2`, `liquidai-d1-2B`); empty leaves
    # the folder's or the settings'
    knowledge_filter_model: Literal["", "google-SigLIP-2", "liquidai-d1-2B"] = ""
    # The part of the input the run processes: a start and an end time (seconds, m:ss or h:mm:ss) for a recording, or
    # "first-last" pages for a PDF; empty is the whole input. Checked as `run` checks them before the job is queued
    range_start: str = Field(default="", max_length=20)
    range_end: str = Field(default="", max_length=20)
    range_pages: str = Field(default="", max_length=20)
    # The video workflow from phase 3 on (`quality`, `direct-draft`, `fast-processing`); empty leaves the folder's or the settings'
    workflow: str = ""
    # The Workflow speed preset and the settings changed from it by hand; empty leaves the folder's or the settings'
    speed_preset: str = ""
    speed_settings: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator("document_pass")
    @classmethod
    def check_document_pass(cls,
                            pass_name: str) -> str:
        if pass_name and pass_name not in output_contract.DOCUMENT_PASSES:
            raise ValueError(f"document_pass {pass_name!r} is none of '', "
                             f"{', '.join(output_contract.DOCUMENT_PASSES)}")

        return pass_name


class StartJobsRequest(RunSelectionRequest):
    """Run each item with the same phase selection, one after another."""

    items: list[JobItemRequest]
    worker_indexes: list[int] = []
    # Tags the run folder gets before phase 1
    tags: list[str] = []
    # phase -> the configured model it calls in these jobs; a phase left out uses the settings' choice
    phase_models: dict[str, str] = {}


class QueueRunRequest(JobItemRequest, RunSelectionRequest):
    """One run an agent queues through the MCP tool `queue_run`, as the page's Run form does: every phase, from a phase,
    up to a phase or one phase, optionally on a settings profile and in an ingesting profile chosen by hand."""


class WorkerSetRequest(BaseModel):
    """The worker set a run that is not running is given; its next phases go on with it."""

    worker_set: str


class ControlRequest(BaseModel):
    """`pause`, `drain`, `stop` or `continue`."""

    signal: str


class ResetRequest(BaseModel):
    """`reset-to N` (kind `phase`) or `reset-chapter N` (kind `chapter`); without `is_confirmed` it only lists."""

    kind: str
    number: int
    is_confirmed: bool = False
    # a chapter reset that keeps the chapters' draft headings and takes back only their texts (`--text-only`)
    is_text_only: bool = False


class FrameDropRequest(BaseModel):
    """Frames dropped from a run before phase 3 reads them; `proposed` are the frames a Refine proposed to drop and
    `drop_scores` its score of each frame it judged, both empty for frames chosen with no Refine."""

    frames: list[str]
    proposed: list[str] = []
    drop_scores: dict[str, float] = {}


class RunListEntry(BaseModel):
    """One of the person's own lists: its id, its name and the rows in it, by the keys the page gives them."""

    id: str
    name: str
    members: list[str] = []


class RunListsRequest(BaseModel):
    """Every one of the person's lists, saved whole, in order."""

    lists: list[RunListEntry] = []


class MoveRequest(BaseModel):
    """The inbox videos to move into `inbox/.trash/` or `inbox/.archive/`, by the paths the inbox listing gives, and
    the run folders no inbox video owns to move there whole, as `<root>/<name>`."""

    videos: list[str] = []
    run_folders: list[str] = []


class RestoreRequest(BaseModel):
    """The trash or archive entries to move back where they came from, by name."""

    names: list[str]


class ZipRequest(BaseModel):
    """Artifacts of one run folder to download together, by their paths in it, and a word for the zip's name."""

    paths: list[str]
    label: str = "files"


class HiddenRequest(BaseModel):
    """Artifacts of one run folder to hide from the UI's view, or to show again."""

    paths: list[str]
    is_hidden: bool


class PartMessageRequest(BaseModel):
    """What the person writes to a phase 7 part's worker while it works: a question it answers in words, or a message
    as the role chosen."""

    text: str = Field(min_length=1, max_length=4_000)
    role: Literal["user", "system"] = "user"
    is_question: bool = False


class JobMoveRequest(BaseModel):
    """Which way a waiting job moves in the queue: `up` runs it sooner, `down` later."""

    direction: str


class QueuePauseRequest(BaseModel):
    """How many minutes no queued job starts; none for a pause until the queue is resumed."""

    minutes: int | None = None
    # the running run stopped at once, as Ctrl-C stops it, and run again first once the pause ends
    is_stopping_now: bool = False


class JobOnFailureRequest(BaseModel):
    """What the queue does after a job's last failed try: `continue`, `block`, or empty for the `queue.onFailure`
    setting."""

    choice: str


class ConvertRequest(BaseModel):
    """The ingesting profile a finished run is converted to, as a new run folder, or, `is_in_place`, a finished audio
    run's document written again in it inside the same folder, the document it has kept as a version."""

    profile: str
    is_in_place: bool = False


class BranchRequest(BaseModel):
    """The phase a run is branched from: the new run folder keeps the phases before it."""

    phase: int
    # A PDF's processing mode in the branch (`pages`, `markdown`), from phase 2 only; empty keeps the run's own
    pdf_mode: Literal["", "pages-as-images", "text-only", "markdown-conversion"] = ""


class PartRunRequest(BaseModel):
    """Another part of a run's input: a start and an end time of its recording, or a PDF's
    "first-last" pages; all empty is the whole input. A new run folder from phase 1 processes it."""

    range_start: str = Field(default="", max_length=20)
    range_end: str = Field(default="", max_length=20)
    range_pages: str = Field(default="", max_length=20)


class RunDisplayNameRequest(BaseModel):
    """The name a run is to be shown under; empty shows it under its folder's name again."""

    name: str


class RunTagsRequest(BaseModel):
    """Every tag the run folder is to have, in order; an empty list takes them all off."""

    tags: list[str]


class RunRef(BaseModel):
    """One run folder, as the run list names it."""

    root: str
    name: str


class BatchDownloadRequest(BaseModel):
    """The run folders to download from, and the parts of each to put in the zip (`batch_download_service.DOWNLOAD_PARTS`)."""

    runs: list[RunRef]
    parts: list[str]
    # which version of each run's document, when a run has several: the latest (the default), the original, or all
    version: Literal["", "latest", "original", "all"] = ""


class RunRefsRequest(BaseModel):
    """Run folders named by their root and name, for a request about each of them."""

    runs: list[RunRef]


class RunNamesRequest(BaseModel):
    """The runs to show under their AI name (`is_by_topic`), or under their file name again."""

    runs: list[RunRef]
    is_by_topic: bool = True


class InboxTagsRequest(BaseModel):
    """An inbox video, by the path the inbox listing gives, and every tag it is to have before its first run."""

    video: str
    tags: list[str]


class RenameRequest(BaseModel):
    """An inbox video, by the path the inbox listing gives, and the name it should get."""

    video: str
    name: str


class MergeRequest(BaseModel):
    """Two or more inbox recordings that are one meeting, as the inbox lists them,
    and the name of the recording they make; empty takes the beginning their names share."""

    videos: list[str] = Field(min_length=2)
    name: str = Field(default="", max_length=200)


class MarkdownWriteRequest(BaseModel):
    """The whole text a markdown artifact is saved as, from the page's editor."""

    markdown: str


class SettingRequest(BaseModel):
    """One setting changed: its key as the playbook files write it (`budgets.maxAttempts`), its new value, and the
    profile whose file takes it ("" for both default settings files)."""

    key: str
    new_value: Any
    profile: str = ""
    # The key part by part, as the settings list's `path` gives it: a model's name may hold a dot
    key_parts: list[str] | None = None


class SettingRemoveRequest(BaseModel):
    """One entry taken out of a settings file: its key part by part, as the settings list's `path` gives it, and the
    profile whose file loses it ("" for the owner's own file)."""

    key_parts: list[str] = Field(min_length=1)
    profile: str = ""


class InstructionSaveRequest(BaseModel):
    """One instruction file saved from the page: its id (`instructions/write.md`), its new text, why it changed, and
    whether it is an edit or an earlier version saved again (`restore`)."""

    id: str
    text: str
    note: str = ""
    kind: Literal["edit", "restore"] = "edit"


class InstructionResetRequest(BaseModel):
    """One instruction file put back to its default."""

    id: str


class InstructionPreviewRequest(BaseModel):
    """The full system prompt one step's worker in one workflow (`video`, `audio`, `pdf`) would get, with an unsaved
    text in place of one file when given."""

    step: str
    workflow: str = "video"
    id: str = ""
    text: str = ""


class ModelsDownloadRequest(BaseModel):
    """`POST /api/setup/models`: the Hugging Face token, empty to use `HF_TOKEN` from the environment."""

    hf_token: str = ""


class ModelsConfigureRequest(BaseModel):
    """`POST /api/setup/configure`: the model host the wizard's person chose (a spec's provider), the model it serves
    (empty for LM Studio's recommended model), a server's address and an API key, each empty when not asked for."""

    provider: str
    model_id: str = ""
    base_url: str = ""
    api_key: str = ""


class SignInRequest(BaseModel):
    """An account's name and password, to sign in."""

    name: str
    password: str


class AccountRequest(SignInRequest):
    """A new account: its name, its password and its role."""

    role: str


class AccountChangeRequest(BaseModel):
    """An account's new role, its new password, or both; a field left empty stays as it was."""

    role: str = ""
    password: str = ""


class ProjectRequest(BaseModel):
    """A project's title, to make it or to rename it."""

    title: str


class GrantRequest(BaseModel):
    """The role a project gives an account or a group; none takes the grant away."""

    role: str | None = None


class GroupRequest(BaseModel):
    """A new group's name."""

    name: str


class GroupAccountsRequest(BaseModel):
    """The accounts a group holds, exactly."""

    accounts: list[str]


class PhaseFlowRequest(BaseModel):
    """The flow a run goes through, as the phase list takes it: the recording's medium, its workflow and ingesting
    profile, the document pass, and the speed's choice of `"off"` for the review and the critique collection. All
    empty reads the phases with no flow."""

    medium: str = ""
    workflow: str = ""
    ingesting_profile: str = ""
    document_pass: str = ""
    review: str = ""
    critique_collection: str = ""
