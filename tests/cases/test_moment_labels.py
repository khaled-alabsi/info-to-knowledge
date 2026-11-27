"""The one label a frame's record carries for R12: `tags`, the words a reader searches for to find the moment. A tag is
a subject (a system, tool or term, open and reused) or a kind of facts (from a closed list). A frame names no topic:
phase 4 makes its own table of contents (the owner, 2026-09-28).

The program keeps the tags clean without costing the record: a generic word, a sentence and any past the limit are
dropped and counted, never a rejection. The merge carries them onto the entries, and speech with no frame of its own
takes the subject tags of the frame on screen, marked inherited. Each frame call is shown the subject tags so far,
most used first, so it reuses a subject's string instead of coining a new one.
"""

from pathlib import Path

from support import fixtures as fx
from support import harness

from models.attempt_request import AttemptRequest
from prompts import frames_processing_messages
from schemas import frame_record
from services.ignore_list_service import IgnoreList
from utils import frame_markdown_utils
from utils import frame_merge_utils
from utils import moment_label_utils

FRAMES_PROCESSING_INSTRUCTION = Path(__file__).resolve().parents[2] / "agent-context" / "instructions" / "_shared" / "frames-processing.md"
RECORD = {"frame": 0, "screen_kind": "web application", "facts": ["A refund can be split into at most three parts."],
          "diagram": "", "screen": '- slide: white, the whole frame\n  - text "Max. 3": centre', "speech_visual_alignment": "", "builds_on": [], "contradicts_earlier": [],
          "open_questions": [],
          "tags": ["refund policy", "reference"],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [],
          "third_person_description": "The presenter points at the figure on the slide and reads it out.",
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}


def _record(index: int,
            timestamp: float,
            **fields) -> dict:
    return {**RECORD, "frame": index, "index": index, "slug": f"frame-{index:03d}", "timestamp": timestamp, **fields}


def test_tags_are_a_section_of_the_notes_and_there_is_no_topics_one():
    """Never empty (the owner, 2026-09-28: "the tags field are empty. This is not allowed"): 42 of 42 frames of a real
    batch answered no tags while their reasoning listed them. The validator rejects notes with none."""

    harness.check("tags are a section of the notes", frame_record.FRAME_SECTIONS["tags"][0] == "Tags")
    harness.check("no topics section exists", "topics" not in frame_record.FRAME_SECTIONS)
    parsed = frame_markdown_utils.parse_answer("# Frame 5\n\n## Tags\nrefund split limit, procedure\n",
                                               is_addition=False)["frames"][0]
    harness.check("one line of tags reads as a list", parsed["tags"] == ["refund split limit", "procedure"],
                  str(parsed["tags"]))


def test_the_program_drops_unusable_tags_and_says_so():
    kept, dropped = moment_label_utils.clean_tags(
        ["Refund split limit", "Procedure", "overview", "refund split limit.", "a whole sentence about what the page does",
         "", "Order Service"])
    harness.check("a kind is kept in lower case, a subject in its own spelling, a repeat once",
                  kept == ["Refund split limit", "procedure", "Order Service"], str(kept))
    harness.check("a generic word, a sentence and an empty tag are dropped and named",
                  dropped == ["tag 'overview'", "tag 'a whole sentence about what the page does'", "tag ''"], str(dropped))
    many, over = moment_label_utils.clean_tags([f"subject {number}" for number in range(moment_label_utils.MAX_TAGS + 2)])
    harness.check("tags past the limit are dropped", len(many) == moment_label_utils.MAX_TAGS and len(over) == 2,
                  str(over))
    harness.check("a subject tag is told from a kind",
                  moment_label_utils.list_subject_tags(["concept", "Order Service", "risk"]) == ["Order Service"])


def test_the_merge_carries_the_tags_onto_entries_and_speech_inherits_the_subjects():
    transcript = {"segments": [{"text": "Refunds come in three parts.", "start": 12.0, "end": 14.0, "words": []}]}
    frames_file = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 10.0},
                             {"slug": "frame-001", "index": 1, "timestamp": 30.0}]}
    records = [_record(0, 10.0, tags=["Refund split limit", "reference", "overview"]),
               _record(1, 30.0, facts=["Each part is booked on its own."], screen="",
                       tags=["refund split limits.", "refund booking", "procedure"])]
    merged = frame_merge_utils.merge_frame_records(transcript, frames_file, records, {})
    facts = [entry for entry in merged["entries"] if entry["kind"] == "facts"]
    harness.check("an entry carries its frame's cleaned tags",
                  facts[0]["tags"] == ["Refund split limit", "reference"] and "topics" not in facts[0],
                  str(facts[0]))
    harness.check("a plural and a trailing full stop are the same subject, in its first spelling",
                  facts[1]["tags"] == ["Refund split limit", "refund booking", "procedure"], str(facts[1]))
    speech = next(entry for entry in merged["entries"] if entry["kind"] == "speech")
    harness.check("speech takes the subject tags of the frame on screen, not its kinds, marked inherited",
                  speech.get("tags") == ["Refund split limit"] and speech.get("tagsInherited") is True, str(speech))
    harness.check("the drops are counted", merged["counts"].get("droppedTags") == 1, str(merged["counts"]))
    harness.check("a frame lists its tags and no topics",
                  merged["frames"][0]["tags"] == ["Refund split limit", "reference"] and "topics" not in merged["frames"][0],
                  str(merged["frames"][0]))


def test_a_frame_is_shown_the_subject_tags_so_far_most_used_first():
    records = [_record(0, 1.0, tags=["refund booking", "concept"]), _record(1, 2.0, tags=["refund split limit"]),
               _record(2, 3.0, tags=["refund split limit", "ledger", "concept"])]
    listed = moment_label_utils.list_tags_so_far(records, max_chars=1000)
    harness.check("most used first, each once, no kinds", listed[:1] == ["refund split limit"] and sorted(listed) ==
                  ["ledger", "refund booking", "refund split limit"], str(listed))
    harness.check("capped by characters, whole tags only",
                  moment_label_utils.list_tags_so_far(records, max_chars=25) == ["refund split limit"])
    paths = fx.make_paths()
    item = {"slug": "frame-003", "index": 3, "timestamp": 4.0, "path": "frames/frame-003.jpg"}
    frame = paths.output_root / item["path"]
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"\xff\xd8\xff not a real frame")
    request = AttemptRequest(job_kind="frames_processing", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs={"slug": "frame-003", "item": item, "previousRecords": [],
                                                          "latestDiagram": None, "tagsSoFar": listed})
    text = frames_processing_messages.build_frame_messages(request)[1].content[0]["text"]
    harness.check("the frame's message lists them", "Tags so far" in text and "- refund split limit" in text, text)
    harness.check("the frame's message names no topics", "Topics so far" not in text, text)


def test_the_frame_instruction_teaches_tags_and_no_topics():
    text = FRAMES_PROCESSING_INSTRUCTION.read_text(encoding="utf-8")
    harness.check("`## Tags` is taught, no topics are", "## Tags" in text and "topics" not in text.casefold())
    harness.check("every kind is named", all(f"`{kind}`" in text for kind in frame_record.KIND_TAGS))
    harness.check("subjects are asked for and copied from the list", "Subjects:" in text and "Tags so far" in text)


def test_a_number_word_is_matched_whole_and_a_repeat_past_the_limit_is_not_dropped():
    from utils import reading_tool_utils
    harness.check("eight does not find eighteen", not reading_tool_utils.is_word_in("eight", "eighteen people came"))
    harness.check("eight finds eight", reading_tool_utils.is_word_in("eight", "eight people came"))
    tags = [f"Subject {chr(65 + index)}" for index in range(moment_label_utils.MAX_TAGS)]
    kept, dropped = moment_label_utils.clean_tags([*tags, "subject a"])
    harness.check("a repeat of a kept tag past the limit is merged, never dropped",
                  len(kept) == moment_label_utils.MAX_TAGS and dropped == [], str(dropped))
