"""Phase 2's screen words: which frames are read again on the recording, and at what size."""

import tempfile
from pathlib import Path
from unittest import mock

import cv2
import numpy

from support import harness

from services import screen_words_service
from services import text_detection_service


def test_a_frame_narrower_than_the_sent_width_is_read_enlarged_by_a_whole_factor_defect():
    # Defect: an SD broadcast stored 720 wide, as recorded, was never read again, and its dialog's small text gave the
    # check 3 lines. Fix: a part narrower than 1,280 pixels is read enlarged by the smallest whole factor reaching it
    folder = Path(tempfile.mkdtemp())
    video = folder / "small.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (720, 576))
    for _ in range(10):
        writer.write(numpy.full((576, 720, 3), 255, numpy.uint8))
    writer.release()
    cv2.imwrite(str(folder / "frame-000.jpg"), numpy.full((576, 720, 3), 255, numpy.uint8))
    item = {"slug": "frame-000", "frameNumber": 5, "path": "frames/frame-000.jpg", "box": [0, 0, 720, 576]}
    widths: list[int] = []

    def read(image, _confidence):
        widths.append(image.shape[1])
        return [{"text": "Refund", "box": [200, 100, 400, 140], "line": 1, "block": 1}]

    with mock.patch.object(text_detection_service, "is_available", return_value=True), \
            mock.patch.object(text_detection_service, "find_words_in_image", side_effect=read):
        [read_item] = screen_words_service.add_screen_words([item], folder, video)
    harness.check("the 720-pixel part is read at twice its width", widths == [1440], str(widths))
    harness.check("its words are kept in the stored frame's pixels",
                  read_item.get("screenWords") == [{"text": "Refund", "box": [100, 50, 200, 70], "line": 1, "block": 1}],
                  str(read_item.get("screenWords")))
