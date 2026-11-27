# CLI context

How the `i2k` command is built: what each command does, how a command line reaches the pipeline, what a run writes,
and where each rule lives in the code. How to type the commands is in [cli-usage.md](cli-usage.md); the pipeline's
phases are in [FLOW.md](FLOW.md). The web page and the terminal UI sit on top of
this CLI and are described in [ui-context.md](ui-context.md).

## Contents

- [The commands](#the-commands)
- [From a command line to the graph](#from-a-command-line-to-the-graph)
- [run](#run)
- [What a run writes](#what-a-run-writes)
- [Choosing phases and resuming](#choosing-phases-and-resuming)
- [inbox](#inbox)
- [Pausing, draining and stopping a run](#pausing-draining-and-stopping-a-run)
- [Taking a run back: reset-to and reset-chapter](#taking-a-run-back-reset-to-and-reset-chapter)
- [models and probe](#models-and-probe)
- [setup-models](#setup-models)
- [setup-mlx](#setup-mlx)
- [Where the defaults live](#where-the-defaults-live)
- [Tests](#tests)

## The commands

| Command | Module | What it does |
|---|---|---|
| `run` | `src/cli/run_command.py` | runs the numbered phases over one video into one run folder; the only command that calls a model; `--workflow quality|direct-draft|fast-processing` is kept in `run-workflow.json` before phase 1 and named in the header's `workflow:` line |
| `inbox` | `src/cli/inbox_command.py` | runs `run` over every inbox video that has no document yet, one after another |
| `models` | `src/cli/setup_commands.py` | prints which configured model each phase calls, with the settings sent to it |
| `probe` | `src/cli/setup_commands.py` | asks each configured model host whether it answers and what it has loaded |
| `tags` | `src/cli/tags_command.py` | prints a run folder's tags; `--add`, `--remove` and `--set` change them, kept in `run-tags.json`; `run --tag` adds them before phase 1 |
| `branch` | `src/cli/branch_command.py` | `--output <run> --from-phase N`: the run copied into `<run>-from-<N>/` and taken back to phase N - 1 there, printing the `run --resume` line; refused under a live run and past the phase after the last reached (`run_operations_service.create_branch`, `run_branch_service`) |
| `convert` | `src/cli/convert_command.py` | `--output <run> --to <ingesting profile>`: copies phases 1 to 3 into `<run>-<profile>/` and prints the `run --from 4 --force` line that writes its document; for an audio recording the two profiles prepare in other steps, phases 1 and 2 and `--from 3` (`run_operations_service.copy_run_for_profile`, `profile_convert_service.select_first_phase`) |
| `recording-to-inbox` | `src/cli/recording_command.py` | moves the recording a done run keeps in `<run folder>/recording/` back to the inbox |
| `recording-to-run` | `src/cli/recording_command.py` | keeps a done run's inbox recording in its folder, as the run does when it ends |
| `compress-recording` | `src/cli/recording_command.py` | re-encodes the recording a done run keeps in its folder to 720p, in place |
| `mcp` | `src/cli/mcp_command.py` | serves the program's controls as MCP tools over stdio, calling the running `i2k serve` (`--server`, `I2K_SERVER_URL`) |
| `run --queue` | `src/cli/run_command.py` | hands the run to the running `i2k serve`'s queue (`services/server_client_service.py`), the request the page's Run form sends, and returns; `--force` is not carried |
| `setup-mlx [MODEL]` | `src/cli/mlx_setup_command.py` | installs mlx-vlm in a venv of its own unless it is installed, and downloads MODEL |
| `setup-models [--hf-token T]` | `src/cli/model_setup_command.py` | downloads the recommended models into `models/` and links them into LM Studio |
| `reset-to N` | `src/cli/reset_command.py` | removes every file and progress record of the phases after N; lists only, without `--yes` |
| `reset-chapter N` | `src/cli/reset_command.py` | removes chapter N and every later chapter of phase 4; lists only, without `--yes` |
| `refine-frames` | `src/cli/frame_drop_command.py` | lists the frames phase 3 has not read that the frame knowledge classifier would drop; with `--yes` drops them all |
| `drop-frames FRAME…` | `src/cli/frame_drop_command.py` | drops the frames named from the run before phase 3 reads them; lists only, without `--yes` |
| `blocks` | `src/cli/blocks_command.py` | how often each step of phases 4 and 5 blocked, was accepted and was reset, with each block's time and reason |
| `serve` | `src/cli/serve_command.py` | the web page, its API and the job queue ([ui-context.md](ui-context.md)); `--sign-in on\|off` (or `I2K_SIGN_IN`) chooses whether anyone signs in |
| `accounts list\|add\|remove\|role\|password` | `src/cli/accounts_command.py` | the accounts of `serve`, on their files directly, so the first admin is made before any server runs; `add NAME --role R`, `role NAME R`; a password is typed twice with getpass, never on the command line |
| `login [NAME]`, `logout` | `src/cli/accounts_command.py` | signs in to the running `serve` and keeps the token in `ui/client-token` for `mcp`, `run --queue` and `tui`; `logout` ends the session and removes it |
| `tui` | `src/cli/tui_command.py` | the terminal UI, talking to `serve` ([ui-context.md](ui-context.md)) |

The code was generated from the `chain-pipeline` profile of the `playbook-creator` skill. A file whose first lines carry
a `sync:` marker came from the profile or its seed; a file without one is this project's own.

## From a command line to the graph

1. **`i2k`** is a bash wrapper. It resolves its own directory through any symlink and runs `main.py` with the project's
   `.venv` Python, so the command works from any folder without the project being installed.
2. **`main.py`** puts `src/` on the import path and calls `cli.main()`. `src/` is flat: its folders (`cli`, `graph`,
   `chains`, `prompts`, `schemas`, `validators`, `services`, `models`, `utils`, `tui`) are imported by name, with no
   package between them and the source root.
3. **`cli/__init__.py`** holds the whole command surface. `build_parser()` is a pure function of the command line, so
   what a line means can be tested without running it. `HANDLERS` maps each command name to its function; each
   function takes the parsed arguments and returns the process exit code.
4. **Logging** is set up once there: messages only (`%(message)s`) on standard output, and the routine INFO lines of the
   libraries in `QUIET_LOGGERS` (the HTTP client) silenced at WARNING.
5. **Ctrl-C** anywhere in a command ends it with exit code 130.

## run

`cmd_run` does six steps, each marked in the code:

1. **Resolve and refuse.** `resolve_paths` turns `--video` and `--output` into a `RunPaths` (the video's folder and
   name, and the run folder). `build_phase_request` turns the phase flags into a `PhaseRequest`. `_open_run` mints a
   new run record or continues the newest one. The model capability check and the transcription engine check run
   here, before anything expensive: a missing package or a model without what its phase needs is a refusal, exit 2.
2. **Create the run's folders** (`run_paths_service.ensure_run_paths`), also on a resume.
3. **Clear a leftover control signal**, or it would stop the new run at once.
4. **Announce the run** on the console and open the console log file (`cli_log_service`); open a timing session in
   `performance.jsonl` (`performance_log_service.open_session`).
5. **Build the graph** (`graph_assembly.build_graph`) and invoke it with the run's own step limit
   (`budgets.runSteps`), never one attempt's limit: the graph takes one step per frame plus one per phase.
6. **Report and decide the exit code** (`_report_run`), and write the session's closing timing line.

| Exit code | Meaning | Run record status |
|---|---|---|
| 0 | every selected phase finished and every frame reached a final status | `complete` |
| 1 | a phase failed, or something is blocked | `failed` or `incomplete` |
| 2 | the command line could not be honoured: a bad target, contradictory phase flags, a missing model capability, nothing to resume | not written |
| 130 | stopped by Ctrl-C or a `stop` signal | `interrupted` |

## What a run writes

Everything goes into the one run folder `--output` names. The phase files are listed in [FLOW.md](FLOW.md); beside them:

| Path in the run folder | Written by | Holds |
|---|---|---|
| `logs/<run id>/run.json` | `run_store_service.RunStore` | the run record: identity (video, output folder), status, the process id writing the folder (`pid`), budgets, the model per role, each phase's status, each frame's status and attempts |
| `logs/<run id>/HH-MM.log`, `HH-MM-resume.log` | `cli_log_service` | every console line of one start of the run |
| `logs/<run id>/performance.jsonl` | `performance_log_service` | one line per session start, per frame, batch or part, per phase and per session end, each with its time and token counts |
| `logs/<run id>/control.json` | `control_signal_service.request` | a waiting `pause`, `drain` or `stop` signal |
| `calls/` | `call_log_service` | what each model call was sent and answered |

A run id is the minute the run was created (`2026-10-01_16-09`), with `dry-` in front for a dry run. A phase's status in
`run.json` is written when the phase ends, so a stopped run keeps the status its last session left.

## Choosing phases and resuming

- **`--phase N`** runs phase N alone; **`--from N`** runs N and every later phase; **`--to N`** stops after N;
  **`--worker N`** asks phase 3 again for frame N only (repeatable). `build_phase_request` refuses `--phase` with
  `--from`/`--to`, `--worker` with any phase flag, a phase that does not exist, and `--from` after `--to`.
- **A phase that is not run is read from its file.** A missing input file is refused by name, never recomputed.
- **`--resume`** continues the newest run record of the folder (`RunStore.load_latest`, dry runs skipped). The phases the
  folder already holds are adopted from their files (`run_resume_service.describe_finished_phases`), so a resume never
  runs a phase again over output that is there. With no run record at all, the finished phase files are adopted into a
  new record; with neither, the resume is refused.
- A resumed life of a run gets each step's attempt budget again (`RunStore` counts attempts per life).

## inbox

- **What is listed** (`inbox_service.list_inbox_videos`): every video file directly in `inbox/`, and the one video of
  each folder in it that holds exactly one (a meeting folder, which may hold the meeting tool's own transcript, R14).
  Dot files and folders, `inbox/.trash/` and `inbox/.archive/` included, are never listed.
- **What is new** (`list_new_videos`): a video whose run folder `output/<video stem>/` has no `04_document.md`. Nothing
  else records what was seen: the document is the record.
- **How it runs**: each new video is handed to `cmd_run` with the same arguments a typed `run` line would have, one
  after another. A failed video is reported by name and the next starts; the exit code is 1 when any failed.
- `--inbox` and `--output` replace the two folders; `inbox`'s `--output` is a root with one folder per video, where
  `run`'s `--output` is the run folder itself.

## Pausing, draining and stopping a run

A run reads `logs/<run id>/control.json` (`control_signal_service`) at three points: between frames (all three
signals), between attempts (`pause`, `stop`) and before each model call (`stop`). `drain` lets the current frame finish
and then stops. A torn or unreadable file counts as no signal. The web page and the terminal UI write this file; a
signal left from an earlier session is cleared when a run starts.

## A recording processed once

`run` hashes its recording (SHA-256, cached in `ui/recording-hashes.json` by path, size and modification time) and
refuses it by name when a done run in another run folder has the same recording: `<root>/<name> is done with the same
recording (SHA-256). Add --force to process it again` (`run_command.find_duplicate_refusal`,
`recording_hash_service.find_done_run`). `--force` on `run` and on `inbox` processes it anyway. The same run folder is
never a duplicate of itself, so Run again and `--resume` work.

## The recording a done run keeps

A `run` that ends with every phase complete in its record moves its recording out of the inbox into
`<run folder>/recording/` (`run_recording_service.keep_recording_if_done`): the video, or its meeting folder whole, with
`recording/.origin.json` naming the inbox path the run record names. A video given from outside the inbox stays where
it is. A later `run` naming that inbox path finds the recording there, or in a sibling run folder of the same video.
While it is there, phases 1 and 2 (which read it) do not run: `run` refuses by name a new run starting at phase 1 or 2
and a resume where either is not complete, and `reset-to 0` and `reset-to 1` refuse. `recording-to-inbox --output
<run folder>` (`cli/recording_command.py`) puts it back under its inbox name, numbered when that is taken, refused
while a run writes into the folder; then phases 1 and 2 run again. `recording-to-run --output <run folder>` keeps it there
again (a done run whose recording is an inbox video only).

## Taking a run back: reset-to and reset-chapter

- **`reset-to N`** (`run_reset_service`) removes every file and progress record of the phases after N, as if they never
  ran, and keeps the phases up to N and the logs. `reset-to 4` puts back the published document from
  `05_review/before.md`. `reset-to 0` and `reset-to 1` refuse while the folder keeps its recording (below).
- **`reset-chapter N`** (`chapter_reset_service`) removes chapter N and every later chapter of phase 4 (draft headings,
  texts, requests, call logs, sections, the final title and summary), so a resume writes them again. `--text-only`
  keeps the draft headings of chapter N and after and removes only their texts, sections and the final parts. Both set
  aside every cut no kept section places (`discarded: "reset"` in `04_writing/images.json`).
- **`refine-frames` and `drop-frames`** (`frame_drop_service`) take frames out of a run after phase 2, before phase 3
  reads them, as phase 2's knowledge filter does: the image moves into `frames/dropped/`, the entry goes from `items` to
  `droppedFrames` in `02_frames.json` (with `droppedBy`: `filter`, `refine` or `hand`, and the drop score), every
  `logs/*/run.json` loses the frame, and the speech of every frame phase 3 has not read is cut again from the
  transcript, so a dropped frame's speech goes to the frame before it. A frame with notes is refused by name.
  `refine-frames` runs the classifier on the frames phase 3 has not read and lists those it would drop; `--yes` drops
  them all. `drop-frames` drops the frames named. Both refuse while a run writes into the folder.
- **Both only list** what they would remove unless `--yes` is given, and both refuse while a `run` process writes into
  the folder. That check (`run_reset_service.list_live_runs`) reads the process list: a Python process whose
  `main.py` is followed by `run` and whose `--output` names the folder. `list_run_processes` is the same list
  without the folder filter, which the UI's queue also reads.

## models and probe

`models` prints each phase that calls a model with the configured model it calls (`frame` for phase 3, `document`
for the others, unless `phaseModels` or `run --phase-model PHASE=NAME` names another), its provider, model and context
size, then every configured model, from `chat_model_service.describe_phase_models`; transcription has its own `audio`
settings. `probe` asks each configured host (LM Studio, Ollama) whether it answers and
what it has loaded. Neither starts a run.

## setup-models

`setup-models [--hf-token TOKEN]` (`services/model_setup_service.py`, the list in
`models/recommended_model_vocabulary.py`) downloads each recommended model's files (its weights and its vision
projector) from Hugging Face into `<repository>/models/<publisher>/<name>/`, git-ignored, skipping a file already
there, then gives LM Studio a symbolic link to each (`lms import --symbolic-link`) unless its models folder holds the
file already. The token (else `HF_TOKEN`) is needed only for a gated repository. Exit 1 when a download or a link
failed; the next file is still tried. `setup.sh` runs it last, after the venv, the token in `.env` and the web build.

## setup-mlx

`setup-mlx [MODEL] [--venv PATH]` (`services/mlx_setup_service.py`) makes mlx-vlm usable for `mlx_vlm.server`, in
this order:

1. Anything but an Apple silicon Mac is refused by name (exit 2).
2. mlx-vlm is looked for in the venv, the Python running `i2k`, every `python3` and `python3.N` on `PATH`, and the
   python.org, Homebrew, `/usr/local` and pyenv folders; the first in which `import mlx_vlm` works is used as it is.
3. Otherwise PyPI says which Python versions fit: those mlx-vlm's `requires_python` accepts and for which mlx ships a
   macOS arm64 wheel this macOS can install (a free-threaded build does not count). The newest such Python on the
   machine creates the venv (`~/.mlx-vlm/venv` by default); pip and mlx-vlm with its dependencies are installed
   into it, and the import is checked.
4. With no such Python, Homebrew installs the newest one when the person answers yes; without a terminal or
   Homebrew, the command names what to install and exits 2.
5. A named MODEL is looked up in the Hugging Face cache (`snapshot_download(local_files_only=True)`) and downloaded
   only when missing, with the Python that has mlx-vlm; the line to start the server is printed.

Exit codes: 0 ready, 2 the machine lacks what it needs, 1 an install or download step failed.

## Where the defaults live

- **`src/config.py`**: `PLAYBOOK_ROOT` (the project's root folder), `WORKSPACE_ROOT` (the same folder, or `I2K_WORKSPACE`),
  `INBOX_DIR` (`inbox/`), `DOCUMENT_ROOT` (`output/`: inbox runs land beside hand-typed ones), and the settings, with
  `playbook.local.json` laid over the shipped defaults on this machine.
- **`models/output_contract.py`**: the phase numbers and names, the phase files, the frame statuses, the run record's
  file and folder names.
- **Earlier inbox runs** left run folders in `knowledge/`; nothing new is written there, and the UI still lists them.

## Tests

The offline suite (`.venv/bin/python tests/run_tests.py`) reaches no model host. Cases that cover the CLI:
`test_phase_flags` (phase selection), `test_inbox_service` (what is new), `test_resume_without_run_record`,
`test_run_reset` and `test_chapter_reset` (the resets and the live-run check), `test_cli_log_names` (log file names),
`test_console_noise` (silenced libraries). `test_layout_conventions`, `test_layer_boundaries` and
`test_module_coverage` hold the naming and layering rules every module follows.
