"""The finished document's name: `[meeting reason or event]-[speakers]-[date]`, for its folder and its markdown file.
REQUIREMENTS.md R13.

The agent decides the event name and the speakers, from what the meeting shows and says; the program decides the
date, from the recording's file name or its metadata, and writes the name in one safe form: lower case, ASCII, words
joined by hyphens. A part nobody established is left out, never guessed.
"""


from support import fixtures as fx
from support import harness

from models.attempt_request import AttemptRequest
from prompts import document_messages
from schemas import document_parts
from services import document_folder_service
from services.ignore_list_service import IgnoreList
from utils import document_name_utils


def test_each_part_becomes_lower_case_ascii_words_joined_by_hyphens():
    harness.check("punctuation and spaces become one hyphen",
                  document_name_utils.build_slug("Entra ID: Onboarding & SSO!") == "entra-id-onboarding-sso")
    harness.check("umlauts are written out, as German does",
                  document_name_utils.build_slug("Übergabe der Größe") == "uebergabe-der-groesse")
    harness.check("other accents are dropped", document_name_utils.build_slug("Café Ré") == "cafe-re")


def test_the_name_is_event_then_speakers_then_date():
    name = document_name_utils.build_document_name("Payment API Onboarding", ["Jonas", "Anna Schmidt"],
                                                   "2026-03-03", fallback="unused")
    harness.check("event, speakers, date", name == "payment-api-onboarding-jonas-anna-schmidt-2026-03-03", name)


def test_long_parts_are_capped_and_missing_parts_left_out():
    long_event = document_name_utils.build_document_name("one two three four five six seven eight", [], "",
                                                         fallback="unused")
    harness.check("the event keeps its first six words", long_event == "one-two-three-four-five-six", long_event)
    many = document_name_utils.build_document_name("Sync", ["A", "B", "C", "D"], "2026-03-03", fallback="unused")
    harness.check("at most three speakers", many == "sync-a-b-c-2026-03-03", many)
    harness.check("no event falls back to the given name",
                  document_name_utils.build_document_name(" ", ["Jonas"], "2026-03-03", fallback="The title here")
                  == "the-title-here-jonas-2026-03-03")


def test_the_date_is_read_from_the_file_name_when_it_holds_one():
    harness.check("a compact date", document_name_utils.find_date_in_name(
        "AcmeSync-jonas-20260303_180349-Besprechungsaufzeichnung") == "2026-03-03")
    harness.check("an ISO date", document_name_utils.find_date_in_name("weekly sync 2026-03-03") == "2026-03-03")
    harness.check("no date is an empty string", document_name_utils.find_date_in_name("weekly sync") == "")
    harness.check("an impossible date is not a date", document_name_utils.find_date_in_name("run 20261399") == "")


def test_the_parts_answer_carries_the_event_and_the_speakers_and_may_leave_them_out():
    parsed = document_parts.DocumentParts.model_validate({"announced_agenda": [], "points": []})
    harness.check("all three are optional", parsed.event_name == "" and parsed.speakers == [] and parsed.topic == "")
    fields = document_parts.DocumentParts.model_json_schema()["properties"]
    harness.check("and all three are sent to the model", {"event_name", "speakers", "topic"} <= set(fields), str(fields))


def test_the_metadata_block_is_yaml_front_matter_with_five_keys_always_present():
    block = document_name_utils.render_front_matter("Single sign-on for the partner portal", "Payment API onboarding",
                                                    ["Dana", "Lee Park"], "2026-03-03")
    harness.check("it opens and closes with three dashes", block.startswith("---\n") and block.endswith("---\n\n"), block)
    harness.check("topic, event, speakers, date and the run's tags, in that order",
                  [line.split(":")[0] for line in block.strip().splitlines()[1:-1]] == ["topic", "event", "speakers", "date", "tags"],
                  block)
    harness.check("strings are quoted so a colon in them stays YAML",
                  'topic: "Single sign-on for the partner portal"' in block and 'speakers: ["Dana", "Lee Park"]' in block,
                  block)
    empty = document_name_utils.render_front_matter("", "", [], "")
    harness.check("a part nobody established is an empty value, never a guess",
                  'event: ""' in empty and "speakers: []" in empty and 'date: ""' in empty and "tags: []" in empty, empty)


def test_the_document_folder_is_named_after_the_meeting():
    paths = fx.make_paths()
    markdown = "# Orders ship from the new warehouse\n\nText.\n"
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(markdown, encoding="utf-8")
    folder = document_folder_service.build_document_dir(paths)
    # With no event named and no date in the recording's name, the document's title names the folder
    harness.check("the folder phase 4 publishes is named from the document's title, in the run folder",
                  folder == paths.output_root / "orders-ship-from-the-new-warehouse", str(folder))
    harness.check("the document inside it carries the same name",
                  document_folder_service.build_document_path(paths) == folder / f"{folder.name}.md")


def test_the_parts_call_is_handed_the_file_name_and_the_transcript_s_speakers():
    """R13 and R14: the hints the agent names the event and the speakers from."""

    paths = fx.make_paths()
    request = AttemptRequest(job_kind="parts", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs={"slug": "parts", "moments": [], "recordingName":
                                                          "Billing-dana-20250114.mp4", "transcriptSpeakers":
                                                          ["Dana Weber", "Lee Park"]})
    text = document_messages.build_parts_messages(request)[1].content[0]["text"]
    harness.check("the file name is in the message", "Billing-dana-20250114.mp4" in text, text)
    harness.check("and the transcript's speakers", "Dana Weber, Lee Park" in text, text)


def test_a_blank_speaker_never_takes_a_named_speakers_place():
    name = document_name_utils.build_document_name("Weekly", ["", "Jonas", "Anna", "Mia"], "2026-03-03", fallback="fallback")
    harness.check("three named speakers", name == "weekly-jonas-anna-mia-2026-03-03", name)
