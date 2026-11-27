"""Phase 2 of an audio recording, through the real writer and the real check, on a made transcript of three voices
(invented names, AcmeSync): a one-line turn of one speaker between two turns of others, which a model's answer names
as the next turn's speaker, is written with its own turn's speaker and passes."""

import tempfile
from pathlib import Path

from support import fixtures as fx
from support import harness

from models import output_contract
from models.attempt_request import AttemptRequest
from services import chain_dispatch_service
from services import phase_file_service
from services import speech_file_service
from services.ignore_list_service import IgnoreList
from utils import speech_markdown_utils
from validators import speech_validator


def _segment(text: str,
             start: float,
             speaker: str) -> dict:
    words = text.split()
    step = 1.0 / len(words)

    return {"text": " " + text, "start": start, "end": start + 1.0, "speaker": speaker,
            "words": [{"word": " " + word, "start": start + i * step, "end": start + (i + 1) * step, "speaker": speaker}
                      for i, word in enumerate(words)]}


def test_a_one_line_turn_named_as_the_next_speaker_is_written_as_its_own_defect():
    # A talk's chair said one line between the speaker's closing and a question, and the answer wrote that line as the
    # next turn's speaker; the run blocked under zero repair. The program now names it by its turn
    transcript = {"segments": [_segment("AcmeSync stores every file twice", 0.0, "Speaker A"),
                               _segment("Thank you all very much", 1.0, "Speaker A"),
                               _segment("I will go through the sources again", 2.0, "Speaker B"),
                               _segment("We have time for one short question", 3.0, "Speaker C"),
                               _segment("Jonas at microphone three please", 4.0, "Speaker C")]}
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-talk.wav")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE], transcript)
    answer = ("# Speech\n\n## S0001-S0002 · Speaker A\nAcmeSync stores every file twice. Thank you all very much.\n\n"
              "## S0003 · Speaker C\nI will go through the sources again.\n\n"
              "## S0004-S0005 · Speaker C\nWe have time for one short question. Jonas, at microphone three, please.\n")
    parsed, why = speech_markdown_utils.parse_speech(answer)
    harness.check("the answer parses", parsed is not None, why)
    request = AttemptRequest(job_kind="speech_structuring", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(),
                             inputs={"lines": speech_markdown_utils.list_numbered_lines(transcript)})
    chain_dispatch_service._write_speech(request, parsed)
    report = speech_validator.validate_speech_record(paths, False)
    harness.check("no speaker finding", "SPEECH_SPEAKER_NOT_HEARD" not in harness.codes(report), str(harness.codes(report)))
    harness.check("the one-line turn is Speaker B",
                  "## S0003 · Speaker B" in speech_file_service.build_speech_path(paths).read_text(encoding="utf-8"))


def test_paragraph_spans_miscounted_by_the_answer_are_closed_by_the_program_defect():
    # A model's answer to a 448-line transcript wrote a heading's last line short of its text and started paragraphs on
    # the line the one before ended on; the run blocked twice. The program now ends each paragraph where the next begins
    transcript = {"segments": [_segment("AcmeSync stores every file twice", 0.0, "Speaker A"),
                               _segment("It keeps the copies in two regions", 1.0, "Speaker A"),
                               _segment("Thank you all very much", 2.0, "Speaker A"),
                               _segment("We have time for one short question", 3.0, "Speaker C"),
                               _segment("Jonas at microphone three please", 4.0, "Speaker C")]}
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-talk.wav")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE], transcript)
    answer = ("# Speech\n\n## S0001 · Speaker A\nAcmeSync stores every file twice. It keeps the copies in two regions.\n\n"
              "## S0003-S0004 · Speaker A\nThank you all very much.\n\n"
              "## S0004 · Speaker C\nWe have time for one short question. Jonas, at microphone three, please.\n")
    parsed, why = speech_markdown_utils.parse_speech(answer)
    harness.check("the answer parses", parsed is not None, why)
    request = AttemptRequest(job_kind="speech_structuring", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(),
                             inputs={"lines": speech_markdown_utils.list_numbered_lines(transcript)})
    chain_dispatch_service._write_speech(request, parsed)
    codes = harness.codes(speech_validator.validate_speech_record(paths, False))
    harness.check("no line missing or twice", not {"SPEECH_LINE_TWICE", "SPEECH_LINE_MISSING"} & set(codes), str(codes))
    text = speech_file_service.build_speech_path(paths).read_text(encoding="utf-8")
    harness.check("the spans run S0001-S0002, S0003, S0004-S0005",
                  "## S0001-S0002 · Speaker A" in text and "## S0003 · Speaker A" in text and "## S0004-S0005 · Speaker C" in text,
                  text)


def test_a_sentence_cut_across_two_lines_joined_in_the_later_paragraph_passes_defect():
    # A talk's transcript cut a sentence across two lines; the answer joined it in the later paragraph, which then held
    # 24 words for 12 spoken, and the run blocked. A paragraph is now judged with its neighbour of the same speaker
    transcript = {"segments": [_segment("AcmeSync keeps every single file in two regions and then every single night it quietly",
                                        0.0, "Speaker A"),
                               _segment("copies each one to a second store in Frankfurt for the auditors", 1.0, "Speaker A"),
                               _segment("We have time for one short question", 2.0, "Speaker C")]}
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="AcmeSync-talk.wav")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE], transcript)
    answer = ("# Speech\n\n## S0001 · Speaker A\nAcmeSync keeps every single file in two regions.\n\n"
              "## S0002 · Speaker A\nAnd then every single night it quietly copies each one to a second store in Frankfurt "
              "for the auditors.\n\n## S0003 · Speaker C\nWe have time for one short question.\n")
    parsed, why = speech_markdown_utils.parse_speech(answer)
    harness.check("the answer parses", parsed is not None, why)
    request = AttemptRequest(job_kind="speech_structuring", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(),
                             inputs={"lines": speech_markdown_utils.list_numbered_lines(transcript)})
    chain_dispatch_service._write_speech(request, parsed)
    codes = set(harness.codes(speech_validator.validate_speech_record(paths, False)))
    harness.check("no word count finding", not codes & {"SPEECH_PARAGRAPH_ADDED", "SPEECH_PARAGRAPH_SHORTENED"}, str(codes))
