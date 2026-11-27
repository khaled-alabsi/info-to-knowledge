"""38c3-fault-injection's chapter 7 (2026-11-05): the talk's "Conclusions" slide, note F54.7, was written as a
markdown fence holding the slide's list, its last line "Got new targets from Chipolo to play with ;)". The ";)" read as
code, so the published document printed the list as a code box, and phase 5's review could not name a wrong line in
it ("a todo cannot quote a fenced block") (DEFECTS 658). The slide's real lines through the real chapter step on a
fixture run: the stored section holds the list as markdown, no fence."""

from support import frame_batch_fixtures
from support import harness

from models.attempt_request import AttemptRequest
from services import chain_dispatch_service, document_file_service
from utils import section_markdown_utils

NOTE = {"id": "F54.7", "kind": "markdown",
        "text": "## Conclusions\n\n- Attackers only need to be lucky once\n- Devices cannot be updated\n"
                "  - Still available for purchasing if you want to try for yourself\n"
                "- Got new targets from Chipolo to play with ;)"}
DRAFT = f"""### Firmware extraction (T07.2)

The device firmware sits in OTP memory and cannot be updated [F54.1].

```markdown
{NOTE["text"]}
```
[F54.7]
"""


def test_the_conclusions_slide_is_stored_as_a_list_defect():
    # Defect: the slide's ";)" kept its list a code box. Fix: a wink ending a line is no code mark
    ctx = frame_batch_fixtures.load_run_context(count=1)
    request = AttemptRequest(job_kind="chapter", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "chapter-07-text",
                                     "sections": [{"slug": "heading-014-1", "topicId": "T07.2", "entries": [NOTE],
                                                   "entryIds": ["F54.1", "F54.7"]}]})
    chain_dispatch_service._write_chapter(request, {"markdown": DRAFT})
    section = document_file_service.build_section_path(ctx.paths, "heading-014-1").read_text(encoding="utf-8")
    blocks = section_markdown_utils.parse_section(section)
    harness.check("the stored section is read as the slide's list, no code box",
                  any(block["kind"] == "list" and "play with ;)" in block["text"] for block in blocks)
                  and all(block["kind"] != "code" for block in blocks), str(blocks))
