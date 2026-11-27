"""The figures phase 5 removes from a finished document with no model, read from the figure files beside it: a figure
that shows what an earlier one shows, and a figure that masks hide past the share a reader can use. The rest of the
audit is text, in `utils/document_audit_utils.py`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy

from models import output_contract
from services import text_detection_service
from utils import document_audit_utils
from utils import frame_region_utils

__all__ = ["find_figures_to_remove"]

_LOG = logging.getLogger(__name__)


def _read_figure_masks(folder: Path) -> dict[int, dict]:
    """Figure number -> its cut's `masks` and `maskedAreas`, from the folder's provenance; empty when it has none."""

    try:
        images = json.loads((folder / output_contract.DOCUMENT_PROVENANCE_FILE).read_text(encoding="utf-8")).get("images") or []
    except (OSError, ValueError):
        return {}

    return {int(image.get("figure") or 0): image for image in images}


def _compute_masked_share(image: numpy.ndarray,
                          record: dict | None) -> float:
    """The share of `image` its cut's masks cover, from the rectangles the mask tool recorded: a figure with no mask
    hides nothing, however dark it is. Pure black pixels were counted instead, and eight dark terminals and slides with
    no mask on them were removed as 30% to 70% masked (DEFECTS 400). A folder published before the areas were kept falls
    back to the pure black pixels, for a figure its record says is masked."""

    if record is not None and not int(record.get("masks") or 0):
        return 0.0
    areas = [area["rect"] for area in (record or {}).get("maskedAreas") or []]
    if areas:
        return frame_region_utils.compute_masked_share(image.shape[1], image.shape[0], areas)

    return float((image.max(axis=2) == 0).mean()) if image.ndim == 3 else float((image == 0).mean())


def _find_earlier_twin(image: numpy.ndarray,
                       words: set[str] | None,
                       kept: list[tuple[int, numpy.ndarray, set[str] | None]]) -> int | None:
    """The number of the first kept figure that `image` repeats, or None. A repeat looks alike and reads alike, as phase
    4's check judges it: two tables of one slide template with other words in every cell differ in under 30% of their
    pixels, and one was removed as the other."""

    for earlier, earlier_image, earlier_words in kept:
        difference = frame_region_utils.compute_content_difference(image, earlier_image)
        if (difference is not None and difference < frame_region_utils.REPEATED_FIGURE_SHARE
                and text_detection_service.is_same_wording(words, earlier_words)):
            return earlier

    return None


def find_figures_to_remove(folder: Path,
                           body: str) -> dict[int, str]:
    """Figure number -> why it goes, for each figure the document places in `folder` that repeats an earlier placed
    figure or is masked past `frame_region_utils.MOST_MASKED_SHARE`. The first of two repeats stays."""

    reasons: dict[int, str] = {}
    kept: list[tuple[int, numpy.ndarray, set[str] | None]] = []
    masks = _read_figure_masks(folder)
    for number, file in document_audit_utils.list_figures(body):
        image = cv2.imread(str(folder / file))
        if image is None:
            _LOG.warning("figure %d: %s could not be read, so it was not checked", number, file)
            continue
        share = _compute_masked_share(image, masks.get(number))
        if share > frame_region_utils.MOST_MASKED_SHARE:
            reasons[number] = f"masks hide {share:.0%} of it"
            continue
        words = text_detection_service.read_figure_words(folder / file)
        twin = _find_earlier_twin(image, words, kept)
        if twin is not None:
            reasons[number] = f"it shows what figure {twin} shows"
            continue
        kept.append((number, image, words))

    return reasons
