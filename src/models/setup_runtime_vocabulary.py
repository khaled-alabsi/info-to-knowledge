"""What a run needs beside Python, each with how to set it up on macOS, Windows and Linux and the page it comes from.
The setup wizard (`GET /api/setup`) shows them; `services/setup_check_service.py` checks each one.

A run needs ONE model host, whichever its models' specs name: a local host (LM Studio, Ollama, an MLX or other
OpenAI-compatible server) or an API reached with a key. The tools are needed whatever the host.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RuntimeSpec", "RUNTIME_SPECS", "SYSTEMS", "PROVIDER_GUIDE_KEYS", "HOST_KIND", "API_KIND", "TOOL_KIND"]

# The systems the steps are written for, in the order the wizard shows them
SYSTEMS = ("mac", "windows", "linux")

# A local model host, an API reached with a key, a program every run uses
HOST_KIND = "host"
API_KIND = "api"
TOOL_KIND = "tool"


@dataclass(frozen=True)
class RuntimeSpec:
    """`steps` maps each of `SYSTEMS` to the steps in order. `start_steps` says how to start a host's server, empty for
    anything else. `download_url` is where it is downloaded (or, for an API, where a key is made), empty when nothing
    can be. `key_variable` is the environment variable an API reads its key from, empty for anything else."""

    key: str
    name: str
    kind: str
    purpose: str
    download_url: str
    steps: dict[str, tuple[str, ...]]
    start_steps: tuple[str, ...] = ()
    key_variable: str = ""


RUNTIME_SPECS = (
    RuntimeSpec(
        "lmstudio", "LM Studio", HOST_KIND, "runs the models on this machine; the recommended models are made for it",
        "https://lmstudio.ai/download",
        {"mac": ("Download LM Studio for Mac (Apple Silicon) from the download page.",
                 "Open the .dmg and drag LM Studio into Applications.",
                 "Open LM Studio once, so it installs its lms command."),
         "windows": ("Download the Windows installer from the download page and run it.",
                     "Open LM Studio once, so it installs its lms command."),
         "linux": ("Download the AppImage from the download page.",
                   "Make it executable: chmod +x LM-Studio-*.AppImage",
                   "Run it once, so it installs its lms command.")},
        ("Open LM Studio's Developer tab and switch on Start server (port 1234), or run: lms server start",)),
    RuntimeSpec(
        "ollama", "Ollama", HOST_KIND, "runs the models on this machine",
        "https://ollama.com/download",
        {"mac": ("Download Ollama for macOS from the download page, open it and move it into Applications.",),
         "windows": ("Download the Windows installer from the download page and run it.",),
         "linux": ("Run: curl -fsSL https://ollama.com/install.sh | sh",)},
        ("Open the Ollama app, or run: ollama serve",
         "Only one model host may hold a model at a time: quit Ollama before LM Studio loads one.")),
    RuntimeSpec(
        "mlx", "MLX or another OpenAI-compatible server", HOST_KIND,
        "runs the models through mlx-vlm on an Apple silicon Mac, or any server that speaks the OpenAI API",
        "https://github.com/Blaizzy/mlx-vlm",
        {"mac": ("Run in this repository: ./i2k setup-mlx <model> (installs mlx-vlm in ~/.mlx-vlm/venv).",),
         "windows": ("MLX runs only on Apple silicon Macs. Use another OpenAI-compatible server (vLLM, llama.cpp's "
                     "llama-server) and give its address as the model's base URL.",),
         "linux": ("MLX runs only on Apple silicon Macs. Use another OpenAI-compatible server (vLLM, llama.cpp's "
                   "llama-server) and give its address as the model's base URL.",)},
        ("Start the server, for MLX: ~/.mlx-vlm/venv/bin/python -m mlx_vlm.server --port 8080",)),
    RuntimeSpec(
        "openai", "OpenAI API", API_KIND, "runs the models on OpenAI's servers: the recording's frames leave the machine",
        "https://platform.openai.com/api-keys",
        {system: ("Make a key on the key page.", "Paste it below; it is kept in the git-ignored .env file.") for system in SYSTEMS},
        key_variable="OPENAI_API_KEY"),
    RuntimeSpec(
        "anthropic", "Anthropic API", API_KIND,
        "runs the models on Anthropic's servers: the recording's frames leave the machine",
        "https://console.anthropic.com/settings/keys",
        {system: ("Make a key on the key page.", "Paste it below; it is kept in the git-ignored .env file.") for system in SYSTEMS},
        key_variable="ANTHROPIC_API_KEY"),
    RuntimeSpec(
        "ffmpeg", "ffmpeg and ffprobe", TOOL_KIND, "read the recording's sound and frames",
        "https://ffmpeg.org/download.html",
        {"mac": ("Install Homebrew from https://brew.sh if it is not installed.", "Run: brew install ffmpeg"),
         "windows": ("Run: winget install Gyan.FFmpeg", "Open a new terminal, so ffmpeg is on the PATH."),
         "linux": ("Run: sudo apt install ffmpeg (Debian, Ubuntu) or sudo dnf install ffmpeg (Fedora)",)}),
    RuntimeSpec(
        "tesseract", "Tesseract text reader, English and German", TOOL_KIND,
        "reads the text on frames: phase 2's board changes and gallery checks, phase 3's screen check, phase 4's cuts",
        "https://tesseract-ocr.github.io/tessdoc/Installation.html",
        {"mac": ("Run: brew install tesseract",
                 "Add a language Settings names in frames.textReaderLanguages: run "
                 "curl -L -o \"$(brew --prefix)/share/tessdata/deu.traineddata\" "
                 "https://github.com/tesseract-ocr/tessdata_fast/raw/main/deu.traineddata (deu for German)"),
         "windows": ("Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki, ticking the German language "
                     "data among the additional languages.", "Open a new terminal, so tesseract is on the PATH."),
         "linux": ("Run: sudo apt install tesseract-ocr tesseract-ocr-deu (Debian, Ubuntu) or sudo dnf install "
                   "tesseract tesseract-langpack-deu (Fedora)",)}),
    RuntimeSpec(
        "classifier", "Frame classifier", TOOL_KIND, "drops the frames that show only people (phase 2's knowledge filter)",
        "",
        {system: ("Run .venv/bin/python -m pip install -r requirements.txt in the playbook folder: the classifier "
                  "runs in the program's own environment.",
                  "Or switch phase 2's filters off in Settings: frames.isKnowledgeFilterOn, "
                  "frames.isTextOnlyMarkOn and frames.isBoardStageOn.") for system in SYSTEMS}),
    RuntimeSpec(
        "unlimited-ocr", "Unlimited-OCR-2 scanned-page reader", TOOL_KIND,
        "reads a PDF's scanned pages in phase 1 (pdf.ocrEngine \"unlimited-ocr\"): nearly every word of a scan kept "
        "where Tesseract loses up to three quarters",
        "",
        {"mac": ("Press Download on the Models step: it builds the tool's environment and downloads its model "
                 "(3.9 GB) from Hugging Face.",
                 "Or run tools/unlimited-ocr/setup.sh --download in the playbook folder."),
         "windows": ("It runs on Apple silicon only: set pdf.ocrEngine to tesseract in Settings.",),
         "linux": ("It runs on Apple silicon only: set pdf.ocrEngine to tesseract in Settings.",)}),
    RuntimeSpec(
        "d1-knowledge-filter", "d1-2B knowledge filter", TOOL_KIND,
        "drops a video's frames with no knowledge in phase 2 when frames.knowledgeFilterModel is \"liquidai-d1-2B\" "
        "(Liquid AI's decision model, no training)",
        "",
        {system: ("Run tools/d1-knowledge-filter/setup.sh --download in the playbook folder: it builds the tool's "
                  "environment and downloads d1-2B (about 4.5 GB) from Hugging Face.",
                  "Or set frames.knowledgeFilterModel to google-SigLIP-2 in Settings.") for system in SYSTEMS}),
)

# The guide of each spec's provider (`<provider>:<model>`)
PROVIDER_GUIDE_KEYS = {"lmstudio": "lmstudio", "ollama": "ollama", "openai-compatible": "mlx", "openai": "openai",
                       "anthropic": "anthropic"}
