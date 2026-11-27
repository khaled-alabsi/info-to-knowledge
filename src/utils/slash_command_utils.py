"""The terminal UI's slash commands: what each one is, and how a typed line becomes a `SlashCommandRequest`.

The options are `run`'s own flags (`--from`, `--to`, `--phase`, `--dry-run`, `--yes`), so a line
typed here reads like the command line it stands for.
"""

from __future__ import annotations

import shlex

from models import output_contract
from models import slash_command_vocabulary
from models.slash_command_request import SlashCommandRequest
from models.ui_errors import SlashCommandError

__all__ = ["list_suggestions", "parse_slash_command", "parse_phase_number"]


def list_suggestions() -> list[str]:
    """What the input line completes to: each command as `/name `."""

    return [f"/{name} " for name in slash_command_vocabulary.SLASH_COMMANDS]


def parse_slash_command(line: str) -> SlashCommandRequest:
    """`/process 1 3 --from 2 --dry-run` as its name, words and options; an unknown command, an unknown option or
    an option missing its value is refused by name."""

    # STEP 1: split like a shell, so a folder name with spaces can be quoted
    try:
        parts = shlex.split(line.strip())
    except ValueError as exc:
        raise SlashCommandError(f"cannot read {line!r}: {exc}") from exc
    if not parts or not parts[0].startswith("/"):
        raise SlashCommandError("a command starts with /, for example /inbox; /help lists them")
    name = parts[0][1:]
    if name not in slash_command_vocabulary.SLASH_COMMANDS:
        raise SlashCommandError(f"/{name} is no command; /help lists them")

    # STEP 2: sort the rest into words and options
    words, options = [], {}
    remaining = iter(parts[1:])
    for part in remaining:
        if not part.startswith("--"):
            words.append(part)
            continue
        option = part[2:]
        if option in slash_command_vocabulary.FLAG_OPTIONS:
            options[option] = True
        elif option in slash_command_vocabulary.VALUE_OPTIONS:
            value = next(remaining, None)
            # The next option is no value: `--from --dry-run` names no phase
            if value is None or value.startswith("--"):
                raise SlashCommandError(f"--{option} needs a number")
            options[option] = value
        else:
            known_options = slash_command_vocabulary.VALUE_OPTIONS + slash_command_vocabulary.FLAG_OPTIONS
            raise SlashCommandError(f"--{option} is no option; the options are "
                                    + ", ".join(f"--{known}" for known in known_options))

    return SlashCommandRequest(name=name, words=tuple(words), options=options)


def parse_phase_number(text: str | bool | None) -> int | None:
    """A phase number typed as a word or an option's value; None when nothing was typed. Refused unless it is one of
    the numbered phases."""

    if text is None:
        return None
    if not isinstance(text, str) or not text.isdigit() or int(text) not in output_contract.PHASE_FILES:
        raise SlashCommandError(f"{text!r} is no phase: the phases are "
                                + ", ".join(str(number) for number in sorted(output_contract.PHASE_FILES)))

    return int(text)
