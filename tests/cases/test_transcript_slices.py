"""Each frame is handed the speech around it: everything said before it, and what is said while it
is on screen. REQUIREMENTS.md R2's four acceptance cases are the first four tests here."""

from support import harness

from utils import transcript_slice_utils


def _build_segment(start: float,
                   end: float,
                   text: str) -> dict:
    """A segment shaped as the transcription writes it, its words spread evenly across it."""

    tokens = text.split()
    step = (end - start) / len(tokens)
    words = [{"word": f" {token}", "start": start + index * step, "end": start + (index + 1) * step}
             for index, token in enumerate(tokens)]

    return {"text": f" {text}", "start": start, "end": end, "words": words}


SEGMENTS = [_build_segment(0.0, 4.0, "Guten Abend meine Damen"),
            _build_segment(4.0, 10.0, "89 Prozent wollen billigeren Sprit"),
            _build_segment(12.0, 18.0, "Die Wahl ist im September")]
TIMESTAMPS = [3.0, 9.0, 15.0]
DURATION_S = 20.0


def _build_slices(**changes) -> list[dict]:
    arguments = {"segments": SEGMENTS, "timestamps": TIMESTAMPS, "duration_s": DURATION_S,
                 "before_seconds": None, **changes}

    return transcript_slice_utils.build_frame_slices(**arguments)


def test_frame_0_has_an_empty_before_and_a_current_that_starts_at_zero():
    first = _build_slices()[0]
    harness.check("frame 0 has heard nothing yet", first["before"] == "", repr(first["before"]))
    harness.check("frame 0's current slice starts at 0, not at its own timestamp",
                  first["current"].startswith("Guten Abend"), repr(first["current"]))


def test_the_last_frames_current_runs_to_the_end_of_the_video():
    last = _build_slices()[-1]
    harness.check("the last frame's slice reaches the final word", last["current"].endswith("September"),
                  repr(last["current"]))


def test_a_figure_spoken_while_a_frame_is_on_screen_is_in_that_frames_current_slice():
    slices = _build_slices()
    harness.check("the figure lands in the frame whose window holds it", "89 Prozent" in slices[0]["current"],
                  repr(slices[0]["current"]))
    harness.check("and is history for the next frame", "89 Prozent" in slices[1]["before"], repr(slices[1]["before"]))


def test_a_video_with_no_speech_still_gets_a_slice_pair_per_frame():
    slices = _build_slices(segments=[])
    harness.check("one slice pair per frame", len(slices) == len(TIMESTAMPS))
    harness.check("both blocks empty", all(entry == {"before": "", "current": ""} for entry in slices), str(slices))


def test_every_word_lands_in_exactly_one_current_slice():
    """Windows tile the video, so no word is dropped between two frames and none is said twice."""

    spoken = " ".join(entry["current"] for entry in _build_slices()).split()
    expected = " ".join(segment["text"] for segment in SEGMENTS).split()
    harness.check("the current slices, joined, are the whole transcript in order", spoken == expected,
                  f"{spoken} vs {expected}")


def test_a_word_the_speech_model_split_in_two_is_joined_back_without_a_space():
    """The speech model marks a new word with a LEADING space, and a continuation with none. Measured
    on the first real run: a hyphenated word split in two came out with a space inside it."""

    split_word = [{"text": " the AcmeSync-Gateway in Jonas", "start": 0.0, "end": 3.0,
                   "words": [{"word": " the", "start": 0.0, "end": 0.3}, {"word": " AcmeSync", "start": 0.3, "end": 0.8},
                             {"word": "-Gateway", "start": 0.8, "end": 1.6}, {"word": " in", "start": 1.6, "end": 1.9},
                             {"word": " Jonas", "start": 1.9, "end": 2.6}]}]
    current = _build_slices(segments=split_word)[0]["current"]
    harness.check("a continuation is attached to the word before it", current == "the AcmeSync-Gateway in Jonas",
                  repr(current))


def test_a_segment_with_no_word_timings_is_placed_by_its_own_start():
    segments = [{"text": " ohne Wortzeiten", "start": 9.5, "end": 11.0, "words": []}]
    slices = _build_slices(segments=segments)
    harness.check("it lands in the window that holds its start", slices[1]["current"] == "ohne Wortzeiten",
                  str(slices))


def test_a_cap_on_the_past_drops_whole_segments_and_never_cuts_a_sentence():
    # Frame 2 is at 15 s, so a 6 s cap reaches back to 9 s: the segment ending at 4 s is outside it,
    # and the one ending at 10 s reaches into it.
    capped = _build_slices(before_seconds=6.0)[2]
    harness.check("a segment that ended before the cap window is dropped whole",
                  "Guten Abend" not in capped["before"], repr(capped["before"]))
    harness.check("a segment that reaches into the cap window is kept whole, from its first word",
                  capped["before"].startswith("89 Prozent"), repr(capped["before"]))


def test_timestamps_out_of_order_are_refused():
    try:
        _build_slices(timestamps=[9.0, 3.0])
    except ValueError as exc:
        harness.check("the refusal says the frames must be in order", "order" in str(exc), str(exc))
    else:
        harness.check("frames out of chronological order are refused", False)


def test_a_sentence_said_across_a_frame_change_stays_with_the_frame_it_started_over():
    """Cut at the next frame's timestamp, "we never told it to store that result" | "anywhere, but …" split one
    sentence, and three steps disagreed about which frame it belonged to (2026-10-13)."""

    segments = [_build_segment(0.0, 6.0, "The Gateway retries. Jonas never stored the result"),
                _build_segment(6.0, 12.0, "anywhere, sadly. Then AcmeSync answers.")]
    slices = transcript_slice_utils.build_frame_slices(segments, [0.0, 5.5], 12.0, None)
    harness.check("the first frame ends with the whole sentence", slices[0]["current"].endswith("anywhere, sadly."),
                  repr(slices[0]["current"]))
    harness.check("the next frame starts with a whole sentence", slices[1]["current"] == "Then AcmeSync answers.",
                  repr(slices[1]["current"]))
    harness.check("the next frame's before holds it", slices[1]["before"].endswith("anywhere, sadly."),
                  repr(slices[1]["before"]))
    run_on = [_build_segment(0.0, 30.0, "one two three four five six " + " ".join(f"w{n}" for n in range(30)) + ".")]
    far = transcript_slice_utils.build_frame_slices(run_on, [0.0, 5.0], 30.0, None)
    harness.check("a sentence ending past the reach leaves the cut at the timestamp", far[1]["current"].startswith("w0"),
                  repr(far[1]["current"][:20]))


def test_a_sentence_with_no_punctuation_ends_at_the_next_pause_of_the_speech_defect():
    # with no full stop within reach, a frame's words stopped at its end mid-sentence ("me tries to install"), and the
    # critique asked the notes to record that cut-off phrase; now the window runs to the speech model's next segment
    segments = [_build_segment(0.0, 6.0, "Jonas opens the Gateway console and then tries to install"),
                _build_segment(6.0, 9.0, "a new AcmeSync package"),
                _build_segment(9.0, 14.0, "which fills the whole disk")]
    slices = transcript_slice_utils.build_frame_slices(segments, [0.0, 7.0], 14.0, None)
    harness.check("the first frame keeps its words up to the pause", slices[0]["current"].endswith("AcmeSync package"),
                  repr(slices[0]["current"]))
    harness.check("the next frame starts after the pause", slices[1]["current"] == "which fills the whole disk",
                  repr(slices[1]["current"]))
