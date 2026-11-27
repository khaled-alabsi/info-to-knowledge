"""The redundancy filter's one call per batch of frames: the frames in time order, each
named by its id and sent as its image, and the request to decide each one.

The SYSTEM text is data, in `agent-context/instructions/frame_redundancy.md`.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from prompts import system_prompt_builder
from utils import frame_message_utils

__all__ = ["build_frame_redundancy_messages"]

FRAME_REDUNDANCY_PHASE = "frame_redundancy"


def build_frame_redundancy_messages(frames: list[tuple[str, Path]],
                                    frame_width: int) -> list[BaseMessage]:
    """The system message of `frame_redundancy.md`, then every frame of the batch as `Frame F012:` and its image, scaled
    down to `frame_width`, and the request to decide each of them."""

    parts: list[dict] = [{"type": "text", "text": "Frames to decide, in time order:"}]
    for frame_id, path in frames:
        parts.append({"type": "text", "text": f"Frame {frame_id}:"})
        parts.append(frame_message_utils.encode_frame(path, max_width=frame_width))
    parts.append({"type": "text", "text": f"Decide each of: {', '.join(frame_id for frame_id, _ in frames)}."})

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(FRAME_REDUNDANCY_PHASE)),
            HumanMessage(content=parts)]
