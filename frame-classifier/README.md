# The frame classifier, as phase 2 runs it

Phase 2 tags every frame it takes people, text or other (and board, once a model trained for it is shipped) with the
frame classifier trained in `~/projects/frame-knowledge-classifier/`. This folder holds the part phase 2 needs, so the
repository runs on a machine without that project:

- `src/frame_knowledge_classifier/`: the inference modules, copied byte for byte by `copy_from_training_project.sh`.
  Never edit them here; change them in the training project and copy them again.
- `run_filter.py` and `classifier`: the one command phase 2 runs, `./classifier filter DIR --out FILE --task tags`.
- It runs in the program's own environment (`../.venv`, its packages in `../requirements.txt`); Tesseract comes from
  the system. Its cache of frame readings is kept in `../.cache/frame-classifier/`.
- The weights are in `../weights/frame-classifier/`, handed over as `FRAME_CLASSIFIER_WEIGHTS_DIR`.
- `corrections/` (git-ignored) receives what a person corrects in Refine; copy it to the training project to train on it.
