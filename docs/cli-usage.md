# CLI usage

Run from the project's root folder. `./i2k` uses the project's own venv. Every command takes `-v` (verbose) and `-h`.

Phases: `1` transcribe, `2` frames, `3` frame notes, `4` document, `5` review.

## run: one video

```bash
./i2k run --video <file.mp4> --output <run folder>             # every phase, a new run
./i2k run --video <file.mp4> --output <run folder> --resume    # continue the newest run of that folder
```

| Flag | What it does |
|---|---|
| `--video FILE` | the recording to work on (required) |
| `--output DIR` | the run folder every phase file, record and log goes to (required) |
| `--phase N` | run phase N only, then stop |
| `--from N` | run phase N and every phase after it |
| `--to N` | stop after phase N |
| `--worker N` | ask phase 3 again for frame N only, rewriting its notes; repeat the flag for more frames |
| `--resume` | continue from where the run stopped or blocked |
| `--dry-run` | write what each model call would be sent, call no model |

- A blocked step stops the run. Fix the cause, then `--resume`; the blocked answer is judged again first.
- `--phase 3 --resume` skips phase 3 when the run's record says it is complete. To ask frames again, use `--worker N`.

## inbox: every new video

```bash
./i2k inbox --list        # what is new; runs nothing
./i2k inbox               # run each new video into output/<video>/
./i2k inbox --dry-run     # write what each call would be sent, call no model
```

| Flag | What it does |
|---|---|
| `--inbox DIR` | where recordings are dropped (default `inbox/`) |
| `--output DIR` | where one run folder per video is written (default `output/`) |

## serve and tui: the web page and the terminal UI

```bash
(cd web && npm install && npm run build)    # once, and after a change to web/
./i2k serve                                 # the web page at http://127.0.0.1:8765
./i2k tui                                   # the terminal UI; starts a server when none answers
```

| Flag | What it does |
|---|---|
| `serve --host ADDR` | the address to listen on (default `127.0.0.1`; another prints a no-login warning) |
| `serve --port N` | the port (default `8765`) |
| `tui --server URL` | the server to talk to (default `http://127.0.0.1:8765`) |

- Both add to one queue, which runs one `run` at a time. A job keeps running when the page, the terminal UI or the
  server closes.
- Dropping a recording on the page uploads it into `inbox/`.
- In the terminal UI, `/help` lists the slash commands; every button is one of them.

## Taking a run back

```bash
./i2k reset-to N --output <run folder>            # list what removing every phase after N removes
./i2k reset-to N --output <run folder> --yes      # remove it; 0 removes every phase
./i2k reset-chapter N --output <run folder>       # list what removing chapter N and later removes
./i2k reset-chapter N --output <run folder> --yes # remove them; --resume writes them again
```

- Both refuse while a run process writes into the folder. Logs are kept.
- `reset-to 4` puts back the published document as phase 4 wrote it.
- `reset-chapter` keeps chapters 1 to N-1, the agenda and the summary draft.

## Hosts and models

`i2k run --worker-set NAME` puts a run on one worker set (`default` or a name in the settings' `workerSets`): its models and
hosts are the settings with that set's changes on top. The folder keeps the set, so a resume with no flag goes on with it.
An unknown name is refused with the names that exist. Runs on different sets go at the same time; runs on one set go one
after another.

```bash
./i2k probe     # are the configured model hosts reachable
./i2k models    # which model each role resolved to
```

To use another machine's LM Studio for one run: `PB_HOSTS__LMSTUDIO__URL=http://<other-machine-address>:1234/v1 ./i2k run ...`; to keep it, add a worker set that sets that host.

## Checks

```bash
.venv/bin/python tests/run_tests.py                                       # the offline suite
```
