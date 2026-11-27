"""What `setup-mlx` reads from and does to the machine, as values and callables, so a test hands in its own."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["MlxSetupContext"]


@dataclass(frozen=True)
class MlxSetupContext:
    """The machine `setup-mlx` works on."""

    run: Callable[[list[str], bool], tuple[int, str]]   # (arguments, is_capturing) -> (exit code, output)
    fetch_json: Callable[[str], dict]                   # a URL's JSON body; raises OSError when unreachable
    ask: Callable[[str], str] | None                    # a yes/no question to the person; None without a terminal
    find_pythons: Callable[[], list[Path]]              # every Python interpreter found on the machine
    say: Callable[[str], None]                          # one console line
    brew: Path | None                                   # Homebrew, when installed
    system: str                                         # platform.system()
    machine: str                                        # platform.machine()
    macos_version: tuple[int, int]                      # (major, minor); (0, 0) off macOS
