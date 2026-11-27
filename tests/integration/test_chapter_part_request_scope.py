"""A chapter past the note limit, split into parts by phase 4's own grouping, each part's request built as the run builds
it (DEFECTS 495): every part said "the whole chapter as one text" beside the part line's "write it with only the notes
listed", and the writer weighed the two. Made data: an invented chapter of three headings, 130 notes."""

from types import SimpleNamespace

from support import harness
from graph.nodes import write_document_node
from prompts import document_messages


def _section(topic: str, count: int) -> dict:
    entries = [{"id": f"F{topic}.{number}", "kind": "fact", "text": f"The gateway retries partner {number} twice."}
               for number in range(count)]

    return {"heading": f"Gateway topic {topic}", "topicId": f"T03.{topic}", "slug": f"heading-03{topic}-1",
            "entries": entries, "entryIds": [entry["id"] for entry in entries]}


def test_each_part_of_a_split_chapter_asks_for_its_own_headings_defect():
    # each part of a split chapter asked for "the whole chapter as one text"
    parts = write_document_node._group_sections([_section("1", 50), _section("2", 40), _section("3", 40)], 70)
    harness.check("the chapter is split into parts", len(parts) > 1, str(len(parts)))
    for number, part in enumerate(parts, start=1):
        inputs = {"sections": part, "chapter": 3, "chapterCount": 9, "chapterTitle": "Gateway retries",
                  "documentTitle": "Gateway report", "part": number, "partCount": len(parts)}
        text = document_messages.build_chapter_text(SimpleNamespace(inputs=inputs, findings=[]))
        harness.check(f"part {number} asks for its own headings, never the whole chapter",
                      "this request's headings as one text" in text and "the whole chapter" not in text,
                      text[-900:])
