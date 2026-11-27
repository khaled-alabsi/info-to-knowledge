"""The command surface of a chain pipeline. One implementation per command, dispatched from one table.

`main.py` imports `main` from here and calls it. `HANDLERS` is the single dispatch point.

`HANDLERS` maps each command to a handler that takes the parsed arguments. `run` takes one recording and the numbered
phases to run; the other commands act on a run folder, the server or the model settings.

Parsing is separated from dispatch on purpose. `build_parser()` is a pure function of the command
line, so what a line MEANS can be checked without any of it being run.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import config
from cli import blocks_command
from cli import document_chat_command
from cli import inbox_command
from cli import mcp_command
from cli import mlx_setup_command
from cli import model_setup_command
from cli import recording_command
from cli import frame_drop_command
from cli import reset_command
from cli import run_command
from cli import serve_command
from cli import serve_models_command
from cli import setup_commands
from cli import branch_command
from cli import convert_command
from cli import tags_command
from cli import accounts_command
from cli import tidy_command
from cli import tui_command
from models import run_job

__all__ = ["main", "build_parser", "HANDLERS", "QUIET_LOGGERS"]

# Libraries whose INFO lines never reach the console: only their warnings and errors do.
QUIET_LOGGERS = ("httpx",)

HANDLERS = {
    "run": run_command.cmd_run,                                       # drive the graph over one video file
    "inbox": inbox_command.cmd_inbox,                                 # drive it over every new video in the inbox
    "mcp": mcp_command.cmd_mcp,                                       # the program's controls as MCP tools
    "models": setup_commands.cmd_models,                              # which model each phase calls
    "probe": setup_commands.cmd_probe,                                # are the configured model hosts reachable
    "setup-mlx": mlx_setup_command.cmd_setup_mlx,                     # install mlx-vlm in a venv, download a model
    "setup-models": model_setup_command.cmd_setup_models,             # download the recommended models, link them into LM Studio
    "reset-to": reset_command.cmd_reset_to,                           # remove every phase after N from a run folder
    "compress-recording": recording_command.cmd_compress_recording,   # a done run's kept recording, re-encoded to 720p
    "accounts": accounts_command.cmd_accounts,                         # the accounts of i2k serve
    "login": accounts_command.cmd_login,                               # sign in to the running server
    "logout": accounts_command.cmd_logout,                             # sign out of it
    "tags": tags_command.cmd_tags,                                    # the tags the owner gives a run
    "apply-tag-rules": tags_command.cmd_apply_tag_rules,              # every run tagged by the naming rules
    "tag-runs": tags_command.cmd_tag_runs,                            # worker tags for finished runs, one call each
    "branch": branch_command.cmd_branch,                              # a run copied up to a phase, into a new folder
    "convert": convert_command.cmd_convert,                           # a run copied to write it in another ingesting profile
    "recording-to-inbox": recording_command.cmd_recording_to_inbox,   # a done run's recording back to the inbox
    "recording-to-run": recording_command.cmd_recording_to_run,       # a done run's inbox recording into its folder
    "chat-document": document_chat_command.cmd_chat_document,         # answer what waits in the chat on a run's final document
    "reset-chapter": reset_command.cmd_reset_chapter,                # remove chapter N and every later one
    "refine-frames": frame_drop_command.cmd_refine_frames,            # which frames phase 3 has not read hold no knowledge
    "drop-frames": frame_drop_command.cmd_drop_frames,                # drop frames from a run before phase 3 reads them
    "tidy-cuts": tidy_command.cmd_tidy_cuts,                          # the cuts no figure uses, moved into .trash/cuts
    "blocks": blocks_command.cmd_blocks,                              # how often each step of phases 4 and 5 blocked
    "serve": serve_command.cmd_serve,                                 # the web page, its API and the job queue
    "serve-models": serve_models_command.cmd_serve_models,            # the decision model served natively, for providers.decision.host
    "tui": tui_command.cmd_tui,                                      # the terminal UI, talking to that server
}
"""Command name -> handler. Each handler is given the parsed arguments and returns an exit code."""


def _add_shared_flags(parser: argparse.ArgumentParser,
                      is_subcommand: bool) -> None:
    """`-v` and `--profile`, on the main parser with their defaults and on every subcommand with none: a subcommand's
    parser writes its defaults over the main parser's, so a default there would undo a flag given before the
    subcommand."""

    parser.add_argument("-v", "--verbose", dest="is_verbose", action="store_true",
                        default=argparse.SUPPRESS if is_subcommand else False)
    parser.add_argument("--profile", dest="profile_name",
                        default=argparse.SUPPRESS if is_subcommand else os.environ.get(config.PROFILE_ENV_VAR, ""),
                        help=f"a named set of settings read on top of {config.DEFAULT_CONFIG_FILE.name} and "
                             f"{config.LOCAL_CONFIG_FILE.name}: playbook.<name>.json (also {config.PROFILE_ENV_VAR})")


def _build_common_parser() -> argparse.ArgumentParser:
    """A parent parser, so `-v` and `--profile` are accepted after the subcommand as well as before it."""

    shared = argparse.ArgumentParser(add_help=False)
    _add_shared_flags(shared, is_subcommand=True)

    return shared


def build_parser() -> argparse.ArgumentParser:
    """Every command line this playbook accepts, in one place."""

    parser = argparse.ArgumentParser(prog=config.CLI_NAME, add_help=True,
                                     description=f"{config.PLAYBOOK_NAME}: a LangGraph chain pipeline")
    _add_shared_flags(parser, is_subcommand=False)
    common = _build_common_parser()
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", parents=[common],
                                help="run the numbered phases over one video file")
    run_command.add_run_arguments(run)

    inbox = subparsers.add_parser("inbox", parents=[common],
                                  help="run every video in the inbox that has no document yet")
    inbox_command.add_inbox_arguments(inbox)

    mcp = subparsers.add_parser("mcp", parents=[common], help="serve the program's controls as MCP tools over stdio, for an agent")
    mcp_command.add_mcp_arguments(mcp)
    subparsers.add_parser("models", parents=[common], help="which model each phase calls, and every configured model")
    subparsers.add_parser("probe", parents=[common], help="are the configured model hosts reachable")
    setup_mlx = subparsers.add_parser("setup-mlx", parents=[common],
                                      help="install mlx-vlm in a venv of its own unless it is installed, and download a model for it")
    mlx_setup_command.add_setup_mlx_arguments(setup_mlx)
    setup_models = subparsers.add_parser("setup-models", parents=[common],
                                         help="download the recommended models into this repository and link them into LM Studio")
    model_setup_command.add_setup_models_arguments(setup_models)
    reset = subparsers.add_parser("reset-to", parents=[common],
                                  help="remove every phase after N from a run folder: its files and its progress")
    reset_command.add_reset_to_arguments(reset)
    tags = subparsers.add_parser("tags", parents=[common], help="print, add or take off the tags of a run folder")
    tags_command.add_tags_arguments(tags)
    subparsers.add_parser("apply-tag-rules", parents=[common],
                          help="tag every run folder under output/ and knowledge/ by the naming rules (tags.nameRules)")
    subparsers.add_parser("tag-runs", parents=[common],
                          help="give every finished run with no worker tags up to tags.workerTags tags, one model call each")
    accounts = subparsers.add_parser("accounts", parents=[common],
                                     help="list, add, remove the accounts of i2k serve, or change an account's role or password")
    accounts_command.add_accounts_arguments(accounts)
    login = subparsers.add_parser("login", parents=[common], help="sign in to the running i2k serve, for the command line's clients")
    accounts_command.add_login_arguments(login)
    subparsers.add_parser("logout", parents=[common], help="sign out of the running i2k serve")
    convert = subparsers.add_parser("convert", parents=[common],
                                    help="copy a finished run into a new folder to write its document in another ingesting profile")
    convert_command.add_convert_arguments(convert)
    branch = subparsers.add_parser("branch", parents=[common],
                                   help="copy a run into a new folder up to a phase, to run that phase and the later ones again")
    branch_command.add_branch_arguments(branch)
    to_inbox = subparsers.add_parser("recording-to-inbox", parents=[common],
                                     help="move the recording a done run keeps in its folder back to the inbox")
    recording_command.add_recording_arguments(to_inbox)
    to_run = subparsers.add_parser("recording-to-run", parents=[common],
                                   help="keep a done run's inbox recording in its folder, as the run does when it ends")
    recording_command.add_recording_arguments(to_run)
    compress = subparsers.add_parser("compress-recording", parents=[common],
                                     help="re-encode the recording a done run keeps in its folder to 720p, in place")
    recording_command.add_recording_arguments(compress)
    chat_document = subparsers.add_parser("chat-document", parents=[common],
                                          help="answer what waits in the chat on a run's final document, then end")
    document_chat_command.add_chat_document_arguments(chat_document)
    reset_chapter = subparsers.add_parser("reset-chapter", parents=[common],
                                          help="remove chapter N and every later chapter from phase 4, to write them again")
    reset_command.add_reset_chapter_arguments(reset_chapter)
    refine = subparsers.add_parser("refine-frames", parents=[common],
                                   help="list the frames phase 3 has not read that the frame knowledge classifier finds no knowledge on")
    frame_drop_command.add_refine_frames_arguments(refine)
    drop = subparsers.add_parser("drop-frames", parents=[common],
                                 help="drop frames from a run before phase 3 reads them, into frames/dropped/")
    frame_drop_command.add_drop_frames_arguments(drop)
    tidy_cuts = subparsers.add_parser("tidy-cuts", parents=[common],
                                      help="move the cuts no figure uses into the run folder's .trash/cuts")
    tidy_command.add_tidy_cuts_arguments(tidy_cuts)
    blocks = subparsers.add_parser("blocks", parents=[common],
                                   help="how often each step of phases 4 and 5 blocked, was accepted and was reset, and why")
    blocks_command.add_blocks_arguments(blocks)
    serve = subparsers.add_parser("serve", parents=[common],
                                  help="serve the web page: upload, run, resume, logs and progress")
    serve_command.add_serve_arguments(serve)
    serve_models = subparsers.add_parser("serve-models", parents=[common],
                                         help="serve the decision model d1-2B natively, for providers.decision.host")
    serve_models_command.add_serve_models_arguments(serve_models)
    tui = subparsers.add_parser("tui", parents=[common],
                                help="open the terminal UI: clicks and slash commands over the same server")
    tui_command.add_tui_arguments(tui)

    return parser


def _configure_logging(is_verbose: bool,
                       stream) -> logging.Logger:
    """The one place the level, the format and the stream are set, and the playbook's own logger."""

    logging.basicConfig(level=logging.DEBUG if is_verbose else logging.INFO, format="%(message)s", stream=stream)
    # A library's routine line is noise on a console read live: the HTTP client announces every request, and the
    # run's own `time` line already says what came back. Its warnings and errors still get through.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return logging.getLogger(config.PLAYBOOK_NAME)


def main(argv: list[str] | None = None) -> int:
    """Parse one command line and dispatch it. What this returns is the process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()

        return 0

    # `mcp` speaks to its agent on standard output, so every line this process logs, the profile's included, goes to
    # standard error
    args.logger = _configure_logging(args.is_verbose, sys.stderr if args.command == "mcp" else sys.stdout)
    # Before any handler, so every phase, every child process and every line printed reads the same settings. A name
    # with no file ends here rather than part-way through a run on the settings the profile was meant to replace.
    name = args.profile_name
    if name:
        try:
            sections = config.load_profile(name)
        except (FileNotFoundError, ValueError) as exc:
            args.logger.error(str(exc))

            return 2
        os.environ[config.PROFILE_ENV_VAR] = name
        args.logger.info("profile %s: %s", name, ", ".join(sections))
    try:
        return HANDLERS[args.command](args)
    except KeyboardInterrupt:
        args.logger.warning("\ninterrupted")

        return run_job.EXIT_INTERRUPTED
