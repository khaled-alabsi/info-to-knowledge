"""One slash command typed into the terminal UI, split into its name, its words and its options."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SlashCommandRequest"]


@dataclass(frozen=True)
class SlashCommandRequest:
    """`/process 1 3 --from 2 --dry-run` is name `process`, words `("1", "3")`, options
    `{"from": "2", "dry-run": True}`."""

    name: str
    words: tuple[str, ...] = ()
    options: dict[str, str | bool] = field(default_factory=dict)
