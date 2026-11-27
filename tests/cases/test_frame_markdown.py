"""A frame's notes as markdown: the model's answer read into fields, and the
fields written back as the same document, which is how they are stored.

The screen is an outline of its parts, one per line, with where each sits and its exact text quoted, so a quote is
never read without knowing whether it is a title, a button or a bookmark (the owner, 2026-09-28).
"""

from pathlib import Path

from support import harness

from schemas import frame_record
from utils import addition_utils
from utils import frame_markdown_utils
from utils import mermaid_text_utils

INSTRUCTIONS = Path(__file__).resolve().parents[2] / "agent-context" / "instructions"

SCREEN = """- the AcmeSync settings page: fills the frame; white background, dark blue header bar
  - title "Retry settings": top left, large dark grey text
  - navigation panel: left edge, a fifth of the width, light grey
    - "Overview": menu entry, highlighted blue, open
    - "Retries": menu entry
  - form "Retry budget": centre, under the title
    - field "Max attempts" = "3": selected, cursor in it
    - button "Save": bottom right, blue"""

ANSWER = f"""Frame 12 shows the settings. I will write the notes now.

# Frame 12 · web application

## Facts
- The AcmeSync gateway retries a failed call at most 3 times.
- Jonas decides the retry budget per client,
  and nobody else may change it.

## Screen
{SCREEN}

## Diagram
```mermaid
flowchart LR
  A["Client"] --> B["Gateway"]
```

## Speech visual alignment
The speaker gives the reason for 3 attempts, which the form does not show.

## What happens
The presenter opens the retry form and types the budget.

## Builds on
- frame 9: the same settings page, now with the retry form open

## Contradicts earlier
(none)

## Open questions
- The unit of the retry delay is not said.

## Web addresses
- https://acme.example.com/settings/retries
  - what it is: the gateway's retry settings page
  - how it appears: open in the browser, being edited

## Action items
(none)

## Questions asked
- Can a client change its own budget? No, only Jonas can.

## Screenshots
- the retry form in the centre
  - importance: needed
  - why: a reader setting a budget must see which field holds it

## Tags
retry policy, AcmeSync gateway, reference

# Frame 13 · web application

## Facts
- Saving applies the budget at once.

## Screen
- the AcmeSync settings page: as in frame 12, the form saved
  - banner "Saved": top, green

## What happens
The presenter saves the form.

## Tags
retry policy

# Batch

## What happens in the batch
The presenter sets a retry budget and saves it.

## Atmosphere
Plain, nobody speaks but the presenter.
"""


# The answer's frames without its own `# Batch`, for a test that writes a batch of its own: a second `# Batch` is a
# shape no answer has
_FRAMES_ONLY = ANSWER.split("\n# Batch")[0]


def _replace_in(text: str,
                old: str,
                new: str,
                count: int = -1) -> str:
    """`text` with `old` replaced by `new`; refused when `text` does not hold `old`, so a test never runs on an input
    its change missed."""

    if old not in text:
        raise ValueError(f"the text holds no {old!r}")

    return text.replace(old, new, count)


def test_every_section_is_read_into_its_field():
    parsed = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)
    first = parsed["frames"][0]
    harness.check("the answer reads with no error", parsed["errors"] == [], str(parsed["errors"]))
    harness.check("two frames, in the order written", [record["frame"] for record in parsed["frames"]] == [12, 13])
    harness.check("the title gives the screen kind", first["screen_kind"] == "web application", first["screen_kind"])
    harness.check("a wrapped list line joins the entry above it", first["facts"][1]
                  == "Jonas decides the retry budget per client, and nobody else may change it.", str(first["facts"]))
    harness.check("the screen outline is kept as written, indentation and all", first["screen"] == SCREEN, first["screen"])
    harness.check("the diagram is its Mermaid source, without the fence",
                  first["diagram"] == 'flowchart LR\n  A["Client"] --> B["Gateway"]', first["diagram"])
    harness.check("a paragraph is one text", first["third_person_description"]
                  == "The presenter opens the retry form and types the budget.")
    harness.check("(none) is an empty list", first["contradicts_earlier"] == [] and first["action_items"] == [])
    harness.check("an address keeps what it is and how it appears", first["urls"] == [
        {"url": "https://acme.example.com/settings/retries", "what_it_is": "the gateway's retry settings page",
         "how_it_appears": "open in the browser, being edited"}], str(first["urls"]))
    harness.check("a screenshot keeps its part, importance and why", first["screenshots"] == [
        {"part": "the retry form in the centre", "importance": "needed",
         "why": "a reader setting a budget must see which field holds it"}], str(first["screenshots"]))
    harness.check("tags are split at the commas", first["tags"] == ["retry policy", "AcmeSync gateway", "reference"])
    harness.check("a section left out is an empty field", parsed["frames"][1]["urls"] == []
                  and parsed["frames"][1]["diagram"] == "")
    harness.check("the batch is read", parsed["batch"] == {
        "batch_title": "", "batch_summary": "The presenter sets a retry budget and saves it.",
        "atmosphere": "Plain, nobody speaks but the presenter."}, str(parsed["batch"]))


def test_every_field_is_filled_and_nothing_else():
    first = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    harness.check("a record holds exactly the fields of the contract", tuple(first) == frame_record.FRAME_FIELDS,
                  str(tuple(first)))


def test_what_the_model_thinks_first_is_not_read():
    thinking = "<think>\n# Frame 99 · draft\n## Facts\n- a draft\n</think>\n" + ANSWER
    parsed = frame_markdown_utils.parse_answer(thinking, is_addition=False)
    harness.check("a document inside the thinking is not an answer", [record["frame"] for record in parsed["frames"]]
                  == [12, 13] and parsed["errors"] == [], str(parsed))


def test_a_heading_the_notes_do_not_have_is_refused_by_name():
    parsed = frame_markdown_utils.parse_answer("# Frame 3 · slide\n\n## Summary\nA summary.\n\n## Facts\n- A fact.\n",
                                               is_addition=False)
    error = parsed["errors"][0] if parsed["errors"] else {}
    harness.check("the error names its frame, the heading and the real ones",
                  len(parsed["errors"]) == 1 and error["frame"] == 3 and '"## Summary"' in error["text"]
                  and '"## Facts"' in error["text"], str(parsed["errors"]))
    harness.check("a frame with an unreadable section is not handed on half read", parsed["frames"] == [])
    unknown = frame_markdown_utils.parse_answer("# Summary\nText.\n", is_addition=False)
    harness.check("a document that is not a frame or a batch is the answer's fault, not a frame's",
                  len(unknown["errors"]) == 1 and unknown["errors"][0]["frame"] is None
                  and '"# Summary"' in unknown["errors"][0]["text"], str(unknown["errors"]))
    mixed = frame_markdown_utils.parse_answer("# Frame 3\n## Summary\nx\n# Frame 4\n## Facts\n- A fact.\n",
                                              is_addition=False)
    harness.check("a frame that read cleanly is kept beside one that did not",
                  [record["frame"] for record in mixed["frames"]] == [4]
                  and [error["frame"] for error in mixed["errors"]] == [3], str(mixed))
    harness.check("an answer with no document at all says so",
                  frame_markdown_utils.parse_answer("Just prose.", is_addition=False)["errors"] != [])
    twice = frame_markdown_utils.parse_answer("# Frame 3\n## Facts\n- a\n# Frame 3\n## Facts\n- b\n",
                                              is_addition=False)
    harness.check("a frame written twice is read from its last copy, and no copy is refused for it",
                  [record["facts"] for record in twice["frames"]] == [["b"]] and twice["errors"] == [], str(twice))
    detail = frame_markdown_utils.parse_answer("# Frame 3\n## Screenshots\n- the form\n  - size: big\n",
                                               is_addition=False)
    harness.check("an entry's detail with an unknown label is refused, naming the labels there are",
                  any("importance" in error["text"] and "size: big" in error["text"] for error in detail["errors"]),
                  str(detail["errors"]))


def test_a_second_pass_that_starts_a_frame_over_is_read_from_its_last_copy_defect():
    # A repair wrote a frame's document with its own deliberation in it, then the frame again whole, and both copies
    # were refused. The last copy is the answer now; the draft's record and errors go
    answer = ("# Frame 3\n\n## What changed\nAdded a fact (wait, no, that is the other frame).\n\n"
              "# Frame 3\n\n## What changed\nAdded the missing fact.\n\n## Facts\n- A fact.\n"
              "\n# Frame 4\n\n## What changed\nx\n\n## Facts\n- one\n# Frame 4\n\n## Summary\nbroken\n"
              "# Frame 4\n\n## What changed\ny\n\n## Facts\n- two\n")
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=True)
    harness.check("each frame is read once, from its last copy, with no error",
                  [(record["frame"], record["what_changed"]) for record in parsed["frames"]]
                  == [(3, "Added the missing fact."), (4, "y")] and parsed["errors"] == [], str(parsed))


def test_a_sentence_said_over_a_frame_that_no_note_carries_is_named_defect():
    # A critique answered "Nothing to criticise" while a frame's speech held facts no note had (DEFECTS 426). The
    # sentences of the speech whose content words the notes hold a third of or less are named, the longest two
    record = {"frame": 7, "facts": ["The gateway retries a failing call three times."],
              "take_aways": ["- the gateway retries → the retry setting: three tries"], "screen": '- slide: white\n  - title "Gateway retries"'}
    speech = ("Speaker B: The gateway retries a failing call three times. "
              "Every cloud provider patched the vulnerability out of their releases long before the community fix arrived. "
              "Okay then. "
              "Nobody maintains the legacy exporter anymore, so migrating the dashboards away from it is advisable soon.")
    found = frame_markdown_utils.list_thinly_noted_sentences(record, speech)
    harness.check("the two sentences the notes carry nothing of are named, in the order said",
                  found == ["Every cloud provider patched the vulnerability out of their releases long before the community fix arrived.",
                            "Nobody maintains the legacy exporter anymore, so migrating the dashboards away from it is advisable soon."],
                  str(found))
    harness.check("a sentence the notes hold, and small talk, are not named",
                  all("retries a failing call" not in sentence and "Okay" not in sentence for sentence in found), str(found))


def test_a_heading_inside_a_diagram_is_the_diagram():
    answer = "# Frame 3\n\n## Diagram\n```mermaid\nflowchart LR\n# Frame 4\n## Facts\nA --> B\n```\n"
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
    harness.check("one frame, its diagram whole", len(parsed["frames"]) == 1
                  and "# Frame 4" in parsed["frames"][0]["diagram"], str(parsed))


def test_a_flowchart_with_no_arrow_is_no_diagram():
    """A flowchart of boxes and no arrow draws no flow: NIST's cover and function list became Mermaid blocks of six
    nodes and no arrow, which the published document showed as six loose boxes. A flow keeps its diagram."""

    loose = "# Frame 3\n\n## Diagram\n```mermaid\nflowchart LR\n    A[Govern]\n    B[Identify]\n```\n"
    flow = "# Frame 3\n\n## Diagram\n```mermaid\nflowchart LR\n    A[Govern] --> B[Identify]\n```\n"
    harness.check("boxes with no arrow are no diagram",
                  frame_markdown_utils.parse_answer(loose, is_addition=False)["frames"][0]["diagram"] == "", loose)
    harness.check("a flow keeps its diagram",
                  "-->" in frame_markdown_utils.parse_answer(flow, is_addition=False)["frames"][0]["diagram"], flow)


def test_a_stored_record_reads_back_as_it_was_written():
    for record in frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"]:
        text = frame_markdown_utils.render_frame_document(record)
        again = frame_markdown_utils.parse_answer(text, is_addition=False)
        harness.check(f"frame {record['frame']} round-trips", again["frames"] == [record] and not again["errors"],
                      text)
    harness.check("an empty field writes no section", "## Action items" not in frame_markdown_utils.render_frame_document(
        frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]))


def test_notes_a_past_run_wrote_under_a_former_heading_still_read_back():
    for former, field in frame_record.FORMER_HEADINGS.items():
        parsed = frame_markdown_utils.parse_answer(f"# Frame 3 · slide\n\n## {former}\n- The export runs nightly.\n",
                                                   is_addition=False)
        record = parsed["frames"][0] if parsed["frames"] else {}
        harness.check(f'"## {former}" reads into {field}',
                      record.get(field) == ["The export runs nightly."] and not parsed["errors"], str(parsed))


def test_an_entry_written_without_its_dash_starts_an_entry():
    """A batch wrote each web address as a bare line, its details indented under it, and four frames were refused
    over the missing "- "; the line is read as the entry it plainly is."""

    lines = ANSWER.splitlines()
    start = lines.index("## Web addresses")
    entry = next(index for index in range(start + 1, len(lines)) if lines[index].startswith("- "))
    lines[entry] = lines[entry][2:]
    parsed = frame_markdown_utils.parse_answer("\n".join(lines), is_addition=False)
    expected = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]["urls"]
    harness.check("read as before, with no error", not parsed["errors"] and parsed["frames"][0]["urls"] == expected,
                  str(parsed["errors"]))


def test_a_detail_value_in_backticks_or_quotes_is_read_without_them():
    """A screenshot's importance written `` `needed` `` was refused as no importance at all."""

    for marked in ("`needed`", '"needed"'):
        parsed = frame_markdown_utils.parse_answer(_replace_in(ANSWER, "  - importance: needed", f"  - importance: {marked}"),
                                                   is_addition=False)
        harness.check(f"{marked} reads as needed", parsed["frames"][0]["screenshots"][0]["importance"] == "needed",
                      str(parsed["frames"][0]["screenshots"]))


def test_a_field_the_document_has_no_section_for_is_never_dropped_in_silence():
    record = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    try:
        frame_markdown_utils.render_frame_document({**record, "on_screen": ["a quote"]})
        harness.check("an unknown field raises", False)
    except ValueError as exc:
        harness.check("an unknown field raises, naming it", "on_screen" in str(exc), str(exc))


ADDITION = """# Frame 12 · settings page

## What changed
The notes missed the budget's owner and the save banner.

## Facts
- Only Jonas may change a client's budget.

## Screen
- the AcmeSync settings page: fills the frame
  - title "Retry settings": top left
  - banner "Saved": top, green

## Tags
access control

# Batch

## What happens in the batch
The presenter sets a budget, saves it and names who owns it.

## Atmosphere
Plain.
"""


def test_the_empty_mark_in_other_words_is_an_empty_field():
    """A worker wrote the empty mark as "(none spoken)." or "nothing said", and each became a note the document had to
    cite (2026-09-30: a chapter part blocked on a note saying only that nothing was said)."""

    said = "The speaker gives the reason for 3 attempts, which the form does not show."
    for spelling in ("(none spoken).", "(nothing is spoken)", "nothing said", "None."):
        parsed = frame_markdown_utils.parse_answer(_replace_in(ANSWER, said, spelling), is_addition=False)
        alignment = parsed["frames"][0]["speech_visual_alignment"]
        harness.check(f"{spelling!r} is an empty field", alignment == "", alignment)
    parsed = frame_markdown_utils.parse_answer(_replace_in(ANSWER, said, "Nothing said on screen matches the budget."),
                                               is_addition=False)
    harness.check("a sentence that begins with those words is kept",
                  parsed["frames"][0]["speech_visual_alignment"] != "")


def test_the_empty_mark_followed_by_any_reason_is_no_fact_defect():
    # A lab photo's facts held "(none) - The slide contains no new textual facts, only a visual of the setup.", read as a
    # fact; each earlier wording was added to the reader one at a time. Now the empty word opening the section and
    # followed by a separator or a bracket is the empty mark, whatever its reason says
    for spelling in ("(none) - The slide contains no new textual facts, only a visual of the setup.",
                     "None (concluding theoretical introduction).", "Nothing: the same slide as before.",
                     "(none); the speaker only greets the audience."):
        answer = ANSWER.replace("- Jonas decides the retry budget per client,\n  and nobody else may change it.\n", "")
        parsed = frame_markdown_utils.parse_answer(answer.replace("The AcmeSync gateway retries a failed call at most 3 times.",
                                                                  spelling), is_addition=False)
        harness.check(f"{spelling!r} is no fact", parsed["frames"][0]["facts"] == [], str(parsed["frames"][0]["facts"]))
    answer = ANSWER.replace("The AcmeSync gateway retries a failed call at most 3 times.",
                            "None of the retries reach the client when the gateway is down.")
    harness.check("a fact that opens with the word in a sentence is kept",
                  len(frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"][0]["facts"]) == 2)


def test_a_fact_saying_there_is_nothing_new_is_no_fact():
    """A frame's facts held "(None new; speech is brief acknowledgment)", which phase 4 had to cite as a note (lecture-9
    chapter 9, 2026-10-21)."""

    for spelling in ("(None new; speech is brief acknowledgment).", "(nothing new: the same slide)", "None new.",
                     "(None from speech)."):
        answer = ANSWER.replace("- Jonas decides the retry budget per client,\n  and nobody else may change it.\n", "")
        parsed = frame_markdown_utils.parse_answer(answer.replace("The AcmeSync gateway retries a failed call at most 3 times.",
                                                                  spelling), is_addition=False)
        facts = parsed["frames"][0]["facts"]
        harness.check(f"{spelling!r} is no fact", facts == [], str(facts))
    answer = ANSWER.replace("The AcmeSync gateway retries a failed call at most 3 times.",
                            "Nothing new is needed on the client side when the gateway retries.")
    harness.check("a fact that opens with those words is kept",
                  len(frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"][0]["facts"]) == 2)


def test_an_addition_adds_to_lists_and_replaces_texts():
    parsed = frame_markdown_utils.parse_answer(ADDITION, is_addition=True)
    addition = parsed["frames"][0]
    harness.check("the addition reads with no error", parsed["errors"] == [], str(parsed["errors"]))
    harness.check("what changed is read", addition["what_changed"].startswith("The notes missed"))
    harness.check("a list section is an addition", addition["added_facts"] == ["Only Jonas may change a client's budget."]
                  and addition["added_tags"] == ["access control"], str(addition))
    harness.check("the screen is a whole new outline", addition["screen"].count("\n") == 2, addition["screen"])
    harness.check("the title's kind is a new screen kind", addition["screen_kind"] == "settings page")
    harness.check("the batch comes with it", parsed["batch"]["atmosphere"] == "Plain.")
    stored = {"is_nothing_to_change": False, "additions": [addition], **parsed["batch"]}
    again = frame_markdown_utils.parse_answer(frame_markdown_utils.render_second_pass_document(stored), is_addition=True)
    harness.check("a stored second pass reads back as it was", again["frames"] == [addition]
                  and again["batch"] == parsed["batch"] and not again["errors"], str(again))
    nothing = frame_markdown_utils.parse_answer("Looked again.\n\n# Nothing to change\n", is_addition=True)
    harness.check("nothing to change is read", nothing["is_nothing_to_change"] and not nothing["frames"]
                  and not nothing["errors"])
    harness.check("and outside the second pass it is refused",
                  frame_markdown_utils.parse_answer("# Nothing to change\n", is_addition=False)["errors"] != [])


def test_the_screen_outline_is_read_part_by_part():
    parts, problems = frame_markdown_utils.read_screen_parts(SCREEN)
    harness.check("every line is a part, at its depth", [part.depth for part in parts] == [0, 1, 1, 2, 2, 1, 2, 2]
                  and problems == [], str(parts) + str(problems))
    harness.check("the quotes are listed in reading order", frame_markdown_utils.list_screen_quotes(SCREEN)[:3]
                  == ["Retry settings", "Overview", "Retries"])
    harness.check("the title is the part named a title", frame_markdown_utils.find_screen_title(SCREEN) == "Retry settings")
    harness.check("no part named a title is no title, not the first quote",
                  frame_markdown_utils.find_screen_title('- a page\n  - "Overview": menu entry') == "")
    _, bad = frame_markdown_utils.read_screen_parts("- a page\n      - too deep\nloose text")
    harness.check("a part indented too deep and a line that is no part are both named", len(bad) == 2
                  and "too deep" in bad[0] and "loose text" in bad[1], str(bad))


def test_the_instructions_teach_every_heading_the_parser_reads():
    instructions = (INSTRUCTIONS / "_shared" / "frames-processing.md").read_text(encoding="utf-8")
    second = (INSTRUCTIONS / "_shared" / "second-pass.md").read_text(encoding="utf-8")
    headings = [heading for heading, _ in frame_record.FRAME_SECTIONS.values()]
    headings += list(frame_record.BATCH_SECTIONS.values())
    missing = [heading for heading in headings if f"## {heading}" not in instructions]
    harness.check("frames_processing.md shows every section's heading", not missing, str(missing))
    harness.check("frames_processing.md shows the frame and batch titles", f"# {frame_record.FRAME_TITLE} " in instructions
                  and f"# {frame_record.BATCH_TITLE}" in instructions)
    harness.check("second_pass.md shows its own two headings",
                  f"## {frame_record.WHAT_CHANGED_HEADING}" in second
                  and f"# {frame_record.NOTHING_TO_CHANGE_TITLE}" in second)


def test_the_text_of_the_first_frames_and_of_most_frames_is_listed_for_the_speakers():
    """A presenter named in every slide's footer, and the agenda step, which never saw a footer, wrote `speakers: []`
    (2026-10-01)."""

    footer = '  - footer "AcmeSync GmbH, Dana Weber (Team Billing)": bottom centre'
    screens = [f'- slide\n  - title "{title}"\n{footer}' for title in ("Refund rules", "Limits", "Approvals")]
    screens.append('- slide\n  - title "Questions"')
    opening, recurring = frame_markdown_utils.list_opening_and_recurring_text(screens)
    harness.check("the first frames' texts are listed", "Refund rules" in opening and "Limits" in opening, str(opening))
    harness.check("the footer on most frames is the recurring text",
                  recurring == ["AcmeSync GmbH, Dana Weber (Team Billing)"], str(recurring))


def test_an_answer_wrapped_in_one_fence_is_read_as_if_it_had_none():
    body = ('# Batch\n\n## Open questions\n- What does "ACME" stand for?\n- Who owns the gateway?\n\n'
            '## Answered questions\n- (none)')
    for fence, language in (("~~~", "markdown"), ("```", "markdown"), ("~~~", "")):
        parsed = frame_markdown_utils.parse_answer(f"{fence}{language}\n{body}\n{fence}\n", is_addition=False)
        batch = parsed["batch"] or {}
        harness.check(f"a {fence}{language} fence around the whole answer leaves no entry behind",
                      len(batch.get("open_questions") or []) == 2 and batch.get("answered_questions") == []
                      and parsed["errors"] == [], str(parsed))
    diagram = '# Frame 3 · slide\n\n## Diagram\n```mermaid\nflowchart LR\n    A --> B\n```\n'
    harness.check("a fence inside the answer is the answer's own, never taken off",
                  frame_markdown_utils.unwrap_answer_fence(diagram.splitlines()) == diagram.splitlines())


def test_a_closing_fence_with_no_opening_one_is_no_part_of_the_last_entry():
    """An answer written from a compacted thinking opened with no fence and ended its last question with "~~~"
    (Lecture 9, frames 96-103)."""

    answer = '# Batch\n\n## Open questions\n- Why does the editor show another return value?\n~~~'
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
    harness.check("the question is read without the fence",
                  (parsed["batch"] or {}).get("open_questions") == ["Why does the editor show another return value?"],
                  str(parsed))
    block = '# Frame 3 · editor\n\n## As markdown\n```python\nreturn "hi"\n```'
    harness.check("a fence closing the answer's own code block is kept",
                  frame_markdown_utils.unwrap_answer_fence(block.splitlines()) == block.splitlines())


def test_an_answer_fenced_after_its_draft_is_read_alone_defect():
    # An answers reply written from a compacted thinking wrote a draft, reasoned on, then gave its settled answer in a
    # "~~~markdown" block; read whole, the draft and the fence were refused as answers (DEFECTS 568). Now the block is it
    draft = ('# Batch\n\n## Open questions\n- Who owns the Gateway? → Jonas does, maybe.\n\n## Answered questions\n'
             '- Who owns the Gateway? → Jonas.\n\nWait, I must check the notes again: nothing says so.\n\n')
    settled = '# Batch\n\n## Open questions\n- Who owns the Gateway?\n\n## Answered questions\n(none)'
    parsed = frame_markdown_utils.parse_answer(f"{draft}~~~markdown\n{settled}\n~~~\n", is_addition=False)
    batch = parsed["batch"] or {}
    harness.check("the fenced answer at the end is read alone",
                  batch.get("open_questions") == ["Who owns the Gateway?"] and batch.get("answered_questions") == [],
                  str(parsed))
    frame = '# Frame 3 · editor\n\nSome words.\n\n## As markdown\n```\nreturn "hi"\n```'
    harness.check("a code block ending a frame's notes is no answer of its own",
                  frame_markdown_utils.unwrap_answer_fence(frame.splitlines()) == frame.splitlines())


def test_a_second_look_ending_on_a_code_block_keeps_its_earlier_frames_defect():
    # Defect: a second look whose last frame ended on its `## As markdown` code block was read as that frame alone: the
    # bare fence closing the frame before's block was taken for an answer fence. Fix: fences are read forward
    answer = ('# Frame 4 · terminal\n\n## Facts\n- The gateway retries 3 times.\n\n## As markdown\n```text\nretries 3\n```\n\n'
              '# Frame 5 · terminal\n\n## Facts\n- The ledger closes at 18:00.\n\n## As markdown\n```text\nclosed\n```')
    frames = [frame["frame"] for frame in frame_markdown_utils.parse_answer(answer, is_addition=True)["frames"]]
    harness.check("both frames are read", frames == [4, 5], str(frames))


def test_a_question_kept_open_is_judged_by_its_question_never_by_its_reason():
    """The answers step writes each open question with why it stays open after the arrow; a reason saying the
    transcript cuts off dropped the question itself, and the answer was refused for losing it."""

    body = ('# Batch\n\n## Open questions\n'
            '- Who owns the gateway? → The transcript cuts off before the owner is named, so it stays open.\n'
            '- The sentence breaks off at "so the". → nothing follows in this batch\n\n## Answered questions\n- (none)')
    batch = frame_markdown_utils.parse_answer(body, is_addition=False)["batch"] or {}
    questions = batch.get("open_questions") or []
    harness.check("the real question stays, the broken-off one goes",
                  len(questions) == 1 and "Who owns the gateway?" in str(questions[0]), str(questions))


def test_an_answer_with_no_heading_is_told_both_headings_an_answer_can_open_with():
    """A batch's open questions answered as bare lines, with no heading, were refused with "no # Frame N heading"
    alone: a step whose answer belongs under "# Batch" was told the wrong heading."""

    errors = frame_markdown_utils.parse_answer("- Who owns the gateway?", is_addition=False)["errors"]
    harness.check("the refusal names both headings", any('"# Frame N' in error["text"] and '"# Batch"' in error["text"]
                                                         for error in errors), str(errors))


def test_a_summary_line_labelled_with_its_own_section_is_kept_only_under_take_aways():
    """A critique misread the take aways' closing line as a rule for every list, and a second look added "- Facts: ..."
    and "- Screen: ..." lines. Only ## Take aways closes with a line labelled with its name; under any other section
    such a line is a summary of the section, dropped."""

    text = ('# Frame 0 · slide\n\n## Take aways\n- "the limit" → the slide: three tries\n'
            '- Take aways: the limit is three tries.\n\n## Facts\n- The gateway retries three times.\n'
            '- Facts: the gateway retries.\n\n## Screen\n- slide: white\n  - text "Limits": top\n'
            '- Screen: a slide about limits.\n')
    record = frame_markdown_utils.parse_answer(text, is_addition=False)["frames"][0]
    harness.check("the take aways keep their closing line", record["take_aways"][-1].startswith("Take aways:"),
                  str(record["take_aways"]))
    harness.check("the facts lose the summary line", record["facts"] == ["The gateway retries three times."],
                  str(record["facts"]))
    harness.check("the outline loses it too", "Screen:" not in record["screen"], record["screen"])


def test_the_take_aways_closing_label_misspelled_by_a_letter_is_written_as_take_aways():
    """A batch of eight frames closed every take aways with "- Take awies: ..."; the program reads that line by its
    label, so a misspelled label is written as the heading, and a take-away with another label stays as it is."""

    text = ('# Frame 0 · slide\n\n## Take aways\n- "the limit" → the slide: three tries\n'
            '- Key point: the gateway is strict.\n- Take awies: the limit is three tries.\n\n'
            '## Facts\n- The gateway retries three times.\n')
    record = frame_markdown_utils.parse_answer(text, is_addition=False)["frames"][0]
    harness.check("the closing line reads Take aways, and a line with another label is left alone",
                  record["take_aways"][1:] == ["Key point: the gateway is strict.", "Take aways: the limit is three tries."],
                  str(record["take_aways"]))


def test_a_note_about_how_the_worker_read_a_text_is_taken_out_of_the_outline():
    """Outlines carried "(misread by OCR)" and "(Corrected from `...`)" after a quoted text: a note about the worker's
    reading, which a reader of the document takes for part of the screen."""

    screen = ('- slide: white\n  - text "Refund limit": top (misread by OCR as "Refund lmit")\n'
              '  - code `print(n)`: centre (Corrected from `prnt(n)`)\n  - text "Total (net)": bottom')
    cleaned = frame_markdown_utils.strip_reading_notes(screen)
    harness.check("both notes go, the screen's own brackets stay",
                  cleaned == '- slide: white\n  - text "Refund limit": top\n  - code `print(n)`: centre\n'
                             '  - text "Total (net)": bottom', cleaned)


def test_the_programs_reading_written_back_as_a_heading_is_read_into_the_outline():
    """A fix pass wrote the program's own part of the outline (the text reader's lines) back as "## Text the program
    read" with a misread line corrected; refused as an unknown heading, it blocked its batch of eight frames."""

    text = ('# Frame 9 · editor\n\n## Screen\n- editor: left half\n  - code `def div(a, b):`\n\n'
            '## Text the program read\n- text "raise ValueError(\'divide by zero\')"\n')
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    screen = parsed["frames"][0]["screen"] if parsed["frames"] else ""
    harness.check("the answer is read with no error, the corrected line in the outline",
                  not parsed["errors"] and "divide by zero" in screen and "def div(a, b):" in screen,
                  f"{parsed['errors']} {screen!r}")


def test_the_follow_ups_read_the_notes_without_the_programs_reading():
    """The critique called the program's misread reader lines mistakes and two fix passes rewrote them under headings
    of their own, each blocking its batch: the follow-ups are shown the notes without that part."""

    record = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    screen = (record["screen"] + "\n- text the program read on the frame that the parts above leave out (its reader "
              'may misread a letter):\n  - text "27 | raise by zero\')"\n  - text "unix | utf-8"')
    shown = frame_markdown_utils.render_notes_to_review({**record, "screen": screen})
    harness.check("the program's part and its lines are left out, the worker's outline kept",
                  "the program read" not in shown and "raise by zero" not in shown
                  and record["screen"].splitlines()[0] in shown, shown[-400:])


def test_a_section_heading_with_a_remark_in_parentheses_is_read_as_its_section_defect():
    # A repair wrote "## Screen (Revised)" and "## Edits (Prompt text correction)", and its batch's whole answer was
    # refused for them. A section's name followed by a remark in parentheses is now read as that section
    record = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    text = _replace_in(frame_markdown_utils.render_frame_document(record), "## Screen\n", "## Screen (Revised)\n")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    harness.check("the remarked heading is read as its section, and the frame is not refused",
                  parsed["frames"] == [record] and not parsed["errors"], str(parsed["errors"]))
    harness.check("a heading that is no section's name with its remark off stays as written",
                  frame_markdown_utils.drop_heading_remark("Notes (draft)") == "Notes (draft)")


def test_a_repair_s_section_no_heading_names_is_set_aside_never_refusing_the_answer_defect():
    # One repair heading the reader could not place refused the batch's whole answer and blocked its frames.
    # Now, in a repair, such a section is set aside as an ignored edit, named on the console, and the rest is read
    text = ("# Frame 4 · terminal\n\n## What changed\nThe prompt line is corrected.\n\n"
            "## Screen as it should read\n- prompt \"run tests\"\n\n## Facts\n- The tests run with one command.\n")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    addition = parsed["frames"][0] if parsed["frames"] else {}
    harness.check("no error refuses the answer", not parsed["errors"], str(parsed["errors"]))
    harness.check("its fact is read", addition.get("added_facts") == ["The tests run with one command."], str(addition))
    harness.check("the section is set aside as an ignored edit",
                  {"action": frame_markdown_utils.IGNORED_EDIT, "id": "", "text": "## Screen as it should read"}
                  in addition.get("edits", []), str(addition.get("edits")))


def test_a_heading_misspelled_by_a_letter_or_two_is_read_as_the_one_it_misspells():
    record = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    text = _replace_in(frame_markdown_utils.render_frame_document(record), "## Screen", "## Scren")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    harness.check("the misspelled heading is read as its section, and the frame is not refused",
                  parsed["frames"] == [record] and not parsed["errors"], str(parsed["errors"]))
    unknown = _replace_in(frame_markdown_utils.render_frame_document(record), "## Screen", "## My thoughts")
    harness.check("a heading like no section is still refused by name",
                  any("My thoughts" in error["text"] for error in frame_markdown_utils.parse_answer(unknown, False)["errors"]))


def test_a_stray_fence_after_a_closed_diagram_does_not_swallow_the_sections_after_it():
    """A worker closed its diagram's fence, wrote a note, and added a second closing fence: read as an opening, it made
    every later section code, and the frame was refused for headings it had."""

    record = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)["frames"][0]
    text = _replace_in(frame_markdown_utils.render_frame_document(record),
                       "## Diagram\n", "## Diagram\n```mermaid\nflowchart LR\n  A --> B\n```\n(a note on the diagram)\n```\n", 1)
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    frame = parsed["frames"][0] if parsed["frames"] else {}
    harness.check("the sections after the stray fence are read as sections",
                  frame.get("tags") == record.get("tags")
                  and frame.get("third_person_description") == record.get("third_person_description"),
                  str(parsed["errors"]))


def test_an_empty_feedback_section_in_the_batch_is_no_section():
    """A worker wrote "## Feedback" with "(none)" in the # Batch, and all ten frames of the batch were refused."""

    parsed_answer = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)
    answer = _FRAMES_ONLY + "\n\n# Batch\n\n## Title\nOrder limits and approval\n\n## Feedback\n(none)\n"
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
    harness.check("the empty feedback section is passed over and the frames are read",
                  parsed["errors"] == [] and len(parsed["frames"]) == len(parsed_answer["frames"]), str(parsed["errors"]))


def test_a_frame_section_written_under_the_batch_is_passed_over():
    """A worker gave the # Batch a "## Tags" section, and all ten frames of the batch were refused; a frame's section
    under the batch holds nothing the batch keeps, and a frame that lost its own tags to it is still refused for them."""

    parsed_answer = frame_markdown_utils.parse_answer(ANSWER, is_addition=False)
    answer = _FRAMES_ONLY + "\n\n# Batch\n\n## Title\nOrder limits and approval\n\n## Tags\norder limits, approval\n"
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
    harness.check("the frame section is passed over and the frames are read",
                  parsed["errors"] == [] and len(parsed["frames"]) == len(parsed_answer["frames"]), str(parsed["errors"]))
    unknown = _FRAMES_ONLY + "\n\n# Batch\n\n## Title\nOrder limits and approval\n\n## Mood board\nwarm\n"
    harness.check("a heading that is no section anywhere is still refused by name",
                  any("Mood board" in error["text"] for error in
                      frame_markdown_utils.parse_answer(unknown, is_addition=False)["errors"]))


def test_an_outline_written_without_its_dashes_is_read_as_an_outline():
    """A batch wrote every frame's `## Screen` as "kind: where" lines with no "- " in front; all ten frames were refused
    for the form alone, though every line was a part (2026-10-06). An outline with no dashed line at all, each line
    a part with its colon, is read with the dashes added; one dashed line keeps the outline as written."""

    text = ("# Frame 0 · slide\n\n## Screen\nslide: white background, the whole frame\n"
            '  heading "Agenda": upper left corner\n  logo "AcmeSync": upper right corner\n')
    record = frame_markdown_utils.parse_answer(text, is_addition=False)["frames"][0]
    harness.check("the dashes are added, the indentation kept",
                  record["screen"] == '- slide: white background, the whole frame\n  - heading "Agenda": upper left corner\n'
                                     '  - logo "AcmeSync": upper right corner', record["screen"])
    mixed = "# Frame 0 · slide\n\n## Screen\n- slide: white\n  heading: top\n"
    kept = frame_markdown_utils.parse_answer(mixed, is_addition=False)["frames"][0]["screen"]
    harness.check("an outline with one dashed line is left as written", kept == "- slide: white\n  heading: top", kept)


def test_a_section_whose_only_bullet_is_the_empty_mark_is_an_empty_section():
    """A batch wrote "## Screenshots" as "- (none)", and eight frames were blocked on a screenshot whose `importance`
    was empty: the bullet was read as one entry with every detail missing."""

    answer = _replace_in(ANSWER, "## Screenshots\n- the retry form in the centre\n"
                         "  - importance: needed\n"
                         "  - why: a reader setting a budget must see which field holds it\n",
                         "## Screenshots\n- (none)\n", 1)
    parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
    frame = parsed["frames"][0]
    harness.check("the bulleted empty mark is no screenshot and no error",
                  frame["screenshots"] == [] and parsed["errors"] == [],
                  f"{frame['screenshots']} {parsed['errors']}")


def test_a_quoted_arrow_label_holding_a_pipe_draws_defect():
    # Defect: A frame was refused for `-->|"e- | PQ"|`, which Mermaid draws: the check read the label only to the pipe inside its quotes.
    # Fix: mermaid_text_utils._EDGE_LABEL reads a quoted label to its closing quote.
    piped = 'flowchart LR\n    PSII["PSII"] -->|"e- | PQ"| PQ["plastoquinone"]\n'
    harness.check("a quoted label with a pipe inside is no problem", mermaid_text_utils.find_syntax_problems(piped) == [],
                  str(mermaid_text_utils.find_syntax_problems(piped)))


def test_an_arrow_label_wrapped_in_quotes_draws_and_one_with_a_quote_inside_does_not():
    """Five frames of one batch were blocked for `-->|"Create Offer"|`, which is Mermaid's own way of writing a label
    with special characters in it (2026-10-06)."""

    wrapped = 'flowchart LR\n    A["Gateway"] -->|"Create Offer"| B["Orders"]\n'
    inside = 'flowchart LR\n    A["Gateway"] -->|returns "OK"| B["Orders"]\n'
    harness.check("a label wrapped in quotes whole is no problem",
                  mermaid_text_utils.find_syntax_problems(wrapped) == [],
                  str(mermaid_text_utils.find_syntax_problems(wrapped)))
    harness.check("a quote inside a label still is",
                  any("double quote" in problem for problem in mermaid_text_utils.find_syntax_problems(inside)),
                  str(mermaid_text_utils.find_syntax_problems(inside)))


def test_a_group_used_as_a_box_keeps_its_arrow_by_its_id_alone():
    """One frame blocked four times writing a group that is also a box, because it wanted an arrow to point into the
    group (2026-10-06). Mermaid's flowchart draws an arrow to a group by its id; the label makes the id a box too, so
    the label goes and the arrow stays on the group (2026-10-13: moved to the first box inside, an input arrow ending on
    a processor was drawn into one of its units)."""

    source = ('flowchart LR\n'
              '  A1["A1"] -->|Migration| G_t["Target landscape"]\n'
              '  subgraph G_t["Target landscape"]\n'
              '    VOFE["VOFE core"]\n'
              '    CD["Customer data"]\n'
              '  end\n')
    repaired = mermaid_text_utils.repair_group_used_as_box(source)
    harness.check("the arrow points at the group by its id alone, and the group keeps its own line",
                  '-->|Migration| G_t\n' in repaired and 'subgraph G_t["Target landscape"]' in repaired,
                  repaired)
    harness.check("the repaired diagram draws", mermaid_text_utils.find_syntax_problems(repaired) == [],
                  str(mermaid_text_utils.find_syntax_problems(repaired)))
    untouched = 'flowchart LR\n  A["One"] --> B["Two"]\n'
    harness.check("a diagram with no group is left exactly as it was",
                  mermaid_text_utils.repair_group_used_as_box(untouched) == untouched)
    empty_group = ('flowchart LR\n  A["One"] --> G_x["Group"]\n  subgraph G_x["Group"]\n  end\n')
    harness.check("a group with no box inside it is left for the check to refuse",
                  mermaid_text_utils.repair_group_used_as_box(empty_group) == empty_group)


def test_an_arrow_label_with_a_bracket_draws_only_wrapped_in_quotes():
    """`-->|Prints letter (High costs)|` ended its label at the bracket, and a published diagram showed Mermaid's parse
    error instead of drawing (2026-10-08)."""

    bare = 'flowchart LR\n    A["Shop"] -->|Sends invoice (paper)| B["Customer"]\n'
    wrapped = 'flowchart LR\n    A["Shop"] -->|"Sends invoice (paper)"| B["Customer"]\n    B -->| pays | A\n'
    harness.check("a bracket in an unquoted label is a problem, with the quoted label as its fix",
                  any('-->|"Sends invoice (paper)"|' in problem for problem in mermaid_text_utils.find_syntax_problems(bare)),
                  str(mermaid_text_utils.find_syntax_problems(bare)))
    harness.check("the same label wrapped in quotes, and a plain label with spaces, draw",
                  mermaid_text_utils.find_syntax_problems(wrapped) == [],
                  str(mermaid_text_utils.find_syntax_problems(wrapped)))


def test_a_batch_s_questions_written_as_plain_lines_are_each_kept():
    """With no bullet at all, each line of a batch's list is one entry, as under a frame's own list."""

    body = "# Batch\n\n## Open questions\nWhat does the gateway log?\nWho owns the retry budget?\n"
    batch = frame_markdown_utils.parse_answer(body, is_addition=False)["batch"] or {}
    harness.check("both plain-line questions are kept",
                  batch.get("open_questions") == ["What does the gateway log?", "Who owns the retry budget?"], str(batch))


def test_the_empty_mark_under_any_bullet_is_an_empty_section():
    """`* (none)` and `1. (none)` are the empty mark as much as `- (none)`."""

    for bullet in ("*", "1.", "+"):
        answer = _replace_in(ANSWER, "## Screenshots\n- the retry form in the centre\n"
                             "  - importance: needed\n"
                             "  - why: a reader setting a budget must see which field holds it\n",
                             f"## Screenshots\n{bullet} (none)\n", 1)
        parsed = frame_markdown_utils.parse_answer(answer, is_addition=False)
        harness.check(f"`{bullet} (none)` is no screenshot and no error",
                      parsed["frames"][0]["screenshots"] == [] and parsed["errors"] == [],
                      f"{parsed['frames'][0]['screenshots']} {parsed['errors']}")


def test_a_feedback_report_with_an_empty_kind_is_of_kind_other():
    text = "# Feedback\n\n## Kind\n**\n\n## Summary\nthe frame's words are cut off\n"
    reports = frame_markdown_utils.read_feedback(text)
    harness.check("an empty kind reads as other", [report["kind"] for report in reports] == ["other"], str(reports))


def test_a_feedback_report_with_each_value_on_its_heading_line_is_read():
    """A worker wrote `## Kind: contradiction` and `## Summary: <text>` on the heading lines, and the report was dropped
    unseen (lecture-1 frames 87-88, 2026-10-13)."""

    text = ("# Batch\n\n## Open questions\n(none)\n\n# Feedback\n## Kind: contradiction\n"
            "## Summary: The notes and the frame disagree on the tooltip.\n## Detail: The notes quote one line; the frame shows two.\n")
    reports = frame_markdown_utils.read_feedback(text)
    harness.check("the report is read with its kind, summary and detail",
                  reports == [{"kind": "contradiction", "summary": "The notes and the frame disagree on the tooltip.",
                               "detail": "The notes quote one line; the frame shows two."}], str(reports))


def test_an_unspaced_arrow_inside_a_group_is_no_box():
    """`A-->B` is an arrow between two bare boxes, as `A --> B` is: no box named `A--` takes the group's arrows."""

    for arrow in ("A-->B", "A --> B"):
        source = f"flowchart LR\n    subgraph G\n        {arrow}\n    end\n    X --> G\n"
        repaired = mermaid_text_utils.repair_group_used_as_box(source)
        harness.check(f"{arrow}: the arrow to the group is left as written", repaired == source, repaired)


def test_an_open_question_that_only_says_the_speech_broke_off_is_dropped_and_a_real_one_stays():
    dropped = ("The transcript cuts off mid-sentence at 'for', leaving the final clause incomplete.",
               "What specific task does the presenter mean? The sentence is incomplete.",
               "How does the presenter finish the thought that breaks off at 'because the'?",
               "What follows the phrase \"And this is a really common\" at the end of frame 49?",
               "What is the complete sentence starting with \"So the next step\" at the end of frame 12?",
               "What is the rest of the sentence after \"we then send\"?",
               "How does the sentence ending with \"so the next\" (frame 9) conclude?")
    kept = ("Which step is cut off from the monthly report?",
            "Who decides the retry budget for each client?",
            "Why is the export left incomplete for archived projects?",
            "What follows the approval step in the onboarding flow?",
            "Which complete sentence of the contract does the clause quote?",
            "How does the onboarding flow conclude for a rejected client?")
    harness.check("a question about broken-off speech is recognised",
                  all(frame_markdown_utils.is_cut_off_question(question) for question in dropped),
                  str([question for question in dropped if not frame_markdown_utils.is_cut_off_question(question)]))
    harness.check("a real question using the same words about the subject is kept",
                  not any(frame_markdown_utils.is_cut_off_question(question) for question in kept),
                  str([question for question in kept if frame_markdown_utils.is_cut_off_question(question)]))


def test_a_markdown_rule_after_the_tags_line_is_no_part_of_the_last_tag():
    lines = ["while loops, user input, persistence  ---"]
    harness.check("the rule is cut off the last tag", frame_markdown_utils._read_tags(lines)
                  == ["while loops", "user input", "persistence"], str(frame_markdown_utils._read_tags(lines)))
    harness.check("a tag that holds a dash keeps it", frame_markdown_utils._read_tags(["write-back cache, retry-budget"])
                  == ["write-back cache", "retry-budget"], str(frame_markdown_utils._read_tags(["write-back cache, retry-budget"])))


def test_a_markdown_rule_at_the_end_of_any_section_is_no_part_of_it():
    """The model writes a rule between frames, and it landed as the outline's last line: a whole addition was dropped
    as "not a part" for it, as tags were joined to it before."""

    text = ('# Frame 0\n\n## What changed\nThe highlight was on another line.\n\n'
            '## Screen\n- code editor: left pane\n  - text "n = 0": top\n\n---\n')
    addition = frame_markdown_utils.parse_answer(text, is_addition=True)
    harness.check("the outline is read without the rule, and nothing is refused",
                  not addition.get("errors") and addition["frames"][0]["screen"].splitlines()[-1].strip() == '- text "n = 0": top',
                  str(addition))


def test_an_edit_line_with_an_id_and_no_action_word_is_a_replace_defect():
    # Defect: a repair listed corrected outline lines under its edits as "- [O44.2] <the part>", and its whole answer
    # was refused as no edit, on 3 attempts. Fix: an id with the new text is a replace; the id is not written twice
    text = ("# Frame 4 · slide\n\n## What changed\nThe outline's axes were misplaced.\n\n## Edits\n"
            "- [O4.2] y-axis: vertical line on the left\n"
            "- replace T4.1: [T4.1] \"a\" → left column: a better line\n"
            "- remove F4.3\n")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    edits = parsed["frames"][0]["edits"]
    harness.check("nothing is refused", not parsed["errors"], str(parsed["errors"]))
    harness.check("the id and its text is a replace", edits[0] == {"action": "replace", "id": "O4.2",
                                                                    "text": "y-axis: vertical line on the left"}, str(edits))
    harness.check("the id is not written into the new text twice", edits[1]["text"] == '"a" → left column: a better line', str(edits))
    harness.check("a removal stays a removal", edits[2]["action"] == "remove", str(edits))
    bare = frame_markdown_utils.parse_answer(text.replace("- [O4.2] y-axis: vertical line on the left", "- [O4.2]"), is_addition=True)
    harness.check("an id alone says nothing: the line is ignored and the answer stands",
                  not bare["errors"] and bare["frames"][0]["edits"][0]["action"] == frame_markdown_utils.IGNORED_EDIT
                  and len(bare["frames"][0]["edits"]) == 3, str(bare))
    held = {"frame": 4, "facts": ["old fact", "second fact"], "take_aways": ["x"]}
    nonsense = {"frame": 4, "edits": [{"action": "replace", "id": "F4.1", "text": "new fact"},
                                      {"action": frame_markdown_utils.IGNORED_EDIT, "id": "", "text": "- [O4.2]"}]}
    record, _, refusals = addition_utils.apply_addition(held, nonsense)
    harness.check("the readable edit is applied and the ignored line is named", record["facts"][0] == "new fact"
                  and len(refusals) == 1 and "[O4.2]" in refusals[0], str((record["facts"], refusals)))


def test_a_repair_replacing_the_speech_visual_alignment_by_name_is_read_not_refused_defect():
    # Defect: a repair wrote "- replace Speech visual alignment: <text>" under its edits, twice in a row, and its whole
    # answer was refused as no edit. Fix: the section named in place of an id is that section's new text
    text = ("# Frame 4 · slide\n\n## What changed\nThe alignment named a study the speaker did not name.\n\n## Edits\n"
            "- replace Speech visual alignment: The speaker cites the percentages and not the studies' names.\n"
            "- replace T4.2: \"a\" → left column: a better line\n")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    addition = parsed["frames"][0]
    harness.check("nothing is refused", not parsed["errors"], str(parsed["errors"]))
    harness.check("the section takes the new text", addition["speech_visual_alignment"] ==
                  "The speaker cites the percentages and not the studies' names.", str(addition["speech_visual_alignment"]))
    harness.check("the line edit beside it stays", [edit["id"] for edit in addition["edits"]] == ["T4.2"], str(addition["edits"]))
    narration = frame_markdown_utils.parse_answer(text.replace("Speech visual alignment", "What happens"), is_addition=True)
    harness.check("every text section is read so, \"What happens\" too",
                  narration["frames"][0]["third_person_description"] == "The speaker cites the percentages and not the studies' names.",
                  str(narration))
    nonsense = frame_markdown_utils.parse_answer(text.replace("Speech visual alignment", "Nothing in particular"), is_addition=True)
    harness.check("another name is an ignored line, not a refused answer", not nonsense["errors"]
                  and nonsense["frames"][0]["edits"][0]["action"] == frame_markdown_utils.IGNORED_EDIT, str(nonsense))


def test_an_editors_line_numbers_are_taken_out_of_the_outline_and_quoted_code_is_kept():
    screen = "\n".join(["- code editor: left pane, white background",
                        "  - code lines 23 to 46 visible",
                        "  - highlighted block: lines 31 to 37, light purple background",
                        "  - line 35 `print('x')`",
                        "  - comment `# EXAMPLE: retry loop`: line 39",
                        "  - cursor: blinking at end of line 36",
                        "  - step 2: purple highlight moves from line 53 to line 54, cursor appears",
                        "  - arrow between line 12 and line 15: points down",
                        '  - slide text "Line 4 opens at noon": centre'])
    stripped = frame_markdown_utils.strip_line_numbers(screen)
    expected = "\n".join(["- code editor: left pane, white background",
                          "  - highlighted block: light purple background",
                          "  - `print('x')`",
                          "  - comment `# EXAMPLE: retry loop`",
                          "  - cursor: blinking at end",
                          "  - step 2: purple highlight moves, cursor appears",
                          "  - arrow: points down",
                          '  - slide text "Line 4 opens at noon": centre'])
    harness.check("line numbers go, quotes and code stay", stripped == expected, stripped)


def test_a_label_s_brackets_left_empty_by_its_line_number_go_defect():
    # Defect: "main function signature (line 13): `fn main()`" was kept as "main function signature (): ..." once its
    # line number went (froscon 3147 frame 157). Fix: brackets left empty go; code and a name's own brackets stay.
    screen = "\n".join(["- code block: centre",
                        "  - main function signature (line 13): `fn main() -> Result<(), Error> {`",
                        "  - call run_all(): below"])
    stripped = frame_markdown_utils.strip_line_numbers(screen)
    harness.check("the empty brackets go, the code's and the name's stay",
                  stripped == "\n".join(["- code block: centre",
                                         "  - main function signature: `fn main() -> Result<(), Error> {`",
                                         "  - call run_all(): below"]), stripped)


def test_a_screen_written_as_markdown_reads_back_whole():
    """Code on screen is written as a block a document can show in place of a picture (the owner, 2026-10-12); a comment
    line inside it starts with "#" and is the code's, never a heading."""

    text = ('# Frame 4 · code editor\n\n## Take aways\n- Take aways: the retry rule.\n\n## Facts\n- An express order '
            'retries five times.\n\n## Screen\n- editor: fills the frame\n\n## As markdown\n```python\n# the budget\n'
            'def max_attempts(order):\n    return 5 if order.is_express else 3\n```\n\n## Tags\nretry policy, reference\n')
    parsed = frame_markdown_utils.parse_answer(text, is_addition=False)
    record = parsed["frames"][0]
    harness.check("the block is kept as written, its comment line inside it",
                  record["as_markdown"].startswith("```python\n# the budget\n") and record["as_markdown"].endswith("```")
                  and record["tags"] == ["retry policy", "reference"], str(record))
    harness.check("and written back the same", "## As markdown\n```python\n# the budget" in
                  frame_markdown_utils.render_frame_document(record))


def test_a_frame_title_written_one_level_too_deep_is_still_the_frame_title():
    """A second look headed its second frame "## Frame 15 · slide", which was read as a section of frame 14, and both
    frames' changes were thrown away (2026-10-12)."""

    text = ("# Frame 14 · slide\n\n## What changed\nA fact was missing.\n\n## Facts\n- The task takes time to plan.\n\n"
            "## Frame 15 · slide\n\n## What changed\nThe outline merged two labels.\n\n## Facts\n- Both labels are bold.\n")
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)
    harness.check("two frames read, no error", [frame["frame"] for frame in parsed["frames"]] == [14, 15]
                  and not parsed["errors"], str(parsed))


def test_a_block_fenced_as_markdown_is_unwrapped_and_code_is_not():
    """A slide of two paragraphs came back inside a fence labelled markdown (2026-10-12): printed as it stands it is a
    code box of prose in the document."""

    prose = "```markdown\nTask: check the retry budget.\n\nAlso, keep it per client.\n```"
    harness.check("a fence labelled markdown is unwrapped",
                  frame_markdown_utils.strip_markdown_fence(prose) == "Task: check the retry budget.\n\nAlso, keep it per client.")
    code = "```python\nretries = 3\n```"
    harness.check("a fence of a language stays", frame_markdown_utils.strip_markdown_fence(code) == code)
    listed = "- Gateway\n- AcmeSync"
    harness.check("a block with no fence stays", frame_markdown_utils.strip_markdown_fence(listed) == listed)


def test_a_slide_title_written_as_a_heading_inside_as_markdown_stays_in_the_block():
    """A frame's As markdown opened with the slide's title as `# What to do now?`, and the answer was refused as a
    document of its own (2026-10-12)."""

    record = frame_markdown_utils.parse_answer(ANSWER.replace(
        "## Speech visual alignment", "## As markdown\n# Retry budgets\n- Gateway\n- AcmeSync\n\n## Speech visual alignment", 1),
        is_addition=False)
    harness.check("the answer is read", not record["errors"] and len(record["frames"]) == 2, str(record["errors"]))
    harness.check("the heading stays in the block",
                  record["frames"][0]["as_markdown"].startswith("# Retry budgets\n- Gateway"), record["frames"][0]["as_markdown"])
    stray = frame_markdown_utils.parse_answer(ANSWER.replace("## Speech visual alignment", "# Retry budgets\nsome words\n\n## Speech visual alignment", 1),
                                              is_addition=False)
    harness.check("outside As markdown such a line is still refused by name",
                  any('"# Retry budgets" is not a document' in error["text"] for error in stray["errors"]), str(stray["errors"]))


def test_a_page_s_section_headings_inside_as_markdown_stay_in_the_block_defect():
    # Defect: a README page's "## Data" and "## Script" inside a frame's As markdown were read as sections of the notes
    # and the frame was refused (DEFECTS 481). Fix: a "## " heading naming no section, inside the block, stays in it;
    # a known heading after it still opens its section, and outside the block such a heading is still refused
    record = frame_markdown_utils.parse_answer(ANSWER.replace(
        "## Speech visual alignment",
        "## As markdown\n# Gateway\n## Data\n- refunds.json\n## Script\n- main.py\n\n## Speech visual alignment", 1),
        is_addition=False)
    harness.check("the answer is read", not record["errors"] and len(record["frames"]) == 2, str(record["errors"]))
    harness.check("both page headings stay in the block, the next section still read",
                  "## Data\n- refunds.json\n## Script\n- main.py" in record["frames"][0]["as_markdown"]
                  and record["frames"][0]["speech_visual_alignment"], record["frames"][0]["as_markdown"])
    stray = frame_markdown_utils.parse_answer(ANSWER.replace("## Speech visual alignment", "## Data\n- refunds.json\n\n## Speech visual alignment", 1),
                                              is_addition=False)
    harness.check("outside As markdown such a heading is still refused by name",
                  any('"## Data" is not a section' in error["text"] for error in stray["errors"]), str(stray["errors"]))


def test_an_unwrapped_block_keeps_no_heading_of_its_own():
    """A slide written as a block with "# <title>" and "### <section>" would open sections in the document once
    unwrapped (2026-10-13)."""

    block = "```markdown\n# Retry budgets\n### Per client\n- Gateway: 3\n```"
    harness.check("headings become bold lines, the list stays",
                  frame_markdown_utils.strip_markdown_fence(block) == "**Retry budgets**\n**Per client**\n- Gateway: 3",
                  frame_markdown_utils.strip_markdown_fence(block))



def test_a_take_away_wrapped_whole_in_backticks_is_unwrapped():
    """Whole take-aways came back as one code span each, which the notes would print as code (2026-10-13)."""

    items = frame_markdown_utils._read_list(['- `"the Gateway retries" → the title: a call gets three tries`',
                                             "- `retry = 3` sets the budget"])
    harness.check("the take-away is unwrapped", items[0] == '"the Gateway retries" → the title: a call gets three tries',
                  str(items))
    harness.check("a real code span inside a line stays", items[1] == "`retry = 3` sets the budget", str(items))


def test_a_bare_empty_mark_under_an_outline_s_parts_is_dropped():
    """A build step whose slide did not change wrote its step line, then "(none)" on the next line, and the batch was
    refused for a line that is not a part (lecture-1 frame 104, 2026-10-13). An outline of nothing but the mark keeps
    it, for the check that an outline is missing."""

    step = "- step 2 of the screen of frame 7: no visual change\n(none)"
    harness.check("the mark under a part is dropped",
                  frame_markdown_utils.normalise_screen_indentation(step) == "- step 2 of the screen of frame 7: no visual change",
                  frame_markdown_utils.normalise_screen_indentation(step))
    harness.check("an outline of the mark alone is kept as written", frame_markdown_utils.normalise_screen_indentation("(none)") == "(none)")


def test_a_bullet_sign_copied_into_a_quote_or_a_list_item_is_taken_out():
    """A slide's third bullet was quoted as "* <its words>" and listed as "- * <its words>", a list inside the list
    (38c3-fault-injection, 2026-10-15)."""

    answer = frame_markdown_utils.parse_answer(
        "# Frame 4 · slide\n\n## Screen\n- slide: dark grey\n  - bullet \"Proceeded to dump\"\n"
        "  - bullet \"* Chip died after the faults :(\"\n    - sub-bullet \"– Easy here\"\n  - formula \"5 * 3\"\n"
        "  - terminal row \"- offset -\"\n\n"
        "## As markdown\n- Proceeded to dump\n- * Chip died after the faults :(\n- **Bold** item", is_addition=False)
    frame = answer["frames"][0]
    harness.check("the quote keeps its words without the sign",
                  '"Chip died after the faults :("' in frame["screen"] and '"5 * 3"' in frame["screen"]
                  and 'sub-bullet "Easy here"' in frame["screen"] and '"- offset -"' in frame["screen"], frame["screen"])
    harness.check("the list item too, and bold stays bold",
                  "- Chip died after the faults :(" in frame["as_markdown"] and "- **Bold** item" in frame["as_markdown"],
                  frame["as_markdown"])


def test_a_slide_s_hyphen_bullet_kept_in_a_list_item_is_taken_out_defect():
    # A slide's hyphen bullets were written "- - extracted from Laser Scans", an empty list item holding a nested one in
    # the document (DEFECTS 631). Any bullet sign after an item's own marker is now taken out, a dash too
    answer = frame_markdown_utils.parse_answer(
        "# Frame 4 · slide\n\n## Screen\n- slide: dark\n  - bullet \"extracted from scans\"\n\n"
        "## As markdown\n**Vegetation data:**\n- - extracted from scans\n- – up to 1GB / km²\n- offset - 3\n", is_addition=False)
    markdown = answer["frames"][0]["as_markdown"]
    harness.check("the slide's dashes leave the items", "- extracted from scans" in markdown and "- up to 1GB / km²" in markdown
                  and "- -" not in markdown and "- –" not in markdown, markdown)
    harness.check("a dash inside an item's text stays", "- offset - 3" in markdown, markdown)


def test_an_outline_line_among_a_markdown_copy_s_other_lines_is_written_as_its_text_defect():
    # A slide's title and bullets beside its table, and a link line with words after its quote, were printed in a
    # chapter with their kinds and quote marks (DEFECTS 634). Each line naming an outline part is now written as its text
    markdown = frame_markdown_utils.rewrite_outline_shaped_markdown(
        '- title "Data source"\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- bullet "hourly series"\n'
        '- link "https://example.org/" provides terrain data.\n- the step "build" runs first')
    harness.check("the title is bold, the bullet and the link are items, a plain item keeps its quote",
                  markdown.startswith("**Data source**") and "- hourly series" in markdown
                  and "- https://example.org/ provides terrain data." in markdown and '- the step "build" runs first' in markdown
                  and "| 1 | 2 |" in markdown, markdown)


def test_every_section_of_a_frame_record_has_a_shape_a_reader_and_a_writer_know():
    unknown = {field: shape for field, (_, shape) in frame_record.FRAME_SECTIONS.items()
               if shape not in frame_record.SECTION_SHAPES}
    harness.check("no row of FRAME_SECTIONS names a shape outside SECTION_SHAPES", not unknown, str(unknown))


def test_a_fact_quoting_code_keeps_the_code_s_spaces():
    """A fact about spacing quoted `def pay(a: int,   b: int)` and the program squeezed its spaces to one, so the fact
    showed the same code before and after (Lecture 9 frame 3). Only the space outside code spans is squeezed."""

    answer = """# Frame 3 · code editor

## Facts
- The formatter reduces extra spaces between parameters, `def pay(a: int,   b: int)`   becomes
  `def pay(a: int, b: int)`.
"""
    fact = frame_markdown_utils.parse_answer(answer, is_addition=False)["frames"][0]["facts"][0]
    harness.check("the code span keeps its three spaces, the text around it is squeezed",
                  fact == "The formatter reduces extra spaces between parameters, `def pay(a: int,   b: int)` becomes "
                          "`def pay(a: int, b: int)`.", fact)


def test_a_question_both_answered_and_left_open_stays_only_answered():
    """An answers call wrote its one question in both sections, and both reached the batch's notes (Lecture 9, frames
    96-103)."""

    question = "Why does the editor show another return value?"
    answered = [f"{question} → The presenter changed it twice (frames 96 and 101)."]
    harness.check("the open copy of an answered question is dropped",
                  frame_markdown_utils.drop_answered_questions([question, "Who owns the gateway?"], answered)
                  == ["Who owns the gateway?"])
    harness.check("letter case and spacing aside",
                  frame_markdown_utils.drop_answered_questions(["why does the editor  show another return value?"],
                                                               answered) == [])


def test_a_part_named_title_wins_over_an_earlier_heading():
    """A wiki page's side panel, outlined first with its own heading, titled every page of a PDF (pdf-01)."""

    screen = ('- side panel: left\n  - heading "Page tree"\n  - link "Partner onboarding"\n'
              '- title "Architecture": centre, large\n- paragraph "A call enters through the edge proxy."')
    harness.check("the title is the part named a title", frame_markdown_utils.find_screen_title(screen) == "Architecture",
                  frame_markdown_utils.find_screen_title(screen))
    harness.check("with no title, the first heading", frame_markdown_utils.find_screen_title(
        '- heading "Refund limits": top\n- heading "Approvals": below') == "Refund limits")


def test_a_needed_screenshot_of_one_quoted_label_the_notes_hold_is_stored_as_helps_defect():
    # Defect: a frame's one label, "SPECIAL OFFER FOR THE TEAM", was marked needed though its notes quote it, and the
    # writer cut a picture of a line of text through the drawing beside it. Fix: such a screenshot is stored as helps.
    from validators import item_validator

    record = {"facts": ["The highlight is a special offer for the team."],
              "screen": '- label "SPECIAL OFFER FOR THE TEAM": lower right\n- drawing of a glass: left',
              "screenshots": [{"part": 'the label "SPECIAL OFFER FOR THE TEAM"', "importance": "needed", "why": "names it"},
                              {"part": "the drawing of a glass", "importance": "needed", "why": "shows it"},
                              {"part": 'the label "NOT IN THE NOTES"', "importance": "needed", "why": "names it"}]}
    stored = [suggestion["importance"] for suggestion in item_validator.set_text_screenshots_to_helps(record)["screenshots"]]
    harness.check("the quoted label the notes hold becomes helps; a drawing and an unheld text stay needed",
                  stored == ["helps", "needed", "needed"], str(stored))


def test_phase_3_is_told_a_number_said_with_a_pronoun_belongs_to_the_thing_talked_about_defect():
    # A frame's notes gave the board's own current, said as "<number> mA braucht er" over a photo of the board, to the
    # computer named in the same breath; phase 3's instructions now say whose number a pronoun's sentence gives
    instructions = (INSTRUCTIONS / "_shared" / "frames-processing.md").read_text(encoding="utf-8")
    harness.check("the pronoun rule is in phase 3's instructions",
                  '**A number said with "it", "he", "er" or "es" belongs to the thing the speaker was talking about**'
                  in instructions)


def test_an_arrow_label_holding_a_bracket_is_quoted_defect():
    # Defect: `-->|Return [0.5, 0.7]|` ended the label at the bracket, Mermaid could not draw it, and the batch blocked.
    # Fix: the program wraps such a label in double quotes as it stores the notes; a quoted one is left as it is.
    from utils import mermaid_text_utils

    source = 'flowchart LR\n  A -->|Return [0.5, 0.7]| B\n  B -.->|"keeps (x)"| C\n  C -->|plain| D'
    quoted = mermaid_text_utils.wrap_bracketed_labels_in_quotes(source)
    harness.check("the bracketed label is quoted, the others stay",
                  '-->|"Return [0.5, 0.7]"|' in quoted and '|"keeps (x)"|' in quoted and "|plain|" in quoted, quoted)
    harness.check("and Mermaid can draw it", mermaid_text_utils.find_syntax_problems(quoted) == [], quoted)


def test_a_code_fence_inside_the_outline_becomes_one_quoted_part_per_line_defect():
    # A worker fenced a terminal's log and a code slide under `## Screen` and the batch was blocked for lines that were
    # not parts (DEFECTS 282); now each fenced line is a `- line "..."` part under the part before the fence
    screen = ('- terminal: black background\n  - output of the last command:\n    ```text\n    Gateway ready\n'
              '      port "8080" open\n    ```\n  *(Note: the cursor blinks after the prompt.)*\n- footer "Acme"')
    record = frame_markdown_utils.parse_answer(f"# Frame 3 · terminal\n\n## Screen\n{screen}\n", is_addition=False)["frames"][0]
    harness.check("every fenced line is a part one level under the part before, its own indent kept",
                  record["screen"] == ('- terminal: black background\n  - output of the last command:\n'
                                       '    - line "Gateway ready"\n    - line "  port \'8080\' open"\n- footer "Acme"'),
                  record["screen"])
    harness.check("a fence opening the outline is at the first level",
                  frame_markdown_utils.open_screen_fences("```\nx = 1\n```") == '- line "x = 1"')
    parts = frame_markdown_utils.read_screen_parts(record["screen"])
    harness.check("the outline check finds no line that is not a part", not parts.problems, str(parts.problems))


def test_a_markdown_copy_written_as_outline_lines_becomes_the_screen_text_defect():
    # A frame's markdown copy held its outline's lines, `- title "..."`, `- text "..."`, and phase 4 copied them into
    # the document as they stood (DEFECTS 295); now such a copy is written as the text it quotes
    shaped = '- title "Ledger limits"\n- text "Orders over the cap wait for approval"\n- bullet "Admins raise the cap"'
    harness.check("the title in bold, each other part a list item",
                  frame_markdown_utils.rewrite_outline_shaped_markdown(shaped)
                  == "**Ledger limits**\n- Orders over the cap wait for approval\n- Admins raise the cap",
                  frame_markdown_utils.rewrite_outline_shaped_markdown(shaped))
    plain = '- Orders over the cap wait\n- the "cap" field holds it'
    harness.check("a list of the screen's own lines is left as it is",
                  frame_markdown_utils.rewrite_outline_shaped_markdown(plain) == plain)


def test_the_open_questions_worksheet_of_decided_candidates_is_read_and_set_aside_defect():
    # The open-questions step re-decided settled candidates for minutes, with nowhere to write each decision once
    # (DEFECTS 641). Its answer may open with a "## Considered" list, which the reader sets aside
    text = ("# Batch\n\n## Considered\n- the tool's name: no, the slide spells it\n- who owns the budget: question\n\n"
            "## Open questions\n- Who owns the retry budget?\n")
    answer = frame_markdown_utils.parse_answer(text, is_addition=False)
    harness.check("the worksheet is no error", not answer["errors"], str(answer["errors"]))
    harness.check("the open question is read", (answer["batch"] or {}).get("open_questions")
                  == ["Who owns the retry budget?"], str(answer["batch"]))
    from prompts import system_prompt_builder

    prompt = " ".join(system_prompt_builder.build_system_prompt("open_questions").split())
    harness.check("the step is asked to settle each candidate once under ## Considered",
                  "**`## Considered`** is where you settle each candidate, once" in prompt, "")
    example = prompt.split("~~~markdown", 1)[1].split("~~~", 1)[0]
    harness.check("and its example answer reads cleanly", not frame_markdown_utils.parse_answer(
        example.replace(" # Batch ", "# Batch\n").replace(" ## ", "\n\n## ").replace(" - ", "\n- "),
        is_addition=False)["errors"], example[:200])


def test_a_fact_about_the_slide_itself_is_named_with_its_id_defect():
    # Facts with the slide or the presentation as their subject reached chapters as sentences about the slides. Now each
    # is named with the id the repair edits it by; a fact about the subject is not
    record = {"frame": 12, "facts": ["The Gateway issues tokens valid for ten minutes.",
                                     "The opening slide is titled \"Gateway\".",
                                     "A presentation slide displays a logo on a dark background."]}
    harness.check("the two slide facts are named by their ids",
                  [line_id for line_id, _ in frame_markdown_utils.list_slide_subject_facts(record)] == ["F12.2", "F12.3"])
