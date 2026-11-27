"""SigLIP 2 B/16 describes each frame as 3,840 numbers; frames that look alike get numbers that are alike.

The model is Google's, used as it is: only the image goes in, no text. It sees five images of each frame, the whole
frame and its four quarters, each shrunk to 224 x 224, and gives 768 numbers for each; a quarter shows twice the
detail, so writing that is readable only at full size still shows. Each dataset's numbers are cached in
`.cache/<dataset>.npz` with each frame's file size and modification time, and a frame is embedded again only when its
file changed or the cache holds numbers of another width. `set_embedder` replaces the model for the tests.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import numpy
from PIL import Image

from frame_knowledge_classifier import config

# frame paths -> one row of EMBEDDING_SIZE numbers per frame: FRAME_PARTS parts of IMAGE_EMBEDDING_SIZE numbers,
# each part of length 1
Embedder = Callable[[list[Path]], numpy.ndarray]


@lru_cache(maxsize=1)
def _load_siglip():
    """The model, its image preprocessing and the device, loaded once per process (about 375 MB the first time)."""

    import open_clip  # noqa: PLC0415  (heavy; the tests never load it)
    import torch  # noqa: PLC0415

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(config.EMBEDDING_ARCHITECTURE,
                                                                 pretrained=config.EMBEDDING_WEIGHTS, device=device)
    model.eval()

    return model, preprocess, device


def split_into_parts(image: Image.Image) -> list[Image.Image]:
    """The whole frame, then its four quarters: top left, top right, bottom left, bottom right."""

    width, height = image.size
    quarters = [image.crop((column * width // 2, row * height // 2, (column + 1) * width // 2, (row + 1) * height // 2))
                for row in range(2) for column in range(2)]

    return [image, *quarters]


def embed_with_siglip(paths: list[Path]) -> numpy.ndarray:
    import torch  # noqa: PLC0415

    model, preprocess, device = _load_siglip()
    rows = []
    with torch.no_grad():
        for path in paths:
            parts = split_into_parts(Image.open(path).convert("RGB"))
            embeddings = model.encode_image(torch.stack([preprocess(part) for part in parts]).to(device))
            embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
            rows.append(embeddings.float().cpu().numpy().reshape(-1))

    return numpy.stack(rows) if rows else numpy.zeros((0, config.EMBEDDING_SIZE))


EMBEDDER: Embedder = embed_with_siglip


def set_embedder(embedder: Embedder) -> Embedder:
    """Replaces the model and returns the one it replaced, so a test can put it back in `finally`."""

    global EMBEDDER
    previous = EMBEDDER
    EMBEDDER = embedder

    return previous


def _stamp(path: Path) -> str:
    status = path.stat()

    return f"{path.name}:{status.st_size}:{status.st_mtime_ns}"


def embed_dataset_frames(dataset_name: str,
                         paths: list[Path]) -> numpy.ndarray:
    """The embedding of every frame in `paths` (all of one dataset), in their order, from the cache where it holds
    an unchanged copy of the frame, else from the model."""

    cache_path = config.CACHE_DIR / f"{dataset_name}.npz"
    cached: dict[str, numpy.ndarray] = {}
    if cache_path.exists():
        stored = numpy.load(cache_path, allow_pickle=False)
        if stored["embeddings"].shape[1] == config.EMBEDDING_SIZE:
            cached = dict(zip(stored["stamps"].tolist(), stored["embeddings"]))
    stamps = [_stamp(path) for path in paths]
    missing = [path for path, stamp in zip(paths, stamps) if stamp not in cached]
    if missing:
        cached.update(zip([_stamp(path) for path in missing], EMBEDDER(missing)))
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        kept = [stamp for stamp in stamps]
        numpy.savez(cache_path, stamps=numpy.array(kept), embeddings=numpy.stack([cached[stamp] for stamp in kept]))
    if not paths:
        return numpy.zeros((0, config.EMBEDDING_SIZE))

    return numpy.stack([cached[stamp] for stamp in stamps])


def embed_folder(paths: list[Path]) -> numpy.ndarray:
    """The embedding of every frame in `paths`, with no cache: for a folder outside the datasets."""

    return EMBEDDER(paths)
