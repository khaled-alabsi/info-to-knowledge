# UI usage

Two front ends over the same server: a web page (`./i2k serve`) and a terminal UI (`./i2k tui`). Both add jobs to one
queue that runs one `run` at a time. The commands typed by hand are in [cli-usage.md](cli-usage.md).

Run from the project's root folder.

## Start

```bash
(cd web && npm install && npm run build)    # once, and after every change to web/
./i2k serve                                 # the web page at http://127.0.0.1:8765
./i2k tui                                   # the terminal UI; starts a server in the background when none answers
```

| Flag | What it does |
|---|---|
| `serve --host ADDR` | the address to listen on (default `127.0.0.1`, this Mac only) |
| `serve --port N` | the port (default `8765`) |
| `tui --server URL` | the server to talk to (default `http://127.0.0.1:8765`) |

- Ctrl-C stops the server. A running job keeps going, and a server started again shows it.
- A server the terminal UI started keeps running after you leave the terminal UI. Its console is `ui/server.log`.
- `--host 0.0.0.0` puts the page on the network. There is no login: anyone who reaches the port can upload, run and
  reset.

## The web page

Left: the **Inbox** and **Queue** tabs. Once phase 4 has named a meeting's topic, it shows under the video's name
and under the open run's title. Right: what you opened. A refresh comes back to the same tab, run,
run tab and artifact (kept in a cookie). On a phone the two are one column, and the
back arrow returns to the tabs. What each action did shows for a few seconds in the bottom right corner.

| Tab | What you do there |
|---|---|
| Inbox | the videos in sections **New**, **Running**, **Not finished** and **Done** (each opens and closes, with its own select-all; which are open is remembered); a run folder whose video is not in the inbox sits in the section of its state, marked "no video in the inbox", and several run folders of one video are one row, the run that went furthest (running, then done, then started), so an earlier attempt left behind never shows; a video's state pill or such a row opens the run; ▶ on a video plays it; the pencil on a **New** video renames it (the extension stays when you leave it out; a taken name is refused); the bin icon moves the ticked videos to `inbox/.trash` (a warning first when one is not done; their runs stay listed), and **Trash** at the bottom lists them with **Restore**, a tick box each and **Delete permanently** (one confirmation for all ticked, naming each and flagging a whole run folder; the only deletion in the page); the box icon archives the ticked videos into `inbox/.archive` with no question, and the **Archive** section lists each with its topic and its run (its state pill opens the run, whose Recording tab still plays) and **Unarchive**, which puts it back under the name it had; drop recordings on the box (or click it) to upload them into `inbox/` (a recording a done run processed already is refused, with an **Open <run>** button to that run, and nothing of it is kept); tick videos; choose the phases in the button beside **Process**; **Process** queues the ticked ones, its arrow offers **Process all new** and **Process every video** |
| Queue | every job, newest first; click one to read everything its process printed; its `⋯` menu holds **Cancel** and **Interrupt** |

**An open run** offers only what fits its state:

| The run is | You see |
|---|---|
| running | which phase it is in, and **Control**: Pause, Drain, Stop (and **Continue** while a signal waits) |
| waiting in the queue | the job it waits as; nothing to start |
| stopped with a phase blocked or failed | **Unblock phase N**: a resume, which asks the blocked step again with the rejection that blocked it, then goes on |
| stopped with blocked frames | **Ask blocked frames … again**: phase 3 asks only those frames again (`--worker`) |
| stopped with a phase not complete | **Resume from N · phase**, **Run** (choose the phases), `⋯` with the two resets |
| complete in every phase | **Run again** and `⋯`; no Resume |
| not running | **Recording ▾**: the recording actions that fit (below) |
| done, its recording in the inbox | **Recording ▾ Move to run folder**: kept there, as a run does when it ends |
| keeping its recording in its folder | **Recording ▾ Move to inbox**; until then every action that runs phase 1 or 2 (Run from 1 or 2, Run only 1 or 2, Reset to 0 or 1, a Run or Resume starting there) is shown disabled with the reason |

**Recording ▾** holds every recording action: Move to inbox, Move to run folder, **Delete recording** (into the inbox
Trash, from the inbox or the run folder; **Restore** there brings it back) and, when the run has no recording anywhere,
**Upload recording…** (saved under the name the run record names; a done run keeps it in its folder). While one works,
a turning ring says what it does. None is offered while the run is live.

The `?` beside a phase's name says what that phase does, what it reads and writes, and whether it calls the model.
Each phase card has its own `⋯` when nothing runs: **Run from N**, **Run only N**, **Reset to N-1**.

**Progress** shows the phases, the frames by status and the live log (Wrap, Copy, Follow). **Artifacts** shows what
the run produced, never a `.json`: the document (the published one first), the draft, the notes, each frame's notes,
the cuts phase 4 made from the recording for its figures, the frames (each with its time; click one for the large view, arrow keys move, **Play from 6:57** opens the
**Recording** tab at that second), and the audio. **Recording** is a tab of its own beside Progress and Artifacts,
shown while the video is still where the run found it, in the archive, or kept in a done run's folder. Under its player
the **Transcript** lists every line said with its time: click a time to play from there. Type in its search box: a list
of the lines holding your words opens under it (a word misspelt by a letter or two still matches), the rest of the word
you are typing shows in grey and **Tab** takes it, and a click or **Enter** on a line plays the recording from it. **Download** beside it saves the transcript as
text, each line with its time. The same transcript sits under the audio player in Artifacts' **Audio and transcript**,
where a time plays the audio. While the run writes, the list updates by
itself. A document is rendered with its figures and its metadata as a table; a link to a heading scrolls the document.
**Source** shows its markdown, **Open** opens the file alone, **Download** saves it (a document that links figures
comes as a zip of it and them), **Read** shows it full screen (Esc or **Close** leaves).
Mermaid diagrams are drawn in place; **Expand** (on hover) opens one full screen: drag to move, scroll or pinch to
zoom, **Fit** or `0` to see it whole, and save it as **SVG**, **PNG** or the **.mmd** source. A click on a figure opens
it the same way. Code blocks are coloured and have **Copy**.

**Frames and Cuts** have a bar above the images: tick images (or **select every image shown**), then **Download
selected** or **Download all** gets one zip; **Hide selected** hides them from this view for good (the files stay in
the run folder, and the pipeline still uses them), **Show hidden** brings them back into view, and **Show selected
again** unhides them.

**Reset**: the dialog lists what would be removed; **Remove it** runs it with `--yes`, **Keep everything** closes it.

**Uploads**: one video dropped alone goes straight into the inbox. Several files, a folder or a zip are held first and
open **which are one meeting?**: every video is its own meeting, except videos whose names differ only by a part
number, which are proposed as one (undo with **Split**); tick videos and press **Join as one meeting**, name it, put
its parts in order with ↑ ↓, and say which meeting a `.vtt` or `.srt` belongs to; **Add to inbox** puts each meeting
in its own folder with its parts numbered `01`, `02`, and **Cancel upload** leaves nothing. A meeting in parts is one
inbox row ("2 parts"); ▶, the Recording tab and **Play from** play its parts one after another. A name already in the inbox gets a number (`meeting-2.mp4`). A file that is not a video is refused, and
the message says so.

`npm run dev` in `web/` serves the page with live reload at http://localhost:5173 and sends `/api` to the server on
8765. Use it while changing the page.

`npm run coverage` in `web/` runs the page's tests (Vitest in jsdom, with `fetch`, `EventSource` and uploads faked; no
server, no model) and fails below 85% coverage of lines, statements, functions or branches. `npm test` runs them
without the coverage. They are not part of the Python suite, which needs no Node.

## The terminal UI

Left: the **Inbox**, **Runs** and **Queue** tabs. Right: the open run, its buttons, and the **Run log** and
**Messages** tabs. Bottom: the command line, with completion for slash commands (the right arrow accepts the
suggestion at the end of the line). Every button runs one of these commands. Ctrl-Q leaves.

| Command | What it does |
|---|---|
| `/inbox` | show the inbox; each video has a number |
| `/process all` | queue a run for every inbox video with no document yet |
| `/process 1 3` | queue runs for inbox videos 1 and 3 |
| `/process 2 --from 3 --to 4 --dry-run` | with a phase choice; `--phase N` runs one phase |
| `/runs` | show the run folders |
| `/open <run folder>` | open a run folder (or click it in Runs); quote a name with spaces |
| `/resume [--dry-run]` | continue the open run where it stopped |
| `/from N [--to M] [--dry-run]` | run the open run's video again from phase N |
| `/phase N [--dry-run]` | run phase N alone on the open run |
| `/worker 12 40` | ask phase 3 again for frames 12 and 40 of the open run |
| `/pause`, `/drain`, `/stop` | signal the open run (drain: finish the current frame, then stop) |
| `/continue` | remove a pause or drain signal |
| `/interrupt` | interrupt the running job's process, as Ctrl-C would |
| `/reset-to N` | list what removing every phase of the open run after N removes |
| `/reset-to N --yes` | remove it |
| `/reset-chapter N [--yes]` | the same for chapter N and every later chapter |
| `/queue` | show the job queue |
| `/cancel <job id>` | take a queued job off the queue |
| `/models`, `/probe` | what `i2k models` and `i2k probe` print |
| `/help` | list the commands |
| `/quit` | leave; jobs keep running |

## What a job ends as

| Status | Meaning |
|---|---|
| `queued` | waiting for the job before it |
| `running` | its `run` process is alive |
| `finished` | exit 0: every selected phase finished |
| `incomplete` | exit 1: a phase failed or something is blocked; fix the cause, then Resume |
| `refused` | exit 2: the run line was refused; the job's console says why |
| `stopped` | exit 130: stopped or interrupted; Resume continues it |
| `crashed` | any other exit code |
| `cancelled` | taken off the queue before it started |
| `lost` | its process ended while no server was waiting on it, so its exit code is unknown |

## Common tasks

- **New recording to document:** drop it on the Inbox box, tick it, **Process selected**.
- **A run blocked:** open it from its video's state pill (or its row), read the log, fix the cause, press **Resume**.
- **Rewrite the document only:** open the run, choose "from phase 4", press **Run again**.
- **Stop a run cleanly:** **Stop** (at its next check) or **Drain** (after the current frame); **Interrupt** at once.
- **Try a run without model calls:** tick **dry run** before queueing.

## Where things are

- `inbox/`: uploaded recordings, until their run is 100% done: then `<run folder>/recording/`.
- `output/<video name>/`: one run folder per inbox video (older inbox runs stay in `knowledge/`).
- `ui/jobs.json`: the queue. `ui/jobs/<job id>.log`: everything a job's process printed.
- The run's own log is `logs/<run id>/HH-MM.log` inside its run folder; the Run log shows the newest one.
