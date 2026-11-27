"""Where the project keeps its files, which image model it uses, and how the classifier is trained.

Every path hangs off the project folder. `FRAME_CLASSIFIER_HOME` points the program at another folder with the same
layout, which is how the tests run it on a small copy without touching the real data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("FRAME_CLASSIFIER_HOME", Path(__file__).resolve().parents[2]))
DATA_DIR = PROJECT_DIR / "data"                 # one folder per dataset: frames/, a labels file per task, dataset.json
# trained classifiers: <name>.npz (weights) and <name>.json (what it was trained on). `FRAME_CLASSIFIER_WEIGHTS_DIR`
# reads them from another folder: video-to-knowledge keeps the copy it runs with in its own weights/frame-classifier/
CLASSIFIERS_DIR = Path(os.environ.get("FRAME_CLASSIFIER_WEIGHTS_DIR", PROJECT_DIR / "classifiers"))
REPORTS_DIR = PROJECT_DIR / "reports"           # evaluation reports and the label page
CACHE_DIR = PROJECT_DIR / ".cache"              # frame embeddings, rebuilt from the frames when missing
# what a person corrected in a Refine proposal on a video-to-knowledge run page, one folder per run: frames/,
# labels.csv and corrections.json. `classifier add-corrections` turns them into datasets
CORRECTIONS_DIR = PROJECT_DIR / "corrections"
SCENES_DIR = PROJECT_DIR / "scenes"             # the made-up meeting scenes: their HTML pages and the faces they use

# The image model: open_clip's name and weights for SigLIP 2 B/16, made by Google, used as it is
EMBEDDING_ARCHITECTURE = "ViT-B-16-SigLIP2"
EMBEDDING_WEIGHTS = "webli"
IMAGE_EMBEDDING_SIZE = 768      # the numbers SigLIP 2 B/16 gives one image
# A frame is described as five images: the whole frame, then its four quarters (top left, top right, bottom left,
# bottom right), each shrunk to 224 x 224 on its own, so a quarter shows twice the detail. Writing on a board or a
# screen that is readable only at full size is lost in the whole frame alone: in the fair test on 2,423 frames the
# quarters raised the drop frames removed with no knowledge lost from 50 to 122, and to 181 with regularisation 3e-3
# (`research/notes.md`)
FRAME_PARTS = 5
EMBEDDING_SIZE = IMAGE_EMBEDDING_SIZE * FRAME_PARTS


@dataclass(frozen=True)
class TrainingSettings:
    """How the classifier is trained and where its threshold sits; every value here was chosen by the fair test
    (`research/notes.md`, "Decision" and "Update")."""

    # Keeps the weights small so the classifier does not learn single frames by heart. With the quarters, each at the
    # smallest margin with no knowledge lost, on 2,423 frames: 1e-4 removed 122 of 466 drop frames, 1e-3 166, 3e-3 181,
    # 1e-2 124; 6,000 steps at 3e-3 gave the same as 3,000. On the tags task (the one phase 2 runs), leaving each
    # rotation group of the public sets out: 3e-3 dropped 262 of 545 people frames, 1e-3 323, 3e-4 356, all with no frame
    # of knowledge; 1e-4 and 3e-5 360 and one knowledge frame (a stage photo with a readable slide). Lecture rooms seen
    # wide scored 0.42 to 0.58 under 3e-3's threshold of 0.65 and were kept
    regularisation: float = 3e-4
    steps: int = 3000
    learning_rate: float = 0.5
    # Added to the highest score a keep frame got. Stronger regularisation keeps every score nearer 0.5, so the margin
    # shrinks with it: at 3e-3, 0 dropped 18 knowledge frames, 0.05 none while removing 181 of 466 drop frames, 0.1
    # none and 150. Which frames are lost moves with the data, so the margin is set where none is.
    safety_margin: float = 0.05
