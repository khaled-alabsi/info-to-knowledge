"""`i2k setup-models`: every recommended model's files are downloaded into the repository's
models folder unless they are there, with the token given, and a file LM Studio already lists is not linked again.

The downloader is a fake set through `model_setup_service.set_downloader`; the LM Studio folder is a temporary one
that already holds every file, so no `lms` command runs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness

from cli import model_setup_command

from models.recommended_model_vocabulary import RECOMMENDED_MODELS
from services import model_setup_service


def _run_setup(models_dir: Path,
               lmstudio_dir: Path,
               asked: list) -> tuple[int, list[str]]:
    """`set_up_models` with a fake download that writes the file, and its LM Studio link in `lmstudio_dir` as `lms
    import` would, so no real `lms` command runs."""

    def download(repository: str, file_name: str, folder: Path, token: str | None) -> None:
        asked.append((repository, file_name, token))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / file_name).write_bytes(b"gguf")
        (lmstudio_dir / repository).mkdir(parents=True, exist_ok=True)
        (lmstudio_dir / repository / file_name).write_bytes(b"link")

    lines: list[str] = []
    previous = model_setup_service.set_downloader(download)
    try:
        return model_setup_service.set_up_models("hf_test", models_dir, lmstudio_dir, lines.append), lines
    finally:
        model_setup_service.set_downloader(previous)


def test_every_recommended_file_is_downloaded_once_with_the_token():
    root = Path(tempfile.mkdtemp())
    asked: list = []
    code, lines = _run_setup(root / "models", root / "lmstudio", asked)
    expected = [(model.repository, file_name, "hf_test") for model in RECOMMENDED_MODELS for file_name in model.files]
    harness.check("every file is downloaded with the token", code == 0 and asked == expected, f"{code} {asked}")
    harness.check("each lands in models/<publisher>/<name>/",
                  all((root / "models" / repository / file_name).is_file() for repository, file_name, _ in expected))
    harness.check("the LFM model and the Qwen MTP model are both recommended",
                  {model.repository for model in RECOMMENDED_MODELS}
                  == {"LiquidAI/LFM2.5-VL-3B-GGUF", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"})
    asked.clear()
    code, lines = _run_setup(root / "models", root / "lmstudio", asked)
    harness.check("a second run downloads nothing", code == 0 and asked == [], str(asked))


def test_a_file_lm_studio_already_holds_is_not_downloaded():
    root = Path(tempfile.mkdtemp())
    lmstudio_dir = root / "lmstudio"
    for model in RECOMMENDED_MODELS:
        for file_name in model.files:
            (lmstudio_dir / model.repository).mkdir(parents=True, exist_ok=True)
            (lmstudio_dir / model.repository / file_name).write_bytes(b"downloaded by LM Studio")
    asked: list = []
    code, lines = _run_setup(root / "models", lmstudio_dir, asked)
    files = sum(len(model.files) for model in RECOMMENDED_MODELS)
    harness.check("nothing is downloaded when LM Studio's folder holds every file", code == 0 and asked == [], str(asked))
    harness.check("and it says LM Studio has each one", sum("LM Studio has" in line for line in lines) == files,
                  "\n".join(lines))
    harness.check("nothing is written into the repository's models folder", not (root / "models").exists())


def test_the_command_takes_the_token_from_its_flag():
    parser = argparse.ArgumentParser()
    model_setup_command.add_setup_models_arguments(parser)
    harness.check("--hf-token is read", parser.parse_args(["--hf-token", "hf_x"]).hf_token == "hf_x")
    harness.check("without it, nothing is set and HF_TOKEN is used", parser.parse_args([]).hf_token is None)


def _models_dir_seen_by_a_process(environment: dict[str, str]) -> str:
    """The models folder a fresh Python process reads from `model_setup_service`, with `environment` added."""

    src = Path(__file__).resolve().parents[2] / "src"
    done = subprocess.run([sys.executable, "-c", "from services import model_setup_service; print(model_setup_service.MODELS_DIR)"],
                          cwd=src, env={**os.environ, **environment, "PYTHONPATH": str(src)}, capture_output=True, text=True,
                          check=True)

    return done.stdout.strip().splitlines()[-1]


def test_the_models_folder_follows_i2k_models_dir_and_defaults_to_the_models_folder_beside_the_inbox():
    with tempfile.TemporaryDirectory() as folder:
        moved = _models_dir_seen_by_a_process({"I2K_MODELS_DIR": folder})
        harness.check("I2K_MODELS_DIR moves the folder the models are downloaded into",
                      Path(moved) == Path(folder).resolve(), moved)
    default = _models_dir_seen_by_a_process({"I2K_MODELS_DIR": ""})
    harness.check("without it the models go into `models/` beside the inbox",
                  Path(default) == Path(__file__).resolve().parents[2] / "models", default)
