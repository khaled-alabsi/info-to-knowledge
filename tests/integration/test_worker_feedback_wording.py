"""The worker-feedback file a run writes, built through the real writer from an invented report (a check on a
made-up subject, AcmeSync), opens in the owner's words: a rejection of a right attempt, never of an "answer"."""

import tempfile
from pathlib import Path

from support import harness

from services import worker_feedback_service
from utils import frame_markdown_utils


def test_a_run_s_feedback_file_opens_with_attempt_not_answer_defect():
    # The page showed the file opening "a check that rejected a right answer"; written now, it says "a right attempt"
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    worker_feedback_service.record_report(path, kind="wrong_rejection", summary="AcmeSync's sync rate was refused",
                                          detail="The check read '30 frames per second' as narration.",
                                          where="finalize · summary · attempt 1")
    worker_feedback_service.record_report(path, kind="unclear_instruction", summary="The step rule reads two ways",
                                          detail="", where="frames-001-009")
    text = path.read_text(encoding="utf-8")
    opening = text.split("\n## ")[0]
    harness.check("both entries written", text.count("\n## ") == 2, text)
    harness.check("the opening says a right attempt", "a right attempt" in opening, opening)
    harness.check("the opening never says answer", "answer" not in opening.lower(), opening)


def test_a_batch_reply_s_empty_feedback_reaches_no_file_defect():
    # A batch's reply ended with a `# Feedback` saying "None", which the run's file kept as an entry to judge; read and
    # recorded through the same two steps a run takes, it now leaves the file unwritten, and a real report beside it stays
    reply = ("# Frame 1 · slide · AcmeSync sync settings\n\n## Facts\n- AcmeSync syncs every 30 s.\n\n"
             "# Feedback\n\n## Kind\nother\n\n## Summary\nNone\n\n## Detail\nNo feedback was generated for this batch.\n\n"
             "# Feedback\n\n## Kind\nunclear_instruction\n\n## Summary\nThe build step rule reads two ways\n\n"
             "## Detail\n\"step k of n\" and \"outline whole\" disagree for a scrolled list.\n")
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    for report in frame_markdown_utils.read_feedback(reply):
        worker_feedback_service.record_report(path, kind=report["kind"], summary=report["summary"],
                                              detail=report["detail"], where="frames-001-009")
    text = path.read_text(encoding="utf-8")
    harness.check("only the real report is written", text.count("\n## ") == 1 and "two ways" in text, text)
