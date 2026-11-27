"""`classifier filter DIR --out FILE --task tags [--vectors-out FILE]`: the one command phase 2 runs, tagging every frame
of a folder with the weights in FRAME_CLASSIFIER_WEIGHTS_DIR, through the inference modules copied from the project the
classifier is trained in (`copy_from_training_project.sh`)."""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path

import numpy

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.label_vocabulary import TAGS
from frame_knowledge_classifier.models.task_record import TAGS_CLASSIFIER_NAME, TAGS_TASK_NAME
from frame_knowledge_classifier.services import embedding_service, filter_service


def _parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="classifier", description="tag every frame of a folder")
    commands = parser.add_subparsers(dest="command", required=True)
    decide = commands.add_parser("filter", help="tag every frame of a folder with the saved tags classifier")
    decide.add_argument("folder", type=Path)
    decide.add_argument("--out", type=Path, required=True)
    decide.add_argument("--task", choices=[TAGS_TASK_NAME], required=True)
    decide.add_argument("--classifier", default=TAGS_CLASSIFIER_NAME)
    decide.add_argument("--vectors-out", type=Path, default=None)
    # the frames' vectors from a model server, handed over by the program: no image model is loaded
    decide.add_argument("--vectors-in", type=Path, default=None)

    return parser.parse_args(arguments)


def _read_given_vectors(vectors_row_by_name: dict[str, numpy.ndarray],
                        paths: list[Path]) -> numpy.ndarray:
    """The vectors the program handed over for each of `paths`, in order; a frame it gave none for is named."""

    missing = [path.name for path in paths if path.name not in vectors_row_by_name]
    if missing:
        raise SystemExit(f"the program gave no vectors for {', '.join(missing)}")

    return numpy.stack([vectors_row_by_name[path.name] for path in paths]) if paths else numpy.zeros((0, config.EMBEDDING_SIZE))


def main(arguments: list[str]) -> int:
    parsed = _parse_arguments(arguments)
    if parsed.vectors_in is not None:
        given = numpy.load(parsed.vectors_in, allow_pickle=False)
        embedding_service.set_embedder(functools.partial(_read_given_vectors,
                                                         dict(zip(given["frames"].tolist(), given["vectors"]))))
    # the copied modules cache beside themselves; here the program keeps that cache with its own, not in this folder
    config.CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "frame-classifier"
    entries = filter_service.tag_folder(parsed.folder, parsed.classifier, parsed.out, parsed.vectors_out)
    print(f"{len(entries)} frames: " + ", ".join(f"{sum(1 for entry in entries if entry['tag'] == tag)} {tag}"
                                                 for tag in TAGS) + f" -> {parsed.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
