"""The pieces phase 4's conversation is built from, before the loop that runs it. REQUIREMENTS.md R12.

The executive summary draft: its own schema with no entry ids, since the opening's moments carry none, and its own
check. The topic ids a link names, which never move when another outline is redone. The `[T08.3]` links a section
writes, found in its text and rendered as links to the topic's heading once every section is written.
"""

import base64
import tempfile
from pathlib import Path

import cv2
import numpy

from support import fixtures as fx
from support import harness

import config as app_config
from models.follow_up_outcome import FollowUpOutcome
from models.submit_tool_spec import SubmitToolSpec
from models.write_read_result import WriteReadResult
from schemas import document_final
from schemas import draft_headings
from schemas import document_chapter
from schemas import executive_summary_draft
from schemas import write_actions
from services import call_log_service
from services import document_file_service
from services import write_conversation_service
from utils import document_plan_utils
from utils import document_text_utils
from utils import frame_message_utils
from utils import tool_call_text_utils
from validators import document_validator

DRAFT = {"working_title": "Refunds are split into at most three parts",
         "sentences": ["A refund can be split into at most three parts.",
                       "Each part is booked as its own payment.",
                       "Who approves a split above a set amount is still open."]}


def _write_draft(paths,
                 draft: dict) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, document_file_service.EXECUTIVE_SUMMARY_DRAFT_SLUG),
                                    {"response": {"outcome": "completed", "parsed": draft, "text": "", "thinking": "",
                                                  "finishReason": "tool_calls"}})
    document_file_service.write_executive_summary_draft(paths, draft)


def _list_codes(paths) -> set[str]:
    return harness.codes(document_validator.validate_executive_summary_draft(paths, 500))


def test_the_draft_schema_asks_for_a_title_and_sentences_and_no_ids():
    schema = executive_summary_draft.ExecutiveSummaryDraft.model_json_schema()
    harness.check("a working title and sentences", set(schema["properties"]) == {"working_title", "sentences"},
                  str(schema["properties"]))
    harness.check("no entry ids anywhere, since the opening's moments carry none", "entry_ids" not in str(schema))


def test_a_good_draft_passes_and_each_defect_has_its_code():
    paths = fx.make_paths()
    _write_draft(paths, DRAFT)
    harness.check("a title and three sentences pass", _list_codes(paths) == set(), str(_list_codes(paths)))

    _write_draft(paths, {**DRAFT, "working_title": " "})
    harness.check("an empty title is DOCUMENT_TITLE_MISSING", _list_codes(paths) == {"DOCUMENT_TITLE_MISSING"},
                  str(_list_codes(paths)))
    _write_draft(paths, {**DRAFT, "working_title": "Clarifies the refund flow and its limits"})
    harness.check("a title saying what the document does is DOCUMENT_TITLE_NOT_CLAIM",
                  _list_codes(paths) == {"DOCUMENT_TITLE_NOT_CLAIM"}, str(_list_codes(paths)))
    _write_draft(paths, {**DRAFT, "sentences": DRAFT["sentences"][:2]})
    harness.check("two sentences is EXECUTIVE_SUMMARY_DRAFT_LENGTH", _list_codes(paths) == {"EXECUTIVE_SUMMARY_DRAFT_LENGTH"},
                  str(_list_codes(paths)))
    _write_draft(paths, {**DRAFT, "sentences": [*DRAFT["sentences"], "The video shows how a split is entered."]})
    harness.check("a banned phrase is caught as in every other text", "DOCUMENT_BANNED_PHRASE" in _list_codes(paths),
                  str(_list_codes(paths)))
    long_sentences = [" ".join(f"w{number}x{index}" for index in range(250)) for number in range(3)]
    _write_draft(paths, {**DRAFT, "sentences": long_sentences})
    harness.check("more words than the summary may have is EXECUTIVE_SUMMARY_DRAFT_LENGTH",
                  _list_codes(paths) == {"EXECUTIVE_SUMMARY_DRAFT_LENGTH"}, str(_list_codes(paths)))


def test_each_follow_up_has_one_submit_tool_with_today_s_schema():
    harness.check("six submit tools, none for one section", set(write_actions.SUBMIT_TOOLS) == {
        "submit_executive_summary_draft", "submit_agenda", "submit_draft_headings", "submit_table_of_contents",
        "submit_chapter", "submit_final"})
    chapter = write_actions.SUBMIT_TOOLS.get("submit_chapter")
    harness.check("a chapter is answered as one markdown, cut at each heading's own line", chapter is not None and chapter.job_kind == "chapter"
                  and chapter.schema is document_chapter.DocumentChapter and set(chapter.schema.model_fields) == {"markdown"})
    reading = write_actions.SUBMIT_TOOLS.get("submit_draft_headings")
    harness.check("the reading of an agenda point takes its own schema: a purpose and subjects with their starts",
                  reading is not None and reading.schema is draft_headings.DraftHeadings
                  and set(draft_headings.DraftHeading.model_fields) == {"title", "first_entry_id", "what"}
                  and reading.job_kind == "draft_headings", str(reading))
    table = write_actions.SUBMIT_TOOLS.get("submit_table_of_contents")
    harness.check("the table of contents names its chapters and their headings, and is judged as a table of contents",
                  table is not None and table.schema.__name__ == "DocumentTableOfContents"
                  and set(table.schema.model_fields) == {"chapters", "set_aside_entry_ids", "not_established"}
                  and table.job_kind == "table_of_contents", str(table))
    harness.check("the final tool adds the title to today's summary",
                  {"title", "summary", "agenda"} <= set(write_actions.SUBMIT_TOOLS["submit_final"].schema.model_fields))
    harness.check("the reading tools are the R12 names and list_batches", set(write_actions.READ_TOOLS) == {
        "list_tags", "read_notes", "read_moments", "find", "read_frame", "read_document", "read_transcript", "note", "list_batches",
        "look", "cut", "mark", "mask", "leave_out", "replace_with_diagram", "undo_cut"})


def test_a_topic_id_is_its_first_outline_and_place_and_survives_a_later_change():
    outlines = [{"topics": [{"title": "Portal", "entry_ids": ["E1"]}, {"title": "Sign-in", "entry_ids": ["E2"]}]},
                {"topics": [{"title": "Tokens", "entry_ids": ["E3"]}, {"title": "Sign-in", "entry_ids": ["E4"]}]}]
    topics, _ = document_plan_utils.merge_part_topics(outlines, [1, 1], 0)
    ids = {topic["title"]: topic["id"] for topic in topics}
    harness.check("ids name the first outline and the place in it",
                  ids == {"Portal": "T01.1", "Sign-in": "T01.2", "Tokens": "T02.1"}, str(ids))
    changed = [outlines[0], {"topics": [{"title": "Refresh", "entry_ids": ["E5"]}, *outlines[1]["topics"]]}]
    again = {topic["title"]: topic["id"] for topic in document_plan_utils.merge_part_topics(changed, [1, 1], 0)[0]}
    harness.check("redoing the second outline leaves the first outline's ids alone",
                  again["Portal"] == "T01.1" and again["Sign-in"] == "T01.2", str(again))


def test_links_are_found_and_rendered_as_heading_links():
    text = "The token is refreshed as [T02.1] explains; the limit is in [T01.2]."
    harness.check("both ids are found, in order", document_text_utils.find_topic_links(text) == ["T02.1", "T01.2"])
    rendered = document_text_utils.render_topic_links(text, {"T02.1": ("Token refresh", "token-refresh"),
                                                              "T01.2": ("Refund limits", "refund-limits")})
    harness.check("each becomes a markdown link to its heading's anchor",
                  "[Token refresh](#token-refresh)" in rendered and "[Refund limits](#refund-limits)" in rendered,
                  rendered)
    harness.check("an id nobody knows is left as written, for the check to name",
                  document_text_utils.render_topic_links("see [T09.9]", {}) == "see [T09.9]")
    # pdf-real-fw9: "_Exempt payee code_ (T08.6)" reached a reader as written
    harness.check("an id in round brackets is a link, the italic name before it its text",
                  document_text_utils.render_topic_links("see _Token refresh_ (T02.1) and (T02.1).",
                                                         {"T02.1": ("Token refresh", "token-refresh")})
                  == "see [Token refresh](#token-refresh) and [Token refresh](#token-refresh).")
    harness.check("a list item with no text is found, one in a code block is not",
                  document_text_utils.find_empty_list_items("A. One code\nB.\n- \n```\n-\n```")
                  == ["B.", "-"])


def test_a_bracket_of_several_topic_ids_is_a_link_for_each_defect():
    # Defect: an executive summary wrote "[T03.1, T05.2]" and the reader saw it as written, since only a bracket with one
    # id was a link. Fix: each id of such a bracket is a link, found by the check too, and a bracket with an unknown id stays.
    targets = {"T02.1": ("Token refresh", "token-refresh"), "T01.2": ("Refund limits", "refund-limits")}
    rendered = document_text_utils.render_topic_links("Both matter [T02.1, T01.2].", targets)
    harness.check("each id of the bracket is a link, a comma between", rendered ==
                  "Both matter [Token refresh](#token-refresh), [Refund limits](#refund-limits).", rendered)
    harness.check("a bracket with one unknown id stays as written, for the check to name",
                  document_text_utils.render_topic_links("see [T02.1, T09.9]", targets) == "see [T02.1, T09.9]")
    harness.check("the ids of a bracket are found in the order written, beside single ones",
                  document_text_utils.find_topic_links("[T01.2] then [T02.1, T09.9] then [T03.4]")
                  == ["T01.2", "T02.1", "T09.9", "T03.4"])


def test_a_call_written_as_text_is_read_and_prose_is_not():
    call = tool_call_text_utils.read_text_call('{"name": "find", "arguments": "{\\"words\\": \\"refund\\"}"}')
    harness.check("a call with its arguments as a JSON string", call == {"name": "find", "args": {"words": "refund"},
                                                                         "id": ""}, str(call))
    harness.check("prose is not a call", tool_call_text_utils.read_text_call("I will now look it up.") is None)


def test_a_call_written_in_the_models_own_tag_format_is_read():
    """A chapter answer came back as `submit_chapter`, `<arg_key>markdown`, `<arg_value>...</arg_value>`,
    `</tool_call>` in the reply's text, its key's closing tag missing, and was taken for no call at all (2026-10-12)."""

    text = ("submit_chapter\n<arg_key>markdown\n<arg_value>### Retries (T01.1)\n\nA job is retried three times. [F1.1]\n"
            "</arg_value>\n</tool_call>")
    call = tool_call_text_utils.read_text_call(text)
    harness.check("the tag format is read as the call, the value whole",
                  call == {"name": "submit_chapter", "args": {"markdown": "### Retries (T01.1)\n\nA job is retried three "
                                                                          "times. [F1.1]"}, "id": ""}, str(call))
    two = tool_call_text_utils.read_text_call("<tool_call>look\n<arg_key>frame</arg_key>\n<arg_value>frame-002</arg_value>\n"
                                              "<arg_key>grid_lines</arg_key>\n<arg_value>20</arg_value>\n</tool_call>")
    harness.check("closed keys and two arguments, a number read as a number",
                  two == {"name": "look", "args": {"frame": "frame-002", "grid_lines": 20}, "id": ""}, str(two))
    harness.check("prose naming a tag is not a call",
                  tool_call_text_utils.read_text_call("I will use <arg_value> later.") is None)


def test_a_frame_is_sent_as_an_inlined_image_part():
    paths = fx.make_paths()
    frame = paths.output_root / "frame.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"png bytes")
    part = frame_message_utils.encode_frame(frame)
    harness.check("an image_url part with the PNG's bytes", part["type"] == "image_url"
                  and part["image_url"]["url"].startswith("data:image/png;base64,"), str(part)[:80])


def test_a_frame_narrower_than_the_sent_width_is_sent_enlarged_and_its_file_is_left_as_it_is():
    paths = fx.make_paths()
    small = paths.output_root / "small.jpg"
    small.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(small), numpy.full((360, 640, 3), 200, dtype=numpy.uint8))
    before = small.read_bytes()
    part = frame_message_utils.encode_frame(small, 1280)
    sent = cv2.imdecode(numpy.frombuffer(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]), numpy.uint8),
                        cv2.IMREAD_COLOR)
    harness.check("a 640x360 frame is sent 1280x720", sent.shape[:2] == (720, 1280), str(sent.shape))
    harness.check("the frame's file is unchanged", small.read_bytes() == before)
    wide = paths.output_root / "wide.png"
    cv2.imwrite(str(wide), numpy.full((720, 1280, 3), 200, dtype=numpy.uint8))
    part = frame_message_utils.encode_frame(wide, 1280)
    harness.check("a frame already that wide is sent as its own bytes",
                  base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) == wide.read_bytes())
    harness.check("phase 3 sends frames at least 1280 wide unless told otherwise",
                  app_config.FramesProcessingSettings().min_sent_frame_width == 1280)


def test_the_carriers_hold_what_they_name():
    spec = SubmitToolSpec(schema=document_final.DocumentFinal, job_kind="final")
    harness.check("a submit tool names its schema and kind", spec.job_kind == "final")
    harness.check("a read result carries no frame unless one was shown",
                  WriteReadResult(text="[r1] nothing").frame_path is None)
    harness.check("an outcome says whether it was accepted",
                  not FollowUpOutcome(is_accepted=False, attempts=3, calls=5, reason="3 answers rejected").is_accepted)


def test_a_small_cut_a_frame_tool_returns_is_sent_enlarged():
    """Sent at its own 310 pixels, a cut of a 360p recording read to the writer as "tiny, blurry, nearly illegible", and
    it undid a cut that was readable."""

    cut = Path(tempfile.mkdtemp()) / "cut-001.png"
    cv2.imwrite(str(cut), numpy.full((248, 310, 3), 200, numpy.uint8))
    part = write_conversation_service.encode_tool_frames([cut])[0]
    data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    width = cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_COLOR).shape[1]
    harness.check("the cut reaches the model at the minimum width phase 3 sends frames at",
                  width == app_config.settings.frames_processing.min_sent_frame_width, str(width))


def test_the_cuts_a_request_lists_are_sent_with_it():
    """After a resume, a writer placed two cuts made in an earlier life without looking at them, and described them
    from memory, wrong in theme, line numbers and code."""

    root = Path(tempfile.mkdtemp())
    (root / "04_writing" / "images" / "cuts").mkdir(parents=True)
    cv2.imwrite(str(root / "04_writing" / "images" / "cuts" / "cut-004.png"), numpy.full((200, 300, 3), 220, numpy.uint8))
    store = {"cuts": {"cut-004": {"frame": "frame-002", "file": "04_writing/images/cuts/cut-004.png"},
                      "cut-005": {"frame": "frame-002", "file": "04_writing/images/cuts/cut-005.png"}}}
    sections = [{"existingCuts": ["cut-004 from frame-002", "cut-005 from frame-002"]}, {"existingCuts": []}]
    message = write_conversation_service.build_existing_cuts_message(root, store, sections)
    images = [part for part in message.content if part.get("type") == "image_url"]
    harness.check("each listed cut whose file stands is sent as an image, named in the text",
                  len(images) == 1 and "cut-004" in message.content[0]["text"], str(message.content[0]))
    harness.check("no message when the request lists no cut",
                  write_conversation_service.build_existing_cuts_message(root, store, [{"existingCuts": []}]) is None)


def test_each_cut_a_request_lists_is_sent_after_its_own_id_defect():
    # Seven cuts sent as one list of ids and then seven images: the writer counted them wrong and described the
    # connector rows of one cut as the relay drawing of the next; each image now follows its own id
    root = Path(tempfile.mkdtemp())
    (root / "04_writing" / "images" / "cuts").mkdir(parents=True)
    store = {"cuts": {}}
    for number in (3, 4, 5):
        cv2.imwrite(str(root / "04_writing" / "images" / "cuts" / f"cut-00{number}.png"),
                    numpy.full((200, 300, 3), 200 + number, numpy.uint8))
        store["cuts"][f"cut-00{number}"] = {"frame": "frame-015", "file": f"04_writing/images/cuts/cut-00{number}.png"}
    sections = [{"existingCuts": ["cut-003 from frame-015", "cut-004 from frame-015", "cut-005 from frame-015"]}]
    message = write_conversation_service.build_existing_cuts_message(root, store, sections)
    kinds = [part.get("type") for part in message.content]
    labels = [part["text"] for part in message.content[1:] if part.get("type") == "text"]
    harness.check("each image comes right after its own id",
                  kinds == ["text", "text", "image_url", "text", "image_url", "text", "image_url"]
                  and labels == ["cut-003, from frame-015:", "cut-004, from frame-015:", "cut-005, from frame-015:"],
                  f"{kinds} {labels}")


def test_calls_written_as_python_above_a_chapter_run_before_it_defect():
    # A worker wrote three leave_out calls as text above its chapter; the chapter was checked and the calls were lost,
    # so the check found the frames unaccounted for (DEFECTS 290); now the leading calls are read, then the chapter
    said = ('leave_out(frame="frame-004", reason="a list written as markdown")\n'
            "look(frame='frame-005')\n\n### Ledger limits (T01.1)\n\nThe ledger caps orders [F4.1].\n")
    calls, rest = tool_call_text_utils.split_leading_calls(said, {"leave_out", "look", "check_chapter"})
    harness.check("both calls are read in order, the chapter is what follows",
                  [(call["name"], call["args"]) for call in calls] == [("leave_out", {"frame": "frame-004", "reason": "a list written as markdown"}),
                                                                       ("look", {"frame": "frame-005"})]
                  and rest.startswith("### Ledger limits"), f"{calls} {rest[:40]}")
    harness.check("a tool not offered is no call", tool_call_text_utils.split_leading_calls(said, {"look"})[0] == [])
    harness.check("a chapter quoting code is never read as calls",
                  tool_call_text_utils.split_leading_calls("### Code (T01.1)\n\nprint(x=1)\n\nIt prints one.\n", {"print"})[0] == [])


def test_a_call_written_below_a_chapter_runs_too_defect():
    # A worker wrote its leave_out below its chapter; read from the top only, the call was lost (DEFECTS 290)
    said = "### Ledger limits (T01.1)\n\nThe ledger caps orders [F4.1].\n\nleave_out(frame='frame-004', reason='a list')\n"
    calls, rest = tool_call_text_utils.split_leading_calls(said, {"leave_out"})
    harness.check("the call below the chapter is read, the chapter kept whole",
                  [call["args"]["frame"] for call in calls] == ["frame-004"] and rest.endswith("[F4.1]."), f"{calls} {rest!r}")
