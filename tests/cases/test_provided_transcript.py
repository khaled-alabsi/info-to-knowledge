"""An inbox item may be a folder: the recording, and optionally the meeting tool's own transcript beside it.
REQUIREMENTS.md R14.

Measured in `research/transcript-sources/`: the meeting tool's transcript is worse than phase 1's on names and terms,
and correcting phase 1 with it drops real speech as often as invented text. So phase 1 always runs; a transcript
found beside the recording is recorded with its agreement, and the speaker names it carries are handed to the parts
call as candidates.
"""

import tempfile
from pathlib import Path

from support import harness

from services import inbox_service
from services import provided_transcript_service
from utils import provided_transcript_utils

VTT = """WEBVTT

a1b2c3d4-0001
00:00:01.000 --> 00:00:04.000
<v Dana Weber>Welcome to the billing review.</v>

a1b2c3d4-0002
00:00:04.500 --> 00:00:09.000
<v Lee Park>Thanks. Refunds can be split into three parts, and each part is booked on its own.</v>

a1b2c3d4-0003
00:01:05.000 --> 00:01:07.250
<v Dana Weber>Any questions?</v>
"""
PLAIN_VTT = "WEBVTT\n\nc-1\n00:00:01.000 --> 00:00:04.000\nWelcome to the billing review.\n"
SRT = "1\n00:00:01,000 --> 00:00:04,000\nWelcome to the billing review.\n"


def _make_folder(*names: str) -> Path:
    folder = Path(tempfile.mkdtemp()) / "meeting"
    folder.mkdir()
    for name in names:
        (folder / name).write_text(VTT if name.endswith(".vtt") else "fixture", encoding="utf-8")

    return folder


def test_cues_are_read_with_their_times_text_and_speaker():
    cues = provided_transcript_utils.read_cues(VTT)
    harness.check("three cues", len(cues) == 3, str(cues))
    harness.check("times in seconds", (cues[1]["start"], cues[2]["end"]) == (4.5, 67.25), str(cues))
    harness.check("the speaker tag is read and removed from the text",
                  cues[0]["speaker"] == "Dana Weber" and cues[0]["text"] == "Welcome to the billing review.", str(cues[0]))
    harness.check("a cue with no speaker tag has an empty speaker",
                  provided_transcript_utils.read_cues(PLAIN_VTT)[0]["speaker"] == "")
    harness.check("SubRip's comma times are read too", provided_transcript_utils.read_cues(SRT)[0]["end"] == 4.0)


def test_speakers_are_listed_by_how_much_they_said():
    speakers = provided_transcript_utils.list_speakers(provided_transcript_utils.read_cues(VTT))
    harness.check("the one who said most first", speakers == ["Lee Park", "Dana Weber"], str(speakers))
    harness.check("no tags, no speakers", provided_transcript_utils.list_speakers(
        provided_transcript_utils.read_cues(PLAIN_VTT)) == [])


def test_agreement_is_one_for_the_same_words_and_falls_with_each_changed_word():
    cues = provided_transcript_utils.read_cues(VTT)
    same = [{"start": cue["start"], "end": cue["end"], "text": cue["text"]} for cue in cues]
    harness.check("the same words agree fully", provided_transcript_utils.compute_agreement(cues, same) == 1.0)
    changed = [{**same[0], "text": "Welcome to the building review."}, *same[1:]]
    rate = provided_transcript_utils.compute_agreement(cues, changed)
    harness.check("one word of 23 changed agrees on the rest", 0.9 < rate < 1.0, str(rate))


def test_a_provided_transcript_with_no_words_has_no_agreement_rather_than_none_at_all():
    """Nothing to compare is not a transcript that disagrees on every word: it is recorded as no value."""

    segments = [{"start": 0.0, "end": 2.0, "text": "Welcome to the review."}]
    harness.check("no cues, no agreement", provided_transcript_utils.compute_agreement([], segments) is None,
                  str(provided_transcript_utils.compute_agreement([], segments)))


def test_a_transcript_beside_the_only_recording_of_a_folder_belongs_to_it():
    folder = _make_folder("meeting-recording.mp4", "meeting-en-US.vtt")
    found = provided_transcript_service.find_provided_transcript(folder / "meeting-recording.mp4")
    harness.check("the folder's transcript is found, whatever its name", found == folder / "meeting-en-US.vtt", str(found))


def test_in_a_folder_of_several_recordings_only_a_transcript_named_after_one_belongs_to_it():
    folder = _make_folder("alpha.mp4", "beta.mp4", "other.vtt", "beta-en-US.vtt")
    harness.check("a transcript named after another recording is not alpha's",
                  provided_transcript_service.find_provided_transcript(folder / "alpha.mp4") is None)
    harness.check("beta's is found by its name",
                  provided_transcript_service.find_provided_transcript(folder / "beta.mp4") == folder / "beta-en-US.vtt")


def test_the_record_holds_the_file_its_size_its_agreement_and_its_speakers():
    folder = _make_folder("meeting.mp4", "meeting.vtt")
    segments = [{"start": cue["start"], "end": cue["end"], "text": cue["text"]}
                for cue in provided_transcript_utils.read_cues(VTT)]
    record = provided_transcript_service.describe_provided_transcript(folder / "meeting.mp4", segments)
    harness.check("the file, its cues and words", (record["file"], record["cues"], record["words"])
                  == ("meeting.vtt", 3, 23), str(record))
    harness.check("its agreement with phase 1", record["agreement"] == 1.0, str(record))
    harness.check("and the speakers it names", record["speakers"] == ["Lee Park", "Dana Weber"], str(record))
    harness.check("no transcript, no record",
                  provided_transcript_service.describe_provided_transcript(_make_folder("x.mp4") / "x.mp4", []) is None)


def test_a_folder_holding_one_recording_is_an_inbox_item():
    root = Path(tempfile.mkdtemp())
    inbox_root = root / "inbox"
    inbox_root.mkdir()
    (inbox_root / "loose.mp4").write_text("fixture", encoding="utf-8")
    (inbox_root / "dana").mkdir()
    (inbox_root / "dana" / "review.mp4").write_text("fixture", encoding="utf-8")
    (inbox_root / "dana" / "review-en-US.vtt").write_text(VTT, encoding="utf-8")
    (inbox_root / "parts").mkdir()
    for name in ("part-1.mp4", "part-2.mp4"):
        (inbox_root / "parts" / name).write_text("fixture", encoding="utf-8")
    (inbox_root / "notes").mkdir()
    items = inbox_service.list_inbox_videos(inbox_root)
    harness.check("the folder's one video, the loose video and the folder of parts, in name order",
                  items == [inbox_root / "dana" / "review.mp4", inbox_root / "loose.mp4", inbox_root / "parts"], str(items))
    harness.check("a folder of several recordings is one meeting in parts, listed as the folder, never part by part",
                  not any("part-" in item.name for item in items), str(items))


def test_a_recording_takes_its_own_transcript_not_one_whose_name_only_starts_with_its_own():
    folder = Path(tempfile.mkdtemp())
    for name in ("call.mp4", "call-2.mp4", "call.vtt", "call-2.vtt"):
        (folder / name).write_bytes(b"x")
    harness.check("call.mp4 takes call.vtt", provided_transcript_service.find_provided_transcript(folder / "call.mp4")
                  == folder / "call.vtt", str(provided_transcript_service.find_provided_transcript(folder / "call.mp4")))
    harness.check("call-2.mp4 takes call-2.vtt", provided_transcript_service.find_provided_transcript(folder / "call-2.mp4")
                  == folder / "call-2.vtt")


def test_a_cue_timing_without_hours_is_read():
    """WebVTT makes the hours optional: `00:01.000 --> 00:04.000` is a cue like any other."""

    cues = provided_transcript_utils.read_cues("WEBVTT\n\n00:01.000 --> 00:04.000\n<v Jonas>Welcome to the review.\n")
    harness.check("the cue is read with its times and speaker",
                  [(cue["start"], cue["end"], cue["speaker"]) for cue in cues] == [(1.0, 4.0, "Jonas")], str(cues))
