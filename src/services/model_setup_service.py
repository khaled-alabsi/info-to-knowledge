"""`i2k setup-models`: download the recommended models into this repository and make LM Studio list them.

Each file of `models/recommended_model_vocabulary.py` that LM Studio's models folder does not hold already is
downloaded from Hugging Face into `<repository>/models/<publisher>/<name>/` (git-ignored), unless it is there already;
then LM Studio is given a symbolic link to it (`lms import --symbolic-link`). A Hugging Face token is
needed only for a gated repository; it is read from the command line, else `HF_TOKEN`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from huggingface_hub import hf_hub_download

import config
from models.recommended_model_vocabulary import RECOMMENDED_MODELS

__all__ = ["MODELS_DIR", "LMSTUDIO_MODELS_DIR", "LMS_COMMAND", "set_up_models", "set_downloader"]

# `I2K_MODELS_DIR` moves the folder the models are downloaded into (a container mounts it as a volume); without it the
# models go into `models/` beside the inbox and the run folders.
MODELS_DIR = (Path(os.environ["I2K_MODELS_DIR"]).resolve() if os.environ.get("I2K_MODELS_DIR")
              else config.PLAYBOOK_ROOT / "models")
LMSTUDIO_MODELS_DIR = Path.home() / ".lmstudio" / "models"
LMS_COMMAND = Path.home() / ".lmstudio" / "bin" / "lms"


def _fetch_file(repository: str,
                file_name: str,
                folder: Path,
                token: str | None) -> None:
    hf_hub_download(repository, file_name, local_dir=str(folder), token=token)


# What downloads one file. A module-level name with a setter: the suite runs with no network.
DOWNLOADER: Callable[[str, str, Path, str | None], None] = _fetch_file


def set_downloader(downloader: Callable[[str, str, Path, str | None], None]
                   ) -> Callable[[str, str, Path, str | None], None]:
    """Swap what downloads a file, for a test; returns the one it replaced."""

    global DOWNLOADER
    previous, DOWNLOADER = DOWNLOADER, downloader

    return previous


def _add_to_lmstudio(path: Path,
                     repository: str,
                     lmstudio_dir: Path,
                     say: Callable[[str], None]) -> int:
    """0 when LM Studio lists the file (already, or through a new link); 1 when `lms import` failed."""

    # Checked again after the download: a models folder that is LM Studio's own lists the file as soon as it lands
    if (lmstudio_dir / repository / path.name).exists():
        say(f"  LM Studio has {repository}/{path.name}")
        return 0
    lms = str(LMS_COMMAND) if LMS_COMMAND.is_file() else shutil.which("lms")
    if lms is None:
        say(f"  LM Studio's lms command was not found: open LM Studio once, then run this again to link {path.name}")
        return 1
    finished = subprocess.run([lms, "import", str(path), "--symbolic-link", "--user-repo", repository, "--yes"],
                              capture_output=True, text=True)
    say(f"  linked {path.name} into LM Studio" if finished.returncode == 0
        else f"  lms import failed for {path.name}: {(finished.stderr or finished.stdout).strip()[-500:]}")

    return 0 if finished.returncode == 0 else 1


def set_up_models(token: str | None,
                  models_dir: Path,
                  lmstudio_dir: Path,
                  say: Callable[[str], None]) -> int:
    """0 when every recommended model is downloaded and listed by LM Studio, 1 when a step failed."""

    failures = 0
    for model in RECOMMENDED_MODELS:
        say(f"{model.repository} (used by {model.used_by})")
        folder = models_dir / model.repository
        for file_name in model.files:
            path = folder / file_name
            in_lmstudio = lmstudio_dir / model.repository / file_name
            if in_lmstudio.exists():
                # LM Studio holds it already (downloaded by LM Studio itself, or linked earlier): nothing to download
                say(f"  LM Studio has {model.repository}/{file_name}")
                continue
            if path.is_file():
                say(f"  already downloaded: {path}")
            else:
                say(f"  downloading {file_name} ...")
                try:
                    DOWNLOADER(model.repository, file_name, folder, token)
                except OSError as exc:
                    # a network or Hugging Face error (HfHubHTTPError is an OSError): reported, the next file is
                    # tried, and the exit code says it failed
                    say(f"  download failed: {exc}")
                    failures += 1
                    continue
            failures += _add_to_lmstudio(path, model.repository, lmstudio_dir, say)

    return 1 if failures else 0
