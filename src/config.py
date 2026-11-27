# <!-- sync: seed/config v4 -->
"""Settings, and the vocabularies that are contract rather than preference.

Two kinds of value live here and they are NOT interchangeable:

  * `Settings` - budgets, hosts, model choices. Set from `playbook.default.json` (the default settings, in git), from
    `playbook.local.json` (this machine, read on top of it) and from `playbook.<name>.json` when a run line says
    `--profile <name>`, which is read on top of both. A `PB_` environment variable sets only a value no file sets:
    the files reach `Settings` as its arguments, which pydantic-settings ranks above the environment. Change these
    freely.

  * The module-level constants: the playbook's paths, the phases that call a model, the Whisper device and model tables, and the
    ceilings the settings are validated against. The output contract is in `models/output_contract.py`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings, SettingsConfigDict

from models import output_contract
from models import setting_choice_vocabulary
from models import speed_setting_vocabulary

PLAYBOOK_NAME = "info-to-knowledge"
# The command's first name was `v2k`: a setup that still sets `V2K_<NAME>` is read as `I2K_<NAME>` (the new one wins).
for _name in ("WORKSPACE", "PROFILE", "SIGN_IN", "TOKEN", "SERVER_URL", "PIPES"):
    if f"V2K_{_name}" in os.environ:
        os.environ.setdefault(f"I2K_{_name}", os.environ[f"V2K_{_name}"])
# The name the command is INVOKED by, which is not the project's name: `./i2k` wraps `main.py`, and
# argparse would otherwise print whichever of the two paths the caller happened to use.
CLI_NAME = "i2k"
# `src/config.py` -> `src/` -> the playbook root. Two levels, because `src/` is FLAT: there is
# no package directory between this file and the source root.
PLAYBOOK_ROOT = Path(__file__).resolve().parent.parent
# A settings file is `playbook.<name>.json`; `default` and `local` are the two every run reads, a profile is any other
_PROFILE_PREFIX = "playbook."
_PROFILE_SUFFIX = ".json"
DEFAULT_SETTINGS_NAME = "default"
LOCAL_SETTINGS_NAME = "local"
LOCAL_CONFIG_FILE = PLAYBOOK_ROOT / f"{_PROFILE_PREFIX}{LOCAL_SETTINGS_NAME}{_PROFILE_SUFFIX}"
# The default settings, kept in git and in step with this machine's file: read first, the local file on top of it
DEFAULT_CONFIG_FILE = PLAYBOOK_ROOT / f"{_PROFILE_PREFIX}{DEFAULT_SETTINGS_NAME}{_PROFILE_SUFFIX}"
# A named set of settings on top of both, asked for by `--profile NAME`: `playbook.strict.json` is the one in git.
# It is read LAST, so a profile asked for on the command line wins over this machine's own file, which is the whole point
# of asking for it. The name reaches a child process through the environment, so a queued job runs the profile its run
# line named.
PROFILE_ENV_VAR = "I2K_PROFILE"
# The Hugging Face token a gated model's first download needs; never read from a settings file
HF_TOKEN_ENV_VAR = "HF_TOKEN"
# The name of the profile `load_profile` last applied; empty for the default settings. Only `load_profile` rebinds it.
PROFILE_NAME = ""
# The name of the worker set the process runs on: "default" is the settings files as they are, any
# other name is a part of the settings (`workerSets` in the settings files) laid over the files and the profile. Only
# `apply_worker_set` rebinds it.
DEFAULT_WORKER_SET_NAME = "default"
WORKER_SET_NAME = DEFAULT_WORKER_SET_NAME
# What a worker set may not hold: the sets and the choice of the default belong to the settings, never to one set
_WORKER_SET_FORBIDDEN_KEYS = ("workerSets", "worker_sets", "defaultWorkerSet", "default_worker_set",
                              "workerSetMachines", "worker_set_machines")
# The Workflow speed presets the program ships (the owner): Quality, the whole quality workflow; Balanced, the same large
# model with no critique, no repair round, no review and no critique collection ("the best model without a critic and reparation
# loops"); Fast, a small model's quick description of each frame and the document in one call
DEFAULT_SPEED_PRESETS: dict[str, dict[str, bool | int | str]] = {
    "quality": {"video.workflow": "quality", "framesProcessing.framesPerCall": 4, "framesProcessing.isCritiqueOn": True,
                "framesProcessing.isCritiqueRepairOn": True, "framesProcessing.isOpenQuestionsOn": True,
                "framesProcessing.repairCalls": 4, "framesProcessing.singleFrameAttempts": 3,
                "budgets.askWhyAfterRejection": True, "review.isOn": True, "critiqueCollection.isOn": True},
    "balanced": {"video.workflow": "quality", "framesProcessing.framesPerCall": 4, "framesProcessing.isCritiqueOn": False,
                 "framesProcessing.isCritiqueRepairOn": False, "framesProcessing.isOpenQuestionsOn": False,
                 "framesProcessing.repairCalls": 0,
                 "framesProcessing.singleFrameAttempts": 0, "budgets.askWhyAfterRejection": False, "review.isOn": False,
                 "critiqueCollection.isOn": False},
    "fast": {"video.workflow": "fast-processing", "framesProcessing.framesPerCall": 4,
             "framesProcessing.isCritiqueOn": False, "framesProcessing.isCritiqueRepairOn": False,
             "framesProcessing.isOpenQuestionsOn": False, "framesProcessing.repairCalls": 0,
             "framesProcessing.singleFrameAttempts": 0,
             "budgets.askWhyAfterRejection": False, "review.isOn": False, "critiqueCollection.isOn": False},
}
_LOGGER = logging.getLogger(PLAYBOOK_NAME)
# Where `i2k tui`, `i2k mcp` and `i2k run --queue` look for the server: this machine's own address
SERVER_HOST = "127.0.0.1"
# Where `i2k serve` listens by default: every address of this machine, so a phone or another computer on the same
# network opens the page (the owner's choice); sign-in, on by default, guards it
SERVE_LISTEN_HOST = "0.0.0.0"
SERVER_PORT = 8765
# The addresses that name this machine: a server on another one prints a warning, and `i2k tui` starts none there
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Everything the worker is given, sorted by HOW it must reach the worker. See
# prompts/system_prompt_builder.py and agent-context/context_map.json.
AGENT_CONTEXT_DIR = PLAYBOOK_ROOT / "agent-context"
# The drop folder the `inbox` command reads, and the folder it writes one run into per video. Both
# are DEFAULTS for that command only: `run` still refuses to start without being told both paths,
# because a run line typed by hand names a single file and must not guess where it lands.
#
# THEY SIT IN THE PROJECT'S ROOT FOLDER (`inbox/`, `output/`, `ui/`, `models/`), git-ignored. Inbox runs land in
# `output/`, the same folder a hand-typed run line names (the owner's choice: one place for every run). Recordings and
# finished documents are the operator's data: a `git clean` takes none of them (they are ignored) and a fresh clone
# starts with empty folders. `--inbox` and `--output` override both, which is what a different layout uses rather
# than an edit here. `I2K_WORKSPACE` moves the whole workspace (the inbox, the run folders and `ui/` together), so a
# server can be started on test folders, as the end-to-end tests on a real server do.
WORKSPACE_ROOT = Path(os.environ["I2K_WORKSPACE"]).resolve() if os.environ.get("I2K_WORKSPACE") else PLAYBOOK_ROOT
INBOX_DIR = WORKSPACE_ROOT / "inbox"
DOCUMENT_ROOT = WORKSPACE_ROOT / "output"
# The job queue and its consoles, the inbox tags, the recording hash cache and the model download state
UI_DIR = WORKSPACE_ROOT / "ui"
# Read by `services/ignore_list_service.load_ignore_list`. A missing file means no rules, never a
# crash: an unreadable ignore list must not stop a run, and an empty one analyses everything rather
# than silently skipping everything.
DEFAULT_IGNORE_FILE = PLAYBOOK_ROOT / "ignore.json"
CONTEXT_MAPPING_FILE = "context_map.json"

# The phase that reads the frames, whose model may set its own frames a call
FRAMES_PROCESSING_PHASE = output_contract.FRAMES_PROCESSING_PHASE
# Phase 2, which calls a model only for an audio recording, phase 4 the document, phase 5 the review, phase 6 the
# critique collection
FRAMES_PREPARING_PHASE = output_contract.FRAMES_PREPARING_PHASE
FINALIZE_PHASE = output_contract.FINALIZE_PHASE
REVIEW_PHASE = output_contract.REVIEW_PHASE
CRITIQUE_COLLECTION_PHASE = output_contract.CRITIQUE_COLLECTION_PHASE
# `models` holds the configured models, each under a name. Each phase that calls a model uses the one `phaseModels`
# names for it, else its default here: `frame` reads the frames in phase 3, `document` writes in every other phase.
# Phase 3's audio preparation is part of `frames_processing`; phase 2 calls a model only for an audio recording.
PHASE_DEFAULT_MODELS = {FRAMES_PREPARING_PHASE: "document", FRAMES_PROCESSING_PHASE: "frame", FINALIZE_PHASE: "document",
                        REVIEW_PHASE: "document", CRITIQUE_COLLECTION_PHASE: "document",
                        output_contract.DOCUMENT_RESHAPING_PHASE: "document"}
# A `models.default` entry serves every model name that has no entry of its own
DEFAULT_MODEL = "default"

# The one Whisper device that cannot work, and why, stated where the setting is read. faster-whisper
# runs on CTranslate2, which has no Metal backend, so `mps` raises `ValueError: unsupported device
# mps` at the first transcription. `cpu` is the only working value on Apple silicon.
UNSUPPORTED_WHISPER_DEVICES = {
    "mps": "CTranslate2, which faster-whisper runs on, has no Metal backend. Use `cpu`.",
}

# `engine: mlx` takes a Hugging Face repo id, not a size, so each logical size names the repo it
# resolves to. Overridable under `audio.mlx.repositories`, and a size with no row is refused by name. A settings file's
# table replaces this whole table, so it lists every size it means to keep.
MLX_WHISPER_REPOSITORIES = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
    "large": "mlx-community/whisper-large-v3",
    "large-v3": "mlx-community/whisper-large-v3",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


class _OverlaySettings(BaseModel):
    """Base for anything the settings files (`playbook.default.json`, `playbook.local.json`, a profile) can set."""

    # Two settings, both load-bearing:
    #
    #   * `alias_generator=to_camel` + `populate_by_name` - the JSON overlay is camelCase, matching what
    #     the run store writes, while the Python fields stay snake_case. Without it, pydantic matches
    #     FIELD names only, so `{"budgets": {"maxAttempts": 3}}` is silently dropped.
    #
    #   * `extra="forbid"` - an unknown or misspelled key is an error, not silence. This was found the
    #     hard way: the whole camelCase `budgets` block was being ignored, and it looked correct because
    #     the values written happened to equal the defaults. A silently-ignored override is worse than a
    #     crash, because the run proceeds with settings nobody chose.

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# The most frames one phase-3 call may be handed, the owner's ceiling (2026-10-02). Measured: a 960 x 540 frame costs
# about 1,100 prompt tokens; a batch of twenty took at most 34,000 prompt and 10,200 output tokens on a 163,840-token
# context. A batch past roughly 140 frames overflows that context, and the host then silently drops the prompt's start.
MAX_FRAMES_PER_CALL = 300


class ModelSettings(_OverlaySettings):
    """`spec` is `provider:model`. `structured_method` is per-provider, never the library default."""

    spec: str = Field(default="lmstudio:qwen3-vl-8b",
                      json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.HOST_MODELS})
    temperature: float = 0.0
    # The context window the program measures a call against. The server loads the model with its own window: a server
    # that reports one at `/v1/models` is read, and this number stands in when it does not
    num_ctx: int | None = 32768
    # The output limit bounds THINKING PLUS REPLY, and thinking is spent first; `max_tokens` is what the request carries,
    # and this one stands in when `max_tokens` is unset. Measured on this machine: a two-field frame record cost about
    # 930 output tokens on `qwen3-vl:8b`, and the 300 the archived program allowed returned an empty reply on every
    # frame.
    num_predict: int | None = 4096
    # The same output limit as the request carries it; None leaves it to the host
    max_tokens: int | None = None
    # Sampling beyond the temperature, as the model's maker recommends it for the mode and the kind of task (its card
    # names the set). `top_p` is set on the client; `sampling` holds the rest (top_k, min_p, repeat_penalty,
    # presence_penalty, frequency_penalty, seed). Each provider is sent only what its client or host takes, and a key it
    # cannot take is refused by name (`chat_model_service`). Empty: the host decides, as before. Measured on a local
    # model: the host's defaults gave a 47-81% spread over seven runs of one test, the card's set 72-84% over five.
    top_p: float | None = None
    # The rest of the card's sampling set (top_k, min_p, repeat_penalty, presence_penalty, frequency_penalty, seed)
    sampling: dict[str, float | int] = Field(default_factory=dict)
    # Read by nothing since the Ollama client was removed: an OpenAI-compatible host decides for itself whether it
    # separates the model's thinking, and `OpenAICompatibleChatModel` logs whatever it separates into
    # `additional_kwargs["reasoning_content"]`, which attempt_log_service writes into messages.jsonl and transcript.md.
    #
    # True here, on purpose. Measured on `qwen3-vl:8b`: False does not stop the thinking, it only
    # throws the text away, at the same 930 output tokens. A reply that holds nothing BUT thinking is
    # the defect this project was rebuilt over, and it can only be read back if the text was kept.
    reasoning: bool | None = True
    # Per HTTP request. Without it a hung host blocks the worker for as long as the process lives,
    # with no error and no log of why. A local model on a long transcript legitimately takes
    # minutes; `None` restores the unbounded wait.
    request_timeout_s: int | None = 900
    # The host's address; empty: the provider's own (`lmstudioBaseUrl`, `ollamaBaseUrl`). The connection a profile
    # switches, as a worker set points the large model at another machine
    base_url: str | None = Field(default=None,
                                 json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.BASE_URLS})
    # For a local host that insists on a key; a real key goes in the environment (OPENAI_API_KEY and the like), never in a settings file
    api_key: str | None = None
    # Measured on a local model: `json_schema` returns prose for a flat schema and the bare inner array
    # for a list-wrapper schema. `function_calling` is the local default for a reason.
    structured_method: Literal["function_calling", "json_schema", "json_mode"] | None = None
    # Phase 3's frames a call while this model runs it, over `framesProcessing.framesPerCall`; None: that setting's.
    # A small model keeps fewer frames apart in one call: qwen3.5-4b mixed the frames of a call of 8 (the owner set 5).
    frames_per_call: int | None = Field(default=None, ge=1, le=MAX_FRAMES_PER_CALL)

    def resolve_max_output_tokens(self) -> int:
        """The most tokens one reply may take, by whichever name its host reads (`max_tokens`, Ollama's `num_predict`);
        0 when no limit is set."""

        return self.max_tokens or self.num_predict or 0


class BudgetSettings(_OverlaySettings):
    """How many attempts, calls and reads each step may spend, and the frame tools' limits."""

    # Attempts a step of phases 3 to 6 may make before a rejection blocks it. The default settings set 3 (the owner: "make them
    # not strict"); `playbook.strict.json` sets 1, where a rejected step blocks and its cause is fixed first
    max_attempts: int = Field(default=1, gt=0)
    # Before a step blocks on its last rejected attempt, the worker gets one turn offering only `report_problem`, to say
    # whether the instruction or its own reply was at fault (the owner, 2026-09-30: "if the input is rejected, the agent
    # have no chance to give feedback because the program is stopped"). On while the playbook is being developed.
    ask_why_after_rejection: bool = True
    # Chapter dry runs: `check_chapter` offered beside `submit_chapter`, and a chapter attempt with no check before it
    # checked first and judged only when it comes again. Both give the writer a second chance at its one attempt.
    chapter_dry_runs: bool = True
    # The WHOLE run's graph steps: one per frame plus one per phase, so it grows with the recording. It
    # is a guard against a route that never advances, and nothing else: handed an attempt's limit of 80,
    # every recording cut into more than about 75 frames stopped before its first model call.
    run_steps: int = Field(default=20000, gt=0)
    # Read by nothing: no attempt loop enforces them, and `attemptTimeoutS` is only copied into the run record.
    # Kept until the owner removes them or builds what they name.
    attempt_timeout_s: int = 1800
    model_calls: int = 40
    tool_calls: int = 120
    recursion_limit: int = 80
    repeat_limit: int = 6
    restore_on_reject: bool = False
    max_read_chars: int = 24000
    max_grep_matches: int = 80
    # REQUIREMENTS.md R12: the tool calls one follow-up of phase 4's conversation may make, reads and submits
    # together, and the reads the whole phase may make. Finding one chapter's draft headings reads every chunk of that
    # chapter in the notes file, up to 10 chunks of `document.readChars` on an 87-minute meeting, so it has its own
    # limit, and the phase's reads make room for the 30 chunks of the whole meeting.
    write_calls: int = Field(default=12, gt=0)
    # The tool calls of one chapter's draft headings follow-up, which reads every chunk of that chapter
    write_headings_calls: int = Field(default=30, gt=0)
    # The reads the whole of phase 4 may make
    write_reads: int = Field(default=120, ge=0)
    # A whole chapter is one follow-up: its reads, its looks at frames and cuts for images, and its submit.
    write_chapter_calls: int = Field(default=40, gt=0)
    # The attempts a chapter's text may make, as `maxAttempts` does for the other steps: 3 in the default settings, 1
    # under `--profile strict`
    write_chapter_attempts: int = Field(default=1, gt=0)
    # The frame tools' limits, one frame at a time over every chapter and resume (the owner, 2026-10-02: in the config,
    # not the code). Cuts in use; undos ("make it 20"), the one way a wrong cut is set aside and the one cap on how
    # often a frame is cut again (sixteen cuts of one frame once, 2026-09-29); and the most grid lines `look` draws.
    cuts_per_frame: int = Field(default=3, gt=0)
    # How often a frame's cut may be set aside with `undo_cut`, over every chapter and resume
    undos_per_frame: int = Field(default=20, ge=0)
    # The most grid lines `look` draws across each side of a frame or a cut
    grid_lines_max: int = Field(default=40, gt=0)
    # Any refusal of a frame tool (a cut past `cutsPerFrame`, a cut of an empty area, an undo past the limit, a mask outside
    # the cut) blocks the chapter and stops the run, so a tool's defect or a writer's misuse is found and fixed before
    # production (the owner: "when agent reaches the cutsPerFrame 3 or when it fail in general, the run should be
    # stopped"). Off, the writer reads the refusal and carries on.
    fail_on_frame_cut_fail: bool = False


class FrameSettings(_OverlaySettings):
    """Which frames phase 2 cuts. `interval` samples by the clock; `scene` cuts one frame per scene; `change` takes a
    frame when the shared screen changes."""

    # REQUIREMENTS.md R1 names the JSON key `mode`. The field says which mode it is.
    selection_mode: Literal["interval", "scene", "change"] = Field(default="interval", alias="mode")
    # `interval` only: how many frames per minute of video.
    per_minute: float = Field(default=5.0, gt=0)
    # `scene` only. The mean absolute grayscale difference between consecutive frames, on a 0..1
    # scale, above which a new scene starts.
    scene_threshold: float = Field(default=0.3, gt=0, le=1)
    # `scene` only: a scene shorter than this is merged into its neighbour.
    min_scene_seconds: float = Field(default=1.0, ge=0)
    # Every mode: never more frames than this. `scene` drops its shortest scenes first.
    max_count: int = Field(default=60, gt=0)
    # The quality each frame is written at, once, after the cut; frames are held at 98 until then
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    # `change` only. Once a second the frame is cropped to the shared screen and compared with the last frame taken,
    # in 32 x 18 blocks. A frame is taken when at least this share of the blocks changed AND the screen then held
    # still for `still_seconds`, or kept moving for `busy_seconds`. Measured in research/frame-change/ on four
    # recordings: 5% took the fewest frames before content went missing, 10% missed twice as much on code.
    change_to_capture: float = Field(default=0.05, gt=0, le=1)
    # `change` only: how long the screen holds still after a change before the frame is taken
    still_seconds: int = Field(default=1, ge=0)
    # `change` only: how long the screen may keep moving before the frame is taken anyway
    busy_seconds: int = Field(default=10, gt=0)
    # `change` only: the shared screen's box is looked for again every this many seconds, and a different box is
    # taken only when two looks in a row agree.
    box_every_seconds: int = Field(default=5, gt=0)
    # `change` only: a frame that repeats an earlier screen and stays on screen less than this many seconds is a
    # pass-through (slides flicked past, windows switched) and is not taken; its speech stays with the frame before it.
    # A repeat shown longer is a return and is read again. 0 keeps every repeat.
    pass_through_seconds: float = Field(default=5.0, ge=0)
    # `change` only: a frame that follows the frame before it and changed from it by at most this share of blocks (a
    # slide gaining a box, an arrow, a picture) is a step of one build: kept in one batch and told as one slide built in
    # steps. Measured: build steps changed 5.6% to 13.2% (a remark box added), so 15% keeps a margin above the largest.
    # 0 marks none.
    build_step_change: float = Field(default=0.15, ge=0, le=1)
    # `change` only: the face detector's model, relative to the playbook folder unless absolute.
    face_model: str = "weights/face_detection_yunet_2023mar.onnx"
    # `change` only: the person model (NanoDet-Plus), relative to the playbook folder unless absolute. The capture loop
    # leaves the blocks a person covers out of its change, so a lecturer walking before an unchanged board takes no frame.
    person_model: str = "weights/object_detection_nanodet_2022nov.onnx"
    # `change` only: cut the bars inside the shared screen (a menu bar, a remote desktop's title bar, a taskbar):
    # the rows and columns at its edges that stay the same while the content changes, found by comparing each taken
    # frame with up to ten taken either side of it in the same screen box. Measured in research/crop-methods/.
    trim_bars: bool = True
    # Every mode: a written frame wider than this is scaled down to it; 0 keeps the recorded size. 960 was read
    # almost perfectly on a screenshare; 1280 keeps small code text readable.
    max_width: int = Field(default=1280, ge=0)
    # REQUIREMENTS.md R12: the subject tags earlier frames used, shown to each frame call so it reuses them, at most
    # this many characters (about 1,500 tokens), most used first.
    tags_chars: int = Field(default=5_000, ge=0)
    # Every mode, after every rule above: the frame classifier tags each frame taken people, text or other (one model,
    # `--task tags`), and a frame tagged people (no knowledge: only people, a gallery, a blank or unreadable screen) is
    # dropped before phase 3. Its image moves into `frames/dropped/`, where the detail view shows it with its drop score
    # (the people score); its speech goes to the frame before it. The classifier is trained in ~/projects/frame-knowledge-classifier/.
    is_knowledge_filter_on: bool = True
    # The classifier's command, relative to the playbook folder unless absolute: the copy of its inference code kept in
    # this repository (frame-classifier/, run in the program's own environment), so a clone runs alone.
    knowledge_classifier_command: str = "frame-classifier/classifier"
    # The trained weights the classifier runs with, relative to the playbook folder unless absolute: the copy kept in
    # this repository (AGENTS.md, "The frame classifier's weights live in this repository"), handed to the classifier
    # as FRAME_CLASSIFIER_WEIGHTS_DIR. "" reads the classifier project's own classifiers/ folder.
    knowledge_classifier_weights: str = "weights/frame-classifier"
    # Which model decides that a frame holds no knowledge, named by its publisher and full name: "google-SigLIP-2", the
    # frame classifier above (trained here on Google's SigLIP 2), or "liquidai-d1-2B", Liquid AI's decision model asked
    # whether the frame holds knowledge and which kind of empty frame it is (people, desktop, blank, joining), with no
    # training (measured offline: 57 of 73 empty frames dropped at 0.20, as many as the classifier, none with
    # knowledge). The classifier still runs for its text and board tags. A run's `--knowledge-filter-model` wins.
    knowledge_filter_model: Literal["google-SigLIP-2", "liquidai-d1-2B"] = "google-SigLIP-2"
    # The d1-2B tool's Python, relative to the playbook folder unless absolute (tools/d1-knowledge-filter/, its own
    # environment built by its setup.sh); the tool's script stands beside the environment's folder.
    decision_filter_python: str = "tools/d1-knowledge-filter/.venv/bin/python"
    # A frame d1-2B gives a knowledge probability under this is dropped: 0.20 lost no knowledge frame of three labelled
    # runs, whose lowest knowledge frame scored 0.246 (lecture 9)
    decision_filter_cut: float = Field(default=0.20, ge=0.0, le=1.0)
    # The same tags mark each kept frame tagged text `isTextOnly` (only printed text an OCR carries whole, with things
    # of no value: a plain line, black edges, small emojis, a logo, a camera tile), with `textOnlyScore`; phase 3 reads
    # the mark (`framesProcessing.isTextOnlyFrameSentAsText`). The classifier runs once for both switches.
    is_text_only_mark_on: bool = True
    # Video only, after the knowledge filter: the frames the same tags call `board` (a task board or a ticket view) are
    # compared with the board frame kept before them, with no model call: one under 1% changed is dropped, one under
    # 40% changed is a step of that board with `boardChange`, the box of what changed, which phase 3 sends cut out.
    is_board_stage_on: bool = True
    # How phase 3 is shown a board step. "image-diff-only": the box of what changed, cut out and enlarged in place of the
    # frame. "text-diff-integration": the whole frame, with the changes the program reads between the text of the step
    # and of the board frame before it (added, removed, changed, moved, a scroll), each under the unchanged line above
    # it, written by phase 2 as `boardTextDiff`.
    # "text-only-integration": every board frame goes as its text alone, read whole by `boardTextReader` (`boardWords`),
    # with no image, a step with its text diff too; the owner's request, kept off the default until a run shows the
    # text loses nothing (measured offline: no reader keeps a ticket panel's small tables).
    board_handler: Literal["image-diff-only", "text-diff-integration", "text-only-integration"] = "text-diff-integration"
    # The text reader the board stage reads a board's words with: "auto" is Apple's recognizer on a Mac that can build
    # its tool (tools/apple-text-reader/: fewer misread words than Tesseract, about 1 s a frame) and
    # Tesseract everywhere else; "apple" or "tesseract" names one, "apple" falling back to Tesseract where it cannot
    # run. The other checks of phase 2 keep Tesseract: their cuts were measured on it.
    board_text_reader: Literal["auto", "apple", "tesseract"] = "auto"
    # The languages the text reader (Tesseract) may read in, by Tesseract's names ("eng", "deu", "fra"): English, and
    # the recording's own language (phase 1's) when it is listed here. One with no model on the machine is left out
    # with a warning, and the setup check names it.
    text_reader_languages: list[str] = Field(default_factory=lambda: ["eng", "deu"])
    # Video only, after the knowledge filter: a small vision model is asked, a batch of consecutive frames at a time,
    # which frames another frame already holds whole (the same screen again, an earlier step, a scroll position shown
    # whole elsewhere, a page with only a menu or a dialog over it), and those leave the run as the knowledge filter's
    # do, `droppedBy: "redundancy"`. Off in code so no test calls a model by accident; the default settings turn it on.
    # Measured offline: it also drops frames whose knowledge no kept frame holds.
    is_redundancy_filter_on: bool = False
    # The name of the model it calls, one of `models`: any configured vision model may be named.
    redundancy_model: str = Field(default="lfm-vl-3b",
                                  json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.MODEL_NAMES})
    # Consecutive frames per call. The POC's best for LFM2.5-VL-3B was 10, at about 2 s a frame; the default settings run 4.
    redundancy_frames_per_call: int = Field(default=7, ge=2, le=40)
    # Each frame is sent scaled to this width: 640 is what the POC measured (about 230 image tokens a frame for LFM).
    redundancy_frame_width: int = Field(default=640, ge=320)
    # A removal by the redundancy filter or the similarity recheck stands only when its holding frames show all but at
    # most this many of the words the text reader finds on the removed frame (bare words of two letters or digits
    # or more); None turns the check off, and then a removal must give a reason. On Lecture 9 the model's removals that lost knowledge each missed 4 or more
    # words, and a right one 2 (both misreads).
    redundancy_max_missing_words: int | None = Field(default=2, ge=0)
    # Under that check, a removal stands only when the removed frame shows at least this many such words: a photo, a
    # room shot or a title card shows too few for its holder to be checked against. On four recordings the wrong
    # removals that passed the check showed 0 to 2 words, the right ones 12 or more but for one empty page (0).
    redundancy_min_words: int = Field(default=5, ge=0)
    # Video only, after the redundancy filter, with its own switch: the kept frames that look alike (the frame
    # classifier's SigLIP vectors, the whole frame and its four quarters, mean cosine at or above
    # `similarity_threshold`) and that the redundancy filter never showed together (with that filter off, all of them)
    # make lists, each asked of `similarity_model` (the redundancy model when empty), `similarity_frames_per_call`
    # frames a call, with the same instruction and checks; a frame it removes leaves as `droppedBy: "similarity"`.
    # Needs the knowledge filter's classifier, which computes the vectors. Off in code so no test calls a model; the
    # default settings turn it on. Measured and tuned offline.
    is_similarity_recheck_on: bool = False
    # The mean cosine of two frames' vectors at which they are compared by the model; the default settings use 0.97
    similarity_threshold: float = Field(default=0.97, ge=0.5, le=1.0)
    # Consecutive frames per similarity call; the default settings run 4
    similarity_frames_per_call: int = Field(default=10, ge=2, le=40)
    # The name of the model the similarity recheck calls, one of `models`; empty uses `redundancy_model`
    similarity_model: str = Field(default="",
                                  json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.MODEL_NAMES})


class FasterWhisperSettings(_OverlaySettings):
    """Only `engine: faster-whisper` reads these. CTranslate2 runs on the CPU cores."""

    device: str = "cpu"
    compute_type: str = "float32"
    beam_size: int = Field(default=5, gt=0)
    # The VAD filter skips silence before decoding it, which is most of a long meeting.
    vad_min_silence_ms: int = Field(default=500, ge=0)

    @field_validator("device")
    @classmethod
    def refuse_unsupported_device(cls,
                                  device: str) -> str:
        if device in UNSUPPORTED_WHISPER_DEVICES:
            raise ValueError(f"device {device!r} cannot work: {UNSUPPORTED_WHISPER_DEVICES[device]}")

        return device


class MlxWhisperSettings(_OverlaySettings):
    """Only `engine: mlx` reads these. MLX runs the model on the GPU through Metal, and has NO VAD
    filter, so silence is decoded rather than skipped. `no_speech_threshold` is what discards it
    afterwards, which is why it is settable here."""

    # The Hugging Face repo each logical model size resolves to. A size with no row here is refused
    # by name rather than passed through, because MLX takes a repo id and not a size.
    repositories: dict[str, str] = Field(default_factory=lambda: dict(MLX_WHISPER_REPOSITORIES))
    # How much audio goes to the GPU per call. macOS kills a Metal command buffer that runs long
    # enough to block the interface, and measured here an 87-minute file in one call died with
    # `kIOGPUCommandBufferCallbackErrorImpactingInteractivity`. 0 means one call, for a short file.
    chunk_seconds: float = Field(default=300.0, ge=0)
    no_speech_threshold: float = Field(default=0.6, ge=0, le=1)
    # False stops one bad segment's text being fed to the next, at some cost in consistency.
    condition_on_previous_text: bool = True


class DiarizationSettings(_OverlaySettings):
    """Who spoke when: phase 1 runs pyannote over the meeting's audio and puts a speaker on every word. Off, or failing,
    the transcript is written without speakers, as before. The Hugging Face token the model's first download needs is
    read from the `HF_TOKEN` environment variable, never from a settings file."""

    is_on: bool = False
    # A Hugging Face repo id of a pyannote pipeline. Community-1 is gated: its terms are accepted once on its page.
    model: str = "pyannote/speaker-diarization-community-1"
    # `auto` takes CUDA, then Apple's MPS, then the CPU, whichever this machine has.
    device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    # None lets the model find the number of speakers; a number bounds it.
    min_speakers: int | None = Field(default=None, gt=0)
    # The most speakers the model may find; None lets it find the number
    max_speakers: int | None = Field(default=None, gt=0)
    # A word is given no speaker when a second speaker's turn covers at least this share of it: overlapping speech is
    # left unassigned rather than given to whoever spoke the most of it.
    overlap_share: float = Field(default=0.5, gt=0, le=1)


class TakeawaySettings(_OverlaySettings):
    """The `takeaway` ingesting profile's audio processing: one call over the transcript
    right after phase 1, and what else it skips to be fast. Kept under `audio`, never at the top level, where a section
    reads like a settings profile."""

    # Whether phase 1 tells the speakers apart for a takeaway run. Off: a fact needs what was agreed, not who said it,
    # and diarization is a pass over the whole recording.
    is_diarization_on: bool = False
    # Whether phase 4's one call thinks before it writes. Off: thinking is most of a call's time; on, if a run shows
    # facts written wrong.
    is_thinking_on: bool = False


class FastProcessingSettings(_OverlaySettings):
    """The fast-processing video workflow: phase 3 describes each frame from its image
    alone, with no check, and phase 4 writes the document in one call from those descriptions."""

    # The configured model that describes the frames: a small vision model is enough for one frame at a time
    frame_model: str = Field(default="qwen3.5-4b",
                             json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.MODEL_NAMES})
    # How many frames one session of the frame model describes, each a follow-up of the one before; then the session
    # starts fresh, so its context never grows past this many frames
    frame_follow_ups: int = Field(default=5, ge=1, le=MAX_FRAMES_PER_CALL)
    # The width each frame is sent at; 1280 is what the small models were measured at
    frame_width: int = Field(default=1280, ge=320)
    # The POC measured the describing without thinking: off, each frame is a short call
    is_frame_thinking_on: bool = False
    # The most one frame's reply may write: the longest description measured was about 530 words (under 1,000
    # tokens); without it a reply that looped ran to the model's own 40,960 for 20 minutes on one frame
    frame_max_tokens: int = Field(default=4096, ge=256)
    # The configured model that writes the document from every description, in one call
    document_model: str = Field(default="document",
                                json_schema_extra={setting_choice_vocabulary.CHOICES_KEY: setting_choice_vocabulary.MODEL_NAMES})
    # Whether the document model thinks before it writes
    is_document_thinking_on: bool = True


class VideoSettings(_OverlaySettings):
    """The video workflow."""

    # The workflow a video run takes when its run line names none: `quality` (checked batches, the document
    # conversation, the review) or `fast-processing`
    workflow: Literal["quality", "fast-processing"] = "quality"
    fast_processing: FastProcessingSettings = Field(default_factory=FastProcessingSettings)


class AudioSettings(_OverlaySettings):
    """How phase 1 transcribes. The values are the ones that gave a correct German transcript."""

    # `engine` chooses the implementation, and each engine's own settings live under its own key, so
    # selecting one does not make the other engine's keys illegal under `extra="forbid"`.
    engine: Literal["faster-whisper", "mlx"] = "faster-whisper"
    # The logical size, not a path: each engine resolves it its own way.
    whisper_model: str = "large"
    # None detects the language. A forced language is only for audio the detector gets wrong.
    language: str | None = None
    faster_whisper: FasterWhisperSettings = Field(default_factory=FasterWhisperSettings)
    mlx: MlxWhisperSettings = Field(default_factory=MlxWhisperSettings)
    diarization: DiarizationSettings = Field(default_factory=DiarizationSettings)
    # The takeaway ingesting profile's audio processing
    takeaway: TakeawaySettings = Field(default_factory=TakeawaySettings)


class PromptSettings(_OverlaySettings):
    """What the operator adds to every frame's message, and how much of the past it carries."""

    # What the operator wants to know. Rendered where the frame instruction says `{prompt}`.
    user_prompt: str = ""
    # None sends everything spoken before the frame. A number sends only the last N seconds of it,
    # cut at a segment boundary, never mid-sentence.
    transcript_before_seconds: float | None = Field(default=None, gt=0)
    # None sends the notes of every earlier frame. A number sends only the newest N, whole.
    previous_frames_limit: int | None = Field(default=None, gt=0)


# The most repair calls one batch may spend on the frames it did not finish, each one handed what the round before it
# got right and why each frame still failed. A ceiling, because every round costs a call on a batch that already
# failed once, and a frame no round finishes is asked alone or blocked anyway.
MAX_REPAIR_CALLS = 5


class FramesProcessingSettings(_OverlaySettings):
    """How phase 3 hands frames to the model."""

    # Consecutive frames per call. 1 is one frame, one call, one record. More puts each frame's words and frame in
    # its own message of one call, and the reply holds one record per frame; the `frame` role's context and output
    # limit (`numCtx`/`numPredict` on Ollama, `maxTokens` otherwise) must then hold all their frames and all their
    # records, thinking included.
    frames_per_call: int = Field(default=1, ge=1, le=MAX_FRAMES_PER_CALL)
    # Whether each batch criticises its own accepted notes and reports each mistake as a rule, one call per batch,
    # into `<batch>-critique.md`. It changes no frame: the artifact is what the monitor validates and what phase 6
    # collects. Off, no critique call is made and no critique file is written.
    is_critique_on: bool = True
    # Whether the call after the critique repairs the notes from the rules it reported, one call per batch. Off, the
    # critique stands as a record and the notes stay as the first call wrote them. With `isCritiqueOn` off there is
    # nothing to repair from, and the repair is skipped with a console line rather than refused.
    is_critique_repair_on: bool = True
    # Whether a PDF's batches are criticised too, with `isCritiqueOn` on. Off, a PDF's batches have no critique and no
    # repair: on the first two PDF runs every critique looped (3 of 3), 5 of its 6 rules were false on the notes, and
    # the critique and its repair took 30 to 40 minutes a batch against 7 to 29 for the reading itself.
    is_pdf_critique_on: bool = False
    # A batch of a PDF's figures alone (frames phase 2 cut from its pages, `pdf-figure`) is criticised and repaired
    # whatever `isPdfCritiqueOn` says: a figure is a picture read like a video's frame, and with no check its first
    # reading was final (the NACA scan's chart, a curve said to level off where it falls, reached the document;
    # DEFECTS 489). A page batch stays as `isPdfCritiqueOn` sets it.
    is_pdf_figure_critique_on: bool = True
    # Whether each batch is asked what a reader still cannot know, then the answers from its notes, words and frames:
    # two calls a batch. Off, no batch has either, and the notes keep the open questions each frame wrote; the Balanced
    # and Fast speeds turn it off, since the owner counts the two among the critic's loops.
    is_open_questions_on: bool = True
    # Whether a PDF's batches are asked their open questions and the answers. Off, a PDF figure batch has neither: over
    # four batches of two PDFs they took 554 to 930 s each and found nothing the document's text, which phase 4 reads,
    # does not answer.
    is_pdf_open_questions_on: bool = False
    # The frames a batch call did not finish go back TOGETHER in this many repair calls, one after another: each round
    # is handed what the round before it got right and why each frame still failed, and the frames one round finishes
    # leave. 1 sends them once more in one call; 0 sends none, and each failed frame goes straight to its single-frame
    # calls.
    repair_calls: int = Field(default=1, ge=0, le=MAX_REPAIR_CALLS)
    # How many times one frame its batch did not finish is sent by itself, after the repair (or at once when there is
    # none), before it is blocked and phase 3 stops. 0 sends none: the frame is blocked at once (the owner, 2026-10-02).
    single_frame_attempts: int = Field(default=1, ge=0)
    # A frame narrower than this many pixels is enlarged to this width before it is sent, its height in proportion; 0
    # sends every frame at its own size. A 640x360 frame reached the model as about 310 tokens of image, under a third
    # of a 960x540 frame's, and the model named a program its title bar did not show; enlarged 2x it did not
    # (measured offline). The frame on disk is never changed.
    min_sent_frame_width: int = Field(default=1280, ge=0, le=4096)
    # A frame phase 2 marked `isTextOnly` is sent as the lines the program reads on it, with no image: the model writes
    # its notes from that text and its words, and the call carries no image tokens for it. Off: every frame is sent as
    # its image, marked or not.
    is_text_only_frame_sent_as_text: bool = True


class CritiqueCollectionSettings(_OverlaySettings):
    """Phase 6, the critique collection: every batch's critique of this run, collected into one rule set."""

    # Whether phase 6 runs at all. Off, it is held and the per-batch critique files stay where they are.
    is_on: bool = True


class DocumentReshapingSettings(_OverlaySettings):
    """Phase 7, document reshaping: one worker cleans up, rewrites or reshapes into knowledge the published document,
    a part at a time, when the run asked for it."""

    # The least a part holds, in estimated tokens of the document's text: the owner's 30,000 (was "at least fifty k",
    # lowered when a part's calls took 10 to 25 minutes each). A part ends at the first heading past it, so a section is
    # never split between two parts. Read again before each part: a new value cuts the parts not started yet.
    batch_tokens: int = Field(default=30_000, ge=1_000)
    # The model calls one part may make (reads and edits together) each time it starts, before it is blocked.
    batch_calls: int = Field(default=40, ge=2)
    # A part's conversation size limit, in estimated tokens: past it the worker writes a summary and the conversation
    # goes on from it. Phase 7's own, apart from the video document's `document.conversationSizeLimitTokens`.
    conversation_size_limit_tokens: int = Field(default=120_000, ge=10_000)
    # The two shares of that limit, in percent, at which the worker is told how full its conversation is and that it
    # may prune the results it no longer needs (the owner's 70 and 85).
    first_warning_percent: int = Field(default=70, ge=1, le=99)
    second_warning_percent: int = Field(default=85, ge=1, le=99)
    # Characters a token is estimated at, for the size the worker is told and the batches are cut at.
    chars_per_token: float = Field(default=3.5, gt=0)


class PdfSettings(_OverlaySettings):
    """How a PDF is read and processed: its mode, its scanned pages, its figures and the LLM passes over its document
    """

    # How a PDF is read when its run chooses none: "by-size" (the owner's: "if the size small choose something fancy as
    # default", "if too big, the default select is markdown conversion"): a PDF of at most `images_page_limit` pages is
    # read as pages as images, one of more than `model_writing_page_limit` pages takes the markdown conversion, any
    # other text only; or one mode for every PDF: "text-only" (its text alone, no picture), "pages-as-images" (each page
    # shown to the model as a picture, read in batches) or "markdown-conversion" (no model writes the document). Taken
    # only by a run that has not read its PDF yet, so a run made before keeps its pages. A run's `--pdf-mode` wins.
    default_mode: Literal["by-size", "pages-as-images", "text-only", "markdown-conversion"] = "by-size"
    # by-size: the most pages a PDF read as pages as images by default has. Not measured: each page is a frame of phase
    # 3, about four a batch, so 20 pages are five batches
    images_page_limit: int = Field(default=20, gt=0)
    # by-size: the most pages a PDF a model writes by default has; a larger one takes the markdown conversion. A wiki
    # export's 1,493 pages held 582,303 tokens, about 390 a page, so 300 pages are about 117,000 tokens, under the
    # 134,000 at which this machine's writing conversation is compacted
    model_writing_page_limit: int = Field(default=300, gt=0)
    # What reads a page whose words are pixels (a scan: a picture fills the page): "tesseract" (inside pymupdf4llm, about 1.7 s a page) or "unlimited-ocr" (Baidu's Unlimited-OCR-2 on MLX, Apple
    # silicon only, about 8 to 20 s a page; measured offline: 99% of a scan's words kept where Tesseract kept 26% to
    # 97%, and its picture boxes are the scan's figures). Not installed, scans are read by Tesseract and the log says
    # so. A run's `--pdf-ocr-engine` wins over it.
    ocr_engine: Literal["tesseract", "unlimited-ocr"] = "tesseract"
    # Unlimited-OCR-2's Python, relative to the playbook folder unless absolute: its own environment, built by
    # tools/unlimited-ocr/setup.sh, which runs tools/unlimited-ocr/read_pages.py
    unlimited_ocr_python: str = "tools/unlimited-ocr/.venv/bin/python"
    # The model's MLX build on Hugging Face, downloaded into its cache by tools/unlimited-ocr/setup.sh --download
    unlimited_ocr_model: str = "mlx-community/Unlimited-OCR-2-8bit"
    # Which figures of a PDF read as text only phase 3 reads; every other one is placed in the published document where
    # it stood, unread, by the program: "read-all"; "place-above-limit" (the owner's
    # default: a PDF of more than `figure_read_limit` figures has every figure placed unread, one of fewer every figure
    # read; a wiki export's 532 figures were about 17 hours of phase 3); "read-drawings-only" (a figure drawn as lines
    # and shapes, a chart or a diagram, is read, a picture, a screenshot or a photo, placed unread). Phase 2 reads it.
    figure_reading: Literal["read-all", "place-above-limit", "read-drawings-only"] = "place-above-limit"
    # the owner's limit: "max 35"
    figure_read_limit: int = Field(default=35, gt=0)
    # The extra wishes phase 7's LLM clean up of a PDF's document is given ("remove every Jira link"): the run form and
    # a done run's Clean up or rewrite load them into the box when clean up is chosen, and a run queued with other
    # wishes saves them here (the owner: "last added wishes should be persisted for a specific mode")
    cleanup_wishes: str = Field(default="", max_length=4_000)
    # The extra wishes phase 7's LLM rewrite of a PDF's document is given, kept apart from the clean up's: loaded into
    # the box when rewrite is chosen, and saved here when a run is queued with other wishes
    rewrite_wishes: str = Field(default="", max_length=4_000)
    # The extra wishes phase 7's LLM knowledge pass is given, kept apart from the other two passes' in the same way
    knowledge_wishes: str = Field(default="", max_length=4_000)


class DocumentSettings(_OverlaySettings):
    """How phase 4 cuts a long meeting into calls a local model's context holds."""

    # The entries one outline call sorts into topics: consecutive, never splitting one moment. 60 entries of about
    # 300 characters is some 5,000 prompt tokens, and the call must still name every id back; an 87-minute meeting
    # gives about 1,900 entries to write from, so some 30 outline calls.
    chunk_entries: int = Field(default=60, gt=0)
    # The most characters of entries one outline call is handed
    chunk_characters: int = Field(default=18_000, gt=0)
    # REQUIREMENTS.md R9. The parts call sees each moment of the meeting with what was said during it, cut to this
    # many characters: 321 moments of an 87-minute meeting at about 450 characters each is some 35,000 tokens.
    moment_speech_chars: int = Field(default=200, ge=0)
    # A later topic of at most this many entries whose title an earlier part already used is a short return to that
    # subject, and is written where the subject was first discussed. 0 folds nothing.
    fold_entries: int = Field(default=5, ge=0)
    # A topic's entries within one step are written by consecutive section calls of at most this many entries each,
    # under the one heading: the table of contents names subjects a reader looks up, and never counts entries.
    section_entries: int = Field(default=12, gt=0)
    # A chapter's text is asked in parts of whole headings of at most this many notes: one attempt covered about 60
    # notes well, and left 30 to 90 of 239 uncovered on five tries
    chapter_entries: int = Field(default=70, gt=0)
    # A heading past this many notes is written in even parts under its one heading, each its own request: one attempt
    # covered a 78-note heading and failed a 103-note one with six frames to place
    heading_entries: int = Field(default=80, gt=0)
    # REQUIREMENTS.md R11. The executive summary is at most a tenth of the document and never more than one page,
    # and never held below `summary_min_words`, so a short document can still say what it concluded.
    summary_max_words: int = Field(default=500, gt=0)
    # The fewest words the executive summary is held to, however short the document
    summary_min_words: int = Field(default=50, gt=0)
    # REQUIREMENTS.md R10: a cut whose shorter side is below this many of the recording's pixels comes back with a
    # warning; the agent decides whether it matters.
    min_cut_pixels: int = Field(default=120, gt=0)
    # REQUIREMENTS.md R12. "conversation" writes the document in one conversation of follow-ups (the owner's order:
    # apply every requirement); "calls" asks each part in a call of its own, and is kept for R12's acceptance baseline.
    write_mode: Literal["calls", "conversation"] = "conversation"
    # Past this many prompt tokens the conversation is compacted; never above the window less the reply's output limit.
    conversation_size_limit_tokens: int = Field(default=120_000, gt=0)
    # The window a conversation keeps within when the host does not say which window it loaded
    # (`host_window_service.resolve_conversation_window` asks LM Studio first).
    conversation_window_tokens: int = Field(default=163_840, gt=0)
    # The most characters one chunk of a read returns
    read_chars: int = Field(default=12_000, gt=0)
    # The most characters the agent's notes hold together
    notes_chars: int = Field(default=4_000, gt=0)
    # The token estimate between two counts the host reports. Not measured yet on this model: R12's probe measures
    # them.
    chars_per_token: float = Field(default=3.5, gt=0)
    # The tokens one frame sent to the model is estimated at, between two counts the host reports
    frame_tokens: int = Field(default=1_000, ge=0)


class ReviewSettings(_OverlaySettings):
    """How phase 5 reviews the finished document, REQUIREMENTS.md R16. Its conversation keeps within the same window
    and size limit as phase 4's (`document.*`), on the model `phaseModels` names for `review`."""

    # Whether phase 5 runs at all. Off, it is recorded "off", the published document stays as phase 4 wrote it,
    # and the run is finished at phase 4 (the owner, 2026-10-08: "i want flag to enable disable phase 5").
    is_on: bool = True

    # The most todos the critique may list. Twenty real gaps beat forty trivial ones; an empty list is a valid reply.
    max_todos: int = Field(default=20, gt=0)
    # The reads one todo's follow-up may make, so one todo cannot spend the session.
    reads: int = Field(default=30, ge=0)
    # The tool calls one follow-up may make, reads and submits together: the reads, plus room for notes and retries.
    calls: int = Field(default=40, gt=0)


def check_phase_calls_model(phase: str) -> None:
    """Raises `ValueError` when `phase` calls no model."""

    if phase not in PHASE_DEFAULT_MODELS:
        raise ValueError(f"{phase!r} calls no model; the phases that do: {', '.join(PHASE_DEFAULT_MODELS)}")


def check_phase_model(phase: str,
                      name: str,
                      models: dict) -> None:
    """Raises `ValueError` saying why `phase` cannot call the model `name` of `models`: the phase calls no model, or no
    model is configured under that name. The one check `phaseModels` and `--phase-model` share."""

    check_phase_calls_model(phase)
    if name not in models:
        raise ValueError(f"{name!r} is not a configured model; configured: {', '.join(sorted(models)) or 'none'}")


class TagRule(_OverlaySettings):
    """One naming rule: a recording whose file name (or meeting folder's name) starts with `prefix`, letter case ignored,
    gets `tag`."""

    prefix: str = Field(min_length=1)
    tag: str = Field(min_length=1)


class TagSettings(_OverlaySettings):
    """Tags a run gets with nobody typing them: by its recording's name, and from the
    last worker of phase 4."""

    # The owner's and the monitoring agent's naming rules; a pattern the monitoring agent finds in 3 recordings or more
    # becomes one. Written before phase 1, beside the run form's tags, and by `i2k apply-tag-rules` onto every run.
    name_rules: list[TagRule] = Field(default_factory=list)
    # The most tags the last worker of phase 4 adds, each from the tags runs already hold or new when none fits
    worker_tags: int = Field(default=3, ge=0, le=10)


class QueueSettings(_OverlaySettings):
    """What the job queue does when a job fails: it runs the job again, with
    `--resume`, before any other job, and after the last try goes on to the next job or holds the queue."""

    # How many times a job that ended incomplete or crashed is run again: 2, so a job runs 3 times in all, as a step
    # gets `budgets.maxAttempts` (3) answers
    retries: int = Field(default=2, ge=0)
    # Seconds a failed job waits before it is run again, so a model host that is restarting has time to come back
    retry_pause_seconds: int = Field(default=60, ge=0)
    # After a job's last try: "continue" starts the next job (the owner's default), "block" holds the queue until the
    # person retries or skips the job. A job's own choice on the page wins over this
    on_failure: Literal["continue", "block"] = "continue"


class ChatProviderSettings(_OverlaySettings):
    """The server that answers chat and vision requests. The model's name is each phase's own (`models`), so there is no
    `model` here."""

    # The address up to and including `/v1`; "" keeps the program's own way of reaching the chat models
    url: str = ""
    # The NAME of the environment variable that holds the server's key; "" for a server with no key. A key is never in a
    # settings file
    api_key_env: str = ""
    # Seconds one request may take before the program gives up on it
    timeout_s: int = Field(default=600, gt=0)


class ProviderSettings(_OverlaySettings):
    """A server that answers one kind of model request by URL: speech to text, speaker turns, image vectors, text with
    boxes, face and person detection, or the frame decision filter."""

    # The address up to and including `/v1`; "" keeps the program's own way of running that model on this machine
    url: str = ""
    # The NAME of the environment variable that holds the server's key; "" for a server with no key
    api_key_env: str = ""
    # The model's name on that server; "" means the name the program uses for it
    model: str = ""
    # Seconds one request may take before the program gives up on it
    timeout_s: int = Field(default=600, gt=0)


class SpeechProviderSettings(ProviderSettings):
    """Speech to text: a whole meeting is one request, so a request may take an hour."""

    timeout_s: int = Field(default=3600, gt=0)


class DiarizationProviderSettings(ProviderSettings):
    """Speaker turns: a whole meeting is one request, so a request may take an hour."""

    timeout_s: int = Field(default=3600, gt=0)


class DetectionProviderSettings(ProviderSettings):
    """Face and person detection: one request per frame, so a request is short."""

    timeout_s: int = Field(default=120, gt=0)


class ProvidersSettings(_OverlaySettings):
    """Where each kind of model is reached: one server per kind, called by URL with the standard REST requests. Every URL
    is "" until a person sets it, and a kind with no URL runs the way it always has (`docs/models.md`)."""

    chat: ChatProviderSettings = Field(default_factory=ChatProviderSettings)
    speech: SpeechProviderSettings = Field(default_factory=SpeechProviderSettings)
    diarization: DiarizationProviderSettings = Field(default_factory=DiarizationProviderSettings)
    embeddings: ProviderSettings = Field(default_factory=ProviderSettings)
    ocr: ProviderSettings = Field(default_factory=ProviderSettings)
    detection: DetectionProviderSettings = Field(default_factory=DetectionProviderSettings)
    decision: ProviderSettings = Field(default_factory=ProviderSettings)


def _describe_validation_issues(error: ValidationError) -> str:
    """Each refused key of a settings check with the reason, as `audio.mlx.model: Input should be a valid string`."""

    issues = [f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()]

    return "; ".join(issues)


class Settings(BaseSettings):
    # The SAME overlay contract as `_OverlaySettings`, applied to the top level: both spellings bind, an
    # unknown key is an error. `extra="ignore"` with no alias generator is silent twice over: a
    # camelCase key at the top level names no field AND is dropped without a word, so the run uses the
    # value nobody chose and every signal reads healthy. Measured: a camelCase key in a local file
    # was ignored for a whole run, which ran on the default instead.
    #
    # `extra="forbid"` is why `read_settings_file` strips `_`-prefixed keys: the local file documents itself
    # with a `_comment` block, and that is a note, not a setting.
    model_config = SettingsConfigDict(env_prefix="PB_", env_nested_delimiter="__",
                                      alias_generator=to_camel, populate_by_name=True,
                                      extra="forbid")

    ollama_base_url: str = "http://localhost:11434"
    lmstudio_base_url: str = "http://localhost:1234/v1"
    models: dict[str, ModelSettings] = Field(default_factory=dict)
    # phase -> the name of the entry of `models` it calls, for the phases that should not use their default
    # (`PHASE_DEFAULT_MODELS`). Set per run by `run --phase-model PHASE=NAME`, which the page's run form writes.
    phase_models: dict[str, str] = Field(default_factory=dict)
    budgets: BudgetSettings = Field(default_factory=BudgetSettings)
    frames: FrameSettings = Field(default_factory=FrameSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    frames_processing: FramesProcessingSettings = Field(default_factory=FramesProcessingSettings)
    document: DocumentSettings = Field(default_factory=DocumentSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    critique_collection: CritiqueCollectionSettings = Field(default_factory=CritiqueCollectionSettings)
    document_reshaping: DocumentReshapingSettings = Field(default_factory=DocumentReshapingSettings)
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    tags: TagSettings = Field(default_factory=TagSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    # The server each kind of model is reached at, by URL. Read by `services/endpoint_client.py`; every URL is "" by default
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    # The Workflow speed menu's presets: each a name and the settings it sets, by their playbook names (the keys
    # `models.speed_setting_vocabulary.SPEED_KEYS` lists). A run keeps the preset chosen for it, and any value changed
    # by hand, in `run-speed.json`; a setting a preset leaves out keeps the settings' own value
    speed_presets: dict[str, dict[str, bool | int | str]] = Field(default_factory=lambda: dict(DEFAULT_SPEED_PRESETS))
    # The worker sets: each a name and the part of the settings it overrides, nested as a profile
    # file nests it (`models`, `lmstudioBaseUrl`, `audio.mlx.model`). Jobs on different sets run at the same time, jobs
    # on one set one after another. The default set is the settings files as they are and has no entry here
    worker_sets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # The set a job takes when none is chosen: "default", or the name of one entry of `workerSets`
    default_worker_set: str = DEFAULT_WORKER_SET_NAME
    # The machine each worker set runs on, in the owner's words ("default": "main machine"), shown beside the set's name
    # wherever a set is chosen; a set with no entry shows its name alone
    worker_set_machines: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _check_worker_sets(cls,
                           values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        worker_sets = values.get("workerSets", values.get("worker_sets"))
        if not worker_sets:
            return values
        if not isinstance(worker_sets, dict):
            raise ValueError("workerSets: it must be an object holding one entry per worker set")
        # Each set is checked by building the settings with it on top: a bad key or value is refused under its name
        settings_without_sets = {key: value for key, value in values.items() if key not in _WORKER_SET_FORBIDDEN_KEYS}
        for name, overrides in worker_sets.items():
            if name == DEFAULT_WORKER_SET_NAME:
                raise ValueError(f"workerSets.{name}: {DEFAULT_WORKER_SET_NAME!r} is the settings files as they are, "
                                 f"so it takes no entry")
            if not isinstance(overrides, dict):
                raise ValueError(f"workerSets.{name}: it must be an object holding the settings it overrides")
            forbidden = sorted(set(overrides) & set(_WORKER_SET_FORBIDDEN_KEYS))
            if forbidden:
                raise ValueError(f"workerSets.{name}: {', '.join(forbidden)} belongs to the settings, not to one set")
            try:
                cls(**merge_settings(settings_without_sets, overrides))
            except ValidationError as error:
                raise ValueError(f"workerSets.{name}: {_describe_validation_issues(error)}") from error

        return values

    @model_validator(mode="after")
    def _check_default_worker_set(self) -> "Settings":
        if self.default_worker_set != DEFAULT_WORKER_SET_NAME and self.default_worker_set not in self.worker_sets:
            raise ValueError(f"defaultWorkerSet: {self.default_worker_set!r} is no worker set; they are "
                             f"{', '.join([DEFAULT_WORKER_SET_NAME, *sorted(self.worker_sets)])}")

        return self

    @field_validator("speed_presets")
    @classmethod
    def _check_speed_presets(cls,
                             speed_presets: dict[str, dict[str, bool | int | str]]) -> dict[str, dict[str, bool | int | str]]:
        for name, values in speed_presets.items():
            unknown = sorted(set(values) - set(speed_setting_vocabulary.SPEED_KEYS))
            if unknown:
                raise ValueError(f"speedPresets.{name}: {', '.join(unknown)} is no setting of the Workflow speed menu; "
                                 f"they are {', '.join(speed_setting_vocabulary.SPEED_KEYS)}")

        return speed_presets

    @field_validator("phase_models")
    @classmethod
    def _check_phase_models(cls,
                            phase_models: dict[str, str]) -> dict[str, str]:
        for phase in sorted(phase_models):
            try:
                check_phase_calls_model(phase)
            except ValueError as exc:
                raise ValueError(f"phaseModels: {exc}") from exc
        # An empty name is the phase's default, as the Settings page's row for each phase writes it
        named_phase_models = {phase: name for phase, name in phase_models.items() if name}

        return named_phase_models

    def find_configured_model(self,
                              name: str) -> ModelSettings | None:
        """The entry of `models` under this name, else `models.default`, else `None`."""

        if name in self.models:
            return self.models[name]

        return self.models.get(DEFAULT_MODEL)

    def resolve_model(self,
                      name: str) -> ModelSettings:
        """The entry of `models` under this name, else `models.default`, else the built-in default."""

        return self.find_configured_model(name) or ModelSettings()

    def select_phase_model_name(self,
                                phase: str) -> str:
        """The name of the model this phase is set to call, `phaseModels`' when it names one, checked by nothing: the
        setup check reads it to say what is missing. Raises `KeyError` for a phase that calls none."""

        return self.phase_models[phase] if phase in self.phase_models else PHASE_DEFAULT_MODELS[phase]

    def resolve_phase_model_name(self,
                                 phase: str) -> str:
        """The name of the model this phase calls. Raises `KeyError` for a phase that calls none, and `ValueError` for
        a name `phaseModels` gives that `models` does not hold: a run never falls back to a model nobody chose."""

        name = self.select_phase_model_name(phase)
        if phase not in self.phase_models:
            return name
        try:
            check_phase_model(phase, name, self.models)
        except ValueError as exc:
            raise ValueError(f"phaseModels.{phase}: {exc}") from exc

        return name

    def resolve_phase_model(self,
                            phase: str) -> ModelSettings:
        """The entry of `models` this phase calls, from `resolve_phase_model_name`."""

        return self.resolve_model(self.resolve_phase_model_name(phase))

    def resolve_frames_per_call(self) -> int:
        """Phase 3's frames a call: its model's own `framesPerCall` when the entry sets one, else
        `framesProcessing.framesPerCall`."""

        model = self.resolve_phase_model(FRAMES_PROCESSING_PHASE)

        return model.frames_per_call or self.frames_processing.frames_per_call


def drop_notes(settings_values: dict) -> dict:
    """The settings without their `_`-prefixed keys, the operator's own notes (`_comment`). The overlay refuses an
    unknown key on purpose, so they are dropped here rather than made to look like one, at every depth, because a
    note belongs beside the settings it explains, not only at the top of the file."""

    return {key: (drop_notes(value) if isinstance(value, dict) else value)
            for key, value in settings_values.items() if not key.startswith("_")}


def read_settings_file(path: Path) -> dict:
    """The settings `path` holds, without notes. A missing file adds nothing and says nothing; a file that cannot be read
    or is not an object adds nothing and logs a warning naming it."""

    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _LOGGER.warning("ignoring %s: %s", path.name, exc)

        return {}
    if not isinstance(data, dict):
        _LOGGER.warning("ignoring %s: it holds a %s, not an object of settings", path.name, type(data).__name__)

        return {}

    return drop_notes(data)


def merge_settings(base: dict,
                   overlay: dict) -> dict:
    """`base` with `overlay` on top: a section on both merges key by key, any other value, a list included, is replaced
    whole."""

    merged = dict(base)
    for key, value in overlay.items():
        is_section_on_both = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = merge_settings(merged[key], value) if is_section_on_both else value

    return merged


def build_profile_path(name: str) -> Path:
    """Where a profile's settings live: `playbook.<name>.json` beside the default and local files."""

    return PLAYBOOK_ROOT / f"{_PROFILE_PREFIX}{name}{_PROFILE_SUFFIX}"


# `playbook.spec.json` is the generator's description of the playbook, not a set of settings
_NOT_PROFILES = (DEFAULT_SETTINGS_NAME, LOCAL_SETTINGS_NAME, "spec")


def list_profile_names() -> list[str]:
    """The profiles a run line may name: every `playbook.<name>.json` beside the default settings but them."""

    names = sorted(path.name[len(_PROFILE_PREFIX):-len(_PROFILE_SUFFIX)]
                   for path in PLAYBOOK_ROOT.glob(f"{_PROFILE_PREFIX}*{_PROFILE_SUFFIX}"))

    return [name for name in names if name not in _NOT_PROFILES]


def read_settings_files(default_file: Path,
                        local_file: Path,
                        profile_path: Path | None = None) -> dict:
    """The settings `default_file` holds, with `local_file`'s on top and `profile_path`'s on top of both, key by key: a
    machine without its local file runs on the default settings rather than the code's bare ones (the owner,
    2026-10-02), and a profile asked for by name wins over both. A default or local file missing or unreadable adds
    nothing."""

    merged = merge_settings(read_settings_file(default_file), read_settings_file(local_file))

    return merge_settings(merged, read_settings_file(profile_path)) if profile_path else merged


def read_layers(profile_name: str,
                worker_set_name: str) -> dict:
    """The settings a run reads, last one wins: the default settings, the local file, the profile `profile_name` names (none
    when empty), then the worker set `worker_set_name` names (none for "default"). Raises `ValueError`, naming the sets
    that exist, for a worker set the merged files do not hold."""

    profile_path = build_profile_path(profile_name) if profile_name else None

    return merge_worker_set(read_settings_files(DEFAULT_CONFIG_FILE, LOCAL_CONFIG_FILE, profile_path), worker_set_name)


def merge_worker_set(merged: dict,
                     worker_set_name: str) -> dict:
    """`merged` (settings already merged from the files) with the entry of `workerSets` named `worker_set_name` on top;
    `merged` itself for "default". Raises `ValueError`, naming the sets that exist, for a name it does not hold."""

    if worker_set_name == DEFAULT_WORKER_SET_NAME:
        return merged
    worker_sets = merged.get("workerSets") or {}
    if worker_set_name not in worker_sets:
        names = ", ".join([DEFAULT_WORKER_SET_NAME, *sorted(worker_sets)])
        raise ValueError(f"no worker set {worker_set_name!r}: the worker sets are {names}")

    return merge_settings(merged, worker_sets[worker_set_name])


def load_profile(name: str) -> list[str]:
    """Read `playbook.<name>.json` on top of the default and local files and rebind `settings` to the result. Returns
    the top-level sections the profile sets, for the line the CLI prints. A name with no file raises: a profile
    asked for and silently not applied runs the whole job on the settings it was meant to replace. The worker set in force
    stays on top of the profile."""

    global PROFILE_NAME, settings
    path = build_profile_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"no profile {name!r}: {path} is not on disk")
    overrides = read_settings_file(path)
    if not overrides:
        raise ValueError(f"profile {name!r} ({path.name}) sets nothing: it is empty, unreadable or all comments")
    # Validated before either global changes: a profile with a bad value leaves the name and the settings in force
    loaded = Settings(**read_layers(profile_name=name, worker_set_name=WORKER_SET_NAME))
    PROFILE_NAME, settings = name, loaded

    return sorted(overrides)


def apply_worker_set(name: str) -> None:
    """Rebind `settings` to the files in force (the default settings, the local file and the profile when one is loaded)
    with the worker set `name` on top; an empty name takes `defaultWorkerSet`. A name no `workerSets` entry holds raises
    `ValueError` naming the sets that exist, before the global changes."""

    global WORKER_SET_NAME, settings
    files_in_force = read_layers(profile_name=PROFILE_NAME, worker_set_name=DEFAULT_WORKER_SET_NAME)
    chosen_name = name or files_in_force.get("defaultWorkerSet", DEFAULT_WORKER_SET_NAME)
    loaded = Settings(**read_layers(profile_name=PROFILE_NAME, worker_set_name=chosen_name))
    WORKER_SET_NAME, settings = chosen_name, loaded


def build_nested_settings(values: dict[str, bool | int | str]) -> dict:
    """`{"framesProcessing.isCritiqueOn": False}` as the settings files nest it: `{"framesProcessing": {"isCritiqueOn":
    False}}`."""

    nested: dict = {}
    for key, value in values.items():
        *sections, name = key.split(".")
        place = nested
        for section in sections:
            place = place.setdefault(section, {})
        place[name] = value

    return nested


def apply_speed_values(values: dict[str, bool | int | str]) -> None:
    """Rebind `settings` to the files in force (the default and local settings, the profile when one is loaded and the worker
    set in force) with this run's Workflow speed values on top. Validated before the global changes, as a profile is."""

    global settings
    merged = read_layers(profile_name=PROFILE_NAME, worker_set_name=WORKER_SET_NAME)
    settings = Settings(**merge_settings(merged, build_nested_settings(values)))


# The one `Settings` of the process, rebound by `load_profile`: read it as `config.settings` at call time, never
# `from config import settings`
settings = Settings(**read_settings_files(DEFAULT_CONFIG_FILE, LOCAL_CONFIG_FILE))
