"""38c3-fault-injection's chapter 5 (2026-11-05): the section on the authentication logic wrote its slides' titles,
"### Reversing", "Reversing auth logic" and "Reversing more auth logic" in notes F31.4, F32.5 and F33.4, as its own
`####` subheadings, which no check refused (DEFECTS 655). Made data in the chapter's shape, its notes as the request
hands them, through the real chapter step (`chain_dispatch_service._write_chapter`) on a fixture run: the stored
section holds the titles as bold lines and keeps the chapter's own heading."""

from support import frame_batch_fixtures
from support import harness

from models.attempt_request import AttemptRequest
from services import chain_dispatch_service, document_file_service

NOTES = [{"id": "F31.4", "kind": "markdown", "text": "### Reversing\n- Found the main app logic\n  - Huge state machine"},
         {"id": "F33.4", "kind": "markdown", "text": "### Reversing more auth logic\n- 16 byte buffer from TEA"},
         {"id": "F33.1", "kind": "facts", "text": "The buffer is the TEA of the address and a fixed value."}]
DRAFT = """### Authentication logic (T05.1)

#### Reversing

- Found the main app logic
  - Huge state machine
[F31.4]

The buffer is the TEA of the address and a fixed value [F33.1].

#### Reversing more auth logic

- 16 byte buffer from TEA
[F33.4]
"""


def test_chapter_5_s_slide_titles_are_stored_as_bold_lines_defect():
    # Defect: slide titles became the chapter's subheadings. Fix: the chapter step writes them as bold lines
    ctx = frame_batch_fixtures.load_run_context(count=1)
    request = AttemptRequest(job_kind="chapter", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "chapter-05-text",
                                     "sections": [{"slug": "heading-009-1", "topicId": "T05.1", "entries": NOTES,
                                                   "entryIds": [note["id"] for note in NOTES]}]})
    chain_dispatch_service._write_chapter(request, {"markdown": DRAFT})
    section = document_file_service.build_section_path(ctx.paths, "heading-009-1").read_text(encoding="utf-8")
    harness.check("the slides' titles are bold lines",
                  "**Reversing**" in section and "**Reversing more auth logic**" in section and "####" not in section,
                  section)
