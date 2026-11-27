"""The two tasks a classifier can be trained for, and what each names: its labels, its files and its score.

Each task splits frames in two: the frames phase 3's model need not read (`skip_label`: dropped, or read by OCR
instead) and the frames it must read (`model_label`). The threshold sits above every frame labelled `model_label`
the classifier trained on, so the costly mistake, a frame the model should read taken away from it, is the FP.
"""

from __future__ import annotations

from dataclasses import dataclass

from frame_knowledge_classifier.models.label_vocabulary import DROP, KEEP, NOT_TEXT_ONLY, TEXT_ONLY


@dataclass(frozen=True)
class TaskRecord:
    name: str               # the `--task` value
    labels_file: str        # each dataset's labels for this task: data/<dataset>/<labels_file>, `frame,label`
    skip_label: str         # the label of a frame the model need not read
    model_label: str        # the label of a frame the model must read
    classifier_name: str    # the classifier `train` saves and `filter` uses when none is named
    score_field: str        # the score's name in the filter's and the evaluation's JSON
    report_name: str        # reports/<report_name>.md and .json, written by `evaluate`
    label_page_name: str    # reports/<label_page_name>, written by `label-page`
    method: str             # "siglip": SigLIP 2's numbers and a logistic regression (classifier_service); "ink-rule":
                            # what is drawn besides the text, measured with Tesseract, and cuts on it (ink_rule_service)

    @property
    def labels(self) -> tuple[str, str]:
        return self.model_label, self.skip_label


KNOWLEDGE_TASK = TaskRecord(name="knowledge", labels_file="labels.csv", skip_label=DROP, model_label=KEEP,
                            classifier_name="current", score_field="dropScore", report_name="evaluation",
                            label_page_name="label.html", method="siglip")
TEXT_ONLY_TASK = TaskRecord(name="text-only", labels_file="text-only-labels.csv", skip_label=TEXT_ONLY,
                            model_label=NOT_TEXT_ONLY, classifier_name="text-only", score_field="textOnlyScore",
                            report_name="evaluation-text-only", label_page_name="label-text-only.html",
                            method="ink-rule")
TASKS = {task.name: task for task in (KNOWLEDGE_TASK, TEXT_ONLY_TASK)}
# The tags task: one model giving each frame people, text or other (tags_service), read from each dataset's tags.csv.
# It has three tags rather than two labels, so it is no TaskRecord; evaluate, train and filter take it by this name
TAGS_TASK_NAME = "tags"
TAGS_CLASSIFIER_NAME = "tags"
