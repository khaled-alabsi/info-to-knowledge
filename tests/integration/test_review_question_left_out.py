"""A review todo could not be resolved, and its question asked whether a word of "the notes F5.1 and F5.3" was a misheard
name "on the slide"; the document's text checks refused the answer three times and blocked the run (DEFECTS 709).
Through the real validator of a resolution and the real rendering of the reviewed document, on an invented document:
the answer stands, its question stays out of the open points, and a question about the subject goes in."""

from support import harness
from services import review_conversation_service

DOCUMENT = ("# Gateway review\n\n## Gateway limits\n\nThe Gateway caps each order at 500 units [F2.1].\n\n"
            "## Open points\n\n- Which team owns the refund rules?\n")
REFUSED = 'Is "Gatway" in the notes F5.1 and F5.3 a misheard name for the system on the slide?'
KEPT = "Which Gateway release raises the order cap?"


def test_a_review_question_the_text_checks_refuse_never_reaches_the_open_points_defect():
    # The question that blocked the run is left out of the open points, named by what the checks found; a question
    # about the subject still goes in
    refused, problems = review_conversation_service._build_todo_question({"question": REFUSED})
    harness.check("the refused question is left out, with what the checks found",
                  refused == "" and {"DOCUMENT_ENTRY_ID_IN_TEXT", "DOCUMENT_RECORDING_NARRATED"} <= set(problems),
                  str(problems))
    kept, kept_problems = review_conversation_service._build_todo_question({"question": KEPT})
    harness.check("a question about the subject is kept", kept == KEPT and not kept_problems, str(kept_problems))
    todos = [{"status": "unresolved", "order": 1, "question": refused, "quote": ""},
             {"status": "unresolved", "order": 2, "question": kept, "quote": ""}]
    text = review_conversation_service.render_reviewed_document(DOCUMENT, todos)
    harness.check("the reviewed document's open points hold the subject's question and not the refused one",
                  KEPT in text and "F5.1" not in text and "on the slide" not in text, text)
