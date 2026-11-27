"""Made data through phase 4's real chapter check, in the shape 38c3 talk 797's chapter 1 showed (DEFECTS 546): a
heading passed beside an earlier answer's other heading, which covered one of its notes; the next draft moved that note
into it and broke it another way, the passed heading was put back beside the new other heading, and the check named the
note as covered nowhere instead of the draft's own fault. Now the put-back is undone and the draft is told what it wrote
wrong."""

from langchain_core.messages import AIMessage

from support import harness

from cases import test_write_follow_up_loop as loop
from models.follow_up_spec import FollowUpSpec
from services import call_log_service
from services import document_file_service
from services import write_conversation_service

_ENTRIES = [{"id": "F0.1", "kind": "facts", "text": "The partner portal gives partners one sign-in."},
            {"id": "F0.2", "kind": "facts", "text": "The one sign-in also covers the contracts view."}]


def test_a_put_back_heading_does_not_hide_the_draft_s_own_fault_defect():
    # The check named a note the put-back left uncovered; the draft's own uncited block went unnamed
    session = loop._build_session()
    spec = FollowUpSpec(submit_tool="submit_chapter", slug="chapter-01-text", position="chapter 1", text="Write chapter 1.",
                        inputs={"sections": [
                            {"slug": "heading-001-1", "heading": "Portal sign-in", "topicId": "T01.1",
                             "entryIds": ["F0.1"], "requiredIds": ["F0.1"], "entries": _ENTRIES[:1]},
                            {"slug": "heading-002-1", "heading": "Contracts view", "topicId": "T01.2",
                             "entryIds": ["F0.2"], "requiredIds": ["F0.2"], "entries": _ENTRIES[1:]}]})
    paths = session.ctx.paths
    old_first, old_second = ("Partners sign in once, for every view. [F0.1, F0.2]\n",
                             "The portal keeps one sign-in for its views. [F0.1]\n")
    document_file_service.write_section(paths, "heading-001-1", old_first)
    document_file_service.write_section(paths, "heading-002-1", old_second)
    document_file_service.write_chapter_submission(paths, "chapter-01-text", {"sections": 2,
                                                                              "headingIds": ["T01.1", "T01.2"]})
    old_answer = f"### Portal sign-in (T01.1)\n\n{old_first}\n### Contracts view (T01.2)\n\n{old_second}"
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "chapter-01-text"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": old_answer},
                                                  "finishReason": "stop"}})
    draft = ("### Portal sign-in (T01.1)\n\nThe partner portal gives partners one sign-in. [F0.1]\n\n"
             "### Contracts view (T01.2)\n\nThat sign-in also covers the contracts view. [F0.2]\n\n"
             "Its page is the same for every partner.\n")
    result = write_conversation_service._check_draft(session, spec, {"markdown": draft}, AIMessage(content=""))
    harness.check("the draft is told of its own uncited block", "DOCUMENT_PARAGRAPH_UNSOURCED" in result, result[:600])
    harness.check("and not of the note the put-back left uncovered", "uses F0.2" not in result, result[:600])
