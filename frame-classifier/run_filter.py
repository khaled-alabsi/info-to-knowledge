"""`classifier filter DIR --out FILE --task tags [--vectors-out FILE]`: the one command phase 2 runs, tagging every frame
of a folder with the weights in FRAME_CLASSIFIER_WEIGHTS_DIR, through the inference modules copied from the project the
classifier is trained in (`copy_from_training_project.sh`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from frame_knowledge_classifier import config
from frame_knowledge_classifier.models.label_vocabulary import TAGS
from frame_knowledge_classifier.models.task_record import TAGS_CLASSIFIER_NAME, TAGS_TASK_NAME
from frame_knowledge_classifier.services import filter_service


def _parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="classifier", description="tag every frame of a folder")
    commands = parser.add_subparsers(dest="command", required=True)
    decide = commands.add_parser("filter", help="tag every frame of a folder with the saved tags classifier")
    decide.add_argument("folder", type=Path)
    decide.add_argument("--out", type=Path, required=True)
    decide.add_argument("--task", choices=[TAGS_TASK_NAME], required=True)
    decide.add_argument("--classifier", default=TAGS_CLASSIFIER_NAME)
    decide.add_argument("--vectors-out", type=Path, default=None)

    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    parsed = _parse_arguments(arguments)
    # the copied modules cache beside themselves; here the program keeps that cache with its own, not in this folder
    config.CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "frame-classifier"
    entries = filter_service.tag_folder(parsed.folder, parsed.classifier, parsed.out, parsed.vectors_out)
    print(f"{len(entries)} frames: " + ", ".join(f"{sum(1 for entry in entries if entry['tag'] == tag)} {tag}"
                                                 for tag in TAGS) + f" -> {parsed.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
