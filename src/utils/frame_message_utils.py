"""A frame as a chat message part. Pure functions.

An OpenAI-compatible tool message carries text only, so a tool's frame reaches the model as a user message right
after the tool's result (REQUIREMENTS.md R10); phases 4 and 5 both send it this way.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import cv2

__all__ = ["encode_frame", "encode_frame_region", "read_frame_size"]

_MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_ENLARGED_JPEG_QUALITY = 95


def _read_resized_bytes(path: Path,
                        min_width: int,
                        max_width: int) -> bytes | None:
    """The frame enlarged to `min_width` or scaled down to `max_width` (0: no bound) as JPEG bytes, or None when its
    width is already within both or it cannot be read."""

    image = cv2.imread(str(path))
    if image is None:
        return None
    width = image.shape[1]
    target = min_width if min_width and width < min_width else max_width if max_width and width > max_width else 0
    if not target:
        return None
    height = round(image.shape[0] * target / width)
    interpolation = cv2.INTER_CUBIC if target > width else cv2.INTER_AREA
    resized = cv2.resize(image, (target, height), interpolation=interpolation)
    is_encoded, buffer = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, _ENLARGED_JPEG_QUALITY])

    return buffer.tobytes() if is_encoded else None


def encode_frame(path: Path,
                 min_width: int = 0,
                 max_width: int = 0) -> dict[str, Any]:
    """The frame at `path` as an `image_url` content part with its bytes inlined; a frame narrower than `min_width`
    is sent enlarged to that width, one wider than `max_width` scaled down to it, as JPEG, and the file is left as it
    is."""

    mime_type = _MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        raise ValueError(f"no image type for {path.name}: a frame is a {', '.join(_MIME_TYPES)} file")
    resized = _read_resized_bytes(path, min_width, max_width) if min_width or max_width else None
    if resized is not None:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(resized if resized is not None else path.read_bytes()).decode("ascii")

    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def read_frame_size(path: Path) -> tuple[int, int] | None:
    """The frame's width and height, or None when it cannot be read."""

    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)

    return None if image is None else (image.shape[1], image.shape[0])


def encode_frame_region(path: Path,
                        box: list[int],
                        min_width: int) -> dict[str, Any]:
    """The part `box` (x0, y0, x1, y1 in the frame's pixels) cut out of the frame at `path` as an `image_url` content
    part, enlarged to `min_width` when narrower, as JPEG; the file is left as it is."""

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"the frame {path.name} cannot be read")
    x0, y0, x1, y1 = box
    region = image[y0:y1, x0:x1]
    width = region.shape[1]
    if min_width and width < min_width:
        region = cv2.resize(region, (min_width, round(region.shape[0] * min_width / width)),
                            interpolation=cv2.INTER_CUBIC)
    is_encoded, buffer = cv2.imencode(".jpg", region, [cv2.IMWRITE_JPEG_QUALITY, _ENLARGED_JPEG_QUALITY])
    if not is_encoded:
        raise ValueError(f"the part {box} of the frame {path.name} cannot be encoded")
    encoded = base64.b64encode(buffer.tobytes()).decode("ascii")

    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}

