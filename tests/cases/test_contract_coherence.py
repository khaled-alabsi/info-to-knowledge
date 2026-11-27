"""The schema, the instruction and the validator state ONE contract.

In a chain pipeline the contract is the record's schema. The model is told about a field by the
instruction it is sent, and the validator checks the field on disk, so a field named in one of the
three and not the others is the drift that fails every item on every attempt while the records are
substantively fine.
"""

from support import harness
import config
from models import output_contract
from schemas import frame_record
from services import chain_dispatch_service
from services import validation_service

INSTRUCTION = config.AGENT_CONTEXT_DIR / "instructions" / "_shared" / "frames-processing.md"


def test_the_record_contract_has_been_filled_in():
    harness.check("schemas/frame_record.py states the record's real fields", set(frame_record.FRAME_FIELDS) != {"todo"},
                  "the generated schema still carries its one placeholder field")


def test_every_heading_of_the_record_is_taught_in_the_instruction_the_model_is_sent():
    instruction_text = INSTRUCTION.read_text(encoding="utf-8")
    for field, (heading, _) in frame_record.FRAME_SECTIONS.items():
        harness.check(f"the work instruction shows the heading `## {heading}` of `{field}`", f"## {heading}" in instruction_text,
                      "the program reads the notes by it and the instruction never tells the model to write it")


def test_every_phase_file_is_written_by_a_phase_the_graph_has():
    for number, row in output_contract.PHASE_FILES.items():
        harness.check(f"phase {number} is written by a known node phase", row["phase"] in output_contract.PHASE_NUMBERS,
                      str(row))


def test_every_chain_kind_has_a_validator():
    for kind in chain_dispatch_service.MESSAGE_BUILDERS:
        harness.check(f"chain kind `{kind}` has a validator row", kind in validation_service.VALIDATORS,
                      "a kind with no validator raises on its first attempt")


def test_a_word_recognised_as_misheard_is_no_open_question_of_the_frame_defect():
    # Two frames' notes wrote "the term … is a transcript error for <term>" as an open question, and the document listed
    # both as open points (DEFECTS 368): the open-questions and answers steps held the rule, the frame's own field not
    instruction = " ".join(INSTRUCTION.read_text(encoding="utf-8").split())
    field = instruction[instruction.index("- `## Open questions`:"):]
    harness.check("the frame's open-questions field says a recognised mishearing is no question",
                  "A word you recognise as misheard" in field[:1200]
                  and "a line saying a word is a transcript error never stands under this heading" in field[:1200],
                  field[:400])


def test_the_chapter_is_written_only_in_its_tool_call_defect():
    # 327 of 336 public chapter replies wrote the chapter as their own text and again in the tool call, and two of them
    # repeated their ends until the loop test stopped them (DEFECTS 386). The chapter instruction now says where it goes
    instruction = " ".join((config.AGENT_CONTEXT_DIR / "instructions" / "_shared" / "write.md").read_text(encoding="utf-8").split())
    chapter = instruction[instruction.index("# A chapter"):][:1200]
    harness.check("the chapter instruction says the chapter goes only in the tool call",
                  "Write the chapter only as the `markdown` of your `check_chapter` or `submit_chapter` call" in chapter
                  and "leave your reply's own text empty" in chapter, chapter[:300])


def test_a_figure_s_lead_in_and_a_link_between_notes_are_bounded_defect():
    # A chapter placed one tool's figure under a sentence leading into another tool, and tied the two with a claim
    # neither note made. The writing instruction now says what stands above a figure and that a sentence links no notes
    instruction = " ".join((config.AGENT_CONTEXT_DIR / "instructions" / "_shared" / "write.md").read_text(encoding="utf-8").split())
    harness.check("the paragraph above a frame is about that frame's own notes",
                  "The paragraph right above the frame is about that frame's own notes" in instruction, "")
    harness.check("a sentence holding two notes never states a link neither states",
                  "never a link neither states" in instruction, "")
