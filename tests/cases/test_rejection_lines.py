"""A rejected submit prints its reasons in the console log, under its line (the owner, 2026-09-29)."""

from support import harness

from services import write_conversation_service
from utils import finding_text_utils


def test_a_rejection_prints_its_attempt_and_one_line_per_finding():
    lines = write_conversation_service.render_rejection_lines(
        [{"code": "DOCUMENT_ENTRY_UNCOVERED", "detail": "heading-001-1: no block of the section uses F4.4",
          "fix": "add it"},
         {"code": "DOCUMENT_IMAGE_INVALID", "detail": "heading-002-1:   an image is not usable", "fix": "cut first"}], 2, 3)
    harness.check("the attempt comes first", lines[0] == "  rejected, attempt 2 of 3:", str(lines))
    harness.check("each finding has its code and its reason on one line",
                  lines[1] == "    DOCUMENT_ENTRY_UNCOVERED: heading-001-1: no block of the section uses F4.4"
                  and lines[2] == "    DOCUMENT_IMAGE_INVALID: heading-002-1: an image is not usable", str(lines))


def test_a_finding_is_read_whether_it_is_a_dict_or_an_object():
    class Finding:
        code = "DOCUMENT_IMAGE_INVALID"
        detail = "an image is not usable"

    harness.check("the shared reader takes a dict", finding_text_utils.read_field({"code": "A"}, "code") == "A")
    harness.check("and an object", finding_text_utils.read_field(Finding(), "detail") == "an image is not usable")


def test_an_accepted_draft_headings_or_chapter_file_is_never_written_again_on_a_resume():
    """The owner, 2026-09-29: "once a json file is written, in resume we dont write it again". Only the summary, which summarises
    the sections, is tied to its inputs."""

    from services import document_conversation_service as conversation

    harness.check("only the summary is input-bound", tuple(conversation._INPUT_BOUND_FOLLOW_UP_KINDS) == ("summary",),
                  str(conversation._INPUT_BOUND_FOLLOW_UP_KINDS))


def test_a_chapter_answer_is_cut_at_each_headings_own_line():
    from utils import section_markdown_utils as cut

    text = ("### First heading (T01.1)\n\nOne paragraph. [F1.1]\n\n#### A subheading\n\nMore. [F1.2]\n\n"
            "### Second heading (T01.2)\n\nTwo. [F2.1]\n")
    parts = cut.split_chapter_markdown(text)
    harness.check("two headings give two parts, and a level 4 subheading stays inside its part",
                  len(parts) == 2 and "#### A subheading" in parts[0] and parts[1] == "Two. [F2.1]", str(parts))
    harness.check("text with no heading line is one part, which the count then refuses by number",
                  len(cut.split_chapter_markdown("Just one block. [F1.1]")) == 1)
    harness.check("what stands before the first heading line goes to the first heading",
                  cut.split_chapter_markdown("Intro. [F0.1]\n\n### A (T01.1)\n\nBody. [F1.1]\n")[0].startswith("Intro."))
    chatter = "To fix the rejection, I write every id.\nHere is the chapter:\n\n### A (T01.1)\n\nBody. [F1.1]\n"
    harness.check("the worker talking to itself before the first heading, naming no entry id, is not read",
                  cut.split_chapter_markdown(chatter)[0] == "Body. [F1.1]", str(cut.split_chapter_markdown(chatter)))
    # 2026-10-02: a chapter answer wrapped whole in "```markdown" gave its first paragraph the fence line and its last
    # heading an empty block, both refused as unsourced
    fenced = cut.split_chapter_markdown("```markdown\n### A (T01.1)\n\nBody. [F1.1]\n\n### B (T01.2)\n\nTwo. [F2.1]\n```\n")
    harness.check("a fence around the whole answer is taken off", fenced == ["Body. [F1.1]", "Two. [F2.1]"], str(fenced))


def test_a_resume_on_the_conversation_path_is_an_earlier_life_and_clears_nothing():
    from graph.nodes import write_document_node
    from services import document_file_service
    from support import fixtures as fx

    ctx = type("Ctx", (), {"store": type("Store", (), {"count_attempts": lambda self, *a: 0})(), "paths": fx.make_paths()})()
    harness.check("with no record of the agenda there is no earlier life", not write_document_node._has_earlier_life(ctx))
    document_file_service.write_follow_up_record(ctx.paths, write_document_node.PARTS_SLUG,
                                                 {"status": "accepted", "attempts": 1, "calls": 1, "reason": "", "inputsHash": "x"})
    harness.check("once the agenda follow-up is recorded, a start is a resume and clears nothing",
                  write_document_node._has_earlier_life(ctx))
