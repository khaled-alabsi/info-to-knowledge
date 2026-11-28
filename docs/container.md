# Running in a container

The image holds the program, its libraries and the built web page. Models, settings, recordings and run results stay
on your machine and are mounted into the container, so the image stays small and a new model needs no rebuild.

## What is mounted

| On your machine | In the container | Holds |
|---|---|---|
| `playbook.local.json` | `/app/playbook.local.json` | your settings: the model host addresses and your provider sets |
| `inbox/` | `/data/inbox` | recordings to process |
| `output/` | `/data/output` | run folders and finished documents |
| `ui/` | `/data/ui` | the queue and the console of each job |
| `models/` | `/models` | downloaded models (`I2K_MODELS_DIR`, and `HF_HOME` for the Hugging Face cache) |

## Start

With Podman (Docker works the same with `docker` in place of `podman`):

```bash
cp playbook.local.example.json playbook.local.json   # then set your model host's address
export HF_TOKEN=hf_...                               # only for gated models such as the speaker model
podman compose up --build
```

The web page answers at http://localhost:8765. Without an account, anyone who reaches that port can upload, run and
reset. Compose publishes the port on `127.0.0.1` only. Create an account before you open it to your network:

```bash
podman compose run --rm i2k i2k accounts add admin --role admin
```

Run any command instead of the server:

```bash
podman compose run --rm i2k i2k setup-models        # download the recommended models into ./models
podman compose run --rm i2k i2k probe               # check that the model hosts answer
podman compose run --rm i2k i2k run --video /data/inbox/meeting.mp4 --output /data/output/meeting
```

## Build by hand

```bash
podman build -t info-to-knowledge -f Containerfile .
podman build -t info-to-knowledge --build-arg WITH_OFFICE_TOOLS=1 -f Containerfile .   # adds LibreOffice and librsvg
```

The build arguments:

| Argument | Default | What it adds |
|---|---|---|
| `WITH_OFFICE_TOOLS` | `0` | `soffice` (LibreOffice Impress) to turn `.odt` and slide files into `.docx` and PDF, and `rsvg-convert` to draw SVG pictures. About 700 MB. Word, PDF and PowerPoint files read without it. |

## Container mode (`I2K_CONTAINER`)

The image sets `I2K_CONTAINER=1`. It tells the program that Python, its libraries, ffmpeg and Tesseract are installed
already, so nothing on the machine is set up from the page. Unset (a run on your own machine), everything is on.

| With `I2K_CONTAINER=1` | |
|---|---|
| Setup wizard | never opens and has no button; the Settings page has no "Setup wizard" |
| Models download from the page (`POST /api/setup/models`) | refused with 409 and the reason |
| `i2k setup-mlx` | refused with exit code 2 (mlx-vlm needs Apple silicon) |
| Model providers (the models, their addresses and keys, `providers`) | stay editable in Settings |
| A model a run calls that has no reachable provider | named on the Settings page (the gear shows a mark), and a run started from the page is refused with the model's name and the reason: no model set, a host that does not answer, a host without the model, an API without its key |

`GET /api/setup` carries the mode as `is_container` and the missing providers as `missing_providers`; in a container it
lists no runtimes and no recommended models. `i2k setup-models` on the command line still downloads into `./models`.

## The language model

No language model is inside the image. It runs outside the container, in LM Studio, Ollama or llama.cpp on your
machine or another machine. Inside the container your machine is `host.containers.internal`, and the `container`
settings profile (switched on by the image through `I2K_PROFILE=container`) already names it:

```json
{
  "hosts": {
    "lmstudio": {"url": "http://host.containers.internal:1234/v1"},
    "ollama": {"url": "http://host.containers.internal:11434"}
  }
}
```

A provider set, or a host added to `hosts` and named in a model's `host`, in `playbook.local.json` can reach any other machine. Compose adds
`host.containers.internal` with `extra_hosts`, so it also works with Docker on Linux.

## Limits

- MLX and Apple's text recognizer need macOS, so the container uses faster-whisper on the CPU and Tesseract. Speech
  recognition is slower than on an Apple silicon Mac.
- The container has no GPU by default. For an NVIDIA card, start it with GPU access (`--device nvidia.com/gpu=all` in
  Podman) and install a CUDA build of torch in a derived image.
- The image holds CPU builds of torch. Models stay outside, so the first `setup-models` run downloads several GB into
  `./models`. Check your free disk first.
- Tesseract (text on frames) and the two small OpenCV models that find faces and people (`weights/`, 0.2 MB and 3.8 MB)
  are inside the image and run on the CPU. Nothing needs to be set for them.
- The scanned-page reader (`tools/unlimited-ocr`) runs on Apple silicon only, so the container profile reads scanned
  pages with Tesseract, unless `providers.chat.host` names a chat server that serves the Unlimited OCR model
  (`pdf.unlimitedOcrModel`): then that server reads each page.
- The d1 decision model (`tools/d1-knowledge-filter`) is optional and is not built in the image. In the container it
  loads from the models volume (`/models`) and runs on the CPU, which is slow. On a Mac, where a container has no GPU,
  run `i2k serve-models` natively on the Mac, add a host at
  `http://host.containers.internal:8790/v1` to `hosts` and name it in `providers.decision.host` (start it with `--host 0.0.0.0` so the container can reach it). See
  [models](models.md).
