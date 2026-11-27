"""What a run uses for a setting that has no value of its own, as the Settings page says it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["SettingRunValueResult"]


@dataclass(frozen=True)
class SettingRunValueResult:
    """`used_value` is what a run uses, None when it uses no value (a limit that is not sent, a number found from the
    recording); `note` says where the value comes from, or what happens instead, in a person's words."""

    used_value: Any
    note: str
