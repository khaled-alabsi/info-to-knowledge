"""The system prompt a phase 4 writer gets for a PDF read as its text, with no figure, built from the real instruction
files (DEFECTS 521): PDF run 4's quarterly report was told "This source is a PDF document" and "This recording has no
frames: it is an audio recording", and its writer weighed one against the other. Through the real builder, such a PDF is
told it has no figure, never that it is an audio recording."""

from support import harness
from prompts import write_messages


def test_the_figureless_pdf_s_prompt_never_calls_it_audio_defect():
    # Two lines of "This recording" contradicted each other for a PDF with no figure
    pdf = write_messages.build_write_system_text("knowledge", [], has_frames=False, is_pdf=True)
    recording = pdf[pdf.index("# This recording"):]
    harness.check("the PDF lines are there", "This source is a PDF document" in recording, recording[:300])
    harness.check("no line calls it an audio recording", "audio recording" not in recording, recording[-600:])
    harness.check("it is told it has no figure", "This document has no figure" in recording, recording[-600:])
