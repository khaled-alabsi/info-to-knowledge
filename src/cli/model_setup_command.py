"""`setup-models [--hf-token TOKEN]`: download the recommended models into this repository and link them into LM
Studio, and the scanned-page reader Unlimited-OCR-2 when `pdf.ocrEngine` names it. The steps live in
`services/model_setup_service.py` and `services/unlimited_ocr_reader_service.py`; this module only reads the command
line."""

from __future__ import annotations

import argparse
import os

import config
from services import model_setup_service
from services import pdf_source_service
from services import unlimited_ocr_reader_service

__all__ = ["add_setup_models_arguments", "cmd_setup_models"]


def add_setup_models_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hf-token", dest="hf_token", default=None,
                        help=f"a Hugging Face token, needed only for a gated model; {config.HF_TOKEN_ENV_VAR} when not given")


def cmd_setup_models(args: argparse.Namespace) -> int:
    """0 when every recommended model is downloaded and LM Studio lists it, and, while `pdf.ocrEngine` is
    "unlimited-ocr", its tool is built and its model downloaded (3.9 GB, too large for git); 1 when a step failed."""

    exit_code = model_setup_service.set_up_models(args.hf_token or os.environ.get(config.HF_TOKEN_ENV_VAR),
                                                  model_setup_service.MODELS_DIR,
                                                  model_setup_service.LMSTUDIO_MODELS_DIR, print)
    if config.settings.pdf.ocr_engine != pdf_source_service.UNLIMITED_OCR_ENGINE:
        return exit_code
    if unlimited_ocr_reader_service.is_installed():
        print("Unlimited-OCR-2 (scanned PDF pages): already installed")
        return exit_code
    print("Unlimited-OCR-2 (scanned PDF pages, pdf.ocrEngine): building its tool and downloading its model")
    is_set_up = unlimited_ocr_reader_service.set_up(print) == 0

    return exit_code if is_set_up else 1
