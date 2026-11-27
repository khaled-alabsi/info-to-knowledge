"""The system prompt a PDF run's phase 4 writer gets, built from the real instruction files (DEFECTS 515): PDF run 2's
operator manual had its command written "> **Action:** Use the following syntax to create the key", which the
closing lists as something assigned. Through the real builder, a PDF's writer is told a step the document gives its
reader is never an action, beside write.md's own rule for the action line."""

from support import harness
from prompts import write_messages


def test_the_pdf_writer_s_prompt_says_an_instruction_is_no_action_defect():
    # A manual's instruction was marked as an action item; the PDF lines now say a reader's step is never one
    pdf = write_messages.build_write_system_text("knowledge", [], has_frames=True, is_pdf=True)
    video = write_messages.build_write_system_text("knowledge", [], has_frames=True, is_pdf=False)
    harness.check("write.md's action line is in both", "> **Action:** <who does what, by when>" in pdf
                  and "> **Action:** <who does what, by when>" in video, "")
    harness.check("the PDF prompt says a reader's step is never an action", "never as an action" in pdf, pdf[-1500:])
    harness.check("a video's prompt does not", "never as an action" not in video, "")
