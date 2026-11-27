# info-to-knowledge

info-to-knowledge turns one recording into a reference document that a person who did not attend can read. The
recording can be a video, an audio file or a PDF. The document opens with an executive summary and an agenda, its
chapters follow the recording's own parts, and every statement names the part of the recording it rests on.

It runs against a local model host such as LM Studio or Ollama. Nothing is sent to a hosted service. A run is seven
numbered phases, and each one writes its own files, so any phase can run again on its own against results made
hours earlier.

## Features

- **Video, audio or PDF in, document out.** A folder of parts (the files of one meeting) is read as one recording.
- **Frames that carry knowledge.** For a video, the program keeps the frames that show something new and drops people
  galleries, blank screens and repeats. A model describes each kept frame.
- **Checks on every answer.** A validator reads the files on disk and rejects a bad answer with the reason. The model
  tries again. A step that spends all its attempts stops the run with a clear message, and `--resume` continues it.
- **Review.** A second worker reads the finished document as a critic and fixes what the recording can answer.
- **A web page and a terminal UI.** Both queue recordings, show each run's phases, frames and log, and pause, resume
  or reset a run.
- **Document shapes.** Reference document (the default), presentation, conversation, meeting, language lesson and
  sprint notes.
- **Many model hosts.** LM Studio, Ollama, llama.cpp and any OpenAI-style URL. Named settings profiles and worker
  sets send jobs to other machines on your network.

## Requirements

- Python 3.12, FFmpeg, Node.js 22 and Tesseract (with the English and German language data).
- Pandoc, for Word, PowerPoint and OpenDocument files.
- A model host with a vision-capable model. The default settings name `qwen3.6-35b-a3b-mtp` in LM Studio at
  `http://localhost:1234/v1`.
- Disk space for the models. Check your free disk first: the recommended language model is about 19 GB.

On an Apple silicon Mac the program also uses MLX for speech recognition and Apple's text recognizer. On Linux and in a
container it uses faster-whisper and Tesseract instead (`--profile container`).

## Install

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .         # installs the packages of requirements.txt and the `i2k` command
source .venv/bin/activate
(cd web && npm ci && npm run build)          # the web page
export HF_TOKEN=hf_...                       # a Hugging Face token, only for gated models such as the speaker model
i2k setup-models                             # downloads the recommended models into models/ (git-ignored)
i2k probe                                    # checks that every configured model host answers
```

`./setup.sh hf_...` does the same steps in one script. To run in a container, read
[docs/container.md](docs/container.md).

Speaker recognition (who spoke when) is optional: `pip install -r requirements-diarization.txt`, then accept the terms
of the model on huggingface.co with the account of your token.

## First run

```bash
i2k run --video ~/Downloads/meeting.mp4 --output output/meeting
```

`--output` is the run folder itself. It receives the phase files (`01_transcript.json`, `02_frames.json`, ...), the
cut `frames/`, and the finished document in a folder named after the meeting. A stopped run continues with
`--resume`. Other ways to start:

```bash
i2k inbox --list                          # recordings in inbox/ that have no document yet
i2k inbox                                 # run every new one
i2k run --video notes.mp3 --output output/notes --ingesting-profile meeting
i2k run --video meeting.mp4 --output output/meeting --from 4      # write the document again from the frames' notes
i2k reset-to 4 --output output/meeting --yes                      # take a run back to phase 4
i2k serve                                 # the web page at http://127.0.0.1:8765
```

## Settings

`playbook.default.json` holds the default settings. Copy `playbook.local.example.json` to `playbook.local.json` for
the values of your machine. The program reads the local file on top of the default file, and git ignores it. A key
or a password never goes in a settings file: the settings name an environment variable, or the program reads
`HF_TOKEN` from the environment.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): the phases, the run folder and the code layers.
- [Flow](docs/FLOW.md): what each phase reads and writes, file by file.
- [Command line](docs/cli-usage.md) and [web page](docs/ui-usage.md).
- [Full guide](docs/GUIDE.md): install, every run option, the settings and troubleshooting.
- [Workflow](docs/WORKFLOW.md), and how the [command line](docs/cli-context.md) and the [web page](docs/ui-context.md) are built.
- [Running in a container](docs/container.md).
- [Third-party models and data](NOTICE).

## Tests

```bash
.venv/bin/python tests/run_tests.py                    # the offline suite, no model host needed
.venv/bin/python tests/run_tests.py test_ui_services   # one case file
(cd web && npm test)                                   # the web page's unit tests
```

## Contributing

Bug reports, ideas and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first and follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Report a security problem as [SECURITY.md](SECURITY.md) says.

## License

info-to-knowledge is free software under the [GNU Affero General Public License v3.0](LICENSE). You may use, change
and share it. If you share a changed version, or run one as a service for others, you must publish its source code
under the same license.

The models, word lists and helper tools it uses have their own licenses: see [NOTICE](NOTICE).
