"""A sign-in form drawn with an invented e-mail address: the real cut and mask tools hide the address, and the chapter
check reads the masked cut with the real text reader and clears it (DEFECTS 730)."""

import tempfile
from pathlib import Path

import cv2
import numpy
from PIL import Image, ImageDraw, ImageFont

from support import harness

from cases import test_illustration_actions as action_cases
from models import output_contract
from services import document_file_service, illustration_action_service, phase_file_service
from validators import document_validator


def _draw_form() -> numpy.ndarray:
    """A white 1920x1080 form with a label and an invented e-mail address in large black type."""

    picture = Image.new("RGB", (1920, 1080), (255, 255, 255))
    pen = ImageDraw.Draw(picture)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 44)
    pen.text((200, 200), "Sign in", fill=(0, 0, 0), font=font)
    pen.text((200, 400), "jonas@acmesync.example", fill=(0, 0, 0), font=font)
    frame = cv2.cvtColor(numpy.array(picture), cv2.COLOR_RGB2BGR)

    return frame


def test_a_mask_over_an_email_address_clears_the_cut_defect():
    # Defect: a mask over an e-mail address was refused as covering "only words that hold no value".
    # Fix: an e-mail address under a mask counts as a hidden value, so the chapter check clears the cut.
    paths, _ = action_cases._build_run(Path(tempfile.mkdtemp()))
    full = _draw_form()
    video = paths.output_root / "frame-030.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (1920, 1080))
    for _ in range(50):
        writer.write(full)
    writer.release()
    cv2.imwrite(str(paths.output_root / "frames" / "frame-000.jpg"), cv2.resize(full, (960, 540)))
    context = action_cases._build_context(paths, video)
    store = action_cases._build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [50, 50, 910, 340]})
    masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "jonas@acmesync.example"})
    harness.check("the mask over the address is made", masked.is_applied, masked.text)
    document_file_service.write_image_store(paths, store)
    item = {"slug": "frame-000", "index": 0, "timestamp": 1.0, "path": "frames/frame-000.jpg", "record": {
        "screenshots": [{"part": "the add a secret form", "importance": "needed", "why": "it shows the client secret form"}]}}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    document_file_service.write_section(paths, "heading-021-1", 'A form. [F0.1]\n\n![The form](cut-001 "The form")\n[F0.1]\n')
    report = document_validator.validate_section_images(paths, "heading-021-1")
    codes = [finding.code for finding in report.findings]
    harness.check("a mask over an e-mail address clears the cut", "DOCUMENT_IMAGE_SECRET_UNMASKED" not in codes,
                  str([(finding.code, finding.detail) for finding in report.findings]))
