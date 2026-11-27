"""A frame stored shrunk from a large screen keeps the words of its small code font: phase 2 reads them on the
recording at its own size (`screen_words_service`), and phase 3's outline check and completion quote them
(DEFECTS 41, 69). Run on a video made from a rendered scene, a schema table of ten field names in a 9 px code font
at twice the pixels, 2,676 wide, stored at 1,185 as phase 2 stores it."""

import subprocess
import tempfile
from pathlib import Path

import cv2

from support import fixtures as fx
from support import harness

from models import output_contract
from services import phase_file_service
from services import screen_words_service
from services import text_detection_service
from validators import item_validator

SCENE = Path(__file__).resolve().parents[1] / "resources" / "scenes" / "small-monospace-table.png"
NAMES = ("clientId", "displayName", "refundLimit", "approvalGroup", "tokenProvider", "createdAt", "updatedAt",
         "billingRegion", "retryBudget", "auditTrailRef")
STORED_WIDTH = 1185


def _count_names(lines: list[str]) -> int:
    words = {word for line in lines for word in line.split()}

    return sum(name in words for name in NAMES)


def test_a_small_code_font_shrunk_to_the_stored_frame_is_read_at_full_size_defect():
    # The field names of a table in a small code font were not read on the frame stored shrunk, so its rows reached
    # neither the notes nor the completion; phase 2 now reads them on the recording at its own size
    if not SCENE.is_file() or not text_detection_service.is_available():
        print("scene not rendered or the text reader not installed: skipped")
        return
    scene = cv2.imread(str(SCENE))
    height, width = scene.shape[:2]
    folder = Path(tempfile.mkdtemp())
    video = folder / "schema.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(SCENE), "-t", "2", "-r", "5",
                    "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(video)], check=True)
    paths = fx.make_paths()
    frames_dir = paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME
    frames_dir.mkdir(parents=True, exist_ok=True)
    stored = cv2.resize(scene, (STORED_WIDTH, round(height * STORED_WIDTH / width)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(frames_dir / "frame-000.jpg"), stored)
    item = {"slug": "frame-000", "index": 0, "frameNumber": 5, "path": "frames/frame-000.jpg",
            "box": [0, 0, width, height]}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [item]})
    shrunk = _count_names(item_validator.read_screen_lines(paths, "frame-000"))
    [read] = screen_words_service.add_screen_words([item], frames_dir, video)
    harness.check("phase 2 read the frame on the recording", "screenWords" in read, str(sorted(read)))
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": [read]})
    full = _count_names(item_validator.read_screen_lines(paths, "frame-000"))
    harness.check("read on the stored frame, the reader misses field names", shrunk < len(NAMES), str(shrunk))
    harness.check("read at full size, every field name is a line to quote", full == len(NAMES), f"{full} of {len(NAMES)}")
