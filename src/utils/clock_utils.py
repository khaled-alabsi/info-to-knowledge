"""A moment of the recording as the notes, the reports and the reading tools write it, `mm:ss` or `h:mm:ss`, and read
back from what a model sends. Pure functions."""

from __future__ import annotations

__all__ = ["render_clock", "parse_clock"]


def render_clock(seconds: float) -> str:
    minutes, second = divmod(int(seconds), 60)
    hours, minute = divmod(minutes, 60)

    return f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minute:02d}:{second:02d}"


def parse_clock(text: str) -> float | None:
    """Seconds from `mm:ss`, `h:mm:ss` or plain seconds; None when empty or unreadable."""

    try:
        parts = [float(part) for part in text.strip().split(":")] if text.strip() else []
    except ValueError:
        return None

    return sum(part * 60 ** power for power, part in enumerate(reversed(parts))) if 0 < len(parts) <= 3 else None
