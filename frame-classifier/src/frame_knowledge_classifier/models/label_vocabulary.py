"""The labels a frame can have in each task, and the four outcomes of a decision against its label."""

# The knowledge task: does the frame carry knowledge at all?
KEEP = "keep"   # the frame carries knowledge: a slide, code, a document, a board with writing
DROP = "drop"   # the frame carries none: only people, an audience, a gallery, a blank or unreadable screen
LABELS = (KEEP, DROP)

# The text-only task: can an OCR carry everything the frame holds?
TEXT_ONLY = "text-only"             # only printed text, with at most things of no value: a plain line, black edges
                                    # from lighting or cropping, small emojis, a logo, a footer
NOT_TEXT_ONLY = "not-text-only"     # something an OCR loses: a diagram, a chart, a table, a photo, a UI, colours or
                                    # highlights that mean something, handwriting, or no text at all

# The tags task, one model for both questions: each frame gets one of three tags (data/<dataset>/tags.csv)
PEOPLE = "people"   # nothing a reader can learn: people, a gallery, a call window, a blank or unreadable screen
TEXT = "text"       # knowledge that is all text an OCR reads, with things of no value beside it
OTHER = "other"     # knowledge an OCR of the text would lose: a diagram, a table, a photo, a UI, meaningful colour
BOARD = "board"     # a task board or a ticket tracker's view (Jira and alike): cards in status columns, a backlog,
                    # a ticket open with its fields; knowledge that video-to-knowledge hands on as changes
TAGS = (PEOPLE, TEXT, OTHER, BOARD)

# A decision against the label, the label the program acts on counted as positive: TP acted on and labelled so, FN
# labelled so but left to the model, FP acted on but labelled for the model (knowledge lost), TN left to the model and
# labelled so
OUTCOMES = ("TP", "FN", "FP", "TN")
