"""`chat-document --output <run folder>`: answer what waits in the chat on a run's final document, then end. The server starts
it when a message comes (`document_chat_service`); nothing else needs to."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from models import output_contract
from services import chat_model_service
from services import document_chat_service

__all__ = ["add_chat_document_arguments", "cmd_chat_document"]


def add_chat_document_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)


def cmd_chat_document(args: argparse.Namespace) -> int:
    """0 once no message waits."""

    model = chat_model_service.get_model(output_contract.DOCUMENT_RESHAPING_PHASE)
    paths = document_chat_service.build_paths(args.output_root)
    turns = document_chat_service.answer_waiting_messages(paths, model, args.logger)
    args.logger.info(f"document chat: answered {turns} message(s)")

    return 0
