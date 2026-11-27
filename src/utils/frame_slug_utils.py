"""A frame's slug, the one pattern every reader of a frame by its number builds it with."""

from __future__ import annotations

__all__ = ["build_frame_slug", "read_frame_number"]


def build_frame_slug(index: int) -> str:
    """A frame's slug and file stem, `frame-007`: the frames file, the notes and the cut tool name it so."""

    return f"frame-{index:03d}"


def read_frame_number(slug: str) -> int:
    """The frame's number in its slug, `7` for `frame-007`."""

    return int(slug.rsplit("-", 1)[1])
