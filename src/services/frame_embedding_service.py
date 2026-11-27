"""Describe each frame as numbers by asking a model server for its image vectors, the way the frame classifier's own image
model does in the program: five images of each frame (the whole frame, then its four quarters), one vector for each, every
vector scaled to length 1, the five put one after another as 3,840 numbers."""

from __future__ import annotations

import io
from pathlib import Path

import numpy
from PIL import Image

from models.endpoint_spec import EndpointSpec
from services import endpoint_client

__all__ = ["FRAME_SUFFIXES", "IMAGE_EMBEDDING_SIZE", "FRAME_PARTS", "list_frame_images", "request_frame_vectors",
           "write_frame_vectors"]

FRAME_SUFFIXES = (".jpg", ".jpeg", ".png")                    # the images of a frames folder the classifier reads
IMAGE_EMBEDDING_SIZE = 768                                    # the numbers the image model gives one image
FRAME_PARTS = 5                                               # the whole frame and its four quarters
QUARTER_JPEG_QUALITY = 95                                     # a quarter is cut from the frame and sent as a JPEG


def list_frame_images(frames_dir: Path) -> list[Path]:
    """Give me every frame image of `frames_dir`, sorted by name, as the classifier lists them."""

    return sorted(path for path in frames_dir.iterdir() if path.suffix.lower() in FRAME_SUFFIXES)


def _encode_part(part: Image.Image) -> str:
    buffer = io.BytesIO()
    part.save(buffer, format="JPEG", quality=QUARTER_JPEG_QUALITY)

    return endpoint_client.build_data_url(content=buffer.getvalue(), media_type="image/jpeg")


def _build_part_data_urls(frame_path: Path) -> list[str]:
    """Give me the whole frame, then its top left, top right, bottom left and bottom right quarters, as data URLs."""

    image = Image.open(frame_path).convert("RGB")
    width, height = image.size
    part_list = [image]
    for row in range(2):
        for column in range(2):
            part_list.append(image.crop((column * width // 2, row * height // 2,
                                         (column + 1) * width // 2, (row + 1) * height // 2)))

    return [_encode_part(part=part) for part in part_list]


def request_frame_vectors(endpoint: EndpointSpec,
                          frame_path_list: list[Path]) -> numpy.ndarray:
    """Give me one row of 3,840 numbers for each frame of `frame_path_list`, in that order, asked of the server one frame
    at a time (one request for each of its five images); the error names the frame whose five vectors did not come back right."""

    row_list = []
    for frame_path in frame_path_list:
        data_url_list = _build_part_data_urls(frame_path=frame_path)
        vector_array = numpy.array(endpoint_client.request_embeddings(endpoint=endpoint, data_url_list=data_url_list),
                                   dtype=float)
        if vector_array.shape != (FRAME_PARTS, IMAGE_EMBEDDING_SIZE):
            raise ValueError(f"the server gave {frame_path.name} vectors of shape {vector_array.shape}, not "
                             f"{(FRAME_PARTS, IMAGE_EMBEDDING_SIZE)}: its model is not the frame classifier's image model")
        length_array = numpy.linalg.norm(vector_array, axis=-1, keepdims=True)     # each vector scaled to length 1
        row_list.append((vector_array / length_array).reshape(-1))

    return numpy.stack(row_list)


def write_frame_vectors(endpoint: EndpointSpec,
                        frame_path_list: list[Path],
                        out_path: Path) -> None:
    """Write the vectors the server gives `frame_path_list` into the `.npz` file `out_path` as `frames` (each file's name)
    and `vectors` (one row for each), for the classifier to read instead of loading an image model."""

    vector_array = request_frame_vectors(endpoint=endpoint, frame_path_list=frame_path_list)
    numpy.savez(out_path, frames=numpy.array([path.name for path in frame_path_list]), vectors=vector_array)
