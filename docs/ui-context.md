# UI context

How the web page (`i2k serve`) and the terminal UI (`i2k tui`) are built: their architecture, where each feature
lives, how they learn what a run is doing, and how they are tested. What each button does for a user is in
[ui-usage.md](ui-usage.md); the commands they start are in [cli-context.md](cli-context.md).

## Contents

- [Architecture](#architecture)
- [Server modules](#server-modules)
- [The API](#the-api)
- [How the UI knows what a run is doing](#how-the-ui-knows-what-a-run-is-doing)
- [The job queue](#the-job-queue)
- [The inbox, uploads, the trash and the archive](#the-inbox-uploads-the-trash-and-the-archive)
- [Artifacts and the recording](#artifacts-and-the-recording)
- [The web page](#the-web-page)
- [The terminal UI](#the-terminal-ui)
- [Tests](#tests)
- [The skill it is synced with](#the-skill-it-is-synced-with)

## Architecture

```
browser (web/, React)          terminal (src/tui/, Textual)
        │  HTTP, an update a second       │  HTTP (ui_api_client)
        └──────────────┬──────────────────┘
                 i2k serve  (FastAPI, src/services/web_api_service.py)
        ┌──────────────┼───────────────────────────┐
  reads run folders   job queue (ui/jobs.json)    inbox/, inbox/.trash/, inbox/.archive/
  (run.json, logs,     │ one child at a time
   performance.jsonl,  └─> main.py run --video … --output …   (the CLI's run, in its own session)
   phase files)
```

- **One server, two clients.** The web page and the terminal UI both talk to `serve`; neither runs the pipeline itself.
- **A run is always a child process** `main.py run …`, started by the queue in a session of its own: every refusal and
  exit code stays the CLI's, and a run outlives the server that started it.
- **Everything shown is read from disk** or from the process list, never from a run's memory, so a run started on the
  command line shows up exactly like one started from the page.
- **Accounts, off until the first one exists, or off for the server.** The server listens on every address (`0.0.0.0`), so the page opens from the same network;
  `--host 127.0.0.1` keeps it to this machine, and an address beyond it with sign-in off prints one warning line. `i2k serve --sign-in off` (or `I2K_SIGN_IN=off`) asks nobody to sign in and opens every route and project; it is
  chosen at start and is no setting the page edits, and `GET /api/auth/me` says it (`is_sign_in_required`). With sign-in
  on and no account every route answers as before; once `i2k accounts add` makes one, every `/api` route needs a
  signed-in session but signing in, out and `GET /api/auth/me`. Each account has
  one role, each allowed what the one before it is: `viewer` reads everything, `operator` also changes runs, jobs and
  the inbox, `admin` also the settings, the setup and the accounts. Which role a route needs is one table,
  `utils/route_role_utils.find_needed_role`, checked in front of the router (`web_api_service._check_account`): 401 with
  no live session, 403 for a role below. Accounts (a scrypt hash with its salt) are `ui/accounts.json`, sessions
  (14 days) `ui/sessions.json`, both 0600 (`services/account_service.py`). The page sends its session as the
  `i2k_session` cookie; the CLI's clients (`i2k mcp`, `run --queue`, the terminal UI) send the token `i2k login` keeps
  in `ui/client-token` (or `I2K_TOKEN`) as a bearer (`services/client_token_service.py`).
- **Projects and groups** `ui/projects.json` (`services/project_service.py`) holds each
  project's title, the roles it gives to accounts (`members`) and to groups (`groups`), and which project each run
  folder is in; a run it does not list is in `default`. `ui/groups.json` (`services/group_service.py`) holds each
  group's accounts. An identity's role in a project is the highest it is given there by name or by group, in `default`
  its account's own unless a grant by name replaces it; another project gives nothing to anyone it names nowhere; an
  admin account is admin everywhere. The check in front of the router takes the run's project for a route of one run
  (`route_role_utils.find_request_run`), the account's own role for the accounts, projects, groups, settings and setup
  (`is_admin_path`), and its role in `default` for the rest; `GET /api/runs`, `/api/inbox` and `/api/jobs` leave out
  what it may not see, and `POST /api/jobs` checks each run's project. `GET /api/projects` lists the projects an account
  holds a role in (every one, with its grants and the groups, for an admin); the rest of `/api/projects` and
  `/api/groups` is an admin's. The page: the Manager tab's Admin section, under its run tools (Users, Groups, Projects; `AccountsView`, `GroupsView`,
  `ProjectsView`) shown to an admin alone, and the top bar's project menu, which filters the inbox and run lists.

## Server modules

| Module | Role |
|---|---|
| `cli/serve_command.py` | starts the queue's worker thread and uvicorn; a stop waits at most 2 s for open requests (a video download among them) |
| `cli/tui_command.py` | starts a local server in the background when none answers (console in `ui/server.log`), then the terminal UI; it binds the port itself and hands the listening socket to `serve --fd`, so its one request waits in the socket's queue until the server answers |
| `services/web_api_service.py` | the FastAPI app: one `APIRouter` under `/api`, and the built page from `web/dist/` at `/` |
| `services/job_queue_service.py` | the queue: `ui/jobs.json`, one child at a time, consoles in `ui/jobs/<job id>.log` |
| `services/run_status_service.py` | what a run folder is doing: status, phases, frames, timing, newest log, live processes, inbox state |
| `services/run_artifact_service.py` | which files of a run folder a person looks at, serving one, zipping a document with its figures, frame times |
| `services/artifact_edit_service.py` | saving one `.md` of a run folder from the page, the bytes it replaces kept in `.edits/<stamp>/<path>`, 20 versions per file |
| `services/upload_bundle_service.py` | an upload of several files, a folder or a zip: held in `inbox/.staging/<id>/` while a zip is unpacked and each recording checked against the runs and the inbox, then every recording moved into the inbox as its own, a transcript of the same name with it in a folder of its stem; nothing is asked |
| `services/inbox_merge_service.py` | ticked inbox recordings that are one meeting merged into one recording in parts: a folder of their own, parts numbered `01 `, `02 ` in natural name order, named by what their names share unless a name is given |
| `services/upload_service.py` | an upload into `inbox/`: written to `<name>.part`, renamed when complete, numbered on a clash |
| `services/hidden_artifact_service.py` | which artifacts are hidden from the UI's view of a folder, in `ui/hidden-artifacts.json` |
| `services/inbox_rename_service.py` | renaming an inbox video in its own folder, keeping a video suffix, never over a file that is there |
| `services/inbox_move_service.py` | moving inbox videos (or meeting folders) to `inbox/.trash/` or `inbox/.archive/` and back, each folder's `.origins.json` recording where each entry came from |
| `services/setup_check_service.py` | the setup wizard's check, its saving of a chosen host and model, and the models download; the steps per system are `models/setup_runtime_vocabulary.py` |
| `services/run_operations_service.py` | the short operations (`reset-to`, `reset-chapter`, the recording moves, compress, `models`, `probe`), one function each, called by the endpoints and the CLI commands alike; a compression runs in a child that calls it directly |
| `services/ui_api_client.py` | the terminal UI's HTTP calls, with a transport seam for tests; it sends the kept token, and asks the open `/api/auth/me` whether a server is up |
| `services/account_service.py` | the accounts and their sessions: add, remove, a role, a password, sign in and out, a session read |
| `services/client_token_service.py` | the token the command line's clients send: `I2K_TOKEN`, else `ui/client-token` |
| `utils/route_role_utils.py` | the role each `/api` route needs, the one table the server checks |
| `models/run_job.py` | `RunJobRequest`, `RunJob`, the exit-code-to-status table |
| `models/web_api_request.py` | the API's request bodies (pydantic) |
| `models/phase_guide.py` | what each phase does, reads and writes, and whether it calls a model, for the `?` help |
| `models/ui_errors.py`, `models/slash_command_request.py` | the UI's error types and a parsed slash command |
| `utils/slash_command_utils.py` | the terminal UI's slash commands and their parser |
| `tui/console_tui_runner.py` | the Textual app |

Every module follows the layout tests' naming rules: a role suffix on the module, a verb or a question opening every
function, a kind word ending every class (Textual's `App` and pydantic's `BaseModel` subclasses are exempt).

## The API

| Route | What it does |
|---|---|
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | signing in (`{name, password}`: the account, its role and the token, and the `i2k_session` cookie; 401 for a wrong name or password, never saying which), out, and who is signed in with whether accounts exist (`has_accounts`, `account`, `roles`); open to everyone |
| `GET /api/accounts`, `POST /api/accounts`, `PUT /api/accounts/{name}`, `DELETE /api/accounts/{name}` | an admin's: every account with its role and when made; a new one `{name, password, role}`; a new role or password (a new password ends the account's sessions); a removal; a refusal is 400 in the store's words |
| `GET /api/phases` | the phases with their help text |
| `GET /api/inbox` | every inbox video with its run folders and its state |
| `POST /api/inbox/upload` | saves uploaded videos into `inbox/`; one refused file does not stop the others |
| `GET /api/inbox/video?path=` | streams an inbox video (range requests, so a player can seek) |
| `GET /api/inbox/trash`, `POST /api/inbox/trash`, `POST /api/inbox/trash/restore`, `POST /api/inbox/trash/delete` | the trash; `delete` removes entries for good, only from `inbox/.trash/` |
| `GET /api/inbox/archive`, `POST /api/inbox/archive`, `POST /api/inbox/archive/restore` | the archive, each entry with its runs, state and topic |
| `POST /api/inbox/upload-bundle` | save an upload of several files, a folder (each file's path in it) or a zip, each recording as its own; `{added, refused}` |
| `POST /api/inbox/merge` | merge two or more ticked inbox recordings into one recording in parts, their tags with it; a recording with a job queued or running is refused |
| `POST /api/inbox/rename` | renames an inbox video; the page offers it only on New videos, since a run record names its video by path |
| `GET /api/runs`, `GET /api/runs/{root}/{name}` | the run folders, and one in full |
| `GET /api/runs/{root}/{name}/log?offset=`, `…/update?offset=` | the newest log from a byte offset; the folder's state and its log text from the offset, with the offset to ask from next (one short answer, asked each second) |
| `GET /api/runs/{root}/{name}/artifacts`, `…/files/{path}`, `…/download/{path}`, `…/recording` | artifacts, one file, a download (a zip when a markdown links files), the recording |
| `PUT /api/runs/{root}/{name}/files/{path}` | saves one markdown artifact as the page's editor left it; the version it replaces is kept first |
| `GET …/versions/{path}`, `GET …/version/{path}?at=` | the versions of one markdown artifact, newest first; one version's text |
| `GET /api/runs/{root}/{name}/transcript` | phase 1's transcript as `lines`, each with `start_s`, `end_s` and `text` (`run_artifact_service.read_transcript_lines`); none before phase 1 |
| `GET /api/runs/{root}/{name}/worker?phase=N`, `…/worker/attempt?ref=` | what the worker of phase N saw (`worker_view_service`): its steps in the order asked, each with the attempts the run saved (`logs/<session>/<phase>/<step>/attempt-N/`, a dry run's `logs/<session>/dry-run/<phase>/<step>/`, and phases 4 and 5's follow-ups from `logs/conversation.jsonl`) and how each ended; one attempt's instructions (`system-prompt.md`), its input as text and images, each image named by the frame the text before it last names (`Frame 12, …`) with its file from `02_frames.json`, its findings from `validation.json`, and a follow-up's turns; a `ref` outside the run's `logs/` is 404 |
| `GET /api/phase-instructions?phase=N&audio=B` | each instruction set phase N sends to a video run (an audio run with `audio=true`; the other medium's sets left out), built now from `agent-context/` by `system_prompt_builder` for a step with no fields of its own (`write` with every follow-up's section, as the conversation sends it), each with `used_by`, who is sent it at which stage, no model called; a set that cannot be built so names why (`error`) |
| `GET /api/runs/{root}/{name}/transcript/download` | the transcript as `<folder>-transcript.txt`, one `[mm:ss] text` line per thing said (`render_transcript_text`); 404 before phase 1 |
| `POST /api/runs/{root}/{name}/control` | writes `pause`, `drain` or `stop` into the run's `control.json`; `continue` removes it |
| `POST /api/runs/{root}/{name}/reset` | `reset-to` or `reset-chapter`: the listing, or with `is_confirmed` the removal |
| `POST /api/runs/{root}/{name}/frames/refine` | `refine-frames`: the classifier's drop score and decision on every frame phase 3 has not read, in `records`; nothing changes |
| `POST /api/runs/{root}/{name}/frames/drop` | `drop-frames`: `frames` dropped; `proposed` and `drop_scores` are what a Refine proposed, so each frame records `refine` or `hand`, and the proposal's FP and FN are saved as the classifier's learning data; with a proposal, `frames` may be empty |
| `POST /api/runs/display-names` | `{runs, by_topic}`: each run shown under its AI name (its topic) or, with `by_topic` false, its file name again (`run_operations_service.name_runs`); nothing on disk is renamed; a run with no topic yet keeps its name |
| `GET /api/download/parts` | the parts of a run a batch download may hold, `{key, label}` (`batch_download_service.DOWNLOAD_PARTS`): for now only `document` |
| `POST /api/download/documents` | `{runs: [{root, name}], parts}`: one zip of each run's published `<name>/<name>.md` and `resources/`, a run with no document left out and named in `X-Skipped-Runs`; 409 when no part is known or no run has a document |
| `PUT /api/runs/{root}/{name}/tags` | `tags --set`: `{tags}` becomes the run's whole list of tags; the run list and the run's detail carry them as `tags` |
| `GET /api/tags` | what the tag box suggests: every tag runs have, once whatever its letter case, and each ingesting profile with its aliases |
| `GET /api/ingesting-profiles` | the ingesting profiles, the default first, each with the tags that pick it |
| `POST /api/runs/{root}/{name}/branch` | `branch --from-phase {phase}`: the run copied into `<run>-from-<phase>/` up to that phase; answers `records[0].name`, which the page opens (nothing is queued) |
| `POST /api/runs/{root}/{name}/convert` | `convert --to {profile}`: phases 1 to 3 copied into `<run>-<profile>/`, and its `run --from 4 --force` queued (`--from 3` with phases 1 and 2 when phase 3 prepares an audio recording again); answers `jobs`, whose request carries `purpose`, naming each phase the job runs |
| `POST /api/runs/{root}/{name}/recording/to-inbox` | `recording-to-inbox`: the recording a done run keeps goes back to the inbox |
| `POST /api/runs/{root}/{name}/recording/delete`, `…/recording/upload` | the recording into `inbox/.trash/` (from the inbox or the run folder); a missing recording saved where the run record names it; both refused (409) while a run writes into the folder |
| `POST /api/runs/{root}/{name}/recording/to-run` | `recording-to-run`: a done run's inbox recording is kept in its folder (`can_keep_recording`) |
| `POST /api/runs/{root}/{name}/recording/compress` | starts `compress-recording` in its own session and returns its pid; its progress is the run's `recording_compression` (status, sizes, error) |
| `GET /api/profiles` | the settings profiles a job may name (`playbook.<name>.json` but default, local and spec) and the server's own; `POST /api/jobs` takes `profile` |
| `GET /api/run-defaults?profile=` | what a run started now takes for each choice the run form leaves untouched (`pdf_mode`, `pdf_ocr_engine`, `knowledge_filter_model`, `workflow`), read from the files under that profile (the server's own when empty), 404 for a profile with no file; the form's untouched boxes name it ("default: text only") |
| `GET /api/phase-models` | the configured models (`models`, each `name` and `spec`) and each phase that calls one, with its number, title and the model it calls when a run names none; `POST /api/jobs` takes `phase_models` (phase -> name), refused with 400 for a phase or name not listed, and the job's line gets one `--phase-model PHASE=NAME` each |
| `GET /api/settings?profile=` | every setting: `key` (playbook name), `value`, `kind`, `source` (code, default, local or the profile), `comment` |
| `PUT /api/settings` | `{key, value, profile}`: saved in both default settings files or the profile's file; `exit_code` 2 with the schema's reason when refused |
| `GET /api/instructions` | the workers' instructions as a tree (`instruction_catalogue_service`): each workflow with its profiles or modes, its phases, each step with who gets it and the files its worker is built from; and every instruction file with `kind` (instruction, shared, document, compaction), `used_by`, `is_edited` and `is_default_changed` |
| `GET /api/instructions/file?id=` | one file: `default` (as shipped), `text` (in force), `is_edited`, `is_default_changed`, `default_at_edit`, `versions`, its title and steps; 404 for a file the program does not ship |
| `PUT /api/instructions/file` | `{id, text, note, kind}` (`edit` or `restore`): put in force in the workspace store and kept as a version; `is_saved: false` with `problems` when the program could not build a worker's message from it |
| `POST /api/instructions/reset` | `{id}`: the default back in force, the edit kept as a version |
| `GET /api/instructions/versions?id=`, `GET /api/instructions/version?id=&at=` | every version, newest first (`at`, `kind`, `account`, `note`, `size_bytes`); one version's `text` and the `default_text` it was made over |
| `POST /api/instructions/preview` | `{step, id, text}`: the whole system prompt the step's worker gets, with `text` in place of file `id` when given; `error` when it cannot be built |
| `GET /api/jobs`, `POST /api/jobs`, `POST /api/jobs/{id}/cancel`, `…/interrupt`, `…/log`, `…/update` | the queue |
| `GET /api/models`, `GET /api/probe` | what those commands print |
| `GET /api/setup` | what the setup wizard shows (`setup_check_service.check_setup`): each model host, API and tool with `is_needed`, `is_installed` and, for a local host, `is_running`, its steps for macOS, Windows and Linux and its download page; each model a phase calls with whether its host has it; `has_models`; and the models download's state and log |
| `POST /api/setup/configure` | `{provider, model_id, base_url, api_key}`: the host and model chosen, written as `models.frame` and `models.document` in both default settings files; an API key into the git-ignored `.env` only |
| `POST /api/setup/models` | `{hf_token}`: starts `i2k setup-models` in its own session (the token as `HF_TOKEN`, never on its command line), its lines in `ui/setup-models.log`; 409 while one runs |

`{root}` is `output` or `knowledge` (`run_status_service.RUN_ROOTS`); a name that leaves its root is refused with 404.
JSON fields are snake_case, matching the job records.

## How the UI knows what a run is doing

- **Status and phases** come from the newest `logs/<run id>/run.json`. A phase without a recorded status is `complete`
  when its output is on disk, else `not run`; phase 3 with written frames and no status of its own is `incomplete`
  (a stopped run never records it).
- **Live** means the process the folder's run record names by its `pid` is running, or a `main.py run` or
  `main.py inbox` process whose `--output` names the folder is in the process list
  (`run_reset_service.list_live_runs`). The record's pid is what an `inbox` run is found by, since it names no folder
  on its command line; a `--output` path is matched word by word, so a folder name holding a space is found too.
  While live, the folder's status reads `running`.
- **The running phase** is read from the newest log, because the record changes only when a phase ends: the last line
  opening with `phase N`. A `phase N time:` line means phase N ended and the next one runs (phase 3 prints no line when
  it starts); after the last phase's time line no phase runs. A resume prints a line for a phase it only reads from its
  file, so the running phase is the first one from the log's phase on that is not complete.
- **Frames** are counted by status from `run.json`'s items; the blocked ones are named, so they can be asked again.
  While a live run reads frames (phase 3 running), a frame recorded blocked counts as pending and is not named: a
  resume asks every blocked frame again, and phase 3 stops on the first frame that blocks anew.
- **Timing** (`describe_timing`) sums `performance.jsonl` over every session of the folder: each phase's phase lines (or
  its unit lines when the session ended before the phase line), each session's length, and, while live, the current
  session and the running phase up to now. Phases 1 and 2 start over on every run, so each counts its newest session
  alone: summed, a phase 2 taken back and run again many times read hours a few minutes into its newest run.
- **Topic**: what the meeting is about in a few words, read from `04_writing/parts.json`, which phase 4's agenda step
  writes minutes into phase 4; each run folder and each inbox video (from its run) carries it, and the page shows it
  under the name. Empty before then.
- **Inbox state** links each inbox video to every run folder whose run record names it, in either root: `running`
  while one of them is live, `done` once one holds `04_document.md`, `started` when a run holds a phase's output (phase 1's transcript), else `new`: a run reset to 0 is as if it never ran.

## The job queue

- `queue_jobs` appends to `ui/jobs.json` (rewritten whole on each change, under one lock) and wakes the worker thread.
- `run_next_job` starts the oldest queued job only when no other run is going: neither a job of the queue whose process
  is alive, nor any `main.py run` in the process list (a run started on the command line). It waits for the child
  without holding the lock and records the outcome from the exit code: 0 `finished`, 1 `incomplete`, 2 `refused`,
  130 or -2 (the interrupt arrived while the run was still loading) `stopped`, anything else `crashed`.
- A `running` job whose process is gone when the queue is read again (the server was restarted in between) becomes `lost`.
  `cancelled` is a queued job taken off. Interrupt sends the job's process SIGINT, as Ctrl-C would.
- One lane per worker set: a job runs in the lane of its request's set, else its run folder's kept set, else the settings'
  `defaultWorkerSet`. A dispatcher thread starts one waiter thread for each lane that has a job to run and none waiting;
  `run_next_job(queue_dir, lane)` looks only at that lane's jobs. A lane with a live running job starts nothing, a block or
  a retry pause holds its own lane, a run folder in use by a running job of another lane waits, and the pause is global.
- The run settings (one run, a ticked batch, and a resume) hold a Worker set choice, shown only when the settings define a
  set besides the default; the queue row names a job's set and a run's page shows the set it keeps.
- Seams for tests: `set_command_prefix` (a stand-in for `main.py`) and `set_run_lister` (a fixed process list).

## The inbox, uploads, the trash and the archive

- **Every list is newest first, by the date its state means** (`state_at`, in seconds): a new video by when it came
  into the inbox, a running one by when its run's newest console log began, a not finished one by when that log last
  changed, a done one by when its `04_document.md` was written. Run folders no inbox video owns are dated the same way
  and sorted in among the videos of their section. The trash and the archive list the newest move first (`moved_at`).
- **Uploads** are streamed into `inbox/<name>.part` and renamed when complete, so a half-sent file is never listed; a
  name already in the inbox gets a number; a file that is not a video is refused with the suffixes that are.
- **The trash** is `inbox/.trash/`, which the inbox never lists. A video in a meeting folder moves with its folder. Only
  the recording moves: its run folders stay, and the inbox lists them in the section of their state. A name already in the trash, or back in the
  inbox on restore, gets a number. Reading the trash or the archive drops from its `.origins.json` every
  entry no longer there (removed by hand in Finder). The warning before trashing a video that is not done is the page's and the terminal
  UI's (`/trash N` without `--yes`), not the server's.
- **A run folder no inbox video owns** has a checkbox of its own in its section. Several such folders naming one video
  (a run begun again in a new folder) are one row, the run that went furthest (`InboxPanel.pickLeadRuns`), as an inbox
  video's row shows one state for all its runs. A folder made from another run by Branch or Convert (the summary's
  `is_derived`: a `branch.json`, or a profile chosen by the conversion) is a run of its own: never one of its video's
  runs (`run_status_service._list_video_runs`) and never folded into another row, so a blocked branch stands under Not
  finished beside its finished original. Archive or Trash moves it whole into
  `inbox/.archive/` or `inbox/.trash/` (`inbox_move_service.move_run_folder_out`, the request's `run_folders` as
  `<root>/<name>`), its origin recording the folder it came from, so Unarchive and Restore (`move_back`) put it back
  there and not into the inbox. A folder a run is writing into does not move: the run would go on writing at the old
  path. Process stays with the ticked videos only.
- **A started run's pill names the phase it stopped in** (`phase 3 ›`, its title the phase's name): the run summary's
  `stopped_phase`, the first phase of the run's flow not complete, read from the same statuses as the run's own page
  (`run_status_service._decide_phase_statuses`, so the Inbox and "Resume from N" name one phase), set only for a run
  not going and without a document; a video's pill reads its lead run's. A run with a phase recorded blocked or failed
  (`is_blocked`) reads `blocked in phase 3 ›`, in red. The footer's reset opens keeping the phases every ticked run
  holds whole.
- **Reset to phase over the ticked rows**: the footer's reset button opens one dialog, a phase to keep up to and the
  run folders it takes back (each ticked video's run its name opens, and each ticked run folder; a new video has none),
  and Reset sends each one's `POST /api/runs/{root}/{name}/reset` in turn, the detail view's own `reset-to`. A run the
  server refuses (one writing into its folder) is named in the message and the others still go.
- **The archive** is `inbox/.archive/`, for videos done with: moved the same way as to the trash, with no warning,
  and listed in the inbox's Archive section (`run_status_service.list_archive_items`) with the runs whose record names
  the path the video had in the inbox, kept in `.archive/.origins.json`. Those runs leave the other sections. A run of an
  archived video still finds its recording there (`run_artifact_service.find_recording`), so the Recording tab plays it.
  Unarchive puts it back under its inbox name, so its runs name it again. The terminal UI has `/archive N` and
  `/unarchive <name>`.

## Artifacts and the recording

- **Listed groups** (`run_artifact_service.list_artifacts`), in this order: Document (the published `<name>/<name>.md`,
  the final document, or `04_document.md` while nothing is published, then `05_review/review.md`), Draft (`04_writing/draft.md`), Notes (`03_notes.md`,
  `worker-feedback.md`), Frame notes (`03_processed-frames/*.md`), Cuts (`04_writing/images/cuts/*`, the regions phase 4 cut from the recording
  for its figures), Frames (`frames/*.jpg`, each with its second from
  `02_frames.json`), Audio and transcript (`audio.wav`, and under its player the same `TranscriptView` as the
  Recording tab, whose times play the audio). Only `.md`, images and audio are listed or served; every `.json`, the call logs
  and any path outside the folder are refused.
- **Download** of a markdown file that links files inside the run folder (its figures) returns a zip of it and them at
  the paths it uses; any other file downloads as itself.
- **Zips and hiding**: `POST …/zip` returns chosen artifacts as one zip at their paths in the folder
  (`build_files_zip`, images stored uncompressed). `POST …/hidden` hides or shows artifacts in the UI's view for good
  (`hidden_artifact_service`, kept in `ui/hidden-artifacts.json` per `<root>/<name>`); each listed artifact carries
  `is_hidden`, and the files themselves are never touched.
- **A meeting in parts** (an inbox folder of two or more videos, `recording_parts_service`) is one inbox item whose
  `parts` list the files in the order they are read; its run's `recording_parts` give each part's `start_s` in the
  meeting's time, so a frame's second maps to the last part starting at or before it, and the play routes take a
  `part` number.
- **The recording** is not an artifact: it is the video the run record names, shown in its own tab while it is still a
  file there, in the archive, or kept in a done run's `recording/` (this folder's or a sibling run folder's of the same
  video, `run_recording_service`) (`has_recording`); `is_recording_kept` says this folder keeps it, and the page then
  offers Move recording to inbox and shows every action that runs phase 1 or 2 disabled with the reason, and streamed with range requests. Beside the player, the run's `batches` (each batch of
  `03_batch_summaries.json` as its `title`, `start_s`, `end_s` and `summary`) are listed under "Timestamps" by title; a
  click plays the recording from the batch's first second, the batch playing is marked, and the arrow beside a
  timestamp opens its summary. A run before phase 3 has no list. A PDF's tab shows the PDF alone, the page's width:
  its phase 1 text is the Artifacts tab's Text group (`run_artifact_service`'s `text` group, listed for a run with no
  audio, its paragraphs shown by `TranscriptView` with its search). The Artifacts tab's Worker turns lists every worker
  session that ran or runs (each phase whose card has a worker, then each phase 7 part that started, built from the run's
  `phases` and `document_pass_parts` by `RunDetail`), opens the running one first, and shows the chosen session's turns
  and tool calls as the worker's live panel does (`WorkerSessions`, the turn list `WorkerTurns` that `WorkerLive` uses
  too); a part's row asking to open its thinking opens that part's session there. Under the player, `TranscriptView` lists phase 1's
  transcript line by line, the line playing marked and each line's time a button that plays from it. Its search box
  suggests, while typing, the 8 lines that hold every word typed (`transcriptSearch.findTranscriptMatches`: the same
  word, a word it begins, a word holding it, then a word one letter off up to 6 letters or two letters off past that,
  a line holding the words as one phrase first; case and accents never count), the matched words marked; it shows in
  grey the rest of the transcript's most frequent word beginning with the word being typed (`completeWord`), which Tab
  takes; the arrow keys, Enter or a click play from the chosen line and mark it in the list. Its **Download** saves the transcript as text. Speech ids (`S0415`) are not
  shown: they number phase 4's merged speech entries, not the transcript's lines.

## The web page

`web/` is React 19 with Vite and TypeScript, built into `web/dist/`, which `serve` hosts as it is (`npm run build`).

- **Structure** (`src/`): `App.tsx` (top bar, sidebar tabs Inbox | Queue, the open run or job, toasts, list
  refresh every 3 s, following an open run whose folder moved), `api.ts` (types matching the API, `fetch` helpers, the
  upload with progress over `XMLHttpRequest`, time formatting), `useEventStream.ts` and `updateSource.ts` (an update asked
  for once a second with a short request, from the last offset: an open event stream per view took all 6 connections
  Firefox allows one server, and a video then never loaded), `main.tsx` (one reload when a newer build deleted a lazily loaded file,
  with a `replaceReloadPage` seam), `viewState.ts` (the one cookie, `i2k-view`, that keeps where the page was: the
  sidebar tab, the open run or job, the run's tab, the artifact group and file, the open inbox lists; read and saved
  only through `readViewState`, `saveViewValue` and the `usePersistentState` hook), `components/`.
- **Components**: `InboxPanel` (drop zone, two lists To process and Done with select-all, trash; a new or started video's ▶ starts or resumes its run with the phases chosen below, and a done, archived or trashed one has none; a video's name opens its page; each row, a video's or a run folder's, shows its file's own type, video, audio or pdf, in a pill from `file_type`, so a video processed as audio still reads video; the search box's X empties it),   `QueuePanel` (waiting jobs, each named by the run folder it writes, the recording under it when the folder is named otherwise, and what it runs: a conversion's `purpose`, such as "convert to knowledge · phase 4 only (document writing)", else its phase range; then a collapsed Finished jobs list), `ManagerPanel` (the sidebar's Manager tab: only the menu of tools over many runs; the chosen one opens in the detail view, as `{kind: "tool"}`), `RenameView` (the Rename tool in the detail view: the runs with an AI name, each beside the name it is shown under; the ticked ones take their AI name or their file name back), `DownloadView` (the Download tool in the detail view: runs with a document, filtered by their tags (a run shown has every chosen tag), ticked, what of each, one zip), `VideoDetail` (the page of an inbox video no run used yet, opened by a click on its name or its `new ›` pill: Process as (a video only, from the inbox item's `is_audio`: Video,
or Audio only for its sound alone, sent as `is_run_as_audio`), Workflow (a video run only: quality or fast processing,
sent as `workflow`, dropped when the video is set to Audio only; the detail view's header then shows `Workflow: …`), Ingesting profile (an audio run only: an audio file, or a
video set to Audio only; Automatic, or a profile from `GET /api/ingesting-profiles`, sent as `ingesting_profile`, and
dropped when a video goes back to Video), Settings and a Models menu (each phase beside its model in a wider panel, `RunChoices.tsx`), all in the header right of
the title as one-line boxes whose value names what they choose, their names in the tooltip (`.field-select`): what runs
(Process as, Ingesting profile, Phases), a divider, how it runs (Settings, Models), then Start run; its Phases menu holds
only which phases run and Dry run; the tags sit on the path's line, a pencil beside the title that renames the video in place (`POST /api/inbox/rename`; the page follows it to its new
path), a tag row under the title (a "+ tag" pill opening the tag box; each change saved at once by `PUT /api/inbox/tags`
into `ui/inbox-tags.json` by the video's path, listed back as the inbox item's `tags` and moved with a rename, so a
refresh keeps them; sent with the run, so the Phases menu shows no tags here), the phases, Start run and a Recording tab playing it from `/api/inbox/video`; the app opens its run's page in its place once a run of it is listed, and remembers it across a refresh as `{kind: "video"}`), `TagInput` (the box a tag is typed into, in the run's tag row and the run form: it suggests from `GET /api/tags`, prefix matches first, the arrow keys mark one, Enter or Tab takes it, Escape closes), `PartsPlayer` plays an audio part in an `<audio>` element, `RunDetail` (actions chosen by the run's state:
  Control while live, Unblock or Ask blocked frames again, Resume from N, Run again; phase cards with time, `?` help and
  a `⋯` menu, a phase's name shown with spaces (`frames_processing` as "frames processing"); the progress bar of the
  phase card clicked (a finished phase's too; clicked again, back), else of the running phase, else of the first phase
  not complete, a clicked phase with nothing to count saying so: phase 2's stage (Preparing frames: the rules, the classifier,
  then the model, the first not done, with its seconds of video or frames done and waiting and its pace, from
  `logs/<run id>/frames-preparing.json` as `frames_preparing`, `frames_preparing_progress_service.read_progress`),
  phase 3's frames by status, or for an audio recording that
  phase 3 prepares, Preparing the notes with the first step not done and its calls done and waiting (`preparation`,
  `preprocessing_call_service.describe_progress`), or phase 4's stage (Writing the table
  of contents, the chapters, the final summary: `document_stages`, from `04_writing/follow-ups.json` by
  `document_file_service.list_follow_up_steps`) with that stage's steps by status, and no bar for phases 1 and 5;
  a collapsed Blocks card listing every phase 4 or 5 step that blocked or was reset, its counts and each block's time
  and one-line reason (the whole reason on hover): `step_outcomes`, from both conversation logs by
  `run_status_service.list_step_outcomes`, as `i2k blocks` prints them;
  tabs Progress | Artifacts | Recording), `ArtifactsView`, `RecordingView`, `TranscriptView`, `JobConsole`,
  `LogView`, `MarkdownView`, `FigureViewer`, `PhaseSelector`, `Dropdown`/`Menu`, `Dialog`, `Toasts`, `Icon`, and `WorkerView` ("What the worker saw": the button on a phase's card from phase 2 on opens it over the page; the phase's steps on the left, each attempt chosen above, tabs Instructions | Input | Turns (a conversation's follow-up), a refused attempt's findings, a frame's thumbnail enlarged by a click, and Preview instructions).
- **Markdown** (`MarkdownView`): the front matter becomes a table; the body goes through `marked`, then `DOMPurify`,
  then `prepareHtml` (heading anchors as GitHub writes them, relative images and links pointed at the run folder);
  after it is in the page, Mermaid fences are drawn and other code fences coloured with highlight.js, both loaded only
  when a document needs them. Diagrams and figures open in `FigureViewer` (pan, zoom about the pointer, pinch, Fit,
  export as SVG, PNG or `.mmd`). **Full screen** opens the document over the window; when it has a contents list (the list right
  after a heading named "Contents" or "Table of contents"), that list is pinned in a column on the left and left out of
  the text, and a click on it scrolls the text to the heading (on a narrow screen it sits above the text).
- **Editing a document** (`MarkdownView`, the wiki skill's model): full screen offers **Edit**, which opens the lock;
  the document stays drawn and the block the pointer lands on shows its own markdown in a textarea, so an edited
  mermaid fence redraws the moment the caret leaves it. Every top-level block carries the line it started on and the
  markdown it was made from (`data-line`, `data-src`), and **Save** reassembles the file from them, front matter and
  all, through `PUT …/files/{path}`. The front matter is no block of the text, so the lock replaces its table
  with a textarea of its own, kept line for line. `Ctrl`/`Cmd`+`Z` puts the last 100 committed blocks back one at a
  time, `Source` becomes a textarea over the whole file, and **Versions** lists what the folder kept and loads one
  into the editor, which a Save then puts back. Leaving full screen locks the editor and puts the file on disk back
  in the page, asking first when changes were not saved, so a document is never left editable outside full screen.
  While the lock is open the contents list is not pinned: what is pinned beside the text is not in the text a save
  would write.
- **Instructions** (`InstructionsView`, `instructionsApi.ts`): the settings gear opens two
  pages, Settings and Instructions (`App.tsx`, kept in the cookie as `settingsPage`). Instructions shows, on the left,
  a workflow (Video, Audio, PDF), a profile or mode to show it for, a search, then each phase's steps, each step a list
  of the files its worker is built from with a badge (default, edited, default changed, not saved). On the right, the
  open file: who gets it, which workflows send it, and five tabs: Read (rendered through `marked`, `DOMPurify` and
  `markdownEnhance.ts`, which `MarkdownView` uses too; `{{SHARED:x}}` is a chip that opens the guideline, the markers the
  program fills are chips), Edit (the markdown beside how it reads, a note, Save, Discard changes, Reset to default),
  Compare (any two of the default, the text in force, the draft, the default the edit was made over and every version,
  side by side from 1100 px and one column below, changed words marked by `diff`'s `diffWordsWithSpace`, unchanged runs
  folded), History (every version, Restore loading it into the editor as a restore) and Full prompt (the whole system
  prompt of the step, with the draft when there is one). Typed text stays per file while another file is open. Under
  900 px the tree and the open file take turns, with Every step to go back.
- **Dropdowns** are drawn in a portal on `document.body`, placed under their button (or above it near the bottom of the
  window), so a scrolling list never cuts them off.
- **Styles** (`styles.css`) are colour tokens on `:root` with a dark set under `prefers-color-scheme`, one column below
  900 px wide (with a back arrow), its rows held at the top, and phone rules last so they win. Under 640 px a
  finished run's actions and a new video's Start run take their own lines under the title, a new video's choices stand
  each beside its label, and a menu is at least 320 px or the screen less 24; `e2e/small-screen.spec.ts` checks each
  view at 320 x 700 for a page wider than the screen. The page's HTML is sent `no-cache`, so a phone gets each new
  build.

## The terminal UI

`tui/console_tui_runner.py` is one Textual `App`. Its layout is built in `on_mount` (a method named `compose` would fail
the verb-name test). Every button builds the same `SlashCommandRequest` a typed line would, and one `_run_<name>`
handler per command carries it out, so mouse and keyboard cannot drift apart. The lists refresh every 3 s and the open
run every second, over `ui_api_client`. The slash commands are listed in `utils/slash_command_utils.SLASH_COMMANDS`.

## Tests

- **Python** (in the offline suite): `tests/cases/test_ui_services.py` (queue, status, timing, running phase, inbox state,
  uploads, trash, artifacts, downloads, saving a markdown artifact and its kept versions, the recording, the API
  through FastAPI's `TestClient`) and
  `tests/cases/test_console_tui.py` (the terminal UI driven headless, typing and clicking, against the API in process).
- **Web**: `cd web && npm run coverage` runs Vitest with Testing Library in jsdom (`vitest.config.ts`), and fails when
  lines, statements, functions or branches drop below 85%. `src/test/fakeServer.ts` fakes `fetch`, the update source (`setUpdateSourceOpener`) and
  `XMLHttpRequest` with the API's JSON shapes and invented names; `src/test/setup.ts` supplies the browser parts jsdom
  lacks. Mermaid and highlight.js are mocked.
