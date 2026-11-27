"""A section written as markdown, read back into blocks.

The writing agent lays its section out itself: paragraphs, lists, tables, diagrams and frames in the order the text
needs, `####` subheadings, and each decision, action item and open point marked where it was said. Every block ends
with the ids of the notes it rests on, in brackets. The program reads the blocks, their kinds and their ids back out,
and strips the ids before a reader sees the text.
"""

from support import harness

from utils import document_text_utils
from utils import section_markdown_utils

SECTION = """The app never talks to the provider directly. It sends the token to the proxy. [F12.1, S0415]

#### The login flow

```mermaid
flowchart LR
  App --> Proxy --> Provider
```
[F12.2]

The proxy checks the token first. A token older than one hour is refused. [F12.3]

| Step | Done by |
|---|---|
| Send token | App |
[F12.1, F12.3]

- The app keeps no token.
- The proxy keeps it for one hour.
[F12.3]

![The proxy's settings page](cut-03 "Tokens older than one hour are refused here.")
> **What the figure shows:** the settings page "Retry budget" on white. Left, the menu with "Overview" and "Retries", "Retries" open. Centre, the form with the field "Max attempts" = "3" and the blue button "Save".
[F12.3]

> **Decision:** The team keeps the proxy. [S0417]

> **Action:** Dana sets up the proxy by Friday. [S0415]

> **Open:** Who renews the token is not said. [F13.1]
"""


def test_every_block_is_read_in_the_order_it_was_written():
    blocks = section_markdown_utils.parse_section(SECTION)
    harness.check("the kinds come back in the section's own order", [block["kind"] for block in blocks] == [
        "paragraph", "subheading", "diagram", "paragraph", "table", "list", "image", "decision", "action", "open"],
                  str([block["kind"] for block in blocks]))


def test_each_block_carries_the_ids_written_after_it():
    blocks = section_markdown_utils.parse_section(SECTION)
    by_kind = {block["kind"]: block for block in blocks}
    harness.check("ids at the end of a paragraph's last sentence are the paragraph's",
                  blocks[0]["entry_ids"] == ["F12.1", "S0415"], str(blocks[0]))
    harness.check("ids on the line under a diagram are the diagram's", by_kind["diagram"]["entry_ids"] == ["F12.2"],
                  str(by_kind["diagram"]))
    harness.check("ids under a table and a list are theirs",
                  by_kind["table"]["entry_ids"] == ["F12.1", "F12.3"] and by_kind["list"]["entry_ids"] == ["F12.3"])
    harness.check("a marked decision, action and open point each carry theirs",
                  [by_kind[kind]["entry_ids"] for kind in ("decision", "action", "open")]
                  == [["S0417"], ["S0415"], ["F13.1"]])
    harness.check("a subheading needs none", by_kind["subheading"]["entry_ids"] == [])


def test_a_block_keeps_its_text_without_the_ids():
    blocks = section_markdown_utils.parse_section(SECTION)
    by_kind = {block["kind"]: block for block in blocks}
    harness.check("a paragraph's text ends at its last sentence",
                  blocks[0]["text"] == "The app never talks to the provider directly. It sends the token to the proxy.",
                  repr(blocks[0]["text"]))
    harness.check("a diagram's text is its Mermaid source, without the fence",
                  by_kind["diagram"]["text"] == "flowchart LR\n  App --> Proxy --> Provider", repr(by_kind["diagram"]["text"]))
    harness.check("a marked claim's text is the sentence after its mark",
                  by_kind["decision"]["text"] == "The team keeps the proxy.", repr(by_kind["decision"]["text"]))
    image = by_kind["image"]
    harness.check("an image gives its cut, its alt text and its caption",
                  (image["cut"], image["alt"], image["caption"])
                  == ("cut-03", "The proxy's settings page", "Tokens older than one hour are refused here."), str(image))
    harness.check("a subheading gives its level and its words",
                  (by_kind["subheading"]["level"], by_kind["subheading"]["text"]) == (4, "The login flow"))


def test_a_caption_holding_quote_marks_is_still_a_frame_line():
    markdown = ('The proxy keeps the token. [F12.1]\n\n'
                '![The settings page](cut-03 "The field \\"timeout\\" set to \\"60\\"")\n'
                '[F12.2]\n'
                '> **Open:** Who sets the timeout is not said. [F12.3]\n')
    images = [block for block in section_markdown_utils.parse_section(markdown) if block["kind"] == "image"]
    harness.check("a caption with quote marks escaped inside it is read as the frame's caption",
                  [(image["cut"], image["caption"], image["entry_ids"]) for image in images]
                  == [("cut-03", 'The field "timeout" set to "60"', ["F12.2"])], str(images))


def test_a_block_with_no_ids_has_none_and_a_high_heading_is_its_own_kind():
    blocks = section_markdown_utils.parse_section("A sentence with no source.\n\n### A heading too high\n")
    harness.check("a paragraph with no bracket has no ids, for the check to refuse",
                  blocks[0]["kind"] == "paragraph" and blocks[0]["entry_ids"] == [], str(blocks[0]))
    harness.check("a heading of level 3 or higher is kind `heading`, for the check to refuse",
                  blocks[1]["kind"] == "heading" and blocks[1]["level"] == 3, str(blocks[1]))


def test_a_fence_never_closed_is_one_unclosed_diagram_and_swallows_nothing_silently():
    blocks = section_markdown_utils.parse_section("First. [F1.1]\n\n```mermaid\nflowchart LR\n  A --> B\n\nLast. [F1.2]\n")
    harness.check("the unclosed fence is read as a diagram marked unclosed, for the check to refuse",
                  blocks[-1]["kind"] == "diagram" and blocks[-1]["is_unclosed"], str(blocks))


def test_a_bracket_that_is_not_note_ids_is_left_in_the_text():
    section = "The login is described under [T08.3], and in [the guide](https://example.org/guide). [F1.1]\n"
    block = section_markdown_utils.parse_section(section)[0]
    harness.check("a topic link and a web link stay in the text; only the note ids are taken",
                  block["text"] == "The login is described under [T08.3], and in [the guide](https://example.org/guide)."
                  and block["entry_ids"] == ["F1.1"], str(block))
    harness.check("the reader keeps both links", "[T08.3]" in section_markdown_utils.strip_entry_ids(section)
                  and "(https://example.org/guide)" in section_markdown_utils.strip_entry_ids(section))


def test_the_reader_sees_the_section_without_the_ids():
    text = section_markdown_utils.strip_entry_ids(SECTION)
    harness.check("no id is left for a reader", "F12" not in text and "S04" not in text, text)
    harness.check("the diagram stays between its two paragraphs",
                  text.index("directly.") < text.index("```mermaid") < text.index("checks the token first"), text)
    harness.check("no id line leaves a blank gap of its own", "\n\n\n" not in text, repr(text))


def test_the_record_lists_each_kind_the_checks_and_the_closing_read():
    record = section_markdown_utils.build_section_record(SECTION)
    harness.check("the record keeps the markdown and its blocks", record["markdown"] == SECTION and len(record["blocks"]) == 10)
    harness.check("prose is every paragraph, list and table, in order",
                  [paragraph["entry_ids"] for paragraph in record["paragraphs"]]
                  == [["F12.1", "S0415"], ["F12.3"], ["F12.1", "F12.3"], ["F12.3"]], str(record["paragraphs"]))
    harness.check("a diagram gives its Mermaid source", record["diagrams"] == [
        {"mermaid": "flowchart LR\n  App --> Proxy --> Provider", "entry_ids": ["F12.2"]}], str(record["diagrams"]))
    harness.check("decisions, action items and open points are listed for the closing",
                  record["decisions"] == [{"text": "The team keeps the proxy.", "entry_ids": ["S0417"]}]
                  and record["action_items"] == [{"text": "Dana sets up the proxy by Friday.", "entry_ids": ["S0415"]}]
                  and record["not_established"] == ["Who renews the token is not said."], str(record))
    harness.check("an image gives its cut, alt text, caption and ids", record["images"] == [
        {"cut": "cut-03", "alt_text": "The proxy's settings page", "caption": "Tokens older than one hour are refused here.",
         "description": 'the settings page "Retry budget" on white. Left, the menu with "Overview" and "Retries", "Retries" open. Centre, the form with the field "Max attempts" = "3" and the blue button "Save".',
         "entry_ids": ["F12.3"]}], str(record["images"]))


def test_a_figures_description_belongs_to_the_figure_and_is_no_block_of_its_own():
    """The owner, 2026-10-06: a reader who cannot see the figure, and an agent reading the document as text, read the
    description in its place, so it travels with the figure and no check reads it as a claim."""

    blocks = section_markdown_utils.parse_section(SECTION)
    image = next(block for block in blocks if block["kind"] == "image")
    harness.check("the description sits on the image block",
                  image["description"].startswith("the settings page") and '"Max attempts" = "3"' in image["description"],
                  image["description"])
    harness.check("it is no block of its own, so no check reads it as a sentence of the writer's",
                  not any(block["kind"] == "figure_description" for block in blocks)
                  and not any("What the figure shows" in block.get("text", "") for block in blocks
                              if block["kind"] != "image"),
                  str([block["kind"] for block in blocks]))
    plain = section_markdown_utils.parse_section('![a](cut-01 "b")\n')
    harness.check("a figure written without one has an empty description", plain[0]["description"] == "")


def test_every_diagram_is_rewritten_in_place_and_nothing_else():
    rewritten = section_markdown_utils.rewrite_diagrams(SECTION, lambda source: source.replace("Proxy", "Gateway"))
    harness.check("the diagram's source is rewritten where it stands",
                  "App --> Gateway --> Provider" in rewritten and rewritten.index("directly.") < rewritten.index("Gateway"))
    harness.check("the prose around it is untouched", "It sends the token to the proxy." in rewritten)


def test_a_range_of_notes_in_brackets_cites_each_note():
    moved = document_text_utils.move_bracketed_entry_ids({"text": "Two facts. [F0.1-F0.3, F1.4-F1.5, S0004]"})
    harness.check("a range is written out note by note", moved["entry_ids"] == ["F0.1", "F0.2", "F0.3", "F1.4", "F1.5", "S0004"],
                  str(moved))
    harness.check("the bracket is taken out of the text", moved["text"] == "Two facts.", moved["text"])


def test_inline_code_around_a_citation_is_removed_with_the_citation():
    from utils import document_text_utils
    moved = document_text_utils.move_bracketed_entry_ids({"text": "Two facts. `[F0.1]`"})
    harness.check("the cited id is retained", moved["entry_ids"] == ["F0.1"], str(moved))
    harness.check("the empty inline code is removed", moved["text"] == "Two facts.", moved["text"])


def test_a_cut_id_in_a_bracket_of_note_ids_is_read_and_cites_nothing():
    from utils import document_text_utils
    moved = document_text_utils.move_bracketed_entry_ids({"text": "The portal hosts the catalog. [F6.4, cut-003]"})
    harness.check("the note is cited and the cut is not an id", moved["entry_ids"] == ["F6.4"], str(moved))
    harness.check("the whole bracket is taken out of the text", moved["text"] == "The portal hosts the catalog.", moved["text"])


def test_a_bracket_the_program_cannot_read_is_named():
    from utils import document_text_utils
    unread = document_text_utils.find_unread_brackets("The portal hosts the catalog. [F3, S0061]\nSee [T08.3] and [F1.1].")
    harness.check("the bracket with a whole frame is named, a topic link and a read bracket are not", unread == ["[F3, S0061]"],
                  str(unread))


def test_a_frame_in_the_working_draft_points_at_its_file():
    text = '![The proxy](cut-03 "Tokens older than one hour are refused.")\n\n![Gone](cut-09 "Not in the store.")\n'
    linked = section_markdown_utils.replace_cut_ids(text, {"cut-03": "images/cuts/cut-03.png"})
    harness.check("the cut id becomes its file, the caption stays",
                  '![The proxy](images/cuts/cut-03.png "Tokens older than one hour are refused.")' in linked, linked)
    harness.check("a cut the store does not know is left as written", "(cut-09 " in linked, linked)


def test_a_set_aside_line_covers_its_notes_and_no_reader_sees_it():
    section = "The portal lists every API. [F3.1]\n\n> **Set aside:** they only say what was clicked. [F3.2, F6.3]\n"
    blocks = section_markdown_utils.parse_section(section)
    harness.check("the line is a set-aside block holding its ids",
                  blocks[-1]["kind"] == "set_aside" and blocks[-1]["entry_ids"] == ["F3.2", "F6.3"], str(blocks))
    harness.check("the reader's text has no trace of it", "Set aside" not in section_markdown_utils.strip_entry_ids(section)
                  and "clicked" not in section_markdown_utils.strip_entry_ids(section),
                  section_markdown_utils.strip_entry_ids(section))


def test_a_note_named_by_its_step_or_its_heading_is_read_as_the_note():
    from utils import document_text_utils
    moved = document_text_utils.move_bracketed_entry_ids({"text": "Step three. [F2.7-3, F2.7-4, T01.1-F1.4, F1.5-F1.6]"})
    harness.check("a step of a note cites the note, a heading prefix is dropped, a range stays a range",
                  moved["entry_ids"] == ["F2.7", "F1.4", "F1.5", "F1.6"], str(moved))
    harness.check("the bracket is taken out", moved["text"] == "Step three.", moved["text"])


def test_a_marked_line_right_under_a_paragraph_is_its_own_block():
    section = ("The portal lists every API. [F3.1]\n> **Set aside:** a cut-off word. [F16.4]\n"
               "> **Decision:** The team keeps the portal. [S0417]\n")
    blocks = section_markdown_utils.parse_section(section)
    harness.check("the paragraph, the set-aside line and the decision are three blocks, each with its own ids",
                  [(block["kind"], block["entry_ids"]) for block in blocks]
                  == [("paragraph", ["F3.1"]), ("set_aside", ["F16.4"]), ("decision", ["S0417"])], str(blocks))


def test_a_range_of_transcript_lines_cites_each_line():
    from utils import document_text_utils
    moved = document_text_utils.move_bracketed_entry_ids({"text": "The flow starts here. [S0115-S0118, F20.4]"})
    harness.check("a range of transcript lines is written out line by line",
                  moved["entry_ids"] == ["S0115", "S0116", "S0117", "S0118", "F20.4"], str(moved))
    harness.check("the bracket is taken out", moved["text"] == "The flow starts here.", moved["text"])


def test_a_frame_line_right_under_a_paragraph_is_its_own_block():
    section = ("The portal lists every API. [F3.1] The guide follows:\n![The flow](cut-024 \"The whole flow\")\n[F19.5, F20.4]\n")
    blocks = section_markdown_utils.parse_section(section)
    harness.check("the paragraph and the frame are two blocks, the frame with the ids under it",
                  [(block["kind"], block["entry_ids"]) for block in blocks]
                  == [("paragraph", ["F3.1"]), ("image", ["F19.5", "F20.4"])], str(blocks))


def test_a_range_across_frames_is_never_half_read():
    from utils import document_text_utils
    moved = document_text_utils.move_bracketed_entry_ids({"text": "The steps. [F40.1-F42.3]"})
    harness.check("the bracket is not read: no endpoint is cited as if it were the range", moved["entry_ids"] == [], str(moved))
    harness.check("and it is named as unread", document_text_utils.find_unread_brackets(moved["text"]) == ["[F40.1-F42.3]"],
                  str(document_text_utils.find_unread_brackets(moved["text"])))


def test_a_range_across_frames_is_written_out_from_the_request_s_note_order():
    from utils import document_text_utils
    ordered = ["F40.1", "F40.2", "S0190", "F41.1", "F41.2", "F42.1", "F42.2", "F42.3", "F43.1"]
    text = document_text_utils.expand_cross_frame_ranges("The steps. [F40.1-F42.3, S0190]", ordered)
    harness.check("every frame note between the two ends, in the request's order, and nothing else",
                  text == "The steps. [F40.1, F40.2, F41.1, F41.2, F42.1, F42.2, F42.3, S0190]", text)
    kept = document_text_utils.expand_cross_frame_ranges("The steps. [F40.1-F49.9]", ordered)
    harness.check("a range whose end is not in the request is left as written, to be named", kept == "The steps. [F40.1-F49.9]", kept)


def test_a_lead_in_ending_in_a_colon_takes_the_ids_of_the_block_it_introduces():
    """"The response holds an id and a date:" followed by a JSON block and its ids was read as a lead-in with no ids,
    and the chapter was rejected (2026-09-30)."""

    markdown = ('The response holds an id and a date:\n```json\n{"id": "1a2b3c4d"}\n```\n[F3.1, F3.2]\n\n'
                'A plain paragraph. [F3.3]\n')
    blocks = section_markdown_utils.parse_section(markdown)
    lead = blocks[0]
    record = section_markdown_utils.build_section_record(markdown)
    harness.check("the lead-in carries the code block's ids", all(paragraph["entry_ids"] for paragraph in record["paragraphs"]),
                  str([(block["kind"], block["entry_ids"]) for block in record["blocks"]]))
    harness.check("only a lead-in ending in a colon", lead["text"].rstrip().endswith(":"), lead["text"])


def test_formulas_cited_once_after_the_last_are_each_cited_defect():
    # Defect: four `$$` formulas and their lead-ins, written with no blank lines and cited once after the last, were
    # each refused as a block naming no id (DEFECTS 496). Fix: a formula is a block of its own, paired like code
    markdown = ("The upper-surface coordinates of a point are:\n$$\nx_U = x - y_t \\sin \\theta\n$$\n"
                "$$\ny_U = y_c + y_t \\cos \\theta\n$$\nThe lower-surface ones are:\n"
                "$$\nx_L = x + y_t \\sin \\theta\n$$\n$$ y_L = y_c - y_t \\cos \\theta $$\n[S0173-S0178]")
    blocks = section_markdown_utils.build_section_record(markdown)["blocks"]
    harness.check("each formula is a block of its own", sum(section_markdown_utils.is_formula(block) for block in blocks) == 4,
                  str([block["text"][:20] for block in blocks]))
    harness.check("every block, lead-ins and formulas, carries the ids", all(block["entry_ids"] for block in blocks),
                  str([(block["text"][:20], block["entry_ids"]) for block in blocks]))


def test_a_table_takes_the_ids_of_its_footnote_defect():
    # Defect: a table cited once, on the footnote written under it, was refused as naming no id (DEFECTS 498). Fix: a
    # table right before a footnote (a superscript digit or an asterisk first) takes its ids
    markdown = ("| Airfoil | Lift |\n|---|---|\n| 747A315 | 0.763 |\n\n"
                "¹ The numbers give the design lift coefficient [S0226, S0227]\n\n"
                "| Airfoil | Lift |\n|---|---|\n| 747A415 | 0.100 |\n\nThe second family lifts more [S0228]")
    blocks = section_markdown_utils.build_section_record(markdown)["blocks"]
    tables = [block for block in blocks if block["kind"] == "table"]
    harness.check("the table before a footnote takes its ids", tables[0]["entry_ids"] == ["S0226", "S0227"],
                  str(tables[0]))
    harness.check("a table before a plain paragraph takes none", tables[1]["entry_ids"] == [], str(tables[1]))


def test_a_code_block_right_before_another_takes_its_ids():
    """Code, then the output it gives, cited once after the second fence: the first block was refused as citing
    nothing on three checks of one chapter (Lecture 9 chapter 2)."""

    markdown = ('The check flags the early return. [F17.1]\n\n```python\nif done:\n    return "ok"\nelif ready:\n'
                '    return "go"\n```\n```text\nRET505 Unnecessary elif after return\n```\n[F17.4]\n')
    record = section_markdown_utils.build_section_record(markdown)
    codes = [block for block in record["blocks"] if block["kind"] == "code"]
    harness.check("both blocks carry the ids written after the second", [block["entry_ids"] for block in codes]
                  == [["F17.4"], ["F17.4"]], str([(block["kind"], block["entry_ids"]) for block in record["blocks"]]))


def test_a_paragraph_right_before_a_code_block_or_list_takes_its_ids():
    """A paragraph saying what the block under it shows, its ids written once after the block: three paragraphs of one
    chapter were refused as citing nothing (lecture-9 chapter 9, 2026-10-21). A cited paragraph keeps its own."""

    markdown = ("The pattern matches one or more digits.\n\n```text\n\\d+\n```\n[F147.3]\n\n"
                "The tester shows the flavors it reads.\n\n- flavor: pcre2, python\n- match: 2\n[F148.2]\n\n"
                "Retries are capped. [F1.1]\n\n- one\n- two\n[F1.2]\n")
    record = section_markdown_utils.build_section_record(markdown)
    paragraphs = [block["entry_ids"] for block in record["blocks"] if block["kind"] == "paragraph"]
    harness.check("each uncited paragraph takes the ids of the block under it, a cited one keeps its own",
                  paragraphs == [["F147.3"], ["F148.2"], ["F1.1"]], str(paragraphs))


def test_a_list_right_before_a_table_takes_its_ids():
    """A slide's bullet and the table under it, written as the note holds them with the ids once after the table: the
    bullet was refused as citing nothing, check after check (pdf-03)."""

    markdown = "- Measured on 5,000 refunds\n\n| Quarter | Requests |\n|---|---|\n| Q1 | 120 |\n[F4.3]\n"
    record = section_markdown_utils.build_section_record(markdown)
    cited = [(block["kind"], block["entry_ids"]) for block in record["blocks"]]
    harness.check("the bullet and the table both cite F4.3", cited == [("list", ["F4.3"]), ("table", ["F4.3"])],
                  str(cited))


def test_a_block_after_a_cited_lead_in_ending_in_a_colon_takes_its_ids():
    """A lead-in that ends in a colon and names its ids says what the table under it is: the writer cited the table's
    note once, on the lead-in, and the table was refused as citing nothing (NIST chapter 1's check)."""

    markdown = ("The document lists its figures [S0014]:\n\n| Figure | Shows |\n|---|---|\n| Fig. 1 | The Core |\n\n"
                "A plain sentence of its own. [S0015]\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    record = section_markdown_utils.build_section_record(markdown)
    cited = [(block["kind"], block["entry_ids"]) for block in record["blocks"]]
    harness.check("the table after the lead-in cites S0014; the one after a plain sentence still cites nothing",
                  cited == [("paragraph", ["S0014"]), ("table", ["S0014"]), ("paragraph", ["S0015"]), ("table", [])],
                  str(cited))


def test_on_screen_is_removed_from_a_chapter_before_it_is_judged():
    """Five chapter answers blocked on "on screen" added to a sentence ("the page was displayed on screen"); the phrase
    is taken out by the program, which the sentence reads right without (2026-09-30). "frame" is left for the check."""

    markdown = ("### Refunds (T01.1)\n\nThe refund page was displayed on screen while Jonas spoke. [F1.1]\n\n"
                "The figure on the screen shows the limit. [F1.2]\n")
    sections = section_markdown_utils.split_chapter_markdown(markdown)
    text = " ".join(str(section) for section in sections.values()) if isinstance(sections, dict) else str(sections)
    harness.check("the phrase is gone and the sentence stays", "on screen" not in text and "on the screen" not in text
                  and "The refund page was displayed while Jonas spoke." in text, text[:300])


def test_on_the_screen_that_ends_its_sentence_is_never_cut_out_defect():
    # "… is mentioned in the speech but is not on the screen; this is …" reached the document as "… but is not; this
    # is …": the phrase was the sentence's own end (DEFECTS 367). After a form of be or remain it stays, for the check
    markdown = ("### Refunds (T01.1)\n\nThe word Gatewayz is heard but is not on the screen; Jonas says it twice. [F1.1]\n\n"
                "The broken refund remains on screen with every test passing. [F1.2]\n")
    sections = section_markdown_utils.split_chapter_markdown(markdown)
    text = " ".join(str(section) for section in sections.values()) if isinstance(sections, dict) else str(sections)
    harness.check("both sentences keep their end", "is not on the screen;" in text and "remains on screen with" in text,
                  text[:300])


def test_removing_the_ids_keeps_the_closing_backtick_of_a_code_span_before_them():
    """"`host.example.com` [F5.1]" lost its closing backtick with the ids, and the published code ran on to the next
    backtick (2026-10-01)."""

    from utils import document_text_utils
    text = "The portal is at `portal.acme.example` [F5.1]. Its catalog is at `portal.acme.example/catalog` [F5.2]."
    harness.check("both code spans keep their backticks",
                  document_text_utils.remove_bracketed_entry_ids(text)
                  == "The portal is at `portal.acme.example`. Its catalog is at `portal.acme.example/catalog`.",
                  document_text_utils.remove_bracketed_entry_ids(text))
    harness.check("ids wrapped in backticks still go whole",
                  document_text_utils.remove_bracketed_entry_ids("A fact. `[F5.1]`") == "A fact.",
                  document_text_utils.remove_bracketed_entry_ids("A fact. `[F5.1]`"))


def test_a_diagram_with_an_open_quote_is_named_and_never_joined_into_one_line():
    """A diagram left one label's quote open; the program then turned every later line break into `<br/>`, and the
    published diagram was one line Mermaid could not parse (2026-10-01)."""

    from utils import mermaid_text_utils
    source = 'flowchart LR\n  Gate["Gateway group] --> Store\n  Cert["Cert Manager"] --> Entra\n  App --> Cert'
    harness.check("nothing is joined while a quote is open",
                  mermaid_text_utils.name_bare_nodes(source).count("\n") == source.count("\n"),
                  mermaid_text_utils.name_bare_nodes(source))
    problems = mermaid_text_utils.find_syntax_problems(source)
    harness.check("the open quote is named by its line", len(problems) == 1 and "line 2" in problems[0], str(problems))
    joined = 'flowchart LR\n  Gate --> Store<br/>  Cert --> Entra'
    harness.check("`<br/>` outside a label is named",
                  "outside a quoted label" in " ".join(mermaid_text_utils.find_syntax_problems(joined)))
    harness.check("a clean diagram has no problem",
                  mermaid_text_utils.find_syntax_problems('flowchart LR\n  A["Line one<br/>line two"] --> B[Store]') == [])
    harness.check("a box with an empty label is named",
                  all("empty label" in " ".join(mermaid_text_utils.find_syntax_problems(source))
                      for source in ('flowchart LR\n  Start(("")) --> A["Shop"]', 'flowchart LR\n  Start{{}} --> A["Shop"]')))
    harness.check("double quotes inside an arrow's label are named",
                  "double quote" in " ".join(mermaid_text_utils.find_syntax_problems('flowchart LR\n  A -->|returns "OK"| B')))
    harness.check("a labelled start circle and a plain arrow label pass",
                  mermaid_text_utils.find_syntax_problems('flowchart LR\n  Start(("Start")) --> A["Shop"]\n  A -->|returns OK| B["Bank"]') == [])
    end_box = 'flowchart LR\n  A["Shop"] -->|the end| end["Done"]\n  subgraph G["Gateway"]\n    G1 --> G2\n  end'
    harness.check("a box named `end` is named, and a group's closing `end` and `end` inside a label are not",
                  [problem[:6] for problem in mermaid_text_utils.find_syntax_problems(end_box)] == ["line 2"],
                  str(mermaid_text_utils.find_syntax_problems(end_box)))
    bad_direction = 'flowchart LR\n  A["Shop"] --> G\n  subgraph G["Gateway"]\n    direction RB\n    G1["Login"] --> G2["Token"]\n  end'
    harness.check("a direction Mermaid does not know is named",
                  "direction" in " ".join(mermaid_text_utils.find_syntax_problems(bad_direction)),
                  str(mermaid_text_utils.find_syntax_problems(bad_direction)))
    same_name = 'flowchart LR\n  A["Shop"] --> B["Billing"]\n  subgraph B\n    B1["Invoice"] --> B2["Ledger"]\n  end'
    harness.check("a group named as a box of the same diagram is named",
                  "group" in " ".join(mermaid_text_utils.find_syntax_problems(same_name)),
                  str(mermaid_text_utils.find_syntax_problems(same_name)))
    harness.check("a group with its own name and a known direction is clean",
                  mermaid_text_utils.find_syntax_problems(
                      'flowchart LR\n  A["Shop"] --> G\n  subgraph G["Gateway"]\n    direction TB\n    G1["Login"] --> G2["Token"]\n  end') == [])


def test_sentences_that_narrate_the_recording_are_found():
    """One published document narrated the recording in 118 of its 709 sentences and wrote 17 about the transcript
    itself (2026-10-01)."""

    from utils import document_text_utils
    text = ("The refund limit is 500 euros per order. The presenter hovers over the limit field while explaining it. "
            "The speaker jokes about the old form. The rest of the sentence was cut off in the transcript. "
            "Jonas approves every refund above the limit.")
    found = document_text_utils.find_narrated_sentences(text)
    harness.check("the three narrating sentences are found, the two facts are not", len(found) == 3
                  and not any("500 euros" in sentence or "approves" in sentence for sentence in found), str(found))


def test_a_sentence_copied_from_the_speech_is_found():
    """30 sentences of one published document held a whole transcript line, filler included (2026-10-01)."""

    from utils import document_text_utils
    speech = ["So you basically have to open a ticket for the refund, et cetera. Yeah."]
    text = ("A refund needs a ticket. So you basically have to open a ticket for the refund, et cetera. "
            "The ticket names the order.")
    found = document_text_utils.find_copied_speech(text, speech)
    harness.check("the pasted line is found, the written facts are not",
                  found == ["So you basically have to open a ticket for the refund, et cetera."], str(found))


def test_the_same_figures_in_other_words_are_said_earlier():
    """One count of a tenant's users, groups, applications and devices stood in three sections in different words
    (2026-10-01)."""

    from utils import document_text_utils
    earlier = "The tenant holds 1,200 users, 340 groups and 56 applications. [F3.1]"
    later = "Its overview lists 1,200 users, 340 groups and 56 applications today. Refunds need a ticket."
    harness.check("the reworded sentence with the same three numbers is said earlier",
                  len(document_text_utils.find_sentences_said_earlier(later, earlier)) == 1)
    harness.check("two shared numbers are not enough",
                  document_text_utils.find_sentences_said_earlier("The order of 1,200 items took 340 days.", earlier)
                  == [])
    harness.check("small numbers of another fact are not the same figures",
                  document_text_utils.find_sentences_said_earlier(
                      "Two arguments, as in `range(3, 5)`, keep the step of 1.",
                      "For instance, `range(5)` produces the values 0, 1, 2, 3, and 4. [F1.1]") == [])


def test_a_short_spoken_sentence_copied_whole_is_found():
    """"Okay. So." and a lone "Back." reached a published document (2026-10-01)."""

    from utils import document_text_utils
    found = document_text_utils.find_copied_speech("Refunds need a ticket. Back. Okay.", ["Back. Okay. So the ticket."])
    harness.check("the whole short spoken sentences are found", found == ["Back.", "Okay."], str(found))


def test_a_sentence_saying_the_speaker_explained_or_outlined_counts_as_narration():
    """The instruction says never write that something is shown, said, explained or presented; the check did not know
    those verbs, and such sentences passed (2026-10-01)."""

    for sentence in ["The speaker explained the risk shown in the diagram.", "The presenter outlines the structure.",
                     "The speaker introduces the second option."]:
        harness.check(f"narration: {sentence}", bool(document_text_utils.find_narrated_sentences(sentence)))
    for sentence in ["A second gateway is planned.", "Speaker notes are kept for one year.", "The status page shows the outage."]:
        harness.check(f"no narration: {sentence}", not document_text_utils.find_narrated_sentences(sentence))


def test_a_marked_line_written_at_the_end_of_a_paragraph_is_its_own_block():
    """A set-aside line written on the same line as the paragraph before it was read as that paragraph's text and
    published to readers (2026-10-12)."""

    answer = ("### Retries (T01.1)\n\nA job is retried three times [F1.1]. > **Set aside:** who presents [F1.2]\n\n"
              "The delay doubles [F1.3]. > **Open:** Who sets the limit is not settled. [F1.4]\n")
    blocks = section_markdown_utils.parse_section(section_markdown_utils.clean_chapter_markdown(answer).split("\n", 1)[1])
    kinds = [block["kind"] for block in blocks]
    harness.check("the set-aside and the open line each stand as their own block, the paragraphs keep their ids",
                  kinds == ["paragraph", "set_aside", "paragraph", "open"]
                  and blocks[0]["entry_ids"] == ["F1.1"] and "Set aside" not in blocks[0]["text"], str(blocks))


def test_a_lone_fence_closing_nothing_at_the_end_of_an_answer_is_taken_off():
    """A chapter answer ended in a "```" line left from wrapping it, with no opening line: read as a fence never closed,
    it was refused twice with an empty unsourced block the writer could not find (2026-10-12)."""

    answer = ("### Retries (T01.1)\n\nA job is retried three times. [F1.1]\n\n```python\nretry(3)\n```\n[F1.2]\n\n"
              "> **Set aside:** who presents [F1.3]\n```\n")
    cleaned = section_markdown_utils.clean_chapter_markdown(answer)
    harness.check("the stray closing line goes, the code block keeps both its fences",
                  cleaned.rstrip().endswith("[F1.3]") and cleaned.count("```") == 2, repr(cleaned))
    closed = "### Retries (T01.1)\n\nA job is retried. [F1.1]\n\n```python\nretry(3)\n```\n"
    harness.check("a code block that closes on the last line is left whole",
                  section_markdown_utils.clean_chapter_markdown(closed).count("```") == 2)


def test_an_open_point_saying_a_step_is_not_shown_yet_is_narration():
    """Four open points asking what the lecture would show next reached accepted chapters, beside the answer in the same
    section; an instruction against them did not stop the fifth (2026-10-12)."""

    for sentence in ["The syntax of the loop is not yet written or shown at this stage of the explanation.",
                     "How the job is stopped is not shown yet.", "The retry setting is not yet demonstrated.",
                     "How the limit fits the report is not yet addressed.",
                     "The output after the fix is not shown until the next step.",
                     "The expected output is not shown in this section."]:
        harness.check(f"narration: {sentence}", bool(document_text_utils.find_narrated_sentences(sentence)))
    for sentence in ["The new report is not yet shown to customers.", "The quota is not yet decided.",
                     "The total is not shown until checkout.",
                     "Which team owns the gateway is not settled."]:
        harness.check(f"no narration: {sentence}", not document_text_utils.find_narrated_sentences(sentence))


def test_a_sentence_with_the_speaker_acting_is_narration_whatever_its_verb():
    """A verb list missed "completes", "concludes", "notes", "asks" and "detailed" in one published document (2026-10-01)."""

    for sentence in ["The speaker concludes that the check belongs in the repository.", "The presenter asks whether it is fast enough.",
                     "This step is accompanied by the speaker preparing to open the next page."]:
        harness.check(f"narration: {sentence}", bool(document_text_utils.find_narrated_sentences(sentence)))
    for sentence in ["The presenter's team owns the importer.", "Speaker notes are kept for one year."]:
        harness.check(f"no narration: {sentence}", not document_text_utils.find_narrated_sentences(sentence))


def test_brackets_joined_by_a_comma_are_taken_out_as_one():
    """Taken out one by one, "[F<frame>.<note>], [S<line>]" left ",." behind on five lines of a summary (2026-10-01)."""

    moved = document_text_utils.move_bracketed_entry_ids({"text": "Open points remain for the gateway [F20.2], [S0634]."})
    harness.check("no comma is left behind", moved["text"] == "Open points remain for the gateway.", moved["text"])
    harness.check("and both ids are kept", moved["entry_ids"] == ["F20.2", "S0634"], str(moved["entry_ids"]))


def test_a_sentence_telling_the_course_of_the_meeting_counts_as_narration():
    """"The presentation moves to the third use case" reached a published chapter: no presenter or speaker is named."""

    # "The session concludes with a summary" was held as the subject's here; 28 such sentences in the published
    # documents of 15 public runs all told what the talk did, so it is narration (DEFECTS 204)
    for sentence in ("The presentation moves to the third use case: static texts.", "The session then turns to refunds.",
                     "The talk then shifts to the cost of the cluster.",
                     "The session concludes with a summary of pros and cons, followed by questions."):
        harness.check(f"{sentence!r} is narration", document_text_utils.find_narrated_sentences(sentence) == [sentence],
                      str(document_text_utils.find_narrated_sentences(sentence)))
    for sentence in ("The meeting decided to split refunds into three parts.", "A refund session expires after an hour.",
                     "The presentation layer calls the billing service.", "The session token expires after an hour."):
        harness.check(f"{sentence!r} is about the subject", document_text_utils.find_narrated_sentences(sentence) == [],
                      str(document_text_utils.find_narrated_sentences(sentence)))


def test_the_narrating_words_of_a_sentence_are_named():
    """A finding that quoted a sentence's first words left the worker hunting for 'which the speaker introduces'."""

    sentence = "These keywords let analysts define tests, which the speaker introduces with an example."
    harness.check("the clause that narrates is returned", document_text_utils.find_narration_words(sentence)
                  .startswith("speaker introduc"), document_text_utils.find_narration_words(sentence))
    harness.check("a subject sentence has none", document_text_utils.find_narration_words("A refund has three parts.") == "")


def test_a_short_range_of_one_frames_notes_is_written_in_full():
    """`[F82.1-82.3]` was refused as an unreadable bracket; it means F82.1 to F82.3."""

    harness.check("the second end gets its F",
                  document_text_utils.write_short_ranges_in_full("Read [S0665, F81.1, F82.1-82.3].")
                  == "Read [S0665, F81.1, F82.1-F82.3].")
    harness.check("a range written with a typographic dash is read as one",
                  document_text_utils.write_short_ranges_in_full("Flow [F13.9\u2013F13.12] and [S0115 \u2014 S0118].")
                  == "Flow [F13.9-F13.12] and [S0115-S0118].")
    harness.check("a dash in prose and in a link's text stays",
                  document_text_utils.write_short_ranges_in_full("From 2024\u20132025, see [2024\u20132025](x) [F1.1]")
                  == "From 2024\u20132025, see [2024\u20132025](x) [F1.1]")
    harness.check("a full range and a diagram step stay as written",
                  document_text_utils.write_short_ranges_in_full("[F1.2-F1.4] and [F2.7-3]") == "[F1.2-F1.4] and [F2.7-3]")


def test_a_diagrams_ids_followed_by_text_on_their_line_are_given_the_line_alone():
    """"[F66.3] This script …" after a fence was refused: the diagram's ids must stand alone on their line."""

    text = "```mermaid\nflowchart LR\n  A --> B\n```\n[F66.3] The same logic runs everywhere [S0643].\n"
    harness.check("the ids keep their line and the text becomes a paragraph",
                  document_text_utils.split_ids_after_fence(text)
                  == "```mermaid\nflowchart LR\n  A --> B\n```\n[F66.3]\n\nThe same logic runs everywhere [S0643].\n",
                  document_text_utils.split_ids_after_fence(text))
    alone = "```mermaid\nflowchart LR\n  A --> B\n```\n[F66.3]\n"
    harness.check("ids already alone stay as they are", document_text_utils.split_ids_after_fence(alone) == alone)


def test_a_marked_lines_ids_in_round_brackets_are_put_in_square_ones():
    """A set-aside line ended "(F151.3, F153.1)." and was refused as a marked line naming no id (2026-10-02)."""

    text = "> **Set aside:** who presents and a window switched (F<a>.1, F<b>.2, S<c>).\n\nBody. [F1.1]\n"
    text = text.replace("<a>", "12").replace("<b>", "13").replace("<c>", "0415")
    fixed = document_text_utils.rewrite_marked_line_ids_in_brackets(text)
    harness.check("the ids of a marked line go into square brackets",
                  fixed == "> **Set aside:** who presents and a window switched [F12.1, F13.2, S0415]\n\nBody. [F1.1]\n", fixed)
    prose = "The limit (F12.1) is a code name.\n"
    harness.check("round brackets in ordinary prose stay", document_text_utils.rewrite_marked_line_ids_in_brackets(prose) == prose)


def test_a_subject_about_transcription_is_no_narration():
    """A product whose pipeline transcribes calls was refused as narration (2026-10-03); the meeting's own transcript
    still is."""

    from utils import document_text_utils
    harness.check("a pipeline that transcribes calls is the subject",
                  not document_text_utils.find_narrated_sentences("The AcmeSync pipeline transcribes and summarizes calls."))
    harness.check("the meeting's transcript is narration",
                  bool(document_text_utils.find_narrated_sentences("The transcript cuts off before the answer."))
                  and bool(document_text_utils.find_narrated_sentences("The name is transcribed as Akme.")))


def test_a_short_spoken_fact_inside_a_longer_own_sentence_is_no_copy():
    """A six-word spoken definition stood inside a 27-word sentence of the writer's own, with no filler, and the
    chapter was blocked as copied speech: a copy is a sentence made mostly of the speech."""

    from utils import document_text_utils
    speech = ["GTX stands for guaranteed transfer exchange. So you can imagine, these are, yeah, special orders."]
    text = ("GTX stands for guaranteed transfer exchange, the order type a treasury desk books when a client needs a "
            "fixed rate on a transfer it makes later in the quarter.")
    found = document_text_utils.find_copied_speech(text, speech)
    harness.check("the writer's own sentence holding a short spoken definition is not a copy", found == [], str(found))
    pasted = "GTX stands for guaranteed transfer exchange, yeah."
    harness.check("a sentence made mostly of the spoken one still is",
                  document_text_utils.find_copied_speech(pasted, speech) == [pasted],
                  str(document_text_utils.find_copied_speech(pasted, speech)))


def test_a_sentence_stitched_from_two_spoken_phrases_is_a_copy_defect():
    # A sentence joined from two spoken phrases, filler kept, passed: neither phrase made up half of it. A sentence whose
    # words lie three quarters in spoken runs of six words or more, the screen's own runs left out, is now a copy
    from utils import document_text_utils
    speech = ["So the gateway needs what is like the minimum you should do before it goes live, yeah.",
              "And that is why we get more in the direction which is typical of a partner launch."]
    stitched = ("Setting up what is like the minimum you should do before it goes live gets more in the direction "
                "which is typical of a partner launch.")
    harness.check("a sentence stitched from two spoken phrases is a copy",
                  document_text_utils.find_copied_speech(stitched, speech) == [stitched],
                  str(document_text_utils.find_copied_speech(stitched, speech)))
    harness.check("the same words on the screen are the screen's own",
                  document_text_utils.find_copied_speech(stitched, speech, screen_texts=[stitched]) == [], "")
    own = ("The gateway goes live once its partner checks pass, and the minimum you should do before it goes live is "
           "a signed contract and a tested refund flow.")
    harness.check("the writer's own sentence with one spoken phrase is not",
                  document_text_utils.find_copied_speech(own, speech) == [],
                  str(document_text_utils.find_copied_speech(own, speech)))


def test_the_meeting_transitioning_to_its_next_part_is_narration():
    from utils import document_text_utils
    found = document_text_utils.find_narrated_sentences("The presentation transitions to the target design. Refunds need a "
                                                       "ticket. The refund flow transitions data to the ledger.")
    harness.check("the meeting moving on is found, a process that transitions data is not",
                  found == ["The presentation transitions to the target design."], str(found))


def test_the_meeting_entering_its_next_part_and_what_the_slide_shows_are_narration():
    from utils import document_text_utils
    found = document_text_utils.find_narrated_sentences(
        "The presentation enters section 5, titled Target. On the slide, the refund flow is presented as an overview. "
        "A refund enters the ledger after approval.")
    harness.check("the meeting entering a section and 'on the slide' are found, a refund entering a ledger is not",
                  len(found) == 2 and not any("ledger" in sentence for sentence in found), str(found))


def test_a_block_right_before_a_frame_takes_the_frames_ids_and_a_lone_block_does_not():
    """The worker wrote the ids once, under the frame, and the paragraph above it was refused as naming no entry id."""

    markdown = ('The confirmation screen tells the payer the money arrived.\n'
                '![Confirmation](cut-01 "The screen after a transfer")\n[F1.2, F1.3]\n\n'
                'The services behind the step:\n\n- **Profile API:** keeps defaults.\n- **Limits API:** checks limits.\n\n'
                '![Services](cut-02 "The panel of services")\n[F2.1]\n\n'
                'A paragraph with no ids and no frame after it.\n')
    paragraphs = section_markdown_utils.build_section_record(markdown)["paragraphs"]
    harness.check("the paragraph over a frame takes the frame's ids",
                  paragraphs[0]["entry_ids"] == ["F1.2", "F1.3"], str(paragraphs))
    harness.check("a lead-in and its list over a frame take the frame's ids",
                  all(paragraph["entry_ids"] == ["F2.1"] for paragraph in paragraphs[1:-1]), str(paragraphs))
    harness.check("a paragraph with no frame after it stays without ids", paragraphs[-1]["entry_ids"] == [], str(paragraphs))


def test_a_horizontal_rule_is_no_paragraph():
    """A `---` between two blocks was refused as a block naming no entry id."""

    for rule in ("---", "***", "_ _ _"):
        paragraphs = section_markdown_utils.build_section_record(f"A fact [F1.1].\n\n{rule}\n\nAnother fact [F1.2].\n")["paragraphs"]
        harness.check(f"{rule!r} is not read as a paragraph", [paragraph["text"] for paragraph in paragraphs]
                      == ["A fact.", "Another fact."], str(paragraphs))


def test_ids_in_parentheses_at_a_block_s_end_are_read_as_its_ids():
    """A chapter answer ended a paragraph with "(S0110)." instead of "[S0110]." and was refused as unsourced, a whole
    resume; ids in parentheses at a line's end are its ids. Parentheses holding other words stay as written."""

    markdown = ("### Refund limits (T02.1)\n\nThe slide lists the five steps of the export (S0110).\n\n"
                "The timeout rises to 1000 seconds (S0099). The period stays at 10 [F3.2].\n\n"
                "The limit rises to 500 euros (for one order) [F3.1].\n")
    parts = section_markdown_utils.split_chapter_markdown(markdown)
    harness.check("the heading's own line still splits the chapter", len(parts) == 1
                  and section_markdown_utils.list_heading_ids(markdown) == ["T02.1"], str(parts))
    harness.check("the parenthesised ids become the block's ids", "the export [S0110]." in parts[0], parts[0])
    harness.check("and so do ids in parentheses inside a paragraph", "1000 seconds [S0099]. The period" in parts[0], parts[0])
    harness.check("parentheses holding words stay", "(for one order) [F3.1]." in parts[0], parts[0])


def test_the_screens_own_text_is_no_copy_of_the_speaker_who_read_it():
    """A slide's bullets, written as the markdown block the request asks for, were refused as copied speech because
    the speaker read them aloud (2026-10-13)."""

    speech = ["Retry budgets must be set per client, and every client gets its own budget."]
    bullet = "- Retry budgets must be set per client, and every client gets its own budget"
    harness.check("without the screen's text, the bullet is a copy",
                  document_text_utils.find_copied_speech(bullet, speech) != [])
    harness.check("held by a markdown note, it is the screen's own text",
                  document_text_utils.find_copied_speech(bullet, speech, screen_texts=[bullet]) == [])
    harness.check("a sentence the screen does not hold is still a copy",
                  document_text_utils.find_copied_speech("Retry budgets must be set per client, and every client gets "
                                                         "its own budget.", speech, screen_texts=["- Gateway"]) != [])


def test_a_figure_description_with_no_figure_above_it_is_dropped():
    """A frame left out kept its "What the figure shows" box; the document printed it as a bare sentence about the
    slide and was refused whole (2026-10-13)."""

    markdown = ("Retry budgets are set per client. [F3.1]\n\n"
                f"> {document_text_utils.FIGURE_DESCRIPTION_LABEL} the slide \"Budgets\" with one table [F3.2]")
    blocks = section_markdown_utils.parse_section(markdown)
    harness.check("no description block is left", all(block["kind"] != "figure_description" for block in blocks), str(blocks))
    harness.check("its ids stay on the block before it", "F3.2" in blocks[0]["entry_ids"], str(blocks))



def test_a_fence_holding_markdown_is_read_as_that_markdown():
    """A slide's sentences and list fenced with no language reached the reader as code boxes of prose (2026-10-13);
    a fence of code, or of a language, stays a code block."""

    markdown = ("```\n# Step 2: Ask AcmeSync\n\n- **Task:** check the Gateway\n- report to Jonas\n```\n[F4.1]\n\n"
                "```markdown\nRetries stop after three attempts.\n```\n[F4.2]\n\n"
                "```\nretry = 3;\n```\n[F4.3]\n\n```text\nGateway ready\n```\n[F4.4]")
    blocks = section_markdown_utils.parse_section(markdown)
    kinds = [block["kind"] for block in blocks]
    harness.check("the fenced title is a bold line", blocks[0]["text"] == "**Step 2: Ask AcmeSync**", str(blocks))
    harness.check("the fenced list is a list with its ids", blocks[1]["kind"] == "list"
                  and blocks[1]["entry_ids"] == ["F4.1"], str(blocks))
    harness.check("the markdown fence is a paragraph", blocks[2] == {"kind": "paragraph",
                  "text": "Retries stop after three attempts.", "entry_ids": ["F4.2"]}, str(blocks))
    harness.check("code and a named language stay code", kinds[3:] == ["code", "code"], str(kinds))


def test_a_sentence_written_under_a_marked_line_is_a_block_of_its_own():
    """A sentence under an action line with no blank line between ran into the action, and the closing list printed it
    as an action item (lecture-1, 2026-10-15)."""

    blocks = section_markdown_utils.parse_section("> **Action:** Download the Gateway slides\n"
                                                 "The session opens with a welcome. [S0001]\n\n"
                                                 "> **Action:** Ask Jonas for access\n[F4.7]")
    kinds = [(block["kind"], block["text"], block["entry_ids"]) for block in blocks]
    harness.check("the action keeps only its own line", kinds[0][1].endswith("Download the Gateway slides"), str(kinds))
    harness.check("the sentence is a paragraph with its ids", kinds[1] == ("paragraph", "The session opens with a welcome.",
                                                                       ["S0001"]), str(kinds))
    harness.check("an ids line under a marked line is still its ids", kinds[2][2] == ["F4.7"], str(kinds))


def test_a_fence_of_calls_alone_stays_code():
    """A slide's console lines, calls with no assignment, were opened as markdown and ran together into one paragraph
    (lecture-1, 2026-10-15)."""

    blocks = section_markdown_utils.parse_section("```\n## CONVERT ##\nfloat(3)\nGateway.ping()\n```\n[F4.1]\n\n"
                                                 "```\nCall Jonas (today)\n```\n[F4.2]")
    harness.check("calls stay a code block", blocks[0]["kind"] == "code", str(blocks))
    harness.check("a sentence with a bracket is still markdown", blocks[1]["kind"] == "paragraph", str(blocks))


def test_each_block_of_an_opened_fence_takes_the_ids_written_after_it():
    """A slide fenced as markdown, cited once after the fence: read out as markdown, every paragraph and list but the
    last named no id and was refused as unsourced (lecture-1 chapter 6, 2026-10-14). A fence of code keeps its ids
    as it did."""

    markdown = "```markdown\nREFUNDS\n\n* Parts come first\n  * Like orders\n\nThree parts per order\n```\n[F2.1]\n"
    blocks = section_markdown_utils.parse_section(markdown)
    harness.check("every block read out of the fence names F2.1",
                  [block["entry_ids"] for block in blocks] == [["F2.1"]] * 3, str(blocks))
    code = section_markdown_utils.parse_section("```python\nparts = 3\n```\n[F2.1]\n")
    harness.check("a code fence is one block with the ids", [block["entry_ids"] for block in code] == [["F2.1"]], str(code))


def test_a_look_s_box_id_is_taken_out_of_a_figure_s_description_and_caption():
    """A look's box ids are the worker's handles on a frame: NIST's figure description read "labeled "NIST
    Cybersecurity Framework" (C1)" in the published document. A C3 plant in the text is no box id and stays."""

    markdown = ('![The framework wheel](cut-01 "The wheel (P1) of functions")\n\n'
                f'> {document_text_utils.FIGURE_DESCRIPTION_LABEL} A centre labeled "Framework" (C1), a ring (T2, T3). [F3.2]\n\n'
                "C3 plants fix carbon directly (C3). [F3.3]")
    blocks = section_markdown_utils.parse_section(markdown)
    image = next(block for block in blocks if block["kind"] == "image")
    harness.check("no box id in the caption", image["caption"] == "The wheel of functions", str(image))
    harness.check("no box id in the description", image["description"] == 'A centre labeled "Framework", a ring.', str(image))
    harness.check("the text keeps its own words", "(C3)" in blocks[-1]["text"], str(blocks[-1]))


def test_a_paragraph_right_before_a_diagram_takes_the_diagram_s_ids():
    """A sentence saying what the Mermaid block under it draws, the ids written once after the block: the wiki's
    chapter 9 was refused for "A diagram illustrates the process of photorespiration …", as a paragraph before a frame
    or a table once was."""

    markdown = ("A diagram shows the three compartments.\n\n```mermaid\nflowchart LR\n    A[Chloroplast] --> B[Peroxisome]\n```\n"
                "[F15.8]")
    blocks = section_markdown_utils.build_section_record(markdown)["blocks"]
    harness.check("the lead-in takes the diagram's ids", blocks[0]["entry_ids"] == ["F15.8"], str(blocks))


def test_a_bracketed_list_of_box_ids_is_taken_out_of_the_text():
    """A look's box ids in brackets ended the wiki's Z scheme paragraph, "… to produce NADPH [C2-C5]", in the published
    document. An index in code stays, and so does a note id."""

    markdown = ("The carriers pass electrons to NADP+ reductase [C2-C5]. [F5.1]\n\n"
                "```python\nrow = cells[C1]\n```\n[F5.2]")
    blocks = section_markdown_utils.parse_section(markdown)
    harness.check("the box ids are gone, the note id kept", blocks[0]["text"] == "The carriers pass electrons to NADP+ reductase."
                  and blocks[0]["entry_ids"] == ["F5.1"], str(blocks[0]))
    harness.check("code keeps its brackets", "cells[C1]" in blocks[1]["text"], str(blocks[1]))


def test_a_link_wrapped_whole_in_a_code_span_is_unwrapped_defect():
    # A chapter wrote its wiki link as `[AcmeSync wiki](https://wiki.example.org/cli.html)`, a code box to the reader
    # with no link; the program now takes the backticks off a span that holds only a link
    cleaned = section_markdown_utils.clean_chapter_markdown(
        "### Commands (T03.1)\n\nThe help is at `[AcmeSync wiki](https://wiki.example.org/cli.html)` [F31.2].\n\n"
        "Run `curl https://wiki.example.org/cli.html` to fetch it [F31.3].\n\n```text\n`[x](https://a.example.org)`\n```\n")
    harness.check("the link stands outside the backticks",
                  "The help is at [AcmeSync wiki](https://wiki.example.org/cli.html) [F31.2]." in cleaned, cleaned)
    harness.check("a command naming an address and a fenced block keep theirs",
                  "`curl https://wiki.example.org/cli.html`" in cleaned and "`[x](https://a.example.org)`" in cleaned, cleaned)
    harness.check("cleaning twice changes nothing", section_markdown_utils.clean_chapter_markdown(cleaned) == cleaned)


def test_a_block_repeating_one_already_written_is_a_twin_and_no_note_to_cite_defect():
    # Asked to cite a note repeating a help list an earlier heading wrote, without writing it, a worker wrote the list
    # again and cited the other heading's note: three findings for one; such a twin is now sorted out once
    help_list = "```text\nhelp: list the commands\nping: check a node\nstatus: show the radio\nselect: pick a node\n```"
    written = [section_markdown_utils.list_block_lines(help_list)]
    entries = [{"id": "F48.4", "kind": "markdown", "text": help_list},
               {"id": "F54.3", "kind": "markdown", "text": "```text\nrfms: scan the band\nrssi: -100 dBm\n```"},
               {"id": "F55.1", "kind": "facts", "text": "The status command sums up the radio."}]
    blocks, twins, prose = section_markdown_utils.split_twin_blocks(entries, written)
    harness.check("the repeated help list is a twin, the new block is written", twins == ["F48.4"] and blocks == ["F54.3"],
                  f"{blocks} {twins} {prose}")


def test_a_block_read_one_letter_off_is_still_a_twin_defect():
    # One slide's list read twice, "Temperatursmessung" and "Temperaturmessung", was written twice in one heading: two
    # of its four lines matched exactly, under the share; a line one letter off now counts
    first = "- Temperaturen Hol...\n- Temperaturmessung - Holzofen\n- Solaranlage Batterie, Strom\n- Temperatur Solarthermie"
    second = "Anwendungen\n- Temperatursmessung - Holzofen\n- Solaranlage Batterie, Strom\n- Temperatur Solarthermie"
    entries = [{"id": "F67.3", "kind": "markdown", "text": first}, {"id": "F68.7", "kind": "markdown", "text": second}]
    blocks, twins, _ = section_markdown_utils.split_twin_blocks(entries, [])
    harness.check("the second reading is a twin of the first", blocks == ["F67.3"] and twins == ["F68.7"], f"{blocks} {twins}")


def test_a_slide_fenced_as_plain_text_is_read_as_its_lines_and_a_terminal_stays_code_defect():
    # Defect: a slide's title and items fenced as ```text reached the reader as a code box, and opened they ran on as one line.
    # Fix: a plain-text fence citing slide frames alone is opened, each line kept on its own; a terminal's stays code.
    markdown = ("```text\nContents\nMotivation\nHardware\n```\n[F2.1]\n\n"
                "```text\nGateway ready\nretries 3 OK\n```\n[F9.1]")
    blocks = section_markdown_utils.parse_section(section_markdown_utils.open_slide_fences(markdown, {2}))
    harness.check("the slide is a paragraph keeping its line breaks", blocks[0] == {
        "kind": "paragraph", "text": "Contents\\\nMotivation\\\nHardware", "entry_ids": ["F2.1"]}, str(blocks))
    harness.check("the terminal's output stays code", blocks[1]["kind"] == "code", str(blocks))


def test_a_terminal_dialog_fenced_as_markdown_stays_code_defect():
    # Defect: a terminal's permission dialog fenced as ```markdown read as prose and reached the reader as hard-broken lines.
    # Fix: a markdown fence citing terminal frames alone is renamed plain text, kept as code; a slide's stays opened.
    markdown = ("```markdown\nFetch https://acme.example and sum it up\nDo you want to allow it?\n1. Yes\n2. No\n```\n"
                "[F9.4]\n\n```markdown\nRefund rules:\n- one ledger posting per refund\n- settled daily\n```\n[F2.1]")
    blocks = section_markdown_utils.parse_section(section_markdown_utils.keep_terminal_fences(markdown, {9}))
    harness.check("the terminal's dialog is one code block", blocks[0]["kind"] == "code"
                  and "1. Yes" in blocks[0]["text"] and blocks[0]["entry_ids"] == ["F9.4"], str(blocks[0]))
    harness.check("the slide's list is still read as markdown", all(block["kind"] != "code" for block in blocks[1:]),
                  str([block["kind"] for block in blocks]))


def test_a_closing_fence_before_a_terminal_block_stays_a_closing_fence_defect():
    # Defect: a closing "```" was read as an opening fence and renamed "```text" when the next block cited a terminal,
    # so a chapter of terminal blocks was refused as an open fence five checks running (DEFECTS 654). Fix: only an
    # opening fence is renamed
    markdown = ("Run it:\n\n```text\n> python sploit.py\n```\n[F45.3]\n\nThen replay it:\n\n"
                "```text\n> asciinema play backup.cast\n```\n[F47.3]")
    kept = section_markdown_utils.keep_terminal_fences(markdown, {45, 47})
    harness.check("every closing fence stays a bare closing fence", kept == markdown, kept)
    blocks = section_markdown_utils.parse_section(kept)
    harness.check("both terminal blocks are closed code blocks, each cited",
                  [block["entry_ids"] for block in blocks if block["kind"] == "code"] == [["F45.3"], ["F47.3"]],
                  str(blocks))


def test_a_slide_title_copied_as_a_subheading_becomes_a_bold_line_defect():
    # Defect: a chapter wrote its slides' "### Reversing" titles as its own #### subheadings (DEFECTS 655). Fix: a
    # heading that is a heading of a cited screen-markdown note is a bold line; the chapter's own headings stay
    markdown = ("### Auth logic (T05.1)\n\nThe app is a state machine [F31.1].\n\n#### Reversing\n\n- Found the main app "
                "logic\n[F31.4]\n\n#### Token recovery\n\nThe token is predictable [F35.3].")
    out = section_markdown_utils.write_note_headings_as_bold(markdown, ["### Reversing\n- Found the main app logic"])
    harness.check("the slide's title is a bold line", "\n**Reversing**\n" in out and "#### Reversing" not in out, out)
    harness.check("the chapter's own headings stay", "### Auth logic (T05.1)" in out and "#### Token recovery" in out, out)


def test_a_slide_list_ending_in_a_wink_is_read_as_markdown_defect():
    # A slide's "Conclusions" list fenced as markdown stayed a code box, its last line's ";)" read as code (DEFECTS 658).
    # Now a ";)" ending a line is no code mark; a statement's ";" still is
    markdown = "```markdown\n## Conclusions\n\n- Devices cannot be updated\n- New targets to play with ;)\n```\n[F54.7]"
    kinds = [block["kind"] for block in section_markdown_utils.parse_section(markdown)]
    harness.check("the slide's list is read as a list, no code box", "list" in kinds and "code" not in kinds, str(kinds))
    harness.check("a statement ending in ';' still reads as code",
                  bool(section_markdown_utils.CODE_MARK.search("x = f(a);")), "")


def test_a_bare_lead_in_is_no_claim_said_earlier_defect():
    # A list's three-word lead-in ending in a colon was refused as said earlier, and the writer dropped the list. Now a
    # lead-in of four words or fewer ending in a colon is no sentence; a whole claim ending in a colon still is
    from utils import document_text_utils

    earlier = "Gateway tokens are:\n\nThe refund service splits each refund into three ledger postings before settlement:"
    text = "Gateway tokens are:\n\nThe refund service splits each refund into three ledger postings before settlement:"
    said = document_text_utils.find_sentences_said_earlier(text, earlier)
    harness.check("only the whole claim is said earlier", said == [document_text_utils._normalise_sentence(
        "The refund service splits each refund into three ledger postings before settlement:")], str(said))


def test_a_sentence_ending_in_a_colon_with_no_block_under_it_is_named_defect():
    # A paragraph announced a command with a colon and none followed, another led into an Open line, and no check read
    # it. Now a lead-in colon with no code block, table, list or figure under it is named; one with a block is not
    markdown = ("The Gateway token is fetched with [F1.1]:\n\n```\ncurl https://gateway.example.test/token\n```\n\n"
                "The refund states are:\n\n- open\n- settled\n\n"
                "The exact command to rotate the key is [F2.1]:\n\n> **Open:** who rotates it [F2.2]\n\n"
                "The last sentence ends here [F3.1]:")
    found = section_markdown_utils.list_lead_ins_into_nothing(markdown)
    harness.check("the two colons into nothing are named, the two into a block are not",
                  len(found) == 2 and "rotate the key" in found[0] and "ends here" in found[1], str(found))


def test_a_colon_leading_into_nothing_is_ended_as_the_section_is_read_defect():
    # A colon into nothing refused the chapter, and a resume asked four accepted parts again for a full stop; a quoted
    # screen comment under a colon was refused as nothing. Now the colon is made a full stop and a quote is a block
    markdown = ("The exact command to rotate the key is [F2.1]:\n\n> **Open:** who rotates it [F2.2]\n\n"
                "The Gateway issue was opened by Jonas [F3.1]:\n\n> Hi!\n> I have less time now.\n\n"
                "The last sentence ends here:")
    record = section_markdown_utils.build_section_record(markdown)
    texts = [paragraph["text"] for paragraph in record["paragraphs"]]
    harness.check("the colon before an Open line is a full stop, its ids kept",
                  "The exact command to rotate the key is." in texts[0] and "F2.1" in record["blocks"][0]["entry_ids"],
                  str(record["blocks"][0]))
    harness.check("the colon before a quote is kept", texts[1].endswith("Jonas:"), str(texts))
    harness.check("the colon at the end is a full stop", texts[-1] == "The last sentence ends here.", str(texts))


def test_a_fenced_unfinished_slide_sentence_is_no_block_defect():
    # A slide's "Gateway tokens are:", its rest a picture, stood fenced as a block of its own. Now such a fence is
    # dropped as the section is read, its ids kept with the block before it
    markdown = ("The Gateway issues short-lived tokens [F4.1].\n\n```markdown\nGateway tokens are:\n```\n\n[F4.2]\n\n"
                "```markdown\nRefunds are:\n- open\n- settled\n```\n\n[F5.1]")
    record = section_markdown_utils.build_section_record(markdown)
    texts = [block["text"] for block in record["blocks"]]
    harness.check("the unfinished sentence is gone", not any("tokens are" in text for text in texts), str(texts))
    harness.check("its id goes with the block before", "F4.2" in record["blocks"][0]["entry_ids"], str(record["blocks"][0]))
    harness.check("a fence with a list under its colon stays", any("Refunds are" in text for text in texts), str(texts))


def test_a_file_fenced_as_markdown_stays_code_defect():
    # A `.gitignore` fenced as markdown was read as markdown, its "# " comments shown as headings. Now a fence opens as
    # markdown only when half its lines read as prose; a slide's list still opens
    markdown = ("The Gateway ignores its build output [F7.1].\n\n```markdown\n# Generated by the build\n/target/\n"
                "# local settings\n.env\n```\n\n[F7.2]\n\n```markdown\n# Refund states\n- open\n- settled\n```\n\n[F8.1]")
    kinds = [block["kind"] for block in section_markdown_utils.build_section_record(markdown)["blocks"]]
    harness.check("the file stays code, the list opens", kinds.count("code") == 1 and kinds[-1] != "code", str(kinds))


def test_a_lead_in_right_above_an_opened_slide_fence_stays_its_own_paragraph_defect():
    # A slide's agenda fenced right under "The agenda is as follows:" was opened into the same paragraph, and a reader
    # got "follows: Overview" on one line. Now the opened lines start after a blank line
    markdown = "The agenda is as follows:\n```text\nOverview\nRefund rules\n```\n[F2.1]"
    blocks = section_markdown_utils.parse_section(section_markdown_utils.open_slide_fences(markdown, {2}))
    harness.check("the lead-in and the agenda are two blocks", [block["text"].split("\n")[0] for block in blocks]
                  == ["The agenda is as follows:", "Overview\\"], str([block["text"] for block in blocks]))


def test_tool_call_closing_tags_at_an_answer_s_end_are_taken_off_defect():
    # A chapter's answer ended with the model's own "</parameter>", "</function>", "</tool_call>" lines, read as a block
    # citing no note, and the chapter's check refused it (DEFECTS 518). Now they are taken off; a tag inside code stays
    markdown = ("The alert pages on-call [F3.2].\n\n```xml\n<function>run</function>\n```\n[F3.6]\n"
                "</parameter>\n</function>\n</tool_call>")
    cleaned = section_markdown_utils.clean_chapter_markdown(markdown)
    harness.check("the trailing tags are gone", not cleaned.rstrip().endswith("</tool_call>")
                  and cleaned.rstrip().endswith("[F3.6]"), cleaned)
    harness.check("the code's own tag stays", "<function>run</function>" in cleaned, cleaned)


def test_an_answer_ends_at_its_call_tags_whatever_follows_them_defect():
    # A chapter's answer ended "</parameter></function></tool_call><br><br>": not at the very end, the tags stayed as a
    # block citing no note (DEFECTS 592). Now the answer ends at the first line outside a fence opening with "</"
    markdown = ("The alert pages on-call [F3.2].\n\n```html\n</div>\n```\n[F3.6]\n"
                "</parameter>\n</function>\n</tool_call><br><br>")
    cleaned = section_markdown_utils.clean_chapter_markdown(markdown)
    harness.check("the tags and what follows them are gone", cleaned.rstrip().endswith("[F3.6]"), cleaned)
    harness.check("a closing tag inside code stays", "</div>" in cleaned, cleaned)


def test_a_dialog_for_another_command_is_no_repeat_of_earlier_dialogs_defect():
    # A permission dialog for a new command was refused as repeating an earlier dialog, its fixed lines making up the
    # share (DEFECTS 595). Lines two earlier blocks hold are left out of it; a block written again whole is still held
    dialog = ("Bash command\n{command}\nDo you want to proceed?\n1. Yes\n2. Yes, and don't ask again\n3. No\n"
              "Esc to cancel")
    earlier = [section_markdown_utils.list_block_lines(dialog.format(command=command))
               for command in ("hatch test", "git commit -m fix")]
    new = section_markdown_utils.list_block_lines(dialog.format(command='ag "import" --python'))
    harness.check("a dialog for another command is no repeat", not section_markdown_utils.is_block_held(new, earlier), "")
    harness.check("the same dialog written again is", section_markdown_utils.is_block_held(earlier[0], earlier), "")


def test_a_lone_fence_right_after_a_table_closes_nothing_defect():
    # A chapter closed its table with a lone ``` that opened a fence over the ids, the next block and the prose, and the
    # check named prose as code (DEFECTS 594). Now such a fence, with an ids line under it, is dropped; a code block
    # opened after a blank line stays
    markdown = ("| File | Line |\n|---|---|\n| a.py | 8 |\n```\n[F2.1]\n\nIt finds one file. [F2.2]\n\n"
                "| X |\n|---|\n| y |\n\n```\nls\n```\n[F2.3]")
    blocks = section_markdown_utils.parse_section(section_markdown_utils.clean_chapter_markdown(markdown))
    kinds = [block["kind"] for block in blocks]
    harness.check("the table, the paragraph, the second table and the code block, each its own",
                  kinds == ["table", "paragraph", "table", "code"], str(kinds))


def test_lines_of_cited_sentences_are_paragraphs_of_their_own_defect():
    # A glossary's definitions, each a line with its own ids, were written as one paragraph and ran together on the
    # page. Now each such line is its own paragraph; a sentence wrapped over two lines, and a fence, stay as written
    markdown = ("Tenant: one partner's configuration [S0027].\nShard: one of eight databases [S0028].\n\n"
                "The proxy checks the token\nbefore it forwards the call [F3.1].\n\n```text\nLine one [F3.2].\nLine two [F3.3].\n```")
    cleaned = section_markdown_utils.clean_chapter_markdown(markdown)
    blocks = section_markdown_utils.parse_section(cleaned)
    harness.check("two definitions, the wrapped sentence and the code are four blocks",
                  [block["kind"] for block in blocks] == ["paragraph", "paragraph", "paragraph", "code"],
                  str([block["text"] for block in blocks]))
    harness.check("each definition keeps its own ids", [block["entry_ids"] for block in blocks[:2]]
                  == [["S0027"], ["S0028"]], str(blocks[:2]))
    harness.check("cleaning twice changes nothing", section_markdown_utils.clean_chapter_markdown(cleaned) == cleaned,
                  cleaned)
