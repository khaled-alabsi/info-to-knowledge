# Architecture

This page tells how info-to-knowledge is built: the idea, the seven phases of a run, how a model's answer is
checked, how settings are layered and where the code lives. [FLOW.md](FLOW.md) lists what every phase reads and
writes, file by file.

## The idea

One recording goes in and one reference document comes out. A person who did not attend can read it: it opens with an
executive summary and an agenda, its chapters follow the recording's own parts in the recording's order, and each
statement names the parts of the recording it rests on.

Three rules shape the design:

1. **Plain code does what code can do.** Transcription, frame capture, cropping, merging, counting and publishing are
   programs. A model is asked only for what needs reading and writing: a frame's notes, a chapter's text, a review.
2. **Every phase writes its own files.** A phase reads the files of the phases before it. Any phase can run again on
   its own, a stopped run continues with `--resume`, and a run folder can be taken back to an earlier phase.
3. **Checks decide from files, not from what a model says.** After each answer a validator reads what was written and
   returns findings. A rejected attempt shows the model the findings and asks again. A step that spends all its
   attempts blocks, and a blocked step stops the phase: nothing after it runs over a hole.

## The phases

| Phase | Name | What happens | Model call? |
|---|---|---|---|
| 1 | Transcribe | Speech recognition (faster-whisper or MLX) turns the audio into timed segments. Optional speaker recognition adds who spoke. For a PDF or an office file, the page text is read instead. | no |
| 2 | Frames preparing | The program decodes the video once, takes a frame whenever the shared screen changed and held still, crops it to the shared screen, and drops frames that carry no knowledge (people galleries, blank screens, repeats). For an audio recording one call turns the transcript into clean paragraphs. | none for a video |
| 3 | Frames processing | A model writes each frame's notes: the outline of the screen, its facts, the quoted text, and how the speech and the screen fit together. Frames go in batches, each told what earlier frames established. A critique, a repair, the open questions and the answers follow each batch. | yes |
| 4 | Document writing | One conversation with a model that has reading tools writes the document in parts: an executive summary draft, the agenda, each chapter's headings, a table of contents, each chapter's text with its frames placed, then the final title and summary. The program assembles and publishes the document folder. | yes |
| 5 | Review | A second worker, who did not write the document, reads it as a critic, lists what a reader still cannot know, and resolves each point from the transcript, the notes and the frames. | yes |
| 6 | Critique collection | The program groups the rules that each batch's critique found, and one call words each group as a rule for any recording. | yes |
| 7 | Document reshaping | Optional. A clean up, a rewrite or a knowledge pass over the published document, part by part. | yes |

A recording with no frames (audio, or a PDF read as text only) takes the same phases with the frame steps left out.
The profile of an audio recording (meeting, conversation, language lesson, sprint planning, presentation, knowledge)
decides the shape of the document.

The run folder is the `--output` you give. It holds one numbered file or folder per phase (`01_transcript.json`,
`02_frames.json`, `03_processed-frames/`, `04_document.md`, `05_review/`, `06_critique-collection/`), the cut
`frames/`, the per-attempt messages and call logs, and the published folder named after the recording.

## How a worker's answer is checked

1. The program builds the call's instructions from the text files in `agent-context/` and the call's data.
2. The model answers, through a tool that names exactly what it refers to (a frame, a note, a line by its id).
3. A validator reads the answer and the files it touches. It never calls a model and never writes a file.
4. A rejection goes back to the model with the findings. The attempt count and the rule that refused are logged.
5. An answer that passes is saved in the same operation that accepts it. A step whose attempts are spent blocks.

A conversation that grows past the model's window is compacted: the model writes a summary of its own work, and the
conversation restarts from the starting messages, that summary and the last turns. The program never drops earlier
messages on its own.

Every model call goes through one module, so every call is counted and logged in the run folder.

## Settings

`playbook.default.json` holds the default settings and is kept in git. `playbook.local.json` (git-ignored) is read on
top of it, and a named profile (`--profile <name>`, file `playbook.<name>.json`) is read on top of both. An unknown
key is an error, and a key that starts with `_` is a note. Shipped profiles:

| Profile | What it changes |
|---|---|
| `container` | Linux settings: faster-whisper and Tesseract in place of the Apple tools, model hosts reached through `host.containers.internal` |
| `strict` | zero repair of a frame, and a refused frame tool stops the run |

Provider sets name other model hosts. Jobs on different sets run at the same time, jobs on one set one after another.

## The server, the queue and the pages

`i2k serve` starts one server and one queue. The web page and the terminal UI add jobs to the queue. The queue runs
one job at a time per provider set, each as its own `i2k run` process, so closing a page or the server leaves a running
job going. A restarted server finds a job by its process id and marks it `lost` when the process ended with no exit
code to read. Progress is read from the run folder, so a run started by hand shows up the same way.

Accounts are off until the first one exists. Then the page asks to sign in. A `viewer` reads, an `operator` also runs,
resets and edits, and an `admin` also changes the settings and the accounts.

## Folders

| Folder or file | What it holds |
|---|---|
| `main.py` | Starts the command from a checkout. |
| `pyproject.toml` | The package, its dependencies and the `i2k` command. |
| `src/` | The Python code, in layers (below). |
| `agent-context/` | What each worker is told, as text and data files, one folder per kind of instruction. |
| `web/` | The web page: a React app built with Vite, with its unit tests and browser tests. |
| `tests/` | `cases/` (offline unit tests), `support/`, `resources/` (rendered boards, PDFs and public-domain faces), `integration/` and `run_tests.py`. |
| `weights/`, `frame-classifier/` | The face and person detectors (OpenCV YuNet and NanoDet, shipped in the image) and the frame classifier phase 2 runs. |
| `word-lists/` | A German word list the outline check counts as readable. |
| `tools/` | Helper programs with their own environments: Apple's text reader, a Pandoc filter, a scanned-page reader and a decision model for frames. |
| `docs/` | This page, the flow, and the usage guides. |
| `inbox/`, `output/`, `ui/`, `models/` | Your data: recordings, run folders, the queue and downloaded models. All git-ignored. |

The code in `src/` is split by role:

| Layer | Role |
|---|---|
| `cli/` | The commands (`run`, `inbox`, `serve`, `reset-to`, `blocks`, ...). |
| `graph/` | The LangGraph graph of a run: assembly, routing and one node per phase step. |
| `chains/`, `prompts/`, `schemas/`, `validators/` | The model calls, what they are told, the shape of their answers and the checks on them. |
| `services/` | The work itself: capture, cropping, text reading, merging, publishing, the queue and the web API. |
| `agent_client/` | The agent a conversation runs on: the model's reading tools, the folders they may read, and how a reply ended. |
| `models/`, `utils/` | Data carriers and small helpers with no input or output of their own. |
| `tui/` | The terminal UI. |

A test in `tests/cases/` checks names, nesting and layout of every module, so a misplaced file fails the suite.

## Where the model hosts are

The program reaches a language model by URL: LM Studio, Ollama, llama.cpp or any OpenAI-style server. Each role
(`frame`, `document`, `facts`, ...) names its model and host in the settings. Run `i2k probe` to see which hosts
answer and which of vision, tools and thinking each model offers, and `i2k models` to see which model each phase uses.

Four kinds of model are standard and can be served by any OpenAI-compatible provider: chat, speech, speaker turns
(diarization) and image embeddings, each set under `providers` in the settings. Tesseract (text on frames) and the two
OpenCV detectors for faces and people run inside the program, on the processor, with no URL. The decision model d1-2B
runs inside the program by default, and `providers.decision.url` may point at `i2k serve-models`, a small server that
runs it natively on the host. [Models](models.md) lists each kind, the providers that answer it, and the settings.
