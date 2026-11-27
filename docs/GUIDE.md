# info-to-knowledge: the full guide

Turns one video file into a knowledge document, in six numbered phases. Two of them are plain code
and four call a model, and every phase writes its own numbered file, so any phase can be re-run
on its own against results produced hours earlier. Phase 5 writes the folder a reader opens,
named after the meeting: the document with its screenshots and its provenance.
Phase 5 reviews that published document as a critic and fixes what the recording can answer.

It runs against a local model host. Nothing is sent anywhere.

## Install

On a new machine, one script does every step below that it can, and downloads the recommended models (the Qwen MTP
model `frame` and `document` use, and LFM2.5-VL-3B for the redundancy filter) into `models/` (git-ignored), linked
into LM Studio:

```bash
cd info-to-knowledge
./setup.sh hf_xxx          # a Hugging Face token; kept in .env (git-ignored); HF_TOKEN when not given
i2k setup-models           # the models alone, again: a file already there is not downloaded twice
```

While `pdf.ocrEngine` is `unlimited-ocr` (both default settings files), `i2k setup-models` and the setup wizard's
Download also build the scanned-page reader Unlimited-OCR-2 in `tools/unlimited-ocr/.venv` and download its model
(3.9 GB, into the Hugging Face cache, too large for git); Apple silicon only. By hand: `tools/unlimited-ocr/setup.sh
--download`. A run picks the engine for its scanned pages with `--pdf-ocr-engine tesseract|unlimited-ocr` (the run
form's Scans); the fast profile reads them with Tesseract. A video run picks the model that drops its frames with no
knowledge with `--knowledge-filter-model google-SigLIP-2|liquidai-d1-2B` (the run form's Knowledge filter;
`frames.knowledgeFilterModel`, `google-SigLIP-2` by default); `liquidai-d1-2B` needs its tool, built by
`tools/d1-knowledge-filter/setup.sh --download`.

It needs Python 3.12, ffmpeg, Node.js and LM Studio installed; it says which is missing. By hand:

```bash
cd info-to-knowledge
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ln -s "$PWD/i2k" ~/.local/bin/i2k      # optional: the `i2k` command, from anywhere
```

`ffmpeg` and `ffprobe` must be on the `PATH`. The first run downloads the faster-whisper model.

Speaker diarization (who spoke when, `audio.diarization.isOn`) is optional and runs on macOS, Linux and Windows:

```bash
.venv/bin/python -m pip install -r requirements-diarization.txt
echo "HF_TOKEN=hf_..." > .env     # git-ignored; `i2k` exports it, needed only for the model's first download
```

Accept the terms of `pyannote/speaker-diarization-community-1` once on huggingface.co with the account the token
belongs to. After the first download the model is read from the Hugging Face cache, offline; usage reporting to
pyannote's server is switched off. Without the package or the model, phase 1 writes the transcript with no speakers
and says why on the console.

To serve the `frame` and `document` models with `mlx_vlm.server` on an Apple silicon Mac:

```bash
i2k setup-mlx                                        # mlx-vlm only
i2k setup-mlx unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit    # and download that model
```

It uses mlx-vlm wherever it is already installed (the Python running `i2k` first, then every `python3` found).
Otherwise it reads from PyPI which Python versions fit (mlx-vlm's minimum, and an mlx build for this macOS), creates
the venv `~/.mlx-vlm/venv` (`--venv` for another place) from the newest such Python on the machine and installs
mlx-vlm with its dependencies into it. With no such Python it offers to install one with Homebrew, or says what to
install and stops. A named model is downloaded into the Hugging Face cache unless it is there, and the line to start
the server is printed.

`i2k` is a wrapper around `main.py` that uses this project's own venv, so it needs no install and
works from any directory. Everything below is spelled `i2k`; `.venv/bin/python main.py` is the same
command and takes the same arguments.

Check the host and the models before a long run:

```bash
i2k probe     # are the configured hosts reachable
i2k models    # which configured model each phase calls, and every configured model
```

`probe` reports each model's `vision`, `tools` and `thinking` capabilities. A `frame` model without
`vision` cannot do phase 3, and a model without `tools` cannot return structured output at all.

Give a run tags, the same ones the detail view shows and changes:

```bash
i2k tags --output output/meeting --add "client Acme" demo   # adds them, prints every tag
i2k tags --output output/meeting --remove demo              # takes one off
i2k tags --output output/meeting --set                      # takes them all off
```

A tag naming an ingesting profile, or one of its aliases, picks the shape the document is written in: `knowledge` (the
default reference document), `presentation`, `conversation` ("chat"), `meeting`, `language-lesson` ("learn German") or
`sprint-change` or `sprint-planning`. For an audio recording, `sprint-planning`, `sprint-change` and `language-lesson`
(and `meeting` and `presentation` from 60 paragraphs) prepare the notes in phase 3 first: the items, then each item in
detail, one call each, kept in `03_prepared/`; the steps are `agent-context/preprocessing/<profile>.json`. Tags given
on the run line are written before phase 1, where they still decide it:

```bash
i2k run --video ~/Downloads/lesson.mp3 --output output/lesson --tag "learn German" --tag week-3
```

Write a finished run's document again in another profile, as a new run folder beside it (`<run>-<profile>/`); the run
is only read:

```bash
i2k convert --output output/meeting --to presentation   # copies phases 1 to 3, prints the run line for phase 4
                                                           # (1 and 2, and --from 3, when phase 3 prepares again)
```

Take a run folder back to an earlier phase, to run the later ones again:

```bash
i2k reset-to 4 --output output/meeting          # lists what it would remove
i2k reset-to 4 --output output/meeting --yes    # removes it
```

`reset-to N` (0 to 6) removes every file and every progress record of the phases after N (their phase statuses,
attempts and frame statuses in `logs/*/run.json`), as if they never ran. The phases up to N and the
logs stay. It refuses while a run process writes into the folder. `reset-to 5` puts back the published document
from `05_review/before.md`.

`reset-chapter N --output <run folder>` takes back only part of phase 4: chapter N and every later chapter (their draft
headings, texts, requests, call logs and sections) and the final title and summary, so the next `--resume` writes them
again from the notes as they are now. Chapters 1 to N-1, the agenda and the summary draft stay. Use it when phase 3
filled in frames after phase 4 had written their chapter. `--text-only` keeps the draft headings of chapter N and
after and removes only their texts, sections and the final title and summary, for a text written wrong from right
headings. Like `reset-to`, it only lists without `--yes` and refuses
under a live run.

`refine-frames --output <run folder>` runs the frame knowledge classifier on the frames phase 3 has not read and lists
those it would drop, with their drop scores; `--yes` drops them all. `drop-frames frame-003 frame-007 --output <run
folder> --yes` drops the frames named. A dropped frame moves into `frames/dropped/`, leaves `02_frames.json`'s list and
every run record, and its speech goes to the frame before it, so the next `--resume` never reads it; a frame phase 3
already read is refused. In the detail view the same is the Frames bar's Refine and Drop selected.

`blocks --output <run folder>` prints how often each step of phases 4 and 5 blocked, was accepted and was reset, and
each block's time and reason in one line (`rejected: DOCUMENT_ENTRY_UNCOVERED ×2`), read from the two conversation
logs; the detail view shows the same in its Blocks card. A block logged before log rows carried a time shows `no time`.

## Run

```bash
i2k run --video <path-to-video> --output <run-folder>
```

`--video` may also be a folder of two or more videos: the parts of one meeting, read in natural name order as one video
(one transcript, frame numbers running on across the parts, one document).

`--video` may also be an audio file (`.mp3`, `.m4a`, `.wav`, `.aac`, `.flac`, `.ogg`, `.opus`) or a folder of audio parts.
It has no frames: phase 2 is one model call that turns the transcript into paragraphs of clean speech, one speaker's
turn on one point each, in `02_speech.md`; phase 3 has nothing to read, and phase 4 writes the document from the
paragraphs. `--tag` gives the run tags before phase 1. `--ingesting-profile NAME` picks the document's shape by hand
(`meeting`, `conversation`, ...), over any tag and phase 2's own pick. `--as-audio` processes a video as an audio
recording: its sound is transcribed, no frame is taken, and it runs the audio workflow; the choice is kept in the run
folder, so a resume needs no flag. `--workflow fast-processing` runs a video's phases 3 and 4 fast: each frame described
from its image alone, five frames to a session, and one call writing the document, with no check and no review;
`--workflow quality` (the default) runs them as above; kept in the run folder too. `--ingesting-profile takeaway` is the fast profile: one call after the transcript lists only
what the recording settled and what it left open (a video given it runs on its sound alone).

`--output` is the run folder **itself**, not a parent it derives a subfolder from. Give each video
its own:

```bash
i2k run \
    --video ~/Downloads/meeting.mp4 \
    --output output/meeting
```

Everything the run produces or reads lands in that one folder: the six phase files, `audio.wav`,
the cut `frames/`, one markdown file per frame's notes under `03_processed-frames/`, and the per-attempt messages and
call logs under `calls/` and `logs/`. The instructions are not copied in: they stay in the
playbook's own `agent-context/`, and each prompt is assembled from there when the call is made.

### The inbox

Drop recordings into `inbox/` and run the batch instead of typing a path per video. A recording may also sit in
its own folder, with the meeting tool's own transcript (`.vtt` or `.srt`) beside it: phase 1 still transcribes, and
records the provided transcript's agreement and speaker names (REQUIREMENTS.md R14).

```bash
i2k inbox --list     # what is new, and what would be skipped
i2k inbox            # run every new one, to the end
```

`inbox/` and `output/` sit in the project's root folder and are git-ignored: recordings and finished documents are
your data. `I2K_WORKSPACE` puts them in another folder. Each inbox video runs into `output/<its-stem>/`, the same
folder a hand-typed run line names.

```
inbox/                drop recordings here
output/               one run folder per video, from `i2k inbox`, the web page and hand-typed run lines
models/               the models `i2k setup-models` downloads
ui/                   the web page's job queue and each job's console
```

A video is **new** until `output/<its-stem>/04_document.md` exists. Nothing keeps a list of what
has been seen: the document is the record, so deleting a folder under `output/` is how you ask for
that video again, and moving a file out of `inbox/` is how you skip it. A run that stopped partway
left no document, so the item stays new and the next pass reads the phase files already on disk
instead of re-doing them.

One failed video does not stop the batch. Each one is reported by name and the next starts; the exit
code is non-zero when any of them failed.

`--inbox` and `--output` override the two folders, and the command prints both before it starts.
Note that `inbox`'s `--output` is a **root** it creates one folder per video under, where `run`'s
`--output` is the single run folder itself.

### Running part of the pipeline

| Flag | What it does |
|---|---|
| `--phase N` | run exactly phase N and stop |
| `--from N` | run phase N and every phase after it |
| `--to N` | stop after phase N |
| `--worker N` | re-run the model call for frame N only, rewriting its one entry |
| `--dry-run` | render what every call would be sent, and call no model |
| `--resume` | continue the newest run of this output folder instead of starting a new one |

`--resume` continues the newest run of the folder, and when that folder has no run record (after `reset-to N`, or
when its logs were moved) it reads the phases the folder already holds off their files and runs the first one it
does not. It never re-runs a phase whose output is there, and it never compares the models or the instructions with
what the run started under: an instruction edited mid-run applies to whatever is asked after it.

A phase that was not asked for is **read from its file** instead of run, and a missing input file is
refused by name rather than silently recomputed. So `--phase 3` never touches the audio, and
`--phase 2` refuses if `01_transcript.json` is not there.

```bash
# transcribe and cut frames, then stop and look at them
i2k run --video meeting.mp4 --output output/meeting --from 1 --to 2

# see exactly what frame 0 would be sent, without calling anything
i2k run --video meeting.mp4 --output output/meeting --dry-run

# one frame came back wrong: redo that one
i2k run --video meeting.mp4 --output output/meeting --worker 7

# the frames are fine, rewrite the document
i2k run --video meeting.mp4 --output output/meeting --from 4
```

`--worker N` takes a frame number, not a phase, and combining it with `--phase` is refused.

### Watching a run

Each run writes its log to `logs/<run id>/HH-MM.log` inside the run folder, and every model call's request and answer
beside it. Follow the newest log while the run works:

```bash
tail -f output/meeting/logs/*/*.log
```

### The web page and the terminal UI

Both do what the commands above do, without typing a run line: pick inbox videos (some or all), resume a run, run it
again from a phase or one phase alone, pause, drain or stop it, take it back with `reset-to` or `reset-chapter`, and
follow its phases, frames and log live.

```bash
cd web && npm install && npm run build && cd ..   # once, and after a change to web/
i2k serve                                        # the page at http://127.0.0.1:8765, and from the same network at
                                                 # http://<this machine's address>:8765 (--host 127.0.0.1: this machine only)
i2k tui                                          # the terminal UI; starts a server when none answers
i2k accounts add Ada --role admin                # the first account turns sign-in on (roles: viewer, operator, admin)
i2k login Ada                                    # the command line's clients (mcp, run --queue, tui) sign in
```

- **Accounts are off until the first one exists.** With none, the page and the API open to anyone who reaches the
  port, as before. Once one exists, the page asks to sign in; a `viewer` reads, an `operator` also runs, resets and
  edits, an `admin` also changes the settings, the setup and the accounts (the Manager's Accounts tool).

- **One server, one queue.** `serve` holds the queue both UIs add to and runs one job at a time, each as its own
  `i2k run` process. Closing the page, the terminal UI or the server leaves a running job going; a server started
  again finds it by its process id, and marks it `lost` once that process is gone with no exit code to read.
- **Dropping a recording on the page uploads it into `inbox/`.** A name already there gets a number
  (`meeting-2.mp4`); a file that is not a video is refused by name.
- **What a job ends as comes from `run`'s exit code**: `finished` 0, `incomplete` 1 (a phase failed or something is
  blocked), `refused` 2 (the run line was refused; the job's console says why), `stopped` 130.
- **Progress is read from the run folder**, so a run started by hand shows up the same way. The queue is
  `ui/jobs.json`, and everything a job's process printed is `ui/jobs/<job id>.log`.
- **The terminal UI** takes clicks and slash commands (`/help` lists them): `/process all`, `/process 1 3 --from 2`,
  `/open <run folder>`, `/resume`, `/from 4`, `/phase 3`, `/worker 12`, `/pause`, `/drain`, `/stop`, `/continue`,
  `/interrupt`, `/reset-to 3 [--yes]`, `/reset-chapter 2 [--yes]`, `/queue`, `/cancel <job id>`, `/models`, `/probe`.
- **Hosting.** `serve` listens on the network by default (`--host 0.0.0.0`), so a phone on the same Wi-Fi opens the
  page; `--host 127.0.0.1` keeps it to this machine. With `--sign-in off` it prints a warning line: anyone who reaches
  the port can upload, run and reset.
- `npm run dev` in `web/` serves the page with live reload on port 5173 and sends `/api` to the server on 8765.
- `cd web && npm run coverage` runs the page's tests, offline with the server faked, and fails below 85% coverage on any
  measure; the Python suite does not run them.

## The five phases

| # | Phase | Writes | Model? |
|---|---|---|---|
| 1 | `transcribe` | `01_transcript.json` | no |
| 2 | `frames_preparing` | `02_frames.json` | no |
| 3 | `frames_processing` | each frame's notes inside its item of `02_frames.json`, `03_processed-frames/<slug>.md` | one call per frame |
| 4 | `finalize` | `04_document.md`, `04_document.provenance.json` | the default, `document.writeMode` "conversation": one conversation of follow-ups: an executive summary draft, the agenda, then a fresh start: one draft-headings request per chapter, ONE table of contents that scaffolds the document, one request per chapter that writes its sections and places its frames, the final summary and title; "calls": a parts call, an outline call per batch, a title call, one per topic part, a summary call |
| 5 | `review` | `05_review/review.md`, and the published document edited in place | an agent with reading tools, one conversation: the critique, then one follow-up per todo |

**Phase 1 `transcribe`.** faster-whisper over the extracted `audio.wav`, producing timed segments
with word times; with `audio.diarization.isOn`, pyannote then puts a speaker (`Speaker A` …) on every word and
segment. The language is detected, not forced, and both the detected language and its
probability are written into the file. A failure here stops the run, because everything later rests
on it.

**Phase 2 `frames_preparing`.** Decodes the video once, decides which timestamps to keep, writes a JPEG per
kept frame, and attaches each frame's `transcriptBefore` and `transcriptCurrent` from phase 1. The
resulting list is frozen into the store, and it is what the loop iterates.

**Phase 3 `frames_processing`.** One model call per frame, one frame per invocation, so the graph's own cycle is
the loop. Frames are **sequential**: each message carries the accepted records of the frames before
it, read back from each item of `02_frames.json`, so a frame is told what has already been established
without any agent keeping a context open. A frame that exhausts its retry budget is marked `blocked`, and the
run STOPS there (the owner, 2026-09-28): phases 4, 5 and 6 are all built on the frames' notes, so none of them runs
over a frame that has none. The console says which frame blocked and why; fix the cause and `--resume`, which
re-asks only the frames that have no notes. Every rejected attempt prints its checks, so a retry is never silent. Each record carries `tags`, its one
label: the kinds of knowledge it holds (concept, procedure, reference, problem, risk, decision, status) and the
subjects it names (systems, tools, components, standards, terms), three to ten, at most 12 kept. Each call is shown
the subject tags so far, so a frame reuses a name instead of inventing one.

**The merge.** The merge of the transcript and the accepted frame records into one entry list (layer 2) runs in
memory whenever phase 4, 5 or 6 needs it, in plain code, in a few hundredths of a second. Merging, de-duplicating and
counting are arithmetic, and a model asked to do them is right most of the time, which is the worst available
outcome. The phases were renumbered 1 to 6 on 2026-09-28 and to 1 to 5 on 2026-09-29, when the illustrate phase was removed after the merge phase folded into the writing phase.

**Phase 4 `finalize`.** Never one call, and written in parts so a meeting of any length fits a local
model's context. It is written from the frames' knowledge, diagrams, open questions, web addresses,
action items and questions, plus the speech itself: the lines one frame heard are ONE speech entry under that
frame, and each batch no frame read is one entry, under its first line's id (`S0415`) with every line listed.
So what was said and never noted is covered like any other entry, with no separate step (R8). First one call divides the meeting into
its own parts, which become the chapters, in the meeting's order (R9). One outline
call per batch of a part names where each topic starts, and the program fills in its entries, reusing the titles earlier batches made;
a short return to an earlier subject is folded back where it was first discussed. One call writes the
document's title, one writes each topic part's section (and names the decisions and action items it states),
and one, last, writes the executive summary and the agenda (R11). On this calls path (`document.writeMode: "calls"`)
each goes through the attempt loop with its own retry budget and its own validator. The default,
`document.writeMode: "conversation"` (R12), is one session, one step, one worker: the same pieces are follow-ups of
ONE conversation with an agent that has reading tools. It scaffolds the document, then fills it: an executive summary
draft; the agenda, the meeting's own points, drafted from the moments and phase 3's batch accounts without their
frame and time ranges; then the conversation starts afresh from its starting messages; one draft-headings request per chapter, in
which the agent reads that chapter's part of the meeting notes chunk by chunk (`read_notes(chapter, chunk)`; the
program refuses the answer until every chunk was read) and names the chapter's purpose and its headings with where
each starts; ONE table of contents that writes the whole scaffold, one chapter per agenda point with its headings,
each named by the entry it starts at; one request per chapter that writes every heading's section of it and may place
frames with phase 5's look, cut, mark and mask tools; and the final title, summary and agenda. The program
orders every chapter and heading by where it starts, files a heading listed under the wrong chapter by its start, and
fills in each heading's entries; the table-of-contents file keeps only the agent's answer. `04_writing/draft.md` shows
the plan, the draft headings, then every heading with a placeholder until its chapter fills it, and every request sent
is saved in `04_writing/requests/<slug>.md`. A transcript line id the agent names is filed under the speech entry that
holds it, and a section need not cite speech a frame already heard. No separate call runs; each answer comes through a submit tool and is judged by the validator its call would have
had. The table of contents aims at about one heading per three to five minutes of meeting, with no cap on a heading's
entries: the program splits a heading's entries within a chapter into even consecutive section calls of at most
`document.sectionEntries` under the one heading. The program assembles the document, which closes with the decisions,
the action items and the open points, and writes the provenance file beside it with a coverage table of every
entry. A blocked part keeps its entries under "Not written up" instead of stopping the phase.

**Publishing (the end of phase 4).** No model. The program keeps the frames phase 4's chapters placed (from
`04_writing/images.json`), writes the folder named after the meeting, and checks it.

**Phase 5 `review`.** A worker that did not write the document reads the published one as a critic (R16) and
lists what a reader still cannot know: gaps, open questions, contradictions, unsupported claims. Then, in the same
conversation, it resolves each one from the transcript, the frame notes and the frames, or says it cannot. The
program checks every answer, makes each edit in the published document, adds what stayed open to its open points,
ends it with one line linking `review.md`, and keeps the document as it was in `05_review/before.md`. Every
critique stays on disk, each point with the step of the flow that lost it (`lost_at`), to improve the instructions
from. It is held (status `held`, never `complete`) while any earlier phase is recorded blocked or failed, or any
frame is blocked in phase 3: a review of a document with a known hole makes no sense. `--to 5` skips it;
`--phase 5` runs it alone on a finished run. It uses the `facts` role.

## Configuration

Defaults live in `src/config.py`. `playbook.default.json` beside `main.py` is read on top of them, kept in
git, and `playbook.local.json` on top of that, which is **not** committed. Keys are camelCase, and an unknown key is
an error rather than a silent no-op; a key starting with `_` is your own note, at any depth.

`--profile <name>`, accepted on every command, reads `playbook.<name>.json` on top of both, so a named profile wins
over this machine's file. Two are shipped, each used only when asked for: `playbook.strict.json`, zero repair of a
frame, and a refused frame tool stops the run; `playbook.fast.json`, a quicker run with no critique after each batch of
frames, no fix from it, no review (phase 5), no phase 6 and no feedback turn after a rejected answer. A run's log names its repair values in the header's `repair:` line. `I2K_PROFILE=<name>` sets it for a whole shell, and
`./i2k serve --profile <name>` writes the flag into every queued job's run line. The names V2K_WORKSPACE, V2K_PROFILE, V2K_SIGN_IN, V2K_TOKEN and V2K_SERVER_URL of earlier versions are still read when the I2K_ one is not set.

```json
{
  "frames": { "mode": "scene", "sceneThreshold": 0.25, "maxCount": 120 },
  "audio": { "whisperModel": "large", "language": "de" },
  "prompt": {
    "userPrompt": "This is a recorded architecture workshop. Name the systems and the decisions.",
    "transcriptBeforeSeconds": 90,
    "previousFramesLimit": 8
  },
  "models": {
    "frame": { "spec": "ollama:qwen3-vl:8b", "numPredict": 4096, "reasoning": true }
  }
}
```

The same values are settable from the environment with the `PB_` prefix and `__` between levels, for
example `PB_FRAMES__MODE=scene`, but only where no settings file sets them: a value in a file wins over the
environment.

### The settings worth knowing

| Key | Default | What it changes |
|---|---|---|
| `frames.mode` | `interval` | `interval` keeps one frame every `60 / perMinute` seconds; `scene` keeps one per scene; `change` keeps the shared screen whenever it changed, see below |
| `frames.perMinute` | `5` | `interval` only |
| `frames.sceneThreshold` | `0.3` | `scene` only: the mean absolute grayscale difference at which a new scene starts |
| `frames.maxCount` | `60` | the hard cap on frames, in every mode |
| `frames.maxWidth` | `1280` | a written frame wider than this is scaled down to it; `0` keeps the recorded size |
| `frames.changeToCapture` | `0.05` | `change` only: the share of the screen's 32 x 18 blocks that must differ from the last frame taken |
| `frames.stillSeconds` | `1` | `change` only: how long the screen must then hold still before it is taken |
| `frames.busySeconds` | `10` | `change` only: a screen that never holds still is taken anyway after this long |
| `frames.boxEverySeconds` | `5` | `change` only: how often the shared screen's box is looked for again |
| `frames.passThroughSeconds` | `5` | `change` only: a repeat of an earlier screen shown under this many seconds is not taken (a pass-through); 0 keeps every repeat |
| `frames.buildStepChange` | `0.15` | `change` only: a frame that changed at most this share from the frame just before it is a step of one slide built up step by step, kept in one batch; 0 marks none |
| `frames.faceModel` | `weights/face_detection_yunet_2023mar.onnx` | `change` only: OpenCV's YuNet face detector, run locally |
| `frames.personModel` | `weights/object_detection_nanodet_2022nov.onnx` | `change` only: OpenCV's NanoDet-Plus detector, its person class, run locally; the blocks a person covers are left out of the capture loop's change |
| `audio.language` | `null` | `null` detects it. Set it only for audio the detector gets wrong |
| `audio.device` | `cpu` | `mps` is refused by name: CTranslate2 has no Metal backend |
| `prompt.userPrompt` | `""` | what you want to know, added to every frame's message |
| `prompt.transcriptBeforeSeconds` | `null` | `null` sends everything spoken before the frame |
| `prompt.previousFramesLimit` | `null` | `null` sends the notes of every earlier frame |
| `models.<name>.numPredict` | `4096` | the output limit bounds **thinking plus answer**, and thinking is spent first |
| `models.<name>.temperature` | `0.0` | a thinking model needs its vendor's setting: `qwen3-vl:8b` loops at 0, Qwen says 0.6 |
| `models.<name>.topP` | `null` | the maker's top-p for the mode and task; `null` leaves it to the host |
| `models.<name>.sampling` | `{}` | the rest of the maker's set, under the host's names: `top_k`, `min_p`, `repeat_penalty`, `presence_penalty`, `frequency_penalty`, `seed`; a key the provider cannot take is refused by name. Measured on `qwen3.6-35b-a3b-mtp` (LM Studio): the card's coding set (`topP` 0.95, `top_k` 20, `min_p` 0, `repeat_penalty` 1.0) beat the host's defaults, `research/frames-per-call/README.md` |
| `frames.trimBars` | `true` | `change` only: cut the menu bars, title bars and taskbars that stay the same across the frames around each frame |
| `framesProcessing.framesPerCall` | `1` | consecutive frames per phase-3 call, at most 300; each frame is its own message, the answer one record per frame |
| `framesProcessing.repairCalls` | `1` | `1` sends the frames a batch call did not finish back together once; `0` skips that, and each goes to its single-frame calls |
| `framesProcessing.singleFrameAttempts` | `1` | how many times one frame is sent by itself before it is blocked and phase 3 stops |
| `framesProcessing.minSentFrameWidth` | `1280` | a frame narrower than this is sent enlarged to this width; `0` sends every frame at its own size |
| `document.chunkEntries` | `60` | phase 4: the entries one outline call sorts; a cut never splits one moment |
| `document.chunkCharacters` | `18000` | phase 4: the most text one outline call is handed |
| `document.momentSpeechChars` | `200` | phase 4: the speech the parts call sees of each moment |
| `document.foldEntries` | `5` | phase 4: a later topic this small, titled like one of an earlier part, is folded back into it; `0` folds nothing |
| `document.summaryMaxWords` | `500` | phase 4: the executive summary's ceiling; it is also at most a tenth of the document |
| `document.summaryMinWords` | `50` | phase 4: the summary's limit never drops below this |
| `document.minCutPixels` | `120` | phase 5: a cut with a shorter side comes back with a warning |
| `review.maxTodos` | `20` | phase 5: the most todos the critique may list |
| `review.reads` | `30` | phase 5: the reads one todo's follow-up may make |
| `review.calls` | `40` | phase 5: the tool calls one follow-up may make, reads and submits together |
| `document.writeMode` | `"conversation"` | phase 4: `"conversation"` writes the document in one conversation of follow-ups (R12); `"calls"` asks each piece in a call of its own |
| `document.conversationTriggerTokens` | `120000` | phase 4's conversation: past this it is compacted; never above the window less the answer |
| `document.conversationWindowTokens` | `163840` | phase 4's conversation: the window when LM Studio does not say; the smaller of the two is used |
| `document.readChars` / `document.notesChars` | `12000` / `4000` | phase 4's conversation: one chunk of a read; all notes together |
| `document.charsPerToken` / `document.frameTokens` | `3.5` / `1000` | phase 4's conversation: the token estimate between two host counts (not measured yet) |
| `budgets.writeCalls` / `budgets.writeReads` | `12` / `120` | phase 4's conversation: tool calls per follow-up; reads for the whole phase |
| `budgets.writeHeadingsCalls` | `30` | phase 4's conversation: tool calls of each chapter's draft-headings request, which reads every chunk of that chapter |
| `budgets.writeChapterCalls` | `40` | phase 4's conversation: tool calls of each chapter's writing request, reads, looks, cuts and the answer together |
| `document.headingEntries` | `80` | phase 4's conversation: a heading past this many notes is written in even parts under its one heading, each its own request |
| `document.chapterEntries` | `70` | phase 4's conversation: a chapter past this many notes is asked in parts of whole headings, one answer each |
| `budgets.writeChapterAttempts` | `1` | phase 4's conversation: answers a chapter may give before it blocks |
| `document.sectionEntries` | `12` | phase 4: a heading with more entries in one chapter is split into even consecutive section calls under the one heading |
| `frames.tagsChars` | `5000` | phase 3: the "Tags so far" (subject tags only) each frame call is shown, most used first (R12) |
| `budgets.maxAttempts` | `1` | answers per frame or per section before it is blocked; a blocked step is fixed, then resumed |
| `budgets.askWhyAfterRejection` | `true` | after every rejected phase 4 or 5 answer, one turn offering only `report_problem`: the worker says, correcting nothing, whether the instruction, the template, a check or its own answer was at fault (into `worker-feedback.md`); then the correction is asked if an attempt is left, and the step blocks if not. A turn with no report is passed over |

`models` holds the configured models by name; `phaseModels` names the one a phase calls (`frames_preparing`, `frames_processing`, `finalize`, `review`, `critique_collection`), and a phase it leaves out calls `frame` (phase 3) or `document` (every other). A name with no entry falls back to `models.default`. One run can choose its own with `i2k run --phase-model frames_processing=lfm-vl-3b` (repeatable), or with the run form's Models choice.

### Frame count on a long video

`maxCount` applies after the mode has chosen. At the `interval` default an 87-minute recording wants
437 frames and gets 60, so for anything longer than about 15 minutes either raise `maxCount` or
switch to `scene` or `change`, which spend frames where the frame actually changes.

### The `change` mode, for Teams and Zoom recordings

Once a second the frame is cropped to the shared screen: Teams' black stage is cut away, and so is a
column or row of at least two faces packed against the screen. It is then compared with the last
frame taken, and taken again once enough of it changed and it held still. A second that shows only
faces is never taken, and a return to an earlier screen is taken again with `repeatOf` naming the
earlier frame, so the speech while it is back stays attached to it. Each frame records its crop
`box` in `02_frames.json`.

Measured on five recordings, the frames taken fell from 273 to 439 at
the interval default to between 29 and 331, depending on how much the screen moved, with less
content missed on every recording. Against a slide deck, every slide visit was caught with no extra
frame.

## What each phase writes

```
<run-folder>/
  01_transcript.json              detected language, segments, word times
  02_frames.json                  one item per frame, with its two transcript slices and its notes
  03_notes.md                     layer 2 for the agents: one section per frame, ids F154.2, S0415
  03_processed-frames/frame-NNN.md          one frame's notes, as the model wrote them (R17)
  04_document.md                 the document
  04_document.provenance.json    which entries each section rests on, and the coverage table
  04_writing/                     the answers as given (parts, draft-headings/, table of contents, chapter-NN-text,
                                  sections heading-NNN-N, summary), draft.md, requests/<slug>.md, images.json
  <event>-<speakers>-<date>/      what a reader opens, moved as one piece, named after the meeting (R13)
    <event>-<speakers>-<date>.md  the document with its front matter and its figures
    resources/figure-NN.png       one frame per figure, in document order
    provenance.json               what every figure and paragraph rests on; secrets in web addresses hidden
  04_writing/images/              the looked-at frames and the cuts the chapters made
  05_review/                      phase 5: before.md, document.md (edits so far), todos.json (the critique, each
                                  todo's outcome and lost_at), answers/, notes.json, logs/, review.md, and
                                  earlier/<time>/ for every earlier review of this run, kept whole
  frames/                         the cut JPEGs
  calls/                          per attempt: the messages sent, and the call log
  logs/<run-id>/                  the run's own logs
    calls.jsonl                   one line per model call: tokens, prefill and output time, how it ended
    performance.jsonl             one line per frame, group, document part, phase and session (below)
  audio.wav
```

The call log carries `answerChannel`, which says whether the model returned its answer as a function
call or as text that had to be read as one. A run showing `text` often is a fact about the host worth
knowing.

**Performance.** Every unit of work writes one line to `performance.jsonl` the moment it ends, and prints the same
figures to the console: `item` (a frame asked alone, retries included, or a repeat that reused its original),
`batch` (several frames in one call), `part` (a phase-4 call or follow-up, or a phase-5 conversation), `phase` (each phase this
session ran, with its frames and seconds a frame) and `video` (the session against the recording's length). A
`session` line opens every command, so a resumed run's lines stay apart. Each line has its start, its seconds, its
status, and the calls, prompt and output tokens, model seconds and output tokens a second it cost, read from
`calls.jsonl`. The run ends with one line for its own calls (`calls this resume: 7 · 131,508 prompt + 21,412 output tokens · 13.6 min · table in <the calls.jsonl path>`) and a table of its phases and the video. The console names each unit when its call starts and prints its outcome under it when it ends, and nothing for a
copied frame: `frames 20 → 23 of 321 at 6:59 …`, then `  4:39 (70 s a frame) · 10,670 in / 8,501 out · context 12% of
163,840 · accepted`, where the
context share is the last call's tokens against the window the host loaded (read from LM Studio at the start, else
`numCtx`). A batch with failed frames ends `→ retried together`; its repair call is a unit of its own
(`frames-020-023-repair`). A phase-4 follow-up names its place among the follow-ups of its kind, so the console shows
how many are left: `building the headings of chapter 2 of 6 …`, `writing chapter 4 of 6 …`; one asked once says what it does:
`drafting the executive summary …`, `building the agenda …`, `scaffolding the document: the table of contents …`,
`writing the final executive summary …`. On the calls path (`document.writeMode: "calls"`) a part keeps
its own name, `outline-03 …`. Inside a follow-up, the console prints one line per agent turn saying what it did,
such as `  read_notes chapter 2 chunk 3 · 69,617 tok`.

## Changing what the model is asked

Instruction text is data, not code. The model steps read from `agent-context/instructions/`, one folder per
workflow, each holding its own file for every step it sends:

| Folder | What it holds |
|---|---|
| `video/` | the video workflow's files: frame notes, critique, second look, open questions, answers, writing, review, critique collection, clean up; the redundancy filter and the fast workflow's two files |
| `audio/` | the audio workflow's files: speech into paragraphs, preparing the notes, takeaway, writing, clean up |
| `pdf/` | the PDF workflow's files: page and figure notes, critique, second look, open questions, answers, writing, review, critique collection, clean up |
| `_shared/` | the text a step's files share (each workflow's file includes it with `{{SHARED:<name>}}`), and the team's terms and the problem report guideline |
| top level | the sections of the writing conversation, also sent alone in the calls path: parts, outline, contents, section, summary, draft headings, table of contents |

`agent-context/context_map.json` says which pieces each phase assembles. Editing any of them changes
the next run, with no code change. Nothing is copied into the run folder: every prompt is assembled
from this folder at the moment the call is made, and `calls/` holds the messages each call was
actually sent. The compaction texts, what a worker is asked when its work outgrows its room, are files
too, under `agent-context/compaction/` (its README lists each one and the placeholders it must keep).

On an installed copy, change the instructions on the page instead: the settings gear, then
**Instructions**. Pick a workflow (video, audio, PDF), a phase and a step, then a file: Read shows it
rendered, Edit saves your text with a note, Compare puts any two of the default, the text in force,
your draft and every saved version side by side, History lists every version with Restore, and Full
prompt shows the whole system prompt the step's worker gets. The program's defaults are never written:
your edits and every version are kept in the workspace, in `instructions/` beside `output/` and `ui/`
(`I2K_WORKSPACE` moves it with them), and **Reset to default** puts a default back, the edit kept in
the history. A saved edit reaches a running run's next call.

## Tests

```bash
.venv/bin/python tests/run_tests.py
```

Offline, with a scripted fake chat model. No model host is needed and none is reached.


## Troubleshooting

| What you see | What it means |
|---|---|
| `RESPONSE_CUT_AT_LIMIT` | the reply hit `numPredict`. Raise it; retrying with the same budget fails the same way |
| `RESPONSE_THINKING_ONLY` | the model thought and answered nothing. The prompt, or a model that answers |
| `MODEL_NO_STRUCTURED_OUTPUT` | nothing parsed from either channel. Check `structuredMethod`; on a local model it is `function_calling` |
| `MODEL_CALL_FAILED` | the host raised. `main.py probe` |
| a phase refuses by name | its input file is not in the output folder. Run that phase first |
| `device: mps` refused | CTranslate2 has no Metal backend. Use `cpu` |
